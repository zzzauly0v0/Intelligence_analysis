from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AlreadyExistsError, AuthenticationError, NotFoundError
from app.core.security import get_password_hash
from app.db.models.user import UserRole
from app.schemas.user import UserRegister, UserUpdate
from app.services.user import UserService
from tests.helpers import FakeUser


class TestGetUser:
    @pytest.mark.anyio
    async def test_get_by_id_success(
        self, user_service: UserService, fake_user: FakeUser
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=fake_user)

            result = await user_service.get_by_id(fake_user.id)

            assert result == fake_user
            mock_repo.get_by_id.assert_awaited_once_with(user_service.db, fake_user.id)

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self, user_service: UserService):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError) as exc_info:
                await user_service.get_by_id(uuid4())

            assert "User not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_get_by_email(self, user_service: UserService, fake_user: FakeUser):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=fake_user)

            result = await user_service.get_by_email(fake_user.email)

            assert result == fake_user
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )

    @pytest.mark.anyio
    async def test_list_users(self, user_service: UserService, fake_user: FakeUser):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.list_users = AsyncMock(return_value=([fake_user], 1))

            result = await user_service.list_users(skip=0, limit=10, search=None)

            assert result == ([fake_user], 1)
            mock_repo.list_users.assert_awaited_once_with(
                user_service.db,
                skip=0,
                limit=10,
                search=None,
            )


class TestRegister:
    @pytest.mark.anyio
    async def test_register_first_user_becomes_admin(
        self, user_service: UserService, fake_user: FakeUser
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=None)
            mock_repo.count = AsyncMock(return_value=0)
            mock_repo.create = AsyncMock(return_value=fake_user)

            user_in = UserRegister(
                email=fake_user.email,
                full_name=fake_user.full_name,
                password="password123",
            )

            result = await user_service.register(user_in)

            assert result == fake_user
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )
            mock_repo.count.assert_awaited_once_with(user_service.db)
            mock_repo.create.assert_awaited_once()
            await_args = mock_repo.create.await_args
            assert await_args is not None
            create_kwargs = await_args.kwargs
            assert create_kwargs["email"] == user_in.email
            assert create_kwargs["full_name"] == user_in.full_name
            assert create_kwargs["role"] == UserRole.ADMIN
            assert create_kwargs["is_app_admin"] is True

    @pytest.mark.anyio
    async def test_register_with_duplicate_email_raises_already_exists(
        self, user_service: UserService, fake_user: FakeUser
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=fake_user)

            user_in = UserRegister(
                email=fake_user.email,
                full_name=fake_user.full_name,
                password="password123",
            )

            with pytest.raises(AlreadyExistsError) as exc_info:
                await user_service.register(user_in)

            assert "User with this email already exists" in str(exc_info.value)
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )


class TestAuthenticate:
    @pytest.mark.anyio
    async def test_authenticate_success(
        self, user_service: UserService, fake_user: FakeUser
    ):
        fake_user.hashed_password = get_password_hash("password123")
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=fake_user)

            result = await user_service.authenticate(fake_user.email, "password123")

            assert result == fake_user
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )

    @pytest.mark.anyio
    async def test_authenticate_with_wrong_password_raises_authentication_error(
        self, user_service: UserService, fake_user: FakeUser
    ):
        fake_user.hashed_password = get_password_hash("correctpassword")
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=fake_user)

            with pytest.raises(AuthenticationError) as exc_info:
                await user_service.authenticate(fake_user.email, "wrongpassword")

            assert "Incorrect email or password" in str(exc_info.value)
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )

    @pytest.mark.anyio
    async def test_authenticate_unknown_email_raises_authentication_error(
        self, user_service: UserService
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=None)

            with pytest.raises(AuthenticationError) as exc_info:
                await user_service.authenticate(
                    "nonexistent@example.com", "password123"
                )

            assert "Incorrect email or password" in str(exc_info.value)
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, "nonexistent@example.com"
            )

    @pytest.mark.anyio
    async def test_authenticate_inactive_user_raises_authentication_error(
        self, user_service: UserService, fake_user: FakeUser
    ):
        fake_user.is_active = False
        fake_user.hashed_password = get_password_hash("password123")
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_email = AsyncMock(return_value=fake_user)

            with pytest.raises(AuthenticationError) as exc_info:
                await user_service.authenticate(fake_user.email, "password123")

            assert "Inactive user" in str(exc_info.value)
            mock_repo.get_by_email.assert_awaited_once_with(
                user_service.db, fake_user.email
            )


class TestUpdateAndDelete:
    @pytest.mark.anyio
    async def test_update_changes_data(
        self, user_service: UserService, fake_user: FakeUser
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=fake_user)
            mock_repo.update = AsyncMock(return_value=fake_user)

            result = await user_service.update(
                fake_user.id, UserUpdate(full_name="Updated Name")
            )

            assert result == fake_user
            mock_repo.update.assert_awaited_once()
            await_args = mock_repo.update.await_args
            assert await_args is not None
            update_kwargs = await_args.kwargs
            assert update_kwargs["db_user"] == fake_user
            assert update_kwargs["update_data"]["full_name"] == "Updated Name"

    @pytest.mark.anyio
    async def test_update_with_password_revokes_sessions(
        self, user_service: UserService, fake_user: FakeUser
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=fake_user)
            mock_repo.update = AsyncMock(return_value=fake_user)
            mock_repo.deactivate_all_user_sessions = AsyncMock(return_value=1)

            result = await user_service.update(
                fake_user.id, UserUpdate(password="newpassword123")
            )

            assert result == fake_user
            mock_repo.update.assert_awaited_once()
            await_args = mock_repo.update.await_args
            assert await_args is not None
            update_data = await_args.kwargs["update_data"]
            assert "hashed_password" in update_data
            mock_repo.deactivate_all_user_sessions.assert_awaited_once_with(
                user_service.db, fake_user.id
            )

    @pytest.mark.anyio
    async def test_delete_user(self, user_service: UserService, fake_user: FakeUser):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=fake_user)
            mock_repo.delete = AsyncMock(return_value=None)

            await user_service.delete(fake_user.id)

            mock_repo.delete.assert_awaited_once_with(user_service.db, fake_user)

    @pytest.mark.anyio
    async def test_delete_unknown_user_raises_not_found(
        self, user_service: UserService
    ):
        with patch("app.services.user.user_repo") as mock_repo:
            mock_repo.get_by_id = AsyncMock(return_value=None)

            with pytest.raises(NotFoundError):
                await user_service.delete(uuid4())

            mock_repo.get_by_id.assert_awaited_once()
