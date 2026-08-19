"""Loading the monitored-site list and its pinned list-container selectors."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.services.crawler.common.constants import MONITOR_ONLY
from app.services.crawler.common.paths import LIST_SELECTORS_PATH, SITES_PATH

logger = logging.getLogger(__name__)

# Only these annotator statuses activate a pinned selector. "manual" means the
# page needs per-site work before the selector can be trusted, so those sites
# keep the generic whole-page path until they are promoted.
_ACTIVE_SELECTOR_STATUSES = ("ready", "fixed")

# Keys copied onto a site when its selector entry sets them.
_SELECTOR_OVERRIDES = ("list_selector", "wait_for", "allow_external", "exclude_paths")


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s, ignoring: %s", path.name, exc)
        return None


def load_list_selectors() -> dict[str, dict[str, Any]]:
    """Return {site_name: overrides} for sites whose news-list container is pinned.

    Those sites scrape the container directly; the light rejects still apply (see
    ``urls.light_reject``). Missing file or unpinned site -> not present.
    """
    config = _read_json(LIST_SELECTORS_PATH) or {}
    selectors: dict[str, dict[str, Any]] = {}
    for entry in config.get("sites", []):
        if not entry.get("list_selector"):
            continue
        status = (entry.get("status") or "ready").lower()
        if status not in _ACTIVE_SELECTOR_STATUSES:
            logger.info("list_selector for %s is status=%r — not activated", entry.get("name"), status)
            continue
        overrides: dict[str, Any] = {"list_selector": entry["list_selector"]}
        if entry.get("wait_for"):
            overrides["wait_for"] = entry["wait_for"]
        if entry.get("allow_external"):
            overrides["allow_external"] = True
        if entry.get("exclude_paths"):
            overrides["exclude_paths"] = [p.lower() for p in entry["exclude_paths"]]
        selectors[entry["name"]] = overrides
    return selectors


def load_sites() -> list[dict[str, Any]]:
    """Enabled sites from monitor_sites.json, with selector overrides merged in.

    Honours MONITOR_ONLY, so a test run touches only the named sites.
    """
    config = _read_json(SITES_PATH)
    if config is None:
        raise FileNotFoundError(f"Monitor site config not found: {SITES_PATH}")

    sites = [site for site in config.get("sites", []) if site.get("enabled", True)]
    selectors = load_list_selectors()
    for site in sites:
        overrides = selectors.get(site["name"])
        if overrides:
            site.update({k: v for k, v in overrides.items() if k in _SELECTOR_OVERRIDES})

    if MONITOR_ONLY:
        sites = [s for s in sites if any(part in s["name"].lower() for part in MONITOR_ONLY)]
    return sites
