"""Crawler package facade."""

from app.services.crawler.delivery.cli import main, summarize_hits
from app.services.crawler.common.constants import BASELINE_OVERRIDE
from app.services.crawler.delivery.digest import (
    DIGEST_KINDS,
    build_digest_body,
    build_digest_html,
    format_baseline_label,
)
from app.services.crawler.common.models import Candidate, Hit, Outcome, Summary
from app.services.crawler.orchestration.pipeline import (
    MonitorRun,
    SiteScan,
    fetch_all_new_hits,
    scan_all_sites,
    scan_site,
)
from app.services.crawler.config.sites import load_sites
from app.services.crawler.config.state import load_last_times, save_last_times

__all__ = [
    "BASELINE_OVERRIDE",
    "DIGEST_KINDS",
    "Candidate",
    "Hit",
    "MonitorRun",
    "Outcome",
    "SiteScan",
    "Summary",
    "build_digest_body",
    "build_digest_html",
    "fetch_all_new_hits",
    "format_baseline_label",
    "load_last_times",
    "load_sites",
    "main",
    "save_hits_to_db",
    "save_last_times",
    "scan_all_sites",
    "scan_site",
    "summarize_hits",
]

def __getattr__(name: str):
    if name == "save_hits_to_db":
        from app.services.crawler.delivery.persistence import save_hits_to_db
        return save_hits_to_db
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
