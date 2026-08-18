#!/usr/bin/env python3
"""
Multi-site monitor workflow (timestamp mode).

  1. Scrape every enabled site (fetch_monitor_sites) -> items whose publish
     date is newer than that site's saved last-article time.
  2. Summarize each new item's body with the chosen model (summarizer.py).
  3. Email TWO separate digests via Gmail (process_and_email.py), split on each
     hit's `group`: competitor news to the `competitor` list, regulatory notices
     (卫健委 …) to the `regulatory` list — both from config/recipients.json.
     Either email is skipped when that group had no new items.

Freshness is decided by comparing publish dates against a per-site timestamp
(config/monitor_last_times.json) — no keyword matching.

Run manually:
    python3 src/run_monitor.py                  # 默认模型（sonnet，Bedrock 上的 Claude）
    python3 src/run_monitor.py --model unsloth  # 换成自建本地模型（免费，质量略降）
    python3 src/run_monitor.py --list-models    # 看有哪些可选
    python3 src/run_monitor.py --dry-run        # 抓取+摘要但不发信
Or via cron (see management/run_monitor_cron.sh).
"""

import argparse
import html
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_monitor_sites import (fetch_all_new_hits, BASELINE_OVERRIDE,
                                 load_last_times)
from process_and_email import EmailSender
from summarizer import Summarizer, format_model_table, DEFAULT_MODEL


# --------------------------------------------------------------------------- #
# Palette (inline-styled for email-client compatibility)
# --------------------------------------------------------------------------- #
INK = "#12303a"        # deep teal-ink — headers
ACCENT = "#0f8a7e"     # teal accent — company bars, links
MUTED = "#5b6b70"      # secondary text
LINE = "#e2e8ea"       # hairline borders
CARD_BG = "#ffffff"
PAGE_BG = "#f4f6f6"
DATE_BG = "#eef4f3"


def _md_to_html(text):
    """
    Convert the lightweight Markdown the model emits into safe inline HTML.
    Handles **bold**, *italic*, `code`, bullet lines (- / •) and line breaks.
    Everything is HTML-escaped first so article text can't inject markup.
    """
    if not text:
        return ""
    esc = html.escape(text.strip())
    # **bold** and __bold__
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"__(.+?)__", r"<strong>\1</strong>", esc)
    # *italic* (avoid matching the ** already consumed)
    esc = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", esc)
    # `inline code`
    esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)

    # Render the structured 【核心】【要点】【影响】 section labels the model emits
    # as small colored badges so the summary reads cleanly.
    def _label(m):
        return (f'<span style="display:inline-block;font-size:12px;font-weight:700;'
                f'color:{ACCENT};background:{DATE_BG};border-radius:4px;'
                f'padding:1px 7px;margin:2px 0;">{m.group(1)}</span> ')
    esc = re.sub(r"【(核心|要点|影响|摘要|简介)】", _label, esc)

    # Turn leading bullet markers into • and preserve line breaks.
    out_lines = []
    for line in esc.split("\n"):
        stripped = line.strip()
        stripped = re.sub(r"^[-*•]\s+", "• ", stripped)
        out_lines.append(stripped)
    return "<br>".join(out_lines)


def _display_date(publish_date):
    """Date as shown in the email: day precision only. The stored value keeps
    its HH:MM:SS (freshness comparison needs it); the time part is CMS-internal
    noise to a reader, so strip it for display. None -> 未知."""
    if not publish_date:
        return "未知"
    return str(publish_date).split(" ")[0]


_VIDEO_HOST_RE = re.compile(r"(youtube\.com|youtu\.be|vimeo\.com|bilibili\.com)", re.I)
_DOC_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?)($|\?)", re.I)


def _external_note(hit):
    """Short label for an item we deliberately don't summarize.

    Some newsrooms publish third-party coverage as their news (Zydus's "In the
    news" wall): those items live on another site, in a video player, or in a
    scanned PDF clipping, so there is no body we could reliably scrape and no
    point summarizing someone else's page — the reader clicks through. Naming
    WHICH kind it is tells them what to expect before they click.
    """
    url = hit.get("url", "")
    if _VIDEO_HOST_RE.search(url):
        return "视频"
    if _DOC_EXT_RE.search(url):
        return "PDF/文档"
    host = re.sub(r"^www\.", "", urlparse(url).netloc)
    return f"外部媒体报道{f'·{host}' if host else ''}"


def _baseline_label():
    """Human-readable 'articles newer than X' label for the email header.

    Test runs (MONITOR_BASELINE) have ONE baseline for every site — show it
    verbatim. Production runs keep a per-site timestamp, so show the RANGE the
    sites actually compared against (oldest–newest last-seen time), collapsed
    to one value when they agree on the day. No timestamps yet -> 首次运行."""
    if BASELINE_OVERRIDE:
        return f"{BASELINE_OVERRIDE.split(' ')[0]}（测试基线）"
    times = load_last_times()
    days = sorted({str(v).split(" ")[0] for v in times.values() if v})
    if not days:
        return "首次运行"
    if len(days) == 1 or days[0] == days[-1]:
        return days[0]
    return f"{days[0]} ~ {days[-1]}"


# The two digests differ only in wording: competitor news is read as "which
# company did what", regulatory news as "which authority published what". Keeping
# one renderer with a label pack (rather than two copies) means a layout fix
# reaches both emails.
DIGEST_KINDS = {
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


def build_digest_body(hits, summaries, kind="competitor"):
    """Compose the plaintext email grouped by company/site (fallback version)."""
    k = DIGEST_KINDS[kind]
    parts = []
    parts.append(f"{k['emoji']} {k['title']} - 发现 {len(hits)} 条新消息")
    n_ext = sum(1 for h in hits if h.get("external"))
    if n_ext:
        parts.append(f"（其中 {n_ext} 条为站外报道/视频/PDF，仅标题与链接）")
    parts.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    parts.append("=" * 40)
    parts.append("")

    # Group hits by site, preserving first-seen order.
    grouped = OrderedDict()
    for i, h in enumerate(hits):
        grouped.setdefault(h["site"], []).append((h, summaries[i]))

    for site, entries in grouped.items():
        parts.append(f"🏢 {site}  ({len(entries)} 条)")
        parts.append("-" * 40)
        for h, (model, summary) in entries:
            # External reposts / PDFs / videos carry no summary by design — mark
            # them so a reader knows the missing summary is intentional and the
            # content lives on another site.
            parts.append(f"{'🔗' if h.get('external') else '📄'} {h['title']}")
            parts.append(f"发布时间: {_display_date(h.get('publish_date'))}")
            parts.append(f"链接: {h['url']}")
            if h.get("external"):
                parts.append(f"（{_external_note(h)}，无摘要，请点击链接查看原文）")
            elif summary:
                parts.append("📝 摘要:")
                parts.append(summary)
            parts.append("")
        parts.append("")

    parts.append(k["footer"])
    return "\n".join(parts)


def build_digest_html(hits, summaries, kind="competitor"):
    """Compose a styled HTML digest, grouped by company/site."""
    k = DIGEST_KINDS[kind]
    grouped = OrderedDict()
    for i, h in enumerate(hits):
        grouped.setdefault(h["site"], []).append((h, summaries[i]))

    n_ext = sum(1 for h in hits if h.get("external"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    font = ("-apple-system,BlinkMacSystemFont,'Segoe UI',"
            "'PingFang SC','Microsoft YaHei',Roboto,Helvetica,Arial,sans-serif")

    p = []
    p.append(f'<div style="margin:0;padding:0;background:{PAGE_BG};">')
    p.append(f'<div style="max-width:680px;margin:0 auto;padding:24px 16px;'
             f'font-family:{font};color:{INK};">')

    # Header
    p.append(
        f'<div style="background:{INK};border-radius:12px;padding:22px 24px;'
        f'color:#ffffff;">'
        f'<div style="font-size:20px;font-weight:700;letter-spacing:.3px;">'
        f'{k["emoji"]} {k["title"]}</div>'
        f'<div style="font-size:13px;opacity:.8;margin-top:6px;">'
        f'共 {len(hits)} 条新消息 · {len(grouped)} {k["unit"]} · 生成于 {now}</div>'
        f'<div style="font-size:13px;opacity:.8;margin-top:4px;">'
        f'上次监测: {html.escape(_baseline_label())}（本期为此后的新文章）</div>'
        + (f'<div style="font-size:13px;opacity:.8;margin-top:4px;">'
           f'其中 {n_ext} 条为站外报道/视频/PDF，仅提供标题与链接</div>'
           if n_ext else '')
        + f'</div>'
    )

    # Company sections
    for site, entries in grouped.items():
        p.append(
            f'<div style="margin-top:22px;padding-left:12px;'
            f'border-left:4px solid {ACCENT};">'
            f'<span style="font-size:16px;font-weight:700;color:{INK};">'
            f'🏢 {html.escape(site)}</span>'
            f'<span style="font-size:13px;color:{MUTED};margin-left:8px;">'
            f'{len(entries)} 条</span>'
            f'</div>'
        )
        for h, (model, summary) in entries:
            title = html.escape(h.get("title", ""))
            url = html.escape(h.get("url", ""), quote=True)
            date = html.escape(_display_date(h.get("publish_date")))
            is_ext = bool(h.get("external"))
            summary_html = _md_to_html(summary) if summary else ""

            # External items (third-party coverage, videos, PDF clippings) have
            # no summary by design: instead of an empty grey block, show a badge
            # next to the date pill saying what it is, so the reader can tell
            # "no summary on purpose" from "summarization failed".
            pills = (f'<span style="display:inline-block;background:{DATE_BG};'
                     f'color:{ACCENT};font-size:12px;padding:2px 10px;'
                     f'border-radius:20px;">🗓 {date}</span>')
            if is_ext:
                pills += (f'<span style="display:inline-block;background:#fdf1e3;'
                          f'color:#a2601a;font-size:12px;padding:2px 10px;'
                          f'border-radius:20px;margin-left:6px;">'
                          f'🔗 {html.escape(_external_note(h))}</span>')
                body_block = (f'<div style="margin-top:12px;font-size:13px;'
                              f'line-height:1.7;color:{MUTED};">'
                              f'该条为站外内容，未生成摘要，请点击下方链接查看原文。</div>')
            else:
                body_block = (f'<div style="margin-top:12px;font-size:14px;'
                              f'line-height:1.7;color:#2a3b40;">{summary_html}</div>')

            p.append(
                f'<div style="background:{CARD_BG};border:1px solid {LINE};'
                f'border-radius:10px;padding:16px 18px;margin-top:12px;">'
                # title
                f'<div style="font-size:15px;font-weight:600;line-height:1.45;'
                f'color:{INK};">{title}</div>'
                # date pill (+ external badge)
                f'<div style="margin-top:8px;">{pills}</div>'
                # summary, or the "no summary on purpose" note
                + body_block +
                # read-more link + powered-by
                f'<div style="margin-top:14px;">'
                f'<a href="{url}" style="display:inline-block;font-size:13px;'
                f'font-weight:600;color:{ACCENT};text-decoration:none;">'
                f'{"查看原文（站外）" if is_ext else "阅读原文"} →</a>'
                f'</div>'
                f'</div>'
            )

    # Footer
    p.append(
        f'<div style="margin-top:26px;padding-top:16px;border-top:1px solid {LINE};'
        f'font-size:12px;color:{MUTED};line-height:1.6;">'
        f'{k["footer"]}'
        f'</div>'
    )

    p.append('</div></div>')
    return "".join(p)


def summarize_hits(hits, summarizer):
    """Summarize every hit, returning a [(model_id, text)] list aligned to hits.

    Three cases, in order:
      * external — a third-party repost / video / PDF that we deliberately never
        fetched a body for (Zydus's press-coverage wall). Empty summary, NOT an
        error string, so the renderers can style it as "no summary on purpose".
      * has a body — summarized with the regulatory prompt for regulatory sources
        (卫健委, future GRAS/EFSA additions) and the competitor prompt otherwise.
      * body fetch came back empty — a real failure; say so in the digest.
    """
    summaries = []
    for i, h in enumerate(hits, 1):
        print(f"  [{i}/{len(hits)}] {h['title'][:60]}")
        if h.get("external"):
            print("     ↳ 外部/文档链接，仅标题+链接，不做摘要")
            summaries.append((None, ""))
        elif h.get("body_text", "").strip():
            if h.get("group") == "regulatory":
                summaries.append(summarizer.summarize_regulatory(h["body_text"]))
            else:
                summaries.append(summarizer.summarize_competitor(h["body_text"]))
        else:
            summaries.append((None, "（未能获取正文，请点击链接查看）"))
    return summaries


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="竞品情报监测：抓取新文章 → AI 摘要 → 发送邮件摘要",
        epilog=format_model_table(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-m", "--model", default=None,
                   help=f"使用哪个摘要模型（默认读 .env 的 SUMMARIZER_MODEL，"
                        f"否则用 {DEFAULT_MODEL}）")
    p.add_argument("--list-models", action="store_true",
                   help="列出可用模型后退出")
    p.add_argument("--dry-run", action="store_true",
                   help="照常抓取并生成摘要，但不发送邮件（只打印预览）")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.list_models:
        print(format_model_table())
        return 0

    print("=" * 60)
    print("Starting multi-site monitor workflow")
    print("=" * 60)

    # Build the summarizer BEFORE the (slow) scrape: a bad --model name or an
    # unreachable model server should fail in seconds, not after 30 sites of
    # browser work. Same reason the recipient list is resolved up front.
    try:
        summarizer = Summarizer(args.model)
    except (ValueError, RuntimeError) as e:
        print(f"❌ {e}")
        return 2
    sender = EmailSender()

    hits = fetch_all_new_hits()
    if not hits:
        print("\nNo new items (newer than each site's last time). Nothing to send.")
        return 0

    print(f"\nGenerating summaries for {len(hits)} hits via {summarizer.name}...")
    summaries = summarize_hits(hits, summarizer)

    # Split into two independent digests. Regulatory notices (卫健委, future
    # GRAS/EFSA sources) go to the RA list in their own email: mixed into the
    # competitor digest they read as noise to one audience and get buried for the
    # other. Grouping by the hit's `group` field rather than by site name means a
    # newly added regulatory source lands here automatically.
    kinds = [
        ("competitor", [i for i, h in enumerate(hits)
                        if h.get("group") != "regulatory"],
         sender.email_recipients),
        ("regulatory", [i for i, h in enumerate(hits)
                        if h.get("group") == "regulatory"],
         sender.regulatory_recipients),
    ]

    failures = 0
    for kind, idxs, recipients in kinds:
        if not idxs:
            continue
        k_hits = [hits[i] for i in idxs]
        k_summaries = [summaries[i] for i in idxs]
        subject = DIGEST_KINDS[kind]["subject"].format(n=len(k_hits))
        body = build_digest_body(k_hits, k_summaries, kind)        # plain-text
        html_body = build_digest_html(k_hits, k_summaries, kind)   # styled HTML

        print("\n" + "=" * 60)
        print(f"EMAIL PREVIEW [{kind}] (plain-text fallback)")
        print("=" * 60)
        print(f"Subject: {subject}")
        print("To:", ", ".join(recipients))
        print(body)
        print("=" * 60)

        if args.dry_run:
            print(f"🧪 --dry-run：{kind} 邮件未发送。")
            continue

        if sender.send_email(subject, body, html_body=html_body,
                             recipients=recipients):
            print(f"✅ {kind} digest email sent.")
        else:
            # Backup to file if sending failed, so the digest isn't lost.
            backup = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                f"monitor_email_backup_{kind}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            )
            with open(backup, "w", encoding="utf-8") as f:
                f.write(f"Subject: {subject}\nTo: {', '.join(recipients)}\n\n{body}")
            print(f"❌ {kind} email failed. Saved backup to {backup}")
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
