"""Authentication, session and account-provisioning services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import (
    AccountInactive,
    AccountLocked,
    AuthenticationFailed,
    Conflict,
    NotFound,
    ValidationFailed,
)
from app.core.permissions import PERMISSIONS as PERMISSION_CATALOGUE
from app.core.permissions import ROLES_BY_CODE, PermissionDef, RoleDef
from app.core.permissions import validate_catalogue as validate_permission_catalogue
from app.core.security import (
    AccessToken,
    create_access_token,
    generate_refresh_token,
    generate_temporary_password,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.auth.models import (
    LoginAttempt,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.modules.notifications import hooks as notification_hooks

logger = logging.getLogger(__name__)

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 64


def normalize_username(username: str) -> str:
    """Canonical username form: trimmed and lowercased.

    Applied on both write and lookup so that ``A.Sharma`` and ``a.sharma`` are the
    same account and the plain unique constraint on ``users.username`` gives
    case-insensitive uniqueness.
    """
    return username.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not (USERNAME_MIN_LENGTH <= len(normalized) <= USERNAME_MAX_LENGTH):
        raise ValidationFailed(
            f"Username must be between {USERNAME_MIN_LENGTH} and {USERNAME_MAX_LENGTH} characters."
        )
    if not all(char.isalnum() or char in "._-" for char in normalized):
        raise ValidationFailed(
            "Username may contain only letters, digits, dots, underscores and hyphens."
        )
    if not normalized[0].isalnum():
        raise ValidationFailed("Username must start with a letter or digit.")
    return normalized


# ---------------------------------------------------------------- seeding RBAC


def seed_permissions(session: Session) -> dict[str, int]:
    """Reconcile the permission and role catalogue into the database.

    Idempotent. Permissions absent from the catalogue are left in place rather
    than deleted, because a custom role may still grant them; removing a
    permission is an explicit migration, not a side effect of seeding.
    """
    validate_permission_catalogue()

    existing_perms = {p.code: p for p in session.scalars(select(Permission)).all()}
    created_perms = 0
    for definition in PERMISSION_CATALOGUE:
        row = existing_perms.get(definition.code)
        if row is None:
            row = Permission(
                code=definition.code,
                module=definition.module,
                description=definition.description,
            )
            session.add(row)
            existing_perms[definition.code] = row
            created_perms += 1
        else:
            row.module = definition.module
            row.description = definition.description
    session.flush()

    existing_roles = {r.code: r for r in session.scalars(select(Role)).all()}
    created_roles = 0
    grants_added = 0
    grants_removed = 0

    for role_def in ROLES_BY_CODE.values():
        role = existing_roles.get(role_def.code)
        if role is None:
            role = Role(
                code=role_def.code,
                name=role_def.name,
                description=role_def.description,
                is_system=role_def.is_system,
                is_active=True,
            )
            session.add(role)
            session.flush()
            created_roles += 1
        else:
            role.name = role_def.name
            role.description = role_def.description
            role.is_system = role_def.is_system

        added, removed = _sync_role_grants(session, role, role_def, existing_perms)
        grants_added += added
        grants_removed += removed

    session.flush()
    return {
        "permissions_created": created_perms,
        "permissions_total": len(PERMISSION_CATALOGUE),
        "roles_created": created_roles,
        "grants_added": grants_added,
        "grants_removed": grants_removed,
    }


def _sync_role_grants(
    session: Session,
    role: Role,
    role_def: RoleDef,
    permissions_by_code: dict[str, Permission],
) -> tuple[int, int]:
    """Make a system role's grants match its declaration.

    Only system roles are reconciled this way. Custom roles created by an
    administrator are never touched by seeding.
    """
    current_links = {link.permission.code: link for link in role.permissions}
    desired = set(role_def.permission_codes)

    added = 0
    for code in desired - set(current_links):
        permission = permissions_by_code.get(code)
        if permission is None:
            continue
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        added += 1

    removed = 0
    if role_def.is_system:
        for code in set(current_links) - desired:
            session.delete(current_links[code])
            removed += 1

    return added, removed


# -------------------------------------------------------------- provisioning


@dataclass(frozen=True, slots=True)
class ProvisionedAccount:
    user: User
    temporary_password: str


def create_user(
    session: Session,
    *,
    username: str,
    email: str | None,
    role_codes: list[str],
    context: audit.AuditContext,
    employee_id: int | None = None,
    password: str | None = None,
    must_change_password: bool = True,
) -> ProvisionedAccount:
    """Create a sign-in account. There is no public registration path.

    When ``password`` is omitted a temporary password is generated and returned
    to the caller **once**; it is not stored in plaintext anywhere and never
    appears in logs or the audit trail.
    """
    normalized = validate_username(username)

    if session.scalar(select(func.count()).select_from(User).where(User.username == normalized)):
        raise Conflict(
            f"Username {normalized!r} is already taken.",
            details=[{"field": "username", "message": "Already in use."}],
        )

    clean_email = email.strip().lower() if email else None
    if clean_email and session.scalar(
        select(func.count()).select_from(User).where(User.email == clean_email)
    ):
        raise Conflict(
            "That email address is already linked to another account.",
            details=[{"field": "email", "message": "Already in use."}],
        )

    temporary_password = password or generate_temporary_password()
    validate_password_strength(temporary_password, username=normalized)

    roles = _resolve_roles(session, role_codes)

    user = User(
        username=normalized,
        email=clean_email,
        password_hash=hash_password(temporary_password),
        must_change_password=must_change_password,
        is_active=True,
        employee_id=employee_id,
    )
    session.add(user)
    session.flush()

    now = utc_now()
    for role in roles:
        session.add(
            UserRole(
                user_id=user.id,
                role_id=role.id,
                assigned_at=now,
                assigned_by_user_id=context.actor_user_id,
            )
        )

    audit.record(
        session,
        action="auth.user_created",
        context=context,
        entity_type="user",
        entity_id=user.id,
        after={
            "username": user.username,
            "email": user.email,
            "roles": sorted(r.code for r in roles),
            # Named to avoid the substring "password", which the audit redactor
            # blanks by key name. The fact that temporary credentials were
            # issued is worth keeping in the trail; the credentials are not.
            "temporary_credentials_issued": must_change_password,
        },
    )
    session.flush()
    return ProvisionedAccount(user=user, temporary_password=temporary_password)


def _resolve_roles(session: Session, role_codes: list[str]) -> list[Role]:
    if not role_codes:
        raise ValidationFailed("At least one role must be assigned to an account.")
    roles = list(session.scalars(select(Role).where(Role.code.in_(role_codes))).all())
    found = {r.code for r in roles}
    missing = set(role_codes) - found
    if missing:
        raise ValidationFailed(
            f"Unknown role(s): {', '.join(sorted(missing))}.",
            details=[{"field": "role_codes", "message": "Run 'iams seed-permissions'."}],
        )
    return roles


# ------------------------------------------------------------------ sign-in


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access: AccessToken
    refresh_token: str
    refresh_expires_at: object
    must_change_password: bool


def _build_attempt(
    *,
    identifier: str,
    succeeded: bool,
    user_id: int | None,
    failure_reason: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> LoginAttempt:
    return LoginAttempt(
        identifier=identifier[:100],
        user_id=user_id,
        succeeded=succeeded,
        failure_reason=failure_reason,
        ip_address=ip_address,
        user_agent=user_agent[:255] if user_agent else None,
        attempted_at=utc_now(),
    )


def _record_success(session: Session, **kwargs: object) -> None:
    """Record a successful attempt inside the caller's transaction."""
    session.add(_build_attempt(succeeded=True, failure_reason=None, **kwargs))  # type: ignore[arg-type]


def _record_failure_durably(
    *,
    identifier: str,
    failure_reason: str,
    user_id: int | None = None,
    actor_label: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    count_against_user: bool = False,
) -> None:
    """Persist a failed sign-in in its own committed transaction.

    This must not share the request's transaction. Every failure path ends in an
    exception, the request dependency rolls that transaction back, and both the
    attempt row and the per-account failure counter would vanish with it -
    silently disabling rate limiting and lockout, and leaving no trace of a
    brute-force attempt. So all of it is committed independently here.

    ``count_against_user`` additionally increments ``users.failed_login_count``
    and applies the lockout once the threshold is reached. The increment is an
    in-database ``UPDATE`` rather than a read-modify-write on an ORM object, so
    concurrent attempts cannot lose counts to a race.

    A failure to write never masks the authentication error the caller raises.
    """
    from sqlalchemy import case, update

    from app.db.session import session_scope

    settings = get_settings()
    try:
        with session_scope() as side_session:
            side_session.add(
                _build_attempt(
                    identifier=identifier,
                    succeeded=False,
                    user_id=user_id,
                    failure_reason=failure_reason,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )

            if count_against_user and user_id is not None:
                next_count = User.failed_login_count + 1
                side_session.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(
                        failed_login_count=next_count,
                        locked_until=case(
                            (
                                next_count >= settings.login_max_attempts,
                                utc_now() + timedelta(minutes=settings.login_lockout_minutes),
                            ),
                            else_=User.locked_until,
                        ),
                    )
                )

            if user_id is not None:
                audit.record(
                    side_session,
                    action="auth.login_failed",
                    context=audit.AuditContext(
                        actor_user_id=user_id,
                        actor_label=actor_label,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        request_id=request_id,
                    ),
                    entity_type="user",
                    entity_id=user_id,
                    succeeded=False,
                    extra={"reason": failure_reason},
                )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to persist a login failure record")


def recent_failed_attempts(session: Session, identifier: str, ip_address: str | None) -> int:
    """Failed attempts for this identifier or IP inside the configured window."""
    settings = get_settings()
    since = utc_now() - timedelta(minutes=settings.login_attempt_window_minutes)
    condition = LoginAttempt.identifier == identifier
    if ip_address:
        condition = condition | (LoginAttempt.ip_address == ip_address)
    return (
        session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.attempted_at >= since,
                condition,
            )
        )
        or 0
    )


def find_user_by_identifier(session: Session, identifier: str) -> User | None:
    """Resolve a sign-in identifier.

    Three forms are accepted, in order of cost:

    1. **Username** - the canonical, lowercased account name.
    2. **Email** - either the account email or the employee's work email.
    3. **Employee code** - e.g. ``ITC0007``, via the linked employee record.

    The brief requires sign-in by "employee ID or username", and employees know
    their employee code far better than a username HR chose for them.

    The employee-code lookup is a separate query rather than an OR across a join,
    so an unlinked account (the bootstrap administrator) still resolves on the
    first query without paying for a join.
    """
    normalized = normalize_username(identifier)

    user = session.scalar(
        select(User).where((User.username == normalized) | (User.email == normalized))
    )
    if user is not None:
        return user

    # Imported here rather than at module scope: the auth module must remain
    # importable before the employees module exists in a fresh database, and
    # a top-level import would make the two modules mutually dependent.
    from app.modules.employees.models import Employee

    return session.scalar(
        select(User)
        .join(Employee, Employee.id == User.employee_id)
        .where(
            (Employee.employee_code == identifier.strip().upper())
            | (func.lower(Employee.work_email) == normalized)
        )
    )


def authenticate(
    session: Session,
    *,
    identifier: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
) -> SessionTokens:
    """Verify credentials and issue a session.

    Every failure path returns the same :class:`AuthenticationFailed` message and
    performs comparable work, so the response cannot be used to discover whether
    an account exists.

    ``request_id`` is carried into the audit entries so a reported sign-in
    problem can be traced from the client's request to the server's record of it.
    """
    settings = get_settings()
    normalized = normalize_username(identifier)

    if recent_failed_attempts(session, normalized, ip_address) >= settings.login_max_attempts:
        _record_failure_durably(
            identifier=normalized,
            failure_reason="rate_limited",
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        raise AccountLocked(
            f"Too many failed sign-in attempts. Try again in "
            f"{settings.login_lockout_minutes} minutes."
        )

    user = find_user_by_identifier(session, normalized)

    if user is None:
        # Hash the submitted password anyway: skipping it would make the
        # unknown-account path measurably faster and leak account existence.
        hash_password(password)
        _record_failure_durably(
            identifier=normalized,
            failure_reason="unknown_user",
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        raise AuthenticationFailed()

    now = utc_now()
    if user.locked_until and user.locked_until > now:
        _record_failure_durably(
            identifier=normalized,
            failure_reason="locked",
            user_id=user.id,
            actor_label=user.username,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        raise AccountLocked()

    if not verify_password(password, user.password_hash):
        _record_failure_durably(
            identifier=normalized,
            failure_reason="bad_password",
            user_id=user.id,
            actor_label=user.username,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            count_against_user=True,
        )
        raise AuthenticationFailed()

    # Credentials are correct past this point.
    if not user.is_active:
        _record_failure_durably(
            identifier=normalized,
            failure_reason="inactive",
            user_id=user.id,
            actor_label=user.username,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        raise AccountInactive()

    if needs_rehash(user.password_hash):
        # Transparently upgrade to the current Argon2 parameters.
        user.password_hash = hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    tokens = issue_session(session, user=user, ip_address=ip_address, user_agent=user_agent)

    _record_success(
        session,
        identifier=normalized,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    audit.record(
        session,
        action="auth.login_succeeded",
        context=audit.AuditContext(
            actor_user_id=user.id,
            actor_label=user.username,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        ),
        entity_type="user",
        entity_id=user.id,
    )
    session.flush()
    return tokens


def issue_session(
    session: Session,
    *,
    user: User,
    ip_address: str | None,
    user_agent: str | None,
    replaces: RefreshToken | None = None,
) -> SessionTokens:
    settings = get_settings()
    now = utc_now()
    raw_refresh = generate_refresh_token()
    expires_at = now + timedelta(days=settings.refresh_token_ttl_days)

    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        issued_at=now,
        expires_at=expires_at,
        user_agent=user_agent[:255] if user_agent else None,
        ip_address=ip_address,
    )
    session.add(record)
    session.flush()

    if replaces is not None:
        replaces.revoked_at = now
        replaces.revoked_reason = "rotated"
        replaces.replaced_by_id = record.id

    return SessionTokens(
        access=create_access_token(user.id),
        refresh_token=raw_refresh,
        refresh_expires_at=expires_at,
        must_change_password=user.must_change_password,
    )


def refresh_session(
    session: Session,
    *,
    raw_refresh_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> SessionTokens:
    """Exchange a refresh token for a new pair, rotating the old one.

    Replaying a token that was already **rotated** revokes every session for that
    user. Rotation hands the client a successor, so the old value should never be
    sent again; seeing it means either a stolen token or a replay, and ending all
    sessions is the safe response.

    A token revoked for any *other* reason - logout, a password change, account
    deactivation - is simply rejected. That distinction matters: a browser tab
    left open after signing out will retry with its stale token, and treating
    that as theft would log the user out of every other device for no reason.
    ``replaced_by_id`` is the signal, because only rotation sets it.
    """
    digest = hash_refresh_token(raw_refresh_token)
    record = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))

    if record is None:
        raise AuthenticationFailed("Invalid or expired session. Please sign in again.")

    now = utc_now()
    if record.revoked_at is not None:
        if record.replaced_by_id is not None:
            # Replay of a rotated token. Revoke everything.
            #
            # This must be committed in its own transaction. The request ends in
            # an exception, so the dependency rolls its transaction back - and
            # the revocations, the whole point of detecting reuse, would be
            # undone with it, leaving the thief's sessions alive.
            _revoke_all_sessions_durably(
                user_id=record.user_id,
                reason="reuse_detected",
                ip_address=ip_address,
                user_agent=user_agent,
                triggering_reason=record.revoked_reason,
            )
        raise AuthenticationFailed("Session is no longer valid. Please sign in again.")

    if record.expires_at <= now:
        raise AuthenticationFailed("Your session has expired. Please sign in again.")

    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AccountInactive()

    tokens = issue_session(
        session, user=user, ip_address=ip_address, user_agent=user_agent, replaces=record
    )
    session.flush()
    return tokens


def revoke_session(session: Session, *, raw_refresh_token: str, reason: str = "logout") -> bool:
    digest = hash_refresh_token(raw_refresh_token)
    record = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = utc_now()
    record.revoked_reason = reason
    session.flush()
    return True


def _revoke_all_sessions_durably(
    *,
    user_id: int,
    reason: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    triggering_reason: str | None = None,
) -> int:
    """Revoke every session for a user in its own committed transaction.

    Used on the token-reuse path, where the caller is about to raise and the
    request transaction will be rolled back. A revocation that does not survive
    that rollback is no revocation at all.
    """
    from app.db.session import session_scope

    try:
        with session_scope() as side_session:
            revoked = revoke_all_sessions(side_session, user_id=user_id, reason=reason)
            audit.record(
                side_session,
                action="auth.refresh_token_reuse_detected",
                context=audit.AuditContext(
                    actor_user_id=user_id, ip_address=ip_address, user_agent=user_agent
                ),
                entity_type="user",
                entity_id=user_id,
                succeeded=False,
                extra={
                    "revoked_sessions": revoked,
                    "previous_revoked_reason": triggering_reason,
                },
            )
            return revoked
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to revoke sessions after refresh-token reuse")
        return 0


def revoke_all_sessions(session: Session, *, user_id: int, reason: str) -> int:
    now = utc_now()
    tokens = list(
        session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        ).all()
    )
    for token in tokens:
        token.revoked_at = now
        token.revoked_reason = reason
    session.flush()
    return len(tokens)


# ------------------------------------------------------------------ passwords


def change_own_password(
    session: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    context: audit.AuditContext,
) -> None:
    """Change the acting user's own password and end their other sessions."""
    if not verify_password(current_password, user.password_hash):
        raise AuthenticationFailed(
            "Your current password is incorrect.",
            details=[{"field": "current_password", "message": "Incorrect."}],
        )
    if current_password == new_password:
        raise ValidationFailed(
            "The new password must be different from the current one.",
            details=[{"field": "new_password", "message": "Must differ from the current one."}],
        )

    validate_password_strength(new_password, username=user.username)

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.password_changed_at = utc_now()

    # A password change should not leave older sessions alive.
    revoke_all_sessions(session, user_id=user.id, reason="password_changed")

    audit.record(
        session,
        action="auth.password_changed",
        context=context,
        entity_type="user",
        entity_id=user.id,
    )
    # A security notice the account holder should see even though they just did
    # it themselves: if they did not, this is how they find out.
    notification_hooks.password_changed(
        session, user_id=user.id, changed_at_iso=user.password_changed_at.isoformat()
    )
    session.flush()


def reset_password_for_user(
    session: Session,
    *,
    target_user_id: int,
    context: audit.AuditContext,
) -> str:
    """HR-initiated reset: issue a temporary password and force a change.

    Returns the temporary password to the caller once, for out-of-band delivery.
    No email or SMS provider is required.
    """
    user = session.get(User, target_user_id)
    if user is None:
        raise NotFound("User not found.")

    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.password_changed_at = utc_now()
    user.failed_login_count = 0
    user.locked_until = None

    revoke_all_sessions(session, user_id=user.id, reason="password_reset_by_admin")

    audit.record(
        session,
        action="auth.password_reset_by_admin",
        context=context,
        entity_type="user",
        entity_id=user.id,
    )
    notification_hooks.password_changed(
        session, user_id=user.id, changed_at_iso=user.password_changed_at.isoformat()
    )
    session.flush()
    return temporary


def set_user_active(
    session: Session,
    *,
    target_user_id: int,
    is_active: bool,
    reason: str | None,
    context: audit.AuditContext,
) -> User:
    user = session.get(User, target_user_id)
    if user is None:
        raise NotFound("User not found.")

    was_active = user.is_active
    user.is_active = is_active
    if not is_active:
        user.deactivated_at = utc_now()
        user.deactivated_reason = reason
        revoke_all_sessions(session, user_id=user.id, reason="account_deactivated")
    else:
        user.deactivated_at = None
        user.deactivated_reason = None

    audit.record(
        session,
        action="auth.user_activated" if is_active else "auth.user_deactivated",
        context=context,
        entity_type="user",
        entity_id=user.id,
        before={"is_active": was_active},
        after={"is_active": is_active},
        extra={"reason": reason},
    )
    session.flush()
    return user


def describe_permission(code: str) -> PermissionDef | None:
    from app.core.permissions import PERMISSIONS_BY_CODE

    return PERMISSIONS_BY_CODE.get(code)
