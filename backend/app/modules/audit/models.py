"""Audit trail and scheduled-job bookkeeping."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Jsonb, PrimaryKey


class AuditLog(Base):
    """Append-only record of security- and money-relevant operations.

    Rows are never updated or deleted by application code. The table carries no
    ``updated_at`` for that reason.

    What gets logged: authentication events, account and role changes, settings
    changes, attendance corrections, leave and comp-off decisions, salary
    revisions, statutory configuration changes, payroll state transitions,
    document access.

    What must never reach ``before_value``/``after_value``: passwords, token
    values, full bank account numbers, or any personal data not needed to explain
    the change. Writers pass through
    :func:`app.modules.audit.service.redact_sensitive`.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor_user_id_created_at", "actor_user_id", "created_at"),
        {"comment": "Append-only. Never UPDATE or DELETE rows in this table."},
    )

    id: Mapped[PrimaryKey]

    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    """Dotted verb, e.g. ``auth.login_succeeded``, ``payroll.run_finalized``."""

    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    """String rather than integer so composite or natural keys can be recorded."""

    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """Denormalised username, so the entry stays readable if the user row is gone."""

    actor_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    actor_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    before_value: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    after_value: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """Extra facts: reason text, approval stage, affected period."""

    succeeded: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class JobRun(Base):
    """One execution of a scheduled or manually triggered background job.

    ``idempotency_key`` is the mechanism that makes re-running safe. A job
    computes a deterministic key from its name and the period it covers (for
    example ``leave_accrual:2026-04``) and inserts this row first. The unique
    constraint then makes a duplicate run fail at the database level, which is
    what prevents double leave credits or duplicate encashment entries when a
    scheduler retries.
    """

    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_job_name_started_at", "job_name", "started_at"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','skipped')", name="status_known"
        ),
        CheckConstraint(
            "records_processed >= 0 AND records_skipped >= 0 AND records_failed >= 0",
            name="counters_non_negative",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"
        ),
    )

    id: Mapped[PrimaryKey]
    job_name: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    period_key: Mapped[str | None] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    """running | succeeded | failed | skipped"""

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    result: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    triggered_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
