#!/usr/bin/env python3
"""
Generic multi-site news monitor (timestamp mode).

This mirrors the original NHC scraper's logic, generalized to many sites:

For every enabled site in config/monitor_sites.json:
  1. Load its news page with an undetected Chrome driver.
  2. Extract candidate items (title + absolute URL + a date read from the
     LIST page next to each item — no article page load needed to decide).
  3. Compare each item's list date against the site's saved "last article
     time". Only items newer than the saved timestamp are NEW.
  4. Open ONLY the new items to fetch body text (for AI summarization) and,
     if available, a more precise publish date from the article page itself.

Deciding freshness from the list page (instead of opening every candidate)
means navigation/footer links never consume the open budget, and we never
miss a real article because junk links used up the quota.

There is NO keyword matching and NO seen-URL diffing. Freshness is decided
purely by comparing publish dates against a per-site timestamp, like the NHC
workflow. Each site keeps its own last-article time in
config/monitor_last_times.json; a site not present there defaults to
DEFAULT_LAST_TIME (kept identical to the NHC main-branch default).

Items whose date cannot be read from the list page are skipped with a warning
(so we never re-send undated items on every run). When a run finds more new
items than MAX_NEW_ITEMS_TO_OPEN, it processes the OLDEST ones first and only
advances the saved timestamp past what it actually processed, so the rest are
picked up on the next run rather than being lost.

A hit may be marked "external": some newsrooms publish third-party coverage of
themselves as their news (Zydus's "In the news" wall of media articles, videos
and scanned PDF clippings). Those sites set "allow_external": true in
config/list_selectors.json, which stops the cross-domain filter from dropping
them; the items are then carried WITHOUT a body fetch and without an AI summary,
and run_monitor renders them as title + date + link for the reader to click.

Returns a list of hit dicts via fetch_all_new_hits(). Designed to be imported
by run_monitor.py; can also be run standalone for a dry check.
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "monitor_sites.json")
LIST_SELECTORS_PATH = os.path.join(BASE_DIR, "config", "list_selectors.json")
LAST_TIMES_PATH = os.path.join(BASE_DIR, "config", "monitor_last_times.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Module logger. setup_logging() attaches handlers (file + console) on first run;
# until then log() calls still work (they just go nowhere extra).
logger = logging.getLogger("monitor")


def setup_logging():
    """
    Configure the module logger to write to both a timestamped file in logs/
    and the console. Returns (log_path, summary_path). Safe to call once per run.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"monitor_{stamp}.log")
    summary_path = os.path.join(LOG_DIR, f"monitor_summary_{stamp}.log")

    logger.setLevel(logging.INFO)
    logger.handlers.clear()  # avoid duplicate handlers if called twice
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    file_h = logging.FileHandler(log_path, encoding="utf-8")
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    console_h = logging.StreamHandler(sys.stdout)
    console_h.setFormatter(fmt)
    logger.addHandler(console_h)

    logger.propagate = False
    
    return log_path, summary_path

# Default baseline for a site we've never recorded a timestamp for.
# Kept identical to the NHC main-branch default so behavior matches.
DEFAULT_LAST_TIME = "2025-08-01 00:00:00"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# TEST-ONLY override. Set MONITOR_BASELINE (e.g. "2026-07-20 00:00:00") to make
# EVERY site start from that time this run, ignoring the saved per-site
# timestamps. Pulling the baseline to a recent date means few articles count as
# "new", so a test run finishes fast. Leave it UNSET in production — then the
# normal per-site timestamp logic (only fetch what's newer than last seen) runs.
BASELINE_OVERRIDE = os.getenv("MONITOR_BASELINE", "").strip() or None

# TEST-ONLY override. Set MONITOR_ONLY to a comma-separated list of site names
# (matched case-insensitively, substring OK) to scan ONLY those sites this run,
# skipping every other enabled site to save time. E.g.
#   MONITOR_ONLY="Cargill,Adorvia,Manus,IFF"
# Leave it UNSET in production — then all enabled sites are scanned as normal.
# Pair with MONITOR_BASELINE so the test run neither saves timestamps nor needs
# the full fleet. Substring match keeps long official names usable
# ("Adorvia" matches "Adorvia Biotechnology", "Manus" matches "Manus Bio").
_MONITOR_ONLY_RAW = os.getenv("MONITOR_ONLY", "").strip()
MONITOR_ONLY = [s.strip().lower() for s in _MONITOR_ONLY_RAW.split(",")
                if s.strip()] or None

# Anchor text shorter than this is treated as nav chrome, not a headline.
MIN_TITLE_LEN = 8
# Cap candidate items considered per page so a giant page can't explode.
MAX_ITEMS_PER_PAGE = 200
# Cap how many NEW items we open (fetch article body) per site per run. Because
# we now decide freshness from the list page, only genuinely-new items count
# against this budget. If a run has more new items than this, the OLDEST are
# processed first and the timestamp only advances past what was processed, so
# the remainder are picked up next run (never silently lost).
MAX_NEW_ITEMS_TO_OPEN = 20
# Page-load retry policy: a single network hiccup shouldn't drop an entire site
# for the run. Each retry waits PAGE_LOAD_BACKOFF * attempt seconds.
PAGE_LOAD_RETRIES = 3
PAGE_LOAD_BACKOFF = 8  # seconds; multiplied by attempt number (8s, 16s, ...)
# On a news list page, wait until at least this many <a> elements exist before
# scraping — handles JS-rendered lists not yet in the DOM at readyState.
MIN_ANCHORS_ON_NEWS_PAGE = 5
# For sites whose LIST page shows no date, we open candidate articles to read
# the date from the detail page. Cap how many such undated candidates we probe
# per site per run so a 200-link page can't trigger 200 page loads. Candidates
# are opened in list order (usually newest-first), so the cap keeps the most
# recent ones.
MAX_UNDATED_TO_PROBE = 25

# Generic call-to-action link text that is not a real article headline. When an
# anchor's own text is only one of these, _best_title keeps looking (aria-label,
# a nearby heading, an <img alt>) for the true title. Lowercased for matching.
_PLACEHOLDER_TITLES = {
    "read more", "read more.", "learn more", "find out more", "see more",
    "view more", "more", "details", "view details", "continue reading",
    "阅读更多", "查看详情", "查看更多", "了解更多", "详情", "更多", "点击查看",
    # Site navigation / section labels that are NOT article headlines. Seen
    # mixed into hits from Givaudan/dsm/IFF news pages (nav menus, footers).
    "explore corporate news", "view all news", "all news", "company news",
    "corporate news", "latest news", "news", "newsroom", "subscribe to news",
    "subscribe", "other investor news", "investor news", "corporate presentations",
    "publications", "visit media", "trade media", "social media", "company",
    "ad hoc announcements", "add to calendar", "media", "media releases",
    "media center", "media centre", "press releases", "press release",
    "all news & stories", "news & events", "news and events", "news & media",
    "news and media", "our news", "back to news", "see all", "view all",
}


def _is_placeholder_title(s):
    """True if a string is generic nav / call-to-action text rather than a real
    headline. Module-level so both _best_title (to reject a title) and the
    detail-page title rescue (to validate a replacement) share ONE definition.
    Normalizes first: drops a trailing parenthetical ("(opens in a new window)")
    and any " - …" suffix so ADM's "Read More - Read more about News" is caught."""
    core = re.sub(r"\s*\([^)]*\)\s*", " ", (s or "").lower())
    core = re.split(r"\s+-\s+", core, 1)[0].strip()
    if core in _PLACEHOLDER_TITLES:
        return True
    return core.startswith(("read more", "learn more", "阅读更多", "查看详情"))


# List-card labels that ARE >= MIN_TITLE_LEN (so they pass the list-title filter
# and become a hit's title) but are NOT real headlines — the card only exposed an
# accessibility label. 百事's story/press cards surface aria-label "Go to article
# details" as their only text. When a hit's list title is one of these we rescue
# the real headline from the DETAIL page. Kept SEPARATE from _PLACEHOLDER_TITLES:
# those cause _best_title to DROP a link, which is wrong here — the URL is a
# genuine article (is_probable_article kept it); we just need a better title, so
# dropping would lose a real article (故障B).
_RESCUE_LIST_TITLES = frozenset({
    "go to article details", "go to details", "go to article",
    "view article", "read article", "article details", "view story",
    "go to story", "read the article", "read full article",
    # Kerry's Algolia cards: the only anchor text is this CTA label.
    "read this news", "read news",
})

# List cards that prepend a "kicker/eyebrow" label AND a dateline to the headline
# (and sometimes glue on the teaser), so the anchor text becomes
# "Press Release July 23, 2026 <headline> <teaser…>" (Manus Bio) or
# "NEWS HIGHLIGHTS Jul. 16, 2026 <headline> Read More >" (Adorvia). The headline
# runs into the surrounding text with no clean delimiter, so we can't carve it
# out in _best_title; instead we flag such titles for detail-page rescue (og:title
# / h1 give the clean headline). The pattern requires a KNOWN kicker followed by a
# date, which a real headline never starts with — so the rescue stays conservative.
_KICKER_DATE_RE = re.compile(
    r"^\s*(?:news highlights|press releases?|in the media|in the press|"
    r"in the news)\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+"
    r"\d{1,2},?\s+\d{4}\b", re.I)


def _needs_title_rescue(title):
    """Whether a hit's (list-derived) title should be replaced by a detail-page
    headline if we can get one. True for junk/nav/placeholder labels, the
    _RESCUE_LIST_TITLES set, and kicker+date-prefixed card blobs (Adorvia/Manus).
    Conservative: a real headline never matches, so the rescue only ever fires on
    genuinely bad titles."""
    t = re.sub(r"\s+", " ", (title or "").strip().lower())
    if len(t) < MIN_TITLE_LEN:
        return True
    if t in _RESCUE_LIST_TITLES or _is_placeholder_title(t):
        return True
    if _KICKER_DATE_RE.match(title or ""):
        return True
    return t.startswith(("go to article", "go to story"))


# --------------------------------------------------------------------------- #
# Config / state helpers
# --------------------------------------------------------------------------- #
def load_list_selectors():
    """Return {site_name: {list_selector, wait_for, allow_external}} for sites whose news-list
    container is pinned in config/list_selectors.json. Those sites scrape the
    container directly (light rejects still apply — see
    _extract_items_by_selector). Missing file / unpinned sites -> not present.

    Only entries whose status is "ready"/"fixed" are activated. "manual" means
    the annotator judged the page needs per-site work before the selector is
    trustworthy (e.g. ADM's thin slider, 科宏's <main> mixing repost sections);
    those sites keep the generic whole-page path until promoted."""
    if not os.path.exists(LIST_SELECTORS_PATH):
        return {}
    try:
        with open(LIST_SELECTORS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"⚠️  Could not read list_selectors.json, ignoring: {e}")
        return {}
    out = {}
    for s in cfg.get("sites", []):
        if not s.get("list_selector"):
            continue
        status = (s.get("status") or "ready").lower()
        if status not in ("ready", "fixed"):
            logger.info("list_selector for %s is status=%r — not activated",
                        s.get("name"), status)
            continue
        out[s["name"]] = {"list_selector": s["list_selector"],
                          "wait_for": s.get("wait_for"),
                          # Keep cross-domain press coverage as content (Zydus).
                          "allow_external": bool(s.get("allow_external")),
                          # Per-site path blocklist: drop list items whose URL
                          # path contains any of these substrings (dsm buries
                          # /share-buy-back/ filings in its press-releases feed).
                          "exclude_paths": [p.lower() for p in
                                            (s.get("exclude_paths") or [])]}
    return out


def load_sites():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sites = [s for s in cfg.get("sites", []) if s.get("enabled", True)]
    # Attach the pinned news-list-container selector (if any) so extract_items
    # can scrape that container directly instead of scanning the whole page.
    selectors = load_list_selectors()
    for s in sites:
        if s["name"] in selectors:
            s["list_selector"] = selectors[s["name"]]["list_selector"]
            if selectors[s["name"]].get("wait_for"):
                s["wait_for"] = selectors[s["name"]]["wait_for"]
            if selectors[s["name"]].get("allow_external"):
                s["allow_external"] = True
            if selectors[s["name"]].get("exclude_paths"):
                s["exclude_paths"] = selectors[s["name"]]["exclude_paths"]
    # TEST-ONLY: restrict to the MONITOR_ONLY subset (substring, case-insensitive)
    # so a test run touches only the named sites and skips the rest.
    if MONITOR_ONLY:
        sites = [s for s in sites
                 if any(sub in s["name"].lower() for sub in MONITOR_ONLY)]
    return sites


def load_last_times():
    """Return {site_name: 'YYYY-mm-dd HH:MM:SS'} of last-seen article times."""
    if os.path.exists(LAST_TIMES_PATH):
        try:
            with open(LAST_TIMES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  Could not read last-times store, starting fresh: {e}")
    return {}


def save_last_times(last_times):
    os.makedirs(os.path.dirname(LAST_TIMES_PATH), exist_ok=True)
    with open(LAST_TIMES_PATH, "w", encoding="utf-8") as f:
        json.dump(last_times, f, ensure_ascii=False, indent=2)


def get_last_time(last_times, site_name):
    """Last recorded time for a site, or the default baseline if unseen."""
    return last_times.get(site_name, DEFAULT_LAST_TIME)


# --------------------------------------------------------------------------- #
# Date extraction (generalized from the NHC scraper)
# --------------------------------------------------------------------------- #
def extract_publish_date(html):
    """
    Best-effort extraction of an article's publish date from raw HTML.

    Returns a normalized 'YYYY-mm-dd HH:MM:SS' string, or None if nothing
    plausible is found. Combines the NHC-specific patterns with a set of
    generic ones that cover the assorted (CN/EN/JP) sites we monitor.
    """
    if not html:
        return None
    try:
        # --- NHC-specific / Chinese-government patterns (kept from original) ---
        m = re.search(r'<meta\s+name="PubDate"\s+content="([^"]+)"', html, re.I)
        if m:
            return _normalize_date(m.group(1))

        m = re.search(r'<meta\s+name="others"\s+content="页面生成时间\s+([^"]+)"', html)
        if m:
            return _normalize_date(m.group(1))

        m = re.search(r'(?:发布时间|发布日期|发表时间|时间)[：:]\s*'
                      r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)', html)
        if m:
            return _normalize_date(m.group(1))

        # --- Generic <meta> publish-date tags used by many CMSs ---
        # NOTE: we deliberately do NOT include modified_time / updated_time —
        # otherwise a lightly-edited old article would look "new" and get
        # re-sent. We only trust genuine publish-date fields here.
        meta_names = (
            r'article:published_time',
            r'og:published_time',
            r'publishdate', r'pubdate', r'publication_date', r'publish-date',
            r'dcterms\.date', r'dc\.date', r'sailthru\.date',
            r'parsely-pub-date', r'datePublished',
        )
        for nm in meta_names:
            # matches name="..." content="..." OR property="..." content="..."
            m = re.search(
                r'<meta[^>]+(?:name|property|itemprop)=["\']' + nm + r'["\'][^>]*content=["\']([^"\']+)["\']',
                html, re.I)
            if m:
                d = _normalize_date(m.group(1))
                if d:
                    return d
            # content-first ordering
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property|itemprop)=["\']' + nm + r'["\']',
                html, re.I)
            if m:
                d = _normalize_date(m.group(1))
                if d:
                    return d

        # --- <time datetime="..."> elements ---
        m = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.I)
        if m:
            d = _normalize_date(m.group(1))
            if d:
                return d

        # --- JSON-LD "datePublished": "..." ---
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html, re.I)
        if m:
            d = _normalize_date(m.group(1))
            if d:
                return d

        # --- Bare ISO-ish datetime in VISIBLE TEXT (2025-09-25T15:06:11) ---
        # (Still fairly specific because it requires a time component.)
        # Visible text ONLY: scripts/styles and — crucially — tag ATTRIBUTES are
        # stripped first. AEM sites (Kerry) stamp every component with
        # data-cmp-data-layer='{"repo:modifyDate":"2025-10-23T11:32:22Z"}' — a
        # CMS-internal edit time of a nav button, nothing to do with the
        # article. Matching one of those is worse than returning None: the
        # caller then falls back to the on-page dateline / list-row date, which
        # is the date a human actually sees ("29 July, 2026").
        visible_text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html,
                              flags=re.S | re.I)
        visible_text = re.sub(r"<[^>]+>", " ", visible_text)
        m = re.search(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)', visible_text)
        if m:
            return _normalize_date(m.group(1))

        # NOTE: we intentionally stop here. Matching a bare date "anywhere" on a
        # full article page is too aggressive — it can grab a date mentioned in
        # the body, a sidebar, or a copyright year. For undated article pages we
        # instead fall back to the date read from the list row (see caller).
        return None
    except Exception as e:
        print(f"    ⚠️  Error extracting publish date: {e}", file=sys.stderr)
        return None


def find_date_in_text(text):
    """
    Look for a plausible date inside a SMALL text snippet (e.g. one news-list
    row or an article's date container). Scoped snippets make bare-date matching
    safe, unlike a full page. Tries the most specific patterns first and returns
    a normalized date string or None.
    """
    if not text:
        return None

    _MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
    _ORD = r"(?:st|nd|rd|th)?"  # optional ordinal suffix on the day

    patterns = [
        # ISO datetime with a time component (most precise).
        r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?',
        # Chinese / ISO bare date: 2025年9月25日 / 2025-09-25 / 2025/09/25
        r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?',
        # Month D[th], YYYY  e.g. July 17, 2026 / Jul. 16, 2026 / November 5th, 2025
        _MON + r'\s+\d{1,2}' + _ORD + r',?\s+\d{4}',
        # D[th][.] Month YYYY  e.g. 21 July 2026 / 2 JULY 2025 / 16 June, 2026 /
        # 06. August 2026 (Döhler article datelines put a dot after the day)
        r'\d{1,2}' + _ORD + r'\.?\s+' + _MON + r',?\s+\d{4}',
        # D-Mon-YYYY         e.g. 15-Aug-2022
        r'\d{1,2}-' + _MON + r'-\d{4}',
        # US M/D/YYYY        e.g. 07/20/2026
        r'\d{1,2}/\d{1,2}/\d{4}',
        # US M/D/YY (two-digit year)  e.g. 07/14/26 (ingredion.com news rows).
        # MUST come after the 4-digit pattern above so "07/14/2026" is matched
        # in full first. \b on both ends + a negative lookahead for another
        # "/digit" keep it from eating part of a 4-digit-year date.
        r'\b\d{1,2}/\d{1,2}/\d{2}\b(?!/?\d)',
        # Two-digit-year dotted date: YY.MM.DD  e.g. 26.07.17 (howtiangroup.cn
        # news rows). Anchored to \b…\b with strict 2-digit month/day so it can't
        # eat version strings like "1.10.5". Interpreted as 20YY in _normalize_date.
        r'\b\d{2}\.[01]\d\.[0-3]\d\b',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            d = _normalize_date(m.group(0))
            if d:
                return d
    return None


# Map English month names/abbreviations to numbers (covers the abbrev-with-dot
# and abbrev-with-dash forms our monitored sites use, e.g. "Jul." / "Aug").
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _normalize_date(raw):
    """
    Turn an assortment of date strings into 'YYYY-mm-dd HH:MM:SS'.
    Returns None if it can't be parsed into a real date.

    Handles (real formats seen across the monitored sites):
      2026-06-10 / 2026/06/10 / 2026年6月10日        (ISO / Chinese)
      2026-09-25T15:06:11+08:00                       (ISO w/ time & tz)
      21 July 2026 / 16 June, 2026                    (day month year)
      July 17, 2026 / Jul. 16, 2026                   (month day, year)
      November 5th, 2025                              (ordinal suffix)
      07/20/2026                                      (US m/d/Y)
      15-Aug-2022                                     (d-Mon-Y)
    """
    if not raw:
        return None
    s = raw.strip()

    # Strip timezone designators that datetime can't easily eat, but ONLY when
    # they follow a time (HH:MM). Otherwise a trailing "-2022" year in a string
    # like "15-Aug-2022" would be mistaken for a "-20:22"-style offset.
    s = s.replace("T", " ")
    s = re.sub(r"(?<=\d:\d\d)\s*(Z|[+-]\d{2}:?\d{2})\s*$", "", s)          # after HH:MM
    s = re.sub(r"(?<=\d:\d\d:\d\d)\s*(Z|[+-]\d{2}:?\d{2})\s*$", "", s)     # after HH:MM:SS
    s = s.strip()

    # Chinese formatting -> ISO separators.
    s = s.replace("年", "-").replace("月", "-").replace("日", "")

    # Drop ordinal suffixes on the day: "5th" -> "5", "1st" -> "1", etc.
    s = re.sub(r"(\d{1,2})(st|nd|rd|th)\b", r"\1", s, flags=re.I)

    # 1) Month-name formats via a regex + month lookup (robust to '.'/','):
    #    "<Month> <D>, <Y>"  e.g. July 17, 2026 / Jul. 16, 2026
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        mo = _MONTHS[m.group(1).lower()]
        d, y = int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime(DATE_FMT)
        except ValueError:
            pass
    #    "<D>[.] <Month>[,] <Y>"  e.g. 21 July 2026 / 16 June, 2026 /
    #    15-Aug-2022 / 06. August 2026 (Döhler puts a dot after the day)
    m = re.search(r"(\d{1,2})\.?[-\s]+([A-Za-z]{3,9})\.?,?[-\s]+(\d{4})", s)
    if m and m.group(2).lower() in _MONTHS:
        d = int(m.group(1))
        mo = _MONTHS[m.group(2).lower()]
        y = int(m.group(3))
        try:
            return datetime(y, mo, d).strftime(DATE_FMT)
        except ValueError:
            pass

    # 2) Numeric YYYY-first (+ optional time) with flexible separators.
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
                  r"(?:[ ]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4)) if m.group(4) else 0
        mm = int(m.group(5)) if m.group(5) else 0
        ss = int(m.group(6)) if m.group(6) else 0
        try:
            return datetime(y, mo, d, hh, mm, ss).strftime(DATE_FMT)
        except ValueError:
            return None

    # 3) US-style M/D/YYYY (month first) e.g. 07/20/2026.
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime(DATE_FMT)
        except ValueError:
            return None

    # 3b) US-style M/D/YY with a two-digit year e.g. 07/14/26 (ingredion.com).
    #     Checked AFTER the 4-digit form so a full year isn't truncated. The
    #     two-digit year is read as 20YY; month-first to match rule 3. \b + a
    #     negative lookahead for "/digit" stop it matching inside 07/14/2026.
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b(?!/?\d)", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
        try:
            return datetime(y, mo, d).strftime(DATE_FMT)
        except ValueError:
            return None

    # 4) Two-digit-year dotted date YY.MM.DD e.g. 26.07.17 (howtiangroup.cn).
    #    Requires strict 2-digit month (0-1x) and day (0-3x) so it won't match
    #    version-like "1.10.5". Two-digit year is read as 20YY.
    m = re.search(r"\b(\d{2})\.([01]\d)\.([0-3]\d)\b", s)
    if m:
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime(DATE_FMT)
        except ValueError:
            return None

    return None


def parse_dt(date_str):
    """Parse a normalized 'YYYY-mm-dd HH:MM:SS' string to datetime, or None."""
    if not date_str:
        return None
    s = date_str.strip()
    if len(s.split()) == 1:  # date only
        s += " 00:00:00"
    try:
        return datetime.strptime(s, DATE_FMT)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def build_driver():
    import undetected_chromedriver as uc
    import subprocess

    # Pin to the Google Chrome binary (not the snap chromium, which may sit on a
    # different version and break the matching chromedriver). Detect its major
    # version from the same binary we hand to uc so they always agree.
    chrome_binary = None
    for candidate in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
        if os.path.exists(candidate):
            chrome_binary = candidate
            break

    version_main = None
    try:
        probe = chrome_binary or "google-chrome"
        result = subprocess.run([probe, "--version"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_main = int(result.stdout.strip().split()[2].split(".")[0])
            print(f"Detected Chrome major version: {version_main} (binary: {chrome_binary})")
    except Exception as e:
        print(f"Could not detect Chrome version: {e}")

    # Three display strategies, in priority order:
    #   MONITOR_USE_WSLG=1: headed Chrome on the EXISTING X display (WSLg's :0).
    #     WSL2 ships an X server (WSLg) at :0, so we get a real headed browser —
    #     the same strong anti-bot posture as the production Xvfb box — WITHOUT
    #     starting Xvfb (which hangs uc on WSL2). Use this to test on WSL2 with
    #     production-equivalent fetching (headless gets Access-Denied/Cloudflare-
    #     challenged on sites like Cargill/IFF that headed sails through).
    #   MONITOR_HEADLESS=1: --headless=new, no X server. Fastest on WSL2, but a
    #     weaker anti-bot posture — some sites block or challenge headless.
    #   unset (default, production Linux box): headed Chrome inside a fresh Xvfb
    #     virtual display — the setup every site was validated against.
    use_wslg = os.getenv("MONITOR_USE_WSLG", "").strip() in ("1", "true", "yes")
    headless = os.getenv("MONITOR_HEADLESS", "").strip() in ("1", "true", "yes")
    display = None
    if use_wslg:
        # Headed on the display WSLg already provides; do NOT start Xvfb.
        headless = False
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = ":0"
        print(f"Using existing X display (WSLg) at DISPLAY={os.environ['DISPLAY']} "
              "— headed, no Xvfb")
    elif not headless and sys.platform.startswith("linux"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=0, size=(1920, 1080))
            display.start()
            print("Started virtual display (Xvfb)")
            time.sleep(2)
        except Exception as e:
            print(f"Could not start virtual display: {e}")

    driver = None
    for attempt in range(3):
        try:
            options = uc.ChromeOptions()
            options.add_argument("--window-size=1920x1080")
            options.add_argument("--accept-language=en-US,en;q=0.9,zh-CN;q=0.8")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-background-networking")
            if chrome_binary:
                options.binary_location = chrome_binary
            if headless:
                options.add_argument("--headless=new")
            driver = uc.Chrome(options=options, headless=headless,
                               version_main=version_main)
            driver.set_page_load_timeout(120)
            print("undetected_chromedriver ready")
            break
        except Exception as e:
            print(f"Driver attempt {attempt + 1}/3 failed: {e}", file=sys.stderr)
            if attempt == 2:
                raise
            time.sleep(3)

    return driver, display


def load_page(driver, url, min_anchors=0, wait_for=None):
    """
    Load a URL robustly and return True on success.

    Retries the navigation with linear backoff (a single network hiccup
    shouldn't drop the whole site), waits for readyState=complete, and — when
    min_anchors > 0 — keeps waiting until that many <a> elements exist so
    JS-rendered news lists are in the DOM before we scrape. Falls back to a
    short sleep if the anchor threshold never reaches (page may just be sparse).
    Also scrolls to the bottom to trigger lazy-loaded content.

    `wait_for` (per-site CSS selector) is for lists that render *after*
    readyState=complete via an async data fetch — e.g. Kerry's Algolia
    InstantSearch, whose ol.ais-Hits-list is empty at complete and for the first
    few seconds, so the generic min_anchors gate (satisfied by header/footer
    links) fires too early and we scrape an empty container. When given, we
    additionally wait until at least one element matching wait_for exists.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    last_err = None
    for attempt in range(1, PAGE_LOAD_RETRIES + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            if min_anchors > 0:
                try:
                    WebDriverWait(driver, 15).until(
                        lambda d: len(d.find_elements(By.TAG_NAME, "a")) >= min_anchors
                    )
                except Exception:
                    # Threshold not reached — proceed anyway (page may be sparse).
                    time.sleep(2)
            else:
                time.sleep(2)
            # Trigger lazy-loaded content BEFORE the wait_for gate: some lists
            # (e.g. IFF's div.m-press__list) render the container early but only
            # populate the article cards after a scroll, so waiting first and
            # scrolling second would time out on an empty container.
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)
            except Exception:
                pass
            # Async-rendered list: wait until the real content selector appears.
            # Keep scrolling on each poll so lazy loaders that key off scroll
            # events (not just position) get repeatedly nudged.
            if wait_for:
                def _seen(d):
                    try:
                        d.execute_script(
                            "window.scrollTo(0, document.body.scrollHeight);")
                    except Exception:
                        pass
                    return len(d.find_elements(By.CSS_SELECTOR, wait_for)) > 0
                try:
                    WebDriverWait(driver, 25, poll_frequency=1.5).until(_seen)
                except Exception:
                    # Never showed up — proceed; extract_items will report 0 items
                    # and the summary flags it, same as before.
                    logger.info("    wait_for %r not seen within 25s", wait_for)
            return True
        except Exception as e:
            last_err = e
            if attempt < PAGE_LOAD_RETRIES:
                wait = PAGE_LOAD_BACKOFF * attempt
                print(f"    ⚠️  Load attempt {attempt}/{PAGE_LOAD_RETRIES} failed: {e}; "
                      f"retrying in {wait}s")
                time.sleep(wait)
    print(f"    ❌ Failed to load after {PAGE_LOAD_RETRIES} attempts: {last_err}")
    return False


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def normalize_url(url):
    """Strip fragments/trailing slashes so the same item is consistent."""
    if not url:
        return ""
    url = url.split("#")[0].strip()
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]
    return url


# Matches a /YYYY/ path segment (year 2000-2099), i.e. date-style article URLs
# like /2026/cargill-... or /2023/04/some-release.
_YEAR_SEG_RE = re.compile(r"/20\d{2}(/|$)")

# Class/id names that mark a dedicated dateline element on a news-list row
# (e.g. <div class="date">, <span class="pubdate">, <p class="time">). Used by
# _list_date_for_anchor to prefer a real dateline over a date buried in the
# teaser text. Anchored to word-ish boundaries so "date" matches but "update"/
# "candidate" don't. Chinese "时间"/"日期" included for CN sites.
_DATE_CLASS_RE = re.compile(
    r"(^|[-_ ])(date|time|pubdate|pubtime|published|releasedate|"
    r"post-?date|news-?date|时间|日期|发布)([-_ ]|$)", re.I)

# Article-detail markers in a URL path. "detail"/"article" are unambiguous
# article signals (substring match is safe); "show"/"content"/"view"/"post"
# are only trusted as a whole path segment (bounded by / or start/end) so a
# word like "review" or "download" can't trip them. Used as rule 6 in
# is_probable_article to catch CMS routes like /new_cn_detail/id/14.html.
_ARTICLE_PATH_RE = re.compile(
    r"(detail|article)|(^|/)(show|content|view|post|artikel)(/|$)", re.I)

# Class names marking a card's title/headline element when the site uses a
# <div>/<span> instead of an <h1>-<h4> (e.g. ADM's <div
# class="adm-div-c10-card-title">). Used by _best_title as a fallback after
# real headings. Anchored to a word-ish boundary so "title" matches but
# "subtitle-note" style false hits are avoided; "heading"/"headline" included.
_TITLE_CLASS_RE = re.compile(
    r"(^|[-_ ])(title|headline|heading|card-?title|news-?title|标题)([-_ ]|$)", re.I)


# Section/landing-page slugs that look article-ish (they carry a hyphen) but
# are really sibling nav pages of the news list — e.g. Givaudan's
# /media/company-news, /media/trade-media. Dropped by is_probable_article so
# they don't become false "articles". Kept lowercase; matched after stripping
# any file extension.
_SECTION_SLUGS = frozenset({
    "news", "latest-news", "company-news", "corporate-news", "news-events",
    "newsroom", "news-media", "news-and-media", "media-center", "media-centre",
    "media-releases", "media", "trade-media", "social-media", "press-releases",
    "press-release", "investor-news", "other-investor-news", "all-news",
    "view-all-news", "ad-hoc-announcements", "corporate-presentations",
    "publications", "add-to-calendar", "subscribe-to-news",
    # Section-landing pages seen directly under a site's /news/ dir that are NOT
    # articles (Cargill's newsroom sub-sections). Real Cargill articles live at
    # /YYYY/<slug> (caught by the date-segment rule), never under /news/.
    "cargill-stories", "our-stories", "in-the-news", "sign-up-for-news",
    "media-contacts", "news-media-assets", "media-resources", "media-resource",
})


# Path SEGMENTS that mark a non-news area of a corporate site (products, legal,
# careers, portfolio, generic company nav). If ANY segment of a link's path is
# one of these — or begins "about-" (about-us / about-doehler / …) — the link is
# site chrome, never an article, and is rejected up front. This is what stops a
# news page's own header/footer nav (Döhler's /en/markets/…, /en/our-portfolio/…,
# Coca-Cola's /sg/en/offerings/…, /sg/en/legal/…) from flooding in as fake
# "articles". Kept generic (cross-site) on purpose; add site-specific product
# slugs only if a real site needs them.
_NON_NEWS_SEGMENTS = frozenset({
    "about", "about-us", "company", "who-we-are", "careers", "career", "jobs",
    "contact", "contact-us", "contactus",
    "products", "product", "product-category", "product-categories",
    "offerings", "offering", "portfolio", "our-portfolio", "solutions",
    "markets", "market", "applications-solutions", "applications", "services",
    "legal", "terms", "terms-conditions", "terms-of-service",
    "terms-and-conditions", "terms-of-use", "privacy", "privacy-policy",
    "privacy-statement", "cookie-policy", "cookies", "imprint", "disclaimer",
    "sitemap", "search", "login", "register", "subscribe",
    "lp", "factsheets-whitepapers", "factsheets", "whitepapers",
})

# Two-letter ISO language codes used as the FIRST path segment for a localized
# mirror of the same page (Döhler /es/…, /pt/…, /cn/…, /tr/…; Newnature /fr/…,
# /el/…). A link whose first segment is one of these but DIFFERS from the news
# page's first segment is a translation of site content, not a distinct new
# article. "en" is intentionally excluded (it's the common default we scrape).
_LANG_CODES = frozenset({
    "es", "pt", "fr", "de", "it", "nl", "pl", "ru", "tr", "cn", "zh", "ja",
    "jp", "ko", "kr", "el", "ar", "cs", "da", "fi", "no", "sv", "uk", "ro",
    "hu", "bg", "hr", "sk", "sl", "et", "lv", "lt",
})

# Multi-label public suffixes we monitor (co.jp, com.cn, co.uk, …). Needed so the
# registrable-domain comparison keeps the RIGHT number of labels: morita-kagaku-
# kogyo.co.jp is one registrable domain, not "co.jp". Only the suffixes that
# actually appear (or plausibly could) among our sites — this is a reject-side
# heuristic, not a full PSL, and erring toward "same domain" only ever KEEPS a
# link (worst case: one stray link opened once, then dropped for lack of a date).
_MULTI_LABEL_TLDS = frozenset({
    "co.jp", "com.cn", "co.uk", "com.au", "co.nz", "co.in", "com.br", "com.mx",
    "com.sg", "com.hk", "com.tw", "co.kr", "co.za", "com.tr", "org.uk",
})


def _registrable_domain(netloc):
    """
    Reduce a netloc to its registrable domain (eTLD+1) for same-company checks:
      www.cargill.com            -> cargill.com
      our-company.dsm-firmenich.com -> dsm-firmenich.com
      d-plus.doehler.com         -> doehler.com
      www.morita-kagaku-kogyo.co.jp -> morita-kagaku-kogyo.co.jp
    Port and case are stripped. This lets is_probable_article reject genuinely
    third-party links (forbes.com, usatoday.com reposted on cargill.com/news)
    while KEEPING a company's own subdomains (Döhler's d-plus, dsm-firmenich's
    our-company).
    """
    host = (netloc or "").lower().split(":", 1)[0].strip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_LABEL_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def _news_section(news_path):
    """
    The section ROOT of a news list page, used as the prefix a real article must
    live under. Rules, in order:
      * a trailing pure-number file ("/news/0.html", "/news/1.php") is a paginated
        list, not a section name -> use the parent dir ("/news");
      * a trailing descriptive file ("/en/news-media.html") -> strip the
        extension so the section is that name ("/en/news-media");
      * otherwise ("/news", "/sg/en/media-center") -> use as-is.
    Always returned without a trailing slash.
    """
    p = news_path.rstrip("/")
    if not p:
        return ""
    last = p.rsplit("/", 1)[-1]
    if re.match(r"^\d+\.\w+$", last):            # e.g. 0.html, 12.php -> paginated
        return p.rsplit("/", 1)[0] or "/"
    if re.search(r"\.(html?|aspx|php|jsp|shtml)$", last):
        return re.sub(r"\.(html?|aspx|php|jsp|shtml)$", "", p)
    return p


def is_probable_article(item_url, news_url, allow_paths, require_year=False):
    """
    Decide whether a link found on a news page is a real article vs. site chrome
    (nav bars, footers, section landing pages, "related" cards pointing at other
    sections). Ported from the parallel branch; used to keep junk links out of
    the candidate list so we don't open section pages / non-news links.

    First a set of REJECTS drops obvious non-articles (image/asset files, links
    back to the list page itself, bare section landing pages). Then a link is
    KEPT if ANY of these hold:
      1. Per-site override: its path contains one of `allow_paths` (from config).
      2. Same-section: its path starts with the news page's directory.
      3. Date-style: its path has a /YYYY/ segment.
      4. Its path contains "news" AND carries a real article slug (not a bare
         section page like /news or /news-events/news).

    This drops off-section pages (e.g. a company's /sustainability landing) and
    site chrome (nav "Explore corporate news", image cards, "View all news")
    while keeping the varied real-article URL shapes seen across sites.

    `require_year` (per-site, default off) is the OPPOSITE of allow_paths — a
    tightening switch for sites whose real articles ALL carry a /YYYY/ segment
    while their section-index pages don't. dsm-firmenich is the case: real
    articles are /news/<section>/2026/<slug>.html, but the list page also links
    bare section indexes (/news/share-buy-back.html, investors/.../articles-
    charters.html) that pass the generic rules via the "news"/hyphen-slug path
    and yield nav/aggregate text instead of a body. With require_year on, any
    link lacking a /YYYY/ segment is rejected UNLESS allow_paths force-keeps it,
    so those index pages drop while every dated article survives. Kept per-site
    so no other site's hyphen-slug articles are affected.
    """
    item = urlparse(item_url)
    item_path = item.path.lower()
    if not item_path or item_path == "/":
        return False

    # --- REJECTS (checked first; a match here means "not an article") --------
    # R1. Image / document / asset files are never articles. The list page often
    #     links thumbnails whose href is the image itself (e.g. Benyue's
    #     "...Portfolio.jpg", Cargill's hero image). Drop by extension.
    last_seg = item_path.rsplit("/", 1)[-1]
    if re.search(r"\.(jpe?g|png|gif|svg|webp|bmp|ico|pdf|zip|docx?|xlsx?|pptx?|"
                 r"mp4|mov|avi|mp3|css|js)$", last_seg):
        return False

    # R2. A link that points back to the news list page itself (after dropping
    #     query/fragment/trailing slash) is navigation ("Explore corporate
    #     news", "View all news"), not an article.
    news_parts = urlparse(news_url)
    def _canon(p):
        p = p.rstrip("/")
        return p or "/"
    if not item.query and _canon(item_path) == _canon(news_parts.path.lower()):
        return False

    # R2b. Cross-domain repost. A news page often links out to third-party
    #      coverage ("In The News": cargill.com/news -> forbes.com, usatoday.com,
    #      foodingredientsfirst.com). Those are other companies' pages, not this
    #      company's own news, so drop any link whose registrable domain differs
    #      from the news page's. Registrable-domain (eTLD+1) — not raw netloc — so
    #      a company's OWN subdomains survive (Döhler d-plus.doehler.com,
    #      dsm-firmenich our-company.dsm-firmenich.com). Guarded on item.netloc so
    #      a relative same-site href (empty netloc) is never wrongly rejected.
    if item.netloc and \
            _registrable_domain(item.netloc) != _registrable_domain(news_parts.netloc):
        return False

    # 1. Explicit per-site allow list wins (before the generic rejects below, so
    #    a site can force-keep a path shape our heuristics would otherwise drop).
    for frag in (allow_paths or []):
        if frag and frag.lower() in item_path:
            return True

    # R2c. Per-site "articles must be dated" gate (require_year). Only for sites
    #      configured with it (dsm-firmenich): a real article carries a /YYYY/
    #      segment; a bare section-index page does not. Checked AFTER allow_paths
    #      (so a whitelist can still force-keep) but BEFORE the generic KEEP rules
    #      (so a hyphen-slug index like /news/share-buy-back.html can't sneak
    #      through rule 4). No effect on other sites (default off).
    if require_year and not _YEAR_SEG_RE.search(item_path):
        return False

    news_path = news_parts.path.lower()
    item_segments = [s for s in item_path.split("/") if s]
    news_segments = [s for s in news_path.split("/") if s]

    # R3. Any path segment that names a non-news area of the site (products,
    #     legal, careers, portfolio, markets, generic company nav) means this is
    #     site chrome, not an article. Segment EQUALITY (not substring) so an
    #     article slug like "sweetener-solutions-for-bakery" isn't hit by the
    #     "solutions" entry. "about-*" (about-us / about-doehler / …) also drops.
    #     This is what stops a news page's own header/footer nav from flooding in
    #     (Döhler /en/markets/…, /en/our-portfolio/…; Coca-Cola /sg/en/offerings/…,
    #     /sg/en/legal/…).
    for seg in item_segments:
        seg_slug = re.sub(r"\.(html?|aspx|php|jsp|shtml)$", "", seg)
        if seg_slug in _NON_NEWS_SEGMENTS or seg_slug.startswith("about-"):
            return False

    # R4. A localized MIRROR of the same page: the first path segment is a 2-letter
    #     language code that differs from the news page's first segment (Döhler
    #     /es/…, /cn/…; Newnature /fr/…, /el/…). Same content, another language —
    #     not a new article. "en" is excluded (the default we scrape), and country
    #     roots like "sg" aren't language codes so /sg/en/… is unaffected.
    if item_segments:
        first = item_segments[0]
        news_first = news_segments[0] if news_segments else None
        if first in _LANG_CODES and first != news_first:
            return False

    # 2. Under the news SECTION root (not merely its parent dir). A real article
    #    lives BELOW the section, e.g. /media/media-releases/<slug>,
    #    /en/news-media/details/<slug>, /sg/en/media-center/<slug>. Using the
    #    section (via _news_section) — instead of the parent directory — is what
    #    prevents a shallow news_url like /en/news-media.html from treating the
    #    WHOLE /en/ tree (/en/markets/…, /en/career/…) as "same section".
    news_section = _news_section(news_path)
    if news_section and news_section != "/" and \
            item_path.startswith(news_section + "/"):
        remainder = item_path[len(news_section):].strip("/")
        rem_slug = re.sub(r"\.(html?|aspx|php|jsp|shtml)$", "", remainder)
        # Keep only if there's a further path segment (the article slug) OR a
        # query string identifying the item OR the remainder is a pure numeric ID
        # file (Adorvia's /news/80.html — an ID-based CMS where the article
        # filename is just its numeric id). Known section names (company-news,
        # trade-media, cargill-stories, …) are dropped even though they carry a
        # hyphen — they're sibling landing pages, not articles. A pure-number
        # remainder is only reached here when it sits BELOW the section root;
        # a paginated list file AT the section root (/news/0.html) was already
        # resolved to the section itself by _news_section, so it never lands here.
        if rem_slug not in _SECTION_SLUGS and (
                item.query or ("/" in remainder) or
                re.fullmatch(r"\d+", rem_slug) or
                (rem_slug.count("-") + rem_slug.count("_") >= 1)):
            return True

    # 3. Date-style article path.
    if _YEAR_SEG_RE.search(item_path):
        return True

    # 4. "news" in the path AND a real article slug beyond it. A bare section
    #    page like /news or /news-events/news is nav (its last segment is just
    #    "news"); a real article has a descriptive last segment.
    if "news" in item_path:
        tail = item_path.rstrip("/").rsplit("/", 1)[-1]
        tail_slug = re.sub(r"\.(html?|aspx|php|jsp|shtml)$", "", tail)
        if tail_slug not in _SECTION_SLUGS and \
           (tail_slug.count("-") + tail_slug.count("_") >= 1 or item.query
            or _YEAR_SEG_RE.search(item_path)):
            return True

    # 5. Same-host article file with a LONG descriptive slug. Catches sites whose
    #    articles sit at the site root with long filenames (e.g.
    #    newnaturebio.com/natural-sweeteners-and-functional-polyols-....html)
    #    rather than under /news/. Requires >=3 word separators: real root-level
    #    article slugs are long sentences (the Newnature articles have 6-10
    #    hyphens), whereas root-level PRODUCT/nav pages are short 1-2 word slugs
    #    ("functional-polyols", "high-intensity-sweeteners", "food-thickeners")
    #    that must NOT be mistaken for articles. A query string alone also
    #    qualifies (an ?id=/?aid= item identifier).
    if item.netloc.lower().lstrip("www.") == news_parts.netloc.lower().lstrip("www."):
        last = item_path.rstrip("/").rsplit("/", 1)[-1]
        has_ext = bool(re.search(r"\.(html?|aspx|php|jsp|shtml)$", last))
        slug = re.sub(r"\.(html?|aspx|php|jsp|shtml)$", "", last)
        # Long descriptive slug only. A query string does NOT qualify here:
        # GL Stevia's category/product pages (use.aspx?aid=506 "Applications",
        # News.aspx?acid=191 a section list) are same-host .aspx?query pages
        # that are NOT articles, and a bare "?query keeps it" clause let them
        # all through. Real query-identified articles (GL Stevia's
        # NewsDetail.aspx?aid=N) are kept by rule 4 ("news" in path + query)
        # and rule 6 (the "detail" marker), so dropping the query clause here
        # loses no genuine article while cutting the category-page leak.
        #
        # The slug may be a FILE (…-slug.html) or an EXTENSIONLESS permalink
        # (WordPress /…-slug/): ISA (sweeteners.org) publishes root-level
        # extensionless permalinks whose headline slugs run 8-20 hyphens. A bare
        # extensionless path segment is far more likely to be nav than a file is
        # (no CMS-detail extension to lean on), and ISA's own root nav pages are
        # short info slugs (/what-are-low-no-calorie-sweeteners = 5,
        # /role-in-a-healthy-diet = 4, /benefits-for-people-with-diabetes = 4).
        # So require >=6 separators for extensionless slugs -- comfortably above
        # those nav pages (<=5) and below every real ISA headline (>=8). File
        # slugs keep the original >=3 (Newnature's .html articles rely on it).
        min_seps = 3 if has_ext else 6
        if slug.count("-") + slug.count("_") >= min_seps:
            return True

    # 6. Article-detail path markers. Some CMSs route articles through a path
    #    like /new_cn_detail/id/14.html (安徽金禾) or /article/123 / /content/show
    #    that has no /YYYY/ segment and no "news" token, so rules 2-5 miss them.
    #    "detail"/"article" are strong article signals; the others are anchored to
    #    a path segment so a substring like "review" can't trip them. This only
    #    ADDS matches, so the worst case is a stray link opened once and skipped
    #    for lack of a date.
    if _ARTICLE_PATH_RE.search(item_path):
        return True

    return False


def _list_date_for_anchor(anchor):
    """
    Given a BeautifulSoup <a> tag on a news-list page, look for a date in the
    small neighborhood around it (the anchor itself, its parent, and the parent's
    parent). This is the date shown next to the headline in the listing, used to
    decide freshness WITHOUT opening the article. Scoped snippets keep bare-date
    matching safe. Returns a normalized date string or None.
    """
    # Build the list of scopes to search, widening outward from the anchor:
    #   anchor -> parent -> grandparent -> enclosing list-item/card container.
    # Many sites put the date in a sibling node (e.g. <div class="time">) that
    # only shares the enclosing <li>/<article>/card, not the anchor's immediate
    # parent, so we walk up to that container too.
    scopes = [anchor, anchor.parent, getattr(anchor.parent, "parent", None)]
    try:
        card = anchor.find_parent(["li", "article"]) or anchor.find_parent(
            attrs={"class": re.compile(r"(item|card|news|list|post|entry|media)", re.I)}
        )
    except Exception:
        card = None
    if card is not None and card not in scopes:
        scopes.append(card)

    # 1) A <time> element in any scope is the most reliable signal.
    for scope in scopes:
        if scope is None:
            continue
        try:
            t = scope.find("time")
        except Exception:
            t = None
        if t is not None:
            dt = t.get("datetime") or t.get_text(" ", strip=True)
            d = _normalize_date(dt) or find_date_in_text(dt)
            if d:
                return d

    # 1b) A dedicated date element (class/id like date/time/pubdate) is the next
    #     most reliable signal — and it beats scanning the whole card text, which
    #     can also contain a DIFFERENT date inside the teaser/body (e.g.
    #     howtiangroup.cn shows the real date in <div class="date">26.07.17</div>
    #     while the teaser mentions "2026年7月13日至15日"). We only trust such an
    #     element when its OWN text is short (it's a dateline, not a paragraph).
    for scope in scopes:
        if scope is None:
            continue
        try:
            dated = scope.find_all(attrs={"class": _DATE_CLASS_RE}) + \
                    scope.find_all(attrs={"id": _DATE_CLASS_RE})
        except Exception:
            dated = []
        for el in dated:
            try:
                txt = el.get_text(" ", strip=True)
            except Exception:
                continue
            if txt and len(txt) <= 40:
                d = _normalize_date(txt) or find_date_in_text(txt)
                if d:
                    return d

    # 2) Otherwise scan the text of each scope for a date, smallest first, so a
    #    tight snippet wins over a big blob. Allow a larger cap for the enclosing
    #    card (news-list rows can carry a short teaser alongside the date).
    scored = []
    for scope in scopes:
        if scope is None:
            continue
        try:
            snippet = scope.get_text(" ", strip=True)
        except Exception:
            continue
        scored.append(snippet)
    for snippet in sorted(scored, key=len):
        if len(snippet) <= 500:
            d = find_date_in_text(snippet)
            if d:
                return d
    return None


_FILENAME_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|bmp|ico|pdf|zip|docx?|xlsx?|pptx?|mp4|mov|"
    r"avi|mp3|css|js)$", re.I)


def _looks_like_filename(s):
    """
    True if a string is really an image/asset filename rather than a headline,
    e.g. "Natural-Sweeteners-Portfolio.jpg" or "hero_banner_2.png". We treat it
    as a filename when it ends in a known asset extension. (A real headline that
    merely mentions a file type mid-sentence won't end in the bare extension.)
    """
    if not s:
        return False
    return bool(_FILENAME_EXT_RE.search(s.strip()))


# Romance-language function words + accented letters used to detect a NON-English
# headline. Used only by the per-site english_only switch (IFF): IFF's newsroom
# mixes in Spanish/Portuguese TRANSLATIONS of the same story (e.g.
# "Fortalecimiento de la masa en la panificación industrial …"), and — unlike the
# usual /es/ /pt/ path mirrors caught by R4 — IFF encodes the language only in the
# slug and serves them all as <html lang="en-US">, so there is no path/metadata
# signal. The title text is the only signal left. To stay conservative (never drop
# a real English headline) we require BOTH a Romance accented letter AND at least
# two distinct Romance stopwords — English headlines have neither.
_ROMANCE_ACCENT_RE = re.compile(r"[à-ü]", re.I)
_ROMANCE_STOPWORDS = frozenset({
    "de", "la", "el", "los", "las", "en", "más", "mas", "para", "con", "una",
    "del", "por", "y", "e", "da", "do", "das", "dos", "na", "no", "mais",
    "produção", "eficiente", "consistencia", "consistência", "estabilidad",
})


def _looks_non_english(s):
    """Best-effort: True if a headline is clearly a Romance-language (Spanish/
    Portuguese) translation, not English. Conservative — requires a Romance
    accented character AND >=2 Romance stopwords, so real English headlines
    (which have neither) are never dropped. Only consulted for sites configured
    with english_only=True (IFF)."""
    if not s:
        return False
    if not _ROMANCE_ACCENT_RE.search(s):
        return False
    words = re.findall(r"[a-záàâãéêíóôõúüñç]+", s.lower())
    hits = sum(1 for w in set(words) if w in _ROMANCE_STOPWORDS)
    return hits >= 2


# Leading "call to action" phrase that some sites prepend to the real headline
# when building an accessible link label, e.g. Givaudan's aria-label
# "Read more about <headline>". Stripping it recovers the headline instead of
# discarding the whole link as a placeholder. A bare "Read more" (no following
# headline) doesn't match here — it needs the "about"/":"/"-" continuation —
# so ADM's standalone "Read More" is still treated as a placeholder elsewhere.
_CTA_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"read\s+more\s+about|"
    r"read\s+more\s*[:\-–—]|"
    r"read\s+the\s+(?:full\s+)?(?:story|article)(?:\s+about)?\s*[:\-–—]?|"
    r"learn\s+more\s+about|"
    r"阅读更多[：:]?|查看详情[：:]?"
    r")\s*",
    re.I,
)


def _strip_cta_prefix(s):
    """
    Remove a leading call-to-action phrase ("Read more about <headline>" ->
    "<headline>"). Returns the remainder (stripped) or the original string when
    there's no CTA prefix. Only strips when a headline follows the phrase.
    """
    if not s:
        return s
    return _CTA_PREFIX_RE.sub("", s).strip()


# A dateline some list cards append to the headline inside the same <a>, so the
# anchor text becomes "<headline> Jul. 24, 2026" (Cargill). The month is written
# ABBREVIATED WITH A PERIOD ("Jul.", "May.", "Apr."), which is what makes this
# safe to strip: a real headline never ends in "<Mon>. DD, YYYY". A full-month
# date that is genuinely part of a headline ("… Results on August 4, 2026") has
# NO period after the month, so it is left intact. Anchored to end-of-string.
_TRAILING_DATELINE_RE = re.compile(
    r"\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.\s+"
    r"\d{1,2},?\s+\d{4}\s*$", re.I)


def _strip_trailing_dateline(s):
    """Remove a trailing "Mon. DD, YYYY" dateline appended to a headline (Cargill
    cards glue the publish date onto the title text). Only the abbreviated form
    with a period after the month is stripped, so headlines that legitimately end
    in a date ("… on August 4, 2026") are untouched. Returns the remainder."""
    if not s:
        return s
    return _TRAILING_DATELINE_RE.sub("", s).strip()


def _best_title(a):
    """
    Best-effort headline for a BeautifulSoup <a> tag. Modern card layouts often
    put the title in a sibling heading rather than the <a> text (the <a> wraps
    only an image or a "Read more"), so fall back through:
      <a> title/text -> aria-label -> inner <img alt> -> nearby heading
    in the anchor's container before giving up. Returns a cleaned string
    (possibly shorter than MIN_TITLE_LEN; the caller enforces the threshold).
    """
    def clean(s):
        return re.sub(r"\s+", " ", (s or "").strip())

    # Generic "call to action" link text that is not a real headline. If the
    # anchor text is only this, keep looking (aria-label / heading / img alt).
    # Normalize first: drop a trailing parenthetical ("(link opens in a new
    # window)") and any "- ..." suffix, so ADM's aria-labels like "Read More
    # (link opens in a new window)" / "Read More - Read more about News" are
    # recognized as placeholders too, not mistaken for a headline.
    def is_placeholder(s):
        core = re.sub(r"\s*\([^)]*\)\s*", " ", s.lower())
        core = re.split(r"\s+-\s+", core, 1)[0].strip()
        if core in _PLACEHOLDER_TITLES:
            return True
        return core.startswith(("read more", "learn more", "阅读更多", "查看详情"))

    def usable(s):
        # Clean, then recover a headline from an accessible link label like
        # "Read more about <headline>" by stripping the leading CTA phrase.
        # Return the headline if it's real (long enough, not a bare nav/CTA
        # placeholder, not an image filename); otherwise None so the caller
        # falls through to the next source. Stripping the CTA prefix is what
        # keeps Givaudan's aria-label articles ("Read more about Givaudan
        # inaugurates …") from being discarded wholesale.
        s = _strip_trailing_dateline(_strip_cta_prefix(clean(s)))
        if len(s) >= MIN_TITLE_LEN and not is_placeholder(s) \
           and not _looks_like_filename(s):
            return s
        return None

    # 1) An explicit title attribute, when it's a real headline.
    cand = usable(a.get("title"))
    if cand:
        return cand

    # 2) A heading / title-class element INSIDE the anchor. Some list layouts
    #    wrap the WHOLE card (image + <h4> headline + date + teaser) in a single
    #    <a>, so a.get_text() below would return the entire blob. Prefer the
    #    inner heading, which is just the headline -- e.g. GL Stevia's
    #    <a>…<div class="news-list-cont"><h4>Title</h4>
    #      <p class="news-time">Time：…</p><p class="news-dase">teaser…</p></a>.
    inner = a.find(["h1", "h2", "h3", "h4"]) or \
        a.find(attrs={"class": _TITLE_CLASS_RE})
    if inner is not None:
        cand = usable(inner.get_text(" ", strip=True))
        if cand:
            return cand

    # 3) The anchor's own text.
    title = clean(a.get_text(" ", strip=True))
    cand = usable(title)
    if cand:
        return cand

    # 4) aria-label on the anchor. Often an accessible label that PREPENDS a CTA
    #    to the headline ("Read more about <headline>"); usable() strips that.
    cand = usable(a.get("aria-label"))
    if cand:
        return cand

    # 5) alt text of an <img> inside the anchor. Guard against alt values that
    #    are just the image FILENAME ("Natural-Sweeteners-Portfolio.jpg") — those
    #    are not headlines and were leaking in as fake articles (Benyue's .jpg
    #    cards, a stock-photo alt on Cargill). usable() enforces that guard.
    img = a.find("img")
    if img is not None:
        cand = usable(img.get("alt"))
        if cand:
            return cand

    # 6) A headline in the enclosing <article> card. Some card layouts make the
    #    linking anchor an EMPTY overlay (e.g. Tate & Lyle's
    #    <a class="news-teaser-overlay" href="/news/..."></a>) with the title in
    #    a sibling <h3>. <article> is the HTML5 semantic for ONE self-contained
    #    item, so a heading inside it belongs to THIS card -- unlike the generic
    #    ancestor climb below we do NOT stop on a multi-link container, because
    #    the card also holds a decorative category-icon link (which is exactly
    #    what breaks the climb at distinct_link_count>1). We only reach here when
    #    steps 1-5 found no usable title on the anchor itself (its own text,
    #    aria-label, img alt), so a normal card whose anchor already yields the
    #    headline is unaffected -- this only rescues empty-overlay cards. Skip
    #    visually-hidden a11y headings (class hide/sr-only/visually-hidden/
    #    screen-reader), which are icon labels ("Press releases grey"), not real
    #    headlines.
    art = a.find_parent("article")
    if art is not None:
        for h in art.find_all(["h1", "h2", "h3", "h4"]):
            classes = " ".join(h.get("class", [])).lower()
            if re.search(r"(?:^|[-_ ])(hide|hidden|sr-only|"
                         r"visually-hidden|screen-reader)", classes):
                continue
            cand = usable(h.get_text(" ", strip=True))
            if cand:
                return cand

    # A title in the anchor's enclosing card. The immediate parent often only
    # wraps the "Read more" button (e.g. ADM's CTA sits in
    # <div class="adm-div-c10-card-button">, while the real title lives in a
    # SIBLING <div class="adm-div-c10-card-title"> under a higher card wrapper).
    # So walk up several ancestors and, at each level, look for a heading or a
    # title-class element; return the first good, non-placeholder hit. Widening
    # outward one level at a time keeps the match close to this anchor.
    def distinct_link_count(node):
        # Distinct link destinations inside a container. A single article card
        # points everything (image / title / "read more") at ONE url, so its
        # count is 1; a shared list wrapper (e.g. <ul class="row"> holding many
        # cards, or a <div class="page_list"> of pagination links) has several.
        hrefs = set()
        for x in node.find_all("a"):
            h = x.get("href")
            if not h:
                continue
            h = h.split("#")[0]
            if not h or h.startswith(("javascript:", "mailto:", "tel:")):
                continue
            hrefs.add(h)
        return len(hrefs)

    node = a
    for _ in range(4):
        node = node.parent
        if node is None or getattr(node, "name", None) is None:
            break
        # Once we climb into a container that links to more than one distinct
        # destination, we've left this anchor's own card and any heading here
        # belongs to a SIBLING card. Stop — otherwise pagination / "next page"
        # links (whose own <div> has no title) would inherit the first
        # article's headline from the shared list wrapper.
        if distinct_link_count(node) > 1:
            break
        found = node.find(["h1", "h2", "h3", "h4"]) or \
            node.find(attrs={"class": _TITLE_CLASS_RE})
        if found is not None:
            cand = usable(found.get_text(" ", strip=True))
            if cand:
                return cand

    # No headline found anywhere. Return "" (not the raw anchor text): step 3
    # already tested the anchor text via usable() and it failed — handing it
    # back now would re-admit the very placeholder / nav label / filename we
    # rejected there. The caller's length gate then drops the link.
    return ""


# Pagination links inside a pinned container ("?page=2", "/page/3/",
# "News.aspx?page=4", "/news-6-2.html"-style) are list chrome, not articles.
_PAGINATION_RE = re.compile(
    r"([?&]page=\d+|/page/\d+/?$|[?&]p=\d+$)", re.IGNORECASE)


# Files that are page furniture or not human-readable content: images, styles,
# scripts, bare media files, and .ics calendar subscriptions. Always rejected.
# (.ics matters for Döhler: its newsroom pairs every trade-fair card with an
# "Add to calendar" .ics link that has NO title and duplicates the HTML card,
# and those entries carry FUTURE dates — see the timestamp clamp below.)
_FURNITURE_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|bmp|ico|css|js|mp4|mov|avi|mp3|ics|zip|rar|7z)$",
    re.I)
# Documents a reader can actually open and read. These are real content when a
# site publishes press coverage as files rather than pages — Zydus's newsroom
# links 19 newspaper-clipping PDFs on its own domain, with titles and dates.
_DOCUMENT_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)$", re.I)


def _light_reject(abs_url, news_url, require_year=False, allow_external=False,
                  exclude_paths=None):
    """The filters a pinned news-list container does NOT make redundant.

    The container excludes nav/footer/sidebar, so the URL-shape KEEP heuristics
    of is_probable_article are unnecessary here. But real containers still hold
    (verified 2026-08-03 real-driver run): cross-domain reposts sitting in the
    list as first-class cards (Zydus 57/77, ADM, Döhler, 科宏), asset/calendar
    files (Döhler .ics), links back to the list page itself and pagination
    (GL Stevia, Layn), and undated section-index pages (dsm share-buy-back).

    `allow_external=True` (per-site, config/list_selectors.json) keeps
    cross-domain links instead of dropping them: some newsrooms ARE a
    press-coverage wall, where third-party articles and videos are the content
    the site is publishing (Zydus). Those items get no AI summary — the caller
    marks them external and the digest shows title + link only.

    Returns a reject-reason string, or None to keep.
    """
    item = urlparse(abs_url)
    item_path = item.path.lower()
    news_parts = urlparse(news_url)

    # Non-readable files are always out; readable documents (PDF clippings etc.)
    # fall through to the normal rules.
    last_seg = item_path.rsplit("/", 1)[-1]
    if _FURNITURE_EXT_RE.search(last_seg):
        return "asset"

    # Link back to the list page itself / pagination.
    def _canon(p):
        p = p.rstrip("/")
        return p or "/"
    if not item.query and _canon(item_path) == _canon(news_parts.path.lower()):
        return "self"
    if _PAGINATION_RE.search(abs_url):
        return "pagination"

    # Cross-domain repost: the one signal a shared card template can't hide.
    # eTLD+1 comparison keeps the company's own subdomains.
    if item.netloc and \
            _registrable_domain(item.netloc) != _registrable_domain(news_parts.netloc):
        if not allow_external:
            return "cross-domain"

    # Per-site dated-articles gate (dsm's share-buy-back index pages live
    # INSIDE the pinned container, so this must still apply here). Skipped for
    # documents, whose URLs are filenames and never carry a /YYYY/ segment.
    if require_year and not _YEAR_SEG_RE.search(item_path) \
            and not _DOCUMENT_EXT_RE.search(last_seg):
        return "no-year"

    # Per-site path blocklist (config/list_selectors.json "exclude_paths"). Some
    # newsrooms file routine non-news under their own category and mix it into
    # the list we scrape — dsm-firmenich tags its weekly share-buy-back filings
    # as "press releases", so the Press-releases endpoint returns 5 buy-back
    # notices per page and 1 real release. Dropping /share-buy-back/ here keeps
    # only the real news. Substring match on the path, case-insensitive.
    if exclude_paths and any(seg in item_path for seg in exclude_paths):
        return "excluded-path"

    return None


def _is_external_item(abs_url, news_url):
    """True if this link leaves the monitored site (different registrable
    domain). Only reachable when the site sets allow_external; used to tag the
    item so the digest skips summarization for it."""
    netloc = urlparse(abs_url).netloc
    if not netloc:
        return False
    return _registrable_domain(netloc) != _registrable_domain(urlparse(news_url).netloc)


def _is_document_url(abs_url):
    """True for PDF/Office files — readable content, but no HTML body to scrape,
    so they are treated like external items (title + link, no summary)."""
    last_seg = urlparse(abs_url).path.lower().rsplit("/", 1)[-1]
    return bool(_DOCUMENT_EXT_RE.search(last_seg))


def _extract_items_by_selector(soup, base_url, list_selector,
                               require_year=False, english_only=False,
                               allow_external=False, exclude_paths=None):
    """Scrape article links inside the pinned news-list container(s).

    The container (located by its class/id) already excludes nav/footer/sidebar,
    so the URL-shape KEEP rules of is_probable_article are skipped — but the
    LIGHT rejects (cross-domain, assets, self/pagination, require_year) still
    run: real-driver verification showed that noise sits INSIDE the containers
    as first-class cards. Multiple matching containers (e.g. dsm's paged
    blocks) are merged. Returns [] if the selector matches nothing (caller then
    reports no_items).

    Items are tagged "external": True when they leave the site (only possible
    with allow_external) or point at a PDF/Office document. Those have no
    scrapable HTML body, so the pipeline skips the body fetch and the digest
    lists them as title + link with no AI summary.
    """
    containers = soup.select(list_selector)
    items, seen = [], set()
    rejected = {}
    for cont in containers:
        for a in cont.find_all("a"):
            href = a.get("href")
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            abs_url = normalize_url(urljoin(base_url, href))
            if not abs_url.startswith("http") or abs_url in seen:
                continue
            reason = _light_reject(abs_url, base_url, require_year,
                                   allow_external, exclude_paths)
            if reason:
                seen.add(abs_url)
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            title = _best_title(a)
            if len(title) < MIN_TITLE_LEN:
                continue
            # Same per-site language gate as the generic path (IFF mirrors).
            if english_only and _looks_non_english(title):
                seen.add(abs_url)
                rejected["non-english"] = rejected.get("non-english", 0) + 1
                continue
            seen.add(abs_url)
            is_ext = (_is_external_item(abs_url, base_url)
                      or _is_document_url(abs_url))
            items.append({
                "title": title,
                "url": abs_url,
                "list_date": _list_date_for_anchor(a),
                "external": is_ext,
            })
    n_ext = sum(1 for it in items if it.get("external"))
    logger.info("    Container[%s] matched %d node(s), kept %d link(s)"
                "%s%s",
                list_selector, len(containers), len(items),
                f"（其中外部/文档 {n_ext} 条，不做摘要）" if n_ext else "",
                f", rejected {rejected}" if rejected else "")
    if not containers:
        # The selector broke (site redesign?) — say so loudly instead of the
        # silent-zero failure mode selectors are prone to.
        logger.error("    ❌ list_selector %r matched NOTHING — page layout "
                     "may have changed; falling back would need the generic "
                     "path. Fix config/list_selectors.json.", list_selector)
    return items


def extract_items(driver, base_url, allow_paths=None, require_year=False,
                  english_only=False, list_selector=None, allow_external=False,
                  exclude_paths=None):
    """
    Parse the CURRENT (list) page and return candidate items:
        {title, url, list_date, external}
    list_date is the date shown next to the item in the listing (normalized
    string) or None if none could be found nearby. No article page is opened
    here — freshness is decided from these list dates by the caller.

    If `list_selector` is given (site has a pinned news-list container in
    config/list_selectors.json), scrape that container directly and skip the
    whole-page is_probable_article KEEP heuristics — the container already
    excludes nav/footer/sidebar. The LIGHT rejects (cross-domain reposts,
    assets, pagination, require_year, english_only) still apply there: verified
    container contents show that noise sits inside the list as first-class
    cards (Zydus 57/77 cross-domain, Döhler .ics, dsm undated indexes).

    `allow_external` (per-site) keeps cross-domain links in the container for
    newsrooms that publish press coverage of themselves (Zydus). Those items are
    tagged external and carried through without a body fetch or AI summary.

    Otherwise: links that don't look like real articles (nav bars, footers,
    section landing pages) are filtered out via is_probable_article, so we don't
    open or report section/landing pages as if they were news.

    `english_only` (per-site, default off) drops candidates whose headline is a
    clearly non-English (Romance-language) TRANSLATION of the same story. IFF's
    newsroom mixes Spanish/Portuguese versions in with no /es/ /pt/ path or lang
    metadata (all served as <html lang="en-US">), so R4's path-mirror rule can't
    catch them; the title text is the only signal. Conservative (see
    _looks_non_english), so real English headlines are never dropped.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(driver.page_source, "html.parser")

    # Pinned-container path: locate the news list by class/id and scrape it
    # directly (light rejects still applied inside).
    if list_selector:
        return _extract_items_by_selector(soup, base_url, list_selector,
                                          require_year, english_only,
                                          allow_external, exclude_paths)

    items = []
    seen_local = set()
    skipped_nonarticle = 0
    for a in soup.find_all("a"):
        try:
            href = a.get("href")
            if not href:
                continue
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            # Card layouts may hide the title outside the <a>; _best_title
            # falls back through aria-label / img alt / nearby heading.
            title = _best_title(a)
            if len(title) < MIN_TITLE_LEN:
                continue
            # Per-site: drop non-English translations of the same story (IFF).
            if english_only and _looks_non_english(title):
                skipped_nonarticle += 1
                continue
            abs_url = normalize_url(urljoin(base_url, href))
            if not abs_url.startswith("http"):
                continue
            if abs_url in seen_local:
                continue
            # Keep only links that look like real articles (drops nav/section
            # /landing pages that would otherwise become false "new" items).
            if not is_probable_article(abs_url, base_url, allow_paths,
                                       require_year):
                skipped_nonarticle += 1
                continue
            seen_local.add(abs_url)
            items.append({
                "title": title,
                "url": abs_url,
                "list_date": _list_date_for_anchor(a),
                # The generic path has no allow_external switch: is_probable_article
                # already dropped cross-domain links (R2b) and document files (R1),
                # so this is False today. Tagged anyway so the flag never goes
                # missing if those rules are ever relaxed for a site.
                "external": _is_document_url(abs_url),
            })
            if len(items) >= MAX_ITEMS_PER_PAGE:
                break
        except Exception:
            continue
    if skipped_nonarticle:
        logger.info("    Filtered out %d non-article links", skipped_nonarticle)
    return items


# Common article-body containers, in priority order. Many sites don't use
# <article>/<main>, so we look for these before falling back to <body>.
_CONTENT_SELECTORS = [
    "article", "main",
    ".article-body", ".article-content", ".article__body", ".article__content",
    ".news-detail", ".news-content", ".news__content", ".detail-content",
    ".post-content", ".entry-content", ".rich-text", ".rich_text",
    ".content-body", ".cmp-text", ".body-content", ".page-content",
    # Webflow's rich-text element (class "w-richtext") holds the article body on
    # Webflow-built sites (Manus Bio). Without it, the named selectors miss the
    # real body and a short "related articles" rail ([class*='article']) wins the
    # short-circuit, so the summary describes a NEIGHBOURING teaser, not this
    # article. Webflow-specific class, so it can't mis-target non-Webflow sites.
    ".w-richtext",
    "[class*='article']", "[class*='content']", "[itemprop='articleBody']",
]


def _find_content_container(soup):
    """
    Pick the element most likely to hold the article body. Tries a list of
    common containers and, among those that match, returns the one with the
    most text (the real body usually dwarfs sidebars/teasers). Falls back to
    <body> or the whole soup if nothing matches.
    """
    best = None
    best_len = 0
    for sel in _CONTENT_SELECTORS:
        try:
            for el in soup.select(sel):
                # Never treat the document root as the article body. The wildcard
                # selectors ([class*='content'] etc.) can match <html>/<body>
                # themselves when their class list happens to contain the
                # substring — e.g. Symrise's <html class="… generatedcontent …">
                # (a Modernizr feature-detection class) matches [class*='content']
                # and, being the whole document, always has the most text, so it
                # would win over the real [itemprop='articleBody']. Skipping the
                # roots forces selection of a genuine inner container.
                if el.name in ("html", "body"):
                    continue
                tlen = len(el.get_text(strip=True))
                if tlen > best_len:
                    best, best_len = el, tlen
        except Exception:
            continue
    # A named-selector match is the most reliable, so trust it if it has body.
    if best is not None and best_len >= 120:
        return best

    # Fallback: no known selector matched (e.g. sites that name the article body
    # with a generic class like <div class="bd"> or ASP.NET .aspx layouts). Pick
    # the element with the most PROSE — total text minus link text — so we land
    # on the article container rather than the whole <body> (which drags in the
    # nav menu / breadcrumb link lists). Guarded by a minimum so a page that's
    # genuinely link-heavy still falls back to <body>.
    def prose_len(el):
        total = len(el.get_text(" ", strip=True))
        link_text = sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))
        return total - link_text

    prose_best, prose_score = None, 0
    for el in soup.find_all(["article", "main", "section", "div"]):
        score = prose_len(el)
        if score > prose_score:
            prose_best, prose_score = el, score
    if prose_best is not None and prose_score >= 200:
        return prose_best

    return soup.body or soup


def _extract_detail_title(soup):
    """
    Best-effort article headline from an already-parsed DETAIL page, used to
    RESCUE a hit whose list-page title was a placeholder ("Go to article
    details"). Sources, in order of reliability:
      1. <meta property="og:title"> / <meta name="twitter:title"> — what the site
         itself declares as the page's title for social sharing; the cleanest.
      2. the first <h1> — the on-page headline.
      3. <title>, with a trailing " | Site Name" / " - Site Name" suffix trimmed.
    Returns a cleaned headline (>= MIN_TITLE_LEN, not a nav/CTA placeholder) or
    "" if none qualifies. Never raises. This is ADDITIVE: the caller only uses it
    to replace a title already judged junk, so a miss here changes nothing.
    """
    def clean(s):
        return re.sub(r"\s+", " ", (s or "").strip())

    def good(s):
        s = clean(s)
        return s if (len(s) >= MIN_TITLE_LEN and not _is_placeholder_title(s)
                     and not _looks_like_filename(s)) else ""

    try:
        # 1) Social/meta title.
        for attrs in ({"property": "og:title"}, {"name": "og:title"},
                      {"name": "twitter:title"}, {"property": "twitter:title"}):
            m = soup.find("meta", attrs=attrs)
            if m:
                cand = good(m.get("content"))
                if cand:
                    return cand

        # 2) First on-page <h1>.
        h1 = soup.find("h1")
        if h1 is not None:
            cand = good(h1.get_text(" ", strip=True))
            if cand:
                return cand

        # 3) <title>, minus a trailing " | Brand" / " - Brand" / " – Brand".
        if soup.title:
            raw = clean(soup.title.get_text(" ", strip=True))
            trimmed = re.split(r"\s+[|–—-]\s+", raw)[0].strip()
            cand = good(trimmed) or good(raw)
            if cand:
                return cand
    except Exception:
        pass
    return ""


def fetch_article(driver, url):
    """
    Load an article URL. Return (publish_date, body_text, detail_title).
    publish_date is a normalized string or None; body_text is best-effort text;
    detail_title is a best-effort headline read from the detail page (og:title /
    h1 / <title>), used to rescue hits whose list-page title was a placeholder
    ("Go to article details"), or "" if none. Uses load_page for
    retry-with-backoff so a transient failure doesn't lose the article.
    """
    from bs4 import BeautifulSoup

    try:
        if not load_page(driver, url):
            return None, "", ""
        page_source = driver.page_source

        # 1) Structured extraction (meta tags, <time>, JSON-LD, ISO datetime).
        publish_date = extract_publish_date(page_source)

        soup = BeautifulSoup(page_source, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript",
                         "aside"]):
            tag.decompose()
        # Drop <form> too, but ONLY small ones (search box, login, newsletter).
        # ASP.NET WebForms sites (e.g. GL Stevia's .aspx) wrap the ENTIRE page
        # — article body included — in a single <form id="aspnetForm">, so
        # blindly decomposing every form would delete the whole article. A real
        # junk form is short; a page-wrapping form is large, so guard by length.
        for tag in soup.find_all("form"):
            if len(tag.get_text(strip=True)) < 200:
                tag.decompose()
        # Drop third-party cookie/consent widgets (Cookiebot, OneTrust, …). They
        # inject THOUSANDS of chars of boilerplate ("This website uses cookies …
        # Maximum Storage Duration …") as plain <div>s — not nav/aside/footer, so
        # the strip above misses them. Left in, they sit at the TOP of the body
        # text and, once it's capped for the summarizer, crowd out the real
        # article (Symrise's Cookiebot dialog was ~8800 chars). Match the stable
        # IDs/classes these widgets use; scoped enough not to touch article text.
        for sel in ("#CybotCookiebotDialog", "#onetrust-consent-sdk",
                    "#onetrust-banner-sdk", "[id*='CookieConsent']",
                    "[class*='cookie-consent']", "[class*='cookiebanner']",
                    "[class*='CybotCookiebot']"):
            try:
                for tag in soup.select(sel):
                    tag.decompose()
            except Exception:
                continue

        # 2) Date fallback BEFORE narrowing to the article body. Many sites put
        #    the publish date in a small date bar / info box that sits OUTSIDE
        #    the article container (e.g. Adorvia's <div class="info">Jul. 16,
        #    2026 Views:41</div>), so searching only the article body misses it.
        #    We scan the top region of the whole page, where such datelines and
        #    info bars live, before the container is narrowed for the summary.
        if not publish_date:
            page_top = soup.body.get_text(separator="\n", strip=True) if soup.body else ""
            publish_date = find_date_in_text(page_top[:600])

        # 3) Narrow to the article body for the SUMMARY text (kept separate from
        #    date extraction so a clean body doesn't cost us the date).
        container = _find_content_container(soup)
        text = container.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 4) Last resort: if still no date, try the (narrowed) body text too.
        if not publish_date and text:
            publish_date = find_date_in_text(text[:400])

        # 5) Headline for title rescue. Read from the decomposed soup: og:title
        #    / <title> live in <head> (untouched by the decompose above) and the
        #    article <h1> in the body — none are the nav/header we stripped.
        detail_title = _extract_detail_title(soup)

        return publish_date, text[:8000], detail_title  # cap body for summarizer
    except Exception as e:
        print(f"    ⚠️  Could not fetch article {url}: {e}")
        return None, "", ""


# --------------------------------------------------------------------------- #
# JSON-API sites (Vue/SPA that render nothing server-side but expose a clean
# backend feed, e.g. 元气森林). Driven entirely by the site's "api" config block
# so no per-site code lives here; a new SPA only needs a config entry.
# --------------------------------------------------------------------------- #
def _dig(obj, dotted_path):
    """Walk a dotted key path (e.g. 'data.data') into nested dict/list JSON."""
    cur = obj
    for key in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _http_json(url, data=None, timeout=30):
    """
    GET (data=None) or POST (data=dict, form-encoded) a URL and parse JSON.
    Uses urllib from the stdlib so we add no new dependency. Returns the parsed
    object or None on any error.
    """
    import urllib.request
    import urllib.parse

    headers = {
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except Exception as e:
        logger.warning("    API request failed (%s): %s", url, e)
        return None


def extract_items_api(site):
    """
    Build the same {title, url, list_date} item dicts as extract_items(), but
    from a site's JSON list API instead of a rendered page. Config lives under
    site["api"]. Returns [] on any failure so the caller degrades gracefully.
    """
    api = site.get("api", {})
    payload = _http_json(api["list_url"])
    if payload is None:
        return []
    rows = _dig(payload, api.get("list_items_path", "data")) or []
    if not isinstance(rows, list):
        return []

    title_key = api.get("list_title_key", "title")
    date_key = api.get("list_date_key", "day")
    id_key = api.get("list_id_key", "id")
    tmpl = api.get("article_url_template", "")

    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (row.get(title_key) or "").strip()
        if not title:
            continue
        rid = row.get(id_key)
        url = tmpl.format(id=rid) if tmpl and rid is not None else api["list_url"]
        items.append({
            "title": title,
            "url": url,
            "list_date": _normalize_date(str(row.get(date_key) or "")),
            "_api_id": rid,  # kept so the detail call knows which article to open
        })
    return items


def fetch_article_api(site, item):
    """
    Fetch one article's body via the site's detail JSON API. Returns
    (publish_date, body_text, detail_title) mirroring fetch_article(). The detail
    endpoint is POSTed the article id; the body HTML at detail_content_path is
    stripped to text. Date falls back to the list date (already on the item).
    detail_title is "" — API sites carry a clean list title, so no rescue needed.
    """
    api = site.get("api", {})
    rid = item.get("_api_id")
    detail_url = api.get("detail_url")
    if not detail_url or rid is None:
        return item.get("list_date"), "", ""

    payload = _http_json(detail_url, data={api.get("detail_id_param", "id"): rid})
    if payload is None:
        return item.get("list_date"), "", ""

    content_html = _dig(payload, api.get("detail_content_path", "data.content")) or ""
    body = ""
    if content_html:
        from bs4 import BeautifulSoup
        body = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
        body = re.sub(r"\n{3,}", "\n\n", body)[:8000]

    # Prefer a date from the detail payload if present, else the list date.
    date_key = api.get("list_date_key", "day")
    detail_date = _dig(payload, f"data.{date_key}")
    publish_date = _normalize_date(str(detail_date)) if detail_date else None
    return (publish_date or item.get("list_date")), body, ""


# --------------------------------------------------------------------------- #
# Main entry
# --------------------------------------------------------------------------- #
def _resolve_baseline(last_times, name):
    """
    Return (baseline_dt, baseline_str) for a site, protecting against a
    corrupt/unparseable stored timestamp. If the stored value can't be parsed,
    fall back to DEFAULT_LAST_TIME and warn LOUDLY — otherwise a corrupt entry
    would make every dated article look "new" and trigger an email flood.

    TEST-ONLY: if BASELINE_OVERRIDE (env MONITOR_BASELINE) is set and valid, use
    it for every site, ignoring saved timestamps — a recent value makes runs
    finish fast for testing.
    """
    if BASELINE_OVERRIDE:
        dt = parse_dt(BASELINE_OVERRIDE)
        if dt is not None:
            return dt, BASELINE_OVERRIDE
        logger.warning("MONITOR_BASELINE=%r is invalid; ignoring it.",
                       BASELINE_OVERRIDE)

    raw = get_last_time(last_times, name)
    dt = parse_dt(raw)
    if dt is None:
        logger.warning("Stored last-time for '%s' is invalid (%r); "
                       "falling back to default baseline %s",
                       name, raw, DEFAULT_LAST_TIME)
        raw = DEFAULT_LAST_TIME
        dt = parse_dt(DEFAULT_LAST_TIME)
    return dt, raw


def _write_summary(summary_path, outcomes):
    """
    Write a concise per-site outcome table, highlighting problem sites and
    exactly which stage they got stuck at. Also logs the same table.
    """
    order = {
        "load_failed": "❌ 页面加载失败",
        "no_items": "❌ 列表未解析出条目",
        "all_undated": "⚠️ 有新条目但全部读不到日期",
        "body_empty": "⚠️ 打开了文章但正文全为空",
        "ok": "✅ 成功",
        "no_new": "· 无新文章（正常）",
    }
    lines = []
    lines.append("=" * 60)
    lines.append("站点抓取结果汇总")
    lines.append("=" * 60)

    problems = [o for o in outcomes if o["status"] not in ("ok", "no_new")]
    if problems:
        lines.append(f"\n⚠️ 有问题的站点（{len(problems)} 个）——按卡住的环节分类：\n")
        for o in problems:
            label = order.get(o["status"], o["status"])
            lines.append(f"  {label}  |  {o['name']}")
            lines.append(f"       {o['detail']}")
    else:
        lines.append("\n所有站点均正常（无卡壳）。\n")

    lines.append("\n" + "-" * 60)
    lines.append("全部站点明细：")
    for o in outcomes:
        label = order.get(o["status"], o["status"])
        lines.append(f"  {label:22}  {o['name']}  |  {o['detail']}")
    lines.append("=" * 60)

    text = "\n".join(lines)
    logger.info("\n%s", text)
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        logger.warning("Could not write summary file: %s", e)


def fetch_all_new_hits():
    """
    Scan all enabled sites. For each:
      * read candidate items + their list-page dates (no article load),
      * select items whose list date is newer than the saved baseline,
      * open ONLY those (oldest first) to fetch body text and refine the date,
      * advance the saved timestamp only past items actually processed.

    Logs progress per site to logs/monitor_*.log and writes a problem-site
    summary. Returns a list of hit dicts:
      {site, title, url, publish_date, body_text}
    """
    log_path, summary_path = setup_logging()
    logger.info("Log file: %s", log_path)

    sites = load_sites()
    last_times = load_last_times()
    hits = []
    outcomes = []  # per-site outcome records for the summary

    driver, display = build_driver()
    try:
        for site in sites:
            name = site["name"]
            news_url = site["news_url"]
            baseline_dt, baseline_str = _resolve_baseline(last_times, name)
            logger.info("=== %s ===", name)
            logger.info("    %s", news_url)
            logger.info("    Last article time: %s", baseline_str)

            # A JSON-API site (Vue/SPA) skips the browser entirely: its list and
            # bodies come from a backend feed described in site["api"].
            api_mode = site.get("fetch_mode") == "json_api"
            if api_mode:
                items = extract_items_api(site)
                logger.info("    Extracted %d candidate items (JSON API)", len(items))
                if not items:
                    outcomes.append({"name": name, "status": "no_items",
                                     "detail": "JSON API 未返回任何新闻条目"
                                               "（接口结构可能已变）"})
                    continue
            else:
                # Load the news list page with retry, waiting for anchors to
                # render (many sites build their list with JS after
                # readyState=complete).
                if not load_page(driver, news_url,
                                 min_anchors=MIN_ANCHORS_ON_NEWS_PAGE,
                                 wait_for=site.get("wait_for")):
                    outcomes.append({"name": name, "status": "load_failed",
                                     "detail": f"新闻页加载失败: {news_url}"})
                    continue

                items = extract_items(driver, news_url, site.get("allow_paths"),
                                      site.get("require_year", False),
                                      site.get("english_only", False),
                                      site.get("list_selector"),
                                      site.get("allow_external", False),
                                      site.get("exclude_paths"))
                logger.info("    Extracted %d candidate items", len(items))
                if not items:
                    outcomes.append({"name": name, "status": "no_items",
                                     "detail": "列表页一个候选链接都没解析出来"
                                               "（可能是 JS 动态渲染 / 结构特殊）"})
                    continue

            # Split candidates: those with a usable LIST date vs. those without.
            dated_new = []       # (list_dt, item) — list date newer than baseline
            undated_items = []   # items with no readable list date
            for it in items:
                item_dt = parse_dt(it.get("list_date"))
                if item_dt is None:
                    undated_items.append(it)
                elif item_dt > baseline_dt:
                    dated_new.append((item_dt, it))

            # Oldest first so that, if we exceed the open budget, we keep the
            # oldest and the newest are picked up next run (timestamp only
            # advances past what we actually processed).
            dated_new.sort(key=lambda pair: pair[0])
            total_new = len(dated_new)
            if total_new > MAX_NEW_ITEMS_TO_OPEN:
                logger.warning("    %d dated-new items; processing oldest %d "
                               "this run, rest next run",
                               total_new, MAX_NEW_ITEMS_TO_OPEN)
                batch = dated_new[:MAX_NEW_ITEMS_TO_OPEN]
            else:
                batch = dated_new

            logger.info("    New (by list date): %d | undated on list: %d | "
                        "opening dated: %d", total_new, len(undated_items), len(batch))

            highest_processed_dt = None
            opened = 0
            body_ok = 0
            body_fail = 0
            probed = 0
            found_via_detail = 0
            external_hits = 0   # title+link only (external repost / document)

            def _record_hit(it, final_date, body, detail_title=""):
                nonlocal opened, body_ok, body_fail, external_hits
                opened += 1
                # External / document items are reported as title + link only:
                # we never fetched a body for them, and summarizing someone
                # else's page (or a PDF we can't parse) isn't the point — the
                # reader clicks through. Counted separately so the run summary
                # doesn't read them as body-fetch failures.
                if it.get("external"):
                    external_hits += 1
                    logger.info("      ✓ NEW(外部) [%s] %s  → %s",
                                final_date, it["title"][:56], it["url"][:60])
                    hits.append({
                        "site": name,
                        "title": it["title"],
                        "url": it["url"],
                        "publish_date": final_date,
                        "body_text": "",
                        "external": True,
                        "group": site.get("group", "competitor"),
                    })
                    return
                # Title rescue (故障B): some list cards expose only a placeholder
                # label ("Go to article details") as their text, so the hit's
                # list title is junk even though the URL is a real article. If so,
                # and the detail page gave us a real headline, use that instead.
                title = it["title"]
                if detail_title and _needs_title_rescue(title):
                    logger.info("      ↳ 标题抢救: %r → %r",
                                title[:40], detail_title[:60])
                    title = detail_title
                body_len = len((body or "").strip())
                if body_len:
                    body_ok += 1
                    logger.info("      ✓ NEW [%s] %s  (正文 %d 字)",
                                final_date, title[:60], body_len)
                else:
                    body_fail += 1
                    logger.warning("      ✓ NEW [%s] %s  (正文抓取为空)",
                                   final_date, title[:60])
                hits.append({
                    "site": name,
                    "title": title,
                    "url": it["url"],
                    "publish_date": final_date,
                    "body_text": body,
                    # Routing tag: "regulatory" sites (卫健委 …) get the
                    # regulatory-affairs prompt in run_monitor.
                    "group": site.get("group", "competitor"),
                })

            # (a) Items already known to be new from the list date: open for body.
            for item_dt, it in batch:
                if it.get("external"):
                    # Title + link only — don't drive the browser to someone
                    # else's site (or a PDF viewer) just to throw the body away.
                    _record_hit(it, it["list_date"], "")
                    if highest_processed_dt is None or item_dt > highest_processed_dt:
                        highest_processed_dt = item_dt
                    continue
                if api_mode:
                    article_date, body, detail_title = fetch_article_api(site, it)
                else:
                    article_date, body, detail_title = fetch_article(driver, it["url"])
                final_date = article_date or it["list_date"]
                _record_hit(it, final_date, body, detail_title)
                if highest_processed_dt is None or item_dt > highest_processed_dt:
                    highest_processed_dt = item_dt

            # (b) Undated-on-list items: open the detail page to read the date
            #     there, then decide freshness. Capped so a huge list can't cause
            #     hundreds of loads. Candidates are in list order (newest-first on
            #     most sites), so the cap keeps the most recent.
            skipped_undated_ext = 0
            for it in undated_items:
                if probed >= MAX_UNDATED_TO_PROBE:
                    logger.warning("    Reached undated-probe cap (%d); %d undated "
                                   "items left unchecked this run",
                                   MAX_UNDATED_TO_PROBE,
                                   len(undated_items) - probed)
                    break
                # Undated external items are DROPPED, not probed. Probing means
                # loading a third-party page (bot walls, paywalls, 10-30s each,
                # unreliable date markup) or a PDF (download prompt) purely to
                # read a date whose body we then throw away. Cheap to skip:
                # Zydus, the only allow_external site, dates 73/76 of its links on
                # the list page (verified 2026-08-05), so this loses ~3 items and
                # never lets an undated repost in as a false "new".
                if it.get("external"):
                    skipped_undated_ext += 1
                    continue
                probed += 1
                if api_mode:
                    article_date, body, detail_title = fetch_article_api(site, it)
                else:
                    article_date, body, detail_title = fetch_article(driver, it["url"])
                detail_dt = parse_dt(article_date)
                if detail_dt is None:
                    continue  # still no date even from the detail page — skip
                found_via_detail += 1
                if detail_dt > baseline_dt:
                    _record_hit(it, article_date, body, detail_title)
                    if highest_processed_dt is None or detail_dt > highest_processed_dt:
                        highest_processed_dt = detail_dt

            if probed:
                logger.info("    Probed %d undated items; got date from %d",
                            probed, found_via_detail)
            if skipped_undated_ext:
                logger.info("    Skipped %d undated external/document link(s) "
                            "(not worth loading a third-party page for a date)",
                            skipped_undated_ext)

            # Only advance the saved timestamp past what we actually processed.
            # Clamp to NOW: some lists carry FUTURE-dated entries (Döhler's
            # newsroom mixes trade-fair/event cards, e.g. "Food Tec Kolkata
            # 2026/12/15"). Saving that future date as the baseline would
            # silently mute every real article until the event date passes —
            # exactly what happened to Döhler (baseline stuck at 2026-12-15).
            if highest_processed_dt is not None:
                save_dt = min(highest_processed_dt, datetime.now())
                if save_dt != highest_processed_dt:
                    logger.warning("    ⚠️  最高文章时间 %s 在未来（活动/展会条目），"
                                   "时间戳按当前时刻封顶保存",
                                   highest_processed_dt.strftime(DATE_FMT))
                last_times[name] = save_dt.strftime(DATE_FMT)

            # Classify outcome for the summary.
            if opened == 0:
                # Nothing new. Distinguish "couldn't get any date" from "no updates".
                if undated_items and found_via_detail == 0 and probed > 0:
                    outcomes.append({"name": name, "status": "all_undated",
                                     "detail": f"{len(items)} 候选，列表无日期且详情页也读不到日期"
                                               f"（探测 {probed} 篇，需针对该站适配日期位置）"})
                else:
                    outcomes.append({"name": name, "status": "no_new",
                                     "detail": f"候选 {len(items)}，无更新（≤基线）"})
            elif body_ok == 0 and body_fail > 0:
                # Only a real failure if we actually TRIED to fetch bodies.
                # External/document hits are title+link by design, so a site whose
                # new items are all external must not be reported as broken.
                outcomes.append({"name": name, "status": "body_empty",
                                 "detail": f"打开 {body_fail} 篇，正文全部为空"
                                           "（正文容器非 article/main，需适配选择器）"
                                 + (f"；另有外部/文档链接 {external_hits} 条（仅标题+链接）"
                                    if external_hits else "")})
            else:
                detail_note = f"，其中经详情页补日期 {found_via_detail}" if found_via_detail else ""
                ext_note = (f"，外部/文档链接 {external_hits} 条（仅标题+链接）"
                            if external_hits else "")
                outcomes.append({"name": name, "status": "ok",
                                 "detail": f"新增 {opened} 篇，正文成功 {body_ok}"
                                           + (f"，空 {body_fail}" if body_fail else "")
                                           + ext_note + detail_note})
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        if display:
            try:
                display.stop()
            except Exception:
                pass

    # Don't persist timestamps during a test override run — otherwise a test
    # would overwrite the real per-site progress and corrupt production state.
    if BASELINE_OVERRIDE:
        logger.info("MONITOR_BASELINE override active — NOT saving timestamps "
                    "(test run, production state untouched).")
    else:
        save_last_times(last_times)
    _write_summary(summary_path, outcomes)
    logger.info("Done. %d new hits total. Full log: %s", len(hits), log_path)
    return hits


if __name__ == "__main__":
    results = fetch_all_new_hits()
    print(f"\n=== SUMMARY: {len(results)} new items ===")
    for h in results:
        print(f"  [{h['site']}] ({h['publish_date']}) {h['title']}\n     {h['url']}")
