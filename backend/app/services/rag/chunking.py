"""文章正文切块：把长文本切成带重叠的小块，供嵌入与检索使用。

采用「递归字符分块」策略：优先在自然语义边界（段落 → 句子 → 分句 → 逗号）
处切分，实在没有边界可用时才按字符硬切；再把细碎片段贪心合并成接近目标大小
的块，相邻块之间保留一段重叠，避免跨切割点的语义被劈断。

策略被封装在本模块内：对外只暴露 ``chunk_text``，日后若升级为语义分块等策略，
调用方（indexer、facade）无需改动。
"""

from __future__ import annotations

# 分隔符优先级：越靠前语义边界越「大」。空串是兜底，代表「按字符硬切」。
_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", "；", "，", "")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """把一段文本切成带重叠的块。

    Args:
        text: 待切分的原始文本。
        chunk_size: 每块的目标字符数上限（软约束）。
        overlap: 相邻块之间重叠的字符数，用于保住跨切割点的语义。

    Returns:
        文本块列表；空白输入返回空列表。
    """
    # 非法参数尽早失败：overlap ≥ chunk_size 会让合并窗口无法前进（死循环）。
    if overlap >= chunk_size:
        raise ValueError("overlap 必须小于 chunk_size，否则合并窗口无法前进")

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    pieces = _split_recursive(text, _SEPARATORS, chunk_size)
    return _merge(pieces, chunk_size, overlap)


def _split_recursive(
    text: str, separators: tuple[str, ...], chunk_size: int
) -> list[str]:
    """按分隔符优先级递归切分，返回一串均不超过 chunk_size 的细碎片段。"""
    sep, *rest = separators

    # 兜底层：没有更粗的自然边界可用了，直接按字符窗口硬切。
    if sep == "":
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    pieces: list[str] = []
    for part in text.split(sep):
        if not part:
            continue
        if len(part) <= chunk_size:
            pieces.append(part)
        else:
            # 这一段仍然太长，用更细的分隔符继续切它。
            pieces.extend(_split_recursive(part, tuple(rest), chunk_size))
    return pieces


def _merge(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """把细碎片段贪心拼成接近 chunk_size 的块，相邻块间保留 overlap 重叠。"""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        # 再加这一片就超了：先封存当前块，再从其末尾回退 overlap 个字符开新块。
        if current and len(current) + len(piece) > chunk_size:
            chunks.append(current)
            current = current[-overlap:] if overlap else ""
        current += piece
    if current:
        chunks.append(current)
    return chunks
