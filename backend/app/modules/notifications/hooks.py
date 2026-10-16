"""The calls other modules make to tell somebody something.

Why a separate module
---------------------
Leave, comp-off, attendance and shifts each need to notify people, but none of
them should know how a notification is stored, keyed or muted. They call one
named function here - ``leave_decided``, ``shift_assigned`` - and that is the
whole of their coupling to this module.

Why every function swallows its errors
--------------------------------------
A notification is a side effect of an operation, never part of its purpose. An
approved leave application that could not be announced is still approved, and
rolling the approval back would be a far worse outcome than a missing line in
somebody's notification centre. Each hook therefore reports failure to the log
and returns.

Why recipients are resolved from employee to user
-------------------------------------------------
Notifications belong to the account that signs in, not to the employee record:
the centre has to be readable by whoever is looking at it. An employee with no
active account is simply skipped - there is nobody to show it to.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth.models import Permission, Role, RolePermission, User, UserRole
from app.modules.employees.models import Employee
from app.modules.notifications import policy, service

logger = logging.getLogger(__name__)

#: A cap on how many people one event may notify. A hook that would wake up
#: hundreds of accounts is a bug in the caller, not a notification to send.
MAX_FANOUT = 200

#: The stage names an approval chain uses for "the employee's own manager".
#: Spelled differently by the leave chain ("reporting_manager") and the
#: attendance one ("manager"), and routing has to recognise both - getting this
#: wrong sends the request to HR and leaves the manager's queue silent.
MANAGER_STAGES: frozenset[str] = frozenset({"manager", "reporting_manager"})


def _user_ids_for_employee(session: Session, employee_id: int | None) -> list[int]:
    if employee_id is None:
        return []
    return list(
        session.scalars(
            select(User.id).where(User.employee_id == employee_id, User.is_active.is_(True))
        ).all()
    )


def _user_ids_with_permission(session: Session, code: str) -> list[int]:
    """Active accounts holding a permission, through any active role.

    Used to route an approval into the right queue. It reads the same grants the
    authorization layer enforces, so the person who is told is the person who
    can actually act.
    """
    return list(
        session.scalars(
            select(User.id)
            .distinct()
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                Permission.code == code,
                Role.is_active.is_(True),
                User.is_active.is_(True),
            )
            .limit(MAX_FANOUT)
        ).all()
    )


def _emit(
    session: Session,
    draft: policy.Draft,
    *,
    user_ids: list[int],
    employee_id: int | None = None,
    job_run_id: int | None = None,
) -> int:
    created = 0
    for user_id in user_ids[:MAX_FANOUT]:
        notification = service.safe_notify(
            session,
            user_id=user_id,
            employee_id=employee_id,
            category=draft.category,
            priority=draft.priority,
            title=draft.title,
            body=draft.body,
            link_path=draft.link_path,
            entity_type=draft.entity_type,
            entity_id=draft.entity_id,
            # The key is per recipient: one fact, one row each.
            dedupe_key=f"{draft.dedupe_key}:user:{user_id}",
            job_run_id=job_run_id,
        )
        if notification is not None:
            created += 1
    return created


def _employee_name(session: Session, employee_id: int) -> str:
    employee = session.get(Employee, employee_id)
    if employee is None:  # pragma: no cover - callers pass a loaded employee
        return "An employee"
    return employee.full_name


# ---------------------------------------------------------------------- leave


def leave_decided(
    session: Session,
    *,
    application_id: int,
    employee_id: int,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
    approved: bool,
    is_final: bool,
    comment: str | None = None,
    unpaid_days: Decimal | None = None,
) -> int:
    try:
        draft = policy.leave_decision(
            application_id=application_id,
            leave_type_name=leave_type_name,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            stage=stage,
            approved=approved,
            is_final=is_final,
            comment=comment,
            unpaid_days=unpaid_days,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the decision on leave application %s", application_id)
        return 0


def leave_awaiting_approval(
    session: Session,
    *,
    application_id: int,
    employee_id: int,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
) -> int:
    """Tell whoever has to decide.

    The manager stage goes to that one manager; any other stage goes to the
    holders of ``leave.approve_hr``, because that is who the chain will accept a
    decision from.
    """
    try:
        employee = session.get(Employee, employee_id)
        if employee is None:  # pragma: no cover - FK prevents this
            return 0

        if stage in MANAGER_STAGES and employee.reporting_manager_id is not None:
            user_ids = _user_ids_for_employee(session, employee.reporting_manager_id)
        else:
            user_ids = _user_ids_with_permission(session, "leave.approve_hr")

        draft = policy.leave_awaiting_approval(
            application_id=application_id,
            applicant_name=employee.full_name,
            leave_type_name=leave_type_name,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            stage=stage,
        )
        return _emit(session, draft, user_ids=user_ids)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify approvers of leave application %s", application_id)
        return 0


def leave_cancelled(
    session: Session,
    *,
    application_id: int,
    employee_id: int,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    days_returned: Decimal,
    cancelled_by_self: bool,
) -> int:
    try:
        draft = policy.leave_cancelled(
            application_id=application_id,
            leave_type_name=leave_type_name,
            start_date=start_date,
            end_date=end_date,
            days_returned=days_returned,
            by_whom="you" if cancelled_by_self else "an administrator",
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Could not notify the cancellation of leave application %s", application_id
        )
        return 0


# -------------------------------------------------------------------- comp-off


def compoff_credited(
    session: Session,
    *,
    credit_id: int,
    employee_id: int,
    earned_date: date,
    credit_days: Decimal,
    needs_verification: bool,
    job_run_id: int | None = None,
) -> int:
    try:
        draft = policy.compoff_credited(
            credit_id=credit_id,
            earned_date=earned_date,
            credit_days=credit_days,
            needs_verification=needs_verification,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
            job_run_id=job_run_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify comp-off credit %s", credit_id)
        return 0


def compoff_credit_decided(
    session: Session,
    *,
    credit_id: int,
    employee_id: int,
    earned_date: date,
    credit_days: Decimal,
    approved: bool,
    reason: str | None = None,
) -> int:
    try:
        draft = policy.compoff_credit_decided(
            credit_id=credit_id,
            earned_date=earned_date,
            credit_days=credit_days,
            approved=approved,
            reason=reason,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the decision on comp-off credit %s", credit_id)
        return 0


def compoff_expired(
    session: Session,
    *,
    credit_id: int,
    employee_id: int,
    expires_on: date,
    days_lost: Decimal,
    job_run_id: int | None = None,
) -> int:
    try:
        draft = policy.compoff_expired(
            credit_id=credit_id, expires_on=expires_on, days_lost=days_lost
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
            job_run_id=job_run_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the expiry of comp-off credit %s", credit_id)
        return 0


def compoff_decided(
    session: Session,
    *,
    application_id: int,
    employee_id: int,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
    approved: bool,
    is_final: bool,
    comment: str | None = None,
) -> int:
    try:
        draft = policy.compoff_decision(
            application_id=application_id,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            stage=stage,
            approved=approved,
            is_final=is_final,
            comment=comment,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the decision on comp-off application %s", application_id)
        return 0


def compoff_awaiting_approval(
    session: Session,
    *,
    application_id: int,
    employee_id: int,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
) -> int:
    try:
        employee = session.get(Employee, employee_id)
        if employee is None:  # pragma: no cover - FK prevents this
            return 0

        if stage in MANAGER_STAGES and employee.reporting_manager_id is not None:
            user_ids = _user_ids_for_employee(session, employee.reporting_manager_id)
        else:
            user_ids = _user_ids_with_permission(session, "compoff.approve_hr")

        draft = policy.compoff_awaiting_approval(
            application_id=application_id,
            applicant_name=employee.full_name,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            stage=stage,
        )
        return _emit(session, draft, user_ids=user_ids)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify approvers of comp-off application %s", application_id)
        return 0


# ----------------------------------------------------------------- attendance


def missing_punch(
    session: Session,
    *,
    employee_id: int,
    work_date: date,
    status: str,
    job_run_id: int | None = None,
) -> int:
    """Ask for a correction on a day the punches do not settle.

    Keyed on the date and the status, so a nightly job that re-examines the same
    day notifies once. If the status changes - an incomplete day becoming a
    missing punch - that is a different fact and is reported.
    """
    try:
        draft = policy.missing_punch(work_date=work_date, status=status)
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
            job_run_id=job_run_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Could not notify employee %s about attendance on %s", employee_id, work_date
        )
        return 0


def regularization_decided(
    session: Session,
    *,
    request_id: int,
    employee_id: int,
    work_date: date,
    approved: bool,
    stage: str,
    comment: str | None = None,
) -> int:
    try:
        draft = policy.regularization_decision(
            request_id=request_id,
            work_date=work_date,
            approved=approved,
            stage=stage,
            comment=comment,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the decision on correction %s", request_id)
        return 0


def regularization_awaiting_approval(
    session: Session,
    *,
    request_id: int,
    employee_id: int,
    work_date: date,
    stage: str,
) -> int:
    try:
        employee = session.get(Employee, employee_id)
        if employee is None:  # pragma: no cover - FK prevents this
            return 0

        if stage in MANAGER_STAGES and employee.reporting_manager_id is not None:
            user_ids = _user_ids_for_employee(session, employee.reporting_manager_id)
        else:
            user_ids = _user_ids_with_permission(session, "regularization.approve_hr")

        draft = policy.regularization_awaiting_approval(
            request_id=request_id,
            applicant_name=employee.full_name,
            work_date=work_date,
            stage=stage,
        )
        return _emit(session, draft, user_ids=user_ids)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify approvers of correction %s", request_id)
        return 0


# --------------------------------------------------------------------- shifts


def shift_assigned(
    session: Session,
    *,
    assignment_id: int,
    employee_id: int,
    shift_name: str,
    effective_from: date,
    effective_to: date | None,
) -> int:
    try:
        draft = policy.shift_changed(
            assignment_id=assignment_id,
            shift_name=shift_name,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify shift assignment %s", assignment_id)
        return 0


# ------------------------------------------------------------------- holidays


def holiday_calendar_published(
    session: Session,
    *,
    calendar_id: int,
    calendar_name: str,
    year: int,
    holiday_count: int,
    employee_ids: list[int],
) -> int:
    """Tell the employees a published calendar applies to.

    The caller resolves the audience, because which employees a calendar covers
    is a holidays question - scope by work location and group - not a
    notification one.
    """
    try:
        draft = policy.holiday_calendar_published(
            calendar_id=calendar_id,
            calendar_name=calendar_name,
            year=year,
            holiday_count=holiday_count,
        )
        created = 0
        for employee_id in employee_ids[:MAX_FANOUT]:
            created += _emit(
                session,
                draft,
                user_ids=_user_ids_for_employee(session, employee_id),
                employee_id=employee_id,
            )
        return created
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the publication of holiday calendar %s", calendar_id)
        return 0


# -------------------------------------------------------------------- payroll


def payslip_published(
    session: Session,
    *,
    payslip_id: int,
    employee_id: int,
    period_label: str,
    job_run_id: int | None = None,
) -> int:
    """Called by payroll when a payslip is released to an employee.

    Carries no figures: a notification is not a secure channel for pay.
    """
    try:
        draft = policy.payslip_published(payslip_id=payslip_id, period_label=period_label)
        return _emit(
            session,
            draft,
            user_ids=_user_ids_for_employee(session, employee_id),
            employee_id=employee_id,
            job_run_id=job_run_id,
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the publication of payslip %s", payslip_id)
        return 0


# -------------------------------------------------------------------- account


def password_changed(session: Session, *, user_id: int, changed_at_iso: str) -> int:
    try:
        draft = policy.password_changed(user_id=user_id, changed_at_iso=changed_at_iso)
        return _emit(session, draft, user_ids=[user_id])
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not notify the password change for user %s", user_id)
        return 0
