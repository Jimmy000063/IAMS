"""Business policy configuration, stored as data rather than code.

Why this exists
---------------
The brief requires company defaults (working week, leave year, approval chain,
grace periods) to live "in configuration or policy records rather than embedded
throughout application code". This table is that store for *scalar, company-wide*
settings.

It is intentionally **not** the store for everything:

* Scalar, single-valued, company-wide → ``system_settings`` (this table).
* Rich, effective-dated, per-employee rules → their own tables with
  ``effective_from`` / ``effective_to`` (``leave_policy_rules``, ``shifts``,
  ``statutory_rule_sets``). A key/value row cannot express "12% of PF wages,
  capped at X, from 1 April, for employees hired before Y".

Every change is written to ``setting_change_history`` *and* ``audit_logs``, so a
past attendance or payroll calculation can be explained in terms of the values
that were in force at the time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Jsonb, PrimaryKey, TimestampMixin


class SystemSetting(Base, TimestampMixin):
    """One configurable company-wide setting.

    The value is stored as JSONB with a declared ``value_type`` so that the API
    can validate and the UI can render an appropriate control. ``is_confirmed``
    distinguishes a value the company has actually decided from a placeholder
    default that merely keeps the system running - unconfirmed settings are
    surfaced in the open-items register and on the settings screen.
    """

    __tablename__ = "system_settings"
    __table_args__ = (
        CheckConstraint(
            "value_type IN ('string','integer','decimal','boolean','time','date','json','enum')",
            name="value_type_known",
        ),
    )

    id: Mapped[PrimaryKey]
    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    value: Mapped[dict[str, Any]] = mapped_column(Jsonb, nullable=False)
    """Wrapped as ``{"value": <json>}`` so scalars, lists and objects share a column."""

    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """One of: string, integer, decimal, boolean, time, date, json, enum."""

    default_value: Mapped[dict[str, Any]] = mapped_column(Jsonb, nullable=False)
    constraints: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """Validation metadata: ``min``, ``max``, ``choices``, ``unit``."""

    is_editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """False for values the application derives or that require a migration."""

    is_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """True once the company has explicitly confirmed this value."""

    requires_legal_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """True when the value has statutory consequences and must be verified."""

    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Where a confirmed value came from: policy document, circular, notification."""

    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    history: Mapped[list[SettingChangeHistory]] = relationship(
        back_populates="setting", cascade="all, delete-orphan"
    )

    @property
    def unwrapped_value(self) -> Any:
        return self.value.get("value") if isinstance(self.value, dict) else self.value


class SettingChangeHistory(Base):
    """Append-only record of every settings change.

    Kept alongside ``audit_logs`` rather than relying on it alone, because
    attendance and payroll explanations need to query "what was the grace period
    on 3 March" efficiently, which is awkward against a generic audit table.
    """

    __tablename__ = "setting_change_history"

    id: Mapped[PrimaryKey]
    setting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("system_settings.id", ondelete="CASCADE"), nullable=False
    )
    setting_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    new_value: Mapped[dict[str, Any]] = mapped_column(Jsonb, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    setting: Mapped[SystemSetting] = relationship(back_populates="history")
