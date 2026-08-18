"""Lazy access to the crawler's two heavy siblings: the summarizer and the mailer.

``summarizer`` opens a model client (Bedrock / a local server) and
``process_and_email`` reads Gmail credentials, so importing them at module level
would make ``--help`` and ``--list-models`` pay for both. They are imported here,
by package-relative name, at the moment they are first needed — which also keeps
the failure message specific when one of them is not installed yet.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

_PACKAGE_DIR = "backend/app/services/crawler"

_HINT = (
    "缺少模块 {module}.py（应放在 {directory}/ 下）。"
    "它原先位于 src/，可用 `git show HEAD:src/{module}.py > {directory}/{module}.py` 取回。"
)


class MissingComponentError(RuntimeError):
    """A companion module (summarizer / mailer) is not present."""


def _load(module: str) -> ModuleType:
    try:
        return importlib.import_module(f"{__package__}.{module}")
    except ImportError as exc:
        raise MissingComponentError(_HINT.format(module=module, directory=_PACKAGE_DIR)) from exc


def make_summarizer(model: str | None) -> Any:
    """The configured summarizer. Raises ValueError/RuntimeError on a bad model."""
    return _load("summarizer").Summarizer(model)


def make_email_sender() -> Any:
    return _load("process_and_email").EmailSender()


def format_model_table() -> str:
    """The available-models table, or the reason it can't be shown.

    Never raises: it feeds ``--help``, which must work even in a partial checkout.
    """
    try:
        return _load("summarizer").format_model_table()
    except MissingComponentError as exc:
        return str(exc)


def default_model() -> str:
    try:
        return _load("summarizer").DEFAULT_MODEL
    except MissingComponentError:
        return "sonnet"
