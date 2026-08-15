"""Exception handlers for FastAPI application.

These handlers convert domain exceptions to proper HTTP responses.
WebSocket connections that raise an ``AppException`` before ``accept()`` are
handled too -- Starlette closes the socket with 403 and we just log the
incident; we cannot return an HTTP body for a non-HTTP scope.

The response body is ``ErrorResponse``: the message lives in ``detail`` so it
matches FastAPI's own ``HTTPException``/validation errors (and therefore the
generated clients), with ``code``/``details`` carrying the machine-readable part.
"""

import logging
from typing import Any, cast

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.types import ExceptionHandler

from app.core.exceptions import AppException
from app.schemas.base import ErrorResponse

logger = logging.getLogger(__name__)


def _connection_meta(conn: HTTPConnection) -> dict[str, Any]:
    """Common log fields shared by HTTP requests and WebSocket connections.

    ``method`` exists only on HTTP ``Request`` — for WebSockets we surface the
    scope type so log filters can still distinguish the two.
    """
    return {
        "path": conn.url.path,
        "method": getattr(conn, "method", None) or conn.scope.get("type", "unknown"),
    }


def _is_websocket(conn: HTTPConnection) -> bool:
    return conn.scope.get("type") == "websocket"


def _error_body(*, detail: str, code: str, details: dict[str, Any] | None) -> Any:
    return ErrorResponse(detail=detail, code=code, details=details).model_dump()


async def app_exception_handler(
    request: HTTPConnection, exc: AppException
) -> JSONResponse | None:
    """Handle application exceptions for both HTTP and WebSocket scopes.

    Logs 5xx errors as errors and 4xx as warnings. Returns a JSON response
    for HTTP scopes; returns ``None`` for WebSocket scopes (Starlette will
    close the socket on its own).
    """
    log_extra = {
        "error_code": exc.code,
        "status_code": exc.status_code,
        "details": exc.details,
        **_connection_meta(request),
    }

    if exc.status_code >= 500:
        logger.error("%s: %s", exc.code, exc.message, extra=log_extra)
    else:
        logger.warning("%s: %s", exc.code, exc.message, extra=log_extra)

    if _is_websocket(request):
        return None

    headers: dict[str, str] = {}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"

    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(
            detail=exc.message,
            code=exc.code,
            # 5xx details describe our internals; they are logged, never shipped.
            details=exc.details if exc.status_code < 500 else None,
        ),
        headers=headers,
    )


async def unhandled_exception_handler(
    request: HTTPConnection, exc: Exception
) -> JSONResponse | None:
    """Handle unexpected exceptions.

    Logs the full exception but returns a generic error to the client
    to avoid leaking sensitive information.
    """
    logger.error("Unhandled exception", exc_info=exc, extra=_connection_meta(request))

    if _is_websocket(request):
        return None

    return JSONResponse(
        status_code=500,
        content=_error_body(
            detail="An unexpected error occurred", code="INTERNAL_ERROR", details=None
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app.

    Call this after creating the FastAPI application instance.
    """
    # Starlette types a handler as returning a Response, but it explicitly
    # tolerates ``None`` (see ``wrap_app_handling_exceptions``), which is what a
    # WebSocket scope needs -- hence the cast rather than a lie in the signature.
    app.add_exception_handler(
        AppException, cast("ExceptionHandler", app_exception_handler)
    )
    app.add_exception_handler(
        Exception, cast("ExceptionHandler", unhandled_exception_handler)
    )
