"""FastAPI application entrypoint.

Assembly only: this module wires the app together -- middleware, exception
handlers, routers, process lifecycle -- and owns no business logic. That lives in
``api/routes`` (HTTP shape), ``services`` (rules) and ``repository`` (persistence).

Run it with ``uvicorn app.main:app --reload`` or ``fastapi dev app/main.py``.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware

from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import RateLimitMiddleware, RequestIDMiddleware
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import assert_event_loop_supported, close_db

logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    """Name generated client methods ``<tag>-<function>`` instead of the default.

    Falls back for an untagged route: FastAPI calls this while matching, so an
    ``IndexError`` here would surface as a 500 on unrelated requests.
    """
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}-{route.name}"


""" Lifecycle """


async def _open_todo_pool() -> None:
    """Open the shared asyncpg pool for the deep-research TODO planner.

    ``asyncpg`` is not a declared dependency yet, so a missing install is a
    no-op instead of a boot failure: ``get_todo_pool()`` then returns ``None``
    and callers fall back to in-memory storage.
    """
    try:
        from app.db.todo_pool import init_todo_pool
    except ImportError:
        logger.info("asyncpg not installed, deep-research TODO pool disabled")
        return
    await init_todo_pool()


async def _close_todo_pool() -> None:
    try:
        from app.db.todo_pool import close_todo_pool
    except ImportError:
        return
    await close_todo_pool()


def _preload_rag() -> None:
    """启动时预热 RAG：加载嵌入模型、确保向量库集合存在。

    预热是优化、不是硬依赖：任何失败都只记警告、不阻断启动，首个 /rag 请求
    会再触发懒加载。由 ``settings.RAG_PRELOAD`` 控制，默认关闭，避免模型尚未
    下载时每次启动都去拉取。
    """
    try:
        from app.services.rag.embedding import get_embedder
        from app.services.rag.vectorstore import VectorStore

        get_embedder()
        VectorStore().ensure_collection()
        logger.info("RAG 预热完成")
    except Exception as exc:
        logger.warning("RAG 预热跳过（模型可能未就绪）: %r", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Own the process-wide resources: acquire before serving, release after.

    Shutdown mirrors startup in reverse so that whatever depends on a resource
    is gone before the resource itself is.

    Reserved slots, in the order they should be added once the components exist
    (each is startup here + the matching teardown below):

    1. Redis -- ``settings.REDIS_URL`` is the knob; hold the client on
       ``_app.state`` so routes reach it through a dependency, and ``aclose()``
       it on shutdown.
    2. RAG -- load the embedding model and open the vector store once here
       rather than per request; model load is slow and the store holds sockets.
    3. Telegram / Slack bots -- start polling as a background task owned by this
       scope, and cancel + await it on shutdown so a reload cannot leave two
       pollers competing for the same update stream.
    """
    logger.info("Starting %s (%s)", settings.PROJECT_NAME, settings.ENVIRONMENT)
    # Before anything touches the DB: psycopg cannot run on Windows' Proactor loop.
    assert_event_loop_supported()
    await _open_todo_pool()
    # 1. Redis        -- reserved, see docstring
    # 2. RAG          -- 预热嵌入模型 + 向量库（可选，由 RAG_PRELOAD 控制）
    if settings.RAG_PRELOAD:
        _preload_rag()
    # 3. Bot polling  -- reserved, see docstring

    try:
        yield
    finally:
        # 3. Bot polling -- reserved
        # 2. RAG         -- reserved
        # 1. Redis       -- reserved
        await _close_todo_pool()
        await close_db()
        logger.info("Shutdown complete")


""" Assembly """


def _init_sentry() -> None:
    """Report to Sentry from deployed environments only.

    Local runs would just pollute the project with noise from experiments.
    """
    if settings.SENTRY_DSN and settings.ENVIRONMENT != "local":
        sentry_sdk.init(dsn=str(settings.SENTRY_DSN), enable_tracing=True)


def _add_session_middleware(app: FastAPI) -> None:
    """Signed-cookie sessions, only when ``SESSION_ENABLED`` asks for them.

    Starlette's SessionMiddleware needs ``itsdangerous`` to sign the cookie, so
    the import stays inside the branch: nothing pays for it while the feature is
    off. Sessions are not part of auth -- access/refresh tokens are stateless --
    this is for flows that need server-side state across a redirect.
    """
    if not settings.SESSION_ENABLED:
        return

    from starlette.middleware.sessions import SessionMiddleware

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie=settings.SESSION_COOKIE_NAME,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.session_https_only,
    )


def _register_middleware(app: FastAPI) -> None:
    """Install the global middleware stack.

    ``add_middleware`` prepends, so the *last* one added is the outermost. Adding
    them innermost-first, as below, yields this order from the outside in:

        RequestID -> CORS -> RateLimit -> Session -> exception handlers -> routes

    The order is the point:

    * RequestID is outermost, so even a rejected or crashing request is
      traceable, and every response carries the header.
    * CORS sits outside the rate limit, so a browser can actually read a 429
      (and a preflight is never counted against the caller's budget).
    """
    _add_session_middleware(app)

    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(
            RateLimitMiddleware,
            limit=settings.RATE_LIMIT_REQUESTS,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
            exempt_paths=settings.rate_limit_exempt_paths,
        )

    if settings.all_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.all_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            # Without this the browser hides the header from client JS.
            expose_headers=[settings.REQUEST_ID_HEADER],
        )

    app.add_middleware(RequestIDMiddleware, header_name=settings.REQUEST_ID_HEADER)


def create_app() -> FastAPI:
    """Build the ASGI application.

    A factory rather than module-level statements: tests can build an app with
    patched settings, and nothing but ``app = create_app()`` runs at import time.
    """
    # First, so nothing gets logged before PII redaction is in place.
    setup_logging()
    _init_sentry()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        generate_unique_id_function=custom_generate_unique_id,
        lifespan=lifespan,
    )

    _register_middleware(app)

    # Renders every AppException (and anything unhandled) as a JSON error body
    # instead of leaking a bare 500.
    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.API_V1_STR)

    return app


# The ASGI app uvicorn/fastapi-cli import: `uvicorn app.main:app --reload`.
app = create_app()
