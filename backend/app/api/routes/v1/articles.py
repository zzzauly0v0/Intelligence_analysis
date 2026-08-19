"""Article query routes.

Three read-only endpoints:

  GET /articles/today      — today's articles (optionally filtered by group)
  GET /articles/{id}       — single article with full body text
  GET /articles/history    — paginated history with date/group/site/search filters
"""

from datetime import date
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import ArticleServiceDep
from app.core.exceptions import BadRequestError
from app.schemas.article import ArticleDetail, ArticleList, ArticleQuery, ArticleRead

router = APIRouter()


@router.get("/today", response_model=list[ArticleRead], summary="今日文章")
async def get_today(
    service: ArticleServiceDep,
    group_type: str | None = Query(
        default=None,
        description="'competitor' 或 'regulatory'，不传返回全部",
    ),
) -> Any:
    """返回 publish_date = 今天 的文章列表，按站点名排序。"""
    try:
        return await service.list_today(group_type=group_type)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@router.get("/history", response_model=ArticleList, summary="历史文章")
async def get_history(
    service: ArticleServiceDep,
    group_type: str | None = Query(default=None),
    site_name: str | None = Query(default=None),
    date_from: date | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: date | None = Query(default=None, description="YYYY-MM-DD"),
    search: str | None = Query(default=None, description="标题/站点名关键词"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> Any:
    """分页查询历史文章，支持按分组、站点、日期范围、关键词过滤。"""
    query = ArticleQuery(
        group_type=group_type,
        site_name=site_name,
        date_from=date_from,
        date_to=date_to,
        search=search,
        skip=skip,
        limit=limit,
    )
    try:
        items, total = await service.list_history(query)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return ArticleList(items=items, total=total, skip=skip, limit=limit)


@router.get("/{article_id}", response_model=ArticleDetail, summary="文章详情")
async def get_article(
    article_id: int,
    service: ArticleServiceDep,
) -> Any:
    """返回单篇文章的完整内容（含 body_text）。"""
    return await service.get(article_id)
