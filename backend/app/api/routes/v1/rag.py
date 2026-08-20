"""RAG 检索路由。

POST /rag/query — 语义检索：给一个问题，返回最相关的文章块（带出处）。
                  MVP 只做检索，不生成答案。
"""

from typing import Any

from fastapi import APIRouter

from app.api.deps import RagServiceDep
from app.schemas.rag import RagQuery, RagResult, RagSource

router = APIRouter()


@router.post("/query", response_model=RagResult, summary="语义检索")
async def query(body: RagQuery, service: RagServiceDep) -> Any:
    """把问题编码后在向量库检索，返回最相关的文章块及其出处。"""
    sources = await service.query(body.question, top_k=body.top_k)
    return RagResult(
        question=body.question,
        sources=[RagSource(**s) for s in sources],
    )
