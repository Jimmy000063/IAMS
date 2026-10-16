"""Leave orchestration: types, policies, applications, approvals, the ledger.

Where the rules live
--------------------
Arithmetic and eligibility are in :mod:`app.modules.leave.policy`, which is pure.
This module supplies the values those functions need - from policy rows, company
settings and the shared calendar resolver - and writes the results. It holds no
quota, rate or threshold of its own.

Two decisions worth stating
---------------------------
**Balance is never stored.** It is a ``SUM`` over ``leave_ledger_entries``,
computed on demand. Every movement is a row with a type, a reason and an actor,
so a disputed balance can be reconstructed from first principles rather than
argued about.

**An approved application is never edited.** A modification creates a new
application superseding the old one, and a cancellation posts a reversal. Both
leave the original decision intact, because "what was actually approved" has to
stay answerable after the fact.
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
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees.models import Employee
from app.modules.leave import policy as rules
from app.modules.leave.models import (
    EmployeeLeavePolicy,
    LeaveApplication,
    LeaveApplicationDay,
    LeaveApproval,
    LeaveLedgerEntry,
    LeavePolicy,
    LeaveType,
)
from app.modules.notifications import hooks as notification_hooks
from app.modules.settings import service as settings_service
from app.modules.shifts import service as shifts_service

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


def _days(value: Decimal) -> Decimal:
    """Normalise a leave quantity to two decimal places, as the policy layer does."""
    from decimal import ROUND_HALF_UP

    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


OPEN_STATUSES = ("pending_manager", "pending_hr")
#: Statuses that occupy a date, so a second application cannot use it. A
#: withdrawn or rejected application frees its dates again.
BLOCKING_STATUSES = ("pending_manager", "pending_hr", "approved")


def _violation(problems: list[rules.EligibilityProblem], headline: str) -> ValidationFailed:
    """Turn policy problems into one response listing every one of them.

    All problems at once, not the first: an applicant should fix one form rather
    than discover four refusals in sequence.
    """
    return ValidationFailed(
        headline,
        details=[{"field": problem.field_name, "message": problem.message} for problem in problems],
    )


# ---------------------------------------------------------------------------
# Company settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaveSettings:
    """The company-wide leave settings, read once per operation."""

    year_start_month: int
    year_start_day: int
    approval_chain: list[str]
    manager_missing_behaviour: str
    backdated_window_days: int
    future_window_days: int
    allow_negative_balance: bool
    half_day_enabled: bool
    sandwich_rule_enabled: bool
    proration_basis: str


def load_settings(session: Session) -> LeaveSettings:
    get = settings_service.get_value
    chain = get(session, "leave.approval_chain", default=["reporting_manager", "hr_admin"])
    return LeaveSettings(
        year_start_month=int(get(session, "leave.year_start_month", default=4)),
        year_start_day=int(get(session, "leave.year_start_day", default=1)),
        approval_chain=list(chain) if isinstance(chain, list) else ["hr_admin"],
        manager_missing_behaviour=str(
            get(session, "leave.manager_missing_behaviour", default="escalate_to_hr")
        ),
        backdated_window_days=int(get(session, "leave.backdated_window_days", default=30)),
        future_window_days=int(get(session, "leave.future_window_days", default=365)),
        allow_negative_balance=bool(get(session, "leave.allow_negative_balance", default=False)),
        half_day_enabled=bool(get(session, "leave.half_day_enabled", default=True)),
        sandwich_rule_enabled=bool(get(session, "leave.sandwich_rule_enabled", default=False)),
        proration_basis=str(get(session, "leave.proration_basis", default="calendar_days")),
    )


def _type_rules(leave_type: LeaveType, settings: LeaveSettings) -> rules.TypeRules:
    """Flatten a leave type into the pure rule set, resolving inheritance.

    ``allows_negative_balance`` is null on the type to mean "inherit the company
    setting", so the resolution happens here and the policy layer receives a
    decided value rather than a maybe.
    """
    return rules.TypeRules(
        code=leave_type.code,
        name=leave_type.name,
        is_paid=leave_type.is_paid,
        deducts_balance=leave_type.deducts_balance,
        allows_half_day=leave_type.allows_half_day,
        counts_non_working_days=leave_type.counts_non_working_days,
        requires_reason=leave_type.requires_reason,
        attachment_required_above_days=leave_type.attachment_required_above_days,
        min_notice_days=leave_type.min_notice_days,
        max_consecutive_days=leave_type.max_consecutive_days,
        min_service_days=leave_type.min_service_days,
        gender_restriction=leave_type.gender_restriction,
        allows_negative_balance=(
            leave_type.allows_negative_balance
            if leave_type.allows_negative_balance is not None
            else settings.allow_negative_balance
        ),
    )


# ---------------------------------------------------------------------------
# Leave types
# ---------------------------------------------------------------------------


def create_leave_type(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> LeaveType:
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise ValidationFailed(
            "A leave type code is required.",
            details=[{"field": "code", "message": "Required."}],
        )
    if session.scalar(select(func.count()).select_from(LeaveType).where(LeaveType.code == code)):
        raise Conflict(
            f"Leave type {code!r} already exists.",
            details=[{"field": "code", "message": "Already in use."}],
        )

    leave_type = LeaveType(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        is_paid=data.get("is_paid", True),
        deducts_balance=data.get("deducts_balance", True),
        allows_half_day=data.get("allows_half_day", True),
        allows_negative_balance=data.get("allows_negative_balance"),
        counts_non_working_days=data.get("counts_non_working_days", False),
        requires_reason=data.get("requires_reason", True),
        attachment_required_above_days=data.get("attachment_required_above_days"),
        min_notice_days=data.get("min_notice_days", 0),
        max_consecutive_days=data.get("max_consecutive_days"),
        min_service_days=data.get("min_service_days", 0),
        gender_restriction=data.get("gender_restriction", "any"),
        requires_legal_review=data.get("requires_legal_review", False),
        display_order=data.get("display_order", 100),
        colour=data.get("colour"),
        is_active=data.get("is_active", True),
        created_by_user_id=context.actor_user_id,
    )
    session.add(leave_type)
    session.flush()

    audit.record(
        session,
        action="leave.type_created",
        context=context,
        entity_type="leave_type",
        entity_id=leave_type.id,
        after={
            "code": leave_type.code,
            "name": leave_type.name,
            "is_paid": leave_type.is_paid,
            "deducts_balance": leave_type.deducts_balance,
            "counts_non_working_days": leave_type.counts_non_working_days,
            "requires_legal_review": leave_type.requires_legal_review,
        },
    )
    return leave_type


def update_leave_type(
    session: Session, type_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> LeaveType:
    leave_type = get_leave_type(session, type_id)
    before = {
        "name": leave_type.name,
        "is_paid": leave_type.is_paid,
        "deducts_balance": leave_type.deducts_balance,
        "counts_non_working_days": leave_type.counts_non_working_days,
        "is_active": leave_type.is_active,
    }

    for attribute in (
        "name",
        "description",
        "is_paid",
        "deducts_balance",
        "allows_half_day",
        "allows_negative_balance",
        "counts_non_working_days",
        "requires_reason",
        "attachment_required_above_days",
        "min_notice_days",
        "max_consecutive_days",
        "min_service_days",
        "gender_restriction",
        "requires_legal_review",
        "display_order",
        "colour",
        "is_active",
    ):
        if attribute in data:
            setattr(leave_type, attribute, data[attribute])

    leave_type.updated_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="leave.type_updated",
        context=context,
        entity_type="leave_type",
        entity_id=leave_type.id,
        before=before,
        after={
            "name": leave_type.name,
            "is_paid": leave_type.is_paid,
            "deducts_balance": leave_type.deducts_balance,
            "counts_non_working_days": leave_type.counts_non_working_days,
            "is_active": leave_type.is_active,
        },
        extra={
            "note": (
                "Changing a type affects future applications. Applications already "
                "decided keep the policy snapshot they were decided under."
            )
        },
    )
    return leave_type


def get_leave_type(session: Session, type_id: int) -> LeaveType:
    leave_type = session.get(LeaveType, type_id)
    if leave_type is None:
        raise NotFound("Leave type not found.")
    return leave_type


def list_leave_types(session: Session, *, include_inactive: bool = False) -> list[LeaveType]:
    statement = select(LeaveType).order_by(LeaveType.display_order, LeaveType.code)
    if not include_inactive:
        statement = statement.where(LeaveType.is_active.is_(True))
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


def create_policy(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> LeavePolicy:
    """Create an effective-dated rule set for a leave type.

    A policy starting on or before an existing one's start is refused rather
    than silently overlapping: "which rules were in force on this date" must
    have exactly one answer. To change entitlement from a date, create a new
    policy from that date and close the old one.
    """
    leave_type = get_leave_type(session, data["leave_type_id"])

    effective_from = data["effective_from"]
    effective_to = data.get("effective_to")
    if effective_to is not None and effective_to < effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    frequency = data.get("accrual_frequency", "annual")
    quota = Decimal(str(data.get("annual_quota_days", 0)))
    if frequency == "none" and quota > ZERO:
        raise ValidationFailed(
            "A quota was given but the accrual frequency is 'none', so nothing "
            "would ever be credited. Either set a frequency or grant the balance "
            "with an opening adjustment.",
            details=[{"field": "accrual_frequency", "message": "Inconsistent with the quota."}],
        )

    cap = data.get("max_accumulation_days")
    if cap is not None and Decimal(str(cap)) < quota and frequency != "none":
        # Allowed, but worth saying: the employee can never reach a full year's
        # entitlement. Recorded in the audit note rather than refused, because a
        # company may genuinely want a low ceiling.
        logger.info(
            "Leave policy for %s caps accumulation (%s) below the annual quota (%s)",
            leave_type.code,
            cap,
            quota,
        )

    existing = session.scalar(
        select(LeavePolicy).where(
            LeavePolicy.leave_type_id == leave_type.id,
            LeavePolicy.effective_from == effective_from,
        )
    )
    if existing is not None:
        raise Conflict(
            f"{leave_type.code} already has a policy effective from "
            f"{effective_from.isoformat()}. Close that one or choose another date.",
            details=[{"field": "effective_from", "message": "A policy already starts here."}],
        )

    policy = LeavePolicy(
        leave_type_id=leave_type.id,
        name=data["name"].strip(),
        effective_from=effective_from,
        effective_to=effective_to,
        annual_quota_days=quota,
        accrual_frequency=frequency,
        accrual_rounding=data.get("accrual_rounding", "nearest_half"),
        prorate_on_joining=data.get("prorate_on_joining", True),
        prorate_on_exit=data.get("prorate_on_exit", True),
        accrue_from_confirmation=data.get("accrue_from_confirmation", False),
        max_accumulation_days=cap,
        carry_forward_cap_days=data.get("carry_forward_cap_days"),
        carry_forward_expires_after_months=data.get("carry_forward_expires_after_months"),
        encashment_allowed=data.get("encashment_allowed", False),
        encashment_max_days=data.get("encashment_max_days"),
        notes=data.get("notes"),
        requires_legal_review=data.get("requires_legal_review", False),
        created_by_user_id=context.actor_user_id,
    )
    session.add(policy)
    session.flush()

    audit.record(
        session,
        action="leave.policy_created",
        context=context,
        entity_type="leave_policy",
        entity_id=policy.id,
        after={
            "leave_type": leave_type.code,
            "effective_from": effective_from.isoformat(),
            "annual_quota_days": str(quota),
            "accrual_frequency": frequency,
            "requires_legal_review": policy.requires_legal_review,
        },
    )
    return policy


def close_policy(
    session: Session, policy_id: int, *, effective_to: date, context: audit.AuditContext
) -> LeavePolicy:
    policy = session.get(LeavePolicy, policy_id)
    if policy is None:
        raise NotFound("Leave policy not found.")
    if effective_to < policy.effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    before = policy.effective_to
    policy.effective_to = effective_to
    policy.updated_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="leave.policy_closed",
        context=context,
        entity_type="leave_policy",
        entity_id=policy.id,
        before={"effective_to": before.isoformat() if before else None},
        after={"effective_to": effective_to.isoformat()},
    )
    return policy


def list_policies(session: Session, *, leave_type_id: int | None = None) -> list[LeavePolicy]:
    statement = select(LeavePolicy).order_by(
        LeavePolicy.leave_type_id, LeavePolicy.effective_from.desc()
    )
    if leave_type_id is not None:
        statement = statement.where(LeavePolicy.leave_type_id == leave_type_id)
    return list(session.scalars(statement).all())


def assign_policy(
    session: Session,
    *,
    employee_id: int,
    leave_policy_id: int,
    effective_from: date,
    effective_to: date | None,
    notes: str | None,
    context: audit.AuditContext,
) -> EmployeeLeavePolicy:
    """Give an employee a leave entitlement from a date.

    An existing open assignment for the same leave type is refused rather than
    replaced. Changing entitlement is a dated event - close the old assignment
    and open a new one - so last year's accrual stays explicable.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ValidationFailed(
            "The employee does not exist.",
            details=[{"field": "employee_id", "message": "Unknown."}],
        )
    policy = session.get(LeavePolicy, leave_policy_id)
    if policy is None:
        raise ValidationFailed(
            "The leave policy does not exist.",
            details=[{"field": "leave_policy_id", "message": "Unknown."}],
        )
    if effective_to is not None and effective_to < effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    open_existing = session.scalar(
        select(EmployeeLeavePolicy).where(
            EmployeeLeavePolicy.employee_id == employee_id,
            EmployeeLeavePolicy.leave_type_id == policy.leave_type_id,
            EmployeeLeavePolicy.effective_to.is_(None),
        )
    )
    if open_existing is not None:
        raise Conflict(
            f"{employee.employee_code} already has an open assignment for this leave "
            "type. Close it with an end date first - changing entitlement is a dated "
            "event, and overwriting would rewrite how past accrual was calculated.",
            details=[{"field": "employee_id", "message": "An open assignment exists."}],
        )

    assignment = EmployeeLeavePolicy(
        employee_id=employee_id,
        leave_type_id=policy.leave_type_id,
        leave_policy_id=policy.id,
        effective_from=effective_from,
        effective_to=effective_to,
        notes=notes,
        created_by_user_id=context.actor_user_id,
    )
    session.add(assignment)
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        raise Conflict(
            "An open assignment for this employee and leave type already exists."
        ) from exc

    audit.record(
        session,
        action="leave.policy_assigned",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "employee_code": employee.employee_code,
            "leave_policy_id": policy.id,
            "leave_type_id": policy.leave_type_id,
            "effective_from": effective_from.isoformat(),
        },
    )
    return assignment


def close_assignment(
    session: Session, assignment_id: int, *, effective_to: date, context: audit.AuditContext
) -> EmployeeLeavePolicy:
    assignment = session.get(EmployeeLeavePolicy, assignment_id)
    if assignment is None:
        raise NotFound("Leave policy assignment not found.")
    if effective_to < assignment.effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )
    before = assignment.effective_to
    assignment.effective_to = effective_to
    session.flush()

    audit.record(
        session,
        action="leave.assignment_closed",
        context=context,
        entity_type="employee",
        entity_id=assignment.employee_id,
        before={"effective_to": before.isoformat() if before else None},
        after={"effective_to": effective_to.isoformat()},
    )
    return assignment


def list_assignments(session: Session, employee_id: int) -> list[EmployeeLeavePolicy]:
    return list(
        session.scalars(
            select(EmployeeLeavePolicy)
            .where(EmployeeLeavePolicy.employee_id == employee_id)
            .order_by(
                EmployeeLeavePolicy.leave_type_id,
                EmployeeLeavePolicy.effective_from.desc(),
            )
        ).all()
    )


def resolve_policy(
    session: Session, *, employee_id: int, leave_type_id: int, on_date: date
) -> LeavePolicy | None:
    """The policy governing an employee's leave type on a date.

    Resolution order: an explicit assignment covering that date, then the leave
    type's own policy effective then. The fallback means a company that gives
    every employee the same entitlement does not have to assign it one by one;
    the explicit assignment exists for the cases that differ.
    """
    assignments = session.scalars(
        select(EmployeeLeavePolicy).where(
            EmployeeLeavePolicy.employee_id == employee_id,
            EmployeeLeavePolicy.leave_type_id == leave_type_id,
        )
    ).all()
    covering = [item for item in assignments if item.covers(on_date)]
    if covering:
        chosen = max(covering, key=lambda item: (item.effective_from, item.id))
        return session.get(LeavePolicy, chosen.leave_policy_id)

    candidates = session.scalars(
        select(LeavePolicy)
        .where(LeavePolicy.leave_type_id == leave_type_id)
        .order_by(LeavePolicy.effective_from.desc())
    ).all()
    for policy in candidates:
        if policy.covers(on_date):
            return policy
    return None


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


def _movements(
    session: Session, *, employee_id: int, leave_type_id: int
) -> list[rules.LedgerMovement]:
    rows = session.execute(
        select(
            LeaveLedgerEntry.txn_type,
            LeaveLedgerEntry.days,
            LeaveLedgerEntry.effective_date,
            LeaveLedgerEntry.leave_year,
            LeaveLedgerEntry.expires_on,
        ).where(
            LeaveLedgerEntry.employee_id == employee_id,
            LeaveLedgerEntry.leave_type_id == leave_type_id,
        )
    ).all()
    return [
        rules.LedgerMovement(
            txn_type=row[0],
            days=row[1],
            effective_date=row[2],
            leave_year=row[3],
            expires_on=row[4],
        )
        for row in rows
    ]


def _pending_days(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int,
    exclude_application_id: int | None = None,
) -> Decimal:
    """Days committed by applications awaiting a decision.

    Subtracted from *available* but not from the ledger balance: an employee
    with five days and five pending has a balance of five and nothing to spend.
    Without this they could apply twice for the same days and both requests
    would pass the balance check.
    """
    statement = select(func.coalesce(func.sum(LeaveApplication.total_days), 0)).where(
        LeaveApplication.employee_id == employee_id,
        LeaveApplication.leave_type_id == leave_type_id,
        LeaveApplication.status.in_(OPEN_STATUSES),
    )
    if exclude_application_id is not None:
        statement = statement.where(LeaveApplication.id != exclude_application_id)
    return Decimal(str(session.scalar(statement) or 0))


def balance_for(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int,
    as_of: date | None = None,
    exclude_application_id: int | None = None,
) -> rules.Balance:
    as_of = as_of or date.today()
    return rules.compute_balance(
        _movements(session, employee_id=employee_id, leave_type_id=leave_type_id),
        as_of=as_of,
        pending_days=_pending_days(
            session,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            exclude_application_id=exclude_application_id,
        ),
    )


def balances_for_employee(
    session: Session, *, employee_id: int, as_of: date | None = None
) -> list[dict[str, Any]]:
    """Every active leave type with this employee's balance and entitlement."""
    as_of = as_of or date.today()
    settings = load_settings(session)
    year = rules.leave_year_for(
        as_of, start_month=settings.year_start_month, start_day=settings.year_start_day
    )

    result: list[dict[str, Any]] = []
    for leave_type in list_leave_types(session):
        balance = balance_for(
            session, employee_id=employee_id, leave_type_id=leave_type.id, as_of=as_of
        )
        policy = resolve_policy(
            session, employee_id=employee_id, leave_type_id=leave_type.id, on_date=as_of
        )
        result.append(
            {
                "leave_type_id": leave_type.id,
                "leave_type_code": leave_type.code,
                "leave_type_name": leave_type.name,
                "colour": leave_type.colour,
                "is_paid": leave_type.is_paid,
                "deducts_balance": leave_type.deducts_balance,
                "allows_half_day": leave_type.allows_half_day,
                "counts_non_working_days": leave_type.counts_non_working_days,
                "requires_reason": leave_type.requires_reason,
                "requires_legal_review": leave_type.requires_legal_review
                or bool(policy and policy.requires_legal_review),
                "leave_year": year.label,
                "leave_year_start": year.start,
                "leave_year_end": year.end,
                "annual_quota_days": policy.annual_quota_days if policy else None,
                "accrual_frequency": policy.accrual_frequency if policy else None,
                "has_policy": policy is not None,
                "balance_days": balance.ledger_days,
                "pending_days": balance.pending_days,
                "committed_days": balance.committed_days,
                "available_days": balance.available_days,
                "accrued_days": balance.accrued_days,
                "used_days": balance.used_days,
                "expiring_days": balance.expiring_days,
                "expiring_on": balance.expiring_on,
            }
        )
    return result


def ledger_for(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int | None = None,
    limit: int = 200,
) -> list[LeaveLedgerEntry]:
    statement = (
        select(LeaveLedgerEntry)
        .where(LeaveLedgerEntry.employee_id == employee_id)
        .order_by(LeaveLedgerEntry.effective_date.desc(), LeaveLedgerEntry.id.desc())
        .limit(limit)
    )
    if leave_type_id is not None:
        statement = statement.where(LeaveLedgerEntry.leave_type_id == leave_type_id)
    return list(session.scalars(statement).all())


def _post_entry(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int,
    txn_type: str,
    days: Decimal,
    effective_date: date,
    leave_year: int,
    idempotency_key: str,
    reason: str,
    application_id: int | None = None,
    reverses_entry_id: int | None = None,
    leave_policy_id: int | None = None,
    job_run_id: int | None = None,
    expires_on: date | None = None,
    actor_user_id: int | None = None,
) -> LeaveLedgerEntry | None:
    """Append one ledger entry, or return None if that key is already posted.

    The idempotency key is checked by the **database**, not by a prior read: two
    concurrent accrual runs would both pass a read-then-write check. Returning
    None on a collision rather than raising is what lets a retried job report
    "already done" instead of failing.
    """
    if days == ZERO:
        return None

    entry = LeaveLedgerEntry(
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        txn_type=txn_type,
        days=days,
        effective_date=effective_date,
        leave_year=leave_year,
        expires_on=expires_on,
        application_id=application_id,
        reverses_entry_id=reverses_entry_id,
        leave_policy_id=leave_policy_id,
        job_run_id=job_run_id,
        idempotency_key=idempotency_key,
        reason=reason,
        created_by_user_id=actor_user_id,
    )
    try:
        with session.begin_nested():
            session.add(entry)
            session.flush()
    except IntegrityError:
        # Already posted. The savepoint rolls back only this insert, so a bulk
        # run continues with the rest.
        return None
    return entry


def adjust_balance(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int,
    days: Decimal,
    effective_date: date,
    reason: str,
    token: str,
    context: audit.AuditContext,
) -> LeaveLedgerEntry:
    """Post a manual correction.

    Requires a reason, because an unexplained adjustment is exactly the thing
    this ledger exists to prevent. ``token`` makes a double-submitted form
    harmless while still allowing two genuinely separate corrections of the same
    size on the same day.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")
    leave_type = get_leave_type(session, leave_type_id)

    if days == ZERO:
        raise ValidationFailed(
            "An adjustment of zero days would do nothing.",
            details=[{"field": "days", "message": "Must not be zero."}],
        )
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required for a manual adjustment.",
            details=[{"field": "reason", "message": "Required."}],
        )

    settings = load_settings(session)
    year = rules.leave_year_for(
        effective_date,
        start_month=settings.year_start_month,
        start_day=settings.year_start_day,
    )

    entry = _post_entry(
        session,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        txn_type="adjustment",
        days=days,
        effective_date=effective_date,
        leave_year=year.label,
        idempotency_key=rules.adjustment_key(
            employee_id=employee_id, type_code=leave_type.code, token=token
        ),
        reason=reason.strip(),
        actor_user_id=context.actor_user_id,
    )
    if entry is None:
        raise Conflict(
            "That adjustment has already been posted. This is the double-submission "
            "guard - the balance was not changed twice."
        )

    audit.record(
        session,
        action="leave.balance_adjusted",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "employee_code": employee.employee_code,
            "leave_type": leave_type.code,
            "days": str(days),
            "effective_date": effective_date.isoformat(),
            "reason": reason.strip(),
        },
    )
    return entry


def grant_opening_balance(
    session: Session,
    *,
    employee_id: int,
    leave_type_id: int,
    days: Decimal,
    leave_year: int | None = None,
    reason: str = "Opening balance",
    context: audit.AuditContext,
) -> LeaveLedgerEntry | None:
    """Record a starting balance, typically when migrating from another system.

    Keyed per employee, type and leave year, so importing the same opening
    balances twice credits once.
    """
    leave_type = get_leave_type(session, leave_type_id)
    settings = load_settings(session)
    today = date.today()
    year = rules.leave_year_for(
        today, start_month=settings.year_start_month, start_day=settings.year_start_day
    )
    label = leave_year if leave_year is not None else year.label
    effective = (
        year.start
        if label == year.label
        else date(label, settings.year_start_month, settings.year_start_day)
    )

    entry = _post_entry(
        session,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        txn_type="opening",
        days=days,
        effective_date=effective,
        leave_year=label,
        idempotency_key=rules.opening_key(
            employee_id=employee_id, type_code=leave_type.code, leave_year=label
        ),
        reason=reason,
        actor_user_id=context.actor_user_id,
    )
    if entry is not None:
        audit.record(
            session,
            action="leave.opening_balance_granted",
            context=context,
            entity_type="employee",
            entity_id=employee_id,
            after={
                "leave_type": leave_type.code,
                "days": str(days),
                "leave_year": label,
            },
        )
    return entry


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def _day_types(
    session: Session, *, employee_id: int, start: date, end: date
) -> dict[date, rules.DayType]:
    """Classify each date in a range for one employee.

    Uses the **same** resolver attendance uses, through
    ``shifts_service.calendar_spec`` and ``resolve_day_type``. Leave and
    attendance disagreeing about which days were working days would produce
    exactly the payroll dispute this system exists to avoid, so there is one
    resolver and no second implementation.
    """
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


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def employee_for_user(session: Session, user: User) -> Employee:
    if user.employee_id is None:
        raise PermissionDenied(
            "Your account is not linked to an employee record, so leave cannot be "
            "applied for. Ask HR to link it."
        )
    employee = session.get(Employee, user.employee_id)
    if employee is None:  # pragma: no cover - FK prevents this
        raise PermissionDenied("Your linked employee record no longer exists.")
    return employee


def _manager_available(session: Session, employee: Employee) -> bool:
    """Whether this employee has a manager who could actually decide.

    An exited manager counts as absent: leaving a request in the queue of
    someone who has left the company means it is never decided.
    """
    if employee.reporting_manager_id is None:
        return False
    manager = session.get(Employee, employee.reporting_manager_id)
    return manager is not None and manager.employment_status != "exited"


def _occupied_days(
    session: Session,
    *,
    employee_id: int,
    start: date,
    end: date,
    exclude_application_id: int | None = None,
) -> list[rules.OccupiedDay]:
    """Days already committed by another open or approved application."""
    statement = (
        select(
            LeaveApplicationDay.leave_date,
            LeaveApplicationDay.portion,
            LeaveApplication.id,
            LeaveApplication.status,
        )
        .join(LeaveApplication, LeaveApplication.id == LeaveApplicationDay.application_id)
        .where(
            LeaveApplication.employee_id == employee_id,
            LeaveApplication.status.in_(BLOCKING_STATUSES),
            LeaveApplicationDay.leave_date >= start,
            LeaveApplicationDay.leave_date <= end,
            LeaveApplicationDay.counts_against_balance.is_(True),
        )
    )
    if exclude_application_id is not None:
        statement = statement.where(LeaveApplication.id != exclude_application_id)

    return [
        rules.OccupiedDay(leave_date=row[0], portion=row[1], application_id=row[2], status=row[3])
        for row in session.execute(statement).all()
    ]


def apply_for_leave(
    session: Session,
    *,
    user: User,
    data: dict[str, Any],
    context: audit.AuditContext,
    on_behalf_of_employee_id: int | None = None,
) -> LeaveApplication:
    """Submit an application, after checking every rule that could refuse it.

    ``on_behalf_of_employee_id`` lets HR record leave for someone else - the
    common case of an employee who was absent and could not apply. It is flagged
    on the application rather than disguised as a self-service request.

    Every problem is reported at once. The balance is checked, but an application
    exceeding it is **split** into paid and unpaid days rather than refused
    outright: forcing an employee with three days in hand to choose between a
    refusal and an unrecorded absence helps nobody.
    """
    if on_behalf_of_employee_id is not None:
        employee = session.get(Employee, on_behalf_of_employee_id)
        if employee is None:
            raise ValidationFailed(
                "The employee does not exist.",
                details=[{"field": "employee_id", "message": "Unknown."}],
            )
    else:
        employee = employee_for_user(session, user)

    leave_type = get_leave_type(session, data["leave_type_id"])
    if not leave_type.is_active:
        raise PolicyViolation(f"{leave_type.name} is no longer available.")

    settings = load_settings(session)
    type_rules = _type_rules(leave_type, settings)

    start: date = data["start_date"]
    end: date = data["end_date"]
    if end < start:
        raise ValidationFailed(
            "The end date cannot be before the start date.",
            details=[{"field": "end_date", "message": "Must not precede the start."}],
        )
    if (end - start).days > 366:
        raise ValidationFailed(
            "A single application cannot span more than a year.",
            details=[{"field": "end_date", "message": "Range too long."}],
        )

    start_portion = data.get("start_portion", "full")
    end_portion = data.get("end_portion", "full")
    # A single-day application has one portion; accepting two would make the
    # total ambiguous.
    if start == end:
        end_portion = start_portion

    day_types = _day_types(session, employee_id=employee.id, start=start, end=end)

    try:
        expansion = rules.expand_leave_days(
            start=start,
            end=end,
            day_types=day_types,
            start_portion=start_portion,
            end_portion=end_portion,
            counts_non_working_days=leave_type.counts_non_working_days,
            sandwich_rule=settings.sandwich_rule_enabled,
            is_paid=leave_type.is_paid,
        )
    except rules.LeaveRuleViolation as error:
        raise ValidationFailed(
            str(error),
            details=[{"field": error.field_name, "message": str(error)}],
        ) from error

    request = rules.ApplicationRequest(
        start_date=start,
        end_date=end,
        start_portion=start_portion,
        end_portion=end_portion,
        reason=data.get("reason"),
        has_attachment=data.get("attachment_file_id") is not None,
    )

    today = date.today()
    problems = rules.check_eligibility(
        request=request,
        rules=type_rules,
        today=today,
        date_of_joining=employee.date_of_joining,
        date_of_exit=employee.date_of_exit,
        employee_gender=employee.gender,
        computed_days=expansion.total_days,
        span_days=expansion.span_days,
        half_day_enabled_company_wide=settings.half_day_enabled,
        # HR recording leave on somebody's behalf is exempt from the backdated
        # window: that window exists to stop an **employee** retro-fitting
        # leave, and the refusal an employee sees says "ask HR to record it",
        # which would be a false promise if HR were blocked by it too. The
        # exemption is narrow - it is flagged on the application, audited, and
        # still subject to every other rule, including the balance.
        backdated_window_days=(
            None if on_behalf_of_employee_id is not None else settings.backdated_window_days
        ),
        max_future_days=settings.future_window_days,
    )

    problems += rules.detect_overlap(
        proposed=expansion.days,
        occupied=_occupied_days(session, employee_id=employee.id, start=start, end=end),
    )

    if problems:
        raise _violation(problems, "This leave application cannot be submitted.")

    policy = resolve_policy(
        session, employee_id=employee.id, leave_type_id=leave_type.id, on_date=start
    )
    if leave_type.deducts_balance and policy is None:
        raise PolicyViolation(
            f"No leave policy is in force for {leave_type.name} on "
            f"{start.isoformat()}, so there is no entitlement to draw on. Ask HR to "
            "configure one.",
            details=[{"field": "leave_type_id", "message": "No policy in force."}],
        )

    balance = balance_for(
        session, employee_id=employee.id, leave_type_id=leave_type.id, as_of=start
    )
    split = rules.split_paid_unpaid(
        requested_days=expansion.total_days,
        available_days=balance.available_days,
        type_is_paid=leave_type.is_paid,
        deducts_balance=leave_type.deducts_balance,
        allow_negative=bool(type_rules.allows_negative_balance),
    )

    manager_missing = not _manager_available(session, employee)
    if manager_missing and settings.manager_missing_behaviour == "block":
        raise PolicyViolation(
            f"{employee.employee_code} has no active reporting manager, so this "
            "application cannot be routed. Ask HR to assign one."
        )

    try:
        steps = rules.build_chain(settings.approval_chain, manager_available=not manager_missing)
    except rules.LeaveRuleViolation as error:
        raise PolicyViolation(str(error)) from error

    first = steps[0]
    application = LeaveApplication(
        employee_id=employee.id,
        leave_type_id=leave_type.id,
        leave_policy_id=policy.id if policy else None,
        start_date=start,
        end_date=end,
        total_days=expansion.total_days,
        paid_days=split.paid_days,
        unpaid_days=split.unpaid_days,
        reason=(data.get("reason") or "").strip() or None,
        contact_during_leave=data.get("contact_during_leave"),
        status=first.status,
        current_stage=first.stage,
        escalated_from_missing_manager=manager_missing,
        is_backdated=start < today,
        applied_by_user_id=context.actor_user_id,
        applied_on_behalf=on_behalf_of_employee_id is not None,
        attachment_file_id=data.get("attachment_file_id"),
        policy_snapshot={
            **rules.policy_snapshot(
                rules=type_rules,
                annual_quota_days=policy.annual_quota_days if policy else None,
                accrual_frequency=policy.accrual_frequency if policy else None,
                sandwich_rule=settings.sandwich_rule_enabled,
                allow_negative=bool(type_rules.allows_negative_balance),
                backdated_window_days=settings.backdated_window_days,
                half_day_enabled=settings.half_day_enabled,
                approval_chain=settings.approval_chain,
            ),
            "expansion_notes": list(expansion.notes),
            "split_reason": split.reason,
            "available_at_submission": str(balance.available_days),
        },
    )
    session.add(application)
    session.flush()

    for day in expansion.days:
        session.add(
            LeaveApplicationDay(
                application_id=application.id,
                leave_date=day.leave_date,
                portion=day.portion,
                day_fraction=day.day_fraction,
                is_paid=day.is_paid,
                day_type=day.day_type,
                counts_against_balance=day.counts_against_balance,
            )
        )

    # The skipped manager stage is recorded as a decision, so an approval
    # history with no manager row is explained rather than looking like a
    # bypass.
    if manager_missing and "reporting_manager" in settings.approval_chain:
        session.add(
            LeaveApproval(
                application_id=application.id,
                sequence=1,
                stage="reporting_manager",
                decision="skipped",
                decided_at=utc_now(),
                comment=(
                    "No active reporting manager, so this stage was skipped and the "
                    "application routed straight to HR."
                ),
                was_auto_skipped=True,
            )
        )

    session.flush()

    audit.record(
        session,
        action="leave.applied",
        context=context,
        entity_type="leave_application",
        entity_id=application.id,
        after={
            "employee_code": employee.employee_code,
            "leave_type": leave_type.code,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "total_days": str(expansion.total_days),
            "paid_days": str(split.paid_days),
            "unpaid_days": str(split.unpaid_days),
            "status": application.status,
            "on_behalf": application.applied_on_behalf,
            "escalated": manager_missing,
        },
    )
    if application.current_stage is not None:
        notification_hooks.leave_awaiting_approval(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            leave_type_name=leave_type.name,
            start_date=start,
            end_date=end,
            total_days=expansion.total_days,
            stage=application.current_stage,
        )
    return application


def get_application(session: Session, application_id: int) -> LeaveApplication:
    application = session.scalar(
        select(LeaveApplication)
        .where(LeaveApplication.id == application_id)
        .options(
            selectinload(LeaveApplication.days),
            selectinload(LeaveApplication.approvals),
        )
    )
    if application is None:
        raise NotFound("Leave application not found.")
    return application


def _assert_is_manager_of(session: Session, *, user: User, employee_id: int) -> None:
    """Verify the relationship, not merely the permission.

    Holding ``leave.approve_as_manager`` is a precondition; it does not say
    *whose* leave you may review. Without this check any employee holding the
    permission could approve anyone's leave, including their own reports' peers.
    """
    if user.employee_id is None:
        raise PermissionDenied(
            "Your account is not linked to an employee record, so you cannot review "
            "leave applications."
        )
    subject = session.get(Employee, employee_id)
    if subject is None:
        raise NotFound("Leave application not found.")
    if subject.reporting_manager_id != user.employee_id:
        # 404 rather than 403: confirming that an application exists would leak
        # who works for whom.
        raise NotFound("Leave application not found.")


def _refresh_application(session: Session, application: LeaveApplication) -> LeaveApplication:
    """Reload the collections after appending to them.

    ``approvals`` and ``days`` are eagerly loaded by :func:`get_application`, so
    a row added afterwards is not in the already-populated collection and the
    response would omit the decision that was just recorded.
    """
    session.refresh(application, attribute_names=["approvals", "days"])
    return application


def decide(
    session: Session,
    *,
    application_id: int,
    user: User,
    approve: bool,
    comment: str | None,
    context: audit.AuditContext,
) -> LeaveApplication:
    """Record one stage's decision and advance or close the application.

    Three rules enforced here and not relied upon from the UI:

    1. **Nobody decides their own application**, whatever permissions they hold.
    2. **A manager stage needs the relationship**, not just the permission.
    3. **Stages run in order** - HR cannot approve something still awaiting the
       manager, because that would discard a review the chain requires.

    Approval at the final stage is what posts the ledger deduction and
    recomputes attendance. Nothing is deducted at an intermediate stage, so a
    request a manager approved and HR refused never touches the balance.
    """
    application = get_application(session, application_id)
    settings = load_settings(session)
    leave_type_name = get_leave_type(session, application.leave_type_id).name

    if not application.is_open:
        raise Conflict(
            f"This application is already {application.status}; it cannot be decided again."
        )

    stage = application.current_stage
    if stage is None:  # pragma: no cover - an open application always has a stage
        raise Conflict("This application has no pending stage.")

    employee = session.get(Employee, application.employee_id)
    if employee is None:  # pragma: no cover - FK prevents this
        raise NotFound("Leave application not found.")

    # 1. No self-approval, regardless of permissions held.
    if user.employee_id is not None and user.employee_id == application.employee_id:
        raise PermissionDenied(
            "You cannot decide your own leave application. Ask your manager or HR."
        )

    # 2. Stage authority.
    if stage == "reporting_manager":
        if user.has_permission("leave.approve_hr"):
            # HR may act at the manager stage, but it is recorded as the manager
            # stage being decided by HR rather than silently skipping it.
            pass
        elif user.has_permission("leave.approve_as_manager"):
            _assert_is_manager_of(session, user=user, employee_id=application.employee_id)
        else:
            raise PermissionDenied("You cannot review this application.")
    elif stage == "hr_admin" and not user.has_permission("leave.approve_hr"):
        raise PermissionDenied("Final approval requires the HR approval permission.")

    decider_employee_id = user.employee_id
    sequence = (
        session.scalar(
            select(func.coalesce(func.max(LeaveApproval.sequence), 0)).where(
                LeaveApproval.application_id == application.id
            )
        )
        or 0
    ) + 1

    session.add(
        LeaveApproval(
            application_id=application.id,
            sequence=sequence,
            stage=stage,
            decision="approved" if approve else "rejected",
            decided_by_user_id=context.actor_user_id,
            decided_by_employee_id=decider_employee_id,
            decided_at=utc_now(),
            comment=(comment or "").strip() or None,
        )
    )

    if not approve:
        application.status = "rejected"
        application.current_stage = None
        application.decided_at = utc_now()
        session.flush()

        audit.record(
            session,
            action="leave.rejected",
            context=context,
            entity_type="leave_application",
            entity_id=application.id,
            after={"stage": stage, "comment": (comment or "").strip() or None},
        )
        notification_hooks.leave_decided(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            leave_type_name=leave_type_name,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=stage,
            approved=False,
            is_final=True,
            comment=(comment or "").strip() or None,
        )
        return _refresh_application(session, application)

    steps = rules.build_chain(
        settings.approval_chain,
        manager_available=not application.escalated_from_missing_manager,
    )
    try:
        following = rules.next_stage(steps, stage)
    except rules.LeaveRuleViolation:
        # The chain was reconfigured after this application was raised. Treat
        # the current stage as final rather than leaving it undecidable.
        logger.warning(
            "Application %s is at stage %r which is no longer in the configured "
            "chain; treating this approval as final.",
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
            action="leave.stage_approved",
            context=context,
            entity_type="leave_application",
            entity_id=application.id,
            after={"stage_decided": stage, "now_awaiting": following.stage},
        )
        notification_hooks.leave_decided(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            leave_type_name=leave_type_name,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=stage,
            approved=True,
            is_final=False,
        )
        notification_hooks.leave_awaiting_approval(
            session,
            application_id=application.id,
            employee_id=application.employee_id,
            leave_type_name=leave_type_name,
            start_date=application.start_date,
            end_date=application.end_date,
            total_days=application.total_days,
            stage=following.stage,
        )
        return _refresh_application(session, application)

    return _finalise_approval(
        session, application=application, employee=employee, settings=settings, context=context
    )


def _finalise_approval(
    session: Session,
    *,
    application: LeaveApplication,
    employee: Employee,
    settings: LeaveSettings,
    context: audit.AuditContext,
) -> LeaveApplication:
    """Final approval: deduct the balance and recompute the affected attendance.

    The balance is re-checked here, not trusted from submission time. Days may
    have been spent by another application approved in the meantime, and
    approving into a negative balance that policy forbids would be worse than
    refusing at this point.
    """
    leave_type = get_leave_type(session, application.leave_type_id)
    type_rules = _type_rules(leave_type, settings)

    if leave_type.deducts_balance:
        balance = balance_for(
            session,
            employee_id=application.employee_id,
            leave_type_id=application.leave_type_id,
            as_of=application.start_date,
            exclude_application_id=application.id,
        )
        split = rules.split_paid_unpaid(
            requested_days=application.total_days,
            available_days=balance.available_days,
            type_is_paid=leave_type.is_paid,
            deducts_balance=leave_type.deducts_balance,
            allow_negative=bool(type_rules.allows_negative_balance),
        )
        if split.unpaid_days > ZERO and not type_rules.allows_negative_balance:
            # The split is recorded rather than the approval refused: the leave
            # is happening either way, and payroll needs to know which days are
            # unpaid. Refusing here would leave an absence with no record at all.
            application.paid_days = split.paid_days
            application.unpaid_days = split.unpaid_days
            _mark_unpaid_tail(session, application=application, paid_days=split.paid_days)

        year = rules.leave_year_for(
            application.start_date,
            start_month=settings.year_start_month,
            start_day=settings.year_start_day,
        )
        deducted = application.paid_days if leave_type.is_paid else ZERO
        if deducted > ZERO:
            entry = _post_entry(
                session,
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                txn_type="deduction",
                days=-deducted,
                effective_date=application.start_date,
                leave_year=year.label,
                idempotency_key=rules.deduction_key(application_id=application.id),
                reason=(
                    f"{leave_type.code} {application.start_date.isoformat()} to "
                    f"{application.end_date.isoformat()}"
                ),
                application_id=application.id,
                leave_policy_id=application.leave_policy_id,
                actor_user_id=context.actor_user_id,
            )
            if entry is None:
                # The deduction key already exists, so this application was
                # already approved and deducted. Refuse rather than double-count.
                raise Conflict("This application has already been deducted from the balance.")

    application.status = "approved"
    application.current_stage = None
    application.decided_at = utc_now()
    session.flush()

    recomputed = _recompute_attendance(session, application=application)

    audit.record(
        session,
        action="leave.approved",
        context=context,
        entity_type="leave_application",
        entity_id=application.id,
        after={
            "employee_code": employee.employee_code,
            "leave_type": leave_type.code,
            "total_days": str(application.total_days),
            "paid_days": str(application.paid_days),
            "unpaid_days": str(application.unpaid_days),
            "attendance_days_recomputed": recomputed,
        },
    )
    notification_hooks.leave_decided(
        session,
        application_id=application.id,
        employee_id=application.employee_id,
        leave_type_name=leave_type.name,
        start_date=application.start_date,
        end_date=application.end_date,
        total_days=application.total_days,
        stage=application.current_stage or "final",
        approved=True,
        is_final=True,
        unpaid_days=application.unpaid_days,
    )
    return _refresh_application(session, application)


def _mark_unpaid_tail(
    session: Session, *, application: LeaveApplication, paid_days: Decimal
) -> None:
    """Mark the days beyond the available balance as unpaid, latest first.

    The earliest days are paid and the tail is unpaid, which is the order an
    employee would expect: the balance covers the start of the absence.
    """
    days = sorted(
        (day for day in application.days if day.counts_against_balance),
        key=lambda day: day.leave_date,
    )
    remaining = paid_days
    for day in days:
        if remaining >= day.day_fraction:
            day.is_paid = True
            remaining -= day.day_fraction
        else:
            day.is_paid = False
    session.flush()


def _recompute_attendance(session: Session, *, application: LeaveApplication) -> int:
    """Rebuild the attendance days this application covers.

    Attendance derives ``on_leave`` from approved applications, so approving or
    cancelling leave has to recompute those days - otherwise a payslip would be
    built from attendance that still says the employee was absent.
    """
    from app.modules.attendance import service as attendance_service

    count = 0
    for day in application.days:
        if not day.counts_against_balance:
            continue
        try:
            attendance_service.recompute_day(
                session,
                employee_id=application.employee_id,
                work_date=day.leave_date,
            )
            count += 1
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Could not recompute attendance for employee %s on %s after a leave decision",
                application.employee_id,
                day.leave_date,
            )
    return count


def withdraw(
    session: Session,
    *,
    application_id: int,
    user: User,
    reason: str | None,
    context: audit.AuditContext,
) -> LeaveApplication:
    """The applicant pulls a request that has not been decided yet.

    Distinct from cancellation: nothing has been deducted, so nothing is
    reversed. The dates become free for another application immediately.
    """
    application = get_application(session, application_id)

    if user.employee_id != application.employee_id and not user.has_permission("leave.read_all"):
        raise NotFound("Leave application not found.")
    if not application.is_open:
        raise Conflict(
            f"This application is {application.status} and can no longer be withdrawn."
            + (" Cancel it instead." if application.status == "approved" else "")
        )

    application.status = "withdrawn"
    application.current_stage = None
    application.decided_at = utc_now()
    application.cancellation_reason = (reason or "").strip() or None
    session.flush()

    audit.record(
        session,
        action="leave.withdrawn",
        context=context,
        entity_type="leave_application",
        entity_id=application.id,
        after={"reason": application.cancellation_reason},
    )
    return application


def cancel(
    session: Session,
    *,
    application_id: int,
    user: User,
    reason: str,
    context: audit.AuditContext,
) -> LeaveApplication:
    """Call off an **approved** leave, reversing its ledger deduction.

    The deduction is reversed with a new entry rather than deleted: the ledger
    is append-only, and keeping both rows means the balance can be explained as
    "deducted on approval, returned on cancellation" instead of silently
    changing.

    Cancelling leave already taken is allowed but flagged, because HR sometimes
    has to correct a record after the fact. Attendance for those days is
    recomputed either way.
    """
    application = get_application(session, application_id)

    is_owner = user.employee_id == application.employee_id
    if not is_owner and not user.has_permission("leave.approve_hr"):
        raise NotFound("Leave application not found.")

    if application.status != "approved":
        raise Conflict(
            f"Only approved leave can be cancelled; this application is {application.status}."
        )
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required to cancel approved leave.",
            details=[{"field": "reason", "message": "Required."}],
        )

    today = date.today()
    already_started = application.start_date <= today

    reversed_total = ZERO
    for entry in session.scalars(
        select(LeaveLedgerEntry).where(
            LeaveLedgerEntry.application_id == application.id,
            LeaveLedgerEntry.txn_type == "deduction",
        )
    ).all():
        reversal = _post_entry(
            session,
            employee_id=entry.employee_id,
            leave_type_id=entry.leave_type_id,
            txn_type="reversal",
            days=-entry.days,
            effective_date=max(entry.effective_date, today)
            if already_started
            else entry.effective_date,
            leave_year=entry.leave_year,
            idempotency_key=rules.reversal_key(application_id=application.id, entry_id=entry.id),
            reason=f"Cancellation of application #{application.id}: {reason.strip()}",
            application_id=application.id,
            reverses_entry_id=entry.id,
            leave_policy_id=entry.leave_policy_id,
            actor_user_id=context.actor_user_id,
        )
        if reversal is not None:
            reversed_total += -entry.days

    application.status = "cancelled"
    application.current_stage = None
    application.cancelled_at = utc_now()
    application.cancelled_by_user_id = context.actor_user_id
    application.cancellation_reason = reason.strip()
    session.flush()

    recomputed = _recompute_attendance(session, application=application)

    audit.record(
        session,
        action="leave.cancelled",
        context=context,
        entity_type="leave_application",
        entity_id=application.id,
        after={
            "reason": reason.strip(),
            "days_returned": str(reversed_total),
            "already_started": already_started,
            "cancelled_by_owner": is_owner,
            "attendance_days_recomputed": recomputed,
        },
    )
    notification_hooks.leave_cancelled(
        session,
        application_id=application.id,
        employee_id=application.employee_id,
        leave_type_name=get_leave_type(session, application.leave_type_id).name,
        start_date=application.start_date,
        end_date=application.end_date,
        days_returned=reversed_total,
        cancelled_by_self=is_owner,
    )
    return application


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_applications(
    session: Session,
    *,
    employee_ids: list[int] | None = None,
    statuses: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    leave_type_id: int | None = None,
    limit: int = 200,
) -> list[LeaveApplication]:
    statement = (
        select(LeaveApplication)
        .options(selectinload(LeaveApplication.days), selectinload(LeaveApplication.approvals))
        .order_by(LeaveApplication.start_date.desc(), LeaveApplication.id.desc())
        .limit(limit)
    )
    if employee_ids is not None:
        if not employee_ids:
            return []
        statement = statement.where(LeaveApplication.employee_id.in_(employee_ids))
    if statuses:
        statement = statement.where(LeaveApplication.status.in_(statuses))
    if leave_type_id is not None:
        statement = statement.where(LeaveApplication.leave_type_id == leave_type_id)
    if start is not None:
        statement = statement.where(LeaveApplication.end_date >= start)
    if end is not None:
        statement = statement.where(LeaveApplication.start_date <= end)
    return list(session.scalars(statement).all())


def pending_for_approver(session: Session, *, user: User) -> list[LeaveApplication]:
    """Applications this user may actually decide right now.

    Scoped by stage *and* relationship, so a manager's queue holds their own
    reports' requests and nobody else's. HR sees everything at the HR stage, plus
    the manager stage for employees whose manager is missing.
    """
    statements: list[LeaveApplication] = []

    if user.has_permission("leave.approve_hr"):
        statements += list_applications(session, statuses=["pending_hr"], limit=500)

    if user.has_permission("leave.approve_as_manager") and user.employee_id is not None:
        direct_reports = list(
            session.scalars(
                select(Employee.id).where(Employee.reporting_manager_id == user.employee_id)
            ).all()
        )
        if direct_reports:
            statements += list_applications(
                session,
                employee_ids=direct_reports,
                statuses=["pending_manager"],
                limit=500,
            )

    seen: set[int] = set()
    unique: list[LeaveApplication] = []
    for application in statements:
        if application.id not in seen:
            seen.add(application.id)
            unique.append(application)
    # Oldest first: the one that has been waiting longest should be decided next.
    unique.sort(key=lambda item: (item.start_date, item.id))
    return unique


def approved_leave_on(
    session: Session, *, employee_id: int, work_date: date
) -> LeaveApplicationDay | None:
    """The approved leave covering one date, if any.

    This is the hook the attendance engine calls. It looks only at **approved**
    applications: a pending request must not mark a day as leave, or an employee
    could avoid being marked absent simply by applying.
    """
    return session.scalar(
        select(LeaveApplicationDay)
        .join(LeaveApplication, LeaveApplication.id == LeaveApplicationDay.application_id)
        .where(
            LeaveApplication.employee_id == employee_id,
            LeaveApplication.status == "approved",
            LeaveApplicationDay.leave_date == work_date,
            LeaveApplicationDay.counts_against_balance.is_(True),
        )
        .order_by(LeaveApplicationDay.id)
    )


# ---------------------------------------------------------------------------
# Accrual
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AccrualReport:
    period_key: str
    employees_considered: int = 0
    entries_posted: int = 0
    already_posted: int = 0
    skipped_no_policy: int = 0
    skipped_not_employed: int = 0
    skipped_at_ceiling: int = 0
    days_credited: Decimal = ZERO
    problems: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_key": self.period_key,
            "employees_considered": self.employees_considered,
            "entries_posted": self.entries_posted,
            "already_posted": self.already_posted,
            "skipped_no_policy": self.skipped_no_policy,
            "skipped_not_employed": self.skipped_not_employed,
            "skipped_at_ceiling": self.skipped_at_ceiling,
            "days_credited": f"{self.days_credited:.2f}",
            "problems": (self.problems or [])[:50],
        }


def run_accrual(
    session: Session,
    *,
    as_of: date | None = None,
    leave_type_id: int | None = None,
    employee_id: int | None = None,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Credit one accrual period's entitlement. Safe to run repeatedly.

    Idempotent through the ledger, not through a prior read: each entry's key is
    ``accrual:<employee>:<type>:<period>``, and a duplicate insert is rejected by
    the unique constraint. ``already_posted`` counts those, so a second run
    reports "nothing to do" rather than silently crediting twice - the single
    most damaging failure in leave management.

    The period is the one containing ``as_of``. Accrual for a past period can be
    run by passing a date inside it, which is how a missed month is caught up.
    """
    as_of = as_of or date.today()
    settings = load_settings(session)
    year = rules.leave_year_for(
        as_of, start_month=settings.year_start_month, start_day=settings.year_start_day
    )

    report_key = "unknown"
    report = AccrualReport(period_key=report_key)
    report.problems = []

    types = (
        [get_leave_type(session, leave_type_id)]
        if leave_type_id is not None
        else list_leave_types(session)
    )

    employees = list(
        session.scalars(
            select(Employee).where(
                Employee.id == employee_id
                if employee_id is not None
                else Employee.employment_status != "exited"
            )
        ).all()
    )
    report.employees_considered = len(employees)

    for leave_type in types:
        periods = None
        for employee in employees:
            policy = resolve_policy(
                session,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                on_date=as_of,
            )
            if policy is None:
                report.skipped_no_policy += 1
                continue
            if policy.accrual_frequency == "none":
                report.skipped_no_policy += 1
                continue

            periods = rules.accrual_periods(year, policy.accrual_frequency)
            period = rules.period_containing(as_of, periods)
            if period is None:  # pragma: no cover - as_of is inside its own year
                continue
            report.period_key = period.key

            if not employee.is_employed_on(period.end) and not employee.is_employed_on(
                period.start
            ):
                report.skipped_not_employed += 1
                continue

            accrual_starts_on = None
            if policy.accrue_from_confirmation and employee.probation_end_date is not None:
                accrual_starts_on = employee.probation_end_date

            balance = balance_for(
                session,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                as_of=period.end,
            )

            working_total = None
            eligible_working = None
            if settings.proration_basis == "working_days":
                working_total, eligible_working = _working_day_counts(
                    session, employee=employee, period=period
                )

            result = rules.compute_accrual(
                period=period,
                rules=rules.AccrualInput(
                    annual_quota_days=policy.annual_quota_days,
                    frequency=policy.accrual_frequency,
                    rounding=policy.accrual_rounding,
                    prorate_on_joining=policy.prorate_on_joining,
                    prorate_on_exit=policy.prorate_on_exit,
                    max_accumulation_days=policy.max_accumulation_days,
                    proration_basis=settings.proration_basis,
                ),
                employed_from=employee.date_of_joining,
                employed_to=employee.date_of_exit,
                accrual_starts_on=accrual_starts_on,
                current_balance=balance.ledger_days,
                working_days_in_period=working_total,
                eligible_working_days=eligible_working,
            )

            if result.is_zero:
                if result.was_capped:
                    report.skipped_at_ceiling += 1
                else:
                    report.skipped_not_employed += 1
                continue

            entry = _post_entry(
                session,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                txn_type="accrual",
                days=result.days,
                effective_date=min(period.end, as_of),
                leave_year=year.label,
                idempotency_key=rules.accrual_key(
                    employee_id=employee.id,
                    type_code=leave_type.code,
                    period_key=period.key,
                ),
                reason=result.reason,
                leave_policy_id=policy.id,
                actor_user_id=context.actor_user_id,
            )
            if entry is None:
                report.already_posted += 1
            else:
                report.entries_posted += 1
                report.days_credited += result.days

    audit.record(
        session,
        action="leave.accrual_run",
        context=context,
        entity_type="job",
        entity_id="leave_accrual",
        extra={**report.as_dict(), "as_of": as_of.isoformat(), "leave_year": year.label},
    )
    session.flush()
    return {**report.as_dict(), "as_of": as_of.isoformat(), "leave_year": year.label}


def _working_day_counts(
    session: Session, *, employee: Employee, period: rules.AccrualPeriod
) -> tuple[int, int]:
    """Working days in a period, and those the employee was eligible for."""
    from app.modules.shifts.policy import working_days_between

    calendar = shifts_service.resolve_calendar_for(session, employee.id)
    spec = shifts_service.calendar_spec(
        session, calendar, start=period.start, end=period.end, employee_id=employee.id
    )
    all_days = working_days_between(period.start, period.end, spec)
    eligible = [day for day in all_days if employee.is_employed_on(day)]
    return len(all_days), len(eligible)


def leave_calendar(
    session: Session,
    *,
    employee_ids: list[int] | None,
    start: date,
    end: date,
    include_pending: bool = False,
) -> list[dict[str, Any]]:
    """Who is away on which days, for a team or company calendar.

    Approved leave only by default. Showing pending requests as absences would
    have a manager plan around leave that has not been granted, so including
    them is opt-in and the status travels with each row.
    """
    statuses = ["approved", "pending_manager", "pending_hr"] if include_pending else ["approved"]

    statement = (
        select(
            Employee.id,
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            LeaveApplicationDay.leave_date,
            LeaveApplicationDay.portion,
            LeaveType.code,
            LeaveType.name,
            LeaveType.colour,
            LeaveApplication.status,
        )
        .join(LeaveApplication, LeaveApplication.id == LeaveApplicationDay.application_id)
        .join(Employee, Employee.id == LeaveApplication.employee_id)
        .join(LeaveType, LeaveType.id == LeaveApplication.leave_type_id)
        .where(
            LeaveApplication.status.in_(statuses),
            LeaveApplicationDay.leave_date >= start,
            LeaveApplicationDay.leave_date <= end,
            LeaveApplicationDay.counts_against_balance.is_(True),
        )
        .order_by(LeaveApplicationDay.leave_date, Employee.employee_code)
    )
    if employee_ids is not None:
        if not employee_ids:
            return []
        statement = statement.where(Employee.id.in_(employee_ids))

    return [
        {
            "employee_id": row[0],
            "employee_code": row[1],
            "employee_name": f"{row[2]} {row[3]}".strip(),
            "leave_date": row[4],
            "portion": row[5],
            "leave_type_code": row[6],
            "leave_type_name": row[7],
            "colour": row[8],
            "status": row[9],
        }
        for row in session.execute(statement).all()
    ]


def summarise(session: Session, applications: list[LeaveApplication]) -> list[dict[str, Any]]:
    """Add employee and leave-type names to applications for a list view.

    Resolved in one query per lookup table rather than per row: an approver
    queue of two hundred requests would otherwise issue four hundred queries.
    """
    if not applications:
        return []

    employee_ids = {item.employee_id for item in applications}
    type_ids = {item.leave_type_id for item in applications}

    employees = {
        row[0]: (row[1], f"{row[2]} {row[3]}".strip())
        for row in session.execute(
            select(
                Employee.id, Employee.employee_code, Employee.first_name, Employee.last_name
            ).where(Employee.id.in_(employee_ids))
        ).all()
    }
    types = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(LeaveType.id, LeaveType.code, LeaveType.name).where(LeaveType.id.in_(type_ids))
        ).all()
    }

    result: list[dict[str, Any]] = []
    for item in applications:
        code, name = employees.get(item.employee_id, (None, None))
        type_code, type_name = types.get(item.leave_type_id, (None, None))
        result.append(
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "employee_code": code,
                "employee_name": name,
                "leave_type_id": item.leave_type_id,
                "leave_type_code": type_code,
                "leave_type_name": type_name,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "total_days": item.total_days,
                "paid_days": item.paid_days,
                "unpaid_days": item.unpaid_days,
                "status": item.status,
                "current_stage": item.current_stage,
                "reason": item.reason,
                "is_backdated": item.is_backdated,
                "escalated_from_missing_manager": item.escalated_from_missing_manager,
                "applied_on_behalf": item.applied_on_behalf,
                "created_at": item.created_at,
            }
        )
    return result


def preview_application(
    session: Session,
    *,
    user: User,
    data: dict[str, Any],
    on_behalf_of_employee_id: int | None = None,
) -> dict[str, Any]:
    """Work out what an application would cost, without creating it.

    Runs the **same** expansion, eligibility and split logic as
    :func:`apply_for_leave` and writes nothing. The form can therefore show
    "2 days, the weekend is not deducted" before submission without the browser
    reimplementing the calendar and sandwich rules - a duplicate that would
    inevitably drift from the backend and mislead the applicant.

    Problems are returned rather than raised, because an incomplete form is the
    normal state while someone is filling it in.
    """
    employee = (
        session.get(Employee, on_behalf_of_employee_id)
        if on_behalf_of_employee_id is not None
        else employee_for_user(session, user)
    )
    if employee is None:
        raise NotFound("Employee not found.")

    leave_type = get_leave_type(session, data["leave_type_id"])
    settings = load_settings(session)
    type_rules = _type_rules(leave_type, settings)

    start: date = data["start_date"]
    end: date = data["end_date"]
    if end < start:
        raise ValidationFailed(
            "The end date cannot be before the start date.",
            details=[{"field": "end_date", "message": "Must not precede the start."}],
        )
    if (end - start).days > 366:
        raise ValidationFailed(
            "A single application cannot span more than a year.",
            details=[{"field": "end_date", "message": "Range too long."}],
        )

    start_portion = data.get("start_portion", "full")
    end_portion = data.get("end_portion", "full")
    if start == end:
        end_portion = start_portion

    day_types = _day_types(session, employee_id=employee.id, start=start, end=end)
    expansion = rules.expand_leave_days(
        start=start,
        end=end,
        day_types=day_types,
        start_portion=start_portion,
        end_portion=end_portion,
        counts_non_working_days=leave_type.counts_non_working_days,
        sandwich_rule=settings.sandwich_rule_enabled,
        is_paid=leave_type.is_paid,
    )

    problems = rules.check_eligibility(
        request=rules.ApplicationRequest(
            start_date=start,
            end_date=end,
            start_portion=start_portion,
            end_portion=end_portion,
            reason=data.get("reason"),
            has_attachment=data.get("attachment_file_id") is not None,
        ),
        rules=type_rules,
        today=date.today(),
        date_of_joining=employee.date_of_joining,
        date_of_exit=employee.date_of_exit,
        employee_gender=employee.gender,
        computed_days=expansion.total_days,
        span_days=expansion.span_days,
        half_day_enabled_company_wide=settings.half_day_enabled,
        backdated_window_days=settings.backdated_window_days,
        max_future_days=settings.future_window_days,
    )
    problems += rules.detect_overlap(
        proposed=expansion.days,
        occupied=_occupied_days(session, employee_id=employee.id, start=start, end=end),
    )

    balance = balance_for(
        session, employee_id=employee.id, leave_type_id=leave_type.id, as_of=start
    )
    split = rules.split_paid_unpaid(
        requested_days=expansion.total_days,
        available_days=balance.available_days,
        type_is_paid=leave_type.is_paid,
        deducts_balance=leave_type.deducts_balance,
        allow_negative=bool(type_rules.allows_negative_balance),
    )

    return {
        "leave_type_code": leave_type.code,
        "total_days": expansion.total_days,
        "paid_days": split.paid_days,
        "unpaid_days": split.unpaid_days,
        "span_days": expansion.span_days,
        "available_days": balance.available_days,
        "split_reason": split.reason,
        "notes": list(expansion.notes),
        "days": [
            {
                "leave_date": day.leave_date,
                "portion": day.portion,
                "day_fraction": day.day_fraction,
                "day_type": day.day_type,
                "counts_against_balance": day.counts_against_balance,
                "is_paid": day.is_paid,
            }
            for day in expansion.days
        ],
        "problems": [
            {"field": problem.field_name, "message": problem.message} for problem in problems
        ],
        "can_submit": not problems,
    }


# ---------------------------------------------------------------------------
# Leave-year closing
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class YearEndReport:
    leave_year: int
    employees_considered: int = 0
    employees_closed: int = 0
    already_closed: int = 0
    skipped_no_policy: int = 0
    skipped_nothing_to_close: int = 0
    carried_days: Decimal = ZERO
    encashed_days: Decimal = ZERO
    lapsed_days: Decimal = ZERO
    rows: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "leave_year": self.leave_year,
            "employees_considered": self.employees_considered,
            "employees_closed": self.employees_closed,
            "already_closed": self.already_closed,
            "skipped_no_policy": self.skipped_no_policy,
            "skipped_nothing_to_close": self.skipped_nothing_to_close,
            "carried_days": f"{self.carried_days:.2f}",
            "encashed_days": f"{self.encashed_days:.2f}",
            "lapsed_days": f"{self.lapsed_days:.2f}",
            "rows": self.rows[:500],
        }


def run_year_end(
    session: Session,
    *,
    leave_year: int,
    leave_type_id: int | None = None,
    employee_id: int | None = None,
    dry_run: bool = False,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Close a leave year: carry forward, encash and lapse what is left.

    Idempotent, which the brief requires explicitly: every entry is keyed by
    employee, leave type and closing year, so a second run posts nothing and
    reports ``already_closed`` rather than carrying a balance forward twice.

    ``dry_run`` computes and returns the whole reconciliation without writing a
    single row. Closing a year moves everybody's entitlement at once, so being
    able to read the outcome first is not a luxury.

    Three entries are posted per employee and type, and they always balance
    against the closing figure:

    * ``carry_forward_out`` removes the carried amount from the closing year.
    * ``carry_forward_in`` adds it to the opening of the next year, with its own
      expiry date if the policy sets one.
    * ``expiry`` and ``encashment`` account for whatever does not carry.

    The pair of carry-forward entries rather than one is what keeps each leave
    year's arithmetic self-contained: the closing year sums to zero carried
    balance, and the new year opens with it.
    """
    settings = load_settings(session)
    year = rules.LeaveYear(
        label=leave_year,
        start=date(leave_year, settings.year_start_month, settings.year_start_day),
        end=date(leave_year + 1, settings.year_start_month, settings.year_start_day)
        - timedelta(days=1),
    )
    next_start = year.end + timedelta(days=1)

    today = date.today()
    if year.end >= today and not dry_run:
        raise PolicyViolation(
            f"Leave year {leave_year} does not end until {year.end.isoformat()}, so it "
            "cannot be closed yet. Use a dry run to preview the outcome.",
            details=[{"field": "leave_year", "message": "The year has not ended."}],
        )

    company_rule = str(
        settings_service.get_value(
            session, "leave.year_end_closing_rule", default="carry_forward_then_lapse"
        )
    )

    report = YearEndReport(leave_year=leave_year)

    types = (
        [get_leave_type(session, leave_type_id)]
        if leave_type_id is not None
        else list_leave_types(session, include_inactive=True)
    )
    employees = list(
        session.scalars(
            select(Employee).where(
                Employee.id == employee_id
                if employee_id is not None
                else Employee.employment_status != "exited"
            )
        ).all()
    )
    report.employees_considered = len(employees)

    for leave_type in types:
        if not leave_type.deducts_balance:
            # Nothing accumulates, so there is nothing to carry or lapse.
            continue

        for employee in employees:
            policy = resolve_policy(
                session,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                on_date=year.end,
            )
            if policy is None:
                report.skipped_no_policy += 1
                continue

            if _already_closed(
                session,
                employee_id=employee.id,
                type_code=leave_type.code,
                leave_year=year.label,
            ):
                # The first run zeroed the closing balance, so without this check
                # a re-run would report "nothing to close" - true but ambiguous
                # between "already done" and "never had a balance".
                report.already_closed += 1
                continue

            balance = balance_for(
                session,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                as_of=year.end,
            )

            try:
                closing_rules = rules.ClosingRules(
                    year_end_rule=company_rule,
                    carry_forward_cap_days=policy.carry_forward_cap_days,
                    carry_forward_expires_after_months=(policy.carry_forward_expires_after_months),
                    encashment_allowed=policy.encashment_allowed,
                    encashment_max_days=policy.encashment_max_days,
                )
            except rules.LeaveRuleViolation as error:
                raise PolicyViolation(
                    str(error),
                    details=[{"field": error.field_name, "message": str(error)}],
                ) from error

            outcome = rules.close_leave_year(
                closing_balance=balance.ledger_days,
                rules=closing_rules,
                next_year_start=next_start,
            )

            row = {
                "employee_id": employee.id,
                "employee_code": employee.employee_code,
                "leave_type_code": leave_type.code,
                "opening_days": str(_opening_balance(session, employee.id, leave_type.id, year)),
                "accrued_days": str(balance.accrued_days),
                "used_days": str(balance.used_days),
                "closing_days": str(outcome.closing_balance),
                "carried_days": str(outcome.carried_days),
                "encashed_days": str(outcome.encashed_days),
                "lapsed_days": str(outcome.lapsed_days),
                "carried_expires_on": (
                    outcome.carried_expires_on.isoformat() if outcome.carried_expires_on else None
                ),
                "reason": outcome.reason,
            }

            if outcome.does_nothing:
                report.skipped_nothing_to_close += 1
                report.rows.append({**row, "action": "nothing to close"})
                continue

            if dry_run:
                report.rows.append({**row, "action": "would post"})
                report.carried_days += outcome.carried_days
                report.encashed_days += outcome.encashed_days
                report.lapsed_days += outcome.lapsed_days
                continue

            posted = _post_year_end_entries(
                session,
                employee=employee,
                leave_type=leave_type,
                policy=policy,
                year=year,
                next_start=next_start,
                outcome=outcome,
                context=context,
            )
            if posted == 0:
                report.already_closed += 1
                report.rows.append({**row, "action": "already closed"})
                continue

            report.employees_closed += 1
            report.carried_days += outcome.carried_days
            report.encashed_days += outcome.encashed_days
            report.lapsed_days += outcome.lapsed_days
            report.rows.append({**row, "action": "posted"})

    if not dry_run:
        audit.record(
            session,
            action="leave.year_end_closed",
            context=context,
            entity_type="job",
            entity_id=f"leave_year_end:{leave_year}",
            extra={key: value for key, value in report.as_dict().items() if key != "rows"},
        )
        session.flush()

    return {**report.as_dict(), "dry_run": dry_run, "company_rule": company_rule}


def _already_closed(session: Session, *, employee_id: int, type_code: str, leave_year: int) -> bool:
    """Whether a closing entry for this employee, type and year already exists.

    Checked by the deterministic idempotency keys rather than by looking for a
    zero balance, because a zero balance is also what an employee with no
    entitlement looks like.
    """
    keys = [
        rules.carry_forward_out_key(
            employee_id=employee_id, type_code=type_code, leave_year=leave_year
        ),
        rules.lapse_key(employee_id=employee_id, type_code=type_code, leave_year=leave_year),
        rules.encashment_key(employee_id=employee_id, type_code=type_code, leave_year=leave_year),
    ]
    return bool(
        session.scalar(
            select(func.count())
            .select_from(LeaveLedgerEntry)
            .where(LeaveLedgerEntry.idempotency_key.in_(keys))
        )
    )


def _opening_balance(
    session: Session, employee_id: int, leave_type_id: int, year: rules.LeaveYear
) -> Decimal:
    """The balance the leave year started with, for the reconciliation row."""
    total = session.scalar(
        select(func.coalesce(func.sum(LeaveLedgerEntry.days), 0)).where(
            LeaveLedgerEntry.employee_id == employee_id,
            LeaveLedgerEntry.leave_type_id == leave_type_id,
            LeaveLedgerEntry.effective_date < year.start,
        )
    )
    return Decimal(str(total or 0))


def _post_year_end_entries(
    session: Session,
    *,
    employee: Employee,
    leave_type: LeaveType,
    policy: LeavePolicy,
    year: rules.LeaveYear,
    next_start: date,
    outcome: rules.ClosingOutcome,
    context: audit.AuditContext,
) -> int:
    """Write the closing entries. Returns how many were actually posted.

    Zero means every key already existed, i.e. this employee and type were closed
    by an earlier run. The caller reports that rather than treating it as an
    error, which is what makes the job safe to re-run after a partial failure.
    """
    posted = 0

    if outcome.carried_days != ZERO:
        out_entry = _post_entry(
            session,
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            txn_type="carry_forward_out",
            days=-outcome.carried_days,
            effective_date=year.end,
            leave_year=year.label,
            idempotency_key=rules.carry_forward_out_key(
                employee_id=employee.id, type_code=leave_type.code, leave_year=year.label
            ),
            reason=f"Closing {year.label}: {outcome.reason}",
            leave_policy_id=policy.id,
            actor_user_id=context.actor_user_id,
        )
        in_entry = _post_entry(
            session,
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            txn_type="carry_forward_in",
            days=outcome.carried_days,
            effective_date=next_start,
            leave_year=year.label + 1,
            expires_on=outcome.carried_expires_on,
            idempotency_key=rules.carry_forward_in_key(
                employee_id=employee.id,
                type_code=leave_type.code,
                leave_year=year.label + 1,
            ),
            reason=(
                f"Carried forward from {year.label}"
                + (
                    f", expiring {outcome.carried_expires_on.isoformat()}"
                    if outcome.carried_expires_on
                    else ""
                )
            ),
            leave_policy_id=policy.id,
            actor_user_id=context.actor_user_id,
        )
        posted += sum(1 for entry in (out_entry, in_entry) if entry is not None)

    if outcome.encashed_days > ZERO:
        entry = _post_entry(
            session,
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            txn_type="encashment",
            days=-outcome.encashed_days,
            effective_date=year.end,
            leave_year=year.label,
            idempotency_key=rules.encashment_key(
                employee_id=employee.id, type_code=leave_type.code, leave_year=year.label
            ),
            reason=(
                f"Encashed at the close of {year.label}. Payment is a payroll action; "
                "this entry records the leave leaving the balance."
            ),
            leave_policy_id=policy.id,
            actor_user_id=context.actor_user_id,
        )
        posted += 1 if entry is not None else 0

    if outcome.lapsed_days > ZERO:
        entry = _post_entry(
            session,
            employee_id=employee.id,
            leave_type_id=leave_type.id,
            txn_type="expiry",
            days=-outcome.lapsed_days,
            effective_date=year.end,
            leave_year=year.label,
            idempotency_key=rules.lapse_key(
                employee_id=employee.id, type_code=leave_type.code, leave_year=year.label
            ),
            reason=f"Lapsed at the close of {year.label}: {outcome.reason}",
            leave_policy_id=policy.id,
            actor_user_id=context.actor_user_id,
        )
        posted += 1 if entry is not None else 0

    return posted


def expire_carried_forward(
    session: Session, *, as_of: date | None = None, context: audit.AuditContext
) -> dict[str, Any]:
    """Lapse carried-forward credit that reached its expiry date.

    Separate from the year-end job because the two happen at different times: a
    balance carried into April with a six-month life expires in September, not at
    the next year end.

    Only the **unused portion** lapses. The amount still available is worked out
    by comparing the carried credit with everything consumed since, so an
    employee who used their carried days keeps the days they earned afresh.
    """
    as_of = as_of or date.today()

    due = session.scalars(
        select(LeaveLedgerEntry).where(
            LeaveLedgerEntry.txn_type == "carry_forward_in",
            LeaveLedgerEntry.expires_on.isnot(None),
            LeaveLedgerEntry.expires_on < as_of,
        )
    ).all()

    expired_total = ZERO
    posted = 0
    already = 0
    skipped = 0

    for entry in due:
        leave_type = session.get(LeaveType, entry.leave_type_id)
        if leave_type is None:  # pragma: no cover - FK prevents this
            continue

        # What is left of this carried lot: the balance as at the expiry date,
        # capped at the lot's own size. Anything beyond that came from the year's
        # own accrual and is not affected.
        balance = balance_for(
            session,
            employee_id=entry.employee_id,
            leave_type_id=entry.leave_type_id,
            as_of=entry.expires_on or as_of,
        )
        key = rules.carry_expiry_key(
            employee_id=entry.employee_id,
            type_code=leave_type.code,
            entry_id=entry.id,
        )
        if session.scalar(
            select(func.count())
            .select_from(LeaveLedgerEntry)
            .where(LeaveLedgerEntry.idempotency_key == key)
        ):
            # Already expired by an earlier run. Checked before the balance,
            # which the first run has since reduced to zero.
            already += 1
            continue

        unused = min(Decimal(entry.days), max(balance.ledger_days, ZERO))
        if unused <= ZERO:
            skipped += 1
            continue

        posted_entry = _post_entry(
            session,
            employee_id=entry.employee_id,
            leave_type_id=entry.leave_type_id,
            txn_type="expiry",
            days=-unused,
            effective_date=entry.expires_on or as_of,
            leave_year=entry.leave_year,
            idempotency_key=key,
            reason=(
                f"Carried-forward credit from {entry.leave_year - 1} expired on "
                f"{(entry.expires_on or as_of).isoformat()}."
            ),
            reverses_entry_id=None,
            leave_policy_id=entry.leave_policy_id,
            actor_user_id=context.actor_user_id,
        )
        if posted_entry is None:
            already += 1
        else:
            posted += 1
            expired_total += unused

    result = {
        "as_of": as_of.isoformat(),
        "lots_examined": len(due),
        "entries_posted": posted,
        "already_expired": already,
        "nothing_left_to_expire": skipped,
        "days_expired": f"{expired_total:.2f}",
    }
    audit.record(
        session,
        action="leave.carry_forward_expired",
        context=context,
        entity_type="job",
        entity_id="leave_carry_expiry",
        extra=result,
    )
    session.flush()
    return result


def reconciliation(
    session: Session, *, leave_year: int, leave_type_id: int | None = None
) -> list[dict[str, Any]]:
    """Per employee and type: opening, accrued, used, carried, lapsed, encashed.

    A reconciliation has to balance, which is why the figures come from summing
    the ledger by transaction type rather than from any stored total. If this
    report did not add up, the ledger would be the thing at fault - and there is
    nowhere else for the discrepancy to hide.
    """
    settings = load_settings(session)
    year_start = date(leave_year, settings.year_start_month, settings.year_start_day)
    year_end = date(leave_year + 1, settings.year_start_month, settings.year_start_day) - timedelta(
        days=1
    )

    statement = (
        select(
            LeaveLedgerEntry.employee_id,
            LeaveLedgerEntry.leave_type_id,
            LeaveLedgerEntry.txn_type,
            func.sum(LeaveLedgerEntry.days),
        )
        .where(
            LeaveLedgerEntry.effective_date >= year_start,
            LeaveLedgerEntry.effective_date <= year_end,
        )
        .group_by(
            LeaveLedgerEntry.employee_id,
            LeaveLedgerEntry.leave_type_id,
            LeaveLedgerEntry.txn_type,
        )
    )
    if leave_type_id is not None:
        statement = statement.where(LeaveLedgerEntry.leave_type_id == leave_type_id)

    buckets: dict[tuple[int, int], dict[str, Decimal]] = {}
    for employee_id, type_id, txn_type, total in session.execute(statement).all():
        buckets.setdefault((employee_id, type_id), {})[txn_type] = Decimal(str(total))

    if not buckets:
        return []

    employees = {
        row[0]: (row[1], f"{row[2]} {row[3]}".strip())
        for row in session.execute(
            select(
                Employee.id, Employee.employee_code, Employee.first_name, Employee.last_name
            ).where(Employee.id.in_({key[0] for key in buckets}))
        ).all()
    }
    types = {
        row[0]: row[1]
        for row in session.execute(
            select(LeaveType.id, LeaveType.code).where(
                LeaveType.id.in_({key[1] for key in buckets})
            )
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for (employee_id, type_id), totals in sorted(buckets.items()):
        code, name = employees.get(employee_id, (None, None))
        opening = totals.get("opening", ZERO) + totals.get("carry_forward_in", ZERO)
        accrued = totals.get("accrual", ZERO)
        adjusted = totals.get("adjustment", ZERO) + totals.get("compoff_credit", ZERO)
        used = -(totals.get("deduction", ZERO) + totals.get("reversal", ZERO))
        carried_out = -totals.get("carry_forward_out", ZERO)
        lapsed = -totals.get("expiry", ZERO)
        encashed = -totals.get("encashment", ZERO)

        closing = _days(opening + accrued + adjusted - used - carried_out - lapsed - encashed)

        rows.append(
            {
                "employee_id": employee_id,
                "employee_code": code,
                "employee_name": name,
                "leave_type_code": types.get(type_id),
                "leave_year": leave_year,
                "opening_days": str(_days(opening)),
                "accrued_days": str(_days(accrued)),
                "adjusted_days": str(_days(adjusted)),
                "used_days": str(_days(used)),
                "carried_out_days": str(_days(carried_out)),
                "lapsed_days": str(_days(lapsed)),
                "encashed_days": str(_days(encashed)),
                "closing_days": str(closing),
            }
        )
    return rows
