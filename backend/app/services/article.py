"""Article service: query use-cases for the articles table.

Keeps routes thin: every domain rule (date defaulting, group validation …)
lives here, not in the route handler.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.models.article import Article
from app.repository import article as article_repo
from app.schemas.article import ArticleQuery

VALID_GROUP_TYPES = frozenset({"competitor", "regulatory"})


class ArticleService:
    """Read-side use cases for the articles table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, article_id: int) -> Article:
        article = await article_repo.get_by_id(self.db, article_id)
        if article is None:
            raise NotFoundError("Article not found", details={"article_id": article_id})
        return article

    async def list_today(self, *, group_type: str | None = None) -> list[Article]:
        """All articles with publish_date = today."""
        if group_type and group_type not in VALID_GROUP_TYPES:
            raise ValueError(f"group_type must be one of {VALID_GROUP_TYPES}")
        return await article_repo.list_today(self.db, group_type=group_type)

    async def list_history(self, query: ArticleQuery) -> tuple[list[Article], int]:
        """Paginated history with optional filters."""
        if query.group_type and query.group_type not in VALID_GROUP_TYPES:
            raise ValueError(f"group_type must be one of {VALID_GROUP_TYPES}")
        return await article_repo.list_articles(
            self.db,
            skip=query.skip,
            limit=query.limit,
            group_type=query.group_type,
            site_name=query.site_name,
            date_from=query.date_from,
            date_to=query.date_to,
            search=query.search,
        )
