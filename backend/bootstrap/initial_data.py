"""Seed the first superuser. Idempotent: safe to run on every deploy."""

import asyncio
import logging

from app.core.config import settings
from app.db.session import close_db, get_db_context
from app.schemas.user import UserCreate, UserRole
from app.services.user import UserService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init() -> None:
    async with get_db_context() as db:
        service = UserService(db)
        if await service.get_by_email(settings.FIRST_SUPERUSER):
            logger.info("Superuser %s already exists", settings.FIRST_SUPERUSER)
            return
        await service.create(
            UserCreate(
                email=settings.FIRST_SUPERUSER,
                password=settings.FIRST_SUPERUSER_PASSWORD,
                role=UserRole.ADMIN,
                is_app_admin=True,
            )
        )
        logger.info("Created superuser %s", settings.FIRST_SUPERUSER)


async def main() -> None:
    logger.info("Creating initial data")
    try:
        await init()
    finally:
        await close_db()
    logger.info("Initial data created")


if __name__ == "__main__":
    asyncio.run(main())
