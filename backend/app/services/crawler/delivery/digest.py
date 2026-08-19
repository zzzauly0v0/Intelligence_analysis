"""Rendering the digest emails: plain-text and styled HTML.

Pure functions — hits and summaries in, strings out. Nothing here scrapes,
summarizes or sends, so a layout change is testable on fixed input.

Styles are inline because email clients drop <style> blocks.
"""

from __future__ import annotations

import html
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.services.crawler.common.constants import BASELINE_OVERRIDE
from app.services.crawler.common.models import Hit, Summary

INK = "#12303a"  # deep teal-ink — headers
ACCENT = "#0f8a7e"  # teal accent — company bars, links
MUTED = "#5b6b70"  # secondary text
LINE = "#e2e8ea"  # hairline borders
CARD_BG = "#ffffff"
PAGE_BG = "#f4f6f6"
DATE_BG = "#eef4f3"

_FONT_STACK = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "'PingFang SC','Microsoft YaHei',Roboto,Helvetica,Arial,sans-serif"
)

# The two digests differ only in wording: competitor news reads as "which company
# did what", regulatory news as "which authority published what". One renderer
# plus a label pack means a layout fix reaches both emails.
DIGEST_KINDS: dict[str, dict[str, str]] = {
    "competitor": {
        "emoji": "📡",
        "title": "竞品情报监测",
        "unit": "家企业",
        "subject": "竞品情报监测-{n}条新消息",
        "footer": "此邮件由竞品情报自动监测系统生成。",
    },
    "regulatory": {
        "emoji": "📜",
        "title": "法规动态监测",
        "unit": "个来源",
        "subject": "法规动态监测-{n}条新公告",
        "footer": "此邮件由法规动态自动监测系统生成。",
    },
}

_VIDEO_HOST_RE = re.compile(r"(youtube\.com|youtu\.be|vimeo\.com|bilibili\.com)", re.I)
_DOC_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)($|\?)", re.I)
_SECTION_LABEL_RE = re.compile(r"【(核心|要点|影响|摘要|简介)】")


def subject_for(kind: str, count: int) -> str:
    return DIGEST_KINDS[kind]["subject"].format(n=count)


def md_to_html(text: str | None) -> str:
    """The lightweight Markdown the model emits, as safe inline HTML.

    Escaped first, so article text can never inject markup.
    """
    if not text:
        return ""
    esc = html.escape(text.strip())
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"__(.+?)__", r"<strong>\1</strong>", esc)
    esc = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", esc)
    esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
    esc = _SECTION_LABEL_RE.sub(_section_badge, esc)
    return "<br>".join(
        re.sub(r"^[-*•]\s+", "• ", line.strip()) for line in esc.split("\n")
    )


def _section_badge(match: re.Match[str]) -> str:
    """The model's 【核心】/【要点】/【影响】 labels, as small colored badges."""
    return (
        f'<span style="display:inline-block;font-size:12px;font-weight:700;'
        f"color:{ACCENT};background:{DATE_BG};border-radius:4px;"
        f'padding:1px 7px;margin:2px 0;">{match.group(1)}</span> '
    )


def display_date(publish_date: str | None) -> str:
    """Day precision only: the stored HH:MM:SS is CMS noise to a reader."""
    if not publish_date:
        return "未知"
    return str(publish_date).split(" ")[0]


def external_note(hit: Hit) -> str:
    """What kind of off-site item this is, so the reader knows what to expect.

    Some newsrooms publish third-party coverage as their news: those items live on
    another site, in a video player or in a scanned PDF, so there is no body to
    scrape and no point summarizing someone else's page.
    """
    url = hit.get("url", "")
    if _VIDEO_HOST_RE.search(url):
        return "视频"
    if _DOC_EXT_RE.search(url):
        return "PDF/文档"
    host = re.sub(r"^www\.", "", urlparse(url).netloc)
    return f"外部媒体报道{f'·{host}' if host else ''}"


def format_baseline_label(baselines: Mapping[str, str]) -> str:
    """The "articles newer than X" line, from the baselines the run compared against.

    A test run (MONITOR_BASELINE) shares one baseline across sites — show it
    verbatim. A production run keeps a per-site timestamp, so show the range,
    collapsed when the sites agree on the day.
    """
    if BASELINE_OVERRIDE:
        return f"{BASELINE_OVERRIDE.split(' ')[0]}（测试基线）"
    days = sorted({str(value).split(" ")[0] for value in baselines.values() if value})
    if not days:
        return "首次运行"
    if days[0] == days[-1]:
        return days[0]
    return f"{days[0]} ~ {days[-1]}"


def group_by_site(
    hits: Sequence[Hit], summaries: Sequence[Summary]
) -> OrderedDict[str, list[tuple[Hit, Summary]]]:
    """Hits paired with their summary, bucketed per site in first-seen order."""
    grouped: OrderedDict[str, list[tuple[Hit, Summary]]] = OrderedDict()
    for hit, summary in zip(hits, summaries, strict=True):
        grouped.setdefault(hit["site"], []).append((hit, summary))
    return grouped


def build_digest_body(
    hits: Sequence[Hit],
    summaries: Sequence[Summary],
    kind: str = "competitor",
    *,
    generated_at: datetime | None = None,
) -> str:
    """The plain-text digest, grouped by site (the multipart fallback)."""
    labels = DIGEST_KINDS[kind]
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    external_count = sum(1 for hit in hits if hit.get("external"))

    parts = [f"{labels['emoji']} {labels['title']} - 发现 {len(hits)} 条新消息"]
    if external_count:
        parts.append(f"（其中 {external_count} 条为站外报道/视频/PDF，仅标题与链接）")
    parts += [f"生成时间: {stamp}", "=" * 40, ""]

    for site, entries in group_by_site(hits, summaries).items():
        parts += [f"🏢 {site}  ({len(entries)} 条)", "-" * 40]
        for hit, (_model, summary) in entries:
            is_external = bool(hit.get("external"))
            parts += [
                f"{'🔗' if is_external else '📄'} {hit['title']}",
                f"发布时间: {display_date(hit.get('publish_date'))}",
                f"链接: {hit['url']}",
            ]
            # An external item has no summary by design; say so, or a reader reads
            # the gap as a failure.
            if is_external:
                parts.append(f"（{external_note(hit)}，无摘要，请点击链接查看原文）")
            elif summary:
                parts += ["📝 摘要:", summary]
            parts.append("")
        parts.append("")

    parts.append(labels["footer"])
    return "\n".join(parts)


def build_digest_html(
    hits: Sequence[Hit],
    summaries: Sequence[Summary],
    kind: str = "competitor",
    *,
    baseline_label: str = "",
    generated_at: datetime | None = None,
) -> str:
    """The styled HTML digest, grouped by site."""
    labels = DIGEST_KINDS[kind]
    grouped = group_by_site(hits, summaries)
    blocks = [
        f'<div style="margin:0;padding:0;background:{PAGE_BG};">',
        f'<div style="max-width:680px;margin:0 auto;padding:24px 16px;'
        f'font-family:{_FONT_STACK};color:{INK};">',
        _header_html(labels, hits, len(grouped), baseline_label, generated_at),
    ]
    for site, entries in grouped.items():
        blocks.append(_site_heading_html(site, len(entries)))
        blocks += [_card_html(hit, summary) for hit, summary in entries]
    blocks += [
        f'<div style="margin-top:26px;padding-top:16px;border-top:1px solid {LINE};'
        f'font-size:12px;color:{MUTED};line-height:1.6;">{labels["footer"]}</div>',
        "</div></div>",
    ]
    return "".join(blocks)


def _header_html(
    labels: Mapping[str, str],
    hits: Sequence[Hit],
    site_count: int,
    baseline_label: str,
    generated_at: datetime | None,
) -> str:
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    external_count = sum(1 for hit in hits if hit.get("external"))
    baseline_line = (
        f'<div style="font-size:13px;opacity:.8;margin-top:4px;">'
        f"上次监测: {html.escape(baseline_label)}（本期为此后的新文章）</div>"
        if baseline_label
        else ""
    )
    external_line = (
        f'<div style="font-size:13px;opacity:.8;margin-top:4px;">'
        f"其中 {external_count} 条为站外报道/视频/PDF，仅提供标题与链接</div>"
        if external_count
        else ""
    )
    return (
        f'<div style="background:{INK};border-radius:12px;padding:22px 24px;color:#ffffff;">'
        f'<div style="font-size:20px;font-weight:700;letter-spacing:.3px;">'
        f'{labels["emoji"]} {labels["title"]}</div>'
        f'<div style="font-size:13px;opacity:.8;margin-top:6px;">'
        f'共 {len(hits)} 条新消息 · {site_count} {labels["unit"]} · 生成于 {stamp}</div>'
        f"{baseline_line}{external_line}</div>"
    )


def _site_heading_html(site: str, count: int) -> str:
    return (
        f'<div style="margin-top:22px;padding-left:12px;border-left:4px solid {ACCENT};">'
        f'<span style="font-size:16px;font-weight:700;color:{INK};">'
        f"🏢 {html.escape(site)}</span>"
        f'<span style="font-size:13px;color:{MUTED};margin-left:8px;">{count} 条</span>'
        f"</div>"
    )


def _card_html(hit: Hit, summary: Summary) -> str:
    _model, summary_text = summary
    is_external = bool(hit.get("external"))
    pills = _date_pill_html(display_date(hit.get("publish_date")))
    if is_external:
        # A badge beside the date pill distinguishes "no summary on purpose" from
        # "summarization failed" — an empty grey block would not.
        pills += _external_pill_html(external_note(hit))
        body = (
            f'<div style="margin-top:12px;font-size:13px;line-height:1.7;color:{MUTED};">'
            f"该条为站外内容，未生成摘要，请点击下方链接查看原文。</div>"
        )
    else:
        body = (
            f'<div style="margin-top:12px;font-size:14px;line-height:1.7;color:#2a3b40;">'
            f"{md_to_html(summary_text)}</div>"
        )

    return (
        f'<div style="background:{CARD_BG};border:1px solid {LINE};'
        f'border-radius:10px;padding:16px 18px;margin-top:12px;">'
        f'<div style="font-size:15px;font-weight:600;line-height:1.45;color:{INK};">'
        f'{html.escape(hit.get("title", ""))}</div>'
        f'<div style="margin-top:8px;">{pills}</div>'
        f"{body}"
        f'<div style="margin-top:14px;">'
        f'<a href="{html.escape(hit.get("url", ""), quote=True)}" '
        f'style="display:inline-block;font-size:13px;font-weight:600;'
        f'color:{ACCENT};text-decoration:none;">'
        f'{"查看原文（站外）" if is_external else "阅读原文"} →</a>'
        f"</div></div>"
    )


def _date_pill_html(date_text: str) -> str:
    return (
        f'<span style="display:inline-block;background:{DATE_BG};color:{ACCENT};'
        f'font-size:12px;padding:2px 10px;border-radius:20px;">'
        f"🗓 {html.escape(date_text)}</span>"
    )


def _external_pill_html(note: str) -> str:
    return (
        f'<span style="display:inline-block;background:#fdf1e3;color:#a2601a;'
        f'font-size:12px;padding:2px 10px;border-radius:20px;margin-left:6px;">'
        f"🔗 {html.escape(note)}</span>"
    )


def split_by_group(hits: Sequence[Hit]) -> dict[str, list[int]]:
    """Hit indexes per digest kind.

    Regulatory notices get their own email: mixed into the competitor digest they
    read as noise to one audience and get buried for the other. Splitting on the
    hit's `group` (not on site names) means a new regulatory source routes itself.
    """
    return {
        "competitor": [i for i, hit in enumerate(hits) if hit.get("group") != "regulatory"],
        "regulatory": [i for i, hit in enumerate(hits) if hit.get("group") == "regulatory"],
    }


def select(items: Sequence[Any], indexes: Sequence[int]) -> list[Any]:
    return [items[i] for i in indexes]
