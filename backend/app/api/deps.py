"""API dependencies.

Assembly layer for the routes: hand out a request-scoped DB session, build the
service objects on top of it, and turn the bearer token into a ``User`` (plus the
admin check). Only the pieces that exist today are wired up here -- there is one
service (``UserService``) and no tenancy/organization model yet, so there is no
"current organization" dependency to inject.
"""

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError, NotFoundError
from app.core.security import TokenType, decode_token
from app.db.models.user import User, UserRole
from app.db.session import get_db_session
from app.services.article import ArticleService
from app.services.user import UserService

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/users/login/access-token"
)

""" Infrastructure """

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]

""" Services """


def get_user_service(db: SessionDep) -> UserService:
    return UserService(db)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_article_service(db: SessionDep) -> ArticleService:
    return ArticleService(db)


ArticleServiceDep = Annotated[ArticleService, Depends(get_article_service)]

""" Authentication """


async def get_current_user(service: UserServiceDep, token: TokenDep) -> User:
    """Resolve the bearer access token to an active user.

    Access tokens are stateless: revoking a login session kills its refresh
    token, an already-issued access token stays valid until it expires.
    """
    claims = decode_token(token, expected_type=TokenType.ACCESS)
    if claims is None:
        raise AuthenticationError("Could not validate credentials")
    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Could not validate credentials") from exc

    try:
        user = await service.get_by_id(user_id)
    except NotFoundError as exc:
        # A token for a deleted account is a credentials problem, not a 404.
        raise AuthenticationError("Could not validate credentials") from exc
    if not user.is_active:
        raise AuthenticationError("Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

""" Authorization """


def is_admin(user: User) -> bool:
    """Admins are ``role == admin`` plus the bootstrap app admin."""
    return user.is_app_admin or user.has_role(UserRole.ADMIN)


def get_current_admin(current_user: CurrentUser) -> User:
    if not is_admin(current_user):
        raise AuthorizationError("The user doesn't have enough privileges")
    return current_user


AdminUser = Annotated[User, Depends(get_current_admin)]

""" Request metadata """


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Caller fingerprint recorded on a login session."""

    ip_address: str | None
    user_agent: str | None


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]
