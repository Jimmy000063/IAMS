"""Compensatory off: credits earned from qualifying work, and their consumption.

Why lots rather than a ledger
-----------------------------
Leave uses an append-only ledger because accrued leave is **fungible** - a day
is a day, and the balance is a sum. Comp-off is not: each credit is earned on a
specific date and **expires on its own date**, so consuming "one day" has to say
*which* credit it drew on. Otherwise expiry cannot be computed correctly - the
system would not know whether the day an employee just used was the one about to
lapse or the one earned last week.

So comp-off is modelled as **lots and allocations**:

* ``compoff_credits`` is a lot: earned on a date, worth a quantity, expiring on a
  date, with the attendance day that proves it.
* ``compoff_allocations`` records which lots an application drew on, and how much
  from each.

A balance is then ``SUM(credit_days) - SUM(allocated)`` over live lots, which is
both explainable and lot-accurate. A separate signed ledger would be a third
representation of the same facts and could disagree with the lots; one
representation that cannot drift is better than two that can.

Credits are consumed **earliest-expiring first**, so an employee never loses a
credit that could have been used.

The proof requirement
---------------------
A credit cites ``attendance_day_id``. The brief requires comp-off to come from
*verified qualifying work*, and the attendance day is that verification: it
records the day type and the minutes actually worked, derived from punches. A
credit with no attendance day behind it is only possible as an explicit manual
grant, which is flagged as such.
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

from app.db.base import Base, Days, Jsonb, PrimaryKey, TimestampMixin

#: Lifecycle of a credit lot.
#:
#: ``pending_verification`` is the default when HR verification is required: the
#: work happened, but the credit is not usable until someone confirms it.
CREDIT_STATUSES = (
    "pending_verification",
    "available",
    "rejected",
    "expired",
    "reversed",
)

#: Why a credit exists.
CREDIT_SOURCES = ("attendance", "manual")

#: Application lifecycle, mirroring leave so the two feel the same to a user.
APPLICATION_STATUSES = (
    "pending_manager",
    "pending_hr",
    "approved",
    "rejected",
    "withdrawn",
    "cancelled",
)

APPROVAL_STAGES = ("reporting_manager", "hr_admin")
APPROVAL_DECISIONS = ("approved", "rejected", "skipped")
DAY_PORTIONS = ("full", "first_half", "second_half")


class CompoffCredit(Base, TimestampMixin):
    """One earned comp-off credit, with its own expiry.

    ``unique(employee_id, earned_date)`` is the guarantee that matters: a day
    worked can earn credit **once**, however many times the attendance for that
    day is recomputed or the credit job is re-run.
    """

    __tablename__ = "compoff_credits"
    __table_args__ = (
        CheckConstraint(f"status IN {CREDIT_STATUSES}", name="status_known"),
        CheckConstraint(f"source IN {CREDIT_SOURCES}", name="source_known"),
        CheckConstraint("credit_days > 0 AND credit_days <= 2", name="credit_days_sane"),
        CheckConstraint(
            "allocated_days >= 0 AND allocated_days <= credit_days",
            name="allocated_within_credit",
        ),
        CheckConstraint(
            "expiry_date IS NULL OR expiry_date >= earned_date", name="expiry_after_earned"
        ),
        CheckConstraint("worked_minutes >= 0", name="worked_minutes_non_negative"),
        # A day can earn credit once. This is the whole protection against a
        # recomputation or a re-run double-crediting.
        UniqueConstraint("employee_id", "earned_date"),
        Index("ix_compoff_credits_employee_status", "employee_id", "status"),
        # Backs the FIFO consumption query and the expiry job, both of which look
        # only at live lots.
        Index(
            "ix_compoff_credits_usable",
            "employee_id",
            "expiry_date",
            postgresql_where=text("status = 'available' AND allocated_days < credit_days"),
        ),
        Index("ix_compoff_credits_expiry", "expiry_date"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    earned_date: Mapped[date] = mapped_column(Date, nullable=False)
    attendance_day_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attendance_days.id", ondelete="SET NULL"), nullable=True
    )
    """
    The proof. Null only for an explicit manual grant, which ``source`` records.
    ``SET NULL`` rather than cascade: deleting a recomputed attendance row must
    not silently remove a credit someone has already been granted.
    """

    source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'attendance'")
    )

    day_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """The day type that qualified - weekly_off, holiday or working."""

    worked_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Net minutes worked, as the attendance engine computed them."""

    credit_days: Mapped[Days] = mapped_column(nullable=False)
    """Usually 1.00 or 0.50, from the configured minute thresholds."""

    allocated_days: Mapped[Days] = mapped_column(nullable=False, server_default=text("0"))
    """
    How much of this lot has been consumed. Maintained alongside the allocation
    rows, with a check constraint keeping it within the credit so a bug cannot
    over-draw a lot.
    """

    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    """Null never expires, for a company that sets the expiry window to zero."""

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'pending_verification'")
    )

    verified_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    reversal_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("compoff_credits.id", ondelete="RESTRICT"), nullable=True
    )
    """
    Set on a credit that cancels an earlier one. ``RESTRICT`` so the original
    cannot be deleted and leave the correction dangling.
    """

    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """The thresholds and expiry window in force when the credit was granted."""

    allocations: Mapped[list[CompoffAllocation]] = relationship(
        back_populates="credit", cascade="all, delete-orphan"
    )

    @property
    def remaining_days(self) -> Decimal:
        return Decimal(self.credit_days) - Decimal(self.allocated_days)

    @property
    def is_usable(self) -> bool:
        return self.status == "available" and self.remaining_days > 0

    def expires_by(self, on_date: date) -> bool:
        return self.expiry_date is not None and self.expiry_date < on_date

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CompoffCredit emp={self.employee_id} {self.earned_date} {self.credit_days}>"


class CompoffApplication(Base, TimestampMixin):
    """A request to take time off against earned comp-off credits."""

    __tablename__ = "compoff_applications"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint(f"status IN {APPLICATION_STATUSES}", name="status_known"),
        CheckConstraint("total_days > 0", name="total_days_positive"),
        CheckConstraint(
            "current_stage IS NULL OR current_stage IN " + str(APPROVAL_STAGES),
            name="stage_known",
        ),
        Index("ix_compoff_applications_employee", "employee_id", "start_date"),
        Index(
            "ix_compoff_applications_open",
            "current_stage",
            "start_date",
            postgresql_where=text("status IN ('pending_manager','pending_hr')"),
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_days: Mapped[Days] = mapped_column(nullable=False)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending_manager'")
    )
    current_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    escalated_from_missing_manager: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    applied_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    days: Mapped[list[CompoffApplicationDay]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CompoffApplicationDay.off_date",
    )
    allocations: Mapped[list[CompoffAllocation]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    approvals: Mapped[list[CompoffApproval]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="CompoffApproval.sequence",
    )

    @property
    def is_open(self) -> bool:
        return self.status in ("pending_manager", "pending_hr")


class CompoffApplicationDay(Base):
    """One day of a comp-off application."""

    __tablename__ = "compoff_application_days"
    __table_args__ = (
        CheckConstraint(f"portion IN {DAY_PORTIONS}", name="portion_known"),
        CheckConstraint("day_fraction > 0 AND day_fraction <= 1", name="fraction_in_range"),
        UniqueConstraint("application_id", "off_date"),
        Index("ix_compoff_application_days_date", "off_date"),
    )

    id: Mapped[PrimaryKey]
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compoff_applications.id", ondelete="CASCADE"), nullable=False
    )
    off_date: Mapped[date] = mapped_column(Date, nullable=False)
    portion: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'full'"))
    day_fraction: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("1.00")
    )
    day_type: Mapped[str] = mapped_column(String(20), nullable=False)

    application: Mapped[CompoffApplication] = relationship(back_populates="days")


class CompoffAllocation(Base):
    """How much of one credit lot an application consumed.

    This is what makes expiry correct. Without it, "the employee used a day"
    would not say which credit was spent, and the system could expire a lot that
    had in fact already been consumed - or keep one alive that had not.
    """

    __tablename__ = "compoff_allocations"
    __table_args__ = (
        CheckConstraint("days > 0", name="days_positive"),
        # One row per application and credit; a second draw on the same lot adds
        # to the existing row rather than creating another.
        UniqueConstraint("application_id", "credit_id"),
        Index("ix_compoff_allocations_credit", "credit_id"),
    )

    id: Mapped[PrimaryKey]
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compoff_applications.id", ondelete="CASCADE"), nullable=False
    )
    credit_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compoff_credits.id", ondelete="RESTRICT"), nullable=False
    )
    """``RESTRICT``: a consumed credit cannot be deleted out from under its use."""

    days: Mapped[Days] = mapped_column(nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    application: Mapped[CompoffApplication] = relationship(back_populates="allocations")
    credit: Mapped[CompoffCredit] = relationship(back_populates="allocations")


class CompoffApproval(Base):
    """One stage decision on a comp-off application. Append-only."""

    __tablename__ = "compoff_approvals"
    __table_args__ = (
        CheckConstraint(f"stage IN {APPROVAL_STAGES}", name="stage_known"),
        CheckConstraint(f"decision IN {APPROVAL_DECISIONS}", name="decision_known"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        UniqueConstraint("application_id", "sequence"),
    )

    id: Mapped[PrimaryKey]
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compoff_applications.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)

    decided_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    was_auto_skipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    application: Mapped[CompoffApplication] = relationship(back_populates="approvals")
