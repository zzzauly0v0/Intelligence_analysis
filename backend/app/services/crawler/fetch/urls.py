"""URL classification: is this link a real article, or site chrome?

Keeping junk links out of the candidate list is what stops nav bars, footers and
section landing pages from being reported as news.

Two entry points, for the two extraction paths:
  * ``is_probable_article`` — whole-page scraping, where nothing is trusted.
  * ``light_reject`` — a pinned list container, which already excludes
    nav/footer/sidebar, so only the rejects a shared card template can't hide
    still apply.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.services.crawler.common.models import ExtractRules

# A /YYYY/ path segment, i.e. a date-style article URL.
_YEAR_SEG_RE = re.compile(r"/20\d{2}(/|$)")

# Article-detail markers in a path. "detail"/"article" are unambiguous, so a
# substring match is safe; the rest are only trusted as a whole segment, so
# "review" or "download" can't trip them.
_ARTICLE_PATH_RE = re.compile(r"(detail|article)|(^|/)(show|content|view|post|artikel)(/|$)", re.I)

# Pagination inside a pinned container is list chrome, not an article.
_PAGINATION_RE = re.compile(r"([?&]page=\d+|/page/\d+/?$|[?&]p=\d+$)", re.I)

_ASSET_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|bmp|ico|pdf|zip|docx?|xlsx?|pptx?|mp4|mov|avi|mp3|css|js)$",
    re.I,
)
# Page furniture and non-readable media. .ics matters for Döhler, which pairs
# every trade-fair card with an "Add to calendar" link carrying a FUTURE date.
_FURNITURE_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|bmp|ico|css|js|mp4|mov|avi|mp3|ics|zip|rar|7z)$", re.I
)
# Documents a reader can open. Real content when a site publishes press coverage
# as files rather than pages (Zydus links newspaper-clipping PDFs).
_DOCUMENT_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)$", re.I)

_PAGE_EXT_RE = re.compile(r"\.(html?|aspx|php|jsp|shtml)$", re.I)

# Slugs that look article-ish (they carry a hyphen) but are sibling nav pages of
# the news list itself.
_SECTION_SLUGS = frozenset({
    "news", "latest-news", "company-news", "corporate-news", "news-events",
    "newsroom", "news-media", "news-and-media", "media-center", "media-centre",
    "media-releases", "media", "trade-media", "social-media", "press-releases",
    "press-release", "investor-news", "other-investor-news", "all-news",
    "view-all-news", "ad-hoc-announcements", "corporate-presentations",
    "publications", "add-to-calendar", "subscribe-to-news",
    # Cargill newsroom sub-sections; its real articles live at /YYYY/<slug>.
    "cargill-stories", "our-stories", "in-the-news", "sign-up-for-news",
    "media-contacts", "news-media-assets", "media-resources", "media-resource",
})

# Path segments naming a non-news area. If ANY segment matches, the link is site
# chrome — this is what keeps a news page's own header/footer nav from flooding
# in as fake articles. Matched by segment EQUALITY so an article slug like
# "sweetener-solutions-for-bakery" isn't hit by the "solutions" entry.
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

# Language codes used as a first path segment for a localized mirror of the same
# page. "en" is excluded — it is the default we scrape.
_LANG_CODES = frozenset({
    "es", "pt", "fr", "de", "it", "nl", "pl", "ru", "tr", "cn", "zh", "ja",
    "jp", "ko", "kr", "el", "ar", "cs", "da", "fi", "no", "sv", "uk", "ro",
    "hu", "bg", "hr", "sk", "sl", "et", "lv", "lt",
})

# Multi-label public suffixes among our sites, so the registrable-domain
# comparison keeps the right number of labels (morita-kagaku-kogyo.co.jp is one
# domain, not "co.jp"). A reject-side heuristic, not a full PSL: erring toward
# "same domain" only ever keeps a link.
_MULTI_LABEL_TLDS = frozenset({
    "co.jp", "com.cn", "co.uk", "com.au", "co.nz", "co.in", "com.br", "com.mx",
    "com.sg", "com.hk", "com.tw", "co.kr", "co.za", "com.tr", "org.uk",
})


def normalize_url(url: str | None) -> str:
    """Strip fragment and trailing slash so the same item is always one URL."""
    if not url:
        return ""
    url = url.split("#")[0].strip()
    if len(url) > 1 and url.endswith("/"):
        url = url[:-1]
    return url


def registrable_domain(netloc: str | None) -> str:
    """netloc -> eTLD+1, e.g. our-company.dsm-firmenich.com -> dsm-firmenich.com.

    Lets the cross-domain checks reject genuinely third-party links while keeping
    a company's own subdomains.
    """
    host = (netloc or "").lower().split(":", 1)[0].strip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _bare_host(netloc: str | None) -> str:
    """Host without port or "www.", for same-site comparison."""
    return (netloc or "").lower().split(":", 1)[0].removeprefix("www.")


def is_external_item(url: str, news_url: str) -> bool:
    """True if the link leaves the monitored site (different registrable domain)."""
    netloc = urlparse(url).netloc
    if not netloc:
        return False
    return registrable_domain(netloc) != registrable_domain(urlparse(news_url).netloc)


def is_document_url(url: str) -> bool:
    """True for PDF/Office files: readable, but no HTML body to scrape."""
    return bool(_DOCUMENT_EXT_RE.search(_last_segment(urlparse(url).path.lower())))


def _last_segment(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _strip_page_ext(segment: str) -> str:
    return _PAGE_EXT_RE.sub("", segment)


def _canonical_path(path: str) -> str:
    return path.rstrip("/") or "/"


def _news_section(news_path: str) -> str:
    """The section ROOT a real article must live under.

    A trailing pure-number file ("/news/0.html") is a paginated list, so the
    parent dir is the section; a trailing descriptive file ("/en/news-media.html")
    becomes that name without its extension; anything else is used as-is.
    """
    path = news_path.rstrip("/")
    if not path:
        return ""
    last = path.rsplit("/", 1)[-1]
    if re.match(r"^\d+\.\w+$", last):
        return path.rsplit("/", 1)[0] or "/"
    if _PAGE_EXT_RE.search(last):
        return _PAGE_EXT_RE.sub("", path)
    return path


def is_probable_article(url: str, rules: ExtractRules) -> bool:
    """Whole-page path: keep only links that look like real articles.

    Rejects run first (assets, the list page itself, cross-domain reposts,
    non-news areas, language mirrors). A survivor is then KEPT if any of:
      1. its path matches one of the site's allow_paths;
      2. it lives below the news section root;
      3. its path has a /YYYY/ segment;
      4. its path contains "news" plus a real article slug;
      5. it is a same-host file with a long descriptive slug;
      6. its path carries an article-detail marker (/detail/, /article/, …).
    """
    item = urlparse(url)
    item_path = item.path.lower()
    if not item_path or item_path == "/":
        return False

    news = urlparse(rules.news_url)
    news_path = news.path.lower()

    # Assets: list pages often link a thumbnail whose href is the image itself.
    if _ASSET_EXT_RE.search(_last_segment(item_path)):
        return False

    # A link back to the list page is navigation ("View all news").
    if not item.query and _canonical_path(item_path) == _canonical_path(news_path):
        return False

    # Cross-domain repost ("In The News" pointing at forbes.com). Guarded on
    # netloc so a relative same-site href is never rejected.
    if item.netloc and registrable_domain(item.netloc) != registrable_domain(news.netloc):
        return False

    # 1. Per-site allow list wins, ahead of the generic rejects below.
    for fragment in rules.allow_paths:
        if fragment and fragment.lower() in item_path:
            return True

    # Per-site "articles must be dated" gate: checked after allow_paths so a
    # whitelist can still force-keep, but before the KEEP rules so an undated
    # section index can't sneak through rule 4.
    if rules.require_year and not _YEAR_SEG_RE.search(item_path):
        return False

    item_segments = [s for s in item_path.split("/") if s]
    news_segments = [s for s in news_path.split("/") if s]

    for segment in item_segments:
        slug = _strip_page_ext(segment)
        if slug in _NON_NEWS_SEGMENTS or slug.startswith("about-"):
            return False

    # A localized mirror of the same page — same content, another language.
    # Country roots like "sg" aren't language codes, so /sg/en/… is unaffected.
    if item_segments:
        first = item_segments[0]
        if first in _LANG_CODES and first != (news_segments[0] if news_segments else None):
            return False

    # 2. Below the news SECTION root. Using the section rather than the parent
    #    directory is what stops a shallow news_url like /en/news-media.html from
    #    treating the whole /en/ tree as "same section".
    section = _news_section(news_path)
    if section and section != "/" and item_path.startswith(section + "/"):
        remainder = item_path[len(section):].strip("/")
        slug = _strip_page_ext(remainder)
        # Keep on a further path segment (the article slug), a query identifying
        # the item, or a pure numeric ID file (Adorvia's /news/80.html). Known
        # section names are dropped even though they carry a hyphen.
        if slug not in _SECTION_SLUGS and (
            item.query
            or "/" in remainder
            or re.fullmatch(r"\d+", slug)
            or (slug.count("-") + slug.count("_") >= 1)
        ):
            return True

    # 3. Date-style article path.
    if _YEAR_SEG_RE.search(item_path):
        return True

    # 4. "news" in the path plus a descriptive last segment (a bare /news or
    #    /news-events/news is nav).
    if "news" in item_path:
        tail = _strip_page_ext(_last_segment(item_path.rstrip("/")))
        if tail not in _SECTION_SLUGS and (
            tail.count("-") + tail.count("_") >= 1 or item.query or _YEAR_SEG_RE.search(item_path)
        ):
            return True

    # 5. Same-host file with a LONG descriptive slug, for sites whose articles sit
    #    at the site root. A query string does NOT qualify: same-host .aspx?query
    #    category pages are not articles, and the genuine query-identified ones
    #    are already kept by rules 4 and 6.
    #    Extensionless permalinks need a higher bar than files (>=6 vs >=3
    #    separators): with no CMS extension to lean on, a bare segment is more
    #    likely nav, and real root-level headlines run 8-20 separators while the
    #    nav pages they sit next to run <=5.
    if _bare_host(item.netloc) == _bare_host(news.netloc):
        last = _last_segment(item_path.rstrip("/"))
        min_separators = 3 if _PAGE_EXT_RE.search(last) else 6
        slug = _strip_page_ext(last)
        if slug.count("-") + slug.count("_") >= min_separators:
            return True

    # 6. CMS detail routes with no year and no "news" token (/new_cn_detail/id/14
    #    .html, /article/123). Only ADDS matches, so the worst case is one stray
    #    link opened once and skipped for lack of a date.
    return bool(_ARTICLE_PATH_RE.search(item_path))


def light_reject(url: str, rules: ExtractRules) -> str | None:
    """Rejects that a pinned list container does NOT make redundant.

    Verified against real container contents: cross-domain reposts sit in these
    lists as first-class cards, alongside calendar/asset files, links back to the
    list page, pagination, and undated section indexes.

    Returns a reason string, or None to keep the link.
    """
    item = urlparse(url)
    item_path = item.path.lower()
    news = urlparse(rules.news_url)
    last_segment = _last_segment(item_path)

    # Non-readable files are always out; readable documents fall through.
    if _FURNITURE_EXT_RE.search(last_segment):
        return "asset"

    if not item.query and _canonical_path(item_path) == _canonical_path(news.path.lower()):
        return "self"
    if _PAGINATION_RE.search(url):
        return "pagination"

    # The one signal a shared card template can't hide. allow_external keeps
    # these: some newsrooms ARE a press-coverage wall.
    if (
        item.netloc
        and not rules.allow_external
        and registrable_domain(item.netloc) != registrable_domain(news.netloc)
    ):
        return "cross-domain"

    # Per-site dated-articles gate — the undated section indexes it targets live
    # INSIDE the container. Skipped for documents, whose URLs are filenames and
    # never carry a /YYYY/ segment.
    if (
        rules.require_year
        and not _YEAR_SEG_RE.search(item_path)
        and not _DOCUMENT_EXT_RE.search(last_segment)
    ):
        return "no-year"

    # Per-site path blocklist: some newsrooms file routine non-news under their
    # own category and mix it into the list (dsm tags weekly share-buy-back
    # filings as press releases).
    if any(fragment in item_path for fragment in rules.exclude_paths):
        return "excluded-path"

    return None
