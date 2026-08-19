"""Filesystem locations for crawler config and run artifacts.

Set MONITOR_DATA_DIR to keep config/ and logs/ outside the repo; the default
resolves to the repository root, matching the layout the scraper shipped with.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]

DATA_DIR = Path(os.getenv("MONITOR_DATA_DIR") or _REPO_ROOT)
CONFIG_DIR = DATA_DIR / "config"
LOG_DIR = DATA_DIR / "crawlers" /"logs"

LINKS_XLSX = CONFIG_DIR / "links.xlsx"
SITES_PATH = CONFIG_DIR / "monitor_sites.json"
LIST_SELECTORS_PATH = CONFIG_DIR / "list_selectors.json"
LAST_TIMES_PATH = CONFIG_DIR / "monitor_last_times.json"
