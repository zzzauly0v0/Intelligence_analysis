from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.helpers import FakeUser, auth_headers_for


class TestHealthCheck:
    @pytest.mark.anyio
    async def test_health_check_returns_true(self, client: AsyncClient):
        response = await client.get("/api/v1/utils/health-check/")

        assert response.status_code == 200
        assert response.json() is True


class TestTestEmail:
    @pytest.mark.anyio
    async def test_requires_authentication(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/utils/test-email/", params={"email_to": "someone@example.com"}
        )

        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_requires_admin(
        self,
        client: AsyncClient,
        mock_user_repo: AsyncMock,
        fake_user: FakeUser,
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_user)

        response = await client.post(
            "/api/v1/utils/test-email/",
            params={"email_to": "someone@example.com"},
            headers=auth_headers_for(fake_user),
        )

        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_admin_sends_test_email(
        self,
        client: AsyncClient,
        mock_user_repo: AsyncMock,
        fake_admin: FakeUser,
    ):
        mock_user_repo.get_by_id = AsyncMock(return_value=fake_admin)

        with patch("app.api.routes.v1.utils.send_email", MagicMock()) as mock_send:
            response = await client.post(
                "/api/v1/utils/test-email/",
                params={"email_to": "someone@example.com"},
                headers=auth_headers_for(fake_admin),
            )

        assert response.status_code == 201
        assert response.json()["message"] == "Test email sent"
        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs["email_to"] == "someone@example.com"
