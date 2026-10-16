"""Employee lifecycle: onboarding, updates, transfers, exit, and scoped reads."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.crypto import encrypt, last4
from app.core.errors import Conflict, NotFound, PermissionDenied, PolicyViolation, ValidationFailed
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees import policy
from app.modules.employees.models import (
    Employee,
    EmployeeBankAccount,
    EmployeeStatutoryDetails,
    EmploymentHistory,
)
from app.modules.employees.policy import RuleError
from app.modules.org.models import Department, Designation, WorkLocation

#: Fields an employee may change on their own profile. Anything affecting pay,
#: position or reporting line is HR-only, which is why this list is short and
#: explicit rather than a blocklist.
SELF_EDITABLE_FIELDS = frozenset(
    {
        "personal_email",
        "personal_phone",
        "emergency_contact_name",
        "emergency_contact_phone",
        "emergency_contact_relation",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "marital_status",
    }
)


def _rule(error: RuleError) -> ValidationFailed:
    """Convert a policy RuleError into the API's validation error."""
    return ValidationFailed(str(error), details=[{"field": error.field, "message": str(error)}])


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def get_employee(session: Session, employee_id: int) -> Employee:
    employee = session.get(
        Employee,
        employee_id,
        options=[
            selectinload(Employee.statutory_details),
            selectinload(Employee.bank_accounts),
        ],
    )
    if employee is None:
        raise NotFound("Employee not found.")
    return employee


def find_by_code(session: Session, employee_code: str) -> Employee | None:
    return session.scalar(
        select(Employee).where(
            Employee.employee_code == policy.normalise_employee_code(employee_code)
        )
    )


def _assert_code_available(session: Session, code: str, *, exclude_id: int | None = None) -> str:
    normalised = policy.validate_employee_code(code)
    statement = (
        select(func.count()).select_from(Employee).where(Employee.employee_code == normalised)
    )
    if exclude_id is not None:
        statement = statement.where(Employee.id != exclude_id)
    if session.scalar(statement):
        raise Conflict(
            f"Employee code {normalised!r} is already in use.",
            details=[{"field": "employee_code", "message": "Already in use."}],
        )
    return normalised


def _assert_work_email_available(
    session: Session, email: str | None, *, exclude_id: int | None = None
) -> str | None:
    if not email:
        return None
    cleaned = email.strip().lower()
    statement = select(func.count()).select_from(Employee).where(Employee.work_email == cleaned)
    if exclude_id is not None:
        statement = statement.where(Employee.id != exclude_id)
    if session.scalar(statement):
        raise Conflict(
            "That work email is already assigned to another employee.",
            details=[{"field": "work_email", "message": "Already in use."}],
        )
    return cleaned


def _assert_references_exist(session: Session, data: dict[str, Any]) -> None:
    """Check that referenced org records exist and are usable."""
    checks = (
        ("department_id", Department, "Department"),
        ("designation_id", Designation, "Designation"),
        ("work_location_id", WorkLocation, "Work location"),
    )
    for field, model, label in checks:
        value = data.get(field)
        if value is None:
            continue
        record = session.get(model, value)
        if record is None:
            raise ValidationFailed(
                f"{label} does not exist.", details=[{"field": field, "message": "Unknown."}]
            )
        if not record.is_active:
            raise ValidationFailed(
                f"{label} {record.name!r} is inactive.",
                details=[{"field": field, "message": "Inactive."}],
            )


def _assert_manager_assignable(
    session: Session, *, employee_id: int | None, manager_id: int | None
) -> None:
    """Validate a reporting-manager assignment.

    Two failure modes to prevent:

    * A cycle, which would make the approval walk in leave management loop.
    * An exited manager, who cannot approve anything.
    """
    if manager_id is None:
        return

    manager = session.get(Employee, manager_id)
    if manager is None:
        raise ValidationFailed(
            "The reporting manager does not exist.",
            details=[{"field": "reporting_manager_id", "message": "Unknown employee."}],
        )
    if manager.employment_status == "exited":
        raise PolicyViolation(
            f"{manager.full_name} has exited and cannot be a reporting manager.",
            details=[{"field": "reporting_manager_id", "message": "Employee has exited."}],
        )
    if employee_id is not None and manager_id == employee_id:
        raise PolicyViolation(
            "An employee cannot report to themselves.",
            details=[{"field": "reporting_manager_id", "message": "Invalid manager."}],
        )

    if employee_id is None:
        return

    manager_of = dict(
        session.execute(
            select(Employee.id, Employee.reporting_manager_id).where(
                Employee.reporting_manager_id.isnot(None)
            )
        ).all()
    )
    cycle = policy.detect_reporting_cycle(
        employee_id=employee_id, proposed_manager_id=manager_id, manager_of=manager_of
    )
    if cycle:
        names = dict(
            session.execute(
                select(Employee.id, Employee.employee_code).where(Employee.id.in_(cycle))
            ).all()
        )
        chain = " -> ".join(names.get(node, str(node)) for node in cycle)
        raise PolicyViolation(
            "That assignment would create a loop in the reporting line.",
            details=[{"field": "reporting_manager_id", "message": f"Loop: {chain}"}],
        )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_employee(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> Employee:
    """Onboard an employee and open their employment history."""
    try:
        code = _assert_code_available(session, data["employee_code"])
        policy.validate_employment_fields(
            employment_type=data["employment_type"],
            employment_status=data.get("employment_status", "active"),
            gender=data.get("gender"),
            marital_status=data.get("marital_status"),
            exit_type=data.get("exit_type"),
        )
        policy.validate_lifecycle(
            policy.LifecycleDates(
                date_of_joining=data["date_of_joining"],
                probation_end_date=data.get("probation_end_date"),
                date_of_confirmation=data.get("date_of_confirmation"),
            ),
            today=date.today(),
        )
        work_phone = policy.validate_phone("work_phone", data.get("work_phone"))
        personal_phone = policy.validate_phone("personal_phone", data.get("personal_phone"))
        emergency_phone = policy.validate_phone(
            "emergency_contact_phone", data.get("emergency_contact_phone")
        )
    except RuleError as error:
        raise _rule(error) from error

    _assert_references_exist(session, data)
    _assert_manager_assignable(
        session, employee_id=None, manager_id=data.get("reporting_manager_id")
    )
    work_email = _assert_work_email_available(session, data.get("work_email"))

    employee = Employee(
        employee_code=code,
        first_name=data["first_name"].strip(),
        middle_name=(data.get("middle_name") or "").strip() or None,
        last_name=(data.get("last_name") or "").strip() or None,
        date_of_birth=data.get("date_of_birth"),
        gender=data.get("gender"),
        marital_status=data.get("marital_status"),
        work_email=work_email,
        personal_email=(data.get("personal_email") or "").strip().lower() or None,
        work_phone=work_phone,
        personal_phone=personal_phone,
        emergency_contact_name=data.get("emergency_contact_name"),
        emergency_contact_phone=emergency_phone,
        emergency_contact_relation=data.get("emergency_contact_relation"),
        address_line1=data.get("address_line1"),
        address_line2=data.get("address_line2"),
        city=data.get("city"),
        state=data.get("state"),
        postal_code=data.get("postal_code"),
        department_id=data.get("department_id"),
        designation_id=data.get("designation_id"),
        work_location_id=data.get("work_location_id"),
        reporting_manager_id=data.get("reporting_manager_id"),
        employment_type=data["employment_type"],
        employment_status=data.get("employment_status", "active"),
        date_of_joining=data["date_of_joining"],
        probation_end_date=data.get("probation_end_date"),
        date_of_confirmation=data.get("date_of_confirmation"),
        notes=data.get("notes"),
    )
    session.add(employee)
    session.flush()

    _append_history(
        session,
        employee=employee,
        effective_from=employee.date_of_joining,
        change_type="joining",
        change_reason="Employee onboarded",
        context=context,
    )

    audit.record(
        session,
        action="employee.created",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "employee_code": employee.employee_code,
            "name": employee.full_name,
            "department_id": employee.department_id,
            "employment_type": employee.employment_type,
            "date_of_joining": employee.date_of_joining.isoformat(),
        },
    )
    session.flush()
    return employee


def _append_history(
    session: Session,
    *,
    employee: Employee,
    effective_from: date,
    change_type: str,
    change_reason: str | None,
    context: audit.AuditContext,
) -> EmploymentHistory:
    """Close the open history row and start a new one.

    The previous row's ``effective_to`` is set to the day before the new row
    starts, so the periods abut without overlapping.
    """
    from datetime import timedelta

    open_rows = session.scalars(
        select(EmploymentHistory)
        .where(
            EmploymentHistory.employee_id == employee.id,
            EmploymentHistory.effective_to.is_(None),
        )
        .order_by(EmploymentHistory.effective_from.desc())
    ).all()

    for row in open_rows:
        # Guard against a backdated change creating an inverted period.
        row.effective_to = max(effective_from - timedelta(days=1), row.effective_from)

    entry = EmploymentHistory(
        employee_id=employee.id,
        effective_from=effective_from,
        effective_to=None,
        department_id=employee.department_id,
        designation_id=employee.designation_id,
        work_location_id=employee.work_location_id,
        reporting_manager_id=employee.reporting_manager_id,
        employment_type=employee.employment_type,
        employment_status=employee.employment_status,
        change_type=change_type,
        change_reason=change_reason,
        recorded_by_user_id=context.actor_user_id,
    )
    session.add(entry)
    session.flush()
    return entry


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

#: Changes to these fields open a new employment-history period, because
#: attendance and payroll reports need to know when the change took effect.
HISTORY_TRIGGERING_FIELDS = frozenset(
    {
        "department_id",
        "designation_id",
        "work_location_id",
        "reporting_manager_id",
        "employment_type",
        "employment_status",
    }
)


def update_employee(
    session: Session,
    employee_id: int,
    *,
    data: dict[str, Any],
    context: audit.AuditContext,
    effective_from: date | None = None,
    change_reason: str | None = None,
) -> Employee:
    employee = get_employee(session, employee_id)

    before = _snapshot(employee)

    try:
        if "employee_code" in data:
            employee.employee_code = _assert_code_available(
                session, data["employee_code"], exclude_id=employee_id
            )

        merged_type = data.get("employment_type", employee.employment_type)
        merged_status = data.get("employment_status", employee.employment_status)
        policy.validate_employment_fields(
            employment_type=merged_type,
            employment_status=merged_status,
            gender=data.get("gender", employee.gender),
            marital_status=data.get("marital_status", employee.marital_status),
            exit_type=data.get("exit_type", employee.exit_type),
        )

        policy.validate_lifecycle(
            policy.LifecycleDates(
                date_of_joining=data.get("date_of_joining", employee.date_of_joining),
                probation_end_date=data.get("probation_end_date", employee.probation_end_date),
                date_of_confirmation=data.get(
                    "date_of_confirmation", employee.date_of_confirmation
                ),
                last_working_day=data.get("last_working_day", employee.last_working_day),
                date_of_exit=data.get("date_of_exit", employee.date_of_exit),
            ),
            today=date.today(),
        )
        policy.validate_exit(
            employment_status=merged_status,
            date_of_exit=data.get("date_of_exit", employee.date_of_exit),
            exit_type=data.get("exit_type", employee.exit_type),
        )

        for field in ("work_phone", "personal_phone", "emergency_contact_phone"):
            if field in data:
                data[field] = policy.validate_phone(field, data[field])
    except RuleError as error:
        raise _rule(error) from error

    _assert_references_exist(session, data)
    if "reporting_manager_id" in data:
        _assert_manager_assignable(
            session, employee_id=employee_id, manager_id=data["reporting_manager_id"]
        )
    if "work_email" in data:
        data["work_email"] = _assert_work_email_available(
            session, data["work_email"], exclude_id=employee_id
        )

    assignable = {
        "first_name",
        "middle_name",
        "last_name",
        "date_of_birth",
        "gender",
        "marital_status",
        "work_email",
        "personal_email",
        "work_phone",
        "personal_phone",
        "emergency_contact_name",
        "emergency_contact_phone",
        "emergency_contact_relation",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "department_id",
        "designation_id",
        "work_location_id",
        "reporting_manager_id",
        "employment_type",
        "employment_status",
        "date_of_joining",
        "probation_end_date",
        "date_of_confirmation",
        "date_of_exit",
        "exit_type",
        "exit_reason",
        "last_working_day",
        "notes",
    }
    changed_fields = []
    for field, value in data.items():
        if field in assignable and getattr(employee, field) != value:
            setattr(employee, field, value)
            changed_fields.append(field)

    session.flush()

    if HISTORY_TRIGGERING_FIELDS.intersection(changed_fields):
        change_type = "exit" if employee.employment_status == "exited" else "transfer"
        if changed_fields == ["reporting_manager_id"]:
            change_type = "manager_change"
        elif changed_fields == ["employment_status"]:
            change_type = "status_change"
        _append_history(
            session,
            employee=employee,
            effective_from=effective_from or date.today(),
            change_type=change_type,
            change_reason=change_reason,
            context=context,
        )

    audit.record(
        session,
        action="employee.updated",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        before=before,
        after=_snapshot(employee),
        extra={"changed_fields": sorted(changed_fields), "reason": change_reason},
    )
    session.flush()
    return employee


def _snapshot(employee: Employee) -> dict[str, Any]:
    """Audit-safe view of an employee. No statutory or bank data."""
    return {
        "employee_code": employee.employee_code,
        "name": employee.full_name,
        "work_email": employee.work_email,
        "department_id": employee.department_id,
        "designation_id": employee.designation_id,
        "work_location_id": employee.work_location_id,
        "reporting_manager_id": employee.reporting_manager_id,
        "employment_type": employee.employment_type,
        "employment_status": employee.employment_status,
        "date_of_joining": employee.date_of_joining.isoformat(),
        "date_of_exit": employee.date_of_exit.isoformat() if employee.date_of_exit else None,
    }


def update_own_profile(
    session: Session,
    *,
    user: User,
    data: dict[str, Any],
    context: audit.AuditContext,
) -> Employee:
    """Let an employee change their own permitted personal fields.

    The allow-list is applied here rather than relying on the schema, so that a
    future schema change cannot accidentally widen what an employee may edit
    about themselves.
    """
    if user.employee_id is None:
        raise Conflict("Your account is not linked to an employee record. Contact HR.")

    employee = get_employee(session, user.employee_id)
    rejected = set(data) - SELF_EDITABLE_FIELDS
    if rejected:
        raise PermissionDenied(
            "Some of those fields can only be changed by HR.",
            details=[
                {"field": field, "message": "Not editable by the employee."}
                for field in sorted(rejected)
            ],
        )

    before = {field: getattr(employee, field) for field in data}
    try:
        for field in ("personal_phone", "emergency_contact_phone"):
            if field in data:
                data[field] = policy.validate_phone(field, data[field])
        if "marital_status" in data:
            policy.validate_choice(
                "marital_status",
                data["marital_status"],
                ("single", "married", "other", "not_disclosed"),
            )
    except RuleError as error:
        raise _rule(error) from error

    for field, value in data.items():
        setattr(employee, field, value)

    session.flush()
    audit.record(
        session,
        action="employee.self_profile_updated",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        before=before,
        after=data,
    )
    return employee


# ---------------------------------------------------------------------------
# Restricted details
# ---------------------------------------------------------------------------


def upsert_statutory_details(
    session: Session,
    employee_id: int,
    *,
    data: dict[str, Any],
    context: audit.AuditContext,
) -> EmployeeStatutoryDetails:
    employee = get_employee(session, employee_id)
    record = employee.statutory_details or EmployeeStatutoryDetails(employee_id=employee.id)

    try:
        if data.get("pf_uan") is not None:
            record.pf_uan = policy.validate_uan(data["pf_uan"])
        if data.get("aadhaar_last4") is not None:
            record.aadhaar_last4 = policy.validate_aadhaar_last4(data["aadhaar_last4"])
        if data.get("pan"):
            pan = policy.validate_pan(data["pan"])
            record.pan_encrypted = encrypt(pan)
            record.pan_last4 = last4(pan)
    except RuleError as error:
        raise _rule(error) from error

    for field in (
        "pf_number",
        "esi_number",
        "pf_opted_in",
        "esi_eligible_override",
        "pt_state",
        "tax_regime",
    ):
        if field in data:
            setattr(record, field, data[field])

    if record.id is None:
        session.add(record)
    session.flush()

    # Field names only - never the values. These are the fields the brief
    # explicitly forbids logging.
    audit.record(
        session,
        action="employee.statutory_details_updated",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        extra={"fields_changed": sorted(data.keys())},
    )
    return record


def add_bank_account(
    session: Session,
    employee_id: int,
    *,
    data: dict[str, Any],
    context: audit.AuditContext,
) -> EmployeeBankAccount:
    employee = get_employee(session, employee_id)

    try:
        account_number = policy.validate_account_number(data["account_number"])
        ifsc = policy.validate_ifsc(data["ifsc_code"])
    except RuleError as error:
        raise _rule(error) from error

    make_primary = data.get("is_primary", True)
    if make_primary:
        # The partial unique index permits one primary per employee, so the
        # previous primary must be demoted in the same transaction.
        for existing in employee.bank_accounts:
            existing.is_primary = False
        session.flush()

    account = EmployeeBankAccount(
        employee_id=employee.id,
        account_holder_name=data["account_holder_name"].strip(),
        account_number_encrypted=encrypt(account_number),
        account_number_last4=last4(account_number),
        ifsc_code=ifsc,
        bank_name=data["bank_name"].strip(),
        branch_name=data.get("branch_name"),
        account_type=data.get("account_type"),
        is_primary=make_primary,
    )
    session.add(account)
    session.flush()

    audit.record(
        session,
        action="employee.bank_account_added",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        # Only the last four digits and the bank, never the account number.
        extra={
            "bank_name": account.bank_name,
            "account_last4": account.account_number_last4,
            "is_primary": account.is_primary,
        },
    )
    return account


# ---------------------------------------------------------------------------
# Scoped reads
# ---------------------------------------------------------------------------


def team_employee_ids(session: Session, manager_employee_id: int) -> set[int]:
    """Direct and indirect reports of an employee."""
    rows = session.execute(
        select(Employee.id, Employee.reporting_manager_id).where(
            Employee.reporting_manager_id.isnot(None)
        )
    ).all()
    reports_of: dict[int, list[int]] = {}
    for employee_id, manager_id in rows:
        reports_of.setdefault(manager_id, []).append(employee_id)
    return policy.collect_subordinates(manager_employee_id, reports_of)


def visible_employee_scope(session: Session, user: User) -> set[int] | None:
    """Which employees this user may read.

    ``None`` means unrestricted (``employee.read``). Otherwise a concrete set:
    their own record plus their reports if they hold ``employee.read_team``.

    Returning an explicit set, rather than letting each endpoint build its own
    filter, is what stops one endpoint forgetting the restriction.
    """
    if user.has_permission("employee.read"):
        return None

    allowed: set[int] = set()
    if user.employee_id is not None:
        allowed.add(user.employee_id)
        if user.has_permission("employee.read_team"):
            allowed |= team_employee_ids(session, user.employee_id)
    return allowed


def assert_can_read(session: Session, user: User, employee_id: int) -> None:
    scope = visible_employee_scope(session, user)
    if scope is None or employee_id in scope:
        return
    # Deliberately 404 rather than 403: confirming that an employee id exists
    # would leak the size and shape of the organisation to anyone probing.
    raise NotFound("Employee not found.")


def build_directory_query(
    *,
    scope: set[int] | None,
    search: str | None = None,
    department_id: int | None = None,
    work_location_id: int | None = None,
    employment_status: str | None = None,
    employment_type: str | None = None,
    include_exited: bool = False,
) -> Select[Any]:
    statement = select(Employee)

    if scope is not None:
        if not scope:
            # An empty scope must match nothing, so an account with no employee
            # record sees an empty directory rather than the whole company.
            # `sqlalchemy.false()` renders the SQL keyword; `func.false()` would
            # emit `false()`, which PostgreSQL rejects as a syntax error.
            return statement.where(false())
        statement = statement.where(Employee.id.in_(scope))

    if not include_exited and employment_status is None:
        statement = statement.where(Employee.employment_status != "exited")
    if employment_status:
        statement = statement.where(Employee.employment_status == employment_status)
    if employment_type:
        statement = statement.where(Employee.employment_type == employment_type)
    if department_id:
        statement = statement.where(Employee.department_id == department_id)
    if work_location_id:
        statement = statement.where(Employee.work_location_id == work_location_id)

    if search:
        term = f"%{search.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(Employee.employee_code).like(term),
                func.lower(Employee.first_name).like(term),
                func.lower(Employee.last_name).like(term),
                func.lower(Employee.work_email).like(term),
            )
        )

    return statement


def link_user_account(
    session: Session,
    *,
    employee_id: int,
    user_id: int,
    context: audit.AuditContext,
) -> User:
    """Associate a sign-in account with an employee record."""
    employee = get_employee(session, employee_id)
    user = session.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")

    if user.employee_id is not None and user.employee_id != employee.id:
        raise Conflict("That account is already linked to a different employee.")

    clash = session.scalar(select(User).where(User.employee_id == employee.id, User.id != user.id))
    if clash is not None:
        raise Conflict(
            f"Employee {employee.employee_code} is already linked to account {clash.username!r}."
        )

    user.employee_id = employee.id
    session.flush()
    audit.record(
        session,
        action="employee.account_linked",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={"user_id": user.id, "username": user.username},
    )
    return user


def exit_employee(
    session: Session,
    employee_id: int,
    *,
    date_of_exit: date,
    exit_type: str,
    exit_reason: str | None,
    last_working_day: date | None,
    context: audit.AuditContext,
    deactivate_account: bool = True,
) -> Employee:
    """Offboard an employee, preserving every historical record.

    Nothing is deleted. The employee row stays, their attendance, leave ledger
    and payslips stay, and their sign-in account is deactivated so the history
    remains attributable.
    """
    employee = update_employee(
        session,
        employee_id,
        data={
            "employment_status": "exited",
            "date_of_exit": date_of_exit,
            "exit_type": exit_type,
            "exit_reason": exit_reason,
            "last_working_day": last_working_day or date_of_exit,
        },
        context=context,
        effective_from=date_of_exit,
        change_reason=exit_reason or "Employee exit",
    )

    # Direct reports would otherwise point at a manager who cannot approve.
    orphaned = session.scalars(
        select(Employee).where(
            Employee.reporting_manager_id == employee.id,
            Employee.employment_status != "exited",
        )
    ).all()

    if deactivate_account:
        from app.modules.auth import service as auth_service

        account = session.scalar(select(User).where(User.employee_id == employee.id))
        if account is not None and account.is_active:
            auth_service.set_user_active(
                session,
                target_user_id=account.id,
                is_active=False,
                reason=f"Employee exited on {date_of_exit.isoformat()}",
                context=context,
            )

    audit.record(
        session,
        action="employee.exited",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "date_of_exit": date_of_exit.isoformat(),
            "exit_type": exit_type,
            "account_deactivated": deactivate_account,
        },
        extra={
            "reports_needing_reassignment": [e.employee_code for e in orphaned],
            "last_updated_at": utc_now().isoformat(),
        },
    )
    session.flush()
    return employee


def reports_needing_reassignment(session: Session) -> list[Employee]:
    """Active employees whose reporting manager has exited or is missing.

    Surfaced on the HR dashboard: these employees have no one to review their
    leave, so the condition needs to be visible rather than discovered when a
    request stalls.
    """
    from sqlalchemy.orm import aliased

    manager = aliased(Employee, name="manager")
    return list(
        session.scalars(
            select(Employee)
            .outerjoin(manager, manager.id == Employee.reporting_manager_id)
            .where(
                Employee.employment_status != "exited",
                or_(
                    # No manager assigned at all...
                    Employee.reporting_manager_id.is_(None),
                    # ...or the assigned manager has since exited.
                    manager.employment_status == "exited",
                ),
            )
            .order_by(Employee.employee_code)
        ).all()
    )
