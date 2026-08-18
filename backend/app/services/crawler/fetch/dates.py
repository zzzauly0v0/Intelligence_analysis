"""Date parsing: turn the assorted formats our sites publish into one shape.

Everything here is pure. Timestamps stay NAIVE local time throughout the
crawler because that is what the sites print and what the per-site last-seen
store holds; mixing in an aware datetime would break every comparison.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

DATE_FMT = "%Y-%m-%d %H:%M:%S"

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_ORD = r"(?:st|nd|rd|th)?"

# Most specific first: a full ISO datetime beats a bare date, and the 4-digit
# year forms must be tried before their 2-digit counterparts.
_TEXT_DATE_PATTERNS = (
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?",
    # 2025年9月25日 / 2025-09-25 / 2025/09/25
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
    # July 17, 2026 / Jul. 16, 2026 / November 5th, 2025
    _MON + r"\s+\d{1,2}" + _ORD + r",?\s+\d{4}",
    # 21 July 2026 / 16 June, 2026 / 06. August 2026 (Döhler dots the day)
    r"\d{1,2}" + _ORD + r"\.?\s+" + _MON + r",?\s+\d{4}",
    r"\d{1,2}-" + _MON + r"-\d{4}",
    r"\d{1,2}/\d{1,2}/\d{4}",
    # 07/14/26 — \b plus the lookahead keep it out of a 4-digit-year date.
    r"\b\d{1,2}/\d{1,2}/\d{2}\b(?!/?\d)",
    # 26.07.17 (howtiangroup.cn). Strict 2-digit month/day so it can't eat a
    # version string like "1.10.5".
    r"\b\d{2}\.[01]\d\.[0-3]\d\b",
)

# Genuine publish-date fields only. modified_time / updated_time are deliberately
# absent: a lightly-edited old article would look new and get re-sent.
_META_DATE_NAMES = (
    r"article:published_time",
    r"og:published_time",
    r"publishdate", r"pubdate", r"publication_date", r"publish-date",
    r"dcterms\.date", r"dc\.date", r"sailthru\.date",
    r"parsely-pub-date", r"datePublished",
)


def normalize_date(raw: str | None) -> str | None:
    """Parse one date string into 'YYYY-mm-dd HH:MM:SS', or None if unparseable."""
    if not raw:
        return None
    s = raw.strip().replace("T", " ")

    # Drop a timezone designator, but only where a time precedes it — otherwise
    # the trailing year of "15-Aug-2022" reads as a "-20:22" offset.
    s = re.sub(r"(?<=\d:\d\d)\s*(Z|[+-]\d{2}:?\d{2})\s*$", "", s)
    s = re.sub(r"(?<=\d:\d\d:\d\d)\s*(Z|[+-]\d{2}:?\d{2})\s*$", "", s).strip()

    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", s, flags=re.I)

    # <Month> <D>, <Y>
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        built = _build(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
        if built:
            return built

    # <D>[.] <Month>[,] <Y> — also covers 15-Aug-2022.
    m = re.search(r"(\d{1,2})\.?[-\s]+([A-Za-z]{3,9})\.?,?[-\s]+(\d{4})", s)
    if m and m.group(2).lower() in _MONTHS:
        built = _build(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        if built:
            return built

    # YYYY-first, optional time.
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ ]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        return _build(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4) or 0), int(m.group(5) or 0), int(m.group(6) or 0),
        )

    # US M/D/YYYY, then M/D/YY (read as 20YY) so a full year isn't truncated.
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if m:
        return _build(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b(?!/?\d)", s)
    if m:
        return _build(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))

    # YY.MM.DD (howtiangroup.cn).
    m = re.search(r"\b(\d{2})\.([01]\d)\.([0-3]\d)\b", s)
    if m:
        return _build(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))

    return None


def _build(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> str | None:
    try:
        return datetime(year, month, day, hour, minute, second).strftime(DATE_FMT)
    except ValueError:
        return None


def find_date_in_text(text: str | None) -> str | None:
    """Find a date inside a SMALL snippet (one list row, one dateline element).

    Scoping the snippet is what makes bare-date matching safe — the same search
    over a whole page would happily grab a copyright year or a date in the body.
    """
    if not text:
        return None
    for pattern in _TEXT_DATE_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            found = normalize_date(m.group(0))
            if found:
                return found
    return None


def extract_publish_date(html: str | None) -> str | None:
    """Read an article's publish date out of raw HTML, or None."""
    if not html:
        return None
    try:
        return _first_date_in_html(html)
    except Exception as exc:
        logger.warning("    Error extracting publish date: %s", exc)
        return None


def _first_date_in_html(html: str) -> str | None:
    # Chinese-government markup (the NHC scraper this generalizes from).
    m = re.search(r'<meta\s+name="PubDate"\s+content="([^"]+)"', html, re.I)
    if m:
        return normalize_date(m.group(1))
    m = re.search(r'<meta\s+name="others"\s+content="页面生成时间\s+([^"]+)"', html)
    if m:
        return normalize_date(m.group(1))
    m = re.search(
        r"(?:发布时间|发布日期|发表时间|时间)[：:]\s*"
        r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
        html,
    )
    if m:
        return normalize_date(m.group(1))

    for name in _META_DATE_NAMES:
        for pattern in (
            r'<meta[^>]+(?:name|property|itemprop)=["\']' + name + r'["\'][^>]*content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property|itemprop)=["\']' + name + r'["\']',
        ):
            m = re.search(pattern, html, re.I)
            if m:
                found = normalize_date(m.group(1))
                if found:
                    return found

    for pattern in (
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            found = normalize_date(m.group(1))
            if found:
                return found

    # A bare ISO datetime in VISIBLE text. Attributes are stripped first: AEM
    # sites stamp every component with a data-layer blob holding a CMS edit time
    # of, say, a nav button. Matching one of those is worse than returning None,
    # because None lets the caller fall back to the date a human actually sees.
    visible = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    visible = re.sub(r"<[^>]+>", " ", visible)
    m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", visible)
    if m:
        return normalize_date(m.group(1))

    # Deliberately no bare-date search over the full page — for undated articles
    # the caller falls back to the list-row date instead.
    return None


def parse_dt(date_str: str | None) -> datetime | None:
    """Parse a normalized date string to datetime; a bare date means midnight."""
    if not date_str:
        return None
    s = date_str.strip()
    if len(s.split()) == 1:
        s += " 00:00:00"
    try:
        return datetime.strptime(s, DATE_FMT)
    except ValueError:
        return None
