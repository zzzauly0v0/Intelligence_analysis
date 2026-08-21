from datetime import date
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.helpers import FakeArticle


class TestGetToday:
    @pytest.mark.anyio
    async def test_returns_today_articles(
        self,
        client: AsyncClient,
        mock_article_repo: AsyncMock,
        fake_article: FakeArticle,
    ):
        mock_article_repo.list_today = AsyncMock(return_value=[fake_article])

        response = await client.get("/api/v1/articles/today")

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["title"] == fake_article.title
        mock_article_repo.list_today.assert_awaited_once()

    @pytest.mark.anyio
    async def test_filters_by_group_type(
        self,
        client: AsyncClient,
        mock_article_repo: AsyncMock,
        fake_article: FakeArticle,
    ):
        mock_article_repo.list_today = AsyncMock(return_value=[fake_article])

        response = await client.get("/api/v1/articles/today?group_type=competitor")

        assert response.status_code == 200
        await_args = mock_article_repo.list_today.await_args
        assert await_args is not None
        assert await_args.kwargs["group_type"] == "competitor"

    @pytest.mark.anyio
    async def test_invalid_group_type_returns_400(
        self, client: AsyncClient, mock_article_repo: AsyncMock
    ):
        mock_article_repo.list_today = AsyncMock()

        response = await client.get("/api/v1/articles/today?group_type=bogus")

        assert response.status_code == 400
        mock_article_repo.list_today.assert_not_awaited()


class TestGetHistory:
    @pytest.mark.anyio
    async def test_returns_page_with_defaults(
        self,
        client: AsyncClient,
        mock_article_repo: AsyncMock,
        fake_article: FakeArticle,
    ):
        mock_article_repo.list_articles = AsyncMock(return_value=([fake_article], 1))

        response = await client.get("/api/v1/articles/history")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["skip"] == 0
        assert body["limit"] == 50
        assert body["items"][0]["url"] == fake_article.url

    @pytest.mark.anyio
    async def test_passes_filters_through_to_repository(
        self, client: AsyncClient, mock_article_repo: AsyncMock
    ):
        mock_article_repo.list_articles = AsyncMock(return_value=([], 0))

        response = await client.get(
            "/api/v1/articles/history",
            params={
                "group_type": "regulatory",
                "site_name": "Example News",
                "date_from": "2026-08-01",
                "date_to": "2026-08-20",
                "search": "headline",
                "skip": 10,
                "limit": 20,
            },
        )

        assert response.status_code == 200
        await_args = mock_article_repo.list_articles.await_args
        assert await_args is not None
        kwargs = await_args.kwargs
        assert kwargs["group_type"] == "regulatory"
        assert kwargs["site_name"] == "Example News"
        assert kwargs["date_from"] == date(2026, 8, 1)
        assert kwargs["date_to"] == date(2026, 8, 20)
        assert kwargs["search"] == "headline"
        assert kwargs["skip"] == 10
        assert kwargs["limit"] == 20

    @pytest.mark.anyio
    async def test_invalid_group_type_returns_400(
        self, client: AsyncClient, mock_article_repo: AsyncMock
    ):
        mock_article_repo.list_articles = AsyncMock()

        response = await client.get("/api/v1/articles/history?group_type=bogus")

        assert response.status_code == 400
        mock_article_repo.list_articles.assert_not_awaited()

    @pytest.mark.anyio
    async def test_limit_over_max_returns_422(self, client: AsyncClient):
        response = await client.get("/api/v1/articles/history?limit=201")

        assert response.status_code == 422


class TestGetArticle:
    @pytest.mark.anyio
    async def test_returns_article_with_body_text(
        self,
        client: AsyncClient,
        mock_article_repo: AsyncMock,
        fake_article: FakeArticle,
    ):
        mock_article_repo.get_by_id = AsyncMock(return_value=fake_article)

        response = await client.get(f"/api/v1/articles/{fake_article.id}")

        assert response.status_code == 200
        assert response.json()["body_text"] == fake_article.body_text

    @pytest.mark.anyio
    async def test_unknown_article_returns_404(
        self, client: AsyncClient, mock_article_repo: AsyncMock
    ):
        mock_article_repo.get_by_id = AsyncMock(return_value=None)

        response = await client.get("/api/v1/articles/999")

        assert response.status_code == 404
