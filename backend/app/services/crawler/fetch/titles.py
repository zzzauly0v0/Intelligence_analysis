"""Headline extraction from list cards and detail pages.

Modern card layouts rarely put the headline in the <a> text — it may be an
aria-label, an <img alt>, or a sibling heading — so ``best_title`` walks a
priority chain, and ``needs_title_rescue`` marks the cases where only the detail
page can supply a real headline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.crawler.common.constants import MIN_TITLE_LEN

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag

# Call-to-action and section-nav labels that are never article headlines. When
# an anchor's text is only one of these, best_title keeps looking.
_PLACEHOLDER_TITLES = frozenset({
    "read more", "read more.", "learn more", "find out more", "see more",
    "view more", "more", "details", "view details", "continue reading",
    "阅读更多", "查看详情", "查看更多", "了解更多", "详情", "更多", "点击查看",
    "explore corporate news", "view all news", "all news", "company news",
    "corporate news", "latest news", "news", "newsroom", "subscribe to news",
    "subscribe", "other investor news", "investor news", "corporate presentations",
    "publications", "visit media", "trade media", "social media", "company",
    "ad hoc announcements", "add to calendar", "media", "media releases",
    "media center", "media centre", "press releases", "press release",
    "all news & stories", "news & events", "news and events", "news & media",
    "news and media", "our news", "back to news", "see all", "view all",
})

# Accessibility labels long enough to pass the length gate but still not a
# headline. Kept apart from _PLACEHOLDER_TITLES because these must NOT drop the
# link: the URL is a real article, only the title needs rescuing.
_RESCUE_LIST_TITLES = frozenset({
    "go to article details", "go to details", "go to article",
    "view article", "read article", "article details", "view story",
    "go to story", "read the article", "read full article",
    "read this news", "read news",
})

# A card that glues a kicker + dateline onto the headline, e.g.
# "NEWS HIGHLIGHTS Jul. 16, 2026 <headline> Read More >". There is no clean
# delimiter to carve on, so such titles go to detail-page rescue instead.
# Requiring a KNOWN kicker followed by a date keeps this off real headlines.
_KICKER_DATE_RE = re.compile(
    r"^\s*(?:news highlights|press releases?|in the media|in the press|in the news)\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+"
    r"\d{1,2},?\s+\d{4}\b",
    re.I,
)

_FILENAME_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|svg|webp|bmp|ico|pdf|zip|docx?|xlsx?|pptx?|mp4|mov|avi|mp3|css|js)$",
    re.I,
)

# Class names marking a card's headline when it is a <div>/<span> rather than an
# <h1>-<h4>. Anchored to word-ish boundaries so "subtitle-note" can't match.
_TITLE_CLASS_RE = re.compile(
    r"(^|[-_ ])(title|headline|heading|card-?title|news-?title|标题)([-_ ]|$)", re.I
)

# Leading CTA some sites prepend when building an accessible label, e.g.
# "Read more about <headline>". Stripping it recovers the headline instead of
# discarding the link. A bare "Read More" needs the about/":"/"-" continuation to
# match here, so it is still treated as a placeholder.
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

# A dateline some cards append inside the same <a> ("<headline> Jul. 24, 2026").
# Only the abbreviated-with-period form is stripped: a real headline never ends
# in "<Mon>. DD, YYYY", while one that legitimately ends in a date writes the
# month out in full ("… Results on August 4, 2026") and is left alone.
_TRAILING_DATELINE_RE = re.compile(
    r"\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\.\s+\d{1,2},?\s+\d{4}\s*$",
    re.I,
)

# Romance function words + accented letters, used only by the per-site
# english_only switch. IFF publishes Spanish/Portuguese translations under an
# English path and <html lang="en-US">, so the title text is the only signal.
# Requiring BOTH an accent and >=2 stopwords keeps English headlines safe.
_ROMANCE_ACCENT_RE = re.compile(r"[à-ü]", re.I)
_ROMANCE_STOPWORDS = frozenset({
    "de", "la", "el", "los", "las", "en", "más", "mas", "para", "con", "una",
    "del", "por", "y", "e", "da", "do", "das", "dos", "na", "no", "mais",
    "produção", "eficiente", "consistencia", "consistência", "estabilidad",
})

_HEADING_TAGS = ["h1", "h2", "h3", "h4"]
_HIDDEN_CLASS_RE = re.compile(r"(?:^|[-_ ])(hide|hidden|sr-only|visually-hidden|screen-reader)")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_placeholder_title(text: str | None) -> bool:
    """True for generic nav / CTA text rather than a headline.

    Normalizes first — drops a trailing parenthetical and any " - …" suffix — so
    ADM's "Read More (link opens in a new window)" and "Read More - Read more
    about News" are both recognized.
    """
    core = re.sub(r"\s*\([^)]*\)\s*", " ", (text or "").lower())
    core = re.split(r"\s+-\s+", core, maxsplit=1)[0].strip()
    if core in _PLACEHOLDER_TITLES:
        return True
    return core.startswith(("read more", "learn more", "阅读更多", "查看详情"))


def looks_like_filename(text: str | None) -> bool:
    """True for an asset filename posing as a headline ("hero_banner_2.png")."""
    if not text:
        return False
    return bool(_FILENAME_EXT_RE.search(text.strip()))


def looks_non_english(text: str | None) -> bool:
    """True if a headline is clearly a Romance-language translation (see above)."""
    if not text or not _ROMANCE_ACCENT_RE.search(text):
        return False
    words = set(re.findall(r"[a-záàâãéêíóôõúüñç]+", text.lower()))
    return sum(1 for w in words if w in _ROMANCE_STOPWORDS) >= 2


def strip_cta_prefix(text: str) -> str:
    """"Read more about <headline>" -> "<headline>"."""
    return _CTA_PREFIX_RE.sub("", text).strip() if text else text


def strip_trailing_dateline(text: str) -> str:
    """Drop a "Mon. DD, YYYY" dateline glued to the end of a headline."""
    return _TRAILING_DATELINE_RE.sub("", text).strip() if text else text


def needs_title_rescue(title: str | None) -> bool:
    """Whether a list-derived title should be replaced by a detail-page headline.

    Conservative: a real headline matches none of these, so the rescue only ever
    fires on genuinely bad titles.
    """
    normalized = _clean(title).lower()
    if len(normalized) < MIN_TITLE_LEN:
        return True
    if normalized in _RESCUE_LIST_TITLES or is_placeholder_title(normalized):
        return True
    if _KICKER_DATE_RE.match(title or ""):
        return True
    return normalized.startswith(("go to article", "go to story"))


def _usable(text: str | None) -> str | None:
    """A real headline from `text`, or None so the caller tries the next source."""
    candidate = strip_trailing_dateline(strip_cta_prefix(_clean(text)))
    if (
        len(candidate) >= MIN_TITLE_LEN
        and not is_placeholder_title(candidate)
        and not looks_like_filename(candidate)
    ):
        return candidate
    return None


def _distinct_link_count(node: Tag) -> int:
    """Distinct link destinations inside a container.

    One article card points image, title and "read more" at the SAME url, so its
    count is 1; a shared list wrapper or a pagination block has several.
    """
    hrefs = set()
    for link in node.find_all("a"):
        href = (link.get("href") or "").split("#")[0]
        if href and not href.startswith(("javascript:", "mailto:", "tel:")):
            hrefs.add(href)
    return len(hrefs)


def best_title(anchor: Tag) -> str:
    """Best-effort headline for a list-page <a>, or "" if none was found.

    Returning "" rather than the raw anchor text is deliberate: step 3 already
    tested that text and rejected it, so handing it back would re-admit the
    placeholder we just refused. The caller's length gate then drops the link.
    """
    # 1) An explicit title attribute.
    found = _usable(anchor.get("title"))
    if found:
        return found

    # 2) A heading inside the anchor. Some layouts wrap the WHOLE card (image,
    #    headline, date, teaser) in one <a>, so its full text is a blob.
    inner = anchor.find(_HEADING_TAGS) or anchor.find(attrs={"class": _TITLE_CLASS_RE})
    if inner is not None:
        found = _usable(inner.get_text(" ", strip=True))
        if found:
            return found

    # 3) The anchor's own text, 4) its accessible label, 5) an inner image alt.
    for text in (
        anchor.get_text(" ", strip=True),
        anchor.get("aria-label"),
        anchor.find("img").get("alt") if anchor.find("img") is not None else None,
    ):
        found = _usable(text)
        if found:
            return found

    # 6) A heading in the enclosing <article>. Some cards make the linking anchor
    #    an empty overlay with the title in a sibling <h3>. <article> is the
    #    semantic for ONE item, so we do not stop on a multi-link container here
    #    (a decorative category-icon link would otherwise break the climb below).
    #    Visually-hidden headings are icon labels, not headlines.
    card = anchor.find_parent("article")
    if card is not None:
        for heading in card.find_all(_HEADING_TAGS):
            if _HIDDEN_CLASS_RE.search(" ".join(heading.get("class", [])).lower()):
                continue
            found = _usable(heading.get_text(" ", strip=True))
            if found:
                return found

    # 7) Climb a few ancestors: the immediate parent often wraps only the "Read
    #    more" button while the title sits in a sibling under a higher wrapper.
    #    Stop once a container links to more than one destination — we have left
    #    this anchor's card, and any heading there belongs to a sibling.
    node: Tag | None = anchor
    for _ in range(4):
        node = node.parent
        if node is None or getattr(node, "name", None) is None:
            break
        if _distinct_link_count(node) > 1:
            break
        near = node.find(_HEADING_TAGS) or node.find(attrs={"class": _TITLE_CLASS_RE})
        if near is not None:
            found = _usable(near.get_text(" ", strip=True))
            if found:
                return found

    return ""


def extract_detail_title(soup: BeautifulSoup) -> str:
    """Headline from a parsed DETAIL page, used to rescue a junk list title.

    Sources by reliability: og:/twitter:title, the first <h1>, then <title> with
    a trailing " | Brand" trimmed. Additive — a miss changes nothing, since the
    caller only replaces a title already judged junk.
    """
    try:
        for attrs in (
            {"property": "og:title"},
            {"name": "og:title"},
            {"name": "twitter:title"},
            {"property": "twitter:title"},
        ):
            meta = soup.find("meta", attrs=attrs)
            if meta:
                found = _good_detail_title(meta.get("content"))
                if found:
                    return found

        h1 = soup.find("h1")
        if h1 is not None:
            found = _good_detail_title(h1.get_text(" ", strip=True))
            if found:
                return found

        if soup.title:
            raw = _clean(soup.title.get_text(" ", strip=True))
            trimmed = re.split(r"\s+[|–—-]\s+", raw)[0].strip()
            return _good_detail_title(trimmed) or _good_detail_title(raw)
    except Exception:
        pass
    return ""


def _good_detail_title(text: str | None) -> str:
    cleaned = _clean(text)
    if len(cleaned) >= MIN_TITLE_LEN and not is_placeholder_title(cleaned) and not looks_like_filename(cleaned):
        return cleaned
    return ""
