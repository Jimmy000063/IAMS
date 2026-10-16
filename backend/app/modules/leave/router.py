"""Leave endpoints.

Authorization is enforced here on every route, and scoping is enforced in the
handler where a permission alone is not enough: a manager with
``leave.read_team`` sees their own reports, HR with ``leave.read_all`` sees
everyone, and anything outside the caller's scope returns **404 rather than
403** so employee ids cannot be enumerated.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import NotFound, ValidationFailed
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.leave import service
from app.modules.leave.schemas import (
    AccrualRunRequest,
    AccrualRunResponse,
    AdjustmentRequest,
    CancelRequest,
    ClosePeriodRequest,
    DecisionRequest,
    ExpireCarriedRequest,
    LeaveApplicationResponse,
    LeaveApplicationSummary,
    LeaveApplyOnBehalfRequest,
    LeaveApplyRequest,
    LeaveBalanceResponse,
    LeaveCalendarEntry,
    LeavePolicyCreate,
    LeavePolicyResponse,
    LeavePreviewRequest,
    LeavePreviewResponse,
    LeaveTypeCreate,
    LeaveTypeResponse,
    LeaveTypeUpdate,
    LedgerEntryResponse,
    OpeningBalanceRequest,
    PolicyAssignmentCreate,
    PolicyAssignmentResponse,
    WithdrawRequest,
    YearEndRequest,
)

router = APIRouter(prefix="/leave", tags=["Leave"])

MAX_RANGE_DAYS = 400


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "end", "message": "Must not precede the start."}],
        )
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValidationFailed(
            f"The range cannot exceed {MAX_RANGE_DAYS} days.",
            details=[{"field": "end", "message": "Range too long."}],
        )


def _visible_employee_ids(session: DbSession, user: User) -> list[int] | None:
    """Which employees this caller may see leave for. None means everyone.

    Returning None for HR rather than a list of every id keeps the query a plain
    scan instead of an ``IN`` clause over the whole organisation.
    """
    if user.has_permission("leave.read_all"):
        return None
    visible: set[int] = set()
    if user.employee_id is not None:
        visible.add(user.employee_id)
        if user.has_permission("leave.read_team"):
            visible |= employees_service.team_employee_ids(session, user.employee_id)
    return sorted(visible)


def _assert_can_see(session: DbSession, user: User, employee_id: int) -> None:
    allowed = _visible_employee_ids(session, user)
    if allowed is not None and employee_id not in allowed:
        # 404, not 403: confirming the employee exists would leak the shape of
        # the organisation to anyone enumerating ids.
        raise NotFound("Employee not found.")


# ---------------------------------------------------------------- leave types


@router.get("/types", response_model=list[LeaveTypeResponse], summary="List leave types")
def list_types(
    session: DbSession,
    _user: Annotated[User, Depends(require_any("leave.read_self", "leave.read_all"))],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[LeaveTypeResponse]:
    """Every employee can read the types, since they have to choose one."""
    return [
        LeaveTypeResponse.model_validate(item)
        for item in service.list_leave_types(session, include_inactive=include_inactive)
    ]


@router.post(
    "/types",
    response_model=LeaveTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a leave type",
)
def create_type(
    payload: LeaveTypeCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> LeaveTypeResponse:
    """Define a kind of leave.

    None are seeded. The statutory types have legally mandated entitlements
    that this project has not been given verified references for, so creating
    them with invented quotas would be worse than leaving the list empty - set
    ``requires_legal_review`` on any type whose entitlement still needs checking.
    """
    leave_type = service.create_leave_type(
        session, data=payload.model_dump(exclude_unset=True), context=context
    )
    return LeaveTypeResponse.model_validate(leave_type)


@router.patch("/types/{type_id}", response_model=LeaveTypeResponse, summary="Update a leave type")
def update_type(
    type_id: int,
    payload: LeaveTypeUpdate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> LeaveTypeResponse:
    """Changes affect future applications only.

    Applications already decided keep the policy snapshot they were decided
    under, so a past approval stays explicable.
    """
    leave_type = service.update_leave_type(
        session, type_id, data=payload.model_dump(exclude_unset=True), context=context
    )
    return LeaveTypeResponse.model_validate(leave_type)


# ------------------------------------------------------------------- policies


@router.get("/policies", response_model=list[LeavePolicyResponse], summary="List leave policies")
def list_policies(
    session: DbSession,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
    leave_type_id: Annotated[int | None, Query()] = None,
) -> list[LeavePolicyResponse]:
    return [
        LeavePolicyResponse.model_validate(item)
        for item in service.list_policies(session, leave_type_id=leave_type_id)
    ]


@router.post(
    "/policies",
    response_model=LeavePolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an effective-dated leave policy",
)
def create_policy(
    payload: LeavePolicyCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> LeavePolicyResponse:
    """Superseding, not editing.

    To change entitlement from a date, create a policy from that date and close
    the previous one. Editing in place would rewrite how earlier applications
    were decided, and each application pins the policy version that governed it
    precisely so that cannot happen.
    """
    policy = service.create_policy(
        session, data=payload.model_dump(exclude_unset=True), context=context
    )
    return LeavePolicyResponse.model_validate(policy)


@router.post(
    "/policies/{policy_id}/close",
    response_model=LeavePolicyResponse,
    summary="End a leave policy",
)
def close_policy(
    policy_id: int,
    payload: ClosePeriodRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> LeavePolicyResponse:
    policy = service.close_policy(
        session, policy_id, effective_to=payload.effective_to, context=context
    )
    return LeavePolicyResponse.model_validate(policy)


@router.get(
    "/employees/{employee_id}/policy-assignments",
    response_model=list[PolicyAssignmentResponse],
    summary="An employee's leave entitlements over time",
)
def list_assignments(
    employee_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> list[PolicyAssignmentResponse]:
    return [
        PolicyAssignmentResponse.model_validate(item)
        for item in service.list_assignments(session, employee_id)
    ]


@router.post(
    "/policy-assignments",
    response_model=PolicyAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a leave policy to an employee",
)
def assign_policy(
    payload: PolicyAssignmentCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> PolicyAssignmentResponse:
    """Give an employee an entitlement from a date.

    Only needed where an employee differs from the type's own policy - a company
    giving everyone the same entitlement does not have to assign it one by one,
    because policy resolution falls back to the leave type's policy.
    """
    assignment = service.assign_policy(
        session,
        employee_id=payload.employee_id,
        leave_policy_id=payload.leave_policy_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        notes=payload.notes,
        context=context,
    )
    return PolicyAssignmentResponse.model_validate(assignment)


@router.post(
    "/policy-assignments/{assignment_id}/close",
    response_model=PolicyAssignmentResponse,
    summary="End a leave policy assignment",
)
def close_assignment(
    assignment_id: int,
    payload: ClosePeriodRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("leave.manage_policy"))],
) -> PolicyAssignmentResponse:
    assignment = service.close_assignment(
        session, assignment_id, effective_to=payload.effective_to, context=context
    )
    return PolicyAssignmentResponse.model_validate(assignment)


# --------------------------------------------------------------- self service


@router.get(
    "/me/balances",
    response_model=list[LeaveBalanceResponse],
    summary="Your leave balances",
)
def my_balances(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("leave.read_self"))],
    as_of: Annotated[date | None, Query()] = None,
) -> list[LeaveBalanceResponse]:
    """Balance, pending and available, per leave type.

    ``available_days`` is the balance minus days already committed by requests
    awaiting a decision - what a new application may actually draw on. Without
    the distinction an employee could apply twice for the same days.
    """
    employee = service.employee_for_user(session, user)
    return [
        LeaveBalanceResponse(**row)
        for row in service.balances_for_employee(session, employee_id=employee.id, as_of=as_of)
    ]


@router.get(
    "/me/applications",
    response_model=list[LeaveApplicationSummary],
    summary="Your leave applications",
)
def my_applications(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("leave.read_self"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LeaveApplicationSummary]:
    employee = service.employee_for_user(session, user)
    applications = service.list_applications(session, employee_ids=[employee.id], limit=limit)
    return [LeaveApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.post(
    "/me/applications",
    response_model=LeaveApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apply for leave",
)
def apply(
    payload: LeaveApplyRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.apply"))],
) -> LeaveApplicationResponse:
    """Submit an application.

    Every reason it could be refused is reported at once rather than one at a
    time. An application exceeding the balance is **split** into paid and unpaid
    days rather than refused, unless the leave type forbids that: forcing
    someone with three days in hand to choose between a refusal and an
    unrecorded absence helps nobody.
    """
    application = service.apply_for_leave(
        session, user=user, data=payload.model_dump(exclude_unset=True), context=context
    )
    return LeaveApplicationResponse.model_validate(application)


@router.post(
    "/preview",
    response_model=LeavePreviewResponse,
    summary="What an application would cost, without creating it",
)
def preview(
    payload: LeavePreviewRequest,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("leave.apply"))],
) -> LeavePreviewResponse:
    """Runs the same expansion and checks as applying, and writes nothing.

    This is why the form does not count days itself: a browser-side copy of the
    calendar and sandwich rules would drift from the backend and tell the
    applicant the wrong number.
    """
    if payload.employee_id is not None and not user.has_permission("leave.approve_hr"):
        raise NotFound("Employee not found.")

    data = payload.model_dump(exclude_unset=True)
    data.pop("employee_id", None)
    return LeavePreviewResponse(
        **service.preview_application(
            session,
            user=user,
            data=data,
            on_behalf_of_employee_id=payload.employee_id,
        )
    )


@router.post(
    "/applications/{application_id}/withdraw",
    response_model=LeaveApplicationResponse,
    summary="Withdraw an undecided application",
)
def withdraw(
    application_id: int,
    payload: WithdrawRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.apply"))],
) -> LeaveApplicationResponse:
    """Pull a request that has not been decided.

    Nothing was deducted, so nothing is reversed and the dates become free
    again. Approved leave is *cancelled* instead, which does reverse a ledger
    entry.
    """
    application = service.withdraw(
        session,
        application_id=application_id,
        user=user,
        reason=payload.reason,
        context=context,
    )
    return LeaveApplicationResponse.model_validate(application)


@router.post(
    "/applications/{application_id}/cancel",
    response_model=LeaveApplicationResponse,
    summary="Cancel approved leave",
)
def cancel(
    application_id: int,
    payload: CancelRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require_any("leave.apply", "leave.approve_hr"))],
) -> LeaveApplicationResponse:
    """Call off approved leave and return the days.

    The deduction is reversed with a new ledger entry rather than deleted, so
    the balance reads "deducted on approval, returned on cancellation" instead
    of silently changing. Attendance for those days is recomputed.
    """
    application = service.cancel(
        session,
        application_id=application_id,
        user=user,
        reason=payload.reason,
        context=context,
    )
    return LeaveApplicationResponse.model_validate(application)


# ------------------------------------------------------------------ approvals


@router.get(
    "/approvals/pending",
    response_model=list[LeaveApplicationSummary],
    summary="Applications you can decide now",
)
def pending_approvals(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("leave.approve_as_manager", "leave.approve_hr"))],
) -> list[LeaveApplicationSummary]:
    """Scoped by stage *and* relationship.

    A manager's queue holds their own reports' requests at the manager stage and
    nobody else's. HR sees the HR stage. Oldest first, because the request that
    has waited longest should be decided next.
    """
    applications = service.pending_for_approver(session, user=user)
    return [LeaveApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.post(
    "/applications/{application_id}/decide",
    response_model=LeaveApplicationResponse,
    summary="Approve or reject one stage",
)
def decide(
    application_id: int,
    payload: DecisionRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require_any("leave.approve_as_manager", "leave.approve_hr"))],
) -> LeaveApplicationResponse:
    """Record a decision and advance or close the application.

    Enforced in the backend, not assumed from the UI: nobody decides their own
    application whatever permissions they hold; a manager stage requires the
    actual reporting relationship; and stages run in order, so HR cannot approve
    something still awaiting its manager.

    Only the **final** stage posts the ledger deduction, so a request a manager
    approved and HR refused never touches the balance.
    """
    application = service.decide(
        session,
        application_id=application_id,
        user=user,
        approve=payload.approve,
        comment=payload.comment,
        context=context,
    )
    return LeaveApplicationResponse.model_validate(application)


# ----------------------------------------------------------- administration


@router.get(
    "/applications",
    response_model=list[LeaveApplicationSummary],
    summary="Leave applications, scoped to what you may see",
)
def list_applications(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[
        User, Depends(require_any("leave.read_all", "leave.read_team", "leave.read_self"))
    ],
    employee_id: Annotated[int | None, Query()] = None,
    leave_type_id: Annotated[int | None, Query()] = None,
    application_status: Annotated[str | None, Query(alias="status")] = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[LeaveApplicationSummary]:
    """HR sees everyone, a manager sees their reports, everyone sees their own."""
    if employee_id is not None:
        _assert_can_see(session, user, employee_id)
        employee_ids: list[int] | None = [employee_id]
    else:
        employee_ids = _visible_employee_ids(session, user)

    applications = service.list_applications(
        session,
        employee_ids=employee_ids,
        statuses=[application_status] if application_status else None,
        leave_type_id=leave_type_id,
        start=start,
        end=end,
        limit=limit,
    )
    return [LeaveApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.get(
    "/applications/{application_id}",
    response_model=LeaveApplicationResponse,
    summary="One application in full, with its decision history",
)
def read_application(
    application_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[
        User, Depends(require_any("leave.read_all", "leave.read_team", "leave.read_self"))
    ],
) -> LeaveApplicationResponse:
    """Includes every stage decision, in order and never overwritten."""
    application = service.get_application(session, application_id)
    _assert_can_see(session, user, application.employee_id)
    return LeaveApplicationResponse.model_validate(application)


@router.post(
    "/applications/on-behalf",
    response_model=LeaveApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record leave for an employee who could not apply",
)
def apply_on_behalf(
    payload: LeaveApplyOnBehalfRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.approve_hr"))],
) -> LeaveApplicationResponse:
    """For the common case of someone absent who could not apply themselves.

    Flagged as raised on their behalf rather than disguised as a self-service
    request, so the record shows who actually submitted it. It still goes
    through the approval chain.
    """
    data = payload.model_dump(exclude_unset=True)
    data.pop("employee_id", None)
    application = service.apply_for_leave(
        session,
        user=user,
        data=data,
        context=context,
        on_behalf_of_employee_id=payload.employee_id,
    )
    return LeaveApplicationResponse.model_validate(application)


@router.get(
    "/employees/{employee_id}/balances",
    response_model=list[LeaveBalanceResponse],
    summary="One employee's leave balances",
)
def employee_balances(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("leave.read_all", "leave.read_team"))],
    as_of: Annotated[date | None, Query()] = None,
) -> list[LeaveBalanceResponse]:
    _assert_can_see(session, user, employee_id)
    return [
        LeaveBalanceResponse(**row)
        for row in service.balances_for_employee(session, employee_id=employee_id, as_of=as_of)
    ]


@router.get(
    "/employees/{employee_id}/ledger",
    response_model=list[LedgerEntryResponse],
    summary="Every movement behind an employee's balance",
)
def employee_ledger(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("leave.read_all", "leave.read_team"))],
    leave_type_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LedgerEntryResponse]:
    """The arithmetic behind the balance, so a dispute can be settled.

    The ledger is append-only and the balance is its sum, so this is the whole
    explanation - there is no separate stored figure that could disagree with it.
    """
    _assert_can_see(session, user, employee_id)
    return [
        LedgerEntryResponse.model_validate(entry)
        for entry in service.ledger_for(
            session, employee_id=employee_id, leave_type_id=leave_type_id, limit=limit
        )
    ]


@router.get(
    "/me/ledger",
    response_model=list[LedgerEntryResponse],
    summary="Every movement behind your own balance",
)
def my_ledger(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("leave.read_self"))],
    leave_type_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[LedgerEntryResponse]:
    """An employee can see their own arithmetic without asking HR for it."""
    employee = service.employee_for_user(session, user)
    return [
        LedgerEntryResponse.model_validate(entry)
        for entry in service.ledger_for(
            session, employee_id=employee.id, leave_type_id=leave_type_id, limit=limit
        )
    ]


@router.post(
    "/adjustments",
    response_model=LedgerEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a manual balance correction",
)
def adjust(
    payload: AdjustmentRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.adjust_ledger"))],
) -> LedgerEntryResponse:
    """Correct a balance, with a reason on the record.

    The reason is mandatory: an unexplained adjustment is exactly what an
    append-only ledger exists to prevent. ``token`` makes a double-submitted
    form harmless while still allowing two genuinely separate corrections of the
    same size on the same day.
    """
    entry = service.adjust_balance(
        session,
        employee_id=payload.employee_id,
        leave_type_id=payload.leave_type_id,
        days=payload.days,
        effective_date=payload.effective_date,
        reason=payload.reason,
        token=payload.token,
        context=context,
    )
    return LedgerEntryResponse.model_validate(entry)


@router.post(
    "/opening-balances",
    response_model=LedgerEntryResponse | None,
    status_code=status.HTTP_201_CREATED,
    summary="Record a starting balance",
)
def opening_balance(
    payload: OpeningBalanceRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.adjust_ledger"))],
) -> LedgerEntryResponse | None:
    """For migrating balances in from another system.

    Keyed per employee, type and leave year, so importing the same file twice
    credits once. A repeat returns null rather than failing, which is what a
    bulk import wants.
    """
    entry = service.grant_opening_balance(
        session,
        employee_id=payload.employee_id,
        leave_type_id=payload.leave_type_id,
        days=payload.days,
        leave_year=payload.leave_year,
        reason=payload.reason,
        context=context,
    )
    return LedgerEntryResponse.model_validate(entry) if entry else None


@router.post(
    "/accrual/run",
    response_model=AccrualRunResponse,
    summary="Credit an accrual period",
)
def run_accrual(
    payload: AccrualRunRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.run_year_end"))],
) -> AccrualRunResponse:
    """Safe to run repeatedly.

    Each entry's idempotency key is ``accrual:<employee>:<type>:<period>``, so a
    re-run reports ``already_posted`` rather than crediting a balance twice -
    the most damaging failure in leave management and the hardest to notice.

    Pass a date inside a past period to catch up a missed run.
    """
    return AccrualRunResponse(
        **service.run_accrual(
            session,
            as_of=payload.as_of,
            leave_type_id=payload.leave_type_id,
            employee_id=payload.employee_id,
            context=context,
        )
    )


@router.get(
    "/calendar",
    response_model=list[LeaveCalendarEntry],
    summary="Who is away, for a team or company calendar",
)
def calendar(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[
        User, Depends(require_any("leave.read_all", "leave.read_team", "leave.read_self"))
    ],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    include_pending: Annotated[bool, Query()] = False,
) -> list[LeaveCalendarEntry]:
    """Approved leave by default.

    Showing pending requests as absences would have a manager plan around leave
    nobody has granted, so including them is opt-in and each row carries its
    status.
    """
    today = date.today()
    range_start = start or today.replace(day=1)
    range_end = end or (range_start + timedelta(days=30))
    _validate_range(range_start, range_end)

    return [
        LeaveCalendarEntry(**row)
        for row in service.leave_calendar(
            session,
            employee_ids=_visible_employee_ids(session, user),
            start=range_start,
            end=range_end,
            include_pending=include_pending,
        )
    ]


@router.post(
    "/year-end/run",
    summary="Close a leave year: carry forward, encash and lapse",
)
def run_year_end(
    payload: YearEndRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.run_year_end"))],
) -> dict[str, object]:
    """Safe to re-run, which the brief requires explicitly.

    Every entry is keyed by employee, leave type and closing year, so a second
    run posts nothing and reports ``already_closed`` rather than carrying a
    balance forward twice.

    Use ``dry_run`` first. Closing a year moves everybody's entitlement at once,
    and the dry run returns the entire reconciliation without writing a row.
    """
    return service.run_year_end(
        session,
        leave_year=payload.leave_year,
        leave_type_id=payload.leave_type_id,
        employee_id=payload.employee_id,
        dry_run=payload.dry_run,
        context=context,
    )


@router.post(
    "/year-end/expire-carried",
    summary="Lapse carried-forward credit that reached its expiry date",
)
def expire_carried(
    payload: ExpireCarriedRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("leave.run_year_end"))],
) -> dict[str, object]:
    """Separate from the year-end job because they happen at different times.

    A balance carried into April with a six-month life expires in September, not
    at the next year end. Only the unused portion lapses.
    """
    return service.expire_carried_forward(session, as_of=payload.as_of, context=context)


@router.get(
    "/year-end/reconciliation",
    summary="Opening, accrued, used, carried, lapsed and encashed per employee",
)
def reconciliation(
    session: DbSession,
    _dep: Annotated[User, Depends(require_any("leave.run_year_end", "report.leave"))],
    leave_year: Annotated[int, Query(ge=2000, le=2100)],
    leave_type_id: Annotated[int | None, Query()] = None,
) -> list[dict[str, object]]:
    """A reconciliation that has to balance.

    Every figure is a sum over the ledger by transaction type, not a stored
    total - so if this did not add up, the ledger itself would be at fault and
    there would be nowhere for a discrepancy to hide.
    """
    return service.reconciliation(session, leave_year=leave_year, leave_type_id=leave_type_id)
