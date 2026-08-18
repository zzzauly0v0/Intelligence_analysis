"""Crawler service: clean hit dicts from src/fetch_monitor_sites.py and
persist them to the articles table.

This is the single integration point between the legacy scraper and the
FastAPI / SQLAlchemy stack. Call ``save_hits_to_db`` after
``fetch_all_new_hits()`` returns.

Usage (from src/run_monitor.py):

    import asyncio
    from app.services.crawler import save_hits_to_db

    hits = fetch_all_new_hits()
    summaries = summarize_hits(hits, summarizer)   # existing logic
    asyncio.run(save_hits_to_db(hits, summaries))
"""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_worker_db_context
from app.repository import article as article_repo

logger = logging.getLogger(__name__)


def _parse_date(value: object) -> date | None:
    """Coerce a publish_date value from a hit dict to a ``date``.

    The scraper returns strings like "2026-08-18" or "2026-08-18 00:00:00".
    Accept either; return None for anything unparseable.
    """
    if value is None:
        return None
    s = str(value).strip().split(" ")[0]  # drop time component if present
    try:
        return date.fromisoformat(s)
    except ValueError:
        logger.warning("Could not parse publish_date %r — storing as NULL", value)
        return None


async def _save(
    db: AsyncSession,
    hits: list[dict],
    summaries: list[tuple[str | None, str]],
) -> int:
    """Upsert all hits; return the number of rows written."""
    saved = 0
    for hit, (_model, summary_text) in zip(hits, summaries, strict=True):
        url = hit.get("url", "").strip()
        title = hit.get("title", "").strip()
        if not url or not title:
            logger.warning("Skipping hit with missing url/title: %r", hit)
            continue

        await article_repo.upsert_article(
            db,
            site_name=hit.get("site", ""),
            title=title,
            url=url,
            publish_date=_parse_date(hit.get("publish_date")),
            body_text=hit.get("body_text") or None,
            summary=summary_text or None,
            group_type=hit.get("group", "competitor"),
            is_external=bool(hit.get("external", False)),
        )
        saved += 1

    return saved


async def save_hits_to_db(
    hits: list[dict],
    summaries: list[tuple[str | None, str]],
) -> int:
    """Persist a batch of scraper hits to the articles table.

    Uses ``get_worker_db_context`` — a short-lived NullPool session that
    auto-commits on success and rolls back on error, designed for background
    workers that run outside a FastAPI request context.

    Returns the number of rows successfully upserted.
    """
    if not hits:
        return 0

    async with get_worker_db_context() as db:
        count = await _save(db, hits, summaries)
        logger.info("Saved %d articles to database", count)
        return count
