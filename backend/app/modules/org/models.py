"""Organisational structure: departments, designations, work locations, groups.

These are reference records that almost everything else points at. Two
properties matter downstream:

* ``WorkLocation.timezone`` is what attendance uses to decide which calendar day
  a punch belongs to. It is not optional and not defaulted silently.
* Nothing here is ever deleted once referenced - rows are deactivated, so an
  employee's history keeps resolving to the department they were actually in.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKey, TimestampMixin


class Department(Base, TimestampMixin):
    """A department, optionally nested under a parent."""

    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="no_self_parent"),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    #: Set after the employee exists, so it cannot be a NOT NULL column.
    #: ``use_alter`` is required because departments and employees reference each
    #: other (an employee has a department; a department has a head), so one of
    #: the two constraints must be added after both tables exist.
    head_employee_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employees.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    parent: Mapped[Department | None] = relationship(
        remote_side="Department.id", back_populates="children"
    )
    children: Mapped[list[Department]] = relationship(back_populates="parent")


class Designation(Base, TimestampMixin):
    """A job title. ``level`` orders titles for reporting, not for authority."""

    __tablename__ = "designations"

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )


class WorkLocation(Base, TimestampMixin):
    """A physical or nominal place of work.

    ``timezone`` is the operative field: attendance derives ``work_date`` in this
    zone, so a punch at 00:30 IST belongs to the right local day regardless of
    where the server runs. It is validated against the IANA database on write.
    """

    __tablename__ = "work_locations"
    __table_args__ = (
        CheckConstraint("country_code = upper(country_code)", name="country_code_upper"),
        # A blank timezone would silently fall back to the server default and
        # misdate every punch at this location.
        CheckConstraint("length(btrim(timezone)) > 0", name="timezone_not_blank"),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    """IANA timezone name, e.g. 'Asia/Kolkata'. Required - never guessed."""

    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """Relevant to professional tax, which is levied per state."""

    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'IN'")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )


class EmployeeGroup(Base, TimestampMixin):
    """An arbitrary cohort, used for targeting announcements and calendars.

    Deliberately separate from department: "all employees on the Pune holiday
    calendar" or "all interns" does not necessarily follow the org chart.
    """

    __tablename__ = "employee_groups"

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    members: Mapped[list[EmployeeGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class EmployeeGroupMember(Base):
    __tablename__ = "employee_group_members"
    __table_args__ = (UniqueConstraint("group_id", "employee_id"),)

    id: Mapped[PrimaryKey]
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employee_groups.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    group: Mapped[EmployeeGroup] = relationship(back_populates="members")
