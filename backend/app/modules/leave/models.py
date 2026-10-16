"""Leave types, policies, applications, approvals and the balance ledger.

The central decision
--------------------
**A leave balance is a ``SUM`` over an append-only ledger, never a stored
column.** A mutable balance field cannot be reconciled or explained, and is the
usual source of disputes: an employee asks why they have 11.5 days and nobody
can reconstruct the arithmetic. Here every change is a row with a type, a
reason, an actor and a link to whatever caused it, so a balance can always be
derived from first principles.

The cost is a sum per query, which is paid for by an index on
``(employee_id, leave_type_id, effective_date)``.

Effective dating
----------------
``leave_policies`` and ``employee_leave_policies`` are effective-dated rather
than updated in place, and each application records the policy version that
governed it. Raising a quota in July therefore does not change how a March
application was decided.

Idempotency
-----------
``leave_ledger_entries.idempotency_key`` is unique. An accrual job computes a
deterministic key per employee, type and period, so a retried run inserts
nothing rather than crediting twice - the single most damaging failure mode in
leave management, and one that is invisible until someone audits a balance.
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

from app.db.base import ActorMixin, Base, Days, Jsonb, PrimaryKey, TimestampMixin

# ---------------------------------------------------------------------------
# Value domains
# ---------------------------------------------------------------------------

#: How a leave type's quota is granted over the year.
ACCRUAL_FREQUENCIES = ("none", "monthly", "quarterly", "half_yearly", "annual")

#: What to do with a fractional accrual result.
ROUNDING_MODES = ("none", "nearest_half", "nearest_full", "down_half", "up_half")

#: Portion of a working day a leave day consumes.
DAY_PORTIONS = ("full", "first_half", "second_half")

#: Application lifecycle. ``withdrawn`` is the applicant pulling a *pending*
#: request; ``cancelled`` is an already-approved leave being called off, which
#: has to reverse a ledger entry and recompute attendance.
APPLICATION_STATUSES = (
    "pending_manager",
    "pending_hr",
    "approved",
    "rejected",
    "withdrawn",
    "cancelled",
)

#: Stages in the approval chain, matching ``leave.approval_chain``.
APPROVAL_STAGES = ("reporting_manager", "hr_admin")

#: A decision recorded against a stage.
APPROVAL_DECISIONS = ("approved", "rejected", "skipped")

#: Ledger transaction types. Every balance change is one of these.
LEDGER_TXN_TYPES = (
    "opening",
    "accrual",
    "deduction",
    "reversal",
    "adjustment",
    "carry_forward_in",
    "carry_forward_out",
    "expiry",
    "encashment",
    "compoff_credit",
)

#: Restrictions a leave type may place on who can apply.
GENDER_RESTRICTIONS = ("any", "female", "male")


class LeaveType(Base, TimestampMixin, ActorMixin):
    """A kind of leave - casual, sick, earned, unpaid, maternity.

    No leave types are seeded. The statutory ones (maternity, for instance) have
    legally mandated entitlements that this project has not been given verified
    references for, so creating them with invented quotas would be worse than
    leaving the table empty. ``requires_legal_review`` marks a type whose
    entitlement needs checking before it is relied on.
    """

    __tablename__ = "leave_types"
    __table_args__ = (
        CheckConstraint("code = upper(code)", name="code_upper"),
        CheckConstraint(
            f"gender_restriction IN {GENDER_RESTRICTIONS}", name="gender_restriction_known"
        ),
        CheckConstraint(
            "max_consecutive_days IS NULL OR max_consecutive_days > 0",
            name="max_consecutive_positive",
        ),
        CheckConstraint(
            "min_service_days >= 0 AND min_service_days <= 3650", name="min_service_in_range"
        ),
        CheckConstraint(
            "attachment_required_above_days IS NULL OR attachment_required_above_days >= 0",
            name="attachment_threshold_non_negative",
        ),
        CheckConstraint("min_notice_days >= 0 AND min_notice_days <= 365", name="notice_in_range"),
        Index("ix_leave_types_active", "is_active"),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """
    Whether a day of this leave is paid. Drives ``payable_day_fraction`` on the
    attendance day, so payroll and attendance cannot disagree about it.
    """

    deducts_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    """
    False for a type with no quota at all, such as unpaid leave: the application
    is still recorded and approved, but nothing is deducted because there is no
    balance to deduct from.
    """

    allows_half_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    allows_negative_balance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """Null inherits ``leave.allow_negative_balance``; set to override per type."""

    counts_non_working_days: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """
    Whether weekly offs and holidays inside the range are consumed as leave.
    Separate from the company-wide sandwich rule because some statutory leave
    types are counted in calendar days by law while ordinary leave is not. Both
    have direct payroll consequences, so neither is assumed.
    """

    requires_reason: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    attachment_required_above_days: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True
    )
    """Days beyond which a supporting document is required. Null means never."""

    min_notice_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Advance notice required. Zero allows same-day application."""

    max_consecutive_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_service_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Days of service before an employee becomes eligible."""

    gender_restriction: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'any'")
    )
    """
    Only for types whose statutory basis is gender-specific, such as maternity
    leave. Default is unrestricted.
    """

    requires_legal_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """
    Marks a type whose entitlement is set by statute and has not been verified
    against a legal reference. Surfaced in the UI and in the configuration health
    report rather than quietly trusted.
    """

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    colour: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    policies: Mapped[list[LeavePolicy]] = relationship(
        back_populates="leave_type", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LeaveType {self.code}>"


class LeavePolicy(Base, TimestampMixin, ActorMixin):
    """An effective-dated rule set for one leave type.

    Superseding rather than editing is what keeps a past decision explicable: an
    application records the policy id that governed it, so raising a quota in
    July cannot change how March was decided.
    """

    __tablename__ = "leave_policies"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="period_ordered"
        ),
        CheckConstraint(f"accrual_frequency IN {ACCRUAL_FREQUENCIES}", name="frequency_known"),
        CheckConstraint(f"accrual_rounding IN {ROUNDING_MODES}", name="rounding_known"),
        CheckConstraint("annual_quota_days >= 0", name="quota_non_negative"),
        CheckConstraint(
            "max_accumulation_days IS NULL OR max_accumulation_days >= 0",
            name="accumulation_non_negative",
        ),
        CheckConstraint(
            "carry_forward_cap_days IS NULL OR carry_forward_cap_days >= 0",
            name="carry_forward_non_negative",
        ),
        CheckConstraint(
            "carry_forward_expires_after_months IS NULL OR carry_forward_expires_after_months > 0",
            name="expiry_positive",
        ),
        CheckConstraint(
            "encashment_max_days IS NULL OR encashment_max_days >= 0",
            name="encashment_non_negative",
        ),
        Index("ix_leave_policies_type_from", "leave_type_id", "effective_from"),
        # One policy per type per start date: two would make "which rules applied
        # on this date" ambiguous, and the resolver would have to choose
        # arbitrarily.
        UniqueConstraint("leave_type_id", "effective_from"),
    )

    id: Mapped[PrimaryKey]
    leave_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    annual_quota_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("0")
    )
    accrual_frequency: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'annual'")
    )
    accrual_rounding: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'nearest_half'")
    )

    prorate_on_joining: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    """
    Whether a mid-period joiner accrues a part period. Without proration a
    joiner on the 28th receives the same credit as one who worked the whole
    month.
    """

    prorate_on_exit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    accrue_from_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """Start accruing only after probation ends, if the company works that way."""

    max_accumulation_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    """Ceiling on the running balance. Accrual stops rather than erroring."""

    carry_forward_cap_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    """
    Maximum carried into the next leave year. Null means the year-end rule
    decides alone; zero means nothing carries.
    """

    carry_forward_expires_after_months: Mapped[int | None] = mapped_column(Integer, nullable=True)

    encashment_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    encashment_max_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    requires_legal_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """
    Set when a quota comes from statute rather than company choice. The
    entitlement has not been verified here, so the flag travels with the policy
    instead of the number being presented as settled.
    """

    leave_type: Mapped[LeaveType] = relationship(back_populates="policies")

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


class EmployeeLeavePolicy(Base, TimestampMixin):
    """Which policy governs an employee's leave type, and from when.

    Effective-dated so a mid-year change of entitlement - a promotion, a change
    of grade - does not rewrite how earlier applications were decided.
    """

    __tablename__ = "employee_leave_policies"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="period_ordered"
        ),
        Index(
            "ix_employee_leave_policies_lookup",
            "employee_id",
            "leave_type_id",
            "effective_from",
        ),
        # At most one open assignment per employee and type. Two would make the
        # resolver choose arbitrarily between entitlements.
        Index(
            "ix_employee_leave_policies_open_unique",
            "employee_id",
            "leave_type_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_types.id", ondelete="CASCADE"), nullable=False
    )
    leave_policy_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_policies.id", ondelete="RESTRICT"), nullable=False
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def covers(self, day: date) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day <= self.effective_to


class LeaveApplication(Base, TimestampMixin):
    """One request, covering one or more days of a single leave type."""

    __tablename__ = "leave_applications"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint(f"status IN {APPLICATION_STATUSES}", name="status_known"),
        CheckConstraint("total_days > 0", name="total_days_positive"),
        CheckConstraint("paid_days >= 0 AND unpaid_days >= 0", name="split_non_negative"),
        CheckConstraint(
            "current_stage IS NULL OR current_stage IN " + str(APPROVAL_STAGES),
            name="stage_known",
        ),
        Index("ix_leave_applications_employee_dates", "employee_id", "start_date", "end_date"),
        Index("ix_leave_applications_status", "status"),
        # Backs the approver queues, a small slice of a table that grows forever.
        Index(
            "ix_leave_applications_open",
            "current_stage",
            "start_date",
            postgresql_where=text("status IN ('pending_manager','pending_hr')"),
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False
    )
    leave_policy_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("leave_policies.id", ondelete="RESTRICT"), nullable=True
    )
    """
    The policy version in force when this was applied for, pinned so the
    decision stays explicable after the policy is superseded.
    """

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    total_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    """
    Days this application consumes, after excluding non-working days and
    applying half-day portions. Decimal because half days are real.
    """

    paid_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("0")
    )
    unpaid_days: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("0")
    )
    """
    Split recorded at approval. An application that runs past the balance can be
    part paid and part unpaid, and payroll needs the split rather than a
    recomputation of it.
    """

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_during_leave: Mapped[str | None] = mapped_column(String(120), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending_manager'")
    )
    current_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """Null once the application reaches a terminal state."""

    escalated_from_missing_manager: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """
    Records that the manager stage was skipped because there was no active
    manager - so an approval history with a missing stage is explained rather
    than looking like a bypass.
    """

    is_backdated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    applied_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_on_behalf: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """True when HR raised the application for the employee rather than the employee."""

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    supersedes_application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("leave_applications.id", ondelete="SET NULL"), nullable=True
    )
    """
    A modification is a new application superseding the old one, not an edit.
    Editing an approved request in place would destroy the record of what was
    actually approved.
    """

    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)
    """
    The settings and policy values that governed this application, captured at
    submission. Lets a past decision be explained even after everything has been
    reconfigured.
    """

    attachment_file_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True
    )

    days: Mapped[list[LeaveApplicationDay]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="LeaveApplicationDay.leave_date",
    )
    approvals: Mapped[list[LeaveApproval]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="LeaveApproval.sequence",
    )

    @property
    def is_open(self) -> bool:
        return self.status in ("pending_manager", "pending_hr")

    @property
    def is_effective(self) -> bool:
        """Whether this application currently consumes balance and attendance."""
        return self.status == "approved"


class LeaveApplicationDay(Base):
    """One calendar day of an application.

    Day-level rows rather than a start/end pair alone, because a range can be
    heterogeneous: a weekly off in the middle is not consumed, the first day may
    be a second-half leave, and payroll needs the per-day payability. Deriving
    all that repeatedly from the range would risk two callers deriving it
    differently.
    """

    __tablename__ = "leave_application_days"
    __table_args__ = (
        CheckConstraint(f"portion IN {DAY_PORTIONS}", name="portion_known"),
        CheckConstraint("day_fraction > 0 AND day_fraction <= 1", name="fraction_in_range"),
        UniqueConstraint("application_id", "leave_date"),
        Index("ix_leave_application_days_date", "leave_date"),
    )

    id: Mapped[PrimaryKey]
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_applications.id", ondelete="CASCADE"), nullable=False
    )
    leave_date: Mapped[date] = mapped_column(Date, nullable=False)

    portion: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'full'"))
    day_fraction: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("1.00")
    )
    """1.00 for a full day, 0.50 for a half. Multiplied by payroll, not inferred."""

    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    day_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """The day type at submission - working, weekly_off or holiday."""

    counts_against_balance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    """
    False for a non-working day inside the range that the type does not consume.
    The row is still stored so the calendar shows the full span the employee was
    away, which is what a manager actually wants to see.
    """

    application: Mapped[LeaveApplication] = relationship(back_populates="days")


class LeaveApproval(Base):
    """One stage decision. The complete history, never overwritten.

    A rejected-then-resubmitted request, or one a manager approved and HR
    refused, must stay legible afterwards. Rows are appended per stage with a
    sequence; nothing is updated.
    """

    __tablename__ = "leave_approvals"
    __table_args__ = (
        CheckConstraint(f"stage IN {APPROVAL_STAGES}", name="stage_known"),
        CheckConstraint(f"decision IN {APPROVAL_DECISIONS}", name="decision_known"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        UniqueConstraint("application_id", "sequence"),
        Index("ix_leave_approvals_application", "application_id"),
    )

    id: Mapped[PrimaryKey]
    application_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_applications.id", ondelete="CASCADE"), nullable=False
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
    """A stage recorded as skipped because no active manager existed."""

    application: Mapped[LeaveApplication] = relationship(back_populates="approvals")


class LeaveLedgerEntry(Base):
    """Append-only balance movement. **The balance is the sum of these.**

    Never updated and never deleted - a database trigger enforces that, and
    ``UPDATE``/``DELETE`` are revoked from the runtime role. A mistake is
    corrected by posting a reversal, which keeps both the error and the fix on
    the record.

    ``idempotency_key`` is what makes a retried accrual job harmless.
    """

    __tablename__ = "leave_ledger_entries"
    __table_args__ = (
        CheckConstraint(f"txn_type IN {LEDGER_TXN_TYPES}", name="txn_type_known"),
        CheckConstraint("days <> 0", name="days_non_zero"),
        UniqueConstraint("idempotency_key"),
        Index(
            "ix_leave_ledger_balance",
            "employee_id",
            "leave_type_id",
            "effective_date",
        ),
        Index("ix_leave_ledger_application", "application_id"),
        {"comment": "Append-only. Balance is SUM(days); never store a balance column."},
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    leave_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False
    )

    txn_type: Mapped[str] = mapped_column(String(30), nullable=False)

    days: Mapped[Days] = mapped_column(nullable=False)
    """
    Signed: positive credits, negative consumes. Storing the sign rather than a
    separate direction column means the balance is a plain ``SUM`` that cannot
    be got wrong by a caller.
    """

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    """
    The date the movement belongs to, which is not the date it was posted. An
    accrual for March posted late still belongs to March.
    """

    leave_year: Mapped[int] = mapped_column(Integer, nullable=False)
    """
    The leave year this movement belongs to, for carry-forward and expiry. The
    starting month is configurable, so this is not simply the calendar year.
    """

    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    """For carried-forward credit with a limited life. Null never expires."""

    application_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("leave_applications.id", ondelete="SET NULL"), nullable=True
    )
    reverses_entry_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("leave_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    """
    Which entry a reversal undoes. ``RESTRICT`` because deleting the original
    would orphan the correction and leave the balance unexplainable.
    """

    leave_policy_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("leave_policies.id", ondelete="SET NULL"), nullable=True
    )
    job_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    """
    Deterministic per logical movement, e.g.
    ``accrual:emp42:CL:2026-04``. The unique constraint is what stops a retried
    job from crediting twice.
    """

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LeaveLedgerEntry {self.txn_type} {self.days:+} emp={self.employee_id}>"
