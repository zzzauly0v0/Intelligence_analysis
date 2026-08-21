"""Fixtures for the API layer — an ASGI client plus the repositories behind it."""

from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db_session
from app.main import create_app


@pytest.fixture
async def client(mock_db: AsyncMock) -> AsyncGenerator[AsyncClient]:
    """Drive the real app in-process: routing, auth, RBAC and serialization all run."""
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def mock_user_repo() -> Iterator[MagicMock]:
    """Patches the repository the ``UserService`` -- and therefore auth -- calls."""
    with patch("app.services.user.user_repo") as repo:
        yield repo


@pytest.fixture
def mock_article_repo() -> Iterator[MagicMock]:
    """Patches the repository the ``ArticleService`` calls."""
    with patch("app.services.article.article_repo") as repo:
        yield repo
