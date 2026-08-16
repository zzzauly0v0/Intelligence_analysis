"""Global ASGI middleware.

Cross-cutting request concerns that no single route should own: a request ID for
correlating logs across a call, and a coarse rate limit that sheds floods before
they reach the routes.

Both are plain ASGI middleware rather than ``BaseHTTPMiddleware`` -- neither has
to read or rewrite a body, and staying pure ASGI leaves streaming responses,
background tasks and WebSockets alone.
"""

import logging
import math
import time
import uuid
from collections.abc import Sequence

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import reset_request_id, set_request_id
from app.core.exceptions import RateLimitError
from app.schemas.base import ErrorResponse

logger = logging.getLogger(__name__)

""" Request ID """

# Long enough for a UUID hex or a gateway trace id, short enough to keep logs sane.
_MAX_REQUEST_ID_LENGTH = 64


def _sanitize_request_id(value: str | None) -> str | None:
    """Accept a caller-supplied ID only if it is safe to echo and log.

    The value goes straight back out as a response header, so anything outside
    ``[A-Za-z0-9_-]`` (notably CR/LF) is dropped rather than escaped.
    """
    if not value:
        return None
    candidate = value.strip()[:_MAX_REQUEST_ID_LENGTH]
    if candidate and all(char.isalnum() or char in "-_" for char in candidate):
        return candidate
    return None


class RequestIDMiddleware:
    """Give every request an ID and echo it back on the response.

    An inbound header wins so a gateway or the frontend can correlate one call
    across services; otherwise a UUID4 is minted. The ID lands in three places:
    ``request.state.request_id`` for routes, a context variable for everything
    deeper in the stack (``app.core.context.get_request_id``, which is also what
    puts the ID on every log line), and the response header for the caller.
    Browsers need it in the CORS ``expose_headers`` list to read it.
    """

    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(self.header_name)
        request_id = _sanitize_request_id(inbound) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            # WebSockets never emit this, so they get the ID in state only.
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(self.header_name, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


""" Rate limiting """


class RateLimitMiddleware:
    """Fixed-window request cap, keyed by client IP.

    Counters live in this process' memory, so with N workers the real ceiling is
    N x ``limit``. That is fine for the job it does here -- absorbing accidental
    floods and trivial scripted abuse. When a shared, exact limit is needed
    (multiple workers or hosts), replace ``_hits`` with Redis ``INCR`` + ``EXPIRE``
    on the same key; nothing else in this class changes.

    This is a blanket limit. Per-route budgets (a tighter one on login, say) are
    better expressed as a dependency on that route than as more logic here.
    """

    # Above this many tracked clients, sweep expired windows before inserting.
    _PRUNE_THRESHOLD = 10_000

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        window_seconds: int,
        exempt_paths: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.limit = limit
        self.window_seconds = window_seconds
        self.exempt_paths = tuple(exempt_paths)
        # client key -> (hits in the current window, window start on the monotonic clock)
        self._hits: dict[str, tuple[int, float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # WebSockets are exempt: one long-lived connection is a single request
        # here, and counting it would misprice the traffic in both directions.
        if scope["type"] != "http" or self._is_exempt(scope):
            await self.app(scope, receive, send)
            return

        key = self._client_key(scope)
        retry_after = self._consume(key)
        if retry_after is not None:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client": key,
                    "path": scope.get("path", ""),
                    "limit": self.limit,
                    "window_seconds": self.window_seconds,
                },
            )
            await self._reject(retry_after, scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _is_exempt(self, scope: Scope) -> bool:
        path: str = scope.get("path", "")
        return any(path.startswith(prefix) for prefix in self.exempt_paths)

    @staticmethod
    def _client_key(scope: Scope) -> str:
        """Identify the caller by peer address.

        ``scope["client"]`` is already the real client when uvicorn runs with
        ``--proxy-headers``/``--forwarded-allow-ips``, which is the only case
        where forwarded headers can be trusted -- so no ``X-Forwarded-For``
        parsing happens here.
        """
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _consume(self, key: str) -> int | None:
        """Count one request; return ``None`` if allowed, else seconds to wait.

        Runs to completion without awaiting, so the dict needs no lock: nothing
        else on the event loop can interleave with it.
        """
        now = time.monotonic()
        hits, window_start = self._hits.get(key, (0, now))

        elapsed = now - window_start
        if elapsed >= self.window_seconds:
            hits, window_start, elapsed = 0, now, 0.0
        elif hits >= self.limit:
            return max(1, math.ceil(self.window_seconds - elapsed))

        if key not in self._hits:
            self._prune(now)
        self._hits[key] = (hits + 1, window_start)
        return None

    def _prune(self, now: float) -> None:
        """Drop finished windows so a churn of client IPs cannot grow unbounded."""
        if len(self._hits) < self._PRUNE_THRESHOLD:
            return
        expired = [
            key
            for key, (_, window_start) in self._hits.items()
            if now - window_start >= self.window_seconds
        ]
        for key in expired:
            del self._hits[key]

    @staticmethod
    async def _reject(
        retry_after: int, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Answer with the same error body shape the exception handlers produce.

        The 429 is written here instead of raised: app exception handlers sit
        *inside* the middleware stack, so an exception raised at this level would
        never reach them.
        """
        error = RateLimitError()
        response = JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(detail=error.message, code=error.code).model_dump(),
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)
