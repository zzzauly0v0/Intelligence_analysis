"""Persisting scraped hits to the articles table.

The single integration point between the scraper and the FastAPI / SQLAlchemy
stack — everything else in this package is plain synchronous Python with no DB
dependency. Call it after a scan:

    from app.services.crawler import fetch_all_new_hits, save_hits_to_db

    hits = fetch_all_new_hits()
    summaries = summarize_hits(hits, summarizer)
    asyncio.run(save_hits_to_db(hits, summaries))
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from app.db.session import get_worker_db_context
from app.repository import article as article_repo
from app.services.crawler.common.models import Hit, Summary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def parse_publish_date(value: object) -> date | None:
    """A hit's publish_date as a ``date``.

    The scraper stores "2026-08-18" or "2026-08-18 00:00:00"; the time part is CMS
    noise. Anything unparseable becomes NULL rather than failing the batch.
    """
    if value is None:
        return None
    text = str(value).strip().split(" ")[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        logger.warning("Could not parse publish_date %r — storing as NULL", value)
        return None


async def save_hits_to_db(hits: Sequence[Hit], summaries: Sequence[Summary]) -> int:
    """Upsert a batch of hits; returns the number of rows written.

    ``get_worker_db_context`` is a short-lived NullPool session that commits on
    success and rolls back on error — for background work outside a request.
    """
    if not hits:
        return 0

    async with get_worker_db_context() as db:
        count = await _save(db, hits, summaries)
        logger.info("Saved %d articles to database", count)
        return count


async def _save(db: AsyncSession, hits: Sequence[Hit], summaries: Sequence[Summary]) -> int:
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
            publish_date=parse_publish_date(hit.get("publish_date")),
            body_text=hit.get("body_text") or None,
            summary=summary_text or None,
            group_type=hit.get("group", "competitor"),
            is_external=bool(hit.get("external", False)),
        )
        saved += 1
    return saved
