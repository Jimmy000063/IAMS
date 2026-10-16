"""Holiday calendars, holidays and optional-holiday elections.

The important function here is :func:`holiday_dates_for`. It feeds
``CalendarSpec.holiday_dates``, which the day-type resolver has accepted since
Milestone 3 - so attendance, leave and comp-off all see the same holidays
through the one resolver, with no second implementation to drift.

Why the holiday set is per employee
-----------------------------------
Three reasons, each of them a real arrangement rather than a hypothetical:

* **Calendars are scoped.** A Bengaluru employee and a Kolkata employee do not
  get the same state holidays.
* **Applicable calendars union.** A regional list supplements the national one
  instead of replacing it.
* **Optional holidays are elected.** A restricted holiday is only non-working
  for the employee who chose it.

Nothing is seeded
-----------------
No holiday is created by this system. Dates vary by year, state and company, and
an invented one would put a wrong non-working day into attendance and payroll
for everybody on that calendar.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, PolicyViolation, ValidationFailed
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.employees.models import Employee
from app.modules.holidays.models import (
    ELECTABLE_TYPES,
    Holiday,
    HolidayCalendar,
    HolidayImportRun,
    OptionalHolidayElection,
)
from app.modules.notifications import hooks as notification_hooks
from app.modules.org.models import EmployeeGroupMember

logger = logging.getLogger(__name__)

MAX_IMPORT_ROWS = 400


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


def create_calendar(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> HolidayCalendar:
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise ValidationFailed(
            "A calendar code is required.",
            details=[{"field": "code", "message": "Required."}],
        )

    year = int(data["year"])
    existing = session.scalar(
        select(HolidayCalendar).where(HolidayCalendar.code == code, HolidayCalendar.year == year)
    )
    if existing is not None:
        raise Conflict(
            f"A calendar {code!r} already exists for {year}.",
            details=[{"field": "code", "message": "Already in use for that year."}],
        )

    calendar = HolidayCalendar(
        code=code,
        name=data["name"].strip(),
        year=year,
        work_location_id=data.get("work_location_id"),
        employee_group_id=data.get("employee_group_id"),
        description=data.get("description"),
        is_active=data.get("is_active", True),
        created_by_user_id=context.actor_user_id,
    )
    session.add(calendar)
    session.flush()

    audit.record(
        session,
        action="holiday.calendar_created",
        context=context,
        entity_type="holiday_calendar",
        entity_id=calendar.id,
        after={
            "code": calendar.code,
            "year": calendar.year,
            "scope": calendar.scope_label,
            "is_published": False,
        },
    )
    return calendar


def get_calendar(session: Session, calendar_id: int) -> HolidayCalendar:
    calendar = session.scalar(
        select(HolidayCalendar)
        .where(HolidayCalendar.id == calendar_id)
        .options(selectinload(HolidayCalendar.holidays))
    )
    if calendar is None:
        raise NotFound("Holiday calendar not found.")
    return calendar


def list_calendars(
    session: Session, *, year: int | None = None, include_inactive: bool = False
) -> list[HolidayCalendar]:
    statement = (
        select(HolidayCalendar)
        .options(selectinload(HolidayCalendar.holidays))
        .order_by(HolidayCalendar.year.desc(), HolidayCalendar.code)
    )
    if year is not None:
        statement = statement.where(HolidayCalendar.year == year)
    if not include_inactive:
        statement = statement.where(HolidayCalendar.is_active.is_(True))
    return list(session.scalars(statement).all())


def publish_calendar(
    session: Session, calendar_id: int, *, context: audit.AuditContext
) -> HolidayCalendar:
    """Make a calendar take effect.

    Publishing is deliberate and separate from editing, because it changes
    everybody's attendance on those dates at once. An unpublished calendar is a
    draft that affects nothing, so a list can be prepared and checked first.
    """
    calendar = get_calendar(session, calendar_id)

    if not calendar.holidays:
        raise PolicyViolation(
            "This calendar has no holidays on it, so publishing it would do "
            "nothing. Add the dates first."
        )
    if calendar.is_published:
        raise Conflict("This calendar is already published.")

    electable = [item for item in calendar.holidays if item.requires_election]

    calendar.is_published = True
    calendar.published_at = utc_now()
    calendar.updated_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="holiday.calendar_published",
        context=context,
        entity_type="holiday_calendar",
        entity_id=calendar.id,
        after={
            "code": calendar.code,
            "year": calendar.year,
            "holiday_count": len(calendar.holidays),
            "electable_count": len(electable),
        },
        extra={
            "note": (
                "Published calendars mark their dates as non-working for everyone "
                "in scope. Attendance already computed for those dates is not "
                "rewritten automatically; recompute the range if that is wanted."
            )
        },
    )
    notification_hooks.holiday_calendar_published(
        session,
        calendar_id=calendar.id,
        calendar_name=calendar.name,
        year=calendar.year,
        holiday_count=len(calendar.holidays),
        employee_ids=employees_in_scope(session, calendar),
    )
    return calendar


def employees_in_scope(session: Session, calendar: HolidayCalendar) -> list[int]:
    """The employees a calendar applies to.

    The same scoping rule the lookup uses, read the other way round: no scope
    means everyone, a location or a group narrows it, and both together narrow
    to employees who match both. Exited employees are left out - a holiday list
    is of no use to somebody who has left.
    """
    conditions = [Employee.employment_status != "exited"]
    if calendar.work_location_id is not None:
        conditions.append(Employee.work_location_id == calendar.work_location_id)
    if calendar.employee_group_id is not None:
        conditions.append(
            Employee.id.in_(
                select(EmployeeGroupMember.employee_id).where(
                    EmployeeGroupMember.group_id == calendar.employee_group_id
                )
            )
        )
    return list(session.scalars(select(Employee.id).where(*conditions)).all())


def unpublish_calendar(
    session: Session, calendar_id: int, *, reason: str, context: audit.AuditContext
) -> HolidayCalendar:
    """Withdraw a calendar that was published in error.

    Requires a reason, because withdrawing it turns non-working days back into
    working days for everyone in scope - which can turn a quiet day into an
    absence.
    """
    calendar = get_calendar(session, calendar_id)
    if not calendar.is_published:
        raise Conflict("This calendar is not published.")
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required to withdraw a published calendar.",
            details=[{"field": "reason", "message": "Required."}],
        )

    calendar.is_published = False
    calendar.published_at = None
    calendar.updated_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="holiday.calendar_unpublished",
        context=context,
        entity_type="holiday_calendar",
        entity_id=calendar.id,
        after={"reason": reason.strip()},
        extra={
            "note": (
                "Those dates become working days again for everyone in scope. "
                "Recompute attendance for the affected range."
            )
        },
    )
    return calendar


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------


def add_holiday(
    session: Session, calendar_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Holiday:
    calendar = get_calendar(session, calendar_id)
    holiday_date: date = data["holiday_date"]

    if holiday_date.year != calendar.year:
        raise ValidationFailed(
            f"{holiday_date.isoformat()} is not in {calendar.year}, which is the year "
            "this calendar covers. Add it to that year's calendar instead.",
            details=[{"field": "holiday_date", "message": "Outside the calendar's year."}],
        )

    existing = session.scalar(
        select(Holiday).where(
            Holiday.calendar_id == calendar_id, Holiday.holiday_date == holiday_date
        )
    )
    if existing is not None:
        raise Conflict(
            f"{calendar.code} already has an entry for {holiday_date.isoformat()} "
            f"({existing.name}).",
            details=[{"field": "holiday_date", "message": "Already on this calendar."}],
        )

    holiday = Holiday(
        calendar_id=calendar_id,
        holiday_date=holiday_date,
        name=data["name"].strip(),
        holiday_type=data.get("holiday_type", "public"),
        is_paid=data.get("is_paid", True),
        region=data.get("region"),
        notes=data.get("notes"),
        is_active=data.get("is_active", True),
    )
    session.add(holiday)
    session.flush()

    audit.record(
        session,
        action="holiday.added",
        context=context,
        entity_type="holiday_calendar",
        entity_id=calendar_id,
        after={
            "holiday_date": holiday_date.isoformat(),
            "name": holiday.name,
            "holiday_type": holiday.holiday_type,
            "calendar_published": calendar.is_published,
        },
    )
    return holiday


def remove_holiday(session: Session, holiday_id: int, *, context: audit.AuditContext) -> None:
    """Remove a holiday from a calendar.

    Refused once elections exist against it: an employee has already planned
    around that day, and deleting the row would leave their election pointing at
    nothing. Deactivate it instead, which keeps the record.
    """
    holiday = session.get(Holiday, holiday_id)
    if holiday is None:
        raise NotFound("Holiday not found.")

    elections = session.scalar(
        select(func.count())
        .select_from(OptionalHolidayElection)
        .where(
            OptionalHolidayElection.holiday_id == holiday_id,
            OptionalHolidayElection.status == "elected",
        )
    )
    if elections:
        raise Conflict(
            f"{elections} employee(s) have already chosen this holiday. Deactivate it "
            "rather than deleting it, so their election stays explicable."
        )

    audit.record(
        session,
        action="holiday.removed",
        context=context,
        entity_type="holiday_calendar",
        entity_id=holiday.calendar_id,
        before={
            "holiday_date": holiday.holiday_date.isoformat(),
            "name": holiday.name,
            "holiday_type": holiday.holiday_type,
        },
    )
    session.delete(holiday)
    session.flush()


def import_holidays(
    session: Session,
    calendar_id: int,
    *,
    rows: list[dict[str, Any]],
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Bulk-add a published holiday list.

    Keyed on the calendar and the content, so importing the same list twice does
    nothing the second time. A row that cannot be used is reported individually
    and the rest still import - one bad date should not force the whole list to
    be retyped.
    """
    calendar = get_calendar(session, calendar_id)

    if not rows:
        raise ValidationFailed(
            "The import contains no rows.",
            details=[{"field": "rows", "message": "Required."}],
        )
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValidationFailed(
            f"That is {len(rows)} rows; the maximum is {MAX_IMPORT_ROWS}. A year has "
            "nowhere near that many holidays, so this is probably the wrong file."
        )

    digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    key = f"holiday-import:{calendar_id}:{digest[:40]}"

    run = HolidayImportRun(
        calendar_id=calendar_id,
        idempotency_key=key,
        rows_received=len(rows),
        imported_by_user_id=context.actor_user_id,
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(HolidayImportRun).where(HolidayImportRun.idempotency_key == key)
        )
        return {
            "calendar_code": calendar.code,
            "rows_received": len(rows),
            "created": 0,
            "updated": 0,
            "rejected": 0,
            "already_imported": True,
            "problems": [
                {
                    "row": None,
                    "message": (
                        "This exact list was already imported"
                        + (f" at {existing.started_at.isoformat()}" if existing is not None else "")
                        + ". Nothing was added again."
                    ),
                }
            ],
        }

    created = 0
    updated = 0
    problems: list[dict[str, Any]] = []

    existing_by_date = {
        item.holiday_date: item
        for item in session.scalars(select(Holiday).where(Holiday.calendar_id == calendar_id)).all()
    }

    for index, row in enumerate(rows, start=1):
        raw_date = row.get("holiday_date")
        name = str(row.get("name") or "").strip()

        if not name:
            problems.append({"row": index, "message": "No holiday name."})
            continue

        try:
            holiday_date = (
                raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
            )
        except (TypeError, ValueError):
            problems.append(
                {"row": index, "message": f"Could not read {raw_date!r} as a date (YYYY-MM-DD)."}
            )
            continue

        if holiday_date.year != calendar.year:
            problems.append(
                {
                    "row": index,
                    "message": (
                        f"{holiday_date.isoformat()} is not in {calendar.year}, the year "
                        "this calendar covers."
                    ),
                }
            )
            continue

        holiday_type = str(row.get("holiday_type") or "public")
        if holiday_type not in ("public", "company", "optional", "restricted"):
            problems.append({"row": index, "message": f"Unknown holiday type {holiday_type!r}."})
            continue

        found = existing_by_date.get(holiday_date)
        if found is not None:
            found.name = name
            found.holiday_type = holiday_type
            if row.get("region") is not None:
                found.region = str(row["region"])
            updated += 1
        else:
            holiday = Holiday(
                calendar_id=calendar_id,
                holiday_date=holiday_date,
                name=name,
                holiday_type=holiday_type,
                region=str(row["region"]) if row.get("region") else None,
            )
            session.add(holiday)
            existing_by_date[holiday_date] = holiday
            created += 1

    run.rows_created = created
    run.rows_updated = updated
    run.rows_rejected = len(problems)
    run.problems = {"problems": problems[:50]}
    session.flush()

    audit.record(
        session,
        action="holiday.imported",
        context=context,
        entity_type="holiday_calendar",
        entity_id=calendar_id,
        extra={
            "calendar_code": calendar.code,
            "rows_received": len(rows),
            "created": created,
            "updated": updated,
            "rejected": len(problems),
        },
    )

    return {
        "calendar_code": calendar.code,
        "rows_received": len(rows),
        "created": created,
        "updated": updated,
        "rejected": len(problems),
        "already_imported": False,
        "problems": problems[:50],
    }


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def applicable_calendar_ids(session: Session, *, employee_id: int, years: set[int]) -> list[int]:
    """Which published calendars apply to one employee.

    Union, not precedence: a regional calendar adds its dates to the national
    one. A calendar with no scope applies to everyone; one with a location
    applies to employees there; one with a group applies to its members; one
    with both narrows to employees who match both.
    """
    if not years:
        return []

    employee = session.get(Employee, employee_id)
    if employee is None:
        return []

    group_ids = set(
        session.scalars(
            select(EmployeeGroupMember.group_id).where(
                EmployeeGroupMember.employee_id == employee_id
            )
        ).all()
    )

    candidates = session.scalars(
        select(HolidayCalendar).where(
            HolidayCalendar.year.in_(years),
            HolidayCalendar.is_active.is_(True),
            HolidayCalendar.is_published.is_(True),
        )
    ).all()

    applicable: list[int] = []
    for calendar in candidates:
        if (
            calendar.work_location_id is not None
            and calendar.work_location_id != employee.work_location_id
        ):
            continue
        if calendar.employee_group_id is not None and calendar.employee_group_id not in group_ids:
            continue
        applicable.append(calendar.id)
    return applicable


def holiday_dates_for(
    session: Session, *, employee_id: int, start: date, end: date
) -> frozenset[date]:
    """The dates that are holidays **for this employee** in a range.

    This is what ``CalendarSpec.holiday_dates`` is populated from, so attendance,
    leave and comp-off all agree without any of them implementing holiday logic.

    Automatic types (``public``, ``company``) are included for everyone in scope.
    Electable types (``optional``, ``restricted``) are included **only** if this
    employee elected them - that is what makes a restricted holiday a day off for
    one person and a working day for the next.
    """
    if end < start:
        return frozenset()

    years = {year for year in range(start.year, end.year + 1)}
    calendar_ids = applicable_calendar_ids(session, employee_id=employee_id, years=years)
    if not calendar_ids:
        return frozenset()

    rows = session.execute(
        select(Holiday.id, Holiday.holiday_date, Holiday.holiday_type).where(
            Holiday.calendar_id.in_(calendar_ids),
            Holiday.is_active.is_(True),
            Holiday.holiday_date >= start,
            Holiday.holiday_date <= end,
        )
    ).all()
    if not rows:
        return frozenset()

    automatic = {row[1] for row in rows if row[2] not in ELECTABLE_TYPES}
    electable_ids = [row[0] for row in rows if row[2] in ELECTABLE_TYPES]

    elected: set[date] = set()
    if electable_ids:
        elected = {
            row[0]
            for row in session.execute(
                select(Holiday.holiday_date)
                .join(
                    OptionalHolidayElection,
                    OptionalHolidayElection.holiday_id == Holiday.id,
                )
                .where(
                    Holiday.id.in_(electable_ids),
                    OptionalHolidayElection.employee_id == employee_id,
                    OptionalHolidayElection.status == "elected",
                )
            ).all()
        }

    return frozenset(automatic | elected)


# ---------------------------------------------------------------------------
# Optional holiday elections
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ElectionLimits:
    max_elections: int
    elected_count: int

    @property
    def remaining(self) -> int:
        return max(self.max_elections - self.elected_count, 0)


def election_limits(session: Session, *, employee_id: int, year: int) -> ElectionLimits:
    from app.modules.settings import service as settings_service

    maximum = int(settings_service.get_value(session, "holiday.max_optional_elections", default=2))
    count = (
        session.scalar(
            select(func.count())
            .select_from(OptionalHolidayElection)
            .join(Holiday, Holiday.id == OptionalHolidayElection.holiday_id)
            .join(HolidayCalendar, HolidayCalendar.id == Holiday.calendar_id)
            .where(
                OptionalHolidayElection.employee_id == employee_id,
                OptionalHolidayElection.status == "elected",
                HolidayCalendar.year == year,
            )
        )
        or 0
    )
    return ElectionLimits(max_elections=maximum, elected_count=count)


def elect_holiday(
    session: Session,
    *,
    employee_id: int,
    holiday_id: int,
    context: audit.AuditContext,
) -> OptionalHolidayElection:
    """Choose an optional or restricted holiday.

    Limited per year by ``holiday.max_optional_elections``: the whole point of a
    restricted holiday is that an employee picks a few from a longer list.
    """
    holiday = session.get(Holiday, holiday_id)
    if holiday is None:
        raise NotFound("Holiday not found.")
    if not holiday.requires_election:
        raise PolicyViolation(
            f"{holiday.name} is a {holiday.holiday_type} holiday, which applies "
            "automatically. There is nothing to choose."
        )

    calendar = session.get(HolidayCalendar, holiday.calendar_id)
    if calendar is None or not calendar.is_published or not calendar.is_active:
        raise PolicyViolation(
            "That holiday is on a calendar that is not published, so it cannot be chosen yet."
        )

    existing = session.scalar(
        select(OptionalHolidayElection).where(
            OptionalHolidayElection.employee_id == employee_id,
            OptionalHolidayElection.holiday_id == holiday_id,
        )
    )
    if existing is not None and existing.status == "elected":
        raise Conflict(f"You have already chosen {holiday.name}.")

    limits = election_limits(session, employee_id=employee_id, year=calendar.year)
    if limits.remaining <= 0:
        raise PolicyViolation(
            f"You have already chosen {limits.elected_count} of "
            f"{limits.max_elections} optional holidays for {calendar.year}. "
            "Withdraw one before choosing another.",
            details=[{"field": "holiday_id", "message": "No elections remaining."}],
        )

    if existing is not None:
        # Re-electing a withdrawn choice reuses the row, so the history of what
        # was chosen and when stays in one place.
        existing.status = "elected"
        existing.elected_at = utc_now()
        existing.withdrawn_at = None
        existing.elected_by_user_id = context.actor_user_id
        election = existing
    else:
        election = OptionalHolidayElection(
            employee_id=employee_id,
            holiday_id=holiday_id,
            status="elected",
            elected_at=utc_now(),
            elected_by_user_id=context.actor_user_id,
        )
        session.add(election)

    session.flush()

    audit.record(
        session,
        action="holiday.optional_elected",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "holiday": holiday.name,
            "holiday_date": holiday.holiday_date.isoformat(),
            "year": calendar.year,
            "elected_count": limits.elected_count + 1,
            "max_elections": limits.max_elections,
        },
    )
    return election


def withdraw_election(
    session: Session, *, employee_id: int, holiday_id: int, context: audit.AuditContext
) -> OptionalHolidayElection:
    """Give up a chosen optional holiday.

    The row is kept with a withdrawn status rather than deleted: attendance for
    that date may already have been computed as non-working, and the record of
    what was elected when is what explains it.
    """
    election = session.scalar(
        select(OptionalHolidayElection).where(
            OptionalHolidayElection.employee_id == employee_id,
            OptionalHolidayElection.holiday_id == holiday_id,
        )
    )
    if election is None or election.status != "elected":
        raise NotFound("You have not chosen that holiday.")

    holiday = session.get(Holiday, holiday_id)
    if holiday is not None and holiday.holiday_date < date.today():
        raise PolicyViolation(
            f"{holiday.name} has already passed, so the choice cannot be withdrawn. "
            "Ask HR to correct the attendance if it was recorded wrongly."
        )

    election.status = "withdrawn"
    election.withdrawn_at = utc_now()
    session.flush()

    audit.record(
        session,
        action="holiday.optional_withdrawn",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "holiday_id": holiday_id,
            "holiday": holiday.name if holiday else None,
        },
        extra={
            "note": (
                "That date becomes a working day again for this employee; "
                "attendance for it should be recomputed."
            )
        },
    )
    return election


def elections_for(
    session: Session, *, employee_id: int, year: int | None = None
) -> list[dict[str, Any]]:
    statement = (
        select(
            OptionalHolidayElection.id,
            OptionalHolidayElection.status,
            OptionalHolidayElection.elected_at,
            Holiday.id,
            Holiday.holiday_date,
            Holiday.name,
            Holiday.holiday_type,
            HolidayCalendar.year,
        )
        .join(Holiday, Holiday.id == OptionalHolidayElection.holiday_id)
        .join(HolidayCalendar, HolidayCalendar.id == Holiday.calendar_id)
        .where(OptionalHolidayElection.employee_id == employee_id)
        .order_by(Holiday.holiday_date)
    )
    if year is not None:
        statement = statement.where(HolidayCalendar.year == year)

    return [
        {
            "election_id": row[0],
            "status": row[1],
            "elected_at": row[2],
            "holiday_id": row[3],
            "holiday_date": row[4],
            "name": row[5],
            "holiday_type": row[6],
            "year": row[7],
        }
        for row in session.execute(statement).all()
    ]


def available_optional_holidays(
    session: Session, *, employee_id: int, year: int
) -> list[dict[str, Any]]:
    """Optional holidays this employee could still choose from.

    Scoped to the calendars that apply to them, so an employee is not offered
    another region's restricted holidays.
    """
    calendar_ids = applicable_calendar_ids(session, employee_id=employee_id, years={year})
    if not calendar_ids:
        return []

    elected_ids = set(
        session.scalars(
            select(OptionalHolidayElection.holiday_id).where(
                OptionalHolidayElection.employee_id == employee_id,
                OptionalHolidayElection.status == "elected",
            )
        ).all()
    )

    rows = session.scalars(
        select(Holiday)
        .where(
            Holiday.calendar_id.in_(calendar_ids),
            Holiday.is_active.is_(True),
            Holiday.holiday_type.in_(ELECTABLE_TYPES),
        )
        .order_by(Holiday.holiday_date)
    ).all()

    return [
        {
            "holiday_id": item.id,
            "holiday_date": item.holiday_date,
            "name": item.name,
            "holiday_type": item.holiday_type,
            "region": item.region,
            "is_elected": item.id in elected_ids,
            "has_passed": item.holiday_date < date.today(),
        }
        for item in rows
    ]


def calendar_for_employee(
    session: Session, *, employee_id: int, start: date, end: date
) -> list[dict[str, Any]]:
    """Every holiday affecting one employee in a range, with its effect.

    An electable holiday the employee has **not** chosen is still listed, marked
    as not applying - otherwise it would look as though it did not exist, and the
    employee would not know it was available to choose.
    """
    years = {year for year in range(start.year, end.year + 1)}
    calendar_ids = applicable_calendar_ids(session, employee_id=employee_id, years=years)
    if not calendar_ids:
        return []

    elected_ids = set(
        session.scalars(
            select(OptionalHolidayElection.holiday_id).where(
                OptionalHolidayElection.employee_id == employee_id,
                OptionalHolidayElection.status == "elected",
            )
        ).all()
    )

    rows = session.scalars(
        select(Holiday)
        .where(
            Holiday.calendar_id.in_(calendar_ids),
            Holiday.is_active.is_(True),
            Holiday.holiday_date >= start,
            Holiday.holiday_date <= end,
        )
        .order_by(Holiday.holiday_date)
    ).all()

    return [
        {
            "holiday_id": item.id,
            "holiday_date": item.holiday_date,
            "name": item.name,
            "holiday_type": item.holiday_type,
            "region": item.region,
            "is_paid": item.is_paid,
            "requires_election": item.requires_election,
            "is_elected": item.id in elected_ids,
            "applies": (not item.requires_election) or item.id in elected_ids,
        }
        for item in rows
    ]
