"""Pure shift and calendar resolution. No I/O.

This module answers two questions that both the attendance engine and (from
Milestone 5) the leave engine need:

* **Which shift applies to this employee on this date?**
* **What kind of day is it - working, weekly off, or holiday?**

Having one implementation of the second question is the point. A leave day that
attendance believes was a working day, or a holiday one engine honours and the
other does not, produces reconciliation work that nobody can resolve from the
data. The brief requires the rules to be shared, so they live here and both
engines call in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

DayType = Literal["working", "weekly_off", "holiday"]


@dataclass(frozen=True, slots=True)
class AssignmentWindow:
    """An effective-dated shift assignment, reduced to what resolution needs."""

    assignment_id: int
    shift_id: int
    effective_from: date
    effective_to: date | None
    work_calendar_id: int | None = None

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


def resolve_assignment(
    assignments: Iterable[AssignmentWindow], day: date
) -> AssignmentWindow | None:
    """The assignment in force on ``day``.

    If more than one covers the date - which the database's exclusion constraint
    is there to prevent, but which historical data may still contain - the one
    with the latest ``effective_from`` wins, since it is the most recent
    decision. Ties break on the highest id, so the result is deterministic
    rather than dependent on row order.
    """
    covering = [window for window in assignments if window.covers(day)]
    if not covering:
        return None
    return max(covering, key=lambda window: (window.effective_from, window.assignment_id))


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    """A weekly-off pattern plus its date exceptions, as flat data.

    ``holiday_dates`` is supplied by the caller. In Milestone 3 it comes from
    calendar exceptions alone; Milestone 6 adds the holiday calendars and passes
    them in here, with no change to this function.
    """

    weekly_off_days: frozenset[int]
    """Weekday numbers that are non-working, ``date.weekday()`` style (Mon=0)."""

    exceptions: Mapping[date, str]
    """Date to ``'working'`` or ``'non_working'``."""

    holiday_dates: frozenset[date] = frozenset()


def resolve_day_type(day: date, calendar: CalendarSpec) -> DayType:
    """Classify a date.

    Precedence, highest first:

    1. **A ``working`` exception** makes the date a working day even if it is a
       Saturday or a holiday. This is the "working Saturday to make up for the
       long weekend" case, and it has to beat everything or it cannot express
       that.
    2. **A ``non_working`` exception** makes it a holiday - an unplanned closure.
    3. **A holiday calendar entry.**
    4. **The weekly-off pattern.**
    5. Otherwise it is a working day.

    A ``non_working`` exception reports ``holiday`` rather than ``weekly_off``
    because downstream the distinction matters: comp-off eligibility and leave
    deduction treat a declared closure as a holiday.
    """
    exception = calendar.exceptions.get(day)
    if exception == "working":
        return "working"
    if exception == "non_working":
        return "holiday"

    if day in calendar.holiday_dates:
        return "holiday"

    if day.weekday() in calendar.weekly_off_days:
        return "weekly_off"

    return "working"


def working_days_between(start: date, end: date, calendar: CalendarSpec) -> list[date]:
    """Every working day in an inclusive range.

    Used by payroll's ``working_days`` payable-days basis and by leave day
    counting, so both agree with attendance on which days were working days.
    """
    if end < start:
        return []
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if resolve_day_type(cursor, calendar) == "working":
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def date_range(start: date, end: date) -> list[date]:
    """Inclusive list of dates. Empty when ``end`` precedes ``start``."""
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def overlaps(
    a_from: date,
    a_to: date | None,
    b_from: date,
    b_to: date | None,
) -> bool:
    """Whether two half-open-ended date ranges intersect.

    ``None`` means open-ended. Used to reject an overlapping shift assignment
    with a readable message before the database constraint produces an opaque
    one.
    """
    # Stated as "neither ends before the other starts", which keeps the
    # symmetry of the relation visible rather than hiding it in early returns.
    a_ends_before_b_starts = a_to is not None and a_to < b_from
    b_ends_before_a_starts = b_to is not None and b_to < a_from
    return not (a_ends_before_b_starts or b_ends_before_a_starts)
