"""Article schemas: request filters and API response shapes."""

from datetime import date, datetime

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampSchema


class ArticleRead(TimestampSchema):
    """Single article returned by the API."""

    id: int
    site_name: str
    title: str
    url: str
    publish_date: date | None = None
    summary: str | None = None
    group_type: str
    is_external: bool
    embedding_done: bool


class ArticleDetail(ArticleRead):
    """Article with full body text, returned by the detail endpoint."""

    body_text: str | None = None


class ArticleList(BaseSchema):
    """Paginated list of articles."""

    items: list[ArticleRead]
    total: int
    skip: int
    limit: int


class ArticleQuery(BaseSchema):
    """Query parameters for the history / list endpoint."""

    group_type: str | None = Field(default=None, description="'competitor' or 'regulatory'")
    site_name: str | None = Field(default=None, description="Filter by exact site name")
    date_from: date | None = Field(default=None, description="Publish date ≥ this date")
    date_to: date | None = Field(default=None, description="Publish date ≤ this date")
    search: str | None = Field(default=None, description="Keyword search in title / site name")
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)
