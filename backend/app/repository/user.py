"""User data access: user rows and their login sessions.

Repository functions never commit -- the request-scoped session in
``app.db.session`` commits once the request succeeds. They ``flush`` (and
``refresh`` where a server default is needed) so callers see generated values.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session import Session
from app.db.models.user import User, UserRole

""" Users """


async def get_by_id(db: AsyncSession, user_id: UUID) -> User | None:
    return await db.get(User, user_id)


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def list_users(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
) -> tuple[list[User], int]:
    """Return one page of users plus the total number of matches."""
    stmt = select(User)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))

    total_result = await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    result = await db.execute(
        stmt.order_by(User.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all()), total_result.scalar_one()


async def create(
    db: AsyncSession,
    *,
    email: str,
    hashed_password: str,
    full_name: str | None = None,
    role: UserRole = UserRole.USER,
    is_active: bool = True,
    is_app_admin: bool = False,
) -> User:
    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=full_name,
        role=role.value,
        is_active=is_active,
        is_app_admin=is_app_admin,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)  # created_at is a server default
    return user


async def update(
    db: AsyncSession, *, db_user: User, update_data: dict[str, Any]
) -> User:
    """Apply already-validated column values; ``role`` may be passed as enum or str."""
    for field, value in update_data.items():
        if field == "role" and isinstance(value, UserRole):
            value = value.value
        setattr(db_user, field, value)
    db.add(db_user)
    await db.flush()
    await db.refresh(db_user)
    return db_user


async def delete(db: AsyncSession, db_user: User) -> None:
    await db.delete(db_user)
    await db.flush()


""" Login sessions """


async def create_session(
    db: AsyncSession,
    *,
    user_id: UUID,
    refresh_token_hash: str,
    expires_at: datetime,
    session_id: UUID | None = None,
    device_name: str | None = None,
    device_type: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Session:
    login_session = Session(
        id=session_id or uuid4(),
        user_id=user_id,
        refresh_token_hash=refresh_token_hash,
        expires_at=expires_at,
        device_name=device_name,
        device_type=device_type,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(login_session)
    await db.flush()
    return login_session


async def get_active_session_by_hash(
    db: AsyncSession, refresh_token_hash: str
) -> Session | None:
    result = await db.execute(
        select(Session).where(
            Session.refresh_token_hash == refresh_token_hash,
            Session.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def rotate_session(
    db: AsyncSession,
    *,
    login_session: Session,
    refresh_token_hash: str,
    expires_at: datetime,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Session:
    """Swap in the digest of the newly issued refresh token, retiring the old one."""
    login_session.refresh_token_hash = refresh_token_hash
    login_session.expires_at = expires_at
    login_session.last_used_at = datetime.now(UTC)
    if ip_address is not None:
        login_session.ip_address = ip_address
    if user_agent is not None:
        login_session.user_agent = user_agent
    db.add(login_session)
    await db.flush()
    return login_session


async def deactivate_session(db: AsyncSession, login_session: Session) -> None:
    login_session.is_active = False
    db.add(login_session)
    await db.flush()


async def deactivate_all_user_sessions(db: AsyncSession, user_id: UUID) -> int:
    """Revoke every refresh token of a user; returns how many were still active."""
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            sql_update(Session)
            .where(Session.user_id == user_id, Session.is_active.is_(True))
            .values(is_active=False)
        ),
    )
    await db.flush()
    return result.rowcount
