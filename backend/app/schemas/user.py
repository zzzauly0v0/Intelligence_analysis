"""User schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.security import TokenType
from app.db.models.user import UserRole
from app.schemas.base import BaseSchema, TimestampSchema

__all__ = [
    "NewPassword",
    "RefreshToken",
    "Token",
    "TokenPayload",
    "TokenType",
    "UpdatePassword",
    "UserBase",
    "UserCreate",
    "UserList",
    "UserRead",
    "UserRegister",
    "UserRole",
    "UserUpdate",
    "UserUpdateMe",
]

""" The Request schemas for the User model """


class UserBase(BaseSchema):
    """Shared identity properties for user request schemas."""

    email: EmailStr = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """Lower-case the address; the DB unique index is case-sensitive."""
        return value.lower()


class UserRegister(UserBase):
    """Properties to receive via API on public self-registration."""

    password: str = Field(min_length=8, max_length=128)


class UserCreate(UserRegister):
    """Properties to receive via API on admin-side user creation.

    Extends the registration payload with the privileged flags that must
    never be settable from the public signup route.
    """

    is_active: bool = True
    role: UserRole = UserRole.USER
    is_app_admin: bool = False


class UserUpdateMe(BaseSchema):
    """Properties to receive via API on user self-update."""

    email: EmailStr | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        """Lower-case the address; the DB unique index is case-sensitive."""
        return value.lower() if value else value


class UserUpdate(UserUpdateMe):
    """Properties to receive via API on admin-side user update, all optional."""

    is_active: bool | None = None
    role: UserRole | None = None
    is_app_admin: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UpdatePassword(BaseSchema):
    """Properties to receive via API on password update."""

    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class NewPassword(BaseSchema):
    """Properties to receive via API on setting a new password."""

    token: str
    new_password: str = Field(min_length=8, max_length=128)


class RefreshToken(BaseSchema):
    """Properties to receive via API on refreshing or revoking a login session."""

    refresh_token: str


""" The Response schemas for the User model """


class UserRead(TimestampSchema):
    """Properties to return via API.

    Mirrors ``app.db.models.user.User`` minus credentials, so response
    fields are listed explicitly rather than inherited from a request schema.
    """

    id: UUID
    email: EmailStr
    full_name: str | None = None
    is_active: bool
    role: UserRole
    is_app_admin: bool
    avatar_url: str | None = None
    onboarding_completed_at: datetime | None = None
    oauth_provider: str | None = None


class UserList(BaseSchema):
    """Properties to return via API for a list of users."""

    items: list[UserRead]
    total: int


class Token(BaseSchema):
    """Properties to return via API for an access token."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None


class TokenPayload(BaseSchema):
    """Decoded contents of a JWT."""

    sub: UUID | None = None
    exp: int | None = None
    type: TokenType | None = None
    sid: UUID | None = None
    jti: str | None = None
