"""Attendance events, computed days, sessions and regularization requests.

The central separation
----------------------
``attendance_events`` is the **raw, immutable record** of what was punched.
``attendance_days`` is a **derived summary** that can be thrown away and
recomputed from the events at any time.

That split is what makes the engine trustworthy:

* A policy change (grace period, rounding, thresholds) is applied by
  recomputing, not by editing figures.
* A correction is visibly a correction - HR adds an event marked
  ``source='manual'`` with a reason, and the summary is recalculated. The
  original machine or web punches are never altered or deleted.
* A bug in the calculation is fixable retroactively, because the inputs survive.

``attendance_days.computed_from_event_count`` and the stored policy snapshot let
a past figure be explained in terms of the inputs and rules that produced it.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Jsonb, PrimaryKey, TimestampMixin

EVENT_TYPES = ("in", "out")

#: Where a punch came from. ``web`` is the self-service button, ``device`` an
#: attendance machine (Milestone 4), ``import`` a CSV load, ``manual`` an HR
#: correction, ``regularization`` an approved employee request.
EVENT_SOURCES = ("web", "device", "import", "manual", "regularization")

#: Computed status for a day. Deliberately a small, closed set: every additional
#: status becomes another branch in payroll.
#:
#: ``absent`` with ``worked_minutes > 0`` is the "turned up but under the
#: half-day threshold" case - the minutes are retained for reporting even though
#: the day does not count.
ATTENDANCE_STATUSES = (
    "present",
    "half_day",
    "absent",
    "on_leave",
    "compoff",
    "holiday",
    "weekly_off",
    "incomplete",
    "missing_punch",
)

#: What kind of day the calendar said it was, before attendance is considered.
DAY_TYPES = ("working", "weekly_off", "holiday")

REGULARIZATION_STATUSES = (
    "pending_manager",
    "pending_hr",
    "approved",
    "rejected",
    "cancelled",
)

REGULARIZATION_STAGES = ("reporting_manager", "hr_admin", "complete")


class AttendanceEvent(Base):
    """One punch. Append-only; never updated.

    ``occurred_at`` is always a server-generated or HR-entered UTC instant. The
    browser clock is never trusted - a client-supplied timestamp would let an
    employee punch in for a time they were not there.

    ``unique(source, external_event_id)`` is the idempotency mechanism: a
    redelivered webhook, a re-imported CSV or a double-clicked button cannot
    create a second punch.
    """

    __tablename__ = "attendance_events"
    __table_args__ = (
        UniqueConstraint("source", "external_event_id"),
        CheckConstraint(f"event_type IN {EVENT_TYPES}", name="event_type_known"),
        CheckConstraint(f"source IN {EVENT_SOURCES}", name="source_known"),
        # A manual or regularization event must say why it exists.
        CheckConstraint(
            "source NOT IN ('manual', 'regularization') OR reason IS NOT NULL",
            name="manual_events_need_a_reason",
        ),
        Index("ix_attendance_events_employee_id_occurred_at", "employee_id", "occurred_at"),
        Index("ix_attendance_events_employee_id_work_date", "employee_id", "work_date"),
        # Every computation reads only the live events for a day. Superseded
        # rows are kept for audit and would otherwise grow the index that the
        # hot path uses.
        Index(
            "ix_attendance_events_live",
            "employee_id",
            "work_date",
            postgresql_where=text("NOT is_superseded"),
        ),
        {"comment": "Append-only raw punches. Never UPDATE or DELETE; supersede instead."},
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    event_type: Mapped[str] = mapped_column(String(10), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """UTC. Server-generated for web punches."""

    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    """
    The shift date this punch is attributed to, derived in the employee's
    work-location timezone. For an overnight shift this is the date the shift
    *started*, so a 02:00 punch on the 5th belongs to the 4th.
    """

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """Device/import identifier. NULL for web punches, which use the PK."""

    device_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """
    Which device recorded this punch. ``ON DELETE SET NULL`` on purpose: deleting
    a decommissioned device must not delete the attendance it recorded, and the
    event itself is append-only.
    """

    timezone_used: Mapped[str] = mapped_column(String(64), nullable=False)
    """
    The IANA zone the work date was derived in, recorded so the attribution can
    be re-explained even if the employee later moves location.
    """

    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: An event can be superseded (e.g. an HR correction replaces a bad punch)
    #: without being deleted, so the original stays auditable.
    is_superseded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attendance_events.id", ondelete="SET NULL"), nullable=True
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """Original device payload, retained verbatim for reconciliation."""

    recorded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AttendanceDay(Base, TimestampMixin):
    """Computed attendance summary for one employee on one shift date.

    Entirely derived from ``attendance_events`` plus the shift, calendar and
    policy in force. Safe to delete and rebuild.

    No row at all means "not applicable" - before joining, after the last
    working day, or not yet computed. That is deliberately distinct from a row
    with status ``absent``.
    """

    __tablename__ = "attendance_days"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date"),
        CheckConstraint(f"status IN {ATTENDANCE_STATUSES}", name="status_known"),
        CheckConstraint(f"day_type IN {DAY_TYPES}", name="day_type_known"),
        CheckConstraint(
            "worked_minutes >= 0 AND break_minutes >= 0 AND net_minutes >= 0 "
            "AND late_minutes >= 0 AND early_exit_minutes >= 0 AND overtime_minutes >= 0",
            name="minutes_non_negative",
        ),
        CheckConstraint(
            "payable_day_fraction >= 0 AND payable_day_fraction <= 1",
            name="payable_fraction_in_range",
        ),
        Index("ix_attendance_days_work_date_status", "work_date", "status"),
        Index("ix_attendance_days_employee_id_work_date_desc", "employee_id", "work_date"),
        # Backs the reconciliation queue, which is a small slice of a large
        # table.
        Index(
            "ix_attendance_days_open_anomalies",
            "work_date",
            postgresql_where=text("has_missing_punch OR anomalies IS NOT NULL"),
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)

    day_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    shift_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True
    )
    """The shift actually used. Retained so the figure stays explicable."""

    first_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    worked_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Sum of completed in/out session durations."""

    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Gaps between sessions, plus any automatic deduction that was applied."""

    net_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Worked minutes after break deduction and rounding. The figure policy uses."""

    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Measured lateness against shift start, regardless of the grace period."""

    early_exit_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    #: Judgments, separated from the measurements above so a report can show
    #: actual lateness while policy decides whether it counts.
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_early_exit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    has_missing_punch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    payable_day_fraction: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("0")
    )
    """
    1.00 present, 0.50 half day, 0 absent. Decimal, not float: payroll
    multiplies by this, and it must sum exactly over a month.

    Payroll uses this rather than re-deriving a fraction from ``status``, so the
    two cannot disagree.
    """

    leave_application_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    """FK added in Milestone 5 with the leave module."""

    is_manual_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """True when HR set the status directly; recomputation leaves these alone."""

    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computed_from_event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """
    The thresholds, grace periods and rounding that produced this row. Without
    it, a figure computed under an old policy cannot be explained after the
    policy changes.
    """

    anomalies: Mapped[list[str] | None] = mapped_column(Jsonb, nullable=True)
    """
    Data problems found while computing: unpaired punches, out-before-in, and so
    on. Surfaced for reconciliation rather than silently discarded.
    """

    sessions: Mapped[list[AttendanceSession]] = relationship(
        back_populates="attendance_day", cascade="all, delete-orphan"
    )


class AttendanceSession(Base):
    """One completed in/out pair within a day.

    Stored rather than only summed so that "when were you actually here" is
    answerable, which matters for multi-session days and for disputes.
    """

    __tablename__ = "attendance_sessions"
    __table_args__ = (
        CheckConstraint("ended_at > started_at", name="session_ordered"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_attendance_sessions_attendance_day_id", "attendance_day_id"),
    )

    id: Mapped[PrimaryKey]
    attendance_day_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("attendance_days.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    in_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attendance_events.id", ondelete="SET NULL"), nullable=True
    )
    out_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attendance_events.id", ondelete="SET NULL"), nullable=True
    )

    attendance_day: Mapped[AttendanceDay] = relationship(back_populates="sessions")


class AttendanceRegularization(Base, TimestampMixin):
    """An employee's request to correct their own attendance.

    Follows the same two-stage chain as leave: reporting manager, then HR/Admin.
    An employee can never approve their own request, and approval does not edit
    the summary directly - it inserts ``source='regularization'`` events and
    recomputes, so the correction is visible in the raw record.
    """

    __tablename__ = "attendance_regularizations"
    __table_args__ = (
        CheckConstraint(f"status IN {REGULARIZATION_STATUSES}", name="status_known"),
        CheckConstraint(f"current_stage IN {REGULARIZATION_STAGES}", name="stage_known"),
        CheckConstraint(
            "requested_out_at IS NULL OR requested_in_at IS NULL "
            "OR requested_out_at > requested_in_at",
            name="requested_times_ordered",
        ),
        CheckConstraint(
            "requested_in_at IS NOT NULL OR requested_out_at IS NOT NULL",
            name="at_least_one_requested_time",
        ),
        # One open request per employee per date; a decided one does not block.
        Index(
            "ix_attendance_regularizations_open_unique",
            "employee_id",
            "work_date",
            unique=True,
            postgresql_where=text("status IN ('pending_manager', 'pending_hr')"),
        ),
        Index("ix_attendance_regularizations_status", "status"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False)

    requested_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending_manager'")
    )
    current_stage: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'reporting_manager'")
    )

    #: Snapshot of the day as it stood when the request was raised, so a
    #: reviewer can see what is being changed and from what.
    status_before: Mapped[str | None] = mapped_column(String(20), nullable=True)

    manager_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    manager_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    manager_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    hr_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    hr_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    hr_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    hr_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    escalated_from_missing_manager: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """True when the manager stage was skipped because there was no manager."""
