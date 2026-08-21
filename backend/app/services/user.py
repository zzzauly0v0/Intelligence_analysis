"""User business logic: accounts, authentication and login sessions.

Everything user-facing lives here, including login, so routes stay thin and the
auth rules (session revocation on password change, first user becomes admin,
constant-time login) are written down exactly once.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exceptions import (
    AlreadyExistsError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
)
from app.core.security import (
    DUMMY_HASH,
    TokenType,
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    hash_refresh_token,
    verify_password,
)
from app.db.models.user import User, UserRole
from app.repository import user as user_repo
from app.schemas.user import (
    NewPassword,
    Token,
    UpdatePassword,
    UserCreate,
    UserRegister,
    UserUpdate,
    UserUpdateMe,
)
from app.services.email.service import (
    EmailData,
    generate_new_account_email,
    generate_reset_password_email,
    send_email,
)

logger = logging.getLogger(__name__)

INVALID_CREDENTIALS = "Incorrect email or password"

# Fields a client may explicitly null out; for every other column an explicit
# ``null`` is dropped rather than written into a NOT NULL column.
CLEARABLE_FIELDS = frozenset({"full_name", "avatar_url"})


class UserService:
    """Use cases for the ``users`` and ``sessions`` tables."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    """ Queries """

    async def get_by_id(self, user_id: UUID) -> User:
        user = await user_repo.get_by_id(self.db, user_id)
        if not user:
            raise NotFoundError(
                message="User not found",
                details={"user_id": user_id},
            )
        return user

    async def get_by_email(self, email: str) -> User | None:
        return await user_repo.get_by_email(self.db, email.lower())

    async def list_users(
        self, *, skip: int = 0, limit: int = 100, search: str | None = None
    ) -> tuple[list[User], int]:
        return await user_repo.list_users(
            self.db, skip=skip, limit=limit, search=search
        )

    """ Accounts """

    async def register(self, user_in: UserRegister) -> User:
        """Public sign-up. The very first account becomes the app admin."""
        await self._ensure_email_available(user_in.email)
        is_first_user = await user_repo.count(self.db) == 0
        return await user_repo.create(
            self.db,
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name,
            role=UserRole.ADMIN if is_first_user else UserRole.USER,
            is_app_admin=is_first_user,
        )

    async def create(self, user_in: UserCreate, *, notify: bool = False) -> User:
        """Admin-side creation; ``notify`` mails the account holder their password."""
        await self._ensure_email_available(user_in.email)
        user = await user_repo.create(
            self.db,
            email=user_in.email,
            hashed_password=get_password_hash(user_in.password),
            full_name=user_in.full_name,
            role=user_in.role,
            is_active=user_in.is_active,
            is_app_admin=user_in.is_app_admin,
        )
        if notify:
            await self._send_email(
                email_to=user.email,
                email_data=generate_new_account_email(
                    email_to=user.email,
                    username=user.email,
                    password=user_in.password,
                ),
            )
        return user

    async def update(self, user_id: UUID, user_in: UserUpdate) -> User:
        """Admin-side update. Setting a password signs the account out everywhere."""
        user = await self.get_by_id(user_id)
        update_data = self._writable_fields(user_in)

        if "email" in update_data:
            await self._ensure_email_available(update_data["email"], exclude=user.id)
        password = update_data.pop("password", None)
        if password is not None:
            update_data["hashed_password"] = get_password_hash(password)

        user = await user_repo.update(self.db, db_user=user, update_data=update_data)
        if password is not None:
            await user_repo.deactivate_all_user_sessions(self.db, user.id)
        return user

    async def update_me(self, user: User, user_in: UserUpdateMe) -> User:
        update_data = self._writable_fields(user_in)
        if "email" in update_data:
            await self._ensure_email_available(update_data["email"], exclude=user.id)
        return await user_repo.update(self.db, db_user=user, update_data=update_data)

    async def change_password(self, user: User, body: UpdatePassword) -> None:
        """Change one's own password; every refresh token of the account is revoked."""
        if not user.hashed_password:
            raise BadRequestError("This account has no password set")
        verified, _ = verify_password(body.current_password, user.hashed_password)
        if not verified:
            raise BadRequestError("Incorrect password")
        if body.current_password == body.new_password:
            raise BadRequestError("New password cannot be the same as the current one")

        await user_repo.update(
            self.db,
            db_user=user,
            update_data={"hashed_password": get_password_hash(body.new_password)},
        )
        await user_repo.deactivate_all_user_sessions(self.db, user.id)

    async def delete(self, user_id: UUID) -> None:
        user = await self.get_by_id(user_id)
        await user_repo.delete(self.db, user)

    """ Authentication """

    async def authenticate(self, email: str, password: str) -> User:
        user = await user_repo.get_by_email(self.db, email.lower())
        if user is None or not user.hashed_password:
            # Spend the same time as a real verification so the response time
            # doesn't reveal whether the address is registered.
            verify_password(password, DUMMY_HASH)
            raise AuthenticationError(INVALID_CREDENTIALS)

        verified, updated_hash = verify_password(password, user.hashed_password)
        if not verified:
            raise AuthenticationError(INVALID_CREDENTIALS)
        if updated_hash:
            await user_repo.update(
                self.db, db_user=user, update_data={"hashed_password": updated_hash}
            )
        if not user.is_active:
            raise AuthenticationError("Inactive user")
        return user

    async def login(
        self,
        email: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Token:
        user = await self.authenticate(email, password)
        session_id = uuid4()
        refresh_token = create_refresh_token(user.id, session_id=session_id)
        await user_repo.create_session(
            self.db,
            session_id=session_id,
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(refresh_token),
            expires_at=self._refresh_expires_at(),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self._token_response(
            user, session_id=session_id, refresh_token=refresh_token
        )

    async def refresh(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Token:
        """Exchange a refresh token for a new pair, rotating the stored digest.

        The presented token dies here: replaying it (or any earlier one) finds no
        active session and is rejected.
        """
        if decode_token(refresh_token, expected_type=TokenType.REFRESH) is None:
            raise AuthenticationError("Refresh token is invalid or has expired")

        login_session = await user_repo.get_active_session_by_hash(
            self.db, hash_refresh_token(refresh_token)
        )
        if login_session is None:
            raise AuthenticationError("Refresh token is invalid or has expired")
        if login_session.expires_at <= datetime.now(UTC):
            await user_repo.deactivate_session(self.db, login_session)
            raise AuthenticationError("Refresh token is invalid or has expired")

        user = await self.get_by_id(login_session.user_id)
        if not user.is_active:
            await user_repo.deactivate_session(self.db, login_session)
            raise AuthenticationError("Inactive user")

        new_refresh_token = create_refresh_token(user.id, session_id=login_session.id)
        await user_repo.rotate_session(
            self.db,
            login_session=login_session,
            refresh_token_hash=hash_refresh_token(new_refresh_token),
            expires_at=self._refresh_expires_at(),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self._token_response(
            user, session_id=login_session.id, refresh_token=new_refresh_token
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke one session. Idempotent: an unknown token is not an error."""
        login_session = await user_repo.get_active_session_by_hash(
            self.db, hash_refresh_token(refresh_token)
        )
        if login_session is not None:
            await user_repo.deactivate_session(self.db, login_session)

    async def logout_all(self, user_id: UUID) -> int:
        return await user_repo.deactivate_all_user_sessions(self.db, user_id)

    """ Password recovery """

    async def request_password_recovery(self, email: str) -> None:
        """Mail a reset link. Silent for unknown/disabled accounts (no enumeration)."""
        user = await user_repo.get_by_email(self.db, email.lower())
        if user is None or not user.is_active:
            logger.info("password_recovery_ignored", extra={"email": email})
            return

        token = create_password_reset_token(user.id)
        if not settings.emails_enabled:
            logger.warning(
                "emails_disabled, password reset token for %s: %s", user.email, token
            )
            return
        await self._send_email(
            email_to=user.email,
            email_data=generate_reset_password_email(
                email_to=user.email, email=user.email, token=token
            ),
        )

    async def reset_password(self, body: NewPassword) -> User:
        """Consume a reset token, then sign the account out of every device."""
        claims = decode_token(body.token, expected_type=TokenType.PASSWORD_RESET)
        if claims is None or "sub" not in claims:
            raise AuthenticationError("Reset link is invalid or has expired")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise AuthenticationError("Reset link is invalid or has expired") from exc

        user = await self.get_by_id(user_id)
        if not user.is_active:
            raise AuthenticationError("Inactive user")

        user = await user_repo.update(
            self.db,
            db_user=user,
            update_data={"hashed_password": get_password_hash(body.new_password)},
        )
        await user_repo.deactivate_all_user_sessions(self.db, user.id)
        return user

    """ Internals """

    async def _ensure_email_available(
        self, email: str, *, exclude: UUID | None = None
    ) -> None:
        existing = await user_repo.get_by_email(self.db, email)
        if existing is not None and existing.id != exclude:
            raise AlreadyExistsError(
                "User with this email already exists", details={"email": email}
            )

    @staticmethod
    def _writable_fields(user_in: UserUpdate | UserUpdateMe) -> dict[str, Any]:
        """Only the fields the client actually sent, minus meaningless nulls."""
        return {
            field: value
            for field, value in user_in.model_dump(exclude_unset=True).items()
            if value is not None or field in CLEARABLE_FIELDS
        }

    @staticmethod
    def _refresh_expires_at() -> datetime:
        return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    @staticmethod
    def _token_response(user: User, *, session_id: UUID, refresh_token: str) -> Token:
        return Token(
            access_token=create_access_token(user.id, session_id=session_id),
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def _send_email(self, *, email_to: str, email_data: EmailData) -> None:
        """Best effort: a broken mailbox must not fail the request it rides on."""
        if not settings.emails_enabled:
            logger.warning("emails_disabled, skipped sending to %s", email_to)
            return
        try:
            await run_in_threadpool(
                send_email,
                email_to=email_to,
                subject=email_data.subject,
                html_content=email_data.html_content,
            )
        except Exception:
            logger.exception("email_send_failed", extra={"email_to": email_to})
