"""Milvus Lite 向量库封装：建集合、写入 chunk 向量、检索最近邻。

把 Milvus 的细节收敛到本模块，对外只暴露 ensure_collection / add / search。
MVP 使用本地文件模式（Milvus Lite），无需独立服务进程。
"""

from __future__ import annotations

import logging
from typing import Any

from pymilvus import DataType, MilvusClient

from app.services.rag.embedding import EMBED_DIM

logger = logging.getLogger(__name__)

# TODO(后续): 迁移到 settings.MILVUS_DB_PATH / settings.RAG_COLLECTION 统一配置。
_DB_PATH = "./milvus_rag.db"
_COLLECTION = "article_chunks"


class VectorStore:
    """Milvus Lite 中「文章块」集合的封装。"""

    def __init__(
        self, db_path: str = _DB_PATH, collection_name: str = _COLLECTION
    ) -> None:
        self._client = MilvusClient(uri=db_path)
        self._collection = collection_name

    def ensure_collection(self) -> None:
        """集合不存在则按 schema 创建（含向量维度与 COSINE 索引）。幂等。"""
        if self._client.has_collection(self._collection):
            return

        schema = MilvusClient.create_schema(auto_id=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("article_id", DataType.INT64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("text", DataType.VARCHAR, max_length=4096)
        schema.add_field("group_type", DataType.VARCHAR, max_length=50)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)

        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", metric_type="COSINE")

        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
        )
        logger.info("已创建 Milvus 集合: %s", self._collection)

    def add(self, rows: list[dict[str, Any]]) -> int:
        """写入一批 chunk，返回写入行数。

        每行需含 article_id / chunk_index / text / group_type / vector。
        MVP 为 insert-only：去重由上游 embedding_done 标志保证。
        """
        # TODO(你来写):
        #   1. 边界：rows 为空返回 0
        #   2. self._client.insert(collection_name=self._collection, data=rows)
        #   3. 返回写入行数
        if not rows:
            return 0
        self._client.insert(collection_name=self._collection, data=rows)
        return len(rows)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        """检索与 query_vector 最相近的 top_k 个 chunk。

        返回每个命中的 article_id / chunk_index / text / group_type / score。
        """
        # TODO(你来写):
        #   1. 调 self._client.search(
        #          collection_name=self._collection,
        #          data=[query_vector],       # 记得包一层 list
        #          limit=top_k,
        #          output_fields=["article_id", "chunk_index", "text", "group_type"],
        #      )
        #   2. 取 results[0]（第一个查询向量的命中列表）
        #   3. 每个 hit 形如 {"id":.., "distance":.., "entity": {字段们}}
        #      组装成 {"article_id":.., "chunk_index":.., "text":.., "group_type":..,
        #      "score": hit["distance"]} 的列表返回
        result = self._client.search(
            collection_name=self._collection,
            data=[query_vector],
            limit=top_k,
            output_fields=["article_id", "chunk_index", "text", "group_type"],
        )
        results = result[0]
        hits = []
        for hit in results:
            hit_dict = {
                "article_id": hit["entity"]["article_id"],
                "chunk_index": hit["entity"]["chunk_index"],
                "text": hit["entity"]["text"],
                "group_type": hit["entity"]["group_type"],
                "score": hit["distance"],
            }
            hits.append(hit_dict)
        return hits
