"""嵌入 worker：把未向量化的文章切块、编码、写入 Milvus。

粘合层——只负责编排 chunking / embedding / vectorstore / repo 四个组件，
不含新的业务逻辑。供 CLI（cron 定时 / 手动触发）调用。
"""

from __future__ import annotations

import logging

from app.db.session import get_worker_db_context
from app.repository import article as article_repo
from app.services.rag.chunking import chunk_text
from app.services.rag.embedding import get_embedder
from app.services.rag.vectorstore import VectorStore

logger = logging.getLogger(__name__)


async def run_indexing(batch_limit: int = 100) -> int:
    """处理一批未向量化的文章，返回成功处理的文章数。

    每篇文章：切块 → 编码 → 写 Milvus → 标记 embedding_done。
    """
    embedder = get_embedder()
    store = VectorStore()
    store.ensure_collection()

    processed = 0
    async with get_worker_db_context() as db:
        articles = await article_repo.list_pending_embedding(db, limit=batch_limit)
        logger.info("待向量化文章: %d 篇", len(articles))

        for article in articles:
            # TODO(你来写)：处理单篇文章
            #   1. chunks = chunk_text(article.body_text or "")
            #   2. 边界：chunks 为空 → 仍要 mark_embedding_done（避免反复处理），然后 continue
            #   3. vectors = embedder.encode(chunks)
            #   4. 组装 rows：每个 chunk 一行 dict，含
            #        article_id / chunk_index / text / group_type / vector
            #      提示：enumerate(zip(chunks, vectors, strict=True))
            #   5. store.add(rows)
            #   6. await article_repo.mark_embedding_done(db, article.id)
            #   7. processed += 1
            chunks = chunk_text(article.body_text or "")
            if not chunks:
                await article_repo.mark_embedding_done(db, article.id)
                continue
            vectors = embedder.encode(chunks)
            rows = [
                {
                    "article_id": article.id,
                    "chunk_index": i,
                    "text": chunk,
                    "group_type": article.group_type,
                    "vector": vec,
                }
                for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True))
            ]
            store.add(rows)
            await article_repo.mark_embedding_done(db, article.id)
            processed += 1

    logger.info("本次索引完成，处理 %d 篇", processed)
    return processed
