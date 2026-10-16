"""Identity and access tables: users, roles, permissions, sessions, login attempts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKey, TimestampMixin


class Permission(Base):
    """A fine-grained capability. Rows are reconciled from ``app.core.permissions``."""

    __tablename__ = "permissions"

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    roles: Mapped[list[RolePermission]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )


class Role(Base, TimestampMixin):
    """A named bundle of permissions.

    ``is_system`` marks the two roles the application depends on (``hr_admin``,
    ``employee``). They may gain or lose permissions but must not be deleted,
    otherwise provisioning and self-service break.
    """

    __tablename__ = "roles"

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )
    users: Mapped[list[UserRole]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    @property
    def permission_codes(self) -> set[str]:
        return {link.permission.code for link in self.permissions}


class RolePermission(Base):
    """Grant of one permission to one role."""

    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    id: Mapped[PrimaryKey]
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship(back_populates="roles", lazy="joined")


class User(Base, TimestampMixin):
    """A sign-in identity.

    ``employee_id`` is nullable on purpose: the bootstrap administrator is created
    before any employee record exists, and a dedicated system/integration account
    may never correspond to a person. Employee-scoped endpoints require the link
    and fail cleanly when it is absent.

    Accounts are deactivated, never deleted, so that attendance, leave, payroll
    and audit history keep a resolvable actor after an employee leaves.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_non_negative"),
        # The lowercase constraints make the case-insensitive uniqueness of
        # username and email a database guarantee, not merely a service-layer
        # convention that a CLI command or a manual fix could bypass.
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        CheckConstraint("email IS NULL OR email = lower(email)", name="email_lowercase"),
        {"comment": "Sign-in identities. Deactivate rather than delete."},
    )

    id: Mapped[PrimaryKey]

    # Employees sign in with either their username or their employee code; both
    # are unique, and the login service accepts either. Usernames are normalised
    # to lowercase on write (see auth.service.normalize_username) so that this
    # plain unique constraint gives case-insensitive uniqueness.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Failed-attempt tracking for lockout. Reset on a successful sign-in.
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Nullable: the bootstrap administrator exists before any employee record,
    # and an integration account may never correspond to a person. Unique, so
    # one employee cannot have two sign-in accounts. RESTRICT rather than
    # CASCADE because employees are never deleted - if a delete is ever
    # attempted, failing is the correct outcome.
    employee_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
        unique=True,
    )

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="UserRole.user_id",
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="RefreshToken.user_id",
    )

    @property
    def role_codes(self) -> set[str]:
        return {link.role.code for link in self.roles}

    @property
    def permission_codes(self) -> set[str]:
        """Union of permissions from all active roles.

        Read fresh from the database on each request rather than cached in the
        access token, so that revoking a role takes effect immediately.
        """
        codes: set[str] = set()
        for link in self.roles:
            if link.role.is_active:
                codes |= link.role.permission_codes
        return codes

    def has_permission(self, code: str) -> bool:
        return code in self.permission_codes


class UserRole(Base):
    """Assignment of one role to one user."""

    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    id: Mapped[PrimaryKey]
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")


class RefreshToken(Base):
    """A refresh-token grant.

    Only the SHA-256 digest is stored. Rotation sets ``replaced_by_id`` so a
    replayed, already-rotated token is detectable - the standard signal of token
    theft - and lets us revoke the whole chain.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id_expires_at", "user_id", "expires_at"),
        # Live sessions only. Revoked rows are kept for forensics and would
        # otherwise dominate this index, which is read on every token refresh.
        Index(
            "ix_refresh_tokens_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        CheckConstraint("expires_at > issued_at", name="expires_after_issue"),
    )

    id: Mapped[PrimaryKey]
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    replaced_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )

    # Coarse client fingerprint for the user's session list. Deliberately not a
    # precise device identifier, and never used as an authentication factor.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens", foreign_keys=[user_id])

    def is_usable(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now


class LoginAttempt(Base):
    """Audit of sign-in attempts, and the basis for rate limiting.

    Keyed on the *submitted* identifier rather than a resolved user id, so that
    attempts against non-existent usernames are throttled too and the response
    time does not reveal whether an account exists.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_identifier_attempted_at", "identifier", "attempted_at"),
        Index("ix_login_attempts_ip_address_attempted_at", "ip_address", "attempted_at"),
    )

    id: Mapped[PrimaryKey]
    identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
