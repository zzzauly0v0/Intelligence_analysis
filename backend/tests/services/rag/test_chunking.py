"""app/services/rag/chunking.py 的单元测试。

纯函数、无 I/O，因此无需数据库或 anyio —— 普通同步测试即可。
"""

import pytest

from app.services.rag.chunking import chunk_text

# 一段含段落（\n\n）、换行（\n）与句号的中文正文，重复以确保远超单块大小。
_LONG_TEXT = (
    "公司今日宣布在印度新建一座原料药工厂。该工厂预计明年投产，产能将翻倍。\n"
    "此举旨在应对东南亚市场的快速增长。管理层表示，扩产是长期战略的一部分。\n\n"
    "与此同时，公司也在推进数字化转型。研发投入较去年增长两成。"
    "分析师普遍看好其未来两年的营收表现。"
) * 10


# --- 正常路径 ---


def test_chunk_long_text_returns_multiple_chunks():
    chunks = chunk_text(_LONG_TEXT, chunk_size=200, overlap=50)
    assert len(chunks) > 1


def test_chunk_long_text_every_chunk_is_non_empty():
    chunks = chunk_text(_LONG_TEXT, chunk_size=200, overlap=50)
    assert all(chunk.strip() for chunk in chunks)


def test_chunk_long_text_respects_soft_size_upper_bound():
    chunk_size, overlap = 200, 50
    chunks = chunk_text(_LONG_TEXT, chunk_size=chunk_size, overlap=overlap)
    # 块大小是软约束：新块会带上一块末尾的 overlap 段，故上界是 chunk_size + overlap。
    assert all(len(chunk) <= chunk_size + overlap for chunk in chunks)


def test_chunk_consecutive_chunks_share_overlap():
    overlap = 50
    chunks = chunk_text(_LONG_TEXT, chunk_size=200, overlap=overlap)
    # 每个后继块都以前一块末尾的 overlap 段开头 —— overlap 生效的直接证据。
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.startswith(prev[-overlap:])


# --- 边界情况 ---


def test_chunk_empty_string_returns_empty_list():
    assert chunk_text("") == []


def test_chunk_whitespace_only_returns_empty_list():
    assert chunk_text("   \n\t  ") == []


def test_chunk_text_shorter_than_size_returns_single_chunk():
    text = "很短的一段话。"
    assert chunk_text(text, chunk_size=200) == [text]


def test_chunk_strips_leading_and_trailing_whitespace():
    assert chunk_text("  首尾有空白  ", chunk_size=200) == ["首尾有空白"]


# --- 非法参数 ---


def test_chunk_overlap_equal_to_chunk_size_raises_value_error():
    with pytest.raises(ValueError):
        chunk_text("任意文本", chunk_size=100, overlap=100)


def test_chunk_overlap_greater_than_chunk_size_raises_value_error():
    with pytest.raises(ValueError):
        chunk_text("任意文本", chunk_size=100, overlap=200)
