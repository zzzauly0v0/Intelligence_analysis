"""Command line entry point: scrape -> summarize -> email two digests.

    python -m app.services.crawler                  # 默认模型
    python -m app.services.crawler --model unsloth  # 换成自建本地模型
    python -m app.services.crawler --list-models    # 看有哪些可选
    python -m app.services.crawler --dry-run        # 抓取+摘要但不发信

Freshness comes from each site's saved timestamp (config/monitor_last_times.json),
never from keyword matching. See ``pipeline`` for the scrape and ``digest`` for the
rendering; this module only wires them together and reports.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.crawler.common.deps import (
    default_model,
    format_model_table,
    make_email_sender,
    make_summarizer,
)
from app.services.crawler.delivery.digest import (
    build_digest_body,
    build_digest_html,
    format_baseline_label,
    select,
    split_by_group,
    subject_for,
)
from app.services.crawler.common.models import Hit, Summary
from app.services.crawler.common.paths import LOG_DIR
from app.services.crawler.orchestration.pipeline import scan_all_sites
from app.services.crawler.delivery.reporting import configure_console, run_stamp

logger = logging.getLogger(__name__)

_RULE = "=" * 60


@dataclass(frozen=True, slots=True)
class Delivery:
    """One rendered email, ready to send or preview."""

    kind: str
    subject: str
    body: str
    html_body: str
    recipients: list[str]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.crawler",
        description="竞品情报监测：抓取新文章 → AI 摘要 → 发送邮件摘要",
        epilog=format_model_table(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-m", "--model", default=None,
        help=f"使用哪个摘要模型（默认读 .env 的 SUMMARIZER_MODEL，否则用 {default_model()}）",
    )
    parser.add_argument("--list-models", action="store_true", help="列出可用模型后退出")
    parser.add_argument(
        "--dry-run", action="store_true", help="照常抓取并生成摘要，但不发送邮件（只打印预览）"
    )
    return parser.parse_args(argv)


def summarize_hits(hits: Sequence[Hit], summarizer: Any) -> list[Summary]:
    """A summary per hit, aligned to ``hits``.

    Three cases, in order:
      * external — a third-party repost / video / PDF we never fetched a body for.
        Empty summary rather than an error string, so the renderers can style it as
        "no summary on purpose";
      * has a body — the regulatory prompt for regulatory sources, else competitor;
      * body came back empty — a real failure, said out loud in the digest.
    """
    summaries: list[Summary] = []
    for index, hit in enumerate(hits, 1):
        logger.info("  [%d/%d] %s", index, len(hits), hit["title"][:60])
        if hit.get("external"):
            logger.info("     ↳ 外部/文档链接，仅标题+链接，不做摘要")
            summaries.append((None, ""))
        elif hit.get("body_text", "").strip():
            if hit.get("group") == "regulatory":
                summaries.append(summarizer.summarize_regulatory(hit["body_text"]))
            else:
                summaries.append(summarizer.summarize_competitor(hit["body_text"]))
        else:
            summaries.append((None, "（未能获取正文，请点击链接查看）"))
    return summaries


def build_deliveries(
    hits: Sequence[Hit],
    summaries: Sequence[Summary],
    recipients: Mapping[str, Sequence[str]],
    baseline_label: str = "",
) -> list[Delivery]:
    """One Delivery per digest kind that actually has new items."""
    deliveries = []
    for kind, indexes in split_by_group(hits).items():
        if not indexes:
            continue
        kind_hits = select(hits, indexes)
        kind_summaries = select(summaries, indexes)
        deliveries.append(
            Delivery(
                kind=kind,
                subject=subject_for(kind, len(kind_hits)),
                body=build_digest_body(kind_hits, kind_summaries, kind),
                html_body=build_digest_html(
                    kind_hits, kind_summaries, kind, baseline_label=baseline_label
                ),
                recipients=list(recipients.get(kind, ())),
            )
        )
    return deliveries


def log_preview(delivery: Delivery) -> None:
    logger.info(
        "\n%s\nEMAIL PREVIEW [%s] (plain-text fallback)\n%s\nSubject: %s\nTo: %s\n%s\n%s",
        _RULE, delivery.kind, _RULE, delivery.subject,
        ", ".join(delivery.recipients), delivery.body, _RULE,
    )


def write_backup(delivery: Delivery) -> Path:
    """Keep a failed digest on disk instead of losing it."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"monitor_email_backup_{delivery.kind}_{run_stamp()}.txt"
    path.write_text(
        f"Subject: {delivery.subject}\nTo: {', '.join(delivery.recipients)}\n\n{delivery.body}",
        encoding="utf-8",
    )
    return path


def deliver(delivery: Delivery, sender: Any) -> bool:
    """Send one digest; on failure back it up to a file. True when sent."""
    if sender.send_email(
        delivery.subject, delivery.body,
        html_body=delivery.html_body, recipients=delivery.recipients,
    ):
        logger.info("✅ %s digest email sent.", delivery.kind)
        return True
    logger.error("❌ %s email failed. Saved backup to %s", delivery.kind, write_backup(delivery))
    return False


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_console()

    if args.list_models:
        logger.info("%s", format_model_table())
        return 0

    logger.info("%s\nStarting multi-site monitor workflow\n%s", _RULE, _RULE)

    # Both are built BEFORE the slow scrape: a bad --model name or unreachable
    # model server should fail in seconds, not after 30 sites of browser work.
    try:
        summarizer = make_summarizer(args.model)
        sender = make_email_sender()
    except (ValueError, RuntimeError) as exc:
        logger.error("❌ %s", exc)
        return 2

    run = scan_all_sites()
    if not run.hits:
        logger.info("\nNo new items (newer than each site's last time). Nothing to send.")
        return 0

    logger.info("\nGenerating summaries for %d hits via %s...", len(run.hits), summarizer.name)
    summaries = summarize_hits(run.hits, summarizer)

    deliveries = build_deliveries(
        run.hits,
        summaries,
        {
            "competitor": sender.email_recipients,
            "regulatory": sender.regulatory_recipients,
        },
        # The baselines captured before the scan advanced them — otherwise the
        # header would claim the run compared against times it only just saved.
        format_baseline_label(run.baselines),
    )

    failures = 0
    for delivery in deliveries:
        log_preview(delivery)
        if args.dry_run:
            logger.info("🧪 --dry-run：%s 邮件未发送。", delivery.kind)
            continue
        if not deliver(delivery, sender):
            failures += 1

    return 1 if failures else 0
