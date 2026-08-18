"""API v1 routes."""

from fastapi import APIRouter

from app.api.routes.v1 import articles, user, utils

v1_router = APIRouter()

v1_router.include_router(user.router, prefix="/users", tags=["users"])
v1_router.include_router(utils.router, prefix="/utils", tags=["utils"])
v1_router.include_router(articles.router, prefix="/articles", tags=["articles"])

__all__ = ["v1_router"]
