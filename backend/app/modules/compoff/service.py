"""Comp-off orchestration: earning credits, verifying them, and spending them.

Credits come from attendance, not from a claim
----------------------------------------------
A credit cites the ``attendance_day`` that proves it - the day type and the
minutes actually worked, derived from punches. The brief requires comp-off to
come from *verified qualifying work*, and that row is the verification. The
credit job reads attendance; it does not take an employee's word for it.

Consumption is lot-accurate
---------------------------
Each credit expires on its own date, so spending "a day" has to record which
credit it drew on. Credits are consumed earliest-expiring first, so an employee
never loses one they could have used. See
:mod:`app.modules.compoff.policy` for the ordering and the arithmetic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    PolicyViolation,
    ValidationFailed,
)
from app.core.timeutil import utc_now
from app.modules.attendance.models import AttendanceDay
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.compoff import policy as rules
from app.modules.compoff.models import (
    CompoffAllocation,
    CompoffApplication,
    CompoffApplicationDay,
    CompoffApproval,
    CompoffCredit,
)
from app.modules.employees.models import Employee
from app.modules.leave import policy as leave_rules
from app.modules.notifications import hooks as notification_hooks
from app.modules.settings import service as settings_service
from app.modules.shifts import service as shifts_service

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
OPEN_STATUSES = ("pending_manager", "pending_hr")
BLOCKING_STATUSES = ("pending_manager", "pending_hr", "approved")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_rules(session: Session) -> rules.CreditRules:
    """The configured thresholds, resolved once per operation."""
    get = settings_service.get_value
    qualifying = get(session, "compoff.qualifying_day_types", default=["weekly_off", "holiday"])
    try:
        return rules.CreditRules(
            qualifying_day_types=frozenset(qualifying if isinstance(qualifying, list) else []),
            min_minutes_full_credit=int(
                get(session, "compoff.min_minutes_full_credit", default=480)
            ),
            min_minutes_half_credit=int(
                get(session, "compoff.min_minutes_half_credit", default=240)
            ),
            expiry_days=int(get(session, "compoff.expiry_days", default=90)),
            requires_verification=bool(
                get(session, "compoff.requires_hr_verification", default=True)
            ),
        )
    except rules.CompoffRuleViolation as error:
        # A contradictory configuration must stop the job rather than quietly
        # grant the wrong credit.
        raise PolicyViolation(
            str(error), details=[{"field": error.field_name, "message": str(error)}]
        ) from error


def _assert_enabled(session: Session) -> None:
    if not bool(settings_service.get_value(session, "compoff.enabled", default=True)):
        raise PolicyViolation(
            "Comp-off is disabled for this company. Enable compoff.enabled in settings first."
        )


# ---------------------------------------------------------------------------
# Earning
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CreditRunReport:
    days_examined: int = 0
    credits_created: int = 0
    already_credited: int = 0
    not_qualifying: int = 0
    days_credited: Decimal = ZERO
    problems: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "days_examined": self.days_examined,
            "credits_created": self.credits_created,
            "already_credited": self.already_credited,
            "not_qualifying": self.not_qualifying,
            "days_credited": f"{self.days_credited:.2f}",
            "problems": self.problems[:50],
        }


def grant_credits(
    session: Session,
    *,
    start: date,
    end: date,
    employee_id: int | None = None,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Create comp-off credits from attendance in a date range.

    Safe to run repeatedly: ``unique(employee_id, earned_date)`` means a day
    earns credit once, however many times attendance for it is recomputed or
    this job is re-run. ``already_credited`` counts the repeats, so a second run
    reports "nothing to do" rather than silently doubling an entitlement.

    Only days whose type is configured as qualifying are considered. Overtime on
    an ordinary working day is overtime, not a day off owed - treating it as
    comp-off would hand out leave for every late evening.
    """
    _assert_enabled(session)
    if end < start:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "end", "message": "Must not precede the start."}],
        )

    credit_rules = load_rules(session)
    report = CreditRunReport()

    statement = select(AttendanceDay).where(
        AttendanceDay.work_date >= start,
        AttendanceDay.work_date <= end,
        AttendanceDay.day_type.in_(sorted(credit_rules.qualifying_day_types)),
        AttendanceDay.net_minutes > 0,
    )
    if employee_id is not None:
        statement = statement.where(AttendanceDay.employee_id == employee_id)

    snapshot = {
        "min_minutes_full_credit": credit_rules.min_minutes_full_credit,
        "min_minutes_half_credit": credit_rules.min_minutes_half_credit,
        "expiry_days": credit_rules.expiry_days,
        "qualifying_day_types": sorted(credit_rules.qualifying_day_types),
        "requires_verification": credit_rules.requires_verification,
    }

    for day in session.scalars(statement.order_by(AttendanceDay.work_date)).all():
        report.days_examined += 1

        outcome = rules.evaluate_day(
            work_date=day.work_date,
            day_type=day.day_type,
            net_minutes=day.net_minutes,
            rules=credit_rules,
        )
        if outcome.earns_nothing:
            report.not_qualifying += 1
            continue

        credit = CompoffCredit(
            employee_id=day.employee_id,
            earned_date=day.work_date,
            attendance_day_id=day.id,
            source="attendance",
            day_type=day.day_type,
            worked_minutes=day.net_minutes,
            credit_days=outcome.credit_days,
            allocated_days=ZERO,
            expiry_date=outcome.expiry_date,
            status=outcome.status,
            decision_note=outcome.reason,
            policy_snapshot=snapshot,
        )
        try:
            with session.begin_nested():
                session.add(credit)
                session.flush()
        except IntegrityError:
            # Already credited for that day. The savepoint discards only this
            # insert, so the rest of the run continues.
            report.already_credited += 1
            continue

        report.credits_created += 1
        report.days_credited += outcome.credit_days

        # Keyed on the credit, so a re-run that creates nothing notifies
        # nothing: only a credit that was actually inserted is announced.
        notification_hooks.compoff_credited(
            session,
            credit_id=credit.id,
            employee_id=credit.employee_id,
            earned_date=credit.earned_date,
            credit_days=credit.credit_days,
            needs_verification=credit.status != "available",
        )

    audit.record(
        session,
        action="compoff.credits_granted",
        context=context,
        entity_type="job",
        entity_id="compoff_credits",
        extra={**report.as_dict(), "from": start.isoformat(), "to": end.isoformat()},
    )
    session.flush()
    return {**report.as_dict(), "from": start.isoformat(), "to": end.isoformat()}


def grant_manual_credit(
    session: Session,
    *,
    employee_id: int,
    earned_date: date,
    credit_days: Decimal,
    reason: str,
    context: audit.AuditContext,
) -> CompoffCredit:
    """Grant a credit by hand, where attendance cannot prove it.

    Flagged ``source='manual'`` and requires a reason, because a credit with no
    attendance behind it is exactly the thing an auditor would question.
    """
    _assert_enabled(session)
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required for a manual comp-off credit.",
            details=[{"field": "reason", "message": "Required."}],
        )
    if credit_days <= ZERO or credit_days > Decimal("2"):
        raise ValidationFailed(
            "A manual credit must be between 0 and 2 days.",
            details=[{"field": "credit_days", "message": "Out of range."}],
        )

    credit_rules = load_rules(session)
    expiry = (
        earned_date + timedelta(days=credit_rules.expiry_days)
        if credit_rules.expiry_days > 0
        else None
    )

    credit = CompoffCredit(
        employee_id=employee_id,
        earned_date=earned_date,
        attendance_day_id=None,
        source="manual",
        day_type="working",
        worked_minutes=0,
        credit_days=credit_days,
        allocated_days=ZERO,
        expiry_date=expiry,
        # A manual grant is already a deliberate act by someone with the
        # permission, so it does not wait for a second verification.
        status="available",
        verified_by_user_id=context.actor_user_id,
        verified_at=utc_now(),
        decision_note=reason.strip(),
    )
    try:
        with session.begin_nested():
            session.add(credit)
            session.flush()
    except IntegrityError as exc:
        raise Conflict(
            f"{employee.employee_code} already has a comp-off credit for "
            f"{earned_date.isoformat()}. A day earns credit once."
        ) from exc

    audit.record(
        session,
        action="compoff.manual_credit_granted",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "employee_code": employee.employee_code,
            "earned_date": earned_date.isoformat(),
            "credit_days": str(credit_days),
            "reason": reason.strip(),
        },
    )
    notification_hooks.compoff_credited(
        session,
        credit_id=credit.id,
        employee_id=credit.employee_id,
        earned_date=credit.earned_date,
        credit_days=credit.credit_days,
        needs_verification=credit.status != "available",
    )
    return credit


def decide_credit(
    session: Session,
    *,
    credit_id: int,
    approve: bool,
    note: str | None,
    context: audit.AuditContext,
) -> CompoffCredit:
    """Verify or reject an earned credit.

    Until verified, a credit is not spendable - which is why the balance keeps
    ``pending_verification_days`` apart from ``available_days``.
    """
    credit = session.get(CompoffCredit, credit_id)
    if credit is None:
        raise NotFound("Comp-off credit not found.")
    if credit.status != "pending_verification":
        raise Conflict(
            f"That credit is {credit.status.replace('_', ' ')}; only a credit awaiting "
            "verification can be decided."
        )

    credit.status = "available" if approve else "rejected"
    credit.verified_by_user_id = context.actor_user_id
    credit.verified_at = utc_now()
    if note:
        credit.decision_note = note.strip()
    session.flush()

    audit.record(
        session,
        action="compoff.credit_verified" if approve else "compoff.credit_rejected",
        context=context,
        entity_type="compoff_credit",
        entity_id=credit.id,
        after={
            "employee_id": credit.employee_id,
            "earned_date": credit.earned_date.isoformat(),
            "credit_days": str(credit.credit_days),
            "note": note.strip() if note else None,
        },
    )
    notification_hooks.compoff_credit_decided(
        session,
        credit_id=credit.id,
        employee_id=credit.employee_id,
        earned_date=credit.earned_date,
        credit_days=credit.credit_days,
        approved=approve,
        reason=note.strip() if note else None,
    )
    return credit


def reverse_credit(
    session: Session, *, credit_id: int, reason: str, context: audit.AuditContext
) -> CompoffCredit:
    """Cancel a credit granted in error, by posting a reversal.

    The original is marked ``reversed`` rather than deleted, and a reversing row
    cites it. Deleting it would remove the evidence that it was ever granted,
    which is the one thing an employee disputing their balance needs to see.

    A credit already spent cannot be reversed - the time off has been taken.
    """
    credit = session.get(CompoffCredit, credit_id)
    if credit is None:
        raise NotFound("Comp-off credit not found.")
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required to reverse a comp-off credit.",
            details=[{"field": "reason", "message": "Required."}],
        )
    if credit.status == "reversed":
        raise Conflict("That credit has already been reversed.")
    if Decimal(credit.allocated_days) > ZERO:
        raise Conflict(
            f"{Decimal(credit.allocated_days)} day(s) of this credit have already been "
            "taken as time off, so it cannot be reversed. Correct the attendance for "
            "those days instead."
        )

    credit.status = "reversed"
    credit.decision_note = reason.strip()
    session.flush()

    reversal = CompoffCredit(
        employee_id=credit.employee_id,
        # Offset by a day so the unique (employee, earned_date) constraint, which
        # exists to stop double-crediting, does not also block the correction.
        earned_date=credit.earned_date + timedelta(days=1),
        attendance_day_id=None,
        source="manual",
        day_type=credit.day_type,
        worked_minutes=0,
        credit_days=credit.credit_days,
        allocated_days=credit.credit_days,
        expiry_date=credit.expiry_date,
        status="rejected",
        reversal_of_id=credit.id,
        verified_by_user_id=context.actor_user_id,
        verified_at=utc_now(),
        decision_note=f"Reversal of credit #{credit.id}: {reason.strip()}",
    )
    try:
        with session.begin_nested():
            session.add(reversal)
            session.flush()
    except IntegrityError:
        # A credit already exists on the following day; the original's status is
        # what matters, so the marker row is not essential.
        logger.info(
            "Could not record a reversal marker row for credit %s; the credit is "
            "still marked reversed.",
            credit.id,
        )

    audit.record(
        session,
        action="compoff.credit_reversed",
        context=context,
        entity_type="compoff_credit",
        entity_id=credit.id,
        after={
            "employee_id": credit.employee_id,
            "earned_date": credit.earned_date.isoformat(),
            "credit_days": str(credit.credit_days),
            "reason": reason.strip(),
        },
    )
    return credit


def expire_credits(
    session: Session, *, as_of: date | None = None, context: audit.AuditContext
) -> dict[str, Any]:
    """Mark credits past their expiry date as expired.

    Idempotent because it only touches rows still ``available``: re-running it
    finds nothing left to expire. Only the unallocated remainder is lost - a
    credit half spent does not have its spent half taken away.
    """
    as_of = as_of or date.today()

    candidates = session.scalars(
        select(CompoffCredit).where(
            CompoffCredit.status == "available",
            CompoffCredit.expiry_date.isnot(None),
            CompoffCredit.expiry_date < as_of,
        )
    ).all()

    expired_days = ZERO
    affected: set[int] = set()
    for credit in candidates:
        remaining = credit.remaining_days
        if remaining <= ZERO:
            # Fully spent; nothing is lost, so it is simply closed.
            credit.status = "expired"
            continue
        credit.status = "expired"
        expired_days += remaining
        affected.add(credit.employee_id)

        # Keyed on the credit, so re-running the expiry job tells nobody twice.
        if credit.expiry_date is not None:
            notification_hooks.compoff_expired(
                session,
                credit_id=credit.id,
                employee_id=credit.employee_id,
                expires_on=credit.expiry_date,
                days_lost=remaining,
            )

    session.flush()

    result = {
        "as_of": as_of.isoformat(),
        "credits_expired": len(candidates),
        "days_lost": f"{expired_days:.2f}",
        "employees_affected": len(affected),
    }
    audit.record(
        session,
        action="compoff.credits_expired",
        context=context,
        entity_type="job",
        entity_id="compoff_expiry",
        extra=result,
    )
    session.flush()
    return result


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


def _lots(session: Session, employee_id: int) -> list[rules.CreditLot]:
    rows = session.execute(
        select(
            CompoffCredit.id,
            CompoffCredit.earned_date,
            CompoffCredit.credit_days,
            CompoffCredit.allocated_days,
            CompoffCredit.expiry_date,
            CompoffCredit.status,
        ).where(CompoffCredit.employee_id == employee_id)
    ).all()
    return [
        rules.CreditLot(
            credit_id=row[0],
            earned_date=row[1],
            credit_days=row[2],
            allocated_days=row[3],
            expiry_date=row[4],
            status=row[5],
        )
        for row in rows
    ]


def _pending_application_days(
    session: Session, employee_id: int, *, exclude_application_id: int | None = None
) -> Decimal:
    statement = select(func.coalesce(func.sum(CompoffApplication.total_days), 0)).where(
        CompoffApplication.employee_id == employee_id,
        CompoffApplication.status.in_(OPEN_STATUSES),
    )
    if exclude_application_id is not None:
        statement = statement.where(CompoffApplication.id != exclude_application_id)
    return Decimal(str(session.scalar(statement) or 0))


def balance_for(
    session: Session,
    *,
    employee_id: int,
    as_of: date | None = None,
    exclude_application_id: int | None = None,
) -> rules.CompoffBalance:
    return rules.compute_balance(
        _lots(session, employee_id),
        as_of=as_of or date.today(),
        pending_allocation_days=_pending_application_days(
            session, employee_id, exclude_application_id=exclude_application_id
        ),
    )


def credits_for(
    session: Session,
    *,
    employee_id: int,
    statuses: list[str] | None = None,
    limit: int = 200,
) -> list[CompoffCredit]:
    statement = (
        select(CompoffCredit)
        .where(CompoffCredit.employee_id == employee_id)
        .order_by(CompoffCredit.earned_date.desc())
        .limit(limit)
    )
    if statuses:
        statement = statement.where(CompoffCredit.status.in_(statuses))
    return list(session.scalars(statement).all())


def pending_verification(session: Session, *, limit: int = 200) -> list[CompoffCredit]:
    """Credits awaiting HR verification, oldest first."""
    return list(
        session.scalars(
            select(CompoffCredit)
            .where(CompoffCredit.status == "pending_verification")
            .order_by(CompoffCredit.earned_date)
            .limit(limit)
        ).all()
    )


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def employee_for_user(session: Session, user: User) -> Employee:
    if user.employee_id is None:
        raise PermissionDenied(
            "Your account is not linked to an employee record, so comp-off cannot be "
            "applied for. Ask HR to link it."
        )
    employee = session.get(Employee, user.employee_id)
    if employee is None:  # pragma: no cover - FK prevents this
        raise PermissionDenied("Your linked employee record no longer exists.")
    return employee


def _day_types(session: Session, *, employee_id: int, start: date, end: date) -> dict[date, str]:
    """The shared resolver again, so comp-off agrees with attendance and leave."""
    from app.modules.shifts.policy import resolve_day_type

    calendar = shifts_service.resolve_calendar_for(session, employee_id)
    spec = shifts_service.calendar_spec(
        session, calendar, start=start, end=end, employee_id=employee_id
    )
    span = (end - start).days + 1
    return {
        start + timedelta(days=offset): resolve_day_type(start + timedelta(days=offset), spec)
        for offset in range(span)
    }


def _manager_available(session: Session, employee: Employee) -> bool:
    if employee.reporting_manager_id is None:
        return False
    manager = session.get(Employee, employee.reporting_manager_id)
    return manager is not None and manager.employment_status != "exited"


def _occupied_dates(session: Session, *, employee_id: int, start: date, end: date) -> set[date]:
    """Dates already taken by another open or approved comp-off application."""
    return {
        row[0]
        for row in session.execute(
            select(CompoffApplicationDay.off_date)
            .join(
                CompoffApplication,
                CompoffApplication.id == CompoffApplicationDay.application_id,
            )
            .where(
                CompoffApplication.employee_id == employee_id,
                CompoffApplication.status.in_(BLOCKING_STATUSES),
                CompoffApplicationDay.off_date >= start,
                CompoffApplicationDay.off_date <= end,
            )
        ).all()
    }


def apply_for_compoff(
    session: Session,
    *,
    user: User,
    data: dict[str, Any],
    context: audit.AuditContext,
    on_behalf_of_employee_id: int | None = None,
) -> CompoffApplication:
    """Request time off against earned credits.

    Credits are **allocated at submission**, not at approval. That is deliberate:
    a credit about to expire must be held for this request rather than left
    available for another one that might be approved first, and the employee
    needs to know at submission whether their credits actually cover the request.

    A shortfall refuses the request. Unlike leave there is no unpaid fallback -
    comp-off is time off already earned, and there is nothing to draw on if the
    credits are not there.
    """
    _assert_enabled(session)

    employee = (
        session.get(Employee, on_behalf_of_employee_id)
        if on_behalf_of_employee_id is not None
        else employee_for_user(session, user)
    )
    if employee is None:
        raise ValidationFailed(
            "The employee does not exist.",
            details=[{"field": "employee_id", "message": "Unknown."}],
        )

    start: date = data["start_date"]
    end: date = data["end_date"]
    if end < start:
        raise ValidationFailed(
            "The end date cannot be before the start date.",
            details=[{"field": "end_date", "message": "Must not precede the start."}],
        )
    if (end - start).days > 60:
        raise ValidationFailed(
            "A comp-off request cannot span more than 60 days.",
            details=[{"field": "end_date", "message": "Range too long."}],
        )

    half_day_enabled = bool(
        settings_service.get_value(session, "compoff.half_day_usage_enabled", default=True)
    )
    start_portion = data.get("start_portion", "full")
    end_portion = data.get("end_portion", "full")
    if start == end:
        end_portion = start_portion

    try:
        off_days, total = rules.expand_off_days(
            start=start,
            end=end,
            day_types=_day_types(session, employee_id=employee.id, start=start, end=end),
            start_portion=start_portion,
            end_portion=end_portion,
            half_day_enabled=half_day_enabled,
        )
    except rules.CompoffRuleViolation as error:
        raise ValidationFailed(
            str(error), details=[{"field": error.field_name, "message": str(error)}]
        ) from error

    if total <= ZERO:
        raise ValidationFailed(
            "None of those dates are working days, so there is nothing to take off.",
            details=[{"field": "start_date", "message": "No working days in the range."}],
        )

    clashes = _occupied_dates(session, employee_id=employee.id, start=start, end=end) & {
        day.off_date for day in off_days
    }
    if clashes:
        raise Conflict(
            "You already have a comp-off request covering "
            + ", ".join(sorted(day.isoformat() for day in clashes))
            + ".",
            details=[{"field": "start_date", "message": "Dates already requested."}],
        )

    if not employee.is_employed_on(start):
        raise PolicyViolation(
            f"{employee.employee_code} was not on the rolls on {start.isoformat()}."
        )

    # Allocate from the lots the employee actually holds, earliest expiry first.
    # The lots already exclude anything held by another open request, because
    # allocation happens at submission - so the plan sees only what is genuinely
    # spendable.
    lots = _lots(session, employee.id)
    plan = rules.plan_allocation(lots=lots, required_days=total, on_date=start)

    if not plan.is_complete:
        balance = balance_for(session, employee_id=employee.id, as_of=start)
        raise PolicyViolation(
            f"You need {total} day(s) of comp-off but have "
            f"{balance.available_days} available"
            + (
                f" ({balance.pending_verification_days} still awaiting verification)"
                if balance.pending_verification_days > ZERO
                else ""
            )
            + ". Comp-off cannot be taken before it has been earned and verified.",
            details=[{"field": "end_date", "message": "Not enough comp-off credit."}],
        )

    manager_missing = not _manager_available(session, employee)
    chain = settings_service.get_value(
        session, "leave.approval_chain", default=["reporting_manager", "hr_admin"]
    )
    behaviour = settings_service.get_value(
        session, "leave.manager_missing_behaviour", default="escalate_to_hr"
    )
    if manager_missing and behaviour == "block":
        raise PolicyViolation(
            f"{employee.employee_code} has no active reporting manager, so this "
            "request cannot be routed. Ask HR to assign one."
        )

    try:
        steps = leave_rules.build_chain(
            list(chain) if isinstance(chain, list) else ["hr_admin"],
            manager_available=not manager_missing,
        )
    except leave_rules.LeaveRuleViolation as error:
        raise PolicyViolation(str(error)) from error

    first = steps[0]
    application = CompoffApplication(
        employee_id=employee.id,
        start_date=start,
        end_date=end,
        total_days=total,
        reason=(data.get("reason") or "").strip() or None,
        status=first.status,
        current_stage=first.stage,
        escalated_from_missing_manager=manager_missing,
        applied_by_user_id=context.actor_user_id,
    )
    session.add(application)
    session.flush()

    for day in off_days:
        session.add(
            CompoffApplicationDay(
                application_id=application.id,
                off_date=day.off_date,
                portion=day.portion,
                day_fraction=day.day_fraction,
                day_type=day.day_type,
            )
        )

    # Hold the credits now. The allocated_days column and the allocation rows are
    # updated together, with a check constraint making an over-draw impossible.
    for allocation in plan.allocations:
        session.add(
            CompoffAllocation(
                application_id=application.id,
                credit_id=allocation.credit_id,
                days=allocation.days,
            )
        )
        credit = session.get(CompoffCredit, allocation.credit_id)
        if credit is not None:
            credit.allocated_days = Decimal(credit.allocated_days) + allocation.days

    if manager_missing and "reporting_manager" in (chain if isinstance(chain, list) else []):
        session.add(
            CompoffApproval(
                application_id=application.id,
                sequence=1,
                stage="reporting_manager",
                decision="skipped",
                decided_at=utc_now(),
                comment=(
                    "No active reporting manager, so this stage was skipped and the "
                    "request routed straight to HR."
                ),
                was_auto_skipped=True,
            )
        )

    session.flush()

    audit.record(
        session,
        action="compoff.applied",
        context=context,
        entity_type="compoff_application",
        entity_id=application.id,
        after={
            "employee_code": employee.employee_code,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "total_days": str(total),
            "credits_used": [
                {"credit_id": item.credit_id, "days": str(item.days)} for item in plan.allocations
            ],
            "status": application.status,
            "escalated": manager_missing,
        },
    )
    if application.current_stage is not None:
        notification_hooks.compoff_awaiting_approval(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=application.current_stage,
        )
    return application


def get_application(session: Session, application_id: int) -> CompoffApplication:
    application = session.scalar(
        select(CompoffApplication)
        .where(CompoffApplication.id == application_id)
        .options(
            selectinload(CompoffApplication.days),
            selectinload(CompoffApplication.approvals),
            selectinload(CompoffApplication.allocations),
        )
    )
    if application is None:
        raise NotFound("Comp-off application not found.")
    return application


def _refresh(session: Session, application: CompoffApplication) -> CompoffApplication:
    session.refresh(application, attribute_names=["approvals", "days", "allocations"])
    return application


def _assert_is_manager_of(session: Session, *, user: User, employee_id: int) -> None:
    if user.employee_id is None:
        raise PermissionDenied(
            "Your account is not linked to an employee record, so you cannot review "
            "comp-off requests."
        )
    subject = session.get(Employee, employee_id)
    if subject is None or subject.reporting_manager_id != user.employee_id:
        # 404 rather than 403, so the reporting structure cannot be mapped by
        # probing ids.
        raise NotFound("Comp-off application not found.")


def decide(
    session: Session,
    *,
    application_id: int,
    user: User,
    approve: bool,
    comment: str | None,
    context: audit.AuditContext,
) -> CompoffApplication:
    """Record a stage decision and advance or close the request.

    The same three rules as leave, enforced here rather than assumed from the
    UI: no self-approval, a manager stage needs the relationship, and stages run
    in order.

    Rejection **releases the held credits**, so they go back to the employee's
    balance rather than being stranded against a request that will never happen.
    """
    application = get_application(session, application_id)

    if not application.is_open:
        raise Conflict(f"This request is already {application.status}; it cannot be decided again.")

    stage = application.current_stage
    if stage is None:  # pragma: no cover - an open request always has a stage
        raise Conflict("This request has no pending stage.")

    if user.employee_id is not None and user.employee_id == application.employee_id:
        raise PermissionDenied(
            "You cannot decide your own comp-off request. Ask your manager or HR."
        )

    if stage == "reporting_manager":
        if user.has_permission("compoff.approve_hr"):
            pass
        elif user.has_permission("compoff.approve_as_manager"):
            _assert_is_manager_of(session, user=user, employee_id=application.employee_id)
        else:
            raise PermissionDenied("You cannot review this request.")
    elif stage == "hr_admin" and not user.has_permission("compoff.approve_hr"):
        raise PermissionDenied("Final approval requires the HR approval permission.")

    sequence = (
        session.scalar(
            select(func.coalesce(func.max(CompoffApproval.sequence), 0)).where(
                CompoffApproval.application_id == application.id
            )
        )
        or 0
    ) + 1

    session.add(
        CompoffApproval(
            application_id=application.id,
            sequence=sequence,
            stage=stage,
            decision="approved" if approve else "rejected",
            decided_by_user_id=context.actor_user_id,
            decided_by_employee_id=user.employee_id,
            decided_at=utc_now(),
            comment=(comment or "").strip() or None,
        )
    )

    if not approve:
        _release_allocations(session, application)
        application.status = "rejected"
        application.current_stage = None
        application.decided_at = utc_now()
        session.flush()

        audit.record(
            session,
            action="compoff.rejected",
            context=context,
            entity_type="compoff_application",
            entity_id=application.id,
            after={"stage": stage, "credits_released": True},
        )
        notification_hooks.compoff_decided(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=stage,
            approved=False,
            is_final=True,
            comment=(comment or "").strip() or None,
        )
        return _refresh(session, application)

    chain = settings_service.get_value(
        session, "leave.approval_chain", default=["reporting_manager", "hr_admin"]
    )
    steps = leave_rules.build_chain(
        list(chain) if isinstance(chain, list) else ["hr_admin"],
        manager_available=not application.escalated_from_missing_manager,
    )
    try:
        following = leave_rules.next_stage(steps, stage)
    except leave_rules.LeaveRuleViolation:
        logger.warning(
            "Comp-off application %s is at stage %r which is no longer in the chain; "
            "treating this approval as final.",
            application.id,
            stage,
        )
        following = None

    if following is not None:
        application.status = following.status
        application.current_stage = following.stage
        session.flush()
        audit.record(
            session,
            action="compoff.stage_approved",
            context=context,
            entity_type="compoff_application",
            entity_id=application.id,
            after={"stage_decided": stage, "now_awaiting": following.stage},
        )
        notification_hooks.compoff_decided(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=stage,
            approved=True,
            is_final=False,
        )
        notification_hooks.compoff_awaiting_approval(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=following.stage,
        )
        return _refresh(session, application)

    application.status = "approved"
    application.current_stage = None
    application.decided_at = utc_now()
    session.flush()

    recomputed = _recompute_attendance(session, application=application)

    audit.record(
        session,
        action="compoff.approved",
        context=context,
        entity_type="compoff_application",
        entity_id=application.id,
        after={
            "total_days": str(application.total_days),
            "attendance_days_recomputed": recomputed,
        },
    )
    notification_hooks.compoff_decided(
        session,
        application_id=application.id,
        employee_id=application.employee_id,
        start_date=application.start_date,
        end_date=application.end_date,
        total_days=application.total_days,
        stage="final",
        approved=True,
        is_final=True,
    )
    return _refresh(session, application)


def _release_allocations(session: Session, application: CompoffApplication) -> Decimal:
    """Return held credits to the balance.

    The allocation rows are removed and each credit's ``allocated_days`` reduced
    by the same amount, so the two never disagree. Without this a rejected or
    withdrawn request would strand the credits, and the employee would lose days
    they had earned.
    """
    released = ZERO
    for allocation in list(application.allocations):
        credit = session.get(CompoffCredit, allocation.credit_id)
        if credit is not None:
            credit.allocated_days = max(
                Decimal(credit.allocated_days) - Decimal(allocation.days), ZERO
            )
        released += Decimal(allocation.days)
        session.delete(allocation)
    session.flush()
    return released


def _recompute_attendance(session: Session, *, application: CompoffApplication) -> int:
    from app.modules.attendance import service as attendance_service

    count = 0
    for day in application.days:
        try:
            attendance_service.recompute_day(
                session, employee_id=application.employee_id, work_date=day.off_date
            )
            count += 1
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Could not recompute attendance for employee %s on %s after a comp-off decision",
                application.employee_id,
                day.off_date,
            )
    return count


def withdraw(
    session: Session,
    *,
    application_id: int,
    user: User,
    reason: str | None,
    context: audit.AuditContext,
) -> CompoffApplication:
    """Pull an undecided request, releasing the credits it held."""
    application = get_application(session, application_id)

    if user.employee_id != application.employee_id and not user.has_permission("compoff.read_all"):
        raise NotFound("Comp-off application not found.")
    if not application.is_open:
        raise Conflict(f"This request is {application.status} and can no longer be withdrawn.")

    released = _release_allocations(session, application)
    application.status = "withdrawn"
    application.current_stage = None
    application.decided_at = utc_now()
    application.cancellation_reason = (reason or "").strip() or None
    session.flush()

    audit.record(
        session,
        action="compoff.withdrawn",
        context=context,
        entity_type="compoff_application",
        entity_id=application.id,
        after={"reason": application.cancellation_reason, "days_released": str(released)},
    )
    return _refresh(session, application)


def cancel(
    session: Session,
    *,
    application_id: int,
    user: User,
    reason: str,
    context: audit.AuditContext,
) -> CompoffApplication:
    """Call off approved comp-off, returning the credits.

    The credits go back with their **original expiry dates** - cancelling does
    not extend them. An employee who cancels a day a week before the credit
    lapses still has only that week to use it, which is the honest outcome.
    """
    application = get_application(session, application_id)

    is_owner = user.employee_id == application.employee_id
    if not is_owner and not user.has_permission("compoff.approve_hr"):
        raise NotFound("Comp-off application not found.")
    if application.status != "approved":
        raise Conflict(
            f"Only approved comp-off can be cancelled; this request is {application.status}."
        )
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required to cancel approved comp-off.",
            details=[{"field": "reason", "message": "Required."}],
        )

    released = _release_allocations(session, application)
    application.status = "cancelled"
    application.current_stage = None
    application.cancelled_at = utc_now()
    application.cancellation_reason = reason.strip()
    session.flush()

    recomputed = _recompute_attendance(session, application=application)

    audit.record(
        session,
        action="compoff.cancelled",
        context=context,
        entity_type="compoff_application",
        entity_id=application.id,
        after={
            "reason": reason.strip(),
            "days_released": str(released),
            "attendance_days_recomputed": recomputed,
            "cancelled_by_owner": is_owner,
        },
        extra={
            "note": (
                "Returned credits keep their original expiry dates; cancelling does "
                "not extend them."
            )
        },
    )
    return _refresh(session, application)


def list_applications(
    session: Session,
    *,
    employee_ids: list[int] | None = None,
    statuses: list[str] | None = None,
    limit: int = 200,
) -> list[CompoffApplication]:
    statement = (
        select(CompoffApplication)
        .options(
            selectinload(CompoffApplication.days),
            selectinload(CompoffApplication.approvals),
        )
        .order_by(CompoffApplication.start_date.desc(), CompoffApplication.id.desc())
        .limit(limit)
    )
    if employee_ids is not None:
        if not employee_ids:
            return []
        statement = statement.where(CompoffApplication.employee_id.in_(employee_ids))
    if statuses:
        statement = statement.where(CompoffApplication.status.in_(statuses))
    return list(session.scalars(statement).all())


def pending_for_approver(session: Session, *, user: User) -> list[CompoffApplication]:
    found: list[CompoffApplication] = []
    if user.has_permission("compoff.approve_hr"):
        found += list_applications(session, statuses=["pending_hr"], limit=500)
    if user.has_permission("compoff.approve_as_manager") and user.employee_id is not None:
        reports = list(
            session.scalars(
                select(Employee.id).where(Employee.reporting_manager_id == user.employee_id)
            ).all()
        )
        if reports:
            found += list_applications(
                session, employee_ids=reports, statuses=["pending_manager"], limit=500
            )

    seen: set[int] = set()
    unique: list[CompoffApplication] = []
    for application in found:
        if application.id not in seen:
            seen.add(application.id)
            unique.append(application)
    unique.sort(key=lambda item: (item.start_date, item.id))
    return unique


def approved_compoff_on(
    session: Session, *, employee_id: int, work_date: date
) -> CompoffApplicationDay | None:
    """The approved comp-off covering one date, for the attendance engine."""
    return session.scalar(
        select(CompoffApplicationDay)
        .join(
            CompoffApplication,
            CompoffApplication.id == CompoffApplicationDay.application_id,
        )
        .where(
            CompoffApplication.employee_id == employee_id,
            CompoffApplication.status == "approved",
            CompoffApplicationDay.off_date == work_date,
        )
        .order_by(CompoffApplicationDay.id)
    )


def summarise(session: Session, applications: list[CompoffApplication]) -> list[dict[str, Any]]:
    if not applications:
        return []
    employee_ids = {item.employee_id for item in applications}
    employees = {
        row[0]: (row[1], f"{row[2]} {row[3]}".strip())
        for row in session.execute(
            select(
                Employee.id, Employee.employee_code, Employee.first_name, Employee.last_name
            ).where(Employee.id.in_(employee_ids))
        ).all()
    }
    result = []
    for item in applications:
        code, name = employees.get(item.employee_id, (None, None))
        result.append(
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "employee_code": code,
                "employee_name": name,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "total_days": item.total_days,
                "status": item.status,
                "current_stage": item.current_stage,
                "reason": item.reason,
                "escalated_from_missing_manager": item.escalated_from_missing_manager,
                "created_at": item.created_at,
            }
        )
    return result
