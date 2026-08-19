"""Article repository: database reads and writes for the articles table.

Repository functions never commit — the request-scoped session in
``app.db.session`` commits once the request succeeds. They ``flush`` (and
``refresh`` where a server default is needed) so callers see generated values.
"""

from datetime import date

from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article import Article


async def get_by_id(db: AsyncSession, article_id: int) -> Article | None:
    return await db.get(Article, article_id)


async def get_by_url(db: AsyncSession, url: str) -> Article | None:
    result = await db.execute(select(Article).where(Article.url == url))
    return result.scalar_one_or_none()


async def list_articles(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    group_type: str | None = None,
    site_name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
) -> tuple[list[Article], int]:
    """Return one page of articles plus the total number of matches."""
    stmt = select(Article)
    if group_type:
        stmt = stmt.where(Article.group_type == group_type)
    if site_name:
        stmt = stmt.where(Article.site_name == site_name)
    if date_from:
        stmt = stmt.where(Article.publish_date >= date_from)
    if date_to:
        stmt = stmt.where(Article.publish_date <= date_to)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                Article.title.ilike(pattern, escape="\\"),
                Article.site_name.ilike(pattern, escape="\\"),
            )
        )

    total_result = await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    result = await db.execute(
        stmt.order_by(Article.publish_date.desc().nullslast(), Article.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all()), total_result.scalar_one()


async def list_today(
    db: AsyncSession,
    *,
    group_type: str | None = None,
) -> list[Article]:
    """Return all articles with publish_date = today (server date)."""
    today = cast(func.now(), Date)
    stmt = select(Article).where(Article.publish_date == today)
    if group_type:
        stmt = stmt.where(Article.group_type == group_type)
    stmt = stmt.order_by(Article.site_name, Article.publish_date.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert_article(
    db: AsyncSession,
    *,
    site_name: str,
    title: str,
    url: str,
    publish_date: date | None = None,
    body_text: str | None = None,
    summary: str | None = None,
    group_type: str = "competitor",
    is_external: bool = False,
) -> Article:
    """Insert or update an article by URL (natural dedup key).

    On conflict the title, body, summary and publish_date are refreshed so
    a re-crawl of the same URL picks up corrections; embedding_done is reset
    to False only when body_text actually changed, so the RAG pipeline is not
    re-triggered needlessly. created_at is never overwritten.
    """
    from sqlalchemy import case, literal

    stmt = (
        pg_insert(Article)
        .values(
            site_name=site_name,
            title=title,
            url=url,
            publish_date=publish_date,
            body_text=body_text,
            summary=summary,
            group_type=group_type,
            is_external=is_external,
            embedding_done=False,
        )
        .on_conflict_do_update(
            index_elements=["url"],
            set_={
                "title": title,
                "publish_date": publish_date,
                "body_text": body_text,
                "summary": summary,
                "group_type": group_type,
                "is_external": is_external,
                # Reset embedding flag only when body_text actually changed, so
                # an identical re-crawl does not trigger the RAG pipeline again.
                "embedding_done": case(
                    (Article.body_text != literal(body_text), False),
                    else_=Article.embedding_done,
                ),
            },
        )
        .returning(Article)
    )
    result = await db.execute(stmt)
    await db.flush()
    row = result.scalar_one()
    return row


async def mark_embedding_done(db: AsyncSession, article_id: int) -> None:
    article = await db.get(Article, article_id)
    if article is not None:
        article.embedding_done = True
        db.add(article)
        await db.flush()


async def list_pending_embedding(
    db: AsyncSession, *, limit: int = 100
) -> list[Article]:
    """Articles that have body text but have not been vectorised yet."""
    result = await db.execute(
        select(Article)
        .where(
            Article.embedding_done.is_(False),
            Article.is_external.is_(False),
            Article.body_text.is_not(None),
        )
        .order_by(Article.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())
