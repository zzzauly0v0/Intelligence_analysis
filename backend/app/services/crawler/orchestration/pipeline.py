"""The scrape: every enabled site -> the items published since we last looked.

Per site:
  1. read candidates and their list-page dates (no article opened);
  2. keep the ones newer than that site's saved timestamp;
  3. open ONLY those, oldest first, for body text and a firmer date;
  4. probe the undated candidates' detail pages, capped;
  5. advance the saved timestamp only past what was actually processed.

Items whose date can't be read anywhere are skipped rather than carried, so they
are never re-sent on every run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from operator import itemgetter
from pathlib import Path
from typing import Any

from app.services.crawler.fetch.browser import browser_session, load_page
from app.services.crawler.common.constants import (
    BASELINE_OVERRIDE,
    MAX_NEW_ITEMS_TO_OPEN,
    MAX_UNDATED_TO_PROBE,
    MIN_ANCHORS_ON_NEWS_PAGE,
)
from app.services.crawler.fetch.dates import parse_dt
from app.services.crawler.fetch.detail import fetch_article
from app.services.crawler.fetch.extract import parse_list_page
from app.services.crawler.fetch.json_api import (
    fetch_article_api,
    is_api_site,
    list_candidates,
)
from app.services.crawler.common.models import (
    ArticleContent,
    Candidate,
    ExtractRules,
    Hit,
    Outcome,
)
from app.services.crawler.delivery.reporting import setup_logging, write_summary
from app.services.crawler.config.sites import load_sites
from app.services.crawler.config.state import (
    clamp_to_now,
    load_last_times,
    resolve_baseline,
    save_last_times,
)
from app.services.crawler.fetch.titles import needs_title_rescue

logger = logging.getLogger(__name__)

# (candidate) -> its article content; hides whether we drive a browser or an API.
ArticleFetcher = Callable[[Candidate], ArticleContent]


@dataclass(frozen=True, slots=True)
class ProbeStats:
    """What the undated-candidate probe did, for the run summary."""

    probed: int = 0
    dated_via_detail: int = 0
    skipped_external: int = 0


@dataclass(frozen=True, slots=True)
class SiteScan:
    outcome: Outcome
    hits: list[Hit] = field(default_factory=list)
    # Newest publish date actually processed; the new saved timestamp.
    latest_dt: datetime | None = None


@dataclass(frozen=True, slots=True)
class MonitorRun:
    hits: list[Hit]
    outcomes: list[Outcome]
    # Baseline each site was compared against, captured BEFORE timestamps were
    # advanced, so a digest can honestly say "newer than X".
    baselines: dict[str, str]
    log_path: Path
    summary_path: Path


def fetch_all_new_hits() -> list[Hit]:
    """Every site's new items. Thin wrapper over ``scan_all_sites``."""
    return scan_all_sites().hits


def scan_all_sites() -> MonitorRun:
    """Scan every enabled site, persist the new timestamps, write the summary."""
    log_path, summary_path = setup_logging()
    logger.info("Log file: %s", log_path)

    sites = load_sites()
    last_times = load_last_times()
    baselines: dict[str, str] = {}
    hits: list[Hit] = []
    outcomes: list[Outcome] = []

    with browser_session() as driver:
        for site in sites:
            name = site["name"]
            baseline_dt, baselines[name] = resolve_baseline(last_times, name)
            logger.info("=== %s ===", name)
            logger.info("    %s", site["news_url"])
            logger.info("    Last article time: %s", baselines[name])

            scan = scan_site(driver, site, baseline_dt)
            hits.extend(scan.hits)
            outcomes.append(scan.outcome)
            if scan.latest_dt is not None:
                last_times[name] = clamp_to_now(name, scan.latest_dt)

    if BASELINE_OVERRIDE:
        logger.info(
            "MONITOR_BASELINE override active — NOT saving timestamps "
            "(test run, production state untouched)."
        )
    else:
        save_last_times(last_times)

    write_summary(summary_path, outcomes)
    logger.info("Done. %d new hits total. Full log: %s", len(hits), log_path)
    return MonitorRun(hits, outcomes, baselines, log_path, summary_path)


def scan_site(driver: Any, site: dict[str, Any], baseline_dt: datetime) -> SiteScan:
    """One site's new items, its outcome, and the timestamp to save."""
    name = site["name"]
    rules = ExtractRules.from_site(site)

    candidates = _collect_candidates(driver, site, rules)
    if candidates is None:
        return SiteScan(Outcome(name, "load_failed", f"新闻页加载失败: {rules.news_url}"))
    if not candidates:
        return SiteScan(Outcome(name, "no_items", _no_items_detail(site)))

    dated, undated = _partition_by_baseline(candidates, baseline_dt)
    batch = dated[:MAX_NEW_ITEMS_TO_OPEN]
    if len(dated) > len(batch):
        logger.warning(
            "    %d dated-new items; processing oldest %d this run, rest next run",
            len(dated), MAX_NEW_ITEMS_TO_OPEN,
        )
    logger.info(
        "    New (by list date): %d | undated on list: %d | opening dated: %d",
        len(dated), len(undated), len(batch),
    )

    fetch = _article_fetcher(driver, site)
    dated_hits, dated_latest = _open_dated(batch, site, fetch)
    undated_hits, undated_latest, stats = _probe_undated(undated, site, fetch, baseline_dt)

    if stats.probed:
        logger.info("    Probed %d undated items; got date from %d", stats.probed, stats.dated_via_detail)
    if stats.skipped_external:
        logger.info(
            "    Skipped %d undated external/document link(s) "
            "(not worth loading a third-party page for a date)",
            stats.skipped_external,
        )

    hits = dated_hits + undated_hits
    return SiteScan(
        outcome=_classify(name, len(candidates), hits, len(undated), stats),
        hits=hits,
        latest_dt=max((dt for dt in (dated_latest, undated_latest) if dt is not None), default=None),
    )


def _collect_candidates(
    driver: Any, site: dict[str, Any], rules: ExtractRules
) -> list[Candidate] | None:
    """Candidates for a site, or None when its news page could not be loaded."""
    if is_api_site(site):
        # A JSON-API site skips the browser entirely: list and bodies come from
        # the backend feed described in site["api"].
        candidates = list_candidates(site)
        logger.info("    Extracted %d candidate items (JSON API)", len(candidates))
        return candidates

    if not load_page(
        driver, rules.news_url,
        min_anchors=MIN_ANCHORS_ON_NEWS_PAGE,
        wait_for=site.get("wait_for"),
    ):
        return None

    candidates = parse_list_page(driver.page_source, rules)
    logger.info("    Extracted %d candidate items", len(candidates))
    return candidates


def _no_items_detail(site: dict[str, Any]) -> str:
    if is_api_site(site):
        return "JSON API 未返回任何新闻条目（接口结构可能已变）"
    return "列表页一个候选链接都没解析出来（可能是 JS 动态渲染 / 结构特殊）"


def _article_fetcher(driver: Any, site: dict[str, Any]) -> ArticleFetcher:
    if is_api_site(site):
        return lambda candidate: fetch_article_api(site, candidate)
    return lambda candidate: fetch_article(driver, candidate.url)


def _partition_by_baseline(
    candidates: list[Candidate], baseline_dt: datetime
) -> tuple[list[tuple[datetime, Candidate]], list[Candidate]]:
    """Split into (newer-than-baseline, sorted oldest first) and (no list date).

    Oldest first so that, if the open budget is exceeded, the ones left behind are
    the newest — and the saved timestamp only advances past what ran, so they come
    back next run.
    """
    dated: list[tuple[datetime, Candidate]] = []
    undated: list[Candidate] = []
    for candidate in candidates:
        item_dt = parse_dt(candidate.list_date)
        if item_dt is None:
            undated.append(candidate)
        elif item_dt > baseline_dt:
            dated.append((item_dt, candidate))
    dated.sort(key=itemgetter(0))
    return dated, undated


def _open_dated(
    batch: list[tuple[datetime, Candidate]], site: dict[str, Any], fetch: ArticleFetcher
) -> tuple[list[Hit], datetime | None]:
    """Open each already-known-new item for its body."""
    hits: list[Hit] = []
    latest: datetime | None = None
    for item_dt, candidate in batch:
        if candidate.external:
            # Title + link only — don't drive the browser to someone else's site
            # (or a PDF viewer) just to throw the body away.
            hits.append(_external_hit(site, candidate))
        else:
            content = fetch(candidate)
            hits.append(_article_hit(site, candidate, content, content.publish_date or candidate.list_date))
        latest = item_dt if latest is None or item_dt > latest else latest
    return hits, latest


def _probe_undated(
    undated: list[Candidate], site: dict[str, Any], fetch: ArticleFetcher, baseline_dt: datetime
) -> tuple[list[Hit], datetime | None, ProbeStats]:
    """Open undated candidates to read a date there, then judge freshness."""
    hits: list[Hit] = []
    latest: datetime | None = None
    probed = 0
    dated_via_detail = 0
    skipped_external = 0

    for candidate in undated:
        if probed >= MAX_UNDATED_TO_PROBE:
            logger.warning(
                "    Reached undated-probe cap (%d); %d undated items left unchecked this run",
                MAX_UNDATED_TO_PROBE, len(undated) - probed,
            )
            break
        # Undated external items are dropped, not probed: loading a third-party
        # page or a PDF purely to read a date whose body we then discard is slow
        # and unreliable. The one allow_external site dates almost all of its
        # links on the list page, so this costs a couple of items and keeps
        # undated reposts from ever entering as false "new".
        if candidate.external:
            skipped_external += 1
            continue

        probed += 1
        content = fetch(candidate)
        detail_dt = parse_dt(content.publish_date)
        if detail_dt is None:
            continue  # no date even on the detail page
        dated_via_detail += 1
        if detail_dt > baseline_dt:
            hits.append(_article_hit(site, candidate, content, content.publish_date))
            latest = detail_dt if latest is None or detail_dt > latest else latest

    return hits, latest, ProbeStats(probed, dated_via_detail, skipped_external)


def _base_hit(site: dict[str, Any], candidate: Candidate, publish_date: str | None) -> Hit:
    return {
        "site": site["name"],
        "title": candidate.title,
        "url": candidate.url,
        "publish_date": publish_date,
        "body_text": "",
        "external": False,
        # Routing tag: "regulatory" sites get the regulatory-affairs prompt and
        # their own digest.
        "group": site.get("group", "competitor"),
    }


def _external_hit(site: dict[str, Any], candidate: Candidate) -> Hit:
    """A third-party repost, video or document: title + link, no summary."""
    logger.info(
        "      ✓ NEW(外部) [%s] %s  → %s",
        candidate.list_date, candidate.title[:56], candidate.url[:60],
    )
    return _base_hit(site, candidate, candidate.list_date) | {"external": True}


def _article_hit(
    site: dict[str, Any], candidate: Candidate, content: ArticleContent, publish_date: str | None
) -> Hit:
    """A scraped article, with its list title rescued if it was junk.

    Some cards expose only an accessibility label ("Go to article details"), so the
    list title is useless even though the URL is a real article.
    """
    title = candidate.title
    if content.detail_title and needs_title_rescue(title):
        logger.info("      ↳ 标题抢救: %r → %r", title[:40], content.detail_title[:60])
        title = content.detail_title

    body_len = len(content.body_text.strip())
    if body_len:
        logger.info("      ✓ NEW [%s] %s  (正文 %d 字)", publish_date, title[:60], body_len)
    else:
        logger.warning("      ✓ NEW [%s] %s  (正文抓取为空)", publish_date, title[:60])

    return _base_hit(site, candidate, publish_date) | {
        "title": title,
        "body_text": content.body_text,
    }


def _classify(
    name: str, candidate_count: int, hits: list[Hit], undated_count: int, stats: ProbeStats
) -> Outcome:
    """Which stage this site got stuck at, if any."""
    external = sum(1 for hit in hits if hit["external"])
    body_ok = sum(1 for hit in hits if not hit["external"] and hit["body_text"].strip())
    body_fail = len(hits) - external - body_ok
    external_note = f"，外部/文档链接 {external} 条（仅标题+链接）" if external else ""

    if not hits:
        if undated_count and stats.probed and not stats.dated_via_detail:
            return Outcome(
                name, "all_undated",
                f"{candidate_count} 候选，列表无日期且详情页也读不到日期"
                f"（探测 {stats.probed} 篇，需针对该站适配日期位置）",
            )
        return Outcome(name, "no_new", f"候选 {candidate_count}，无更新（≤基线）")

    # Only a real failure if we actually tried to fetch bodies: a site whose new
    # items are all external is title+link by design, not broken.
    if body_ok == 0 and body_fail > 0:
        return Outcome(
            name, "body_empty",
            f"打开 {body_fail} 篇，正文全部为空（正文容器非 article/main，需适配选择器）"
            + external_note,
        )

    detail_note = f"，其中经详情页补日期 {stats.dated_via_detail}" if stats.dated_via_detail else ""
    return Outcome(
        name, "ok",
        f"新增 {len(hits)} 篇，正文成功 {body_ok}"
        + (f"，空 {body_fail}" if body_fail else "")
        + external_note
        + detail_note,
    )


if __name__ == "__main__":
    for hit in fetch_all_new_hits():
        logger.info(
            "  [%s] (%s) %s\n     %s", hit["site"], hit["publish_date"], hit["title"], hit["url"]
        )
