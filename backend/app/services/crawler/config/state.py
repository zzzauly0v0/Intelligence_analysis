"""Per-site last-article timestamps — the only thing that decides freshness.

There is no keyword matching and no seen-URL diffing: an item is new when its
publish date is newer than the timestamp this store holds for its site.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from app.services.crawler.common.constants import BASELINE_OVERRIDE, DEFAULT_LAST_TIME
from app.services.crawler.fetch.dates import DATE_FMT, parse_dt
from app.services.crawler.common.paths import LAST_TIMES_PATH

logger = logging.getLogger(__name__)


def load_last_times() -> dict[str, str]:
    """{site_name: 'YYYY-mm-dd HH:MM:SS'} of last-seen article times."""
    if not LAST_TIMES_PATH.exists():
        return {}
    try:
        return json.loads(LAST_TIMES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read last-times store, starting fresh: %s", exc)
        return {}


def save_last_times(last_times: dict[str, str]) -> None:
    LAST_TIMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_TIMES_PATH.write_text(
        json.dumps(last_times, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_baseline(last_times: dict[str, str], site_name: str) -> tuple[datetime, str]:
    """(baseline_dt, baseline_str) for a site, guarding against a corrupt entry.

    An unparseable stored timestamp falls back to DEFAULT_LAST_TIME with a loud
    warning: left alone it would make every dated article look new and trigger an
    email flood. BASELINE_OVERRIDE, when valid, replaces every site's baseline.
    """
    if BASELINE_OVERRIDE:
        override_dt = parse_dt(BASELINE_OVERRIDE)
        if override_dt is not None:
            return override_dt, BASELINE_OVERRIDE
        logger.warning("MONITOR_BASELINE=%r is invalid; ignoring it.", BASELINE_OVERRIDE)

    stored = last_times.get(site_name, DEFAULT_LAST_TIME)
    baseline_dt = parse_dt(stored)
    if baseline_dt is None:
        logger.warning(
            "Stored last-time for '%s' is invalid (%r); falling back to default baseline %s",
            site_name, stored, DEFAULT_LAST_TIME,
        )
        stored = DEFAULT_LAST_TIME
        baseline_dt = parse_dt(DEFAULT_LAST_TIME)
        assert baseline_dt is not None  # DEFAULT_LAST_TIME is a valid constant
    return baseline_dt, stored


def clamp_to_now(site_name: str, latest_dt: datetime) -> str:
    """The timestamp to save for a site, never in the future.

    Some lists carry future-dated entries (Döhler mixes trade-fair cards into its
    newsroom). Saving one as the baseline would silently mute every real article
    until that event date passed.
    """
    saved = min(latest_dt, datetime.now())
    if saved != latest_dt:
        logger.warning(
            "    ⚠️  %s 最高文章时间 %s 在未来（活动/展会条目），时间戳按当前时刻封顶保存",
            site_name, latest_dt.strftime(DATE_FMT),
        )
    return saved.strftime(DATE_FMT)
