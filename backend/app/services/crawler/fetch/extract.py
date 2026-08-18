"""Turning a news-list page into candidates.

Freshness is decided from the LIST page: each candidate carries the date printed
next to it, so navigation and footer links never consume the article-open budget
and a real article is never missed because junk links used up the quota.

Two paths:
  * a pinned ``list_selector`` scrapes that container directly;
  * otherwise the whole page is scanned and ``is_probable_article`` filters it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from app.services.crawler.common.constants import MAX_ITEMS_PER_PAGE, MIN_TITLE_LEN
from app.services.crawler.fetch.dates import find_date_in_text, normalize_date
from app.services.crawler.common.models import Candidate, ExtractRules
from app.services.crawler.fetch.titles import best_title, looks_non_english
from app.services.crawler.fetch.urls import (
    is_document_url,
    is_external_item,
    is_probable_article,
    light_reject,
    normalize_url,
)

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_SKIP_HREF_PREFIXES = ("javascript:", "mailto:", "tel:", "#")

# Class/id names marking a dedicated dateline element on a list row. Anchored to
# word-ish boundaries so "date" matches but "update"/"candidate" don't.
_DATE_CLASS_RE = re.compile(
    r"(^|[-_ ])(date|time|pubdate|pubtime|published|releasedate|"
    r"post-?date|news-?date|时间|日期|发布)([-_ ]|$)",
    re.I,
)

_CARD_CLASS_RE = re.compile(r"(item|card|news|list|post|entry|media)", re.I)

# A dateline element is trusted only when its own text is this short — that makes
# it a dateline rather than a paragraph that happens to mention a date.
_MAX_DATELINE_LEN = 40
# Cap on a text scope we will scan for a bare date.
_MAX_SNIPPET_LEN = 500


def parse_list_page(html: str, rules: ExtractRules) -> list[Candidate]:
    """Candidates from a list page's HTML. Pure — the driver stays in the caller."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    if rules.list_selector:
        return _from_container(soup, rules)
    return _from_whole_page(soup, rules)


def _from_container(soup: BeautifulSoup, rules: ExtractRules) -> list[Candidate]:
    """Scrape the pinned container(s), skipping the URL-shape KEEP heuristics.

    The container already excludes nav/footer/sidebar, but the light rejects still
    run: real containers hold cross-domain reposts, calendar files and undated
    section indexes as first-class cards. Multiple matching containers (paged
    blocks) are merged.
    """
    assert rules.list_selector is not None
    containers = soup.select(rules.list_selector)
    candidates: list[Candidate] = []
    seen: set[str] = set()
    rejected: dict[str, int] = {}

    for container in containers:
        for anchor in container.find_all("a"):
            url = _anchor_url(anchor, rules.news_url)
            if not url or url in seen:
                continue
            reason = light_reject(url, rules)
            if reason:
                seen.add(url)
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            title = best_title(anchor)
            if len(title) < MIN_TITLE_LEN:
                continue
            if rules.english_only and looks_non_english(title):
                seen.add(url)
                rejected["non-english"] = rejected.get("non-english", 0) + 1
                continue
            seen.add(url)
            candidates.append(_make_candidate(anchor, title, url, rules))

    external = sum(1 for c in candidates if c.external)
    logger.info(
        "    Container[%s] matched %d node(s), kept %d link(s)%s%s",
        rules.list_selector,
        len(containers),
        len(candidates),
        f"（其中外部/文档 {external} 条，不做摘要）" if external else "",
        f", rejected {rejected}" if rejected else "",
    )
    if not containers:
        # Selectors fail silently by nature — say it loudly instead.
        logger.error(
            "    ❌ list_selector %r matched NOTHING — page layout may have changed; "
            "fix config/list_selectors.json.",
            rules.list_selector,
        )
    return candidates


def _from_whole_page(soup: BeautifulSoup, rules: ExtractRules) -> list[Candidate]:
    """Scan every link on the page and keep the ones shaped like articles."""
    candidates: list[Candidate] = []
    seen: set[str] = set()
    skipped = 0

    for anchor in soup.find_all("a"):
        try:
            if not (anchor.get("href") or "").strip():
                continue
            title = best_title(anchor)
            if len(title) < MIN_TITLE_LEN:
                continue
            if rules.english_only and looks_non_english(title):
                skipped += 1
                continue
            url = _anchor_url(anchor, rules.news_url)
            if not url or url in seen:
                continue
            if not is_probable_article(url, rules):
                skipped += 1
                continue
            seen.add(url)
            candidates.append(_make_candidate(anchor, title, url, rules))
            if len(candidates) >= MAX_ITEMS_PER_PAGE:
                break
        except Exception:
            continue

    if skipped:
        logger.info("    Filtered out %d non-article links", skipped)
    return candidates


def _anchor_url(anchor: Tag, base_url: str) -> str | None:
    """Absolute, normalized href, or None when it isn't a scrapable http(s) link."""
    href = anchor.get("href")
    if not href or href.startswith(_SKIP_HREF_PREFIXES):
        return None
    url = normalize_url(urljoin(base_url, href))
    return url if url.startswith("http") else None


def _make_candidate(anchor: Tag, title: str, url: str, rules: ExtractRules) -> Candidate:
    # In the whole-page path is_probable_article has already dropped cross-domain
    # links and asset files, so only the document check can be true there; it is
    # evaluated anyway so the flag never goes missing if a rule is relaxed.
    external = is_document_url(url) or (
        rules.allow_external and is_external_item(url, rules.news_url)
    )
    return Candidate(
        title=title,
        url=url,
        list_date=list_date_for_anchor(anchor),
        external=external,
    )


def list_date_for_anchor(anchor: Tag) -> str | None:
    """The date printed next to a headline, read without opening the article.

    Scopes widen outward from the anchor — itself, parent, grandparent, then the
    enclosing <li>/<article>/card — because many sites put the date in a sibling
    node that only shares the card.
    """
    scopes = [anchor, anchor.parent, getattr(anchor.parent, "parent", None)]
    card = _enclosing_card(anchor)
    if card is not None and card not in scopes:
        scopes.append(card)
    scopes = [scope for scope in scopes if scope is not None]

    # 1) A <time> element is the most reliable signal.
    for scope in scopes:
        found = _date_from_time_tag(scope)
        if found:
            return found

    # 2) A dedicated dateline element beats scanning the whole card, which may
    #    also hold a DIFFERENT date inside the teaser.
    for scope in scopes:
        found = _date_from_dateline(scope)
        if found:
            return found

    # 3) Otherwise scan scope text, smallest snippet first so a tight one wins.
    for snippet in sorted(_scope_texts(scopes), key=len):
        if len(snippet) <= _MAX_SNIPPET_LEN:
            found = find_date_in_text(snippet)
            if found:
                return found
    return None


def _enclosing_card(anchor: Tag) -> Tag | None:
    try:
        return anchor.find_parent(["li", "article"]) or anchor.find_parent(
            attrs={"class": _CARD_CLASS_RE}
        )
    except Exception:
        return None


def _date_from_time_tag(scope: Tag) -> str | None:
    try:
        tag = scope.find("time")
    except Exception:
        return None
    if tag is None:
        return None
    raw = tag.get("datetime") or tag.get_text(" ", strip=True)
    return normalize_date(raw) or find_date_in_text(raw)


def _date_from_dateline(scope: Tag) -> str | None:
    try:
        elements = scope.find_all(attrs={"class": _DATE_CLASS_RE}) + scope.find_all(
            attrs={"id": _DATE_CLASS_RE}
        )
    except Exception:
        return None
    for element in elements:
        try:
            text = element.get_text(" ", strip=True)
        except Exception:
            continue
        if text and len(text) <= _MAX_DATELINE_LEN:
            found = normalize_date(text) or find_date_in_text(text)
            if found:
                return found
    return None


def _scope_texts(scopes: list[Tag]) -> list[str]:
    texts = []
    for scope in scopes:
        try:
            texts.append(scope.get_text(" ", strip=True))
        except Exception:
            continue
    return texts
