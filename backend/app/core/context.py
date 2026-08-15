"""Request-scoped context.

A context variable rather than a parameter: the request ID has to be reachable
from log filters, services and repositories, and none of those should grow an
argument for it -- nor should they import from ``app.api``.

``RequestIDMiddleware`` binds it on the way in. Outside a request (startup, CLI,
background workers) it is simply ``None``.
"""

from contextvars import ContextVar, Token

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """The current request's ID, or ``None`` outside a request."""
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind the ID for this context, returning the token to reset with."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous value, so a reused context cannot inherit an ID."""
    _request_id.reset(token)
