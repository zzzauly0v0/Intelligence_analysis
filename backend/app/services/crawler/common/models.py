"""Shapes passed between crawler stages.

``Hit`` stays a plain dict: it is the contract with the digest renderer and the
articles table (see ``persistence.save_hits_to_db``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# {site, title, url, publish_date, body_text, external, group}
Hit = dict[str, Any]
# (model_id, summary_text) — model_id is None when nothing was summarized.
Summary = tuple[str | None, str]

OutcomeStatus = Literal["ok", "no_new", "load_failed", "no_items", "all_undated", "body_empty"]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One link scraped off a news-list page, before freshness is decided."""

    title: str
    url: str
    # Date shown next to the item in the listing, normalized; None if unreadable.
    list_date: str | None = None
    # Third-party repost, video or document: carried as title + link, no body,
    # no AI summary.
    external: bool = False
    # json_api sites: the id the detail endpoint needs.
    api_id: Any = None


@dataclass(frozen=True, slots=True)
class ArticleContent:
    """What a detail page yielded."""

    publish_date: str | None = None
    body_text: str = ""
    # Headline read off the detail page, used to rescue a junk list title.
    detail_title: str = ""


@dataclass(frozen=True, slots=True)
class Outcome:
    """Per-site result, rendered into the run summary."""

    name: str
    status: OutcomeStatus
    detail: str


@dataclass(frozen=True, slots=True)
class ExtractRules:
    """Per-site extraction switches, read from the site's config entry."""

    news_url: str
    # Path fragments that force-keep a link our heuristics would drop.
    allow_paths: tuple[str, ...] = ()
    # Require a /YYYY/ path segment — the opposite of allow_paths, for sites
    # whose real articles are all dated while their section indexes aren't.
    require_year: bool = False
    # Drop headlines that are clearly a Romance-language translation (IFF).
    english_only: bool = False
    # Pinned news-list container; when set, the whole-page heuristics are skipped.
    list_selector: str | None = None
    # Keep cross-domain links: some newsrooms ARE a press-coverage wall (Zydus).
    allow_external: bool = False
    # Path substrings to drop (dsm files share-buy-back notices as news).
    exclude_paths: tuple[str, ...] = ()

    @classmethod
    def from_site(cls, site: dict[str, Any]) -> ExtractRules:
        return cls(
            news_url=site["news_url"],
            allow_paths=tuple(site.get("allow_paths") or ()),
            require_year=bool(site.get("require_year", False)),
            english_only=bool(site.get("english_only", False)),
            list_selector=site.get("list_selector"),
            allow_external=bool(site.get("allow_external", False)),
            exclude_paths=tuple(p.lower() for p in (site.get("exclude_paths") or ())),
        )
