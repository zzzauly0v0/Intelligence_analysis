"""BGE-m3 文本嵌入封装。

把「文本 → 向量」收敛到一个接口后面，隐藏 FlagEmbedding / BGE-m3 的细节。
日后若改用 Bedrock 等其它嵌入服务，只需替换本模块，indexer 与 facade 不受影响。

模型加载慢（首次会下载约 2.3GB 权重并载入内存），因此通过 ``get_embedder``
惰性单例保证全进程只加载一次。
"""

from __future__ import annotations

import logging

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

# BGE-m3 dense 向量维度。建 Milvus collection 时要用到，故设为单一出处的常量。
EMBED_DIM = 1024


class Embedder:
    """BGE-m3 嵌入模型封装。构造即加载模型（慢），故应只实例化一次。"""

    def __init__(self, model_name: str = settings.RAG_EMBED_MODEL) -> None:
        from FlagEmbedding import BGEM3FlagModel
        from modelscope import snapshot_download

        logger.info("正在从 ModelScope 定位 BGE-m3 模型 ...")
        model_dir = snapshot_download(model_name, ignore_file_pattern=["onnx"])
        logger.info("正在加载 BGE-m3 模型: %s", model_dir)
        self._model = BGEM3FlagModel(model_dir, use_fp16=False)
        logger.info("BGE-m3 模型加载完成。")

    def encode(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        """把一批文本编码成 dense 向量。

        Args:
            texts: 待编码的文本列表。
            batch_size: 每批送入模型的文本数。

        Returns:
            与 ``texts`` 等长的向量列表，每个向量 ``EMBED_DIM`` 维；空输入返回 []。
        """
        # TODO(你来写):
        #   1. 边界：texts 为空直接返回 []
        #   2. 调 self._model.encode(texts, batch_size=..., max_length=512)
        #   3. 取返回值的 "dense_vecs"（numpy 数组），转成 list[list[float]] 返回
        if not texts:
            return []
        output = self._model.encode(texts, batch_size=batch_size, max_length=512)
        dense = np.asarray(output["dense_vecs"])
        return dense.tolist()


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """惰性单例：首次调用时加载模型，之后复用同一实例。"""
    # TODO(你来写):
    #   用模块级 _embedder 缓存；为 None 时创建 Embedder() 并赋值，最后返回它
    #   （提示：函数内给模块级变量赋值，需要 global 声明）
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
    raise NotImplementedError
