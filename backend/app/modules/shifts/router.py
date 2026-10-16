"""Shift and work-calendar endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.core.deps import AuditCtx, DbSession, require
from app.core.errors import NotFound
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.shifts import service
from app.modules.shifts.models import CalendarException, Shift, WorkCalendar
from app.modules.shifts.policy import resolve_day_type
from app.modules.shifts.schemas import (
    CalendarExceptionCreate,
    CalendarExceptionResponse,
    DayTypeResponse,
    ShiftAssignmentCloseRequest,
    ShiftAssignmentCreate,
    ShiftAssignmentResponse,
    ShiftCreate,
    ShiftResponse,
    ShiftUpdate,
    WeeklyOffPatternCreate,
    WeeklyOffPatternResponse,
    WorkCalendarCreate,
    WorkCalendarResponse,
)

router = APIRouter(prefix="/shifts", tags=["Shifts & Calendars"])


def _shift_response(shift: Shift) -> ShiftResponse:
    return ShiftResponse(
        id=shift.id,
        code=shift.code,
        name=shift.name,
        description=shift.description,
        start_time=shift.start_time,
        end_time=shift.end_time,
        crosses_midnight=shift.crosses_midnight,
        break_minutes=shift.break_minutes,
        scheduled_minutes=shift.scheduled_minutes,
        grace_late_minutes=shift.grace_late_minutes,
        grace_early_exit_minutes=shift.grace_early_exit_minutes,
        min_minutes_full_day=shift.min_minutes_full_day,
        min_minutes_half_day=shift.min_minutes_half_day,
        early_punch_window_minutes=shift.early_punch_window_minutes,
        late_punch_window_minutes=shift.late_punch_window_minutes,
        work_location_id=shift.work_location_id,
        is_active=shift.is_active,
    )


# -------------------------------------------------------------------- shifts


@router.get("", response_model=list[ShiftResponse], summary="List shifts")
def list_shifts(
    session: DbSession,
    _actor: Annotated[User, Depends(require("shift.read"))],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[ShiftResponse]:
    return [
        _shift_response(shift)
        for shift in service.list_shifts(session, include_inactive=include_inactive)
    ]


@router.post(
    "",
    response_model=ShiftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shift",
)
def create_shift(
    payload: ShiftCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("shift.manage"))],
) -> ShiftResponse:
    """``crosses_midnight`` is derived: it is true when the end time is at or
    before the start time.

    Thresholds left null inherit the company setting of the same name, so a
    night shift can override just its grace period without restating the rest.
    """
    shift = service.create_shift(session, data=payload.model_dump(), context=ctx)
    return _shift_response(shift)


@router.patch(
    "/{shift_id}",
    response_model=ShiftResponse,
    summary="Update a shift",
)
def update_shift(
    shift_id: int,
    payload: ShiftUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("shift.manage"))],
) -> ShiftResponse:
    """Affects attendance computed from now on.

    Existing `attendance_days` rows keep the figures and the policy snapshot
    they were calculated with, so history is not rewritten. Use the recompute
    endpoint deliberately if a change should be applied retroactively.
    """
    shift = service.update_shift(
        session, shift_id, data=payload.model_dump(exclude_unset=True), context=ctx
    )
    return _shift_response(shift)


# ------------------------------------------------------------------ patterns


@router.get(
    "/weekly-off-patterns",
    response_model=list[WeeklyOffPatternResponse],
    summary="List weekly-off patterns",
)
def list_patterns(
    session: DbSession,
    _actor: Annotated[User, Depends(require("calendar.read"))],
) -> list[WeeklyOffPatternResponse]:
    return [
        WeeklyOffPatternResponse(
            id=pattern.id,
            code=pattern.code,
            name=pattern.name,
            off_days=pattern.off_days,
            is_active=pattern.is_active,
        )
        for pattern in service.list_patterns(session)
    ]


@router.post(
    "/weekly-off-patterns",
    response_model=WeeklyOffPatternResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a weekly-off pattern",
)
def create_pattern(
    payload: WeeklyOffPatternCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("calendar.manage"))],
) -> WeeklyOffPatternResponse:
    """The company default confirmed in the brief is Monday to Friday working,
    i.e. Saturday and Sunday off."""
    pattern = service.create_weekly_off_pattern(session, data=payload.model_dump(), context=ctx)
    return WeeklyOffPatternResponse(
        id=pattern.id,
        code=pattern.code,
        name=pattern.name,
        off_days=pattern.off_days,
        is_active=pattern.is_active,
    )


# ----------------------------------------------------------------- calendars


def _calendar_response(session, calendar: WorkCalendar) -> WorkCalendarResponse:
    count = (
        session.scalar(
            select(func.count())
            .select_from(CalendarException)
            .where(CalendarException.calendar_id == calendar.id)
        )
        or 0
    )
    return WorkCalendarResponse(
        id=calendar.id,
        code=calendar.code,
        name=calendar.name,
        weekly_off_pattern_id=calendar.weekly_off_pattern_id,
        weekly_off_pattern_code=calendar.weekly_off_pattern.code,
        off_days=calendar.weekly_off_pattern.off_days,
        work_location_id=calendar.work_location_id,
        is_default=calendar.is_default,
        is_active=calendar.is_active,
        exception_count=count,
    )


@router.get(
    "/calendars",
    response_model=list[WorkCalendarResponse],
    summary="List work calendars",
)
def list_calendars(
    session: DbSession,
    _actor: Annotated[User, Depends(require("calendar.read"))],
) -> list[WorkCalendarResponse]:
    return [_calendar_response(session, row) for row in service.list_calendars(session)]


@router.post(
    "/calendars",
    response_model=WorkCalendarResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work calendar",
)
def create_calendar(
    payload: WorkCalendarCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("calendar.manage"))],
) -> WorkCalendarResponse:
    """A calendar is a weekly-off pattern plus date exceptions.

    It is the single source of "is this a working day" for attendance and, from
    Milestone 5, for leave - so the two engines cannot disagree.
    """
    calendar = service.create_work_calendar(session, data=payload.model_dump(), context=ctx)
    return _calendar_response(session, calendar)


@router.get(
    "/calendars/{calendar_id}/exceptions",
    response_model=list[CalendarExceptionResponse],
    summary="List a calendar's date exceptions",
)
def list_exceptions(
    calendar_id: int,
    session: DbSession,
    _actor: Annotated[User, Depends(require("calendar.read"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[CalendarExceptionResponse]:
    if session.get(WorkCalendar, calendar_id) is None:
        raise NotFound("Work calendar not found.")

    statement = select(CalendarException).where(CalendarException.calendar_id == calendar_id)
    if year is not None:
        statement = statement.where(
            CalendarException.exception_date >= date(year, 1, 1),
            CalendarException.exception_date <= date(year, 12, 31),
        )
    rows = session.scalars(statement.order_by(CalendarException.exception_date)).all()
    return [CalendarExceptionResponse.model_validate(row) for row in rows]


@router.post(
    "/calendars/{calendar_id}/exceptions",
    response_model=CalendarExceptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a calendar date exception",
)
def add_exception(
    calendar_id: int,
    payload: CalendarExceptionCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("calendar.manage"))],
) -> CalendarExceptionResponse:
    """Two directions, both needed: closing a working day, and opening a
    weekend to compensate for one."""
    entry = service.add_calendar_exception(
        session, calendar_id, data=payload.model_dump(), context=ctx
    )
    return CalendarExceptionResponse.model_validate(entry)


@router.get(
    "/calendars/{calendar_id}/day-types",
    response_model=list[DayTypeResponse],
    summary="Resolve day types over a date range",
)
def resolve_day_types(
    calendar_id: int,
    session: DbSession,
    start: Annotated[date, Query(description="First date, inclusive")],
    end: Annotated[date, Query(description="Last date, inclusive")],
    _actor: Annotated[User, Depends(require("calendar.read"))],
) -> list[DayTypeResponse]:
    """Shows how the calendar classifies each date, and which rule decided it.

    Useful for checking a calendar before it drives attendance, rather than
    discovering a mistake in a month-end report.
    """
    from app.core.errors import ValidationFailed
    from app.modules.shifts.policy import date_range

    calendar = session.get(WorkCalendar, calendar_id)
    if calendar is None:
        raise NotFound("Work calendar not found.")
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")
    if (end - start).days > 400:
        raise ValidationFailed("That range is too wide; request at most 400 days.")

    spec = service.calendar_spec(session, calendar, start=start, end=end)
    reasons = {
        row.exception_date: row.reason
        for row in session.scalars(
            select(CalendarException).where(
                CalendarException.calendar_id == calendar_id,
                CalendarException.exception_date >= start,
                CalendarException.exception_date <= end,
            )
        ).all()
    }

    results: list[DayTypeResponse] = []
    for day in date_range(start, end):
        day_type = resolve_day_type(day, spec)
        if day in spec.exceptions:
            source = "calendar_exception"
            reason = reasons.get(day)
        elif day in spec.holiday_dates:
            source, reason = "holiday", None
        elif day.weekday() in spec.weekly_off_days:
            source, reason = "weekly_off_pattern", None
        else:
            source, reason = "default", None
        results.append(
            DayTypeResponse(
                work_date=day,
                day_type=day_type,
                source=source,
                reason=reason,
            )
        )
    return results


# --------------------------------------------------------------- assignments


def _assignment_response(assignment) -> ShiftAssignmentResponse:
    return ShiftAssignmentResponse(
        id=assignment.id,
        employee_id=assignment.employee_id,
        shift_id=assignment.shift_id,
        shift_code=assignment.shift.code,
        shift_name=assignment.shift.name,
        effective_from=assignment.effective_from,
        effective_to=assignment.effective_to,
        work_calendar_id=assignment.work_calendar_id,
        notes=assignment.notes,
        is_current=assignment.covers(date.today()),
    )


@router.get(
    "/assignments/{employee_id}",
    response_model=list[ShiftAssignmentResponse],
    summary="List an employee's shift assignments",
)
def list_assignments(
    employee_id: int,
    session: DbSession,
    actor: Annotated[User, Depends(require("shift.read"))],
) -> list[ShiftAssignmentResponse]:
    """Scoped to employees the caller may see, so a manager cannot read the
    schedule of someone outside their team."""
    employees_service.assert_can_read(session, actor, employee_id)
    return [
        _assignment_response(assignment)
        for assignment in service.list_assignments(session, employee_id)
    ]


@router.post(
    "/assignments",
    response_model=ShiftAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a shift to an employee",
    responses={409: {"description": "The period overlaps an existing assignment"}},
)
def create_assignment(
    payload: ShiftAssignmentCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("shift.manage"))],
) -> ShiftAssignmentResponse:
    """Assignments are effective-dated and must not overlap.

    Two shifts covering one day would make that day's calculation ambiguous, so
    an overlap is refused here rather than resolved by a silent tie-break. Close
    the existing assignment first.
    """
    assignment = service.assign_shift(
        session,
        employee_id=payload.employee_id,
        shift_id=payload.shift_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        work_calendar_id=payload.work_calendar_id,
        notes=payload.notes,
        context=ctx,
    )
    return _assignment_response(assignment)


@router.post(
    "/assignments/{assignment_id}/close",
    response_model=ShiftAssignmentResponse,
    summary="End an open shift assignment",
)
def close_assignment(
    assignment_id: int,
    payload: ShiftAssignmentCloseRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("shift.manage"))],
) -> ShiftAssignmentResponse:
    """Sets the end date so a new assignment can begin the following day."""
    assignment = service.close_assignment(
        session, assignment_id, effective_to=payload.effective_to, context=ctx
    )
    return _assignment_response(assignment)
