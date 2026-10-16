"""The payroll run: lifecycle, calculation, payslips, registers, settlements.

The lifecycle is the point
--------------------------
``draft -> calculated -> reviewed -> approved -> finalized``. Each transition is
a separate permission and a separate audited act, and the numbers may only be
recalculated while the run is in ``draft`` or ``calculated``. Once somebody has
reviewed it, changing the figures underneath them would make the review
meaningless, so the run has to be sent back explicitly - which is itself
recorded.

Finalization is the last gate. With
``payroll.block_finalize_on_unverified_rules`` enabled - the default - a run
that used an unverified statutory rule set cannot be finalized at all. That is
the mechanism behind the brief's requirement that no legal calculation is
claimed to work until its source has been checked.

Where the numbers come from
---------------------------
Payable days are summed from ``attendance_days.payable_day_fraction``, the exact
decimal the attendance engine stored when it classified each day. Payroll never
re-derives a day from punches: one engine decides what a day was worth, and
payroll reads it, so the two cannot disagree.

A day with **no** attendance record is treated as payable and flagged, never as
an absence. The brief forbids classifying a missing punch as absence, and
inferring one here would be the same mistake with money attached.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    ConfigurationMissing,
    Conflict,
    NotFound,
    PolicyViolation,
    ValidationFailed,
)
from app.core.timeutil import utc_now
from app.modules.attendance.models import AttendanceDay
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees.models import Employee
from app.modules.notifications import hooks as notification_hooks
from app.modules.org.models import Department, Designation, WorkLocation
from app.modules.payroll import policy, service
from app.modules.payroll.models import (
    FnfSettlement,
    PayrollAdjustment,
    PayrollPeriod,
    PayrollRun,
    Payslip,
    PayslipLine,
)
from app.modules.settings import service as settings_service

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
ONE = Decimal("1")

#: Statuses that mean the day could not be settled from the punches recorded.
#: A shortfall on one of these is **paid** and flagged, never docked: an
#: unsettled day is a correction to request, and the brief forbids treating a
#: missing punch as absence. Every other shortfall the attendance engine
#: recorded - an absence, the unpaid half of a half day, unpaid leave - is
#: priced as loss of pay, because the engine already decided it was not worked.
UNSETTLED_STATUSES: frozenset[str] = frozenset({"incomplete", "missing_punch"})


@dataclass(frozen=True, slots=True)
class RunSettings:
    """The settings a run was calculated under, captured once."""

    payable_days_basis: str
    fixed_payable_days: int
    rounding_mode: str
    enabled_statutory_modules: list[str]
    block_finalize_on_unverified: bool
    currency: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "payable_days_basis": self.payable_days_basis,
            "fixed_payable_days": self.fixed_payable_days,
            "rounding_mode": self.rounding_mode,
            "enabled_statutory_modules": self.enabled_statutory_modules,
            "block_finalize_on_unverified_rules": self.block_finalize_on_unverified,
            "currency": self.currency,
        }


def load_run_settings(session: Session) -> RunSettings:
    """Read the settings a calculation depends on.

    ``payable_days_basis`` goes through ``require_confirmed``: it is the
    denominator of every loss-of-pay and proration amount in the system, and
    running payroll on an unconfirmed guess would produce plausible numbers that
    are wrong for this company.
    """
    basis = settings_service.require_confirmed(session, "payroll.payable_days_basis")
    enabled = settings_service.get_value(session, "payroll.enabled_statutory_modules", default=[])
    return RunSettings(
        payable_days_basis=str(basis),
        fixed_payable_days=int(
            settings_service.get_value(session, "payroll.fixed_payable_days", default=26)
        ),
        rounding_mode=str(
            settings_service.get_value(session, "payroll.rounding_mode", default="ROUND_HALF_UP")
        ),
        enabled_statutory_modules=list(enabled) if isinstance(enabled, list) else [],
        block_finalize_on_unverified=bool(
            settings_service.get_value(
                session, "payroll.block_finalize_on_unverified_rules", default=True
            )
        ),
        currency=str(settings_service.get_value(session, "payroll.currency", default="INR")),
    )


# ---------------------------------------------------------------------------
# Creating a run
# ---------------------------------------------------------------------------


def create_run(
    session: Session,
    *,
    year: int,
    month: int,
    run_type: str = "regular",
    notes: str | None = None,
    context: audit.AuditContext,
) -> PayrollRun:
    """Open a draft run for a month.

    ``idempotency_key`` is unique over the period and run type, so two regular
    runs for one month are impossible - which matters because the second would
    look like a legitimate register and double the statutory filings.
    """
    if run_type not in ("regular", "supplementary", "fnf"):
        raise ValidationFailed(
            f"Unknown run type {run_type!r}.",
            details=[{"field": "run_type", "message": "Unknown."}],
        )

    period = service.ensure_period(session, year=year, month=month, context=context)
    if period.is_closed:
        raise Conflict(
            f"{year}-{month:02d} is closed. Reopen the period, or post an "
            "adjustment in a later month."
        )

    if run_type == "regular":
        key = f"payroll:{year}-{month:02d}:regular"
    else:
        sequence = (
            session.scalar(
                select(func.count())
                .select_from(PayrollRun)
                .where(PayrollRun.period_id == period.id, PayrollRun.run_type == run_type)
            )
            or 0
        ) + 1
        key = f"payroll:{year}-{month:02d}:{run_type}:{sequence}"

    run = PayrollRun(
        period_id=period.id,
        run_type=run_type,
        status="draft",
        idempotency_key=key,
        notes=notes,
        created_by_user_id=context.actor_user_id,
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as error:
        raise Conflict(
            f"A {run_type} run already exists for {year}-{month:02d}. "
            "Recalculate it rather than creating a second one."
        ) from error

    audit.record(
        session,
        action="payroll.run_created",
        context=context,
        entity_type="payroll_run",
        entity_id=run.id,
        after={"period": f"{year}-{month:02d}", "run_type": run_type, "status": "draft"},
    )
    return run


def get_run(session: Session, run_id: int) -> PayrollRun:
    run = session.scalar(
        select(PayrollRun).where(PayrollRun.id == run_id).options(selectinload(PayrollRun.period))
    )
    if run is None:
        raise NotFound("Payroll run not found.")
    return run


def list_runs(
    session: Session, *, year: int | None = None, status: str | None = None, limit: int = 100
) -> list[PayrollRun]:
    statement = (
        select(PayrollRun)
        .options(selectinload(PayrollRun.period))
        .join(PayrollPeriod)
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc(), PayrollRun.id.desc())
        .limit(limit)
    )
    if year is not None:
        statement = statement.where(PayrollPeriod.year == year)
    if status:
        statement = statement.where(PayrollRun.status == status)
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Attendance input
# ---------------------------------------------------------------------------


def _attendance_for(
    session: Session, *, employee: Employee, period: PayrollPeriod
) -> tuple[policy.PayableDaysInput, list[str]]:
    """Summarise one employee's month from the attendance the engine produced.

    Returns the engine's input and any notes about what was missing. A date with
    no attendance row is counted as payable and reported - never as an absence.
    """
    rows = list(
        session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee.id,
                AttendanceDay.work_date >= period.start_date,
                AttendanceDay.work_date <= period.end_date,
            )
        ).all()
    )

    in_service = [day for day in _dates_in(period) if employee.is_employed_on(day)]
    calendar_days = (period.end_date - period.start_date).days + 1

    present = paid_leave = weekly_off = holiday = unpaid = ZERO
    overtime = 0
    payable_total = ZERO
    unsettled: list[str] = []

    by_date = {row.work_date: row for row in rows}
    for day in in_service:
        row = by_date.get(day)
        if row is None:
            # No record. Not an absence: the brief forbids inferring one, and
            # doing it here would dock pay on missing data.
            payable_total += ONE
            unsettled.append(day.isoformat())
            continue

        payable_total += Decimal(str(row.payable_day_fraction))
        overtime += row.overtime_minutes or 0

        if row.status in ("present", "half_day", "compoff"):
            present += Decimal(str(row.payable_day_fraction))
        elif row.status == "on_leave":
            paid_leave += Decimal(str(row.payable_day_fraction))
        elif row.status == "weekly_off":
            weekly_off += ONE
        elif row.status == "holiday":
            holiday += ONE

        shortfall = ONE - Decimal(str(row.payable_day_fraction))
        if shortfall <= ZERO:
            continue
        if row.status in UNSETTLED_STATUSES:
            # Unsettled, not unpaid. The day is paid in full and flagged so
            # somebody looks at it; docking it on the strength of a missing
            # punch is what the brief forbids.
            payable_total += shortfall
            unsettled.append(day.isoformat())
        else:
            # An absence, the unpaid half of a half day, or leave the leave
            # engine marked unpaid. The attendance engine already decided this
            # by storing a fraction below one; payroll only prices it.
            unpaid += shortfall

    notes: list[str] = []
    if unsettled:
        shown = ", ".join(unsettled[:5])
        more = f" and {len(unsettled) - 5} more" if len(unsettled) > 5 else ""
        notes.append(
            f"{len(unsettled)} day(s) had no settled attendance ({shown}{more}). "
            "They were paid in full and flagged; nothing was treated as absence."
        )

    return (
        policy.PayableDaysInput(
            period_start=period.start_date,
            period_end=period.end_date,
            calendar_days=calendar_days,
            in_service_days=len(in_service),
            attendance_days_recorded=len(rows),
            payable_fraction_total=payable_total,
            present_days=present,
            paid_leave_days=paid_leave,
            weekly_off_days=weekly_off,
            holiday_days=holiday,
            unpaid_days=unpaid,
            overtime_minutes=overtime,
        ),
        notes,
    )


def _dates_in(period: PayrollPeriod) -> list[date]:
    from datetime import timedelta

    span = (period.end_date - period.start_date).days + 1
    return [period.start_date + timedelta(days=offset) for offset in range(span)]


def _working_days_in(session: Session, *, employee: Employee, period: PayrollPeriod) -> int:
    """How many days the company expected work, for the working-days basis.

    Counted from the attendance the engine produced rather than re-deriving the
    calendar here, so the denominator agrees with the day types payroll is
    reading anyway.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(AttendanceDay)
            .where(
                AttendanceDay.employee_id == employee.id,
                AttendanceDay.work_date >= period.start_date,
                AttendanceDay.work_date <= period.end_date,
                AttendanceDay.day_type == "working",
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------


def calculate(
    session: Session,
    run_id: int,
    *,
    employee_ids: list[int] | None = None,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Compute every payslip in a run.

    Idempotent by replacement: calculating again discards the run's payslips and
    rebuilds them, so a correction to somebody's salary or attendance is picked
    up without leaving a half-updated register. That is only allowed while the
    run is open - a reviewed run must be sent back first.

    The rule sets and settings in force are snapshotted onto the run, which is
    what makes this calculation reproducible after either changes.
    """
    run = get_run(session, run_id)
    if not run.is_open:
        raise Conflict(
            f"This run is {run.status}. Send it back to draft before recalculating, "
            "so nobody's review is silently invalidated."
        )

    run_settings = load_run_settings(session)
    period = run.period

    employees = _employees_for(session, period=period, employee_ids=employee_ids)
    if not employees:
        raise PolicyViolation(
            "No employees were in service during this period, so there is nothing to calculate."
        )

    # Replace rather than merge: a stale payslip in a register is worse than a
    # recalculation that takes a moment longer.
    for existing in session.scalars(select(Payslip).where(Payslip.run_id == run.id)).all():
        session.delete(existing)
    session.flush()

    rule_sets: dict[str, service.StatutoryRuleSet] = {}
    snapshot: dict[str, Any] = {}
    for domain in run_settings.enabled_statutory_modules:
        rule_set = service.rule_set_in_force(session, domain=domain, as_of=period.end_date)
        if rule_set is not None:
            rule_sets[domain] = rule_set
            snapshot[domain] = {
                "rule_set_id": rule_set.id,
                "version": rule_set.version_label,
                "status": rule_set.status,
                "effective_from": rule_set.effective_from.isoformat(),
                "source_reference": rule_set.source_reference,
            }

    skipped: list[dict[str, Any]] = []
    unverified: set[str] = set()
    created = 0
    totals = {"gross": ZERO, "deductions": ZERO, "employer": ZERO, "net": ZERO}

    for employee in employees:
        try:
            payslip = _calculate_one(
                session,
                run=run,
                period=period,
                employee=employee,
                run_settings=run_settings,
                rule_sets=rule_sets,
                context=context,
            )
        except (PolicyViolation, ConfigurationMissing) as error:
            # One employee's missing configuration must not abandon the whole
            # run: the rest can be calculated and the gap reported precisely.
            skipped.append(
                {
                    "employee_id": employee.id,
                    "employee_code": employee.employee_code,
                    "reason": str(error),
                }
            )
            continue

        created += 1
        totals["gross"] += payslip.gross_earnings
        totals["deductions"] += payslip.total_deductions
        totals["employer"] += payslip.employer_contributions
        totals["net"] += payslip.net_pay
        for flag in payslip.review_flags or []:
            if flag.get("code") == "unverified_statutory":
                unverified.update(flag.get("domains", []))

    for domain, details in snapshot.items():
        if details["status"] != "verified":
            unverified.add(domain)

    run.status = "calculated"
    run.calculated_at = utc_now()
    run.calculated_by_user_id = context.actor_user_id
    run.rule_set_snapshot = snapshot
    run.settings_snapshot = run_settings.as_dict()
    run.unverified_domains = sorted(unverified)
    run.employee_count = created
    run.total_gross = policy.money(totals["gross"], mode=run_settings.rounding_mode)
    run.total_deductions = policy.money(totals["deductions"], mode=run_settings.rounding_mode)
    run.total_employer_cost = policy.money(totals["employer"], mode=run_settings.rounding_mode)
    run.total_net = policy.money(totals["net"], mode=run_settings.rounding_mode)
    session.flush()

    result = {
        "run_id": run.id,
        "status": run.status,
        "period": f"{period.year}-{period.month:02d}",
        "payslips": created,
        "skipped": skipped,
        "total_gross": str(run.total_gross),
        "total_deductions": str(run.total_deductions),
        "total_employer_cost": str(run.total_employer_cost),
        "total_net": str(run.total_net),
        "unverified_domains": run.unverified_domains,
        "currency": run_settings.currency,
    }
    audit.record(
        session,
        action="payroll.run_calculated",
        context=context,
        entity_type="payroll_run",
        entity_id=run.id,
        after={key: value for key, value in result.items() if key != "skipped"},
        extra={"skipped": skipped} if skipped else None,
    )
    session.flush()
    return result


def _employees_for(
    session: Session, *, period: PayrollPeriod, employee_ids: list[int] | None
) -> list[Employee]:
    """Everybody who was on the rolls for any part of the period.

    Includes leavers: somebody who left on the 10th is owed ten days' pay, and
    excluding them would simply not pay them.
    """
    statement = select(Employee).where(
        Employee.date_of_joining <= period.end_date,
        or_(
            Employee.date_of_exit.is_(None),
            Employee.date_of_exit >= period.start_date,
        ),
    )
    if employee_ids:
        statement = statement.where(Employee.id.in_(employee_ids))
    return list(session.scalars(statement.order_by(Employee.employee_code)).all())


def _calculate_one(
    session: Session,
    *,
    run: PayrollRun,
    period: PayrollPeriod,
    employee: Employee,
    run_settings: RunSettings,
    rule_sets: dict[str, Any],
    context: audit.AuditContext,
) -> Payslip:
    """One employee's payslip, with every figure's basis recorded."""
    salary = service.salary_on(session, employee_id=employee.id, as_of=period.end_date)
    if salary is None:
        salary = service.salary_on(session, employee_id=employee.id, as_of=period.start_date)
    if salary is None:
        raise PolicyViolation(
            f"{employee.employee_code} has no salary revision in force for "
            f"{period.year}-{period.month:02d}."
        )

    attendance, attendance_notes = _attendance_for(session, employee=employee, period=period)
    try:
        payable = policy.compute_payable_days(
            attendance,
            basis=run_settings.payable_days_basis,
            fixed_days=run_settings.fixed_payable_days,
            working_days_in_period=(
                _working_days_in(session, employee=employee, period=period)
                if run_settings.payable_days_basis == "working_days"
                else None
            ),
        )
    except policy.PayrollRuleViolation as error:
        raise PolicyViolation(str(error)) from error

    specs: list[policy.ComponentSpec] = []
    for item in sorted(salary.components, key=lambda c: c.sequence):
        component = service.get_component(session, item.component_id)
        specs.append(
            policy.ComponentSpec(
                component_id=component.id,
                code=component.code,
                label=component.name,
                component_type=component.component_type,
                # The amount was resolved at the revision, so it is a fixed
                # figure here. Re-resolving percentages now would let a
                # structure edit change a past month.
                calculation_type="fixed",
                monthly_amount=item.monthly_amount,
                is_pf_wage=component.is_pf_wage,
                is_esi_wage=component.is_esi_wage,
                is_pt_wage=component.is_pt_wage,
                is_taxable=component.is_taxable,
                prorates_on_payable_days=component.prorates_on_payable_days,
                sequence=item.sequence,
            )
        )

    try:
        lines, bases = policy.compute_earnings(
            specs,
            gross_monthly=salary.gross_monthly,
            earning_ratio=payable.earning_ratio,
            rounding=run_settings.rounding_mode,
        )
    except policy.PayrollRuleViolation as error:
        raise PolicyViolation(str(error)) from error

    eligibility = service.eligibility_on(session, employee_id=employee.id, as_of=period.end_date)
    state_code = None
    if employee.work_location_id:
        location = session.get(WorkLocation, employee.work_location_id)
        state_code = location.state if location else None

    engine_rule_sets: dict[str, policy.RuleSet] = {}
    for domain, rule_set in rule_sets.items():
        resolved = rule_set
        if domain in ("pt", "lwf") and state_code:
            # Professional tax is per state, so prefer that state's set.
            state_specific = service.rule_set_in_force(
                session, domain=domain, as_of=period.end_date, state_code=state_code
            )
            if state_specific is not None:
                resolved = state_specific
        engine_rule_sets[domain] = service.as_policy_rule_set(resolved)

    try:
        statutory = policy.compute_statutory(
            bases=bases,
            rule_sets=engine_rule_sets,
            eligibility=eligibility,
            enabled_domains=run_settings.enabled_statutory_modules,
            rounding=run_settings.rounding_mode,
        )
    except policy.StatutoryRulesMissing as error:
        raise PolicyViolation(str(error)) from error

    all_lines = lines + statutory.employee_lines + statutory.employer_lines

    # Adjustments pending for this employee are paid or recovered here.
    adjustment_lines, applied = _adjustment_lines(
        session, employee=employee, rounding=run_settings.rounding_mode
    )
    all_lines += adjustment_lines

    totals = policy.total_payslip(all_lines, rounding=run_settings.rounding_mode)

    previous_net = session.scalar(
        select(Payslip.net_pay)
        .join(PayrollRun)
        .join(PayrollPeriod)
        .where(
            Payslip.employee_id == employee.id,
            PayrollRun.status == "finalized",
            PayrollPeriod.start_date < period.start_date,
        )
        .order_by(PayrollPeriod.start_date.desc())
        .limit(1)
    )

    flags = policy.review_flags(totals=totals, payable=payable, previous_net=previous_net)
    if statutory.unverified_domains:
        flags.append(
            {
                "code": "unverified_statutory",
                "message": (
                    "Computed with unverified rule set(s): "
                    f"{', '.join(statutory.unverified_domains)}. The run cannot be "
                    "finalized until they are verified against their source."
                ),
                "domains": statutory.unverified_domains,
            }
        )
    if attendance_notes:
        flags.append(
            {
                "code": "attendance_incomplete",
                "message": attendance_notes[0],
            }
        )

    designation = (
        session.get(Designation, employee.designation_id) if employee.designation_id else None
    )
    department = session.get(Department, employee.department_id) if employee.department_id else None
    location = (
        session.get(WorkLocation, employee.work_location_id) if employee.work_location_id else None
    )

    payslip = Payslip(
        run_id=run.id,
        employee_id=employee.id,
        employee_salary_id=salary.id,
        employee_code=employee.employee_code,
        employee_name=employee.full_name,
        designation_name=designation.name if designation else None,
        department_name=department.name if department else None,
        work_location_name=location.name if location else None,
        days_in_period=payable.days_in_period,
        payable_days=payable.payable_days,
        lop_days=payable.lop_days,
        paid_leave_days=policy.day_quantity(attendance.paid_leave_days),
        present_days=policy.day_quantity(attendance.present_days),
        weekly_off_days=policy.day_quantity(attendance.weekly_off_days),
        holiday_days=policy.day_quantity(attendance.holiday_days),
        overtime_minutes=attendance.overtime_minutes,
        gross_earnings=totals.gross_earnings,
        total_deductions=totals.total_deductions,
        employer_contributions=totals.employer_contributions,
        net_pay=totals.net_pay,
        is_prorated=payable.is_prorated,
        proration_reason=payable.reason if payable.is_prorated else None,
        review_flags=flags or None,
        calculation_notes={
            "payable_days": payable.notes,
            "attendance": attendance_notes,
            "statutory": statutory.notes,
            "wage_bases": {
                "gross": str(bases.gross),
                "pf_wage": str(bases.pf_wage),
                "esi_wage": str(bases.esi_wage),
                "pt_wage": str(bases.pt_wage),
                "taxable": str(bases.taxable),
            },
        },
    )
    session.add(payslip)
    session.flush()

    for line in sorted(all_lines, key=lambda row: (row.sequence, row.line_code)):
        session.add(
            PayslipLine(
                payslip_id=payslip.id,
                component_id=line.component_id,
                line_code=line.line_code,
                label=line.label,
                component_type=line.component_type,
                amount=line.amount,
                full_amount=line.full_amount,
                basis_amount=line.basis_amount,
                basis_description=line.basis_description,
                statutory_rule_set_id=line.statutory_rule_set_id,
                sequence=line.sequence,
            )
        )

    for adjustment in applied:
        adjustment.status = "applied"
        adjustment.applied_in_run_id = run.id
        adjustment.applied_at = utc_now()

    session.flush()
    return payslip


def _adjustment_lines(
    session: Session, *, employee: Employee, rounding: str
) -> tuple[list[policy.ComputedLine], list[PayrollAdjustment]]:
    """Pending arrears, bonuses and recoveries as payslip lines.

    An arrear or bonus is an earning; a recovery or reversal is a deduction. The
    stored amount is always positive and the type decides the sign here, in one
    place, so no caller can produce a recovery that pays somebody.
    """
    pending = service.pending_adjustments(session, employee_id=employee.id)
    lines: list[policy.ComputedLine] = []
    for index, adjustment in enumerate(pending):
        is_earning = adjustment.adjustment_type in ("arrear", "bonus")
        lines.append(
            policy.ComputedLine(
                line_code=f"ADJ{adjustment.id}",
                label=adjustment.label,
                component_type="earning" if is_earning else "deduction",
                amount=policy.money(adjustment.amount, mode=rounding),
                basis_amount=adjustment.amount,
                basis_description=(
                    f"{adjustment.adjustment_type.capitalize()}: {adjustment.reason[:200]}"
                ),
                sequence=700 + index,
            )
        )
    return lines, pending


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


def _transition(
    session: Session,
    run: PayrollRun,
    *,
    expected: tuple[str, ...],
    to: str,
    action: str,
    context: audit.AuditContext,
    extra: dict[str, Any] | None = None,
) -> PayrollRun:
    if run.status not in expected:
        raise Conflict(
            f"This run is {run.status}; it can be {to} only from {' or '.join(expected)}."
        )
    run.status = to
    now = utc_now()
    if to == "reviewed":
        run.reviewed_at = now
        run.reviewed_by_user_id = context.actor_user_id
    elif to == "approved":
        run.approved_at = now
        run.approved_by_user_id = context.actor_user_id
    elif to == "finalized":
        run.finalized_at = now
        run.finalized_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action=action,
        context=context,
        entity_type="payroll_run",
        entity_id=run.id,
        after={"status": to, **(extra or {})},
    )
    return run


def review(session: Session, run_id: int, *, user: User, context: audit.AuditContext) -> PayrollRun:
    """Mark a calculated run as reviewed.

    Nobody may review a run they calculated themselves. One pair of eyes over a
    whole company's pay is not a review, and the separation is enforced here
    rather than being left to procedure.
    """
    run = get_run(session, run_id)
    if run.calculated_by_user_id is not None and run.calculated_by_user_id == user.id:
        raise PolicyViolation(
            "You calculated this run, so somebody else must review it. One pair "
            "of eyes over a whole company's pay is not a review."
        )
    flagged = session.scalar(
        select(func.count())
        .select_from(Payslip)
        .where(Payslip.run_id == run.id, Payslip.review_flags.isnot(None))
    )
    return _transition(
        session,
        run,
        expected=("calculated",),
        to="reviewed",
        action="payroll.run_reviewed",
        context=context,
        extra={"flagged_payslips": flagged or 0},
    )


def approve(
    session: Session, run_id: int, *, user: User, context: audit.AuditContext
) -> PayrollRun:
    """Approve a reviewed run.

    The approver may not be the reviewer, for the same reason.
    """
    run = get_run(session, run_id)
    if run.reviewed_by_user_id is not None and run.reviewed_by_user_id == user.id:
        raise PolicyViolation("You reviewed this run, so somebody else must approve it.")
    return _transition(
        session,
        run,
        expected=("reviewed",),
        to="approved",
        action="payroll.run_approved",
        context=context,
    )


def finalize(session: Session, run_id: int, *, context: audit.AuditContext) -> PayrollRun:
    """Finalize an approved run, after which it is immutable.

    This is where the brief's requirement about legal calculations is enforced:
    while ``payroll.block_finalize_on_unverified_rules`` is on, a run that used
    an unverified statutory rule set **cannot** be finalized. The numbers may be
    inspected, but they cannot become the record of what was paid.
    """
    run = get_run(session, run_id)
    settings = load_run_settings(session)

    if run.status != "approved":
        raise Conflict(f"This run is {run.status}; only an approved run can be finalized.")

    if settings.block_finalize_on_unverified and run.unverified_domains:
        raise PolicyViolation(
            "This run used unverified statutory rule set(s): "
            f"{', '.join(run.unverified_domains)}. Verify them against their "
            "published source first, or recalculate the run. "
            "('payroll.block_finalize_on_unverified_rules' can be turned off, "
            "but that is the setting that stops unchecked figures being paid.)"
        )

    negative = session.scalar(
        select(func.count())
        .select_from(Payslip)
        .where(Payslip.run_id == run.id, Payslip.net_pay < 0)
    )
    return _transition(
        session,
        run,
        expected=("approved",),
        to="finalized",
        action="payroll.run_finalized",
        context=context,
        extra={
            "total_net": str(run.total_net),
            "employee_count": run.employee_count,
            "payslips_with_negative_net": negative or 0,
        },
    )


def send_back(
    session: Session, run_id: int, *, reason: str, context: audit.AuditContext
) -> PayrollRun:
    """Return a reviewed or approved run to draft so it can be recalculated.

    Explicit rather than implicit: recalculating under a reviewer would
    invalidate their review without telling anybody, so the step is recorded
    with a reason.
    """
    run = get_run(session, run_id)
    if run.status == "finalized":
        raise Conflict("A finalized run cannot be reopened. Post an adjustment in a later run.")
    if run.status not in ("calculated", "reviewed", "approved"):
        raise Conflict(f"This run is {run.status}; there is nothing to send back.")
    if not reason.strip():
        raise ValidationFailed(
            "Sending a run back needs a reason.",
            details=[{"field": "reason", "message": "Required."}],
        )

    before = run.status
    run.status = "draft"
    run.reviewed_at = None
    run.reviewed_by_user_id = None
    run.approved_at = None
    run.approved_by_user_id = None
    session.flush()

    audit.record(
        session,
        action="payroll.run_sent_back",
        context=context,
        entity_type="payroll_run",
        entity_id=run.id,
        before={"status": before},
        after={"status": "draft", "reason": reason.strip()},
    )
    return run


def cancel(
    session: Session, run_id: int, *, reason: str, context: audit.AuditContext
) -> PayrollRun:
    """Cancel a run that should not have existed.

    The row stays, because its id may already have been quoted. A finalized run
    cannot be cancelled: it is the record of money that moved.
    """
    run = get_run(session, run_id)
    if run.status == "finalized":
        raise Conflict("A finalized run cannot be cancelled. Post a reversal adjustment instead.")
    if run.status == "cancelled":
        raise Conflict("This run is already cancelled.")
    if not reason.strip():
        raise ValidationFailed(
            "Cancelling a run needs a reason.",
            details=[{"field": "reason", "message": "Required."}],
        )

    # Release any adjustments this run had claimed, so they are paid in the next.
    for adjustment in session.scalars(
        select(PayrollAdjustment).where(PayrollAdjustment.applied_in_run_id == run.id)
    ).all():
        adjustment.status = "pending"
        adjustment.applied_in_run_id = None
        adjustment.applied_at = None

    run.status = "cancelled"
    run.cancelled_at = utc_now()
    run.cancellation_reason = reason.strip()
    session.flush()

    audit.record(
        session,
        action="payroll.run_cancelled",
        context=context,
        entity_type="payroll_run",
        entity_id=run.id,
        after={"reason": reason.strip()},
    )
    return run


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------


def publish_payslips(
    session: Session, run_id: int, *, context: audit.AuditContext
) -> dict[str, Any]:
    """Release a finalized run's payslips to employees, and tell them.

    Separate from finalizing, because the register may be finalized for the
    bank before HR is ready for everybody to see their payslip. Idempotent: a
    second call publishes nothing further, and the notification is keyed per
    payslip so nobody is told twice.
    """
    run = get_run(session, run_id)
    if run.status != "finalized":
        raise Conflict(
            f"This run is {run.status}. Only a finalized run's payslips can be published."
        )

    period = run.period
    label = f"{period.start_date.strftime('%B %Y')}"
    now = utc_now()
    published = 0
    notified = 0

    for payslip in session.scalars(select(Payslip).where(Payslip.run_id == run.id)).all():
        if payslip.published_at is not None:
            continue
        payslip.published_at = now
        published += 1
        notified += notification_hooks.payslip_published(
            session,
            payslip_id=payslip.id,
            employee_id=payslip.employee_id,
            period_label=label,
        )
    session.flush()

    result = {
        "run_id": run.id,
        "published": published,
        "notifications_created": notified,
        "period": label,
    }
    if published:
        audit.record(
            session,
            action="payroll.payslips_published",
            context=context,
            entity_type="payroll_run",
            entity_id=run.id,
            after=result,
        )
        session.flush()
    return result


def get_payslip(session: Session, payslip_id: int) -> Payslip:
    payslip = session.scalar(
        select(Payslip)
        .where(Payslip.id == payslip_id)
        .options(selectinload(Payslip.lines), selectinload(Payslip.run))
    )
    if payslip is None:
        raise NotFound("Payslip not found.")
    return payslip


def payslips_in_run(session: Session, run_id: int) -> list[Payslip]:
    return list(
        session.scalars(
            select(Payslip)
            .where(Payslip.run_id == run_id)
            .options(selectinload(Payslip.lines))
            .order_by(Payslip.employee_code)
        ).all()
    )


def payslips_for_employee(
    session: Session, *, employee_id: int, published_only: bool = True, limit: int = 36
) -> list[Payslip]:
    """One employee's payslips, newest first.

    ``published_only`` defaults to true for self-service: a payslip from a run
    still being reviewed is not something the employee should see, because the
    figures may still change.
    """
    statement = (
        select(Payslip)
        .join(PayrollRun)
        .join(PayrollPeriod)
        .where(Payslip.employee_id == employee_id)
        .options(selectinload(Payslip.lines), selectinload(Payslip.run))
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
        .limit(limit)
    )
    if published_only:
        statement = statement.where(Payslip.published_at.isnot(None))
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Registers and reconciliation
# ---------------------------------------------------------------------------


def register(session: Session, run_id: int) -> dict[str, Any]:
    """The run as a register: one row per employee, totals at the bottom.

    What the bank transfer and the statutory filings are prepared from - by a
    human, from this data. Neither is performed by this system.
    """
    run = get_run(session, run_id)
    payslips = payslips_in_run(session, run_id)

    rows = []
    for payslip in payslips:
        earnings = {
            line.line_code: str(line.amount)
            for line in payslip.lines
            if line.component_type in ("earning", "reimbursement")
        }
        deductions = {
            line.line_code: str(line.amount)
            for line in payslip.lines
            if line.component_type == "deduction"
        }
        employer = {
            line.line_code: str(line.amount)
            for line in payslip.lines
            if line.component_type == "employer_contribution"
        }
        rows.append(
            {
                "payslip_id": payslip.id,
                "employee_id": payslip.employee_id,
                "employee_code": payslip.employee_code,
                "employee_name": payslip.employee_name,
                "department": payslip.department_name,
                "payable_days": str(payslip.payable_days),
                "lop_days": str(payslip.lop_days),
                "earnings": earnings,
                "deductions": deductions,
                "employer_contributions": employer,
                "gross_earnings": str(payslip.gross_earnings),
                "total_deductions": str(payslip.total_deductions),
                "net_pay": str(payslip.net_pay),
                "flags": [flag.get("code") for flag in payslip.review_flags or []],
            }
        )

    return {
        "run_id": run.id,
        "period": f"{run.period.year}-{run.period.month:02d}",
        "status": run.status,
        "run_type": run.run_type,
        "employee_count": run.employee_count,
        "total_gross": str(run.total_gross),
        "total_deductions": str(run.total_deductions),
        "total_employer_cost": str(run.total_employer_cost),
        "total_net": str(run.total_net),
        "rule_sets_used": run.rule_set_snapshot or {},
        "settings_used": run.settings_snapshot or {},
        "unverified_domains": run.unverified_domains or [],
        "rows": rows,
    }


def reconciliation(session: Session, run_id: int) -> dict[str, Any]:
    """Check the run against itself.

    Every figure is re-summed from the payslip lines rather than read from the
    stored totals, so a mismatch means the stored totals are wrong - which is
    exactly what a reconciliation is for. A payroll that cannot prove it adds up
    should not be paid.
    """
    run = get_run(session, run_id)
    payslips = payslips_in_run(session, run_id)

    line_gross = ZERO
    line_deductions = ZERO
    line_employer = ZERO
    payslip_gross = ZERO
    payslip_deductions = ZERO
    payslip_net = ZERO
    mismatched: list[dict[str, Any]] = []

    for payslip in payslips:
        own_gross = sum(
            (
                line.amount
                for line in payslip.lines
                if line.component_type in ("earning", "reimbursement")
            ),
            ZERO,
        )
        own_deductions = sum(
            (line.amount for line in payslip.lines if line.component_type == "deduction"),
            ZERO,
        )
        own_employer = sum(
            (
                line.amount
                for line in payslip.lines
                if line.component_type == "employer_contribution"
            ),
            ZERO,
        )

        line_gross += own_gross
        line_deductions += own_deductions
        line_employer += own_employer
        payslip_gross += payslip.gross_earnings
        payslip_deductions += payslip.total_deductions
        payslip_net += payslip.net_pay

        if (
            own_gross != payslip.gross_earnings
            or own_deductions != payslip.total_deductions
            or payslip.net_pay != payslip.gross_earnings - payslip.total_deductions
        ):
            mismatched.append(
                {
                    "payslip_id": payslip.id,
                    "employee_code": payslip.employee_code,
                    "lines_gross": str(own_gross),
                    "stored_gross": str(payslip.gross_earnings),
                    "lines_deductions": str(own_deductions),
                    "stored_deductions": str(payslip.total_deductions),
                    "stored_net": str(payslip.net_pay),
                }
            )

    balanced = (
        not mismatched
        and line_gross == payslip_gross
        and line_deductions == payslip_deductions
        and payslip_gross == run.total_gross
        and payslip_deductions == run.total_deductions
        and payslip_net == run.total_net
        and line_employer == run.total_employer_cost
    )
    return {
        "run_id": run.id,
        "balanced": balanced,
        "payslip_count": len(payslips),
        "lines_gross": str(line_gross),
        "lines_deductions": str(line_deductions),
        "lines_employer": str(line_employer),
        "payslips_gross": str(payslip_gross),
        "payslips_deductions": str(payslip_deductions),
        "payslips_net": str(payslip_net),
        "run_gross": str(run.total_gross),
        "run_deductions": str(run.total_deductions),
        "run_net": str(run.total_net),
        "run_employer_cost": str(run.total_employer_cost),
        "mismatched": mismatched,
    }


def statutory_summary(session: Session, run_id: int) -> dict[str, Any]:
    """Per-domain totals, which is what a statutory filing is prepared from.

    The filing itself is out of scope and explicitly not performed: this is the
    data somebody needs to make it, with the rule set each figure came from.
    """
    run = get_run(session, run_id)
    rows = session.execute(
        select(
            PayslipLine.line_code,
            PayslipLine.label,
            PayslipLine.component_type,
            func.count(),
            func.sum(PayslipLine.amount),
            PayslipLine.statutory_rule_set_id,
        )
        .join(Payslip)
        .where(Payslip.run_id == run_id, PayslipLine.statutory_rule_set_id.isnot(None))
        .group_by(
            PayslipLine.line_code,
            PayslipLine.label,
            PayslipLine.component_type,
            PayslipLine.statutory_rule_set_id,
        )
        .order_by(PayslipLine.line_code)
    ).all()

    return {
        "run_id": run.id,
        "period": f"{run.period.year}-{run.period.month:02d}",
        "status": run.status,
        "lines": [
            {
                "line_code": row[0],
                "label": row[1],
                "component_type": row[2],
                "employee_count": row[3],
                "total_amount": str(row[4] or ZERO),
                "rule_set_id": row[5],
            }
            for row in rows
        ],
        "rule_sets_used": run.rule_set_snapshot or {},
        "note": (
            "This is the data a statutory return is prepared from. No filing is "
            "performed by this system."
        ),
    }


# ---------------------------------------------------------------------------
# Full and final settlement
# ---------------------------------------------------------------------------


def calculate_settlement(
    session: Session,
    *,
    employee_id: int,
    encashable_leave_days: Decimal,
    notice_shortfall_days: Decimal,
    other_dues: Decimal = ZERO,
    other_recoveries: Decimal = ZERO,
    notes: str | None = None,
    context: audit.AuditContext,
) -> FnfSettlement:
    """Compute a leaver's settlement.

    Gratuity comes only from a verified rule set. Without one the line is zero
    and the reason is recorded, because an invented gratuity figure would
    understate or overstate a statutory payment somebody is legally owed - and
    would look equally plausible either way.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")
    if employee.date_of_exit is None:
        raise PolicyViolation(
            f"{employee.employee_code} has no exit date recorded, so there is "
            "nothing to settle. Record the exit first."
        )

    exit_date = employee.last_working_day or employee.date_of_exit
    salary = service.salary_on(session, employee_id=employee.id, as_of=exit_date)
    if salary is None:
        raise PolicyViolation(
            f"{employee.employee_code} has no salary revision in force on {exit_date.isoformat()}."
        )

    run_settings = load_run_settings(session)
    basis_days = (
        Decimal(run_settings.fixed_payable_days)
        if run_settings.payable_days_basis == "fixed_days"
        else Decimal("30")
    )
    per_day = policy.money(salary.gross_monthly / basis_days, mode=run_settings.rounding_mode)

    # Which components count towards gratuity is a company decision, recorded on
    # the components themselves: those that form the PF wage are the usual
    # choice, and using the whole gross instead would overstate it.
    gratuity_wage = ZERO
    for item in salary.components:
        component = service.get_component(session, item.component_id)
        if component.is_pf_wage:
            gratuity_wage += item.monthly_amount

    service_years = Decimal((exit_date - employee.date_of_joining).days) / Decimal("365.25")
    service_years = service_years.quantize(Decimal("0.01"))

    gratuity_rules = service.rule_set_in_force(session, domain="gratuity", as_of=exit_date)
    engine_rules = (
        service.as_policy_rule_set(gratuity_rules) if gratuity_rules is not None else None
    )

    try:
        outcome = policy.compute_settlement(
            policy.SettlementInput(
                monthly_gross=salary.gross_monthly,
                gratuity_wage=gratuity_wage,
                completed_service_years=service_years,
                encashable_leave_days=encashable_leave_days,
                per_day_wage=per_day,
                notice_shortfall_days=notice_shortfall_days,
                other_dues=other_dues,
                other_recoveries=other_recoveries,
            ),
            gratuity_rules=engine_rules,
            rounding=run_settings.rounding_mode,
        )
    except policy.StatutoryRulesMissing as error:
        raise PolicyViolation(str(error)) from error

    settlement = session.scalar(
        select(FnfSettlement).where(FnfSettlement.employee_id == employee.id)
    )
    if settlement is None:
        settlement = FnfSettlement(
            employee_id=employee.id,
            exit_date=employee.date_of_exit,
            last_working_date=employee.last_working_day,
            created_by_user_id=context.actor_user_id,
        )
        session.add(settlement)
    elif settlement.status in ("approved", "paid"):
        raise Conflict(
            f"This settlement is {settlement.status} and cannot be recalculated. "
            "Post an adjustment instead."
        )

    settlement.status = "calculated"
    settlement.leave_encashment_days = policy.day_quantity(encashable_leave_days)
    settlement.leave_encashment_amount = outcome.leave_encashment_amount
    settlement.notice_shortfall_days = policy.day_quantity(notice_shortfall_days)
    settlement.notice_recovery_amount = outcome.notice_recovery_amount
    settlement.gratuity_amount = outcome.gratuity_amount
    settlement.gratuity_rule_set_id = outcome.gratuity_rule_set_id
    settlement.other_dues = policy.money(other_dues, mode=run_settings.rounding_mode)
    settlement.other_recoveries = policy.money(other_recoveries, mode=run_settings.rounding_mode)
    settlement.net_settlement = outcome.net_settlement
    settlement.computation_notes = {
        "per_day_wage": str(per_day),
        "gratuity_wage": str(gratuity_wage),
        "completed_service_years": str(service_years),
        "notes": outcome.notes,
        "gratuity_rule_set": (
            {
                "id": gratuity_rules.id,
                "version": gratuity_rules.version_label,
                "status": gratuity_rules.status,
            }
            if gratuity_rules
            else None
        ),
    }
    settlement.notes = notes
    session.flush()

    audit.record(
        session,
        action="payroll.settlement_calculated",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "employee_code": employee.employee_code,
            "net_settlement": str(settlement.net_settlement),
            "gratuity_amount": str(settlement.gratuity_amount),
            "gratuity_rule_set_id": settlement.gratuity_rule_set_id,
            "leave_encashment_amount": str(settlement.leave_encashment_amount),
            "notice_recovery_amount": str(settlement.notice_recovery_amount),
        },
    )
    return settlement


def approve_settlement(
    session: Session, employee_id: int, *, context: audit.AuditContext
) -> FnfSettlement:
    """Approve a calculated settlement.

    Refused while the gratuity figure rests on an unverified rule set: that is a
    statutory payment, and approving it unchecked is exactly what the
    verification gate exists to prevent.
    """
    settlement = session.scalar(
        select(FnfSettlement).where(FnfSettlement.employee_id == employee_id)
    )
    if settlement is None:
        raise NotFound("No settlement has been calculated for this employee.")
    if settlement.status != "calculated":
        raise Conflict(f"This settlement is {settlement.status}.")

    if settlement.gratuity_amount > ZERO and settlement.gratuity_rule_set_id is not None:
        rule_set = service.get_rule_set(session, settlement.gratuity_rule_set_id)
        if rule_set.status != "verified":
            raise PolicyViolation(
                "The gratuity figure was computed from an unverified rule set. "
                "Verify it against its published source before approving a "
                "statutory payment."
            )

    settlement.status = "approved"
    settlement.approved_at = utc_now()
    session.flush()

    audit.record(
        session,
        action="payroll.settlement_approved",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={"net_settlement": str(settlement.net_settlement)},
    )
    return settlement


def get_settlement(session: Session, employee_id: int) -> FnfSettlement:
    settlement = session.scalar(
        select(FnfSettlement).where(FnfSettlement.employee_id == employee_id)
    )
    if settlement is None:
        raise NotFound("No settlement has been calculated for this employee.")
    return settlement
