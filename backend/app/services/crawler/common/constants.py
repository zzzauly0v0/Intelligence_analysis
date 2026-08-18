"""Tuning knobs and env-driven overrides for a monitor run."""

from __future__ import annotations

import os

# Baseline for a site we have never recorded a timestamp for.
DEFAULT_LAST_TIME = "2025-08-01 00:00:00"

# Anchor text shorter than this is nav chrome, not a headline.
MIN_TITLE_LEN = 8
# Candidate links considered per list page.
MAX_ITEMS_PER_PAGE = 200
# New items opened (body fetch) per site per run. On overflow the OLDEST are
# processed and the saved timestamp only advances past those, so the remainder
# is picked up next run instead of being lost.
MAX_NEW_ITEMS_TO_OPEN = 20
# Undated candidates whose detail page we open just to read a date. Capped so a
# 200-link page can't trigger 200 loads; candidates come in list order
# (newest-first on most sites), so the cap keeps the most recent.
MAX_UNDATED_TO_PROBE = 25
# Body text handed to the summarizer.
BODY_CHAR_LIMIT = 8000

# Page-load retries; each retry waits PAGE_LOAD_BACKOFF * attempt seconds, so a
# single network hiccup doesn't drop a whole site for the run.
PAGE_LOAD_RETRIES = 3
PAGE_LOAD_BACKOFF = 8
# Wait for this many <a> elements before scraping a list page — JS-rendered
# lists are often absent at readyState=complete.
MIN_ANCHORS_ON_NEWS_PAGE = 5

# TEST-ONLY: one baseline for EVERY site this run (e.g. "2026-07-20 00:00:00").
# A recent value keeps few articles "new", so a test run finishes fast.
# Timestamps are not persisted while this is set, so production state is safe.
BASELINE_OVERRIDE = os.getenv("MONITOR_BASELINE", "").strip() or None

# TEST-ONLY: comma-separated site names to scan, matched case-insensitively as
# substrings ("Manus" matches "Manus Bio"). Unset scans every enabled site.
MONITOR_ONLY = [
    part.strip().lower() for part in os.getenv("MONITOR_ONLY", "").split(",") if part.strip()
] or None
