from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.security import get_password_hash
from app.db.models.user import UserRole
from tests.helpers import FakeUser, auth_headers_for, by_id


class TestSignup:
    @pytest.mark.anyio
    async def test_signup_success_promotes_first_user_to_admin(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_email = AsyncMock(return_value=None)
        mock_user_repo.count = AsyncMock(return_value=0)
        mock_user_repo.create = AsyncMock(return_value=fake_user)

        response = await client.post(
            "/api/v1/users/signup",
            json={
                "email": fake_user.email,
                "full_name": fake_user.full_name,
                "password": "password123",
            },
        )

        assert response.status_code == 201
        assert response.json()["email"] == fake_user.email
        await_args = mock_user_repo.create.await_args
        assert await_args is not None
        create_kwargs = await_args.kwargs
        assert create_kwargs["role"] == UserRole.ADMIN
        assert create_kwargs["is_app_admin"] is True

    @pytest.mark.anyio
    async def test_signup_with_existing_email_returns_409(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_email = AsyncMock(return_value=fake_user)

        response = await client.post(
            "/api/v1/users/signup",
            json={"email": fake_user.email, "password": "password123"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "ALREADY_EXISTS"

    @pytest.mark.anyio
    async def test_signup_with_short_password_returns_422(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/users/signup",
            json={"email": "user@example.com", "password": "short"},
        )

        assert response.status_code == 422


class TestLogin:
    @pytest.mark.anyio
    async def test_login_success_returns_token_pair(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        fake_user.hashed_password = get_password_hash("password123")
        mock_user_repo.get_by_email = AsyncMock(return_value=fake_user)
        mock_user_repo.create_session = AsyncMock(return_value=None)

        response = await client.post(
            "/api/v1/users/login/access-token",
            data={"username": fake_user.email, "password": "password123"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]

    @pytest.mark.anyio
    async def test_login_with_wrong_password_returns_401(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        fake_user.hashed_password = get_password_hash("password123")
        mock_user_repo.get_by_email = AsyncMock(return_value=fake_user)

        response = await client.post(
            "/api/v1/users/login/access-token",
            data={"username": fake_user.email, "password": "wrongpassword"},
        )

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


class TestCurrentUser:
    @pytest.mark.anyio
    async def test_read_me_without_token_returns_401(self, client: AsyncClient):
        response = await client.get("/api/v1/users/me")

        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_read_me_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)

        response = await client.get(
            "/api/v1/users/me", headers=auth_headers_for(fake_user)
        )

        assert response.status_code == 200
        assert response.json()["email"] == fake_user.email

    @pytest.mark.anyio
    async def test_update_me_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)
        updated = FakeUser(id=fake_user.id, email=fake_user.email, full_name="New Name")
        mock_user_repo.update = AsyncMock(return_value=updated)

        response = await client.patch(
            "/api/v1/users/me",
            json={"full_name": "New Name"},
            headers=auth_headers_for(fake_user),
        )

        assert response.status_code == 200
        assert response.json()["full_name"] == "New Name"

    @pytest.mark.anyio
    async def test_delete_me_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)
        mock_user_repo.delete = AsyncMock(return_value=None)

        response = await client.delete(
            "/api/v1/users/me", headers=auth_headers_for(fake_user)
        )

        assert response.status_code == 200
        mock_user_repo.delete.assert_awaited_once()
        await_args = mock_user_repo.delete.await_args
        assert await_args is not None
        assert await_args.args[1] is fake_user

    @pytest.mark.anyio
    async def test_delete_me_as_app_admin_returns_403(
        self, client: AsyncClient, mock_user_repo: AsyncMock
    ):
        app_admin = FakeUser(is_app_admin=True)
        mock_user_repo.get_by_id = AsyncMock(return_value=app_admin)

        response = await client.delete(
            "/api/v1/users/me", headers=auth_headers_for(app_admin)
        )

        assert response.status_code == 403


class TestAdminUserManagement:
    @pytest.mark.anyio
    async def test_list_users_as_non_admin_returns_403(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)

        response = await client.get(
            "/api/v1/users/", headers=auth_headers_for(fake_user)
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_list_users_as_admin_success(
        self,
        client: AsyncClient,
        mock_user_repo: AsyncMock,
        fake_admin: FakeUser,
        fake_user: FakeUser,
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_admin)
        mock_user_repo.list_users = AsyncMock(return_value=([fake_user], 1))

        response = await client.get(
            "/api/v1/users/", headers=auth_headers_for(fake_admin)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["email"] == fake_user.email

    @pytest.mark.anyio
    async def test_create_user_as_admin_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_admin)
        mock_user_repo.get_by_email = AsyncMock(return_value=None)
        created = FakeUser(email="new@example.com")
        mock_user_repo.create = AsyncMock(return_value=created)

        response = await client.post(
            "/api/v1/users/",
            json={"email": "new@example.com", "password": "password123"},
            headers=auth_headers_for(fake_admin),
        )

        assert response.status_code == 201
        assert response.json()["email"] == "new@example.com"


class TestReadUserById:
    @pytest.mark.anyio
    async def test_read_own_profile_by_id_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_user))

        response = await client.get(
            f"/api/v1/users/{fake_user.id}", headers=auth_headers_for(fake_user)
        )

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_read_other_users_profile_as_non_admin_returns_403(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        other = FakeUser(email="other@example.com")
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_user, other))

        response = await client.get(
            f"/api/v1/users/{other.id}", headers=auth_headers_for(fake_user)
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_read_other_users_profile_as_admin_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        target = FakeUser(email="target@example.com")
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_admin, target))

        response = await client.get(
            f"/api/v1/users/{target.id}", headers=auth_headers_for(fake_admin)
        )

        assert response.status_code == 200
        assert response.json()["email"] == target.email

    @pytest.mark.anyio
    async def test_read_unknown_user_as_admin_returns_404(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_admin))

        response = await client.get(
            f"/api/v1/users/{uuid4()}", headers=auth_headers_for(fake_admin)
        )

        assert response.status_code == 404


class TestUpdateAndDeleteUserAdmin:
    @pytest.mark.anyio
    async def test_update_user_as_admin_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        target = FakeUser(email="target@example.com")
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_admin, target))
        updated = FakeUser(id=target.id, email=target.email, full_name="Renamed")
        mock_user_repo.update = AsyncMock(return_value=updated)

        response = await client.patch(
            f"/api/v1/users/{target.id}",
            json={"full_name": "Renamed"},
            headers=auth_headers_for(fake_admin),
        )

        assert response.status_code == 200
        assert response.json()["full_name"] == "Renamed"

    @pytest.mark.anyio
    async def test_update_user_as_non_admin_returns_403(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_user: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)

        response = await client.patch(
            f"/api/v1/users/{uuid4()}",
            json={"full_name": "Renamed"},
            headers=auth_headers_for(fake_user),
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_delete_user_as_admin_success(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        target = FakeUser(email="target@example.com")
        mock_user_repo.get_by_id = AsyncMock(side_effect=by_id(fake_admin, target))
        mock_user_repo.delete = AsyncMock(return_value=None)

        response = await client.delete(
            f"/api/v1/users/{target.id}", headers=auth_headers_for(fake_admin)
        )

        assert response.status_code == 200

    @pytest.mark.anyio
    async def test_delete_self_as_admin_returns_403(
        self, client: AsyncClient, mock_user_repo: AsyncMock, fake_admin: FakeUser
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_admin)

        response = await client.delete(
            f"/api/v1/users/{fake_admin.id}", headers=auth_headers_for(fake_admin)
        )

        assert response.status_code == 403
