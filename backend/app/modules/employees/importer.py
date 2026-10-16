"""Validated CSV import for employee onboarding.

Design
------
One code path serves both preview and commit, driven by ``dry_run``. Preview and
commit therefore cannot disagree about what is valid - the classic failure mode
of a separate "validate" implementation that drifts from the real one.

Every row is validated independently and collected into a report. The import is
**all-or-nothing**: if any row fails, nothing is written. A partial import of
employee master data is worse than no import, because the operator cannot tell
what landed without comparing row by row.

Only identifying and positional fields are importable. Bank details and
statutory identifiers are deliberately excluded: bulk-loading them from a
spreadsheet means those values exist in a file on someone's disk, which is
exactly what the restricted-access design is meant to avoid.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.modules.audit import service as audit
from app.modules.employees import policy, service
from app.modules.employees.models import EMPLOYMENT_STATUSES, EMPLOYMENT_TYPES, Employee
from app.modules.org.models import Department, Designation, WorkLocation

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024

REQUIRED_COLUMNS = ("employee_code", "first_name", "employment_type", "date_of_joining")

OPTIONAL_COLUMNS = (
    "middle_name",
    "last_name",
    "work_email",
    "personal_email",
    "work_phone",
    "personal_phone",
    "date_of_birth",
    "gender",
    "marital_status",
    "department_code",
    "designation_code",
    "work_location_code",
    "reporting_manager_code",
    "employment_status",
    "probation_end_date",
    "city",
    "state",
    "postal_code",
)

ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

#: Accepted date formats, most explicit first. ISO is preferred; the two
#: unambiguous day-first forms are accepted because Indian HR spreadsheets
#: commonly use them. Ambiguous US-style month-first input is NOT accepted -
#: guessing between 03/04 and 04/03 would silently corrupt joining dates.
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")


@dataclass(slots=True)
class RowError:
    row_number: int
    column: str | None
    message: str


@dataclass(slots=True)
class ImportReport:
    total_rows: int = 0
    valid_rows: int = 0
    errors: list[RowError] = field(default_factory=list)
    created_codes: list[str] = field(default_factory=list)
    dry_run: bool = True
    preview: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "error_count": len(self.errors),
            "errors": [
                {"row": e.row_number, "column": e.column, "message": e.message} for e in self.errors
            ],
            "created_codes": self.created_codes,
            "preview": self.preview,
        }


def parse_date(value: str, *, column: str) -> date:
    text = value.strip()
    if not text:
        raise policy.RuleError(column, "A date is required.")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise policy.RuleError(
        column,
        f"Could not read {text!r} as a date. Use YYYY-MM-DD (or DD-MM-YYYY). "
        "Month-first formats are not accepted because they are ambiguous.",
    )


def _decode(content: bytes) -> str:
    if len(content) > MAX_BYTES:
        raise ValidationFailed(
            f"The file is larger than {MAX_BYTES // 1024 // 1024} MB.",
            details=[{"field": "file", "message": "File too large."}],
        )
    # Spreadsheet exports from Excel are frequently UTF-8 with a BOM; utf-8-sig
    # strips it so the first header is not read as "ï»¿employee_code".
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationFailed(
        "The file is not valid UTF-8 or Windows-1252 text. Export it as CSV UTF-8.",
        details=[{"field": "file", "message": "Unreadable encoding."}],
    )


def import_employees(
    session: Session,
    *,
    content: bytes,
    context: audit.AuditContext,
    dry_run: bool = True,
) -> ImportReport:
    """Validate, and optionally apply, a CSV of employees."""
    text = _decode(content)
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise ValidationFailed(
            "The file appears to be empty.",
            details=[{"field": "file", "message": "No header row found."}],
        )

    headers = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [column for column in REQUIRED_COLUMNS if column not in headers]
    if missing:
        raise ValidationFailed(
            f"Missing required column(s): {', '.join(missing)}.",
            details=[{"field": "file", "message": f"Expected columns: {', '.join(ALL_COLUMNS)}"}],
        )

    unknown = [column for column in headers if column and column not in ALL_COLUMNS]

    report = ImportReport(dry_run=dry_run)
    if unknown:
        report.errors.append(
            RowError(
                row_number=1,
                column=None,
                message=(
                    f"Unrecognised column(s): {', '.join(unknown)}. Remove them or "
                    "correct the spelling."
                ),
            )
        )

    # Reference data resolved once, so a 500-row import is not 1500 queries.
    departments = {
        code.upper(): record_id
        for code, record_id in session.execute(
            select(Department.code, Department.id).where(Department.is_active.is_(True))
        ).all()
    }
    designations = {
        code.upper(): record_id
        for code, record_id in session.execute(
            select(Designation.code, Designation.id).where(Designation.is_active.is_(True))
        ).all()
    }
    locations = {
        code.upper(): record_id
        for code, record_id in session.execute(
            select(WorkLocation.code, WorkLocation.id).where(WorkLocation.is_active.is_(True))
        ).all()
    }
    existing_codes = {
        code.upper() for (code,) in session.execute(select(Employee.employee_code)).all()
    }
    existing_emails = {
        email.lower()
        for (email,) in session.execute(
            select(Employee.work_email).where(Employee.work_email.isnot(None))
        ).all()
    }

    # Codes and emails seen earlier in this same file, so a duplicate *within*
    # the upload is caught as well as a clash with the database.
    codes_in_file: dict[str, int] = {}
    emails_in_file: dict[str, int] = {}
    parsed_rows: list[tuple[int, dict[str, Any], str | None]] = []

    for offset, raw_row in enumerate(reader):
        row_number = offset + 2  # +1 for zero-based, +1 for the header row
        report.total_rows += 1

        if report.total_rows > MAX_ROWS:
            report.errors.append(
                RowError(
                    row_number,
                    None,
                    f"More than {MAX_ROWS} rows. Split the file into smaller batches.",
                )
            )
            break

        row = {(key or "").strip().lower(): (value or "").strip() for key, value in raw_row.items()}

        try:
            parsed, manager_code = _parse_row(
                row,
                row_number=row_number,
                departments=departments,
                designations=designations,
                locations=locations,
            )
        except policy.RuleError as error:
            report.errors.append(RowError(row_number, error.field, str(error)))
            continue

        code = parsed["employee_code"]
        if code in existing_codes:
            report.errors.append(
                RowError(row_number, "employee_code", f"{code} already exists in the system.")
            )
            continue
        if code in codes_in_file:
            report.errors.append(
                RowError(
                    row_number,
                    "employee_code",
                    f"{code} is also used on row {codes_in_file[code]} of this file.",
                )
            )
            continue
        codes_in_file[code] = row_number

        email = parsed.get("work_email")
        if email:
            if email in existing_emails:
                report.errors.append(
                    RowError(row_number, "work_email", f"{email} is already in use.")
                )
                continue
            if email in emails_in_file:
                report.errors.append(
                    RowError(
                        row_number,
                        "work_email",
                        f"{email} is also used on row {emails_in_file[email]} of this file.",
                    )
                )
                continue
            emails_in_file[email] = row_number

        parsed_rows.append((row_number, parsed, manager_code))
        report.valid_rows += 1

    # Managers may be referenced by code before they appear in the file, so the
    # reference check happens once every code in the batch is known.
    resolvable = existing_codes | set(codes_in_file)
    for row_number, _parsed, manager_code in parsed_rows:
        if manager_code and manager_code not in resolvable:
            report.errors.append(
                RowError(
                    row_number,
                    "reporting_manager_code",
                    f"No employee with code {manager_code} exists or appears in this file.",
                )
            )
            report.valid_rows -= 1

    report.preview = [
        {
            "row": row_number,
            "employee_code": parsed["employee_code"],
            "name": " ".join(
                part
                for part in (
                    parsed["first_name"],
                    parsed.get("middle_name"),
                    parsed.get("last_name"),
                )
                if part
            ),
            "work_email": parsed.get("work_email"),
            "employment_type": parsed["employment_type"],
            "date_of_joining": parsed["date_of_joining"].isoformat(),
            "reporting_manager_code": manager_code,
        }
        for row_number, parsed, manager_code in parsed_rows[:50]
    ]

    if report.errors:
        # All-or-nothing: report everything wrong and write nothing.
        return report

    if dry_run:
        return report

    _apply(session, parsed_rows=parsed_rows, report=report, context=context)
    return report


def _parse_row(
    row: dict[str, str],
    *,
    row_number: int,
    departments: dict[str, int],
    designations: dict[str, int],
    locations: dict[str, int],
) -> tuple[dict[str, Any], str | None]:
    """Convert one CSV row into employee keyword arguments."""
    parsed: dict[str, Any] = {}

    parsed["employee_code"] = policy.validate_employee_code(row.get("employee_code", ""))

    first_name = row.get("first_name", "").strip()
    if not first_name:
        raise policy.RuleError("first_name", "A first name is required.")
    parsed["first_name"] = first_name
    parsed["middle_name"] = row.get("middle_name") or None
    parsed["last_name"] = row.get("last_name") or None

    employment_type = row.get("employment_type", "").strip().lower().replace(" ", "_")
    if employment_type not in EMPLOYMENT_TYPES:
        raise policy.RuleError("employment_type", f"Must be one of: {', '.join(EMPLOYMENT_TYPES)}.")
    parsed["employment_type"] = employment_type

    status = (row.get("employment_status") or "active").strip().lower().replace(" ", "_")
    if status not in EMPLOYMENT_STATUSES:
        raise policy.RuleError(
            "employment_status", f"Must be one of: {', '.join(EMPLOYMENT_STATUSES)}."
        )
    if status == "exited":
        raise policy.RuleError(
            "employment_status",
            "Cannot import an employee as already exited. Import them as active, "
            "then record the exit so the history is correct.",
        )
    parsed["employment_status"] = status

    parsed["date_of_joining"] = parse_date(row.get("date_of_joining", ""), column="date_of_joining")
    if row.get("date_of_birth"):
        parsed["date_of_birth"] = parse_date(row["date_of_birth"], column="date_of_birth")
    if row.get("probation_end_date"):
        parsed["probation_end_date"] = parse_date(
            row["probation_end_date"], column="probation_end_date"
        )

    policy.validate_lifecycle(
        policy.LifecycleDates(
            date_of_joining=parsed["date_of_joining"],
            probation_end_date=parsed.get("probation_end_date"),
        ),
        today=date.today(),
    )

    if row.get("work_email"):
        email = row["work_email"].strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise policy.RuleError("work_email", "Enter a valid email address.")
        parsed["work_email"] = email
    if row.get("personal_email"):
        parsed["personal_email"] = row["personal_email"].strip().lower()

    parsed["work_phone"] = policy.validate_phone("work_phone", row.get("work_phone"))
    parsed["personal_phone"] = policy.validate_phone("personal_phone", row.get("personal_phone"))

    if row.get("gender"):
        parsed["gender"] = policy.validate_choice(
            "gender", row["gender"].strip().lower(), ("female", "male", "other", "not_disclosed")
        )
    if row.get("marital_status"):
        parsed["marital_status"] = policy.validate_choice(
            "marital_status",
            row["marital_status"].strip().lower(),
            ("single", "married", "other", "not_disclosed"),
        )

    for column, lookup, target, label in (
        ("department_code", departments, "department_id", "department"),
        ("designation_code", designations, "designation_id", "designation"),
        ("work_location_code", locations, "work_location_id", "work location"),
    ):
        value = row.get(column)
        if not value:
            continue
        key = value.strip().upper()
        if key not in lookup:
            raise policy.RuleError(
                column,
                f"No active {label} with code {key}. Create it first, or correct the code.",
            )
        parsed[target] = lookup[key]

    for column in ("city", "state", "postal_code"):
        if row.get(column):
            parsed[column] = row[column]

    manager_code = (
        policy.normalise_employee_code(row["reporting_manager_code"])
        if row.get("reporting_manager_code")
        else None
    )
    if manager_code and manager_code == parsed["employee_code"]:
        raise policy.RuleError("reporting_manager_code", "An employee cannot report to themselves.")

    return parsed, manager_code


def _apply(
    session: Session,
    *,
    parsed_rows: list[tuple[int, dict[str, Any], str | None]],
    report: ImportReport,
    context: audit.AuditContext,
) -> None:
    """Create every employee, then wire up managers in a second pass.

    Two passes because a manager may appear later in the file than their
    report. Both happen inside the caller's transaction, so a failure anywhere
    rolls the whole import back.
    """
    created: dict[str, Employee] = {}

    for _row_number, parsed, _manager_code in parsed_rows:
        employee = service.create_employee(session, data=dict(parsed), context=context)
        created[employee.employee_code] = employee
        report.created_codes.append(employee.employee_code)

    for _row_number, parsed, manager_code in parsed_rows:
        if not manager_code:
            continue
        employee = created[parsed["employee_code"]]
        manager = created.get(manager_code) or service.find_by_code(session, manager_code)
        if manager is None:  # pragma: no cover - pre-validated above
            raise ValidationFailed(f"Manager {manager_code} could not be resolved.")
        service.update_employee(
            session,
            employee.id,
            data={"reporting_manager_id": manager.id},
            context=context,
            effective_from=employee.date_of_joining,
            change_reason="Set during CSV import",
        )

    audit.record(
        session,
        action="employee.bulk_imported",
        context=context,
        entity_type="employee",
        entity_id=None,
        extra={"count": len(report.created_codes), "codes": report.created_codes[:100]},
    )
    session.flush()


def csv_template() -> str:
    """A header row plus one example, for the UI's "download template" link."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(ALL_COLUMNS)
    writer.writerow(
        [
            "ITC0101",
            "Priya",
            "full_time",
            "2026-11-03",
            "",
            "Nair",
            "priya.nair@example.com",
            "",
            "+91 98765 43210",
            "",
            "1995-06-14",
            "female",
            "single",
            "ENG",
            "SE2",
            "BLR",
            "ITC0001",
            "probation",
            "2027-02-03",
            "Bengaluru",
            "Karnataka",
            "560001",
        ]
    )
    return buffer.getvalue()
