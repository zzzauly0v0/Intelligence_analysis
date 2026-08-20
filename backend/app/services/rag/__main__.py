"""RAG 嵌入 worker 的命令行入口。

    python -m app.services.rag

把 DB 中 embedding_done=False 的文章切块、编码、写入 Milvus。供 cron 定时或手动触发。
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.services.rag.indexer import run_indexing

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Windows 默认的 Proactor 事件循环无法运行 psycopg 异步查询，改用 Selector 循环。
    # 服务器经 uvicorn 启动时会自动选对循环；这里是我们自己起的 asyncio.run，
    # 故用 loop_factory 指定（Py3.14 起 set_event_loop_policy 已弃用）。
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    count = asyncio.run(run_indexing(), loop_factory=loop_factory)
    logger.info("全部完成，共处理 %d 篇文章", count)


if __name__ == "__main__":
    main()
