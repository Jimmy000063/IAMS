"""Shift, calendar and assignment management, plus the resolution used by attendance."""

from __future__ import annotations

from datetime import date, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PolicyViolation, ValidationFailed
from app.modules.audit import service as audit
from app.modules.employees.models import Employee
from app.modules.notifications import hooks as notification_hooks
from app.modules.org.models import WorkLocation
from app.modules.shifts.models import (
    CalendarException,
    Shift,
    ShiftAssignment,
    WeeklyOffPattern,
    WorkCalendar,
)
from app.modules.shifts.policy import (
    AssignmentWindow,
    CalendarSpec,
    overlaps,
    resolve_assignment,
)

WEEKDAY_COLUMNS = ("mon_off", "tue_off", "wed_off", "thu_off", "fri_off", "sat_off", "sun_off")


def _normalise_code(code: str) -> str:
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise ValidationFailed(
            "A code is required.", details=[{"field": "code", "message": "Required."}]
        )
    return cleaned


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


def _validate_shift_times(start: time, end: time) -> bool:
    """Return ``crosses_midnight``, rejecting a zero-length shift.

    A shift with identical start and end times would have a span of either zero
    or 24 hours depending on interpretation, so it is refused rather than
    guessed at.
    """
    if start == end:
        raise ValidationFailed(
            "A shift must have different start and end times.",
            details=[{"field": "end_time", "message": "Cannot equal the start time."}],
        )
    return end <= start


def create_shift(session: Session, *, data: dict[str, Any], context: audit.AuditContext) -> Shift:
    code = _normalise_code(data["code"])
    if session.scalar(select(func.count()).select_from(Shift).where(Shift.code == code)):
        raise Conflict(
            f"Shift code {code!r} is already in use.",
            details=[{"field": "code", "message": "Already in use."}],
        )

    crosses = _validate_shift_times(data["start_time"], data["end_time"])

    if data.get("work_location_id") is not None:
        location = session.get(WorkLocation, data["work_location_id"])
        if location is None:
            raise ValidationFailed(
                "The work location does not exist.",
                details=[{"field": "work_location_id", "message": "Unknown."}],
            )

    shift = Shift(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        start_time=data["start_time"],
        end_time=data["end_time"],
        crosses_midnight=crosses,
        break_minutes=data.get("break_minutes", 0),
        grace_late_minutes=data.get("grace_late_minutes"),
        grace_early_exit_minutes=data.get("grace_early_exit_minutes"),
        min_minutes_full_day=data.get("min_minutes_full_day"),
        min_minutes_half_day=data.get("min_minutes_half_day"),
        early_punch_window_minutes=data.get("early_punch_window_minutes", 120),
        late_punch_window_minutes=data.get("late_punch_window_minutes", 300),
        work_location_id=data.get("work_location_id"),
        is_active=data.get("is_active", True),
    )
    session.add(shift)
    session.flush()

    audit.record(
        session,
        action="shift.created",
        context=context,
        entity_type="shift",
        entity_id=shift.id,
        after={
            "code": shift.code,
            "name": shift.name,
            "start_time": shift.start_time.isoformat(),
            "end_time": shift.end_time.isoformat(),
            "crosses_midnight": shift.crosses_midnight,
            "scheduled_minutes": shift.scheduled_minutes,
        },
    )
    return shift


def update_shift(
    session: Session, shift_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Shift:
    shift = session.get(Shift, shift_id)
    if shift is None:
        raise NotFound("Shift not found.")

    before = {
        "start_time": shift.start_time.isoformat(),
        "end_time": shift.end_time.isoformat(),
        "break_minutes": shift.break_minutes,
        "grace_late_minutes": shift.grace_late_minutes,
        "min_minutes_full_day": shift.min_minutes_full_day,
        "is_active": shift.is_active,
    }

    if "code" in data:
        code = _normalise_code(data["code"])
        clash = session.scalar(
            select(func.count()).select_from(Shift).where(Shift.code == code, Shift.id != shift_id)
        )
        if clash:
            raise Conflict(f"Shift code {code!r} is already in use.")
        shift.code = code

    if "start_time" in data or "end_time" in data:
        start = data.get("start_time", shift.start_time)
        end = data.get("end_time", shift.end_time)
        shift.crosses_midnight = _validate_shift_times(start, end)
        shift.start_time = start
        shift.end_time = end

    for field in (
        "name",
        "description",
        "break_minutes",
        "grace_late_minutes",
        "grace_early_exit_minutes",
        "min_minutes_full_day",
        "min_minutes_half_day",
        "early_punch_window_minutes",
        "late_punch_window_minutes",
        "work_location_id",
        "is_active",
    ):
        if field in data:
            setattr(shift, field, data[field])

    session.flush()

    # Changing a shift alters how future attendance is computed. Past days keep
    # the figures and the policy snapshot they were calculated with; this entry
    # is the record of when the rules changed.
    audit.record(
        session,
        action="shift.updated",
        context=context,
        entity_type="shift",
        entity_id=shift.id,
        before=before,
        after={
            "start_time": shift.start_time.isoformat(),
            "end_time": shift.end_time.isoformat(),
            "break_minutes": shift.break_minutes,
            "grace_late_minutes": shift.grace_late_minutes,
            "min_minutes_full_day": shift.min_minutes_full_day,
            "is_active": shift.is_active,
        },
        extra={
            "note": (
                "Affects attendance computed from now on. Existing attendance_days "
                "rows retain the policy snapshot they were computed with."
            )
        },
    )
    return shift


def list_shifts(session: Session, *, include_inactive: bool = False) -> list[Shift]:
    statement = select(Shift).order_by(Shift.start_time, Shift.code)
    if not include_inactive:
        statement = statement.where(Shift.is_active.is_(True))
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Weekly-off patterns and calendars
# ---------------------------------------------------------------------------


def create_weekly_off_pattern(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> WeeklyOffPattern:
    code = _normalise_code(data["code"])
    if session.scalar(
        select(func.count()).select_from(WeeklyOffPattern).where(WeeklyOffPattern.code == code)
    ):
        raise Conflict(f"Pattern code {code!r} is already in use.")

    off_days = set(data.get("off_days") or [])
    from app.modules.shifts.models import WEEKDAY_KEYS

    unknown = off_days - set(WEEKDAY_KEYS)
    if unknown:
        raise ValidationFailed(
            f"Unknown weekday(s): {', '.join(sorted(unknown))}.",
            details=[{"field": "off_days", "message": f"Use: {', '.join(WEEKDAY_KEYS)}"}],
        )
    if len(off_days) == 7:
        raise PolicyViolation(
            "Every day cannot be a weekly off - there would be no working days.",
            details=[{"field": "off_days", "message": "Leave at least one working day."}],
        )

    pattern = WeeklyOffPattern(
        code=code,
        name=data["name"].strip(),
        **{
            column: key in off_days
            for column, key in zip(WEEKDAY_COLUMNS, WEEKDAY_KEYS, strict=True)
        },
        is_active=data.get("is_active", True),
    )
    session.add(pattern)
    session.flush()

    audit.record(
        session,
        action="calendar.weekly_off_pattern_created",
        context=context,
        entity_type="weekly_off_pattern",
        entity_id=pattern.id,
        after={"code": pattern.code, "off_days": pattern.off_days},
    )
    return pattern


def create_work_calendar(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> WorkCalendar:
    code = _normalise_code(data["code"])
    if session.scalar(
        select(func.count()).select_from(WorkCalendar).where(WorkCalendar.code == code)
    ):
        raise Conflict(f"Calendar code {code!r} is already in use.")

    pattern = session.get(WeeklyOffPattern, data["weekly_off_pattern_id"])
    if pattern is None:
        raise ValidationFailed(
            "The weekly-off pattern does not exist.",
            details=[{"field": "weekly_off_pattern_id", "message": "Unknown."}],
        )

    making_default = data.get("is_default", False)
    if making_default:
        # Exactly one default; a second would make resolution ambiguous.
        for existing in session.scalars(
            select(WorkCalendar).where(WorkCalendar.is_default.is_(True))
        ).all():
            existing.is_default = False

    calendar = WorkCalendar(
        code=code,
        name=data["name"].strip(),
        weekly_off_pattern_id=pattern.id,
        work_location_id=data.get("work_location_id"),
        is_default=making_default,
        is_active=data.get("is_active", True),
    )
    session.add(calendar)
    session.flush()

    audit.record(
        session,
        action="calendar.created",
        context=context,
        entity_type="work_calendar",
        entity_id=calendar.id,
        after={
            "code": calendar.code,
            "weekly_off_pattern": pattern.code,
            "work_location_id": calendar.work_location_id,
            "is_default": calendar.is_default,
        },
    )
    return calendar


def add_calendar_exception(
    session: Session,
    calendar_id: int,
    *,
    data: dict[str, Any],
    context: audit.AuditContext,
) -> CalendarException:
    calendar = session.get(WorkCalendar, calendar_id)
    if calendar is None:
        raise NotFound("Work calendar not found.")

    exception_date: date = data["exception_date"]
    existing = session.scalar(
        select(CalendarException).where(
            CalendarException.calendar_id == calendar_id,
            CalendarException.exception_date == exception_date,
        )
    )
    if existing is not None:
        raise Conflict(
            f"{calendar.name} already has an exception on {exception_date.isoformat()}.",
            details=[{"field": "exception_date", "message": "Already defined."}],
        )

    entry = CalendarException(
        calendar_id=calendar_id,
        exception_date=exception_date,
        exception_type=data["exception_type"],
        reason=data["reason"].strip(),
    )
    session.add(entry)
    session.flush()

    audit.record(
        session,
        action="calendar.exception_added",
        context=context,
        entity_type="work_calendar",
        entity_id=calendar_id,
        after={
            "date": exception_date.isoformat(),
            "type": entry.exception_type,
            "reason": entry.reason,
        },
    )
    return entry


def list_calendars(session: Session) -> list[WorkCalendar]:
    return list(
        session.scalars(
            select(WorkCalendar).where(WorkCalendar.is_active.is_(True)).order_by(WorkCalendar.code)
        ).all()
    )


def list_patterns(session: Session) -> list[WeeklyOffPattern]:
    return list(session.scalars(select(WeeklyOffPattern).order_by(WeeklyOffPattern.code)).all())


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


def assign_shift(
    session: Session,
    *,
    employee_id: int,
    shift_id: int,
    effective_from: date,
    effective_to: date | None,
    work_calendar_id: int | None,
    notes: str | None,
    context: audit.AuditContext,
) -> ShiftAssignment:
    """Assign a shift to an employee for a date range.

    Overlaps are refused with a readable message. Two shifts covering the same
    day would make the day's calculation ambiguous - and since the engine has to
    pick one, the right place to resolve it is here, not by a silent tie-break.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")

    shift = session.get(Shift, shift_id)
    if shift is None:
        raise ValidationFailed(
            "The shift does not exist.",
            details=[{"field": "shift_id", "message": "Unknown."}],
        )
    if not shift.is_active:
        raise ValidationFailed(
            f"Shift {shift.code} is inactive.",
            details=[{"field": "shift_id", "message": "Inactive."}],
        )

    if effective_to is not None and effective_to < effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    if effective_from < employee.date_of_joining:
        raise PolicyViolation(
            f"{employee.employee_code} joined on "
            f"{employee.date_of_joining.isoformat()}; a shift cannot start before that.",
            details=[{"field": "effective_from", "message": "Before the joining date."}],
        )

    existing = list(
        session.scalars(
            select(ShiftAssignment).where(ShiftAssignment.employee_id == employee_id)
        ).all()
    )
    for other in existing:
        if overlaps(effective_from, effective_to, other.effective_from, other.effective_to):
            raise Conflict(
                f"That period overlaps an existing assignment to shift "
                f"{other.shift.code} from {other.effective_from.isoformat()} to "
                f"{other.effective_to.isoformat() if other.effective_to else 'open-ended'}. "
                "Close that assignment first.",
                details=[
                    {"field": "effective_from", "message": "Overlaps an existing assignment."}
                ],
            )

    if work_calendar_id is not None and session.get(WorkCalendar, work_calendar_id) is None:
        raise ValidationFailed(
            "The work calendar does not exist.",
            details=[{"field": "work_calendar_id", "message": "Unknown."}],
        )

    assignment = ShiftAssignment(
        employee_id=employee_id,
        shift_id=shift_id,
        effective_from=effective_from,
        effective_to=effective_to,
        work_calendar_id=work_calendar_id,
        assigned_by_user_id=context.actor_user_id,
        notes=notes,
    )
    session.add(assignment)
    session.flush()

    audit.record(
        session,
        action="shift.assigned",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "shift_code": shift.code,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "work_calendar_id": work_calendar_id,
        },
    )
    # Late and early-exit marking follow the new shift, so the employee is told
    # rather than finding out from the next day's attendance.
    notification_hooks.shift_assigned(
        session,
        assignment_id=assignment.id,
        employee_id=employee_id,
        shift_name=shift.name,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    return assignment


def close_assignment(
    session: Session,
    assignment_id: int,
    *,
    effective_to: date,
    context: audit.AuditContext,
) -> ShiftAssignment:
    """End an open assignment, so a new one can begin the next day."""
    assignment = session.get(ShiftAssignment, assignment_id)
    if assignment is None:
        raise NotFound("Shift assignment not found.")
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
        action="shift.assignment_closed",
        context=context,
        entity_type="employee",
        entity_id=assignment.employee_id,
        before={"effective_to": before.isoformat() if before else None},
        after={"effective_to": effective_to.isoformat()},
    )
    return assignment


def list_assignments(session: Session, employee_id: int) -> list[ShiftAssignment]:
    return list(
        session.scalars(
            select(ShiftAssignment)
            .where(ShiftAssignment.employee_id == employee_id)
            .order_by(ShiftAssignment.effective_from.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# Resolution - the seam the attendance engine calls through
# ---------------------------------------------------------------------------


def assignment_windows(session: Session, employee_id: int) -> list[AssignmentWindow]:
    rows = session.execute(
        select(
            ShiftAssignment.id,
            ShiftAssignment.shift_id,
            ShiftAssignment.effective_from,
            ShiftAssignment.effective_to,
            ShiftAssignment.work_calendar_id,
        ).where(ShiftAssignment.employee_id == employee_id)
    ).all()
    return [
        AssignmentWindow(
            assignment_id=row[0],
            shift_id=row[1],
            effective_from=row[2],
            effective_to=row[3],
            work_calendar_id=row[4],
        )
        for row in rows
    ]


def resolve_shift_for(
    session: Session, employee_id: int, day: date
) -> tuple[Shift | None, int | None]:
    """The shift and calendar override in force for an employee on a date.

    Returns ``(None, None)`` when nothing is assigned. The caller decides what
    that means - the attendance service falls back to a default shift built
    from company settings, so an employee with no assignment still gets a
    coherent (and clearly flagged) calculation rather than an error.
    """
    window = resolve_assignment(assignment_windows(session, employee_id), day)
    if window is None:
        return None, None
    return session.get(Shift, window.shift_id), window.work_calendar_id


def resolve_calendar_for(
    session: Session, employee_id: int, *, calendar_override_id: int | None = None
) -> WorkCalendar | None:
    """The calendar that governs an employee's working days.

    Precedence: the shift assignment's override, then the employee's work
    location's calendar, then the company default.
    """
    if calendar_override_id is not None:
        calendar = session.get(WorkCalendar, calendar_override_id)
        if calendar is not None:
            return calendar

    location_id = session.scalar(
        select(Employee.work_location_id).where(Employee.id == employee_id)
    )
    if location_id is not None:
        calendar = session.scalar(
            select(WorkCalendar).where(
                WorkCalendar.work_location_id == location_id,
                WorkCalendar.is_active.is_(True),
            )
        )
        if calendar is not None:
            return calendar

    return session.scalar(
        select(WorkCalendar).where(
            WorkCalendar.is_default.is_(True), WorkCalendar.is_active.is_(True)
        )
    )


def calendar_spec(
    session: Session,
    calendar: WorkCalendar | None,
    *,
    start: date,
    end: date,
    employee_id: int | None = None,
) -> CalendarSpec:
    """Flatten a calendar into the pure spec the resolver uses.

    When no calendar exists at all, the company ``company.working_week`` setting
    supplies the weekly offs. That keeps a fresh installation working - the
    brief confirms Monday to Friday - without requiring calendar setup first.

    ``employee_id`` populates ``holiday_dates`` from the published holiday
    calendars that apply to that employee, including the optional holidays they
    personally elected. It is optional because some callers genuinely have no
    employee - the calendar preview screen shows a calendar's own shape - and
    those get weekly offs and exceptions only.

    Passing an employee is what makes attendance, leave and comp-off agree about
    holidays: they all arrive at the same day type through this one function and
    the one resolver, with no module implementing holiday logic of its own.
    """
    from app.modules.settings import service as settings_service

    if calendar is None:
        working_week = settings_service.get_value(
            session, "company.working_week", default=["mon", "tue", "wed", "thu", "fri"]
        )
        from app.modules.shifts.models import WEEKDAY_KEYS

        working = {index for index, key in enumerate(WEEKDAY_KEYS) if key in set(working_week)}
        return CalendarSpec(
            weekly_off_days=frozenset(set(range(7)) - working),
            exceptions={},
            holiday_dates=_holiday_dates(session, employee_id, start, end),
        )

    pattern = calendar.weekly_off_pattern
    from app.modules.shifts.models import WEEKDAY_KEYS

    off = {index for index, key in enumerate(WEEKDAY_KEYS) if key in set(pattern.off_days)}

    exceptions = {
        row.exception_date: row.exception_type
        for row in session.scalars(
            select(CalendarException).where(
                CalendarException.calendar_id == calendar.id,
                CalendarException.exception_date >= start,
                CalendarException.exception_date <= end,
            )
        ).all()
    }

    return CalendarSpec(
        weekly_off_days=frozenset(off),
        exceptions=exceptions,
        holiday_dates=_holiday_dates(session, employee_id, start, end),
    )


def _holiday_dates(
    session: Session, employee_id: int | None, start: date, end: date
) -> frozenset[date]:
    """Published holidays applying to one employee, or none without an employee.

    Imported inside the function because the holidays module reads employee and
    group records; a module-level import would make the dependency circular.
    """
    if employee_id is None:
        return frozenset()
    from app.modules.holidays import service as holidays_service

    return holidays_service.holiday_dates_for(
        session, employee_id=employee_id, start=start, end=end
    )


def employee_timezone(session: Session, employee_id: int) -> str:
    """The IANA zone governing this employee's attendance dates."""
    name = session.scalar(
        select(WorkLocation.timezone)
        .join(Employee, Employee.work_location_id == WorkLocation.id)
        .where(Employee.id == employee_id)
    )
    return name or get_settings().default_timezone
