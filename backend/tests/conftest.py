"""Global fixtures — everything here is available to every test package.

Layer-specific wiring lives in the nearest ``conftest.py`` instead:
``tests/api/conftest.py`` (ASGI client, patched repositories) and
``tests/services/conftest.py`` (service instances).
"""

from unittest.mock import AsyncMock

import pytest

from app.db.models.user import UserRole
from tests.helpers import FakeArticle, FakeUser


@pytest.fixture
def anyio_backend() -> str:
    """Pin anyio to a single backend so async tests don't run twice (asyncio + trio)."""
    return "asyncio"


@pytest.fixture
def mock_db() -> AsyncMock:
    """Stand-in DB session.

    Nothing under test touches it directly -- each test patches the repository
    functions it needs, so the session only has to exist and be awaitable.
    """
    return AsyncMock()


@pytest.fixture
def fake_user() -> FakeUser:
    return FakeUser()


@pytest.fixture
def fake_admin() -> FakeUser:
    return FakeUser(email="admin@example.com", role=UserRole.ADMIN)


@pytest.fixture
def fake_article() -> FakeArticle:
    return FakeArticle()
