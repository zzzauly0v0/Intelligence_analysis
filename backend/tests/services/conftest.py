"""Fixtures for the service layer — services built over the mocked DB session."""

from unittest.mock import AsyncMock

import pytest

from app.services.user import UserService


@pytest.fixture
def user_service(mock_db: AsyncMock) -> UserService:
    return UserService(mock_db)
