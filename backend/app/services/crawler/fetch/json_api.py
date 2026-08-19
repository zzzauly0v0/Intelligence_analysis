"""Sites that render nothing server-side but expose a clean JSON feed.

Everything is driven by the site's "api" config block, so a new SPA needs a
config entry, not code. urllib keeps this dependency-free.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from app.services.crawler.common.constants import BODY_CHAR_LIMIT
from app.services.crawler.fetch.dates import normalize_date
from app.services.crawler.common.models import ArticleContent, Candidate

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def is_api_site(site: dict[str, Any]) -> bool:
    return site.get("fetch_mode") == "json_api"


def _dig(obj: Any, dotted_path: str) -> Any:
    """Walk a dotted key path (e.g. 'data.data') into nested JSON."""
    current = obj
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _request_json(url: str, data: dict[str, Any] | None = None) -> Any | None:
    """GET, or POST form-encoded when `data` is given. None on any failure."""
    body = None
    headers = dict(_HEADERS)
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        request = urllib.request.Request(  # noqa: S310 - URLs come from our own config
            url, data=body, headers=headers, method="POST" if data is not None else "GET"
        )
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("    API request failed (%s): %s", url, exc)
        return None


def list_candidates(site: dict[str, Any]) -> list[Candidate]:
    """The same candidates ``extract.parse_list_page`` builds, from a list API.

    Returns [] on any failure so the caller degrades gracefully.
    """
    api = site.get("api", {})
    payload = _request_json(api["list_url"])
    if payload is None:
        return []
    rows = _dig(payload, api.get("list_items_path", "data")) or []
    if not isinstance(rows, list):
        return []

    title_key = api.get("list_title_key", "title")
    date_key = api.get("list_date_key", "day")
    id_key = api.get("list_id_key", "id")
    url_template = api.get("article_url_template", "")

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (row.get(title_key) or "").strip()
        if not title:
            continue
        row_id = row.get(id_key)
        candidates.append(
            Candidate(
                title=title,
                url=url_template.format(id=row_id) if url_template and row_id is not None else api["list_url"],
                list_date=normalize_date(str(row.get(date_key) or "")),
                api_id=row_id,
            )
        )
    return candidates


def fetch_article_api(site: dict[str, Any], candidate: Candidate) -> ArticleContent:
    """One article's body via the site's detail API.

    The date falls back to the list date. detail_title stays empty: API sites hand
    us a clean list title, so there is nothing to rescue.
    """
    api = site.get("api", {})
    detail_url = api.get("detail_url")
    if not detail_url or candidate.api_id is None:
        return ArticleContent(publish_date=candidate.list_date)

    payload = _request_json(detail_url, data={api.get("detail_id_param", "id"): candidate.api_id})
    if payload is None:
        return ArticleContent(publish_date=candidate.list_date)

    content_html = _dig(payload, api.get("detail_content_path", "data.content")) or ""
    body = _html_to_text(content_html) if content_html else ""

    date_key = api.get("list_date_key", "day")
    detail_date = _dig(payload, f"data.{date_key}")
    publish_date = normalize_date(str(detail_date)) if detail_date else None
    return ArticleContent(publish_date=publish_date or candidate.list_date, body_text=body)


def _html_to_text(content_html: str) -> str:
    from bs4 import BeautifulSoup

    text = BeautifulSoup(content_html, "html.parser").get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)[:BODY_CHAR_LIMIT]
