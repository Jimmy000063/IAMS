"""Holiday calendar endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import NotFound, ValidationFailed
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.holidays import service
from app.modules.holidays.schemas import (
    CalendarCreate,
    CalendarResponse,
    ElectionLimitsResponse,
    ElectionRequest,
    ElectionResponse,
    EmployeeHolidayEntry,
    HolidayCreate,
    HolidayResponse,
    ImportRequest,
    ImportResponse,
    OptionalHolidayOption,
    UnpublishRequest,
)

router = APIRouter(prefix="/holidays", tags=["Holidays"])

MAX_RANGE_DAYS = 800


def _calendar_response(calendar) -> CalendarResponse:  # noqa: ANN001
    return CalendarResponse(
        id=calendar.id,
        code=calendar.code,
        name=calendar.name,
        year=calendar.year,
        work_location_id=calendar.work_location_id,
        employee_group_id=calendar.employee_group_id,
        description=calendar.description,
        is_active=calendar.is_active,
        is_published=calendar.is_published,
        published_at=calendar.published_at,
        holiday_count=len(calendar.holidays),
        electable_count=sum(1 for item in calendar.holidays if item.requires_election),
    )


# -------------------------------------------------------------------- calendars


@router.get("/calendars", response_model=list[CalendarResponse], summary="List holiday calendars")
def list_calendars(
    session: DbSession,
    _user: Annotated[User, Depends(require("holiday.read"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[CalendarResponse]:
    return [
        _calendar_response(item)
        for item in service.list_calendars(session, year=year, include_inactive=include_inactive)
    ]


@router.post(
    "/calendars",
    response_model=CalendarResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a holiday calendar",
)
def create_calendar(
    payload: CalendarCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> CalendarResponse:
    """Create a year's holiday list, optionally scoped to a location or group.

    No holidays are seeded by this system. Dates vary by year, state and
    company, and an invented one would mark a wrong non-working day in
    attendance and payroll for everybody on the calendar.
    """
    calendar = service.create_calendar(
        session, data=payload.model_dump(exclude_unset=True), context=context
    )
    return _calendar_response(calendar)


@router.get(
    "/calendars/{calendar_id}",
    response_model=CalendarResponse,
    summary="Read a holiday calendar",
)
def read_calendar(
    calendar_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("holiday.read"))],
) -> CalendarResponse:
    return _calendar_response(service.get_calendar(session, calendar_id))


@router.get(
    "/calendars/{calendar_id}/holidays",
    response_model=list[HolidayResponse],
    summary="The holidays on a calendar",
)
def list_holidays(
    calendar_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("holiday.read"))],
) -> list[HolidayResponse]:
    calendar = service.get_calendar(session, calendar_id)
    return [HolidayResponse.model_validate(item) for item in calendar.holidays]


@router.post(
    "/calendars/{calendar_id}/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a holiday",
)
def add_holiday(
    calendar_id: int,
    payload: HolidayCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> HolidayResponse:
    holiday = service.add_holiday(
        session, calendar_id, data=payload.model_dump(exclude_unset=True), context=context
    )
    return HolidayResponse.model_validate(holiday)


@router.delete(
    "/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a holiday",
)
def remove_holiday(
    holiday_id: int,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> None:
    """Refused once employees have elected it.

    They have already planned around that day, and deleting the row would leave
    their election pointing at nothing. Deactivate it instead.
    """
    service.remove_holiday(session, holiday_id, context=context)


@router.post(
    "/calendars/{calendar_id}/import",
    response_model=ImportResponse,
    summary="Bulk-add a published holiday list",
)
def import_holidays(
    calendar_id: int,
    payload: ImportRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> ImportResponse:
    """Import a year's list in one go.

    Keyed on the content, so importing the same list twice does nothing the
    second time. A row that cannot be used is reported individually and the rest
    still import - one bad date should not force the whole list to be retyped.
    """
    return ImportResponse(
        **service.import_holidays(
            session,
            calendar_id,
            rows=[row.model_dump() for row in payload.rows],
            context=context,
        )
    )


@router.post(
    "/calendars/{calendar_id}/publish",
    response_model=CalendarResponse,
    summary="Publish a holiday calendar",
)
def publish_calendar(
    calendar_id: int,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> CalendarResponse:
    """Make the calendar take effect.

    Separate from editing on purpose: publishing marks those dates as
    non-working for everyone in scope at once, so a draft can be prepared and
    checked first. Attendance already computed for those dates is not rewritten
    automatically - recompute the range if that is wanted.
    """
    calendar = service.publish_calendar(session, calendar_id, context=context)
    return _calendar_response(calendar)


@router.post(
    "/calendars/{calendar_id}/unpublish",
    response_model=CalendarResponse,
    summary="Withdraw a published calendar",
)
def unpublish_calendar(
    calendar_id: int,
    payload: UnpublishRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("holiday.manage"))],
) -> CalendarResponse:
    """Needs a reason: those dates become working days again for everyone."""
    calendar = service.unpublish_calendar(
        session, calendar_id, reason=payload.reason, context=context
    )
    return _calendar_response(calendar)


# ------------------------------------------------------------------ employee view


@router.get(
    "/me",
    response_model=list[EmployeeHolidayEntry],
    summary="Your holidays",
)
def my_holidays(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("holiday.read"))],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[EmployeeHolidayEntry]:
    """Holidays from every published calendar that applies to you.

    An optional holiday you have not chosen is listed with ``applies`` false,
    rather than hidden - otherwise you would not know it was available.
    """
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    today = date.today()
    range_start = start or date(today.year, 1, 1)
    range_end = end or date(today.year, 12, 31)
    _validate_range(range_start, range_end)

    return [
        EmployeeHolidayEntry(**row)
        for row in service.calendar_for_employee(
            session, employee_id=employee.id, start=range_start, end=range_end
        )
    ]


@router.get(
    "/employees/{employee_id}",
    response_model=list[EmployeeHolidayEntry],
    summary="One employee's holidays",
)
def employee_holidays(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("employee.read", "employee.read_team"))],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[EmployeeHolidayEntry]:
    """Scoped to employees the caller may see; 404 outside that scope."""
    if not user.has_permission("employee.read"):
        team = (
            employees_service.team_employee_ids(session, user.employee_id)
            if user.employee_id
            else set()
        )
        if employee_id != user.employee_id and employee_id not in team:
            raise NotFound("Employee not found.")

    today = date.today()
    range_start = start or date(today.year, 1, 1)
    range_end = end or date(today.year, 12, 31)
    _validate_range(range_start, range_end)

    return [
        EmployeeHolidayEntry(**row)
        for row in service.calendar_for_employee(
            session, employee_id=employee_id, start=range_start, end=range_end
        )
    ]


# -------------------------------------------------------------------- elections


@router.get(
    "/me/optional",
    response_model=list[OptionalHolidayOption],
    summary="Optional holidays you can choose from",
)
def my_optional_holidays(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("holiday.elect_optional"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[OptionalHolidayOption]:
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    return [
        OptionalHolidayOption(**row)
        for row in service.available_optional_holidays(
            session, employee_id=employee.id, year=year or date.today().year
        )
    ]


@router.get(
    "/me/optional/limits",
    response_model=ElectionLimitsResponse,
    summary="How many optional holidays you may still choose",
)
def my_election_limits(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("holiday.elect_optional"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> ElectionLimitsResponse:
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    target = year or date.today().year
    limits = service.election_limits(session, employee_id=employee.id, year=target)
    return ElectionLimitsResponse(
        year=target,
        max_elections=limits.max_elections,
        elected_count=limits.elected_count,
        remaining=limits.remaining,
    )


@router.get(
    "/me/elections",
    response_model=list[ElectionResponse],
    summary="The optional holidays you have chosen",
)
def my_elections(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("holiday.elect_optional"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[ElectionResponse]:
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    return [
        ElectionResponse(**row)
        for row in service.elections_for(session, employee_id=employee.id, year=year)
    ]


@router.post(
    "/me/elections",
    response_model=ElectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Choose an optional holiday",
)
def elect_holiday(
    payload: ElectionRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("holiday.elect_optional"))],
) -> ElectionResponse:
    """Limited per year, because that is what a restricted holiday means.

    The chosen day becomes non-working **for you only**, and attendance for it
    resolves through the same day-type resolver everything else uses.
    """
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    service.elect_holiday(
        session, employee_id=employee.id, holiday_id=payload.holiday_id, context=context
    )
    rows = service.elections_for(session, employee_id=employee.id)
    chosen = next(row for row in rows if row["holiday_id"] == payload.holiday_id)
    return ElectionResponse(**chosen)


@router.delete(
    "/me/elections/{holiday_id}",
    response_model=ElectionResponse,
    summary="Give up a chosen optional holiday",
)
def withdraw_election(
    holiday_id: int,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("holiday.elect_optional"))],
) -> ElectionResponse:
    """The record is kept with a withdrawn status rather than deleted.

    Attendance for that date may already have been computed as non-working, and
    what was elected when is what explains it.
    """
    from app.modules.leave import service as leave_service

    employee = leave_service.employee_for_user(session, user)
    service.withdraw_election(
        session, employee_id=employee.id, holiday_id=holiday_id, context=context
    )
    rows = service.elections_for(session, employee_id=employee.id)
    row = next(item for item in rows if item["holiday_id"] == holiday_id)
    return ElectionResponse(**row)


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
