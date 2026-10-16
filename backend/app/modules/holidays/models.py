"""Holiday calendars, holidays and optional-holiday elections.

Why calendars are scoped rather than global
-------------------------------------------
India has national holidays, state holidays and company-declared closures, and
an employee in Bengaluru does not get the same list as one in Kolkata. A single
global holiday list cannot express that, so a calendar may be scoped to a work
location or an employee group, and the applicable calendars are **unioned** for
each employee: a regional calendar adds to the national one rather than
replacing it.

Optional and restricted holidays
--------------------------------
Some holidays are not automatic. The employee picks a limited number from a
published list - the "restricted holiday" convention - and only the ones they
elected are non-working days *for them*. That makes the holiday set genuinely
per-employee, which is why :func:`app.modules.holidays.service.holiday_dates_for`
takes an employee and not just a date range.

Nothing is seeded
-----------------
No holiday is created by this system. Dates vary by year, state and company, and
inventing them would put wrong non-working days into attendance and payroll.
HR publishes each year's list.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ActorMixin, Base, Jsonb, PrimaryKey, TimestampMixin

#: Kinds of holiday.
#:
#: ``public`` and ``company`` are automatic non-working days. ``optional`` and
#: ``restricted`` are only non-working for an employee who elected them - the
#: distinction between those two is naming convention rather than behaviour, so
#: both are kept and both require an election.
HOLIDAY_TYPES = ("public", "company", "optional", "restricted")

#: Holiday types an employee must choose before the day becomes non-working.
ELECTABLE_TYPES = ("optional", "restricted")

ELECTION_STATUSES = ("elected", "withdrawn")


class HolidayCalendar(Base, TimestampMixin, ActorMixin):
    """One published holiday list for a year, optionally scoped.

    A calendar with no location and no group applies to everyone; one with a
    location applies to employees there; one with a group applies to its
    members. All applicable calendars are unioned, so a regional list
    supplements the national one.
    """

    __tablename__ = "holiday_calendars"
    __table_args__ = (
        CheckConstraint("code = upper(code)", name="code_upper"),
        CheckConstraint("year >= 2000 AND year <= 2100", name="year_in_range"),
        UniqueConstraint("code", "year"),
        Index("ix_holiday_calendars_year_active", "year", "is_active"),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    """
    The calendar year the list covers. Calendar years, not leave years: a
    published holiday list is "the 2027 holidays", and a leave year starting in
    April would otherwise make the list span two of them confusingly.
    """

    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="CASCADE"), nullable=True
    )
    employee_group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee_groups.id", ondelete="CASCADE"), nullable=True
    )
    """
    Scope. Both null means everyone. Set one for a regional or group-specific
    list; setting both narrows to employees who are in the group *and* at the
    location.
    """

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    """
    An unpublished calendar does not affect attendance, leave or payroll. It
    exists so a draft list can be prepared and checked before it starts marking
    days as non-working - publishing is a deliberate act with a visible effect
    on everybody's attendance.
    """

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    holidays: Mapped[list[Holiday]] = relationship(
        back_populates="calendar",
        cascade="all, delete-orphan",
        order_by="Holiday.holiday_date",
    )

    @property
    def scope_label(self) -> str:
        if self.work_location_id is None and self.employee_group_id is None:
            return "everyone"
        parts = []
        if self.work_location_id is not None:
            parts.append(f"location {self.work_location_id}")
        if self.employee_group_id is not None:
            parts.append(f"group {self.employee_group_id}")
        return " and ".join(parts)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<HolidayCalendar {self.code} {self.year}>"


class Holiday(Base, TimestampMixin):
    """One holiday on a calendar."""

    __tablename__ = "holidays"
    __table_args__ = (
        CheckConstraint(f"holiday_type IN {HOLIDAY_TYPES}", name="holiday_type_known"),
        # One entry per date per calendar. Two rows for the same date would make
        # "is this an optional holiday" ambiguous.
        UniqueConstraint("calendar_id", "holiday_date"),
        Index("ix_holidays_date", "holiday_date"),
        Index("ix_holidays_calendar_type", "calendar_id", "holiday_type"),
    )

    id: Mapped[PrimaryKey]
    calendar_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("holiday_calendars.id", ondelete="CASCADE"), nullable=False
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    holiday_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'public'")
    )

    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """
    Whether the day is paid. Nearly always true; the flag exists because an
    unpaid declared closure is a real arrangement and payroll must not assume
    otherwise.
    """

    region: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """Free text, for a state or region name shown alongside the holiday."""

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    calendar: Mapped[HolidayCalendar] = relationship(back_populates="holidays")

    @property
    def requires_election(self) -> bool:
        return self.holiday_type in ELECTABLE_TYPES

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Holiday {self.holiday_date} {self.name!r}>"


class OptionalHolidayElection(Base, TimestampMixin):
    """An employee's choice of an optional or restricted holiday.

    Kept as a row per choice rather than a count, so "which days did they pick"
    is answerable - the day only becomes non-working for the employee who chose
    it, and attendance for that date depends on this record.

    A withdrawal sets the status rather than deleting the row: the day may
    already have been treated as non-working in a computed attendance day, and
    the history of what was elected when is what explains that.
    """

    __tablename__ = "optional_holiday_elections"
    __table_args__ = (
        CheckConstraint(f"status IN {ELECTION_STATUSES}", name="status_known"),
        UniqueConstraint("employee_id", "holiday_id"),
        Index("ix_optional_holiday_elections_employee", "employee_id", "status"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    holiday_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("holidays.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'elected'")
    )
    elected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    elected_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    holiday: Mapped[Holiday] = relationship()

    @property
    def is_active(self) -> bool:
        return self.status == "elected"


class HolidayImportRun(Base):
    """One bulk import of holidays, for traceability.

    A published holiday list is usually typed or pasted in bulk, and getting a
    date wrong silently marks a working day as non-working for everyone on that
    calendar. Recording what each import did - and refusing a repeat of the same
    file - makes that recoverable.
    """

    __tablename__ = "holiday_import_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        Index("ix_holiday_import_runs_calendar", "calendar_id", "started_at"),
    )

    id: Mapped[PrimaryKey]
    calendar_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("holiday_calendars.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    """Derived from the calendar and the content, so the same list imports once."""

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    rows_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    problems: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)

    imported_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
