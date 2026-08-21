"""User endpoints: accounts, authentication and password recovery.

One router for the whole user surface -- signup/login/refresh/logout live next
to the CRUD they belong to. Static paths are declared before ``/{user_id}`` so
they are not swallowed by it.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import EmailStr

from app.api.deps import (
    AdminUser,
    ClientInfoDep,
    CurrentUser,
    UserServiceDep,
    get_current_admin,
    is_admin,
)
from app.core.exceptions import AuthorizationError
from app.schemas.base import Message
from app.schemas.user import (
    NewPassword,
    RefreshToken,
    Token,
    UpdatePassword,
    UserCreate,
    UserList,
    UserRead,
    UserRegister,
    UserUpdate,
    UserUpdateMe,
)

router = APIRouter()

""" Registration and authentication """


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(user_in: UserRegister, service: UserServiceDep) -> Any:
    """Create a new account without being logged in.

    The first account ever created is promoted to app admin.
    """
    return await service.register(user_in)


@router.post("/login/access-token")
async def login_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: UserServiceDep,
    client: ClientInfoDep,
) -> Token:
    """OAuth2 compatible token login: returns an access/refresh token pair."""
    return await service.login(
        form_data.username,
        form_data.password,
        ip_address=client.ip_address,
        user_agent=client.user_agent,
    )


@router.post("/login/refresh-token")
async def refresh_access_token(
    body: RefreshToken, service: UserServiceDep, client: ClientInfoDep
) -> Token:
    """Exchange a refresh token for a fresh pair; the old token is retired."""
    return await service.refresh(
        body.refresh_token,
        ip_address=client.ip_address,
        user_agent=client.user_agent,
    )


@router.post("/login/test-token", response_model=UserRead)
async def test_token(current_user: CurrentUser) -> Any:
    """Check that an access token is still valid."""
    return current_user


@router.post("/logout")
async def logout(body: RefreshToken, service: UserServiceDep) -> Message:
    """Revoke a single login session. Unknown tokens are accepted silently."""
    await service.logout(body.refresh_token)
    return Message(message="Logged out successfully")


@router.post("/logout-all")
async def logout_all(current_user: CurrentUser, service: UserServiceDep) -> Message:
    """Revoke every login session of the current user."""
    revoked = await service.logout_all(current_user.id)
    return Message(message=f"Revoked {revoked} session(s)")


""" Password recovery """


@router.post("/password-recovery/{email}")
async def recover_password(email: EmailStr, service: UserServiceDep) -> Message:
    """Send a password reset link.

    The response is identical for unknown addresses to prevent enumeration.
    """
    await service.request_password_recovery(email)
    return Message(message="If that email is registered, we sent a recovery link")


@router.post("/reset-password")
async def reset_password(body: NewPassword, service: UserServiceDep) -> Message:
    """Set a new password from a recovery token; signs the account out everywhere."""
    await service.reset_password(body)
    return Message(message="Password updated successfully")


""" Current user """


@router.get("/me", response_model=UserRead)
async def read_user_me(current_user: CurrentUser) -> Any:
    """Get the current user."""
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_user_me(
    user_in: UserUpdateMe, current_user: CurrentUser, service: UserServiceDep
) -> Any:
    """Update own profile."""
    return await service.update_me(current_user, user_in)


@router.patch("/me/password")
async def update_password_me(
    body: UpdatePassword, current_user: CurrentUser, service: UserServiceDep
) -> Message:
    """Update own password; all login sessions are revoked."""
    await service.change_password(current_user, body)
    return Message(message="Password updated successfully")


@router.delete("/me")
async def delete_user_me(current_user: CurrentUser, service: UserServiceDep) -> Message:
    """Delete own account."""
    if current_user.is_app_admin:
        raise AuthorizationError("App admins are not allowed to delete themselves")
    await service.delete(current_user.id)
    return Message(message="User deleted successfully")


""" Administration """


@router.get("/", dependencies=[Depends(get_current_admin)])
async def read_users(
    service: UserServiceDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    search: Annotated[str | None, Query(max_length=255)] = None,
) -> UserList:
    """List users, optionally filtered by email or name."""
    users, total = await service.list_users(skip=skip, limit=limit, search=search)
    return UserList(
        items=[UserRead.model_validate(user) for user in users], total=total
    )


@router.post(
    "/",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_admin)],
)
async def create_user(user_in: UserCreate, service: UserServiceDep) -> Any:
    """Create a user and mail them their credentials."""
    return await service.create(user_in, notify=True)


@router.get("/{user_id}", response_model=UserRead)
async def read_user_by_id(
    user_id: UUID, current_user: CurrentUser, service: UserServiceDep
) -> Any:
    """Get a specific user; non-admins may only read themselves."""
    if user_id != current_user.id and not is_admin(current_user):
        raise AuthorizationError("The user doesn't have enough privileges")
    return await service.get_by_id(user_id)


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    dependencies=[Depends(get_current_admin)],
)
async def update_user(
    user_id: UUID, user_in: UserUpdate, service: UserServiceDep
) -> Any:
    """Update any user. Setting a password signs that account out everywhere."""
    return await service.update(user_id, user_in)


@router.delete("/{user_id}")
async def delete_user(
    user_id: UUID, current_user: AdminUser, service: UserServiceDep
) -> Message:
    """Delete any user except yourself."""
    if user_id == current_user.id:
        raise AuthorizationError("Admins are not allowed to delete themselves")
    await service.delete(user_id)
    return Message(message="User deleted successfully")
