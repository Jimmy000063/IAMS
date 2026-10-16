"""Request-scoped dependencies: identity, authorization and audit context.

Authorization is enforced here, in the backend, on every protected route.
Frontend navigation hiding is a usability feature only and is never the control.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import (
    AccountInactive,
    NotAuthenticated,
    PasswordChangeRequired,
    PermissionDenied,
)
from app.core.permissions import ALL_PERMISSION_CODES
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.modules.audit.service import AuditContext
from app.modules.auth.models import User

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer access token")

DbSession = Annotated[Session, Depends(get_db_session)]


def get_client_ip(request: Request) -> str | None:
    """Best-effort client address.

    ``X-Forwarded-For`` is only consulted because this app is expected to sit
    behind a reverse proxy. The value is attacker-controlled when it is not, so
    it is used solely for audit context and coarse throttling - never for
    authorization.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


def get_authenticated_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    """Resolve the bearer token to an active user.

    Permissions are deliberately read from the database here rather than taken
    from the token, so revoking a role takes effect on the next request instead
    of at token expiry.

    This dependency does **not** enforce ``must_change_password``; see
    :func:`get_current_user`. A handful of endpoints must stay reachable while a
    temporary password is pending.
    """
    if credentials is None or not credentials.credentials:
        raise NotAuthenticated()

    claims = decode_access_token(credentials.credentials)
    user = session.get(User, claims.user_id)

    if user is None:
        raise NotAuthenticated("Invalid authentication token.")
    if not user.is_active:
        raise AccountInactive()

    request.state.user_id = user.id
    request.state.username = user.username
    return user


def get_current_user(
    user: Annotated[User, Depends(get_authenticated_user)],
) -> User:
    """The acting user, with a pending temporary password treated as a block.

    Returning 403 ``password_change_required`` lets the frontend route the user
    to the change-password screen instead of showing a generic failure.
    """
    if user.must_change_password:
        raise PasswordChangeRequired()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthenticatedUser = Annotated[User, Depends(get_authenticated_user)]


def get_audit_context(
    request: Request,
    user: Annotated[User | None, Depends(get_authenticated_user)] = None,
) -> AuditContext:
    return AuditContext(
        actor_user_id=user.id if user else None,
        actor_label=user.username if user else None,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


AuditCtx = Annotated[AuditContext, Depends(get_audit_context)]


def get_anonymous_audit_context(request: Request) -> AuditContext:
    """Audit context for unauthenticated endpoints such as sign-in."""
    return AuditContext(
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


AnonymousAuditCtx = Annotated[AuditContext, Depends(get_anonymous_audit_context)]


def require(*permission_codes: str):
    """Build a dependency requiring **all** of the given permissions.

    ``require("payroll.finalize")`` on a route is the authorization control for
    that route. Codes are checked against the catalogue at import time, so a
    typo fails at application start rather than silently granting access -
    an unknown code would otherwise never match any user's permissions and the
    route would appear locked for everyone, or worse, a misspelled check could
    be dropped during refactoring without anyone noticing.
    """
    unknown = set(permission_codes) - ALL_PERMISSION_CODES
    if unknown:
        raise RuntimeError(
            f"require() referenced unknown permission code(s): {sorted(unknown)}. "
            "Add them to app.core.permissions.PERMISSIONS."
        )

    def dependency(user: CurrentUser) -> User:
        held = user.permission_codes
        missing = [code for code in permission_codes if code not in held]
        if missing:
            raise PermissionDenied(
                "You do not have permission to perform this action.",
                details=[{"field": None, "message": f"Requires: {', '.join(missing)}"}],
            )
        return user

    return dependency


def require_any(*permission_codes: str):
    """Build a dependency requiring **at least one** of the given permissions.

    Used where several routes converge, for example a record readable either by
    its owner (``leave.read_self``) or by HR (``leave.read_all``). The handler is
    then responsible for narrowing the result set to what the holder may see.
    """
    unknown = set(permission_codes) - ALL_PERMISSION_CODES
    if unknown:
        raise RuntimeError(
            f"require_any() referenced unknown permission code(s): {sorted(unknown)}."
        )

    def dependency(user: CurrentUser) -> User:
        if not any(code in user.permission_codes for code in permission_codes):
            raise PermissionDenied(
                "You do not have permission to perform this action.",
                details=[
                    {"field": None, "message": f"Requires one of: {', '.join(permission_codes)}"}
                ],
            )
        return user

    return dependency
