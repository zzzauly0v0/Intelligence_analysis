"""Async PostgreSQL database session."""

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)


def assert_event_loop_supported() -> None:
    """Reject Windows' ProactorEventLoop, which psycopg cannot use in async mode.

    uvicorn selects that loop whenever it runs without a subprocess (plain
    ``uvicorn app.main:app``), and every single query would then fail with a 500.
    Better to say so once at startup than per request.
    """
    if sys.platform != "win32":
        return
    loop_name = type(asyncio.get_running_loop()).__name__
    if "Proactor" in loop_name:
        raise RuntimeError(
            f"{loop_name} cannot run psycopg in async mode. On Windows start the "
            "server so that uvicorn picks a selector loop -- e.g. "
            "`fastapi dev app/main.py` or `uvicorn app.main:app --reload` -- "
            "or run the stack in Docker/WSL."
        )


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def _managed_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """Shared session lifecycle: commit on success, rollback on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            # Only the rollback is our business here: an expected 401/404 travels
            # through this block too, and the exception handlers already log the
            # failure itself -- logging it again would bury real errors.
            logger.debug("Rolling back DB session: %r", exc)
            try:
                await session.rollback()
            except Exception:
                logger.exception("DB session rollback failed")
            raise


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Get async database session for FastAPI dependency injection.

    Use this with FastAPI Depends().
    """
    async with _managed_session(async_session_maker) as session:
        yield session


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession]:
    """Get async database session as context manager.

    Use this with 'async with' for manual session management (e.g., WebSockets).
    """
    async with _managed_session(async_session_maker) as session:
        yield session


@asynccontextmanager
async def get_worker_db_context() -> AsyncGenerator[AsyncSession]:
    """Get a short-lived async session for background workers (Celery/ARQ).

    Creates a fresh engine with NullPool on every call so there are no
    cross-fork / cross-event-loop connection issues.  The engine is disposed
    automatically when the context manager exits.
    """
    worker_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    factory = async_sessionmaker(
        worker_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            # Workers have no exception-handler layer above them, so this is the
            # only place the failure gets recorded.
            logger.exception("Worker DB session error, rolling back")
            try:
                await session.rollback()
            except Exception:
                logger.exception("DB session rollback failed")
            raise
        finally:
            await worker_engine.dispose()


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
