"""Attendance endpoints: punching, viewing, correcting and regularizing."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.modules.attendance import service
from app.modules.attendance.models import (
    AttendanceDay,
    AttendanceEvent,
    AttendanceRegularization,
)
from app.modules.attendance.schemas import (
    AttendanceDayResponse,
    AttendanceEventResponse,
    AttendanceSummaryResponse,
    ManualEventRequest,
    OverrideDayRequest,
    PunchRequest,
    PunchResponse,
    PunchStateResponse,
    RecomputeRequest,
    RecomputeResponse,
    RegularizationDecisionRequest,
    RegularizationRequest,
    RegularizationResponse,
    SupersedeEventRequest,
    TodayOverviewResponse,
    UnsettledNotifyRequest,
    UnsettledNotifyResponse,
)
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.employees.models import Employee

router = APIRouter(prefix="/attendance", tags=["Attendance"])

MAX_RANGE_DAYS = 400


def _resolve_own_employee(user: User) -> int:
    if user.employee_id is None:
        raise Conflict(
            "Your account is not linked to an employee record, so attendance "
            "cannot be recorded or shown. Contact HR."
        )
    return user.employee_id


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValidationFailed(f"Request at most {MAX_RANGE_DAYS} days at a time.")


def _regularization_response(session, request: AttendanceRegularization) -> RegularizationResponse:
    employee = session.get(Employee, request.employee_id)
    return RegularizationResponse(
        id=request.id,
        employee_id=request.employee_id,
        employee_code=employee.employee_code if employee else None,
        employee_name=employee.full_name if employee else None,
        work_date=request.work_date,
        requested_in_at=request.requested_in_at,
        requested_out_at=request.requested_out_at,
        reason=request.reason,
        status=request.status,
        current_stage=request.current_stage,
        status_before=request.status_before,
        manager_decision=request.manager_decision,
        manager_comment=request.manager_comment,
        manager_decided_at=request.manager_decided_at,
        hr_decision=request.hr_decision,
        hr_comment=request.hr_comment,
        hr_decided_at=request.hr_decided_at,
        escalated_from_missing_manager=request.escalated_from_missing_manager,
        created_at=request.created_at,
    )


# ---------------------------------------------------------------------------
# Punching
# ---------------------------------------------------------------------------


@router.get(
    "/state",
    response_model=PunchStateResponse,
    summary="Your current punch state",
)
def punch_state(session: DbSession, user: CurrentUser) -> PunchStateResponse:
    """Drives the punch button: whether you are in, and the day so far.

    Computed with the same session-pairing the attendance engine uses, so the
    button can never disagree with the resulting day. ``server_time`` is
    included so the UI can display elapsed time without relying on the device
    clock.
    """
    employee_id = _resolve_own_employee(user)
    return PunchStateResponse(**service.current_state(session, employee_id))


@router.post(
    "/punch",
    response_model=PunchResponse,
    summary="Punch in or out",
    responses={
        409: {"description": "Account not linked to an employee record"},
        422: {"description": "Punch not permitted in the current state"},
    },
)
def punch(
    payload: PunchRequest,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.punch"))],
) -> PunchResponse:
    """Record a punch using the **server's** clock.

    There is no timestamp in the request body by design: a client-supplied time
    would let an employee record attendance for a period they were not present.

    The punch is attributed to the shift date in the employee's work-location
    timezone, which for an overnight shift is the date the shift started - a
    02:00 punch on the 5th belongs to the 4th.

    A repeated identical punch within a few seconds returns ``recorded: false``
    with the unchanged state, so a double-tapped button is not an error the
    employee has to interpret.
    """
    result = service.punch(session, user=user, requested_type=payload.punch_type, context=ctx)
    return PunchResponse(
        recorded=result.recorded,
        event_type=result.event_type,
        work_date=result.day.work_date,
        day=AttendanceDayResponse.model_validate(result.day),
        message=(
            f"Punched {result.event_type}."
            if result.recorded
            else "That punch was already recorded a moment ago."
        ),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=list[AttendanceDayResponse],
    summary="Your attendance for a date range",
)
def my_attendance(
    session: DbSession,
    user: CurrentUser,
    start: Annotated[date | None, Query(description="Defaults to the start of this month")] = None,
    end: Annotated[date | None, Query(description="Defaults to today")] = None,
) -> list[AttendanceDayResponse]:
    employee_id = _resolve_own_employee(user)
    today = date.today()
    range_start = start or today.replace(day=1)
    range_end = end or today
    _validate_range(range_start, range_end)

    rows = service.day_range(session, employee_id=employee_id, start=range_start, end=range_end)
    return [AttendanceDayResponse.model_validate(row) for row in rows]


@router.get(
    "/me/summary",
    response_model=AttendanceSummaryResponse,
    summary="Totals for your attendance over a period",
)
def my_summary(
    session: DbSession,
    user: CurrentUser,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> AttendanceSummaryResponse:
    """``payable_days`` sums the stored per-day fractions rather than counting
    statuses, so it cannot drift from the figure payroll will use."""
    employee_id = _resolve_own_employee(user)
    today = date.today()
    range_start = start or today.replace(day=1)
    range_end = end or today
    _validate_range(range_start, range_end)

    return AttendanceSummaryResponse(
        **service.month_summary(session, employee_id=employee_id, start=range_start, end=range_end)
    )


@router.get(
    "/employees/{employee_id}",
    response_model=list[AttendanceDayResponse],
    summary="One employee's attendance",
)
def employee_attendance(
    employee_id: int,
    session: DbSession,
    actor: Annotated[User, Depends(require_any("attendance.read_all", "attendance.read_team"))],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[AttendanceDayResponse]:
    """Scoped to employees the caller may see.

    A manager with ``attendance.read_team`` can read their own reports; HR with
    ``attendance.read_all`` can read anyone. Outside that scope the response is
    404, not 403, so employee ids cannot be enumerated.
    """
    if not actor.has_permission("attendance.read_all"):
        team = (
            employees_service.team_employee_ids(session, actor.employee_id)
            if actor.employee_id
            else set()
        )
        if employee_id != actor.employee_id and employee_id not in team:
            raise NotFound("Employee not found.")

    today = date.today()
    range_start = start or today.replace(day=1)
    range_end = end or today
    _validate_range(range_start, range_end)

    rows = service.day_range(session, employee_id=employee_id, start=range_start, end=range_end)
    return [AttendanceDayResponse.model_validate(row) for row in rows]


@router.get(
    "/employees/{employee_id}/events",
    response_model=list[AttendanceEventResponse],
    summary="Raw punch events for one employee",
)
def employee_events(
    employee_id: int,
    session: DbSession,
    _actor: Annotated[User, Depends(require("attendance.read_all"))],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    include_superseded: Annotated[
        bool, Query(description="Include events replaced by a correction")
    ] = True,
) -> list[AttendanceEventResponse]:
    """The immutable record behind the computed days.

    Superseded events are included by default: seeing what was replaced, and by
    what, is the point of keeping them.
    """
    today = date.today()
    range_start = start or today.replace(day=1)
    range_end = end or today
    _validate_range(range_start, range_end)

    statement = select(AttendanceEvent).where(
        AttendanceEvent.employee_id == employee_id,
        AttendanceEvent.work_date >= range_start,
        AttendanceEvent.work_date <= range_end,
    )
    if not include_superseded:
        statement = statement.where(AttendanceEvent.is_superseded.is_(False))

    rows = session.scalars(statement.order_by(AttendanceEvent.occurred_at)).all()
    return [AttendanceEventResponse.model_validate(row) for row in rows]


@router.get(
    "/today",
    response_model=TodayOverviewResponse,
    summary="Company-wide attendance for today",
)
def today_overview(
    session: DbSession,
    _actor: Annotated[User, Depends(require("attendance.read_all"))],
) -> TodayOverviewResponse:
    """``not_yet_computed`` is reported separately from ``absent``.

    An employee with no row yet has not been marked absent - the day may simply
    not have been computed, or they may be mid-shift. Collapsing the two would
    misreport absence on the HR dashboard.
    """
    return TodayOverviewResponse(**service.today_overview(session))


# ---------------------------------------------------------------------------
# Correction
# ---------------------------------------------------------------------------


@router.post(
    "/events",
    response_model=AttendanceDayResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a punch on an employee's behalf",
)
def add_manual_event(
    payload: ManualEventRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.manage"))],
) -> AttendanceDayResponse:
    """Inserts an event and recomputes; it never edits an existing punch.

    The original punches stay on the record, so the correction is visible as a
    correction rather than as changed history. A reason is required.
    """
    day = service.add_manual_event(
        session,
        employee_id=payload.employee_id,
        event_type=payload.event_type,
        local_datetime=payload.local_datetime,
        reason=payload.reason,
        context=ctx,
    )
    if day is None:
        raise Conflict("The employee was not on the rolls for that date.")
    return AttendanceDayResponse.model_validate(day)


@router.post(
    "/events/{event_id}/supersede",
    response_model=AttendanceDayResponse,
    summary="Stop a punch from counting, without deleting it",
)
def supersede_event(
    event_id: int,
    payload: SupersedeEventRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.manage"))],
) -> AttendanceDayResponse:
    """For a spurious punch - a device double-reporting, say.

    The row is retained for audit and reconciliation; recomputation ignores it.
    """
    day = service.supersede_event(session, event_id=event_id, reason=payload.reason, context=ctx)
    if day is None:
        raise Conflict("The employee was not on the rolls for that date.")
    return AttendanceDayResponse.model_validate(day)


@router.post(
    "/override",
    response_model=AttendanceDayResponse,
    summary="Set a day's status directly",
)
def override_day(
    payload: OverrideDayRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.manage"))],
) -> AttendanceDayResponse:
    """The escape hatch for cases the engine cannot know about.

    Deliberately sticky - later recomputations preserve it - and deliberately
    loud: the row is flagged `is_manual_override`, a reason is required, and the
    audit entry records the previous and new values.
    """
    day = service.override_day_status(
        session,
        employee_id=payload.employee_id,
        work_date=payload.work_date,
        status=payload.status,
        payable_day_fraction=payload.payable_day_fraction,
        reason=payload.reason,
        context=ctx,
    )
    return AttendanceDayResponse.model_validate(day)


@router.post(
    "/recompute",
    response_model=RecomputeResponse,
    summary="Recompute attendance for a date range",
)
def recompute(
    payload: RecomputeRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.recalculate"))],
) -> RecomputeResponse:
    """Rebuilds days from their raw events. Idempotent.

    By default this honours `attendance.recompute_locked_after_days`, so a
    policy change cannot silently rewrite old attendance, and it preserves days
    HR has pinned. Both guards can be waived explicitly, and the audit entry
    records that they were.
    """
    result = service.recompute_range(
        session,
        employee_ids=payload.employee_ids,
        start=payload.start,
        end=payload.end,
        context=ctx,
        force_over_manual_override=payload.force_over_manual_override,
        respect_lock=payload.respect_lock,
    )
    return RecomputeResponse(**result)


@router.post(
    "/notify-unsettled",
    response_model=UnsettledNotifyResponse,
    summary="Ask employees to correct days the punches do not settle",
)
def notify_unsettled(
    payload: UnsettledNotifyRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("attendance.recalculate"))],
) -> UnsettledNotifyResponse:
    """Notify employees about incomplete or missing-punch days, once each.

    Today is never included: an open shift is not a missing punch. Nothing is
    classified as absence - the notification asks for a correction. Safe to run
    nightly, because each notification is keyed by the date and the status.
    """
    result = service.notify_unsettled_days(
        session,
        start=payload.start,
        end=payload.end,
        employee_ids=payload.employee_ids,
        context=ctx,
    )
    return UnsettledNotifyResponse.model_validate(result)


# ---------------------------------------------------------------------------
# Regularization
# ---------------------------------------------------------------------------


@router.post(
    "/regularizations",
    response_model=RegularizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a correction to your own attendance",
)
def request_regularization(
    payload: RegularizationRequest,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("regularization.apply"))],
) -> RegularizationResponse:
    """Goes to your reporting manager, then to HR for the final decision.

    If you have no active reporting manager the manager stage is skipped
    according to `leave.manager_missing_behaviour` - otherwise the request would
    sit with nobody able to act on it. Requests are limited to
    `attendance.regularization_window_days` in the past.
    """
    request = service.request_regularization(
        session,
        user=user,
        work_date=payload.work_date,
        local_in=payload.local_in,
        local_out=payload.local_out,
        reason=payload.reason,
        context=ctx,
    )
    return _regularization_response(session, request)


@router.get(
    "/regularizations/mine",
    response_model=list[RegularizationResponse],
    summary="Your own regularization requests",
)
def my_regularizations(
    session: DbSession,
    user: CurrentUser,
    _actor: Annotated[User, Depends(require("regularization.apply"))],
) -> list[RegularizationResponse]:
    employee_id = _resolve_own_employee(user)
    rows = session.scalars(
        select(AttendanceRegularization)
        .where(AttendanceRegularization.employee_id == employee_id)
        .order_by(AttendanceRegularization.work_date.desc())
    ).all()
    return [_regularization_response(session, row) for row in rows]


@router.get(
    "/regularizations/pending",
    response_model=list[RegularizationResponse],
    summary="Requests awaiting your decision",
)
def pending_regularizations(
    session: DbSession,
    user: CurrentUser,
    _actor: Annotated[
        User,
        Depends(require_any("regularization.approve_as_manager", "regularization.approve_hr")),
    ],
) -> list[RegularizationResponse]:
    """Only requests this user can actually decide.

    For a manager that means the manager-stage requests of their own direct
    reports; for HR, every request at the HR stage. Scoping it here means a
    reviewer is never shown something they would be refused on.
    """
    return [
        _regularization_response(session, row)
        for row in service.pending_for_reviewer(session, user)
    ]


@router.post(
    "/regularizations/{request_id}/decision",
    response_model=RegularizationResponse,
    summary="Approve or reject a regularization request",
    responses={403: {"description": "Not the subject's manager, or wrong stage"}},
)
def decide_regularization(
    request_id: int,
    payload: RegularizationDecisionRequest,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
) -> RegularizationResponse:
    """Records a decision at whichever stage the request currently sits at.

    Manager approval advances the request; it does not apply the change. HR
    gives the final decision, which inserts `regularization` events and
    recomputes the day - so the correction appears in the raw record rather
    than silently altering a total.

    Nobody can decide their own request, including an HR user who is the subject.
    A manager can only decide for their own direct reports, which is checked
    against the reporting tree and not merely by permission.
    """
    request = service.decide_regularization(
        session,
        request_id=request_id,
        user=user,
        approve=payload.approve,
        comment=payload.comment,
        context=ctx,
    )
    return _regularization_response(session, request)


@router.post(
    "/regularizations/{request_id}/cancel",
    response_model=RegularizationResponse,
    summary="Withdraw your own request",
)
def cancel_regularization(
    request_id: int,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
) -> RegularizationResponse:
    request = service.cancel_regularization(session, request_id=request_id, user=user, context=ctx)
    return _regularization_response(session, request)


@router.get(
    "/anomalies",
    response_model=list[AttendanceDayResponse],
    summary="Days with data problems",
)
def anomalies(
    session: DbSession,
    _actor: Annotated[User, Depends(require("attendance.read_all"))],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[AttendanceDayResponse]:
    """Missing punches and unpaired events, for reconciliation.

    These are surfaced rather than quietly discarded: an unpaired punch is a
    real event that needs a human decision, and leaving it invisible is how it
    becomes a payroll dispute.
    """
    today = date.today()
    range_start = start or today - timedelta(days=30)
    range_end = end or today
    _validate_range(range_start, range_end)

    rows = session.scalars(
        select(AttendanceDay)
        .where(
            AttendanceDay.work_date >= range_start,
            AttendanceDay.work_date <= range_end,
            (AttendanceDay.has_missing_punch.is_(True)) | (AttendanceDay.anomalies.isnot(None)),
        )
        .order_by(AttendanceDay.work_date.desc(), AttendanceDay.employee_id)
    ).all()
    return [AttendanceDayResponse.model_validate(row) for row in rows]
