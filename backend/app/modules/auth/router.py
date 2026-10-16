"""Authentication and account-administration endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from app.core.deps import (
    AnonymousAuditCtx,
    AuditCtx,
    AuthenticatedUser,
    DbSession,
    get_client_ip,
    require,
)
from app.core.errors import NotFound
from app.modules.auth import service
from app.modules.auth.models import Permission, Role, User
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    CreatedUserResponse,
    CreateUserRequest,
    CurrentUserResponse,
    LoginRequest,
    LogoutRequest,
    PermissionResponse,
    RefreshRequest,
    ResetPasswordResponse,
    RoleResponse,
    RoleSummary,
    SetActiveRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _to_current_user(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        employee_id=user.employee_id,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        roles=[RoleSummary(code=link.role.code, name=link.role.name) for link in user.roles],
        permissions=sorted(user.permission_codes),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in with a username, employee code or email",
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Account is inactive"},
        429: {"description": "Too many failed attempts"},
    },
)
def login(
    payload: LoginRequest,
    request: Request,
    session: DbSession,
    ctx: AnonymousAuditCtx,
) -> TokenResponse:
    """Issue an access token and a refresh token.

    Failed attempts are rate limited per identifier and per client address. The
    response is identical for an unknown account and a wrong password.
    """
    tokens = service.authenticate(
        session,
        identifier=payload.identifier,
        password=payload.password,
        ip_address=ctx.ip_address,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id,
    )
    return TokenResponse(
        access_token=tokens.access.token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.access.expires_at,
        refresh_expires_at=tokens.refresh_expires_at,  # type: ignore[arg-type]
        must_change_password=tokens.must_change_password,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new session",
)
def refresh(payload: RefreshRequest, request: Request, session: DbSession) -> TokenResponse:
    """Rotate the refresh token. Presenting an already-rotated token ends all sessions."""
    tokens = service.refresh_session(
        session,
        raw_refresh_token=payload.refresh_token,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=tokens.access.token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.access.expires_at,
        refresh_expires_at=tokens.refresh_expires_at,  # type: ignore[arg-type]
        must_change_password=tokens.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a session")
def logout(
    payload: LogoutRequest,
    session: DbSession,
    user: AuthenticatedUser,
) -> None:
    """Revoke the supplied refresh token, or every session for the user."""
    if payload.all_sessions or not payload.refresh_token:
        service.revoke_all_sessions(session, user_id=user.id, reason="logout_all")
    else:
        service.revoke_session(session, raw_refresh_token=payload.refresh_token)


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="The signed-in user, their roles and permissions",
)
def me(user: AuthenticatedUser) -> CurrentUserResponse:
    """Reachable while a temporary password is pending, so the client can redirect."""
    return _to_current_user(user)


@router.post(
    "/me/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your own password",
)
def change_password(
    payload: ChangePasswordRequest,
    session: DbSession,
    user: AuthenticatedUser,
    ctx: AuditCtx,
) -> None:
    """Also reachable with a pending temporary password - that is its main use.

    All existing sessions are revoked, so the client must sign in again.
    """
    service.change_own_password(
        session,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        context=ctx,
    )


# ---------------------------------------------------------------------------
# Account administration (HR/Admin)
# ---------------------------------------------------------------------------


@router.post(
    "/users",
    response_model=CreatedUserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an employee sign-in account",
)
def create_user(
    payload: CreateUserRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("user.manage"))],
) -> CreatedUserResponse:
    """There is no public registration; accounts exist only via this endpoint.

    The generated temporary password is returned once in the response body and is
    never written to logs or the audit trail.
    """
    result = service.create_user(
        session,
        username=payload.username,
        email=payload.email,
        role_codes=payload.role_codes,
        employee_id=payload.employee_id,
        context=ctx,
    )
    return CreatedUserResponse(
        id=result.user.id,
        username=result.user.username,
        email=result.user.email,
        roles=sorted(link.role.code for link in result.user.roles),
        temporary_password=result.temporary_password,
    )


@router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    summary="Issue a temporary password for a user",
)
def reset_password(
    user_id: int,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("user.reset_password"))],
) -> ResetPasswordResponse:
    temporary = service.reset_password_for_user(session, target_user_id=user_id, context=ctx)
    target = session.get(User, user_id)
    if target is None:  # pragma: no cover - reset_password_for_user already raised
        raise NotFound("User not found.")
    return ResetPasswordResponse(
        user_id=target.id, username=target.username, temporary_password=temporary
    )


@router.patch(
    "/users/{user_id}/active",
    response_model=CurrentUserResponse,
    summary="Activate or deactivate an account",
)
def set_active(
    user_id: int,
    payload: SetActiveRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("user.manage"))],
) -> CurrentUserResponse:
    """Deactivation revokes every live session and preserves all history."""
    target = service.set_user_active(
        session,
        target_user_id=user_id,
        is_active=payload.is_active,
        reason=payload.reason,
        context=ctx,
    )
    return _to_current_user(target)


@router.get(
    "/roles",
    response_model=list[RoleResponse],
    summary="List roles and their permission grants",
)
def list_roles(
    session: DbSession,
    _actor: Annotated[User, Depends(require("role.read"))],
) -> list[RoleResponse]:
    roles = session.scalars(select(Role).order_by(Role.code)).all()
    return [
        RoleResponse(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            is_active=role.is_active,
            permissions=sorted(role.permission_codes),
        )
        for role in roles
    ]


@router.get(
    "/permissions",
    response_model=list[PermissionResponse],
    summary="List every permission the system defines",
)
def list_permissions(
    session: DbSession,
    _actor: Annotated[User, Depends(require("role.read"))],
) -> list[PermissionResponse]:
    permissions = session.scalars(
        select(Permission).order_by(Permission.module, Permission.code)
    ).all()
    return [PermissionResponse.model_validate(p) for p in permissions]
