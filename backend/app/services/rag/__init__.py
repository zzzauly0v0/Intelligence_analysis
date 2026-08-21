"""RAG 服务包。

厚领域子包：对外只暴露门面 RagService，嵌入 / 向量库 / 切块等子模块均为包内实现，
外部（路由、worker）不应直接导入。
"""

from app.services.rag.facade import RagService

__all__ = ["RagService"]
