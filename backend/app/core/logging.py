"""Logging setup — PII redaction filter for GDPR/compliance safety.

Filters here are installed on *handlers*, never on loggers. A logger's own
filters run only for records created on that logger: attaching the redactor to
the root logger would skip every ``logging.getLogger(__name__)`` in the app,
because a propagated record reaches the root's handlers but not its filters.
Handler filters run for every record that arrives, propagated or not.
"""

import logging
import re
from typing import Any, ClassVar

from app.core.config import settings
from app.core.context import get_request_id

# Attributes the logging module puts on every record; anything else came from a
# caller's `extra=` and therefore needs scrubbing too.
_STANDARD_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}

# How deep to walk `extra=` containers before giving up.
_MAX_REDACT_DEPTH = 4

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class PiiRedactionFilter(logging.Filter):
    """Logging filter that redacts personally identifiable information.

    Automatically scrubs email addresses, JWT tokens, API keys, bearer tokens,
    and password-like values from log messages to prevent PII leaks to
    log aggregators (Datadog, CloudWatch, Logfire, etc.).

    Covers the whole record, not just the message: ``args``, the extra fields
    that structured logging ships (``extra={"details": ...}``) and the rendered
    traceback, which routinely echoes query parameters and tokens.

    Install with ``setup_logging()`` rather than by hand -- a filter on a logger
    would miss most records (see the module docstring).
    """

    PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (
            re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
            "[EMAIL_REDACTED]",
        ),
        # JWT tokens (header.payload.signature)
        (
            re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
            "[JWT_REDACTED]",
        ),
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "[API_KEY_REDACTED]"),
        (re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[API_KEY_REDACTED]"),
        # Generic long hex/base64 secrets (40+ chars, likely tokens)
        (
            re.compile(
                r"(?:token|key|secret|password|authorization)[=: ]+['\"]?([A-Za-z0-9_/+=.-]{40,})",
                re.IGNORECASE,
            ),
            "[SECRET_REDACTED]",
        ),
        (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{10,}"), "Bearer [TOKEN_REDACTED]"),
        # Password/secret in key=value or key: value patterns
        (
            re.compile(
                r"(password|passwd|pwd|secret_key|api_key|apikey|auth_token|access_token|refresh_token)"
                r"[\s]*[=:]\s*['\"]?\S+['\"]?",
                re.IGNORECASE,
            ),
            r"\1=[REDACTED]",
        ),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact PII from the record's message, args, extras and traceback."""
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: self._redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        self._redact_extras(record)
        self._redact_traceback(record)
        return True

    def _redact_extras(self, record: logging.LogRecord) -> None:
        """Scrub caller-supplied ``extra=`` fields.

        These never appear in the default format string, so they look harmless --
        until a JSON formatter ships them verbatim to the aggregator.
        """
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_RECORD_ATTRS:
                continue
            record.__dict__[key] = self._redact_value(value)

    def _redact_traceback(self, record: logging.LogRecord) -> None:
        """Render and scrub the traceback before any formatter can emit it raw.

        ``Formatter.format`` reuses ``exc_text`` when it is already set, so doing
        it here covers every handler, including the ones uvicorn owns.
        """
        if record.exc_info and not record.exc_text:
            record.exc_text = self._redact(
                logging.Formatter().formatException(record.exc_info)
            )

    def _redact_value(self, value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            return self._redact(value)
        if depth >= _MAX_REDACT_DEPTH:
            return value
        if isinstance(value, dict):
            return {
                key: self._redact_value(item, depth + 1) for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact_value(item, depth + 1) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(item, depth + 1) for item in value)
        return value

    def _redact(self, value: str) -> str:
        for pattern, replacement in self.PATTERNS:
            value = pattern.sub(replacement, value)
        return value


class RequestIDFilter(logging.Filter):
    """Stamp every record with the current request ID.

    Makes ``%(request_id)s`` usable in a format string and gives a structured
    handler one field to group a whole request by. ``-`` outside a request.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def install_log_filters() -> None:
    """Add the filters to every handler in the process, idempotently.

    Sweeps other libraries' loggers too -- uvicorn keeps its own handlers with
    ``propagate = False``, and its access log carries full request paths.
    Safe to call again after something installs a new handler.
    """
    handlers: list[logging.Handler] = list(logging.getLogger().handlers)
    for existing in logging.root.manager.loggerDict.values():
        if isinstance(existing, logging.Logger):
            handlers.extend(existing.handlers)

    for handler in handlers:
        for filter_class in (PiiRedactionFilter, RequestIDFilter):
            if not any(isinstance(f, filter_class) for f in handler.filters):
                handler.addFilter(filter_class())


def setup_logging() -> None:
    """Configure application logging. Safe to call more than once.

    Gives the root logger a stream handler at ``LOG_LEVEL``, because uvicorn
    configures only its own loggers -- app records would otherwise fall through
    to the ``WARNING``-only handler of last resort. Then installs the filters.

    Only handles the root logger when nothing else has: a deployment that
    configures logging itself (gunicorn, ``--log-config``, a JSON handler for the
    aggregator) keeps its handlers and just gets the filters bolted on, instead
    of every line being emitted twice.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(handler)

    install_log_filters()
