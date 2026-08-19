"""Reading one article page: publish date, body text, and a rescue headline.

``parse_article`` is pure (HTML in, ``ArticleContent`` out); ``fetch_article``
adds the page load.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.services.crawler.fetch.browser import load_page
from app.services.crawler.common.constants import BODY_CHAR_LIMIT
from app.services.crawler.fetch.dates import extract_publish_date, find_date_in_text
from app.services.crawler.common.models import ArticleContent
from app.services.crawler.fetch.titles import extract_detail_title

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag
    from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger(__name__)

# Article-body containers in priority order; many sites use none of the semantic
# tags. ".w-richtext" is Webflow's rich-text element — without it the named
# selectors miss the body and a short "related articles" rail wins instead.
_CONTENT_SELECTORS = (
    "article", "main",
    ".article-body", ".article-content", ".article__body", ".article__content",
    ".news-detail", ".news-content", ".news__content", ".detail-content",
    ".post-content", ".entry-content", ".rich-text", ".rich_text",
    ".content-body", ".cmp-text", ".body-content", ".page-content",
    ".w-richtext",
    "[class*='article']", "[class*='content']", "[itemprop='articleBody']",
)

_NOISE_TAGS = ("script", "style", "nav", "header", "footer", "noscript", "aside")

# Cookie/consent widgets inject thousands of characters of boilerplate as plain
# <div>s, so the tag strip above misses them. Left in, they sit at the top of the
# body and crowd the real article out of the summarizer's window.
_CONSENT_SELECTORS = (
    "#CybotCookiebotDialog", "#onetrust-consent-sdk", "#onetrust-banner-sdk",
    "[id*='CookieConsent']", "[class*='cookie-consent']",
    "[class*='cookiebanner']", "[class*='CybotCookiebot']",
)

# A real junk form (search box, newsletter) is short. ASP.NET WebForms sites wrap
# the ENTIRE page — article included — in one <form>, so only small ones go.
_MAX_JUNK_FORM_LEN = 200
# Text a named selector must hold before we trust it over the prose fallback.
_MIN_NAMED_CONTAINER_LEN = 120
_MIN_PROSE_LEN = 200
# Regions scanned for a dateline when structured markup gave nothing.
_PAGE_TOP_CHARS = 600
_BODY_HEAD_CHARS = 400


def fetch_article(driver: WebDriver, url: str) -> ArticleContent:
    """Load an article and parse it; empty content if the page never loaded."""
    try:
        if not load_page(driver, url):
            return ArticleContent()
        return parse_article(driver.page_source)
    except Exception as exc:
        logger.warning("    Could not fetch article %s: %s", url, exc)
        return ArticleContent()


def parse_article(html: str) -> ArticleContent:
    """Publish date, body text and detail headline from one article's HTML."""
    from bs4 import BeautifulSoup

    # Structured markup first: meta tags, <time>, JSON-LD.
    publish_date = extract_publish_date(html)

    soup = BeautifulSoup(html, "html.parser")
    _strip_noise(soup)

    # Fall back BEFORE narrowing to the body: many sites put the date in a small
    # info bar that sits outside the article container.
    if not publish_date and soup.body:
        publish_date = find_date_in_text(
            soup.body.get_text(separator="\n", strip=True)[:_PAGE_TOP_CHARS]
        )

    # Narrow to the body for the SUMMARY text, kept separate from date extraction
    # so a clean body never costs us the date.
    text = _find_content_container(soup).get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if not publish_date and text:
        publish_date = find_date_in_text(text[:_BODY_HEAD_CHARS])

    # og:title / <title> live in <head> and the article <h1> in the body, so none
    # of them were part of the nav we just stripped.
    return ArticleContent(
        publish_date=publish_date,
        body_text=text[:BODY_CHAR_LIMIT],
        detail_title=extract_detail_title(soup),
    )


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()
    for tag in soup.find_all("form"):
        if len(tag.get_text(strip=True)) < _MAX_JUNK_FORM_LEN:
            tag.decompose()
    for selector in _CONSENT_SELECTORS:
        try:
            for tag in soup.select(selector):
                tag.decompose()
        except Exception:
            continue


def _find_content_container(soup: BeautifulSoup) -> Tag:
    """The element most likely to hold the article body.

    Among the known containers, the one with the most text wins (a real body
    dwarfs sidebars and teasers).
    """
    best: Tag | None = None
    best_len = 0
    for selector in _CONTENT_SELECTORS:
        try:
            matches = soup.select(selector)
        except Exception:
            continue
        for element in matches:
            # Never take the document root: the wildcard selectors can match
            # <html>/<body> when their class list merely contains the substring
            # (a Modernizr "generatedcontent" class does), and being the whole
            # document it would always win on length.
            if element.name in ("html", "body"):
                continue
            length = len(element.get_text(strip=True))
            if length > best_len:
                best, best_len = element, length

    if best is not None and best_len >= _MIN_NAMED_CONTAINER_LEN:
        return best

    # No known selector matched (a generic <div class="bd">, an .aspx layout).
    # Pick the most PROSE — text minus link text — so we land on the article
    # rather than a <body> that drags in the nav and breadcrumb link lists.
    prose_best: Tag | None = None
    prose_score = 0
    for element in soup.find_all(["article", "main", "section", "div"]):
        score = _prose_len(element)
        if score > prose_score:
            prose_best, prose_score = element, score
    if prose_best is not None and prose_score >= _MIN_PROSE_LEN:
        return prose_best

    return soup.body or soup


def _prose_len(element: Tag) -> int:
    total = len(element.get_text(" ", strip=True))
    link_text = sum(len(a.get_text(" ", strip=True)) for a in element.find_all("a"))
    return total - link_text
