#!/usr/bin/env python3
"""
Who gets mail — resolved from config/recipients.json and nowhere else.

The lists used to sit in the repo-root config.json and were read twice,
independently: EmailSender honoured MONITOR_TEST_RECIPIENTS, while
send_failure_notification.py read the same file directly and ignored it — so a
test run that failed would mail the real distribution list anyway. Both readers
now come through load_recipients(), and the data moved next to the other config
files so that adding a person is a config edit, never a code edit.

Every problem aborts loudly: missing file, unknown list name, missing list,
empty list. Recipient config is the worst possible place for a silent fallback —
"sent to nobody" is indistinguishable from success in the log, and "fell back to
the main list" mails nine people something addressed to one.
"""

import json
import os

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "recipients.json")

# The lists the code knows how to ask for. Declared here rather than inferred
# from the JSON, so deleting a list from config is an error instead of a mail
# that quietly stops being delivered.
KNOWN_LISTS = {
    "competitor": "竞品情报邮件（每日摘要）",
    "regulatory": "法规动态邮件（卫健委等）",
    "alert": "流程失败时的报错通知",
}

TEST_OVERRIDE_ENV = "MONITOR_TEST_RECIPIENTS"


class RecipientConfigError(RuntimeError):
    """Recipient config can't be trusted — callers must abort, not fall back."""


def _read_config():
    if not os.path.exists(CONFIG_PATH):
        raise RecipientConfigError(
            f"找不到收件人配置 {CONFIG_PATH}\n"
            "（收件人已从根目录 config.json 迁到 config/recipients.json）")
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RecipientConfigError(f"{CONFIG_PATH} 不是合法 JSON：{e}") from e


def _clean(addrs, where):
    """Trim, drop blanks, de-duplicate case-insensitively, preserve order.

    De-duplication matters because the same person legitimately appears in more
    than one list, and hand-edited config picks up copies — a doubled address
    would otherwise show up twice in the To: header.
    """
    seen, out = set(), []
    for raw in addrs:
        addr = str(raw).strip()
        if not addr:
            continue
        if "@" not in addr:
            raise RecipientConfigError(f"{where}：{addr!r} 不像邮箱地址（没有 @）")
        if addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append(addr)
    return out


def test_override():
    """The MONITOR_TEST_RECIPIENTS addresses, or None when the switch is unset."""
    raw = os.getenv(TEST_OVERRIDE_ENV, "").strip()
    if not raw:
        return None
    addrs = _clean(raw.split(","), TEST_OVERRIDE_ENV)
    if not addrs:
        raise RecipientConfigError(f"{TEST_OVERRIDE_ENV} 设了值但解析后为空")
    return addrs


def load_recipients(name, allow_test_override=True):
    """Return the address list called `name` (see KNOWN_LISTS).

    MONITOR_TEST_RECIPIENTS overrides EVERY list, `alert` included: a failing
    test run must not page the real distribution list either.
    """
    if name not in KNOWN_LISTS:
        raise RecipientConfigError(
            f"未知收件人列表 {name!r}；可用的是 " +
            "、".join(f"{k}（{desc}）" for k, desc in KNOWN_LISTS.items()))

    if allow_test_override:
        override = test_override()
        if override:
            return override

    cfg = _read_config()
    if name not in cfg:
        present = [k for k in cfg if not k.startswith("_")] or ["（一个都没有）"]
        raise RecipientConfigError(
            f"{CONFIG_PATH} 里缺少列表 {name!r}（{KNOWN_LISTS[name]}）；"
            f"文件里现有：{'、'.join(present)}")
    if not isinstance(cfg[name], list):
        raise RecipientConfigError(
            f"{CONFIG_PATH} 的 {name!r} 应该是数组，"
            f"实际是 {type(cfg[name]).__name__}")

    addrs = _clean(cfg[name], f"{CONFIG_PATH} 的 {name!r}")
    if not addrs:
        raise RecipientConfigError(
            f"{CONFIG_PATH} 的 {name!r}（{KNOWN_LISTS[name]}）是空列表 —— "
            "发不出去的邮件在日志里跟成功长得一样，所以这里直接报错")
    return addrs


def load_all(allow_test_override=False):
    """Every known list, resolved. Used by tests/test_config_integrity.py."""
    return {name: load_recipients(name, allow_test_override=allow_test_override)
            for name in KNOWN_LISTS}


if __name__ == "__main__":
    # `./nhc/bin/python src/recipients.py` — 看一眼当前生效的收件人。
    for _name, _addrs in load_all().items():
        print(f"{_name:12s} ({KNOWN_LISTS[_name]}) — {len(_addrs)} 人")
        for _a in _addrs:
            print(f"    {_a}")
