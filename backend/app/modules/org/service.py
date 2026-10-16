"""Department, designation, work location and group management."""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, PolicyViolation, ValidationFailed
from app.core.timeutil import validate_timezone_name
from app.modules.audit import service as audit
from app.modules.org.models import (
    Department,
    Designation,
    EmployeeGroup,
    EmployeeGroupMember,
    WorkLocation,
)

ModelT = TypeVar("ModelT", Department, Designation, WorkLocation, EmployeeGroup)

MAX_DEPARTMENT_DEPTH = 10


def normalise_code(code: str) -> str:
    return code.strip().upper()


def _ensure_code_free(
    session: Session, model: type[ModelT], code: str, *, exclude_id: int | None = None
) -> str:
    normalised = normalise_code(code)
    if not normalised:
        raise ValidationFailed(
            "A code is required.", details=[{"field": "code", "message": "Required."}]
        )

    statement = select(func.count()).select_from(model).where(model.code == normalised)
    if exclude_id is not None:
        statement = statement.where(model.id != exclude_id)
    if session.scalar(statement):
        raise Conflict(
            f"The code {normalised!r} is already in use.",
            details=[{"field": "code", "message": "Already in use."}],
        )
    return normalised


def _get_or_404(session: Session, model: type[ModelT], record_id: int, label: str) -> ModelT:
    record = session.get(model, record_id)
    if record is None:
        raise NotFound(f"{label} not found.")
    return record


# ---------------------------------------------------------------- departments


def _assert_no_department_cycle(
    session: Session, *, department_id: int | None, parent_id: int | None
) -> None:
    """Walk up from the proposed parent, refusing a cycle or excessive depth.

    A cycle here would make any recursive org-chart query loop; the depth cap
    keeps the chart renderable and catches accidental deep nesting.
    """
    if parent_id is None:
        return
    if parent_id == department_id:
        raise PolicyViolation(
            "A department cannot be its own parent.",
            details=[{"field": "parent_id", "message": "Invalid parent."}],
        )

    seen: set[int] = {department_id} if department_id else set()
    current: int | None = parent_id
    depth = 0

    while current is not None:
        if current in seen:
            raise PolicyViolation(
                "That parent would create a loop in the department hierarchy.",
                details=[{"field": "parent_id", "message": "Creates a loop."}],
            )
        seen.add(current)
        depth += 1
        if depth > MAX_DEPARTMENT_DEPTH:
            raise PolicyViolation(
                f"Department nesting is limited to {MAX_DEPARTMENT_DEPTH} levels."
            )
        parent = session.get(Department, current)
        if parent is None:
            raise ValidationFailed(
                "The parent department does not exist.",
                details=[{"field": "parent_id", "message": "Unknown department."}],
            )
        current = parent.parent_id


def create_department(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> Department:
    code = _ensure_code_free(session, Department, data["code"])
    _assert_no_department_cycle(session, department_id=None, parent_id=data.get("parent_id"))

    department = Department(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        parent_id=data.get("parent_id"),
        is_active=data.get("is_active", True),
    )
    session.add(department)
    session.flush()

    audit.record(
        session,
        action="org.department_created",
        context=context,
        entity_type="department",
        entity_id=department.id,
        after={"code": department.code, "name": department.name},
    )
    return department


def update_department(
    session: Session, department_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Department:
    department = _get_or_404(session, Department, department_id, "Department")
    before = {
        "code": department.code,
        "name": department.name,
        "parent_id": department.parent_id,
        "is_active": department.is_active,
    }

    if "code" in data:
        department.code = _ensure_code_free(
            session, Department, data["code"], exclude_id=department_id
        )
    if "parent_id" in data:
        _assert_no_department_cycle(
            session, department_id=department_id, parent_id=data["parent_id"]
        )
        department.parent_id = data["parent_id"]
    for field in ("name", "description", "head_employee_id", "is_active"):
        if field in data:
            setattr(department, field, data[field])

    session.flush()
    audit.record(
        session,
        action="org.department_updated",
        context=context,
        entity_type="department",
        entity_id=department.id,
        before=before,
        after={
            "code": department.code,
            "name": department.name,
            "parent_id": department.parent_id,
            "is_active": department.is_active,
        },
    )
    return department


# --------------------------------------------------------------- designations


def create_designation(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> Designation:
    code = _ensure_code_free(session, Designation, data["code"])
    designation = Designation(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        level=data.get("level"),
        is_active=data.get("is_active", True),
    )
    session.add(designation)
    session.flush()
    audit.record(
        session,
        action="org.designation_created",
        context=context,
        entity_type="designation",
        entity_id=designation.id,
        after={"code": designation.code, "name": designation.name},
    )
    return designation


def update_designation(
    session: Session, designation_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Designation:
    designation = _get_or_404(session, Designation, designation_id, "Designation")
    before = {
        "code": designation.code,
        "name": designation.name,
        "is_active": designation.is_active,
    }

    if "code" in data:
        designation.code = _ensure_code_free(
            session, Designation, data["code"], exclude_id=designation_id
        )
    for field in ("name", "description", "level", "is_active"):
        if field in data:
            setattr(designation, field, data[field])

    session.flush()
    audit.record(
        session,
        action="org.designation_updated",
        context=context,
        entity_type="designation",
        entity_id=designation.id,
        before=before,
        after={
            "code": designation.code,
            "name": designation.name,
            "is_active": designation.is_active,
        },
    )
    return designation


# ------------------------------------------------------------- work locations


def _validate_timezone(name: str) -> str:
    """Reject an unknown IANA timezone at write time.

    This is the single most consequential field in the organisation module: if
    it is wrong, every attendance date computed for employees at that location
    is wrong. Failing here is far cheaper than discovering it in a month-end
    report.
    """
    try:
        # Strict validation, not resolution: resolve_timezone() deliberately
        # falls back to the configured default, which would let an invalid name
        # through here and misdate attendance for everyone at this location.
        return validate_timezone_name(name)
    except ValueError as exc:
        raise ValidationFailed(
            str(exc),
            details=[{"field": "timezone", "message": "Use a name such as 'Asia/Kolkata'."}],
        ) from exc


def create_work_location(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> WorkLocation:
    code = _ensure_code_free(session, WorkLocation, data["code"])
    location = WorkLocation(
        code=code,
        name=data["name"].strip(),
        timezone=_validate_timezone(data["timezone"]),
        address_line1=data.get("address_line1"),
        address_line2=data.get("address_line2"),
        city=data.get("city"),
        state=data.get("state"),
        postal_code=data.get("postal_code"),
        country_code=(data.get("country_code") or "IN").upper(),
        is_active=data.get("is_active", True),
    )
    session.add(location)
    session.flush()
    audit.record(
        session,
        action="org.work_location_created",
        context=context,
        entity_type="work_location",
        entity_id=location.id,
        after={"code": location.code, "name": location.name, "timezone": location.timezone},
    )
    return location


def update_work_location(
    session: Session, location_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> WorkLocation:
    location = _get_or_404(session, WorkLocation, location_id, "Work location")
    before = {
        "code": location.code,
        "name": location.name,
        "timezone": location.timezone,
        "state": location.state,
        "is_active": location.is_active,
    }

    if "code" in data:
        location.code = _ensure_code_free(
            session, WorkLocation, data["code"], exclude_id=location_id
        )
    if "timezone" in data:
        location.timezone = _validate_timezone(data["timezone"])
    for field in (
        "name",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "is_active",
    ):
        if field in data:
            setattr(location, field, data[field])
    if "country_code" in data and data["country_code"]:
        location.country_code = data["country_code"].upper()

    session.flush()
    # A timezone change is logged prominently: it alters how future attendance
    # dates are derived for everyone at this location.
    audit.record(
        session,
        action="org.work_location_updated",
        context=context,
        entity_type="work_location",
        entity_id=location.id,
        before=before,
        after={
            "code": location.code,
            "name": location.name,
            "timezone": location.timezone,
            "state": location.state,
            "is_active": location.is_active,
        },
        extra={"timezone_changed": before["timezone"] != location.timezone},
    )
    return location


# ---------------------------------------------------------------- groups


def create_group(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> EmployeeGroup:
    code = _ensure_code_free(session, EmployeeGroup, data["code"])
    group = EmployeeGroup(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        is_active=data.get("is_active", True),
    )
    session.add(group)
    session.flush()
    audit.record(
        session,
        action="org.group_created",
        context=context,
        entity_type="employee_group",
        entity_id=group.id,
        after={"code": group.code, "name": group.name},
    )
    return group


def set_group_members(
    session: Session,
    group_id: int,
    employee_ids: list[int],
    *,
    context: audit.AuditContext,
) -> EmployeeGroup:
    """Replace a group's membership wholesale."""
    from app.modules.employees.models import Employee

    group = _get_or_404(session, EmployeeGroup, group_id, "Employee group")

    requested = set(employee_ids)
    if requested:
        found = set(session.scalars(select(Employee.id).where(Employee.id.in_(requested))).all())
        missing = requested - found
        if missing:
            raise ValidationFailed(
                f"Unknown employee id(s): {sorted(missing)}.",
                details=[{"field": "employee_ids", "message": "One or more ids do not exist."}],
            )

    existing = {member.employee_id: member for member in group.members}
    for employee_id in requested - set(existing):
        session.add(EmployeeGroupMember(group_id=group.id, employee_id=employee_id))
    for employee_id in set(existing) - requested:
        session.delete(existing[employee_id])

    session.flush()
    # The `members` collection was loaded before these changes, so it would
    # still report the old membership to the caller building the response.
    session.refresh(group, attribute_names=["members"])
    audit.record(
        session,
        action="org.group_members_set",
        context=context,
        entity_type="employee_group",
        entity_id=group.id,
        before={"member_ids": sorted(existing)},
        after={"member_ids": sorted(requested)},
    )
    return group


# ---------------------------------------------------------------- read helpers


def list_departments(session: Session, *, include_inactive: bool = False) -> list[Department]:
    statement = select(Department).order_by(Department.name)
    if not include_inactive:
        statement = statement.where(Department.is_active.is_(True))
    return list(session.scalars(statement).all())


def list_designations(session: Session, *, include_inactive: bool = False) -> list[Designation]:
    statement = select(Designation).order_by(Designation.level, Designation.name)
    if not include_inactive:
        statement = statement.where(Designation.is_active.is_(True))
    return list(session.scalars(statement).all())


def list_work_locations(session: Session, *, include_inactive: bool = False) -> list[WorkLocation]:
    statement = select(WorkLocation).order_by(WorkLocation.name)
    if not include_inactive:
        statement = statement.where(WorkLocation.is_active.is_(True))
    return list(session.scalars(statement).all())


def list_groups(session: Session, *, include_inactive: bool = False) -> list[EmployeeGroup]:
    statement = select(EmployeeGroup).order_by(EmployeeGroup.name)
    if not include_inactive:
        statement = statement.where(EmployeeGroup.is_active.is_(True))
    return list(session.scalars(statement).all())


def resolve_employee_timezone(session: Session, employee_id: int) -> str:
    """The IANA timezone that governs attendance dates for this employee.

    Falls back to the configured default only when the employee has no work
    location. Attendance code must always go through here rather than reading
    the server timezone.
    """
    from app.core.config import get_settings
    from app.modules.employees.models import Employee

    timezone_name = session.scalar(
        select(WorkLocation.timezone)
        .join(Employee, Employee.work_location_id == WorkLocation.id)
        .where(Employee.id == employee_id)
    )
    return timezone_name or get_settings().default_timezone
