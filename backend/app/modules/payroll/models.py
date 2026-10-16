"""Salary structures, statutory rule sets, payroll runs and payslips.

Nothing in this module is seeded
--------------------------------
No salary component, no rate, no ceiling, no slab. A statutory rate entered
without a verified source would produce a wrong deduction that looks right,
which is worse than refusing to compute: the employee loses money and nobody
notices until an inspection. Every statutory figure lives in
``statutory_rule_parameters`` under a rule set that carries its own
``source_reference`` and must be explicitly verified before payroll can be
finalized with it.

The same applies to salary components. "Basic" and "HRA" look universal but
their proportions are a company decision with tax consequences, so the company
defines them.

What makes a past payroll explainable
-------------------------------------
Three snapshots, taken when a run is calculated and never updated afterwards:

* ``payroll_runs.rule_set_snapshot`` - the id and version of every statutory
  rule set the run used. This is what makes a past run **reproducible** after
  the rules change, which is the single most important property of this schema.
* ``payroll_runs.settings_snapshot`` - the payable-days basis, rounding mode and
  other settings in force. Changing a setting next month cannot retrospectively
  alter what was paid.
* ``payslip_lines.basis_amount`` and ``basis_description`` - *what* each figure
  was computed from, not only the figure. "PF 1800" is unanswerable; "12% of a
  PF wage of 15000, capped at the configured ceiling" can be checked.

Money
-----
Every amount is ``NUMERIC(14, 2)`` and every day count ``NUMERIC(6, 2)``.
Nothing monetary is ever a float, in Python or in the database.

Corrections
-----------
A finalized run is immutable. A mistake is corrected by a
``payroll_adjustments`` row applied in a later run - arrears, a recovery or a
reversal - so the register of what was actually paid in a month stays true.
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

from app.db.base import ActorMixin, Base, Jsonb, PrimaryKey, TimestampMixin

#: What a component does to the payslip. ``employer_contribution`` is shown but
#: never deducted from the employee: it is a company cost, and conflating the
#: two is the classic payslip error.
COMPONENT_TYPES: tuple[str, ...] = (
    "earning",
    "deduction",
    "employer_contribution",
    "reimbursement",
)

#: How a component's amount is arrived at. There is deliberately no free-text
#: formula: an arbitrary expression evaluated against payroll data is both a
#: security problem and unauditable. A company needing one declares a fixed
#: amount per employee instead.
CALCULATION_TYPES: tuple[str, ...] = (
    "fixed",
    "percent_of_component",
    "percent_of_gross",
    "percent_of_ctc",
    "balance",
    "statutory",
    "attendance_based",
)

#: Statutory domains the engine knows the *shape* of. Knowing the shape is not
#: knowing the numbers: each still needs a verified rule set before it computes.
STATUTORY_DOMAINS: tuple[str, ...] = (
    "epf",
    "eps",
    "edli",
    "esi",
    "pt",
    "tds",
    "gratuity",
    "lwf",
)

RULE_SET_STATUSES: tuple[str, ...] = ("draft", "verified", "superseded")

#: The lifecycle the brief requires. Each transition is a separate permission
#: and a separate audited act; nothing skips ahead.
RUN_STATUSES: tuple[str, ...] = (
    "draft",
    "calculated",
    "reviewed",
    "approved",
    "finalized",
    "cancelled",
)

RUN_TYPES: tuple[str, ...] = ("regular", "supplementary", "fnf")

ADJUSTMENT_TYPES: tuple[str, ...] = ("arrear", "bonus", "recovery", "reversal")

ZERO = Decimal("0.00")


def money(**kwargs: Any) -> Any:
    """A monetary column. Never a float, in Python or in the database."""
    return mapped_column(Numeric(14, 2), **kwargs)


def days(**kwargs: Any) -> Any:
    """A day-count column, to two places so half days are exact."""
    return mapped_column(Numeric(6, 2), **kwargs)


# ---------------------------------------------------------------------------
# Salary structure
# ---------------------------------------------------------------------------


class SalaryComponent(Base, TimestampMixin, ActorMixin):
    """One line that can appear on a payslip.

    The wage-base flags are the important part. Whether a component counts
    towards the PF wage, the ESI wage, professional tax or taxable income is a
    per-component decision with legal consequences, so each is explicit and
    none is inferred from the component's name. A component whose treatment the
    company has not established carries ``requires_legal_review``, which is
    surfaced rather than quietly trusted.
    """

    __tablename__ = "salary_components"
    __table_args__ = (
        CheckConstraint(f"component_type IN {COMPONENT_TYPES}", name="component_type_known"),
        CheckConstraint(f"calculation_type IN {CALCULATION_TYPES}", name="calculation_type_known"),
        CheckConstraint("code = upper(code)", name="code_upper"),
        CheckConstraint(
            "(calculation_type <> 'percent_of_component') OR (of_component_id IS NOT NULL)",
            name="percent_of_needs_a_base",
        ),
        CheckConstraint(
            "percentage IS NULL OR (percentage > 0 AND percentage <= 100)",
            name="percentage_in_range",
        ),
        CheckConstraint(
            "(calculation_type <> 'statutory') OR (statutory_domain IS NOT NULL)",
            name="statutory_needs_a_domain",
        ),
        {
            "comment": (
                "No component is seeded. Their names and proportions are a company "
                "decision with tax consequences."
            )
        },
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    component_type: Mapped[str] = mapped_column(String(30), nullable=False)
    calculation_type: Mapped[str] = mapped_column(String(30), nullable=False)

    #: For ``percent_of_component``: the component the percentage applies to.
    of_component_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("salary_components.id", ondelete="RESTRICT"), nullable=True
    )
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)

    #: For ``statutory``: which rule set computes it.
    statutory_domain: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # -- wage bases. Explicit per component; never inferred from the name.
    is_pf_wage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_esi_wage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_pt_wage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    #: Whether the amount reduces when payable days are short. A monthly
    #: allowance may be paid in full regardless; basic salary is not.
    prorates_on_payable_days: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    #: Shown on the payslip, as opposed to used only inside the calculation.
    show_on_payslip: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    #: Set when the company has not established this component's statutory
    #: treatment. Surfaced in the configuration health report.
    requires_legal_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class SalaryStructure(Base, TimestampMixin, ActorMixin):
    """A named template of components, for assigning a consistent structure.

    A template, not a binding: an employee's revision copies the resolved
    amounts, so editing a structure later cannot silently rewrite what somebody
    is already being paid.
    """

    __tablename__ = "salary_structures"
    __table_args__ = (CheckConstraint("code = upper(code)", name="code_upper"),)

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    components: Mapped[list[SalaryStructureComponent]] = relationship(
        back_populates="structure", cascade="all, delete-orphan"
    )


class SalaryStructureComponent(Base):
    """A component's place in a structure, with its default value."""

    __tablename__ = "salary_structure_components"
    __table_args__ = (
        UniqueConstraint("structure_id", "component_id"),
        CheckConstraint(
            "fixed_amount IS NULL OR fixed_amount >= 0", name="fixed_amount_not_negative"
        ),
    )

    id: Mapped[PrimaryKey]
    structure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("salary_structures.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("salary_components.id", ondelete="RESTRICT"), nullable=False
    )
    fixed_amount: Mapped[Decimal | None] = money(nullable=True)
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))

    structure: Mapped[SalaryStructure] = relationship(back_populates="components")


class EmployeeSalary(Base, TimestampMixin, ActorMixin):
    """One effective-dated salary revision.

    **Never edited in place.** A raise is a new row, so a payslip from before it
    can still be explained. ``effective_to`` is set when the next revision
    begins, which is what keeps "what was this person's basic in March"
    answerable.

    The component amounts are copied into ``employee_salary_components`` at
    revision time rather than resolved from the structure on each payroll run:
    a structure edited in June must not change what April paid.
    """

    __tablename__ = "employee_salaries"
    __table_args__ = (
        UniqueConstraint("employee_id", "effective_from"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="dates_ordered"
        ),
        CheckConstraint("ctc_annual IS NULL OR ctc_annual >= 0", name="ctc_not_negative"),
        CheckConstraint("gross_monthly >= 0", name="gross_not_negative"),
        Index("ix_employee_salaries_employee_effective", "employee_id", "effective_from"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    structure_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("salary_structures.id", ondelete="SET NULL"), nullable=True
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    ctc_annual: Mapped[Decimal | None] = money(nullable=True)
    gross_monthly: Mapped[Decimal] = money(nullable=False)

    revision_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    components: Mapped[list[EmployeeSalaryComponent]] = relationship(
        back_populates="salary", cascade="all, delete-orphan"
    )


class EmployeeSalaryComponent(Base):
    """A resolved monthly amount for one component of one revision.

    Resolved, not a reference: the figure here is what payroll uses, so the
    arithmetic that produced it happened once, at the revision, where somebody
    could check it.
    """

    __tablename__ = "employee_salary_components"
    __table_args__ = (
        UniqueConstraint("employee_salary_id", "component_id"),
        CheckConstraint("monthly_amount >= 0", name="amount_not_negative"),
    )

    id: Mapped[PrimaryKey]
    employee_salary_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employee_salaries.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("salary_components.id", ondelete="RESTRICT"), nullable=False
    )
    monthly_amount: Mapped[Decimal] = money(nullable=False)
    #: How the amount was arrived at, for display: "40% of gross", "fixed".
    basis_description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))

    salary: Mapped[EmployeeSalary] = relationship(back_populates="components")


# ---------------------------------------------------------------------------
# Statutory configuration
# ---------------------------------------------------------------------------


class StatutoryRuleSet(Base, TimestampMixin, ActorMixin):
    """One version of one statutory domain's rules.

    **Effective-dated and versioned, never edited once verified.** A rate that
    changes in April is a new rule set, so a payroll run from March can still be
    reproduced exactly - which is the property the whole schema exists for.

    ``status`` starts at ``draft``. Verification is a separate, permissioned act
    that records who checked it and against which published source. With
    ``payroll.block_finalize_on_unverified_rules`` enabled - the default - a run
    that used a draft rule set cannot be finalized.

    Nothing here is seeded. Rates, ceilings and slabs live in
    ``statutory_rule_parameters``; this project has not been given verified
    references for any of them.
    """

    __tablename__ = "statutory_rule_sets"
    __table_args__ = (
        # ``NULLS NOT DISTINCT`` because most domains have no state: without it
        # PostgreSQL treats two NULL state codes as different values, and two
        # national rule sets could take effect on the same date - leaving "which
        # rules did April use" with two answers, which is the one thing this
        # table exists to prevent.
        UniqueConstraint(
            "domain",
            "jurisdiction",
            "state_code",
            "effective_from",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(f"domain IN {STATUTORY_DOMAINS}", name="domain_known"),
        CheckConstraint(f"status IN {RULE_SET_STATUSES}", name="status_known"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="dates_ordered"
        ),
        CheckConstraint(
            "(status <> 'verified') OR "
            "(source_reference IS NOT NULL AND verified_by_user_id IS NOT NULL "
            "AND verified_at IS NOT NULL)",
            name="verified_needs_a_source_and_verifier",
        ),
        Index("ix_statutory_rule_sets_lookup", "domain", "effective_from", "status"),
        {
            "comment": (
                "No rates are seeded. A rate entered without a verified source "
                "produces a wrong deduction that looks right."
            )
        },
    )

    id: Mapped[PrimaryKey]
    domain: Mapped[str] = mapped_column(String(20), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'IN'")
    )
    #: Professional tax and labour welfare fund are per state; the rest are not.
    state_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    version_label: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))

    #: The published document this was taken from. Required to verify.
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_published_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    verified_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    parameters: Mapped[list[StatutoryRuleParameter]] = relationship(
        back_populates="rule_set", cascade="all, delete-orphan"
    )


class StatutoryRuleParameter(Base, TimestampMixin):
    """One rate, ceiling, floor or slab table belonging to a rule set.

    Two value columns rather than one: a rate is a number the engine does
    arithmetic with, while a slab table is a structure. Keeping the scalar in
    ``numeric_value`` means a rate cannot be stored as a string that happens to
    parse, and keeping slabs in ``json_value`` means they need no second table
    whose shape would differ per domain anyway.
    """

    __tablename__ = "statutory_rule_parameters"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "key"),
        CheckConstraint(
            "(numeric_value IS NOT NULL) <> (json_value IS NOT NULL)",
            name="exactly_one_value",
        ),
    )

    id: Mapped[PrimaryKey]
    rule_set_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("statutory_rule_sets.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)

    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 6), nullable=True)
    json_value: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)

    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule_set: Mapped[StatutoryRuleSet] = relationship(back_populates="parameters")


class EmployeeStatutoryEligibility(Base, TimestampMixin, ActorMixin):
    """Whether one employee is covered by one statutory domain.

    Effective-dated, because coverage changes: somebody crossing a wage ceiling
    stops being eligible for ESI from a particular month, and the payslips
    before that must still be explicable. A row is required to *exclude*
    somebody as well as to include them, with a reason, so an exemption is a
    recorded decision rather than an absence of data.
    """

    __tablename__ = "employee_statutory_eligibility"
    __table_args__ = (
        UniqueConstraint("employee_id", "domain", "effective_from"),
        CheckConstraint(f"domain IN {STATUTORY_DOMAINS}", name="domain_known"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="dates_ordered"
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(20), nullable=False)
    is_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Payroll runs
# ---------------------------------------------------------------------------


class PayrollPeriod(Base, TimestampMixin, ActorMixin):
    """A calendar month of payroll.

    Separate from the run because a month may need more than one run - a
    supplementary for a missed arrear, a full-and-final for a leaver - and all
    of them belong to the same period and the same register.
    """

    __tablename__ = "payroll_periods"
    __table_args__ = (
        UniqueConstraint("year", "month"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_in_range"),
        CheckConstraint("year BETWEEN 2000 AND 2100", name="year_in_range"),
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
    )

    id: Mapped[PrimaryKey]
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PayrollRun(Base, TimestampMixin, ActorMixin):
    """One calculation of one period, moving through the required lifecycle.

    ``draft -> calculated -> reviewed -> approved -> finalized``, each step a
    separate permission and a separate audited act. Recalculation is allowed
    only while the run is in ``draft`` or ``calculated``: once a human has
    reviewed it, changing the numbers underneath them would make the review
    meaningless, so the run must be sent back explicitly.

    ``idempotency_key`` is unique over the period and run type, so a scheduler
    or an impatient operator cannot create two regular runs for one month.
    """

    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(f"status IN {RUN_STATUSES}", name="status_known"),
        CheckConstraint(f"run_type IN {RUN_TYPES}", name="run_type_known"),
        Index("ix_payroll_runs_period_status", "period_id", "status"),
        {
            "comment": (
                "rule_set_snapshot and settings_snapshot are what make a past run "
                "reproducible after rules or settings change."
            )
        },
    )

    id: Mapped[PrimaryKey]
    period_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payroll_periods.id", ondelete="RESTRICT"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'regular'")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Which rule sets this run used, by id and version. Without this, a rate
    #: change makes every past run unexplainable.
    rule_set_snapshot: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)
    #: Payable-days basis, rounding mode and the rest, as they were.
    settings_snapshot: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)
    #: Domains that computed without a verified rule set. Blocks finalization
    #: while ``payroll.block_finalize_on_unverified_rules`` is on.
    unverified_domains: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)

    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_gross: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    total_deductions: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    total_employer_cost: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    total_net: Mapped[Decimal] = money(nullable=False, server_default=text("0"))

    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calculated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    period: Mapped[PayrollPeriod] = relationship()
    payslips: Mapped[list[Payslip]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        """Whether the numbers may still be recalculated."""
        return self.status in ("draft", "calculated")


class Payslip(Base, TimestampMixin):
    """One employee's pay for one run.

    ``payable_days`` is summed from ``attendance_days.payable_day_fraction`` -
    the exact decimal the attendance engine already stored - so payroll and
    attendance cannot disagree about how much of a month somebody worked. The
    loss-of-pay days are the complement, and both are recorded rather than
    recomputed later, because the attendance behind them may legitimately change
    after the run is finalized.

    ``review_flags`` holds what a human should look at before approving: a
    negative net, an unusually large change from last month, a missing salary
    revision. The run is not blocked by them - that would make an exceptional
    but correct payslip impossible - but they are shown.
    """

    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("run_id", "employee_id"),
        CheckConstraint("payable_days >= 0 AND lop_days >= 0", name="day_counts_not_negative"),
        Index("ix_payslips_employee", "employee_id"),
        Index(
            "ix_payslips_published",
            "employee_id",
            "published_at",
            postgresql_where=text("published_at IS NOT NULL"),
        ),
    )

    id: Mapped[PrimaryKey]
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    employee_salary_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee_salaries.id", ondelete="SET NULL"), nullable=True
    )

    #: Identity as it was, so a payslip stays readable after a transfer.
    employee_code: Mapped[str] = mapped_column(String(30), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    designation_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_location_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    days_in_period: Mapped[Decimal] = days(nullable=False)
    payable_days: Mapped[Decimal] = days(nullable=False)
    lop_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    paid_leave_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    present_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    weekly_off_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    holiday_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    gross_earnings: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    total_deductions: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    employer_contributions: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    net_pay: Mapped[Decimal] = money(nullable=False, server_default=text("0"))

    #: Proration, when somebody joined or left mid-month.
    is_prorated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    proration_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    review_flags: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)
    calculation_notes: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)

    #: Set when the payslip is released to the employee, which is a separate act
    #: from finalizing the run.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[PayrollRun] = relationship(back_populates="payslips")
    lines: Mapped[list[PayslipLine]] = relationship(
        back_populates="payslip", cascade="all, delete-orphan"
    )


class PayslipLine(Base):
    """One figure on a payslip, with what it was computed from.

    ``basis_amount`` and ``basis_description`` are the point of this table. A
    line reading "Provident fund 1800" cannot be checked by the person it was
    deducted from; "12% of a PF wage of 15000" can. Every statutory line also
    cites the rule set that produced it, so a disputed deduction leads back to a
    verified source.

    ``component_id`` is nullable because statutory lines have no salary
    component behind them - they are computed from a rule set - and inventing a
    component row for each would put derived data in a configuration table.
    """

    __tablename__ = "payslip_lines"
    __table_args__ = (
        UniqueConstraint("payslip_id", "line_code"),
        CheckConstraint(f"component_type IN {COMPONENT_TYPES}", name="component_type_known"),
    )

    id: Mapped[PrimaryKey]
    payslip_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payslips.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("salary_components.id", ondelete="SET NULL"), nullable=True
    )

    line_code: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    component_type: Mapped[str] = mapped_column(String(30), nullable=False)

    amount: Mapped[Decimal] = money(nullable=False)
    #: The full monthly amount before proration, where they differ.
    full_amount: Mapped[Decimal | None] = money(nullable=True)
    basis_amount: Mapped[Decimal | None] = money(nullable=True)
    basis_description: Mapped[str | None] = mapped_column(String(300), nullable=True)

    #: For a statutory line: the rule set and version that produced it.
    statutory_rule_set_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("statutory_rule_sets.id", ondelete="SET NULL"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))

    payslip: Mapped[Payslip] = relationship(back_populates="lines")


class PayrollAdjustment(Base, TimestampMixin, ActorMixin):
    """An arrear, bonus, recovery or reversal, applied in a later run.

    **The only way to correct a finalized run.** Rewriting a finalized month
    would falsify the register of what was actually paid, and the bank transfer
    and statutory filings made from it. So a correction is posted forward, with
    the period it relates to recorded separately from the run that pays it -
    which is what makes an arrear explainable as "March's shortfall, paid in
    May".

    ``idempotency_key`` is unique, so a retried import of adjustments cannot
    pay somebody twice.
    """

    __tablename__ = "payroll_adjustments"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(f"adjustment_type IN {ADJUSTMENT_TYPES}", name="adjustment_type_known"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('pending', 'applied', 'cancelled')", name="status_known"),
        Index("ix_payroll_adjustments_pending", "employee_id", "status"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    adjustment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Positive always; the type decides the direction. A signed amount plus a
    #: type would allow a "recovery of -500", which is a payment by accident.
    amount: Mapped[Decimal] = money(nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    #: The period the money belongs to, which is not the run that pays it.
    relates_to_period_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payroll_periods.id", ondelete="SET NULL"), nullable=True
    )
    applied_in_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payroll_runs.id", ondelete="SET NULL"), nullable=True
    )

    is_taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FnfSettlement(Base, TimestampMixin, ActorMixin):
    """Full and final settlement for somebody who has left.

    Its own record rather than an ordinary payslip, because it settles things an
    ordinary month does not: leave encashment, notice recovery, and gratuity
    where the company's verified rules provide for it. Gratuity is computed only
    from a verified rule set - there is no fallback formula, because an invented
    one would understate or overstate a statutory payment somebody is owed.
    """

    __tablename__ = "fnf_settlements"
    __table_args__ = (
        UniqueConstraint("employee_id"),
        CheckConstraint(
            "status IN ('draft', 'calculated', 'approved', 'paid', 'cancelled')",
            name="status_known",
        ),
        CheckConstraint("notice_recovery_amount >= 0", name="recovery_not_negative"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_working_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))

    leave_encashment_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    leave_encashment_amount: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    notice_shortfall_days: Mapped[Decimal] = days(nullable=False, server_default=text("0"))
    notice_recovery_amount: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    gratuity_amount: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    gratuity_rule_set_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("statutory_rule_sets.id", ondelete="SET NULL"), nullable=True
    )
    other_dues: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    other_recoveries: Mapped[Decimal] = money(nullable=False, server_default=text("0"))
    net_settlement: Mapped[Decimal] = money(nullable=False, server_default=text("0"))

    final_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("payroll_runs.id", ondelete="SET NULL"), nullable=True
    )
    computation_notes: Mapped[Any | None] = mapped_column(Jsonb, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
