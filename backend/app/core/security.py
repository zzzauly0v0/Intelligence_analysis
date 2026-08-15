"""Password hashing and JWT issuing/decoding."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

ALGORITHM = "HS256"

password_hash = PasswordHash(
    (
        Argon2Hasher(),
        BcryptHasher(),
    )
)

# Argon2 hash of a random password. Verified against when the email is unknown so
# that a login attempt costs the same whether or not the account exists.
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


class TokenType(StrEnum):
    """Kinds of JWT this API issues, carried in the ``type`` claim.

    A token is only accepted by the flow that matches its type, so an access
    token cannot be replayed as a refresh token or a password-reset link.
    """

    ACCESS = "access"
    REFRESH = "refresh"
    PASSWORD_RESET = "password_reset"


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    """Verify a password; the second item is a new hash when the stored one is outdated."""
    return password_hash.verify_and_update(plain_password, hashed_password)


def _create_token(
    *,
    subject: uuid.UUID | str,
    token_type: TokenType,
    expires_delta: timedelta,
    session_id: uuid.UUID | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(subject),
        "exp": now + expires_delta,
        "iat": now,
        "nbf": now,
        "type": token_type.value,
        "jti": uuid.uuid4().hex,
    }
    if session_id is not None:
        claims["sid"] = str(session_id)
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(
    subject: uuid.UUID | str, *, session_id: uuid.UUID | None = None
) -> str:
    return _create_token(
        subject=subject,
        token_type=TokenType.ACCESS,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        session_id=session_id,
    )


def create_refresh_token(subject: uuid.UUID | str, *, session_id: uuid.UUID) -> str:
    return _create_token(
        subject=subject,
        token_type=TokenType.REFRESH,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        session_id=session_id,
    )


def create_password_reset_token(subject: uuid.UUID | str) -> str:
    return _create_token(
        subject=subject,
        token_type=TokenType.PASSWORD_RESET,
        expires_delta=timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS),
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any] | None:
    """Return the claims, or ``None`` if the token is invalid, expired or of another type."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[ALGORITHM]
        )
    except InvalidTokenError:
        return None
    if claims.get("type") != expected_type.value:
        return None
    return claims


def hash_refresh_token(token: str) -> str:
    """Refresh tokens are stored as digests so a leaked ``sessions`` row can't be replayed.

    A plain digest (no salt) is what lets us look the session up by token; the
    token itself is 200+ bits of signed JWT, so it is not brute-forceable.
    """
    return hashlib.sha256(token.encode()).hexdigest()
