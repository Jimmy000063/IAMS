"""Shifts, shift assignments, weekly-off patterns and work calendars.

Effective dating
----------------
``shift_assignments`` and ``calendar_exceptions`` are effective-dated, and
``attendance_days`` records which shift it actually used. That combination is
what makes a schedule change safe: moving an employee to a new shift from 1 July
does not alter how June was calculated, and a June figure can still be explained
by the shift that was in force then.

The brief is explicit about this - "a schedule change must not silently rewrite
historical attendance calculations" - and the recomputation service refuses to
touch days older than ``attendance.recompute_locked_after_days``.
"""

from __future__ import annotations

from datetime import date, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKey, TimestampMixin

#: Weekday keys used throughout. Monday-first, matching ``date.weekday()``.
WEEKDAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: What a calendar exception does to a date.
EXCEPTION_TYPES = ("non_working", "working")

ROUNDING_MODES = ("none", "nearest", "down", "up")


class Shift(Base, TimestampMixin):
    """A working-time pattern.

    ``crosses_midnight`` is derived, not supplied: it is true exactly when
    ``end_time <= start_time``. Storing it lets queries filter on it without
    recomputing, and the check constraint keeps it honest.

    All the thresholds default to NULL, meaning "use the company setting". A
    per-shift value overrides it. That way a night shift can have its own grace
    period without every shift having to restate the company default.
    """

    __tablename__ = "shifts"
    __table_args__ = (
        CheckConstraint(
            "crosses_midnight = (end_time <= start_time)", name="crosses_midnight_derived"
        ),
        CheckConstraint("break_minutes >= 0 AND break_minutes < 1440", name="break_in_range"),
        CheckConstraint(
            "grace_late_minutes IS NULL OR grace_late_minutes BETWEEN 0 AND 240",
            name="grace_late_in_range",
        ),
        CheckConstraint(
            "grace_early_exit_minutes IS NULL OR grace_early_exit_minutes BETWEEN 0 AND 240",
            name="grace_early_in_range",
        ),
        CheckConstraint(
            "min_minutes_full_day IS NULL OR min_minutes_full_day BETWEEN 1 AND 1440",
            name="full_day_in_range",
        ),
        CheckConstraint(
            "min_minutes_half_day IS NULL OR min_minutes_half_day BETWEEN 1 AND 1440",
            name="half_day_in_range",
        ),
        # A half day must not require more than a full day.
        CheckConstraint(
            "min_minutes_full_day IS NULL OR min_minutes_half_day IS NULL "
            "OR min_minutes_half_day <= min_minutes_full_day",
            name="half_day_not_above_full_day",
        ),
        CheckConstraint("code = upper(code)", name="code_upper"),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    crosses_midnight: Mapped[bool] = mapped_column(Boolean, nullable=False)
    """True for a night shift, e.g. 22:00-06:00. Derived from the two times."""

    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    """Scheduled unpaid break. Used as the fallback when no break punches exist."""

    #: NULL means "inherit the company setting of the same name".
    grace_late_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grace_early_exit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_minutes_full_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_minutes_half_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: How long before/after the scheduled window a punch still belongs to this
    #: shift instance. Needed to attribute an overnight shift's punches to the
    #: date the shift started rather than the calendar date of the punch.
    early_punch_window_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("120")
    )
    late_punch_window_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )

    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="RESTRICT"), nullable=True
    )
    """Optional. A location-specific shift; NULL means available everywhere."""

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    assignments: Mapped[list[ShiftAssignment]] = relationship(back_populates="shift")

    @property
    def scheduled_minutes(self) -> int:
        """Paid scheduled duration: the window length minus the planned break."""
        start = self.start_time.hour * 60 + self.start_time.minute
        end = self.end_time.hour * 60 + self.end_time.minute
        span = (end - start) if not self.crosses_midnight else (1440 - start + end)
        return max(span - self.break_minutes, 0)


class WeeklyOffPattern(Base, TimestampMixin):
    """Which weekdays are non-working.

    Stored as seven booleans rather than a JSON blob so that a query can ask
    "is Saturday an off day for this pattern" in SQL, which the attendance
    recomputation does for whole date ranges.
    """

    __tablename__ = "weekly_off_patterns"
    __table_args__ = (CheckConstraint("code = upper(code)", name="code_upper"),)

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    mon_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    tue_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    wed_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    thu_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    fri_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sat_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    sun_off: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    calendars: Mapped[list[WorkCalendar]] = relationship(back_populates="weekly_off_pattern")

    def is_weekly_off(self, day: date) -> bool:
        """Whether the given date falls on a non-working weekday."""
        return (
            self.mon_off,
            self.tue_off,
            self.wed_off,
            self.thu_off,
            self.fri_off,
            self.sat_off,
            self.sun_off,
        )[day.weekday()]

    @property
    def off_days(self) -> list[str]:
        return [
            key
            for key, off in zip(
                WEEKDAY_KEYS,
                (
                    self.mon_off,
                    self.tue_off,
                    self.wed_off,
                    self.thu_off,
                    self.fri_off,
                    self.sat_off,
                    self.sun_off,
                ),
                strict=True,
            )
            if off
        ]


class WorkCalendar(Base, TimestampMixin):
    """A weekly-off pattern plus date exceptions, optionally per location.

    This is the single source of "is this a working day" for both attendance and
    leave. Having one resolver, rather than a copy in each engine, is what stops
    the two drifting - a leave day that attendance thinks was a working day is a
    reconciliation problem nobody enjoys.

    Holiday calendars (Milestone 6) plug in as an additional source of
    non-working dates; the resolver already takes them as an argument.
    """

    __tablename__ = "work_calendars"
    __table_args__ = (
        CheckConstraint("code = upper(code)", name="code_upper"),
        # Exactly one company default, and at most one active calendar per
        # location. Without these, resolve_calendar_for() would have to pick
        # arbitrarily between two candidates, and the same employee could get a
        # different answer on different days.
        Index(
            "ix_work_calendars_single_default",
            text("(true)"),
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index(
            "ix_work_calendars_one_per_location",
            "work_location_id",
            unique=True,
            postgresql_where=text("work_location_id IS NOT NULL AND is_active"),
        ),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    weekly_off_pattern_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("weekly_off_patterns.id", ondelete="RESTRICT"), nullable=False
    )
    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="RESTRICT"), nullable=True
    )
    """NULL makes this the company-wide default calendar."""

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    weekly_off_pattern: Mapped[WeeklyOffPattern] = relationship(
        back_populates="calendars", lazy="joined"
    )
    exceptions: Mapped[list[CalendarException]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )


class CalendarException(Base, TimestampMixin):
    """A single date that departs from the weekly pattern.

    Two directions, both needed in practice: a one-off company closure on a
    working day, and a working Saturday to make up for one.
    """

    __tablename__ = "calendar_exceptions"
    __table_args__ = (
        UniqueConstraint("calendar_id", "exception_date"),
        CheckConstraint(f"exception_type IN {EXCEPTION_TYPES}", name="exception_type_known"),
        Index("ix_calendar_exceptions_calendar_id_exception_date", "calendar_id", "exception_date"),
    )

    id: Mapped[PrimaryKey]
    calendar_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("work_calendars.id", ondelete="CASCADE"), nullable=False
    )
    exception_date: Mapped[date] = mapped_column(Date, nullable=False)
    exception_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)

    calendar: Mapped[WorkCalendar] = relationship(back_populates="exceptions")


class ShiftAssignment(Base, TimestampMixin):
    """Which shift an employee works, over a date range.

    Overlapping assignments for one employee are prevented by an exclusion
    constraint in the migration - two shifts on the same day would make the
    attendance calculation ambiguous, and the database is the only place that
    can enforce it reliably under concurrency.
    """

    __tablename__ = "shift_assignments"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="period_ordered"
        ),
        Index(
            "ix_shift_assignments_employee_id_effective_from",
            "employee_id",
            "effective_from",
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    shift_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shifts.id", ondelete="RESTRICT"), nullable=False
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """NULL means open-ended - the current assignment."""

    work_calendar_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_calendars.id", ondelete="RESTRICT"), nullable=True
    )
    """Overrides the location's calendar for this employee and period."""

    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    shift: Mapped[Shift] = relationship(back_populates="assignments", lazy="joined")

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )
