"""Run logging and the per-site outcome summary."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from app.services.crawler.common.models import Outcome, OutcomeStatus
from app.services.crawler.common.paths import LOG_DIR

logger = logging.getLogger(__name__)

# Handlers are attached to the package logger, so every module's
# logging.getLogger(__name__) inherits them.
_PACKAGE_LOGGER = logging.getLogger(__package__)

_STATUS_LABELS: dict[OutcomeStatus, str] = {
    "load_failed": "❌ 页面加载失败",
    "no_items": "❌ 列表未解析出条目",
    "all_undated": "⚠️ 有新条目但全部读不到日期",
    "body_empty": "⚠️ 打开了文章但正文全为空",
    "ok": "✅ 成功",
    "no_new": "· 无新文章（正常）",
}

_HEALTHY_STATUSES = ("ok", "no_new")


def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _force_utf8(stream: object) -> None:
    """Keep the ✅/❌ status glyphs from raising on a legacy-codepage console.

    Windows consoles default to cp936/cp1252, where writing an emoji raises
    UnicodeEncodeError inside the logging handler and swallows the message.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def configure_console(level: int = logging.INFO) -> None:
    """Bare stdout logging for CLI use, before any file logging exists."""
    _force_utf8(sys.stdout)
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)


def setup_logging() -> tuple[Path, Path]:
    """Log to a timestamped file and the console. Returns (log_path, summary_path)."""
    _force_utf8(sys.stdout)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp()
    log_path = LOG_DIR / f"monitor_{stamp}.log"
    summary_path = LOG_DIR / f"monitor_summary_{stamp}.log"

    _PACKAGE_LOGGER.setLevel(logging.INFO)
    _PACKAGE_LOGGER.handlers.clear()  # calling twice must not duplicate output
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    for handler in (
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        handler.setFormatter(formatter)
        _PACKAGE_LOGGER.addHandler(handler)
    _PACKAGE_LOGGER.propagate = False

    return log_path, summary_path


def format_summary(outcomes: Sequence[Outcome]) -> str:
    """A per-site table that leads with the sites that got stuck, and where."""
    lines = ["=" * 60, "站点抓取结果汇总", "=" * 60]

    problems = [o for o in outcomes if o.status not in _HEALTHY_STATUSES]
    if problems:
        lines.append(f"\n⚠️ 有问题的站点（{len(problems)} 个）——按卡住的环节分类：\n")
        for outcome in problems:
            lines.append(f"  {_STATUS_LABELS.get(outcome.status, outcome.status)}  |  {outcome.name}")
            lines.append(f"       {outcome.detail}")
    else:
        lines.append("\n所有站点均正常（无卡壳）。\n")

    lines.append("\n" + "-" * 60)
    lines.append("全部站点明细：")
    for outcome in outcomes:
        label = _STATUS_LABELS.get(outcome.status, outcome.status)
        lines.append(f"  {label:22}  {outcome.name}  |  {outcome.detail}")
    lines.append("=" * 60)
    return "\n".join(lines)


def write_summary(summary_path: Path, outcomes: Sequence[Outcome]) -> None:
    text = format_summary(outcomes)
    logger.info("\n%s", text)
    try:
        summary_path.write_text(text + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write summary file: %s", exc)
