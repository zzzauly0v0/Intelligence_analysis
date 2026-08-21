"""RAG 门面：RagService —— 路由唯一可见的入口。

MVP 只做检索（不生成答案）：问题 → 编码 → Milvus 检索 → 回 Postgres 补文章元信息。

嵌入与 Milvus 检索都是同步阻塞调用，在异步请求里必须用 asyncio.to_thread
甩到线程池，否则会卡住整个事件循环（见 query 内注释）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repository import article as article_repo
from app.services.rag.embedding import get_embedder
from app.services.rag.vectorstore import VectorStore


class RagService:
    """RAG 检索用例。路由通过依赖注入拿到它，只调用 query。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        # 单例：首次触发模型加载，之后复用（理想情况由 lifespan 预热）。
        self.embedder = get_embedder()
        self.store = VectorStore()

    async def query(self, question: str, top_k: int = 5) -> list[dict[str, Any]]:
        """检索与问题最相关的文章块，返回带出处的结果列表。"""
        # 1. 把问题编码成向量（阻塞调用 → 甩到线程池；注意传函数和参数，不是调用结果）
        vectors = await asyncio.to_thread(self.embedder.encode, [question])
        query_vec = vectors[0]

        # 2. TODO(你来写)：Milvus 检索
        #    模仿第 1 步，把 self.store.search(query_vec, top_k) 甩到线程池，得到 hits

        # 3. TODO(你来写)：回 Postgres 补文章元信息并组装返回
        #    对每个 hit：
        #      article = await article_repo.get_by_id(self.db, hit["article_id"])
        #      组装 dict：
        #        {"article_id": hit["article_id"],
        #         "title": article.title if article else None,
        #         "url": article.url if article else None,
        #         "site_name": article.site_name if article else None,
        #         "chunk_text": hit["text"],
        #         "score": hit["score"]}
        #    返回这些 dict 组成的列表
        hits = await asyncio.to_thread(self.store.search, query_vec, top_k)
        results = []
        for hit in hits:
            article = await article_repo.get_by_id(self.db, hit["article_id"])
            result = {
                "article_id": hit["article_id"],
                "title": article.title if article else None,
                "url": article.url if article else None,
                "site_name": article.site_name if article else None,
                "chunk_text": hit["text"],
                "score": hit["score"],
            }
            results.append(result)
        return results
