"""RAG 检索的请求 / 响应 schema。"""

from pydantic import Field

from app.schemas.base import BaseSchema


class RagQuery(BaseSchema):
    """POST /rag/query 的请求体。"""

    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)


class RagSource(BaseSchema):
    """单条检索命中：一个文章块 + 它所属文章的元信息。"""

    article_id: int
    title: str | None = None
    url: str | None = None
    site_name: str | None = None
    chunk_text: str
    score: float


class RagResult(BaseSchema):
    """POST /rag/query 的响应。"""

    question: str
    sources: list[RagSource]
