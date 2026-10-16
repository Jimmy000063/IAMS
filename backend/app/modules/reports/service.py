"""Reports, built from the data the other modules already own.

Reports read; they never compute
--------------------------------
Every figure here comes from a table some module already wrote: payable day
fractions from the attendance engine, balances from the leave ledger, totals
from payslips. A report that recomputed anything would be a second
implementation of that rule, and the two would eventually disagree - at which
point nobody could say which was right.

Shape
-----
Each report returns a :class:`ReportResult`: the column definitions and the
rows, as plain values. The same structure serves the JSON endpoint, the CSV
export and the Excel export, so a column added here appears in all three
without being declared three times.

Scope
-----
Reports obey the same visibility rules as the modules they read. A caller who
may see only their own team passes the employee ids they are allowed, and the
query is narrowed; there is no report that widens somebody's access.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.modules.attendance.models import AttendanceDay, AttendanceRegularization
from app.modules.compoff.models import CompoffCredit
from app.modules.employees.models import Employee
from app.modules.leave.models import LeaveLedgerEntry, LeaveType
from app.modules.org.models import Department, Designation, WorkLocation
from app.modules.payroll.models import (
    PayrollPeriod,
    PayrollRun,
    Payslip,
    PayslipLine,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

#: A hard ceiling on rows in one response. A report is for reading; an export of
#: everything is a different operation with a different cost, and an unbounded
#: query against attendance would eventually take the database down.
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    label: str
    #: ``text``, ``number``, ``money``, ``days``, ``date`` or ``boolean``. Used
    #: by the exporters for alignment and by the interface for formatting.
    kind: str = "text"


@dataclass(frozen=True, slots=True)
class ReportResult:
    code: str
    title: str
    description: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    #: What the report was run with, echoed back so an exported file can say so.
    filters: dict[str, Any] = field(default_factory=dict)
    #: Totals for the numeric columns, where a total means something.
    totals: dict[str, str] = field(default_factory=dict)
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "columns": [
                {"key": column.key, "label": column.label, "kind": column.kind}
                for column in self.columns
            ],
            "rows": self.rows,
            "filters": self.filters,
            "totals": self.totals,
            "row_count": len(self.rows),
            "truncated": self.truncated,
        }


def _decimal(value: Any) -> str:
    """A money or day figure as a string, never a float.

    Reports cross a JSON boundary and then a spreadsheet boundary, and a float
    would lose paise at either.
    """
    if value is None:
        return "0.00"
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _employee_scope(statement, *, employee_ids: list[int] | None, column):
    """Narrow a query to the employees a caller may see.

    ``None`` means no restriction - the caller holds a read-all permission. An
    empty list means they may see nobody, which has to return nothing rather
    than everything; getting that the wrong way round is how a scoped report
    leaks a whole company.
    """
    if employee_ids is None:
        return statement
    if not employee_ids:
        return statement.where(column.in_([-1]))
    return statement.where(column.in_(employee_ids))


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


def attendance_summary(
    session: Session,
    *,
    start: date,
    end: date,
    employee_ids: list[int] | None = None,
    department_id: int | None = None,
) -> ReportResult:
    """One row per employee for the period: how the days classified.

    ``payable_days`` is the sum of the fractions the attendance engine stored,
    which is the same figure payroll uses - so this report and a payslip cannot
    disagree about how much of a month somebody worked.
    """
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")

    statement = (
        select(
            Employee.id,
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            Department.name,
            func.count(AttendanceDay.id),
            func.coalesce(func.sum(AttendanceDay.payable_day_fraction), 0),
        )
        .select_from(Employee)
        .join(Department, Department.id == Employee.department_id, isouter=True)
        .join(
            AttendanceDay,
            (AttendanceDay.employee_id == Employee.id)
            & (AttendanceDay.work_date >= start)
            & (AttendanceDay.work_date <= end),
            isouter=True,
        )
        .group_by(
            Employee.id,
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            Department.name,
        )
        .order_by(Employee.employee_code)
        .limit(MAX_ROWS + 1)
    )
    statement = _employee_scope(statement, employee_ids=employee_ids, column=Employee.id)
    if department_id is not None:
        statement = statement.where(Employee.department_id == department_id)

    # The per-status counts are a second pass rather than eight CASE sums: it is
    # one extra query, and it keeps the status list in one place so a new status
    # cannot be silently omitted from the report.
    status_rows = session.execute(
        _employee_scope(
            select(
                AttendanceDay.employee_id,
                AttendanceDay.status,
                func.count(),
                func.coalesce(func.sum(AttendanceDay.overtime_minutes), 0),
                func.coalesce(func.sum(cast(AttendanceDay.is_late, Integer)), 0),
            )
            .where(AttendanceDay.work_date >= start, AttendanceDay.work_date <= end)
            .group_by(AttendanceDay.employee_id, AttendanceDay.status),
            employee_ids=employee_ids,
            column=AttendanceDay.employee_id,
        )
    ).all()

    by_employee: dict[int, dict[str, Any]] = {}
    for employee_id, status, count, overtime, late in status_rows:
        bucket = by_employee.setdefault(employee_id, {"overtime_minutes": 0, "late_days": 0})
        bucket[status] = count
        bucket["overtime_minutes"] += int(overtime or 0)
        bucket["late_days"] += int(late or 0)

    rows: list[dict[str, Any]] = []
    for record in session.execute(statement).all():
        employee_id = record[0]
        counts = by_employee.get(employee_id, {})
        rows.append(
            {
                "employee_code": record[1],
                "employee_name": " ".join(part for part in (record[2], record[3]) if part),
                "department": record[4],
                "days_recorded": record[5],
                "payable_days": _decimal(record[6]),
                "present": counts.get("present", 0),
                "half_day": counts.get("half_day", 0),
                "absent": counts.get("absent", 0),
                "on_leave": counts.get("on_leave", 0),
                "compoff": counts.get("compoff", 0),
                "holiday": counts.get("holiday", 0),
                "weekly_off": counts.get("weekly_off", 0),
                "unsettled": counts.get("incomplete", 0) + counts.get("missing_punch", 0),
                "late_days": counts.get("late_days", 0),
                "overtime_minutes": counts.get("overtime_minutes", 0),
            }
        )

    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]

    return ReportResult(
        code="attendance_summary",
        title="Attendance summary",
        description=(
            "One row per employee. Payable days are summed from the fractions the "
            "attendance engine stored, so this agrees with payroll by construction."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("department", "Department"),
            Column("days_recorded", "Days recorded", "number"),
            Column("payable_days", "Payable days", "days"),
            Column("present", "Present", "number"),
            Column("half_day", "Half days", "number"),
            Column("absent", "Absent", "number"),
            Column("on_leave", "On leave", "number"),
            Column("compoff", "Comp-off", "number"),
            Column("holiday", "Holidays", "number"),
            Column("weekly_off", "Weekly offs", "number"),
            Column("unsettled", "Unsettled", "number"),
            Column("late_days", "Late", "number"),
            Column("overtime_minutes", "Overtime (min)", "number"),
        ],
        rows=rows,
        filters={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "department_id": department_id,
        },
        totals={
            "payable_days": _decimal(sum(Decimal(row["payable_days"]) for row in rows)),
            "overtime_minutes": str(sum(row["overtime_minutes"] for row in rows)),
            "unsettled": str(sum(row["unsettled"] for row in rows)),
        },
        truncated=truncated,
    )


def attendance_register(
    session: Session,
    *,
    start: date,
    end: date,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Day by day, for the period. The detail behind the summary."""
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")
    if (end - start).days > 92:
        raise ValidationFailed(
            "The daily register covers at most 92 days at a time. Use the summary "
            "for a longer period."
        )

    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            AttendanceDay.work_date,
            AttendanceDay.day_type,
            AttendanceDay.status,
            AttendanceDay.first_in_at,
            AttendanceDay.last_out_at,
            AttendanceDay.net_minutes,
            AttendanceDay.late_minutes,
            AttendanceDay.early_exit_minutes,
            AttendanceDay.overtime_minutes,
            AttendanceDay.payable_day_fraction,
            AttendanceDay.is_manual_override,
        )
        .join(Employee, Employee.id == AttendanceDay.employee_id)
        .where(AttendanceDay.work_date >= start, AttendanceDay.work_date <= end)
        .order_by(Employee.employee_code, AttendanceDay.work_date)
        .limit(MAX_ROWS + 1)
    )
    statement = _employee_scope(
        statement, employee_ids=employee_ids, column=AttendanceDay.employee_id
    )

    records = session.execute(statement).all()
    rows = [
        {
            "employee_code": record[0],
            "employee_name": " ".join(part for part in (record[1], record[2]) if part),
            "work_date": record[3].isoformat(),
            "day_type": record[4],
            "status": record[5],
            "first_in": record[6].isoformat() if record[6] else None,
            "last_out": record[7].isoformat() if record[7] else None,
            "net_minutes": record[8],
            "late_minutes": record[9],
            "early_exit_minutes": record[10],
            "overtime_minutes": record[11],
            "payable_fraction": _decimal(record[12]),
            "manual_override": bool(record[13]),
        }
        for record in records[:MAX_ROWS]
    ]

    return ReportResult(
        code="attendance_register",
        title="Daily attendance register",
        description="Every recorded day in the period, as the engine classified it.",
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("work_date", "Date", "date"),
            Column("day_type", "Day type"),
            Column("status", "Status"),
            Column("first_in", "First in"),
            Column("last_out", "Last out"),
            Column("net_minutes", "Net minutes", "number"),
            Column("late_minutes", "Late (min)", "number"),
            Column("early_exit_minutes", "Early exit (min)", "number"),
            Column("overtime_minutes", "Overtime (min)", "number"),
            Column("payable_fraction", "Payable", "days"),
            Column("manual_override", "Pinned by HR", "boolean"),
        ],
        rows=rows,
        filters={"start": start.isoformat(), "end": end.isoformat()},
        truncated=len(records) > MAX_ROWS,
    )


def unsettled_days(
    session: Session,
    *,
    start: date,
    end: date,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Days the punches could not settle, and whether a correction is pending.

    Deliberately not called an absence report. These are days needing a
    decision, and the pending-correction column is the point: it distinguishes
    "nobody has looked" from "somebody has asked".
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            AttendanceDay.work_date,
            AttendanceDay.status,
            AttendanceDay.first_in_at,
            AttendanceDay.last_out_at,
            AttendanceDay.anomalies,
        )
        .join(Employee, Employee.id == AttendanceDay.employee_id)
        .where(
            AttendanceDay.work_date >= start,
            AttendanceDay.work_date <= end,
            AttendanceDay.status.in_(["incomplete", "missing_punch"]),
        )
        .order_by(AttendanceDay.work_date.desc(), Employee.employee_code)
        .limit(MAX_ROWS + 1)
    )
    statement = _employee_scope(
        statement, employee_ids=employee_ids, column=AttendanceDay.employee_id
    )
    records = session.execute(statement).all()

    pending = {
        (row[0], row[1])
        for row in session.execute(
            select(AttendanceRegularization.employee_id, AttendanceRegularization.work_date).where(
                AttendanceRegularization.work_date >= start,
                AttendanceRegularization.work_date <= end,
                AttendanceRegularization.status.in_(["pending_manager", "pending_hr"]),
            )
        ).all()
    }
    employee_by_code = {
        row[0]: row[1] for row in session.execute(select(Employee.employee_code, Employee.id)).all()
    }

    rows = [
        {
            "employee_code": record[0],
            "employee_name": " ".join(part for part in (record[1], record[2]) if part),
            "work_date": record[3].isoformat(),
            "status": record[4],
            "first_in": record[5].isoformat() if record[5] else None,
            "last_out": record[6].isoformat() if record[6] else None,
            "anomalies": ", ".join(record[7]) if record[7] else None,
            "correction_pending": (employee_by_code.get(record[0]), record[3]) in pending,
        }
        for record in records[:MAX_ROWS]
    ]

    return ReportResult(
        code="unsettled_days",
        title="Unsettled attendance",
        description=(
            "Days the punches could not settle. Not absences: each needs a "
            "correction or a decision, and none has been treated as unpaid."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("work_date", "Date", "date"),
            Column("status", "Status"),
            Column("first_in", "First in"),
            Column("last_out", "Last out"),
            Column("anomalies", "Anomalies"),
            Column("correction_pending", "Correction pending", "boolean"),
        ],
        rows=rows,
        filters={"start": start.isoformat(), "end": end.isoformat()},
        totals={"unsettled": str(len(rows))},
        truncated=len(records) > MAX_ROWS,
    )


def overtime_report(
    session: Session,
    *,
    start: date,
    end: date,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Overtime minutes by employee.

    Minutes only. No amount is derived, because the multiplier is a legal rate
    this project has not been given a verified reference for - see
    ``docs/open-items.md``. A company paying overtime posts it as a payroll
    adjustment, which keeps the figure attributable.
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            func.count(AttendanceDay.id),
            func.coalesce(func.sum(AttendanceDay.overtime_minutes), 0),
        )
        .join(Employee, Employee.id == AttendanceDay.employee_id)
        .where(
            AttendanceDay.work_date >= start,
            AttendanceDay.work_date <= end,
            AttendanceDay.overtime_minutes > 0,
        )
        .group_by(Employee.employee_code, Employee.first_name, Employee.last_name)
        .order_by(func.coalesce(func.sum(AttendanceDay.overtime_minutes), 0).desc())
        .limit(MAX_ROWS)
    )
    statement = _employee_scope(
        statement, employee_ids=employee_ids, column=AttendanceDay.employee_id
    )
    records = session.execute(statement).all()

    rows = [
        {
            "employee_code": record[0],
            "employee_name": " ".join(part for part in (record[1], record[2]) if part),
            "days_with_overtime": record[3],
            "overtime_minutes": int(record[4] or 0),
            "overtime_hours": str(
                (Decimal(int(record[4] or 0)) / Decimal(60)).quantize(Decimal("0.01"))
            ),
        }
        for record in records
    ]

    return ReportResult(
        code="overtime",
        title="Overtime",
        description=(
            "Minutes recorded by the attendance engine. No amount is derived: the "
            "overtime multiplier is a legal rate that has not been verified here."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("days_with_overtime", "Days", "number"),
            Column("overtime_minutes", "Minutes", "number"),
            Column("overtime_hours", "Hours", "number"),
        ],
        rows=rows,
        filters={"start": start.isoformat(), "end": end.isoformat()},
        totals={"overtime_minutes": str(sum(row["overtime_minutes"] for row in rows))},
    )


# ---------------------------------------------------------------------------
# Leave and comp-off
# ---------------------------------------------------------------------------


def leave_balances(
    session: Session,
    *,
    as_of: date,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Balances as of a date, summed from the ledger.

    The ledger **is** the balance - there is no stored total to drift from it -
    so this report is a sum by transaction type rather than a lookup, and it
    splits the movements out so a balance can be explained rather than merely
    asserted.
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            LeaveType.code,
            func.coalesce(
                func.sum(
                    case(
                        (LeaveLedgerEntry.txn_type == "opening", LeaveLedgerEntry.days),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (LeaveLedgerEntry.txn_type == "accrual", LeaveLedgerEntry.days),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (LeaveLedgerEntry.txn_type == "deduction", LeaveLedgerEntry.days),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(LeaveLedgerEntry.days), 0),
        )
        .join(Employee, Employee.id == LeaveLedgerEntry.employee_id)
        .join(LeaveType, LeaveType.id == LeaveLedgerEntry.leave_type_id)
        .where(LeaveLedgerEntry.effective_date <= as_of)
        .group_by(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            LeaveType.code,
        )
        .order_by(Employee.employee_code, LeaveType.code)
        .limit(MAX_ROWS)
    )
    statement = _employee_scope(
        statement, employee_ids=employee_ids, column=LeaveLedgerEntry.employee_id
    )
    records = session.execute(statement).all()

    rows = [
        {
            "employee_code": record[0],
            "employee_name": " ".join(part for part in (record[1], record[2]) if part),
            "leave_type": record[3],
            "opening": _decimal(record[4]),
            "accrued": _decimal(record[5]),
            "used": _decimal(abs(Decimal(str(record[6])))),
            "balance": _decimal(record[7]),
        }
        for record in records
    ]

    return ReportResult(
        code="leave_balances",
        title="Leave balances",
        description=(
            "Summed from the append-only ledger as of the chosen date. There is no "
            "stored balance for this to disagree with."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("leave_type", "Type"),
            Column("opening", "Opening", "days"),
            Column("accrued", "Accrued", "days"),
            Column("used", "Used", "days"),
            Column("balance", "Balance", "days"),
        ],
        rows=rows,
        filters={"as_of": as_of.isoformat()},
        totals={"balance": _decimal(sum(Decimal(row["balance"]) for row in rows))},
    )


def compoff_position(
    session: Session, *, as_of: date, employee_ids: list[int] | None = None
) -> ReportResult:
    """Comp-off credits, what is left of each, and when it expires.

    Credit by credit rather than as a total, because each one expires on its own
    date - a single number would hide a day about to be lost.
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            CompoffCredit.earned_date,
            CompoffCredit.credit_days,
            CompoffCredit.allocated_days,
            CompoffCredit.expiry_date,
            CompoffCredit.status,
            CompoffCredit.source,
        )
        .join(Employee, Employee.id == CompoffCredit.employee_id)
        .order_by(Employee.employee_code, CompoffCredit.earned_date)
        .limit(MAX_ROWS)
    )
    statement = _employee_scope(
        statement, employee_ids=employee_ids, column=CompoffCredit.employee_id
    )
    records = session.execute(statement).all()

    rows = []
    for record in records:
        remaining = Decimal(str(record[4])) - Decimal(str(record[5]))
        expires = record[6]
        rows.append(
            {
                "employee_code": record[0],
                "employee_name": " ".join(part for part in (record[1], record[2]) if part),
                "earned_date": record[3].isoformat(),
                "credit_days": _decimal(record[4]),
                "used_days": _decimal(record[5]),
                "remaining_days": _decimal(remaining),
                "expires_on": expires.isoformat() if expires else None,
                "days_to_expiry": (expires - as_of).days if expires else None,
                "status": record[7],
                "source": record[8],
            }
        )

    return ReportResult(
        code="compoff_position",
        title="Comp-off position",
        description=(
            "Credit by credit, with what is left of each and when it expires. A "
            "single total would hide a day about to be lost."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("earned_date", "Earned", "date"),
            Column("credit_days", "Credit", "days"),
            Column("used_days", "Used", "days"),
            Column("remaining_days", "Remaining", "days"),
            Column("expires_on", "Expires", "date"),
            Column("days_to_expiry", "Days left", "number"),
            Column("status", "Status"),
            Column("source", "Source"),
        ],
        rows=rows,
        filters={"as_of": as_of.isoformat()},
        totals={
            "remaining_days": _decimal(
                sum(Decimal(row["remaining_days"]) for row in rows if row["status"] == "available")
            )
        },
    )


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------


def employee_master(
    session: Session,
    *,
    include_exited: bool = False,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """The employee register.

    Deliberately without bank details, PAN or Aadhaar. Those are encrypted at
    rest and exist behind their own permissions; putting them in an exportable
    report would make a spreadsheet of them the easiest thing in the system to
    produce, and the easiest to leak.
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            Employee.work_email,
            Department.name,
            Designation.name,
            WorkLocation.name,
            Employee.employment_type,
            Employee.employment_status,
            Employee.date_of_joining,
            Employee.date_of_exit,
            Employee.reporting_manager_id,
        )
        .select_from(Employee)
        .join(Department, Department.id == Employee.department_id, isouter=True)
        .join(Designation, Designation.id == Employee.designation_id, isouter=True)
        .join(WorkLocation, WorkLocation.id == Employee.work_location_id, isouter=True)
        .order_by(Employee.employee_code)
        .limit(MAX_ROWS)
    )
    statement = _employee_scope(statement, employee_ids=employee_ids, column=Employee.id)
    if not include_exited:
        statement = statement.where(Employee.employment_status != "exited")

    records = session.execute(statement).all()
    managers = {
        row[0]: row[1] for row in session.execute(select(Employee.id, Employee.employee_code)).all()
    }

    rows = [
        {
            "employee_code": record[0],
            "employee_name": " ".join(part for part in (record[1], record[2]) if part),
            "work_email": record[3],
            "department": record[4],
            "designation": record[5],
            "work_location": record[6],
            "employment_type": record[7],
            "employment_status": record[8],
            "date_of_joining": record[9].isoformat() if record[9] else None,
            "date_of_exit": record[10].isoformat() if record[10] else None,
            "reports_to": managers.get(record[11]),
        }
        for record in records
    ]

    return ReportResult(
        code="employee_master",
        title="Employee register",
        description=(
            "Identity and employment fields only. Bank details, PAN and Aadhaar are "
            "deliberately absent: they are encrypted at rest and sit behind their "
            "own permissions, and an exportable report of them would be the easiest "
            "thing in the system to leak."
        ),
        columns=[
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("work_email", "Work email"),
            Column("department", "Department"),
            Column("designation", "Designation"),
            Column("work_location", "Location"),
            Column("employment_type", "Type"),
            Column("employment_status", "Status"),
            Column("date_of_joining", "Joined", "date"),
            Column("date_of_exit", "Exited", "date"),
            Column("reports_to", "Reports to"),
        ],
        rows=rows,
        filters={"include_exited": include_exited},
        totals={"employees": str(len(rows))},
    )


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------


def _run_or_fail(session: Session, run_id: int) -> PayrollRun:
    run = session.scalar(select(PayrollRun).where(PayrollRun.id == run_id))
    if run is None:
        raise NotFound("Payroll run not found.")
    return run


def payroll_register(session: Session, *, run_id: int) -> ReportResult:
    """A run as a flat register, one row per employee with every component.

    The columns are discovered from the lines actually present, so a company's
    own components appear without this report knowing their names.
    """
    run = _run_or_fail(session, run_id)
    period = session.get(PayrollPeriod, run.period_id)

    line_rows = session.execute(
        select(
            Payslip.id,
            Payslip.employee_code,
            Payslip.employee_name,
            Payslip.department_name,
            Payslip.payable_days,
            Payslip.lop_days,
            Payslip.gross_earnings,
            Payslip.total_deductions,
            Payslip.net_pay,
            PayslipLine.line_code,
            PayslipLine.label,
            PayslipLine.component_type,
            PayslipLine.amount,
        )
        .join(PayslipLine, PayslipLine.payslip_id == Payslip.id)
        .where(Payslip.run_id == run_id)
        .order_by(Payslip.employee_code, PayslipLine.sequence)
    ).all()

    by_payslip: dict[int, dict[str, Any]] = {}
    labels: dict[str, str] = {}
    order: list[str] = []
    for record in line_rows:
        row = by_payslip.setdefault(
            record[0],
            {
                "employee_code": record[1],
                "employee_name": record[2],
                "department": record[3],
                "payable_days": _decimal(record[4]),
                "lop_days": _decimal(record[5]),
                "gross_earnings": _decimal(record[6]),
                "total_deductions": _decimal(record[7]),
                "net_pay": _decimal(record[8]),
            },
        )
        code = record[9]
        row[code] = _decimal(record[12])
        if code not in labels:
            labels[code] = record[10]
            order.append(code)

    rows = sorted(by_payslip.values(), key=lambda item: item["employee_code"])
    for row in rows:
        for code in order:
            row.setdefault(code, "0.00")

    columns = [
        Column("employee_code", "Code"),
        Column("employee_name", "Employee"),
        Column("department", "Department"),
        Column("payable_days", "Payable days", "days"),
        Column("lop_days", "LOP days", "days"),
        *[Column(code, labels[code], "money") for code in order],
        Column("gross_earnings", "Gross", "money"),
        Column("total_deductions", "Deductions", "money"),
        Column("net_pay", "Net pay", "money"),
    ]

    totals = {
        key: _decimal(sum(Decimal(row[key]) for row in rows))
        for key in ["gross_earnings", "total_deductions", "net_pay", *order]
    }

    return ReportResult(
        code="payroll_register",
        title=f"Payroll register - run #{run_id}",
        description=(
            "One row per employee with every component. This is the data a bank "
            "transfer and a statutory return are prepared from; neither is "
            "performed by this system."
        ),
        columns=columns,
        rows=rows,
        filters={
            "run_id": run_id,
            "period": f"{period.year}-{period.month:02d}" if period else None,
            "status": run.status,
            "rule_sets_used": run.rule_set_snapshot or {},
        },
        totals=totals,
    )


def deductions_summary(session: Session, *, run_id: int) -> ReportResult:
    """Per-deduction totals for a run, with the rule set behind each.

    What a statutory return is prepared from. The rule-set column is what makes
    the figure traceable to a source somebody verified.
    """
    _run_or_fail(session, run_id)
    records = session.execute(
        select(
            PayslipLine.line_code,
            PayslipLine.label,
            PayslipLine.component_type,
            func.count(),
            func.coalesce(func.sum(PayslipLine.amount), 0),
            PayslipLine.statutory_rule_set_id,
        )
        .join(Payslip, Payslip.id == PayslipLine.payslip_id)
        .where(
            Payslip.run_id == run_id,
            PayslipLine.component_type.in_(["deduction", "employer_contribution"]),
        )
        .group_by(
            PayslipLine.line_code,
            PayslipLine.label,
            PayslipLine.component_type,
            PayslipLine.statutory_rule_set_id,
        )
        .order_by(PayslipLine.component_type, PayslipLine.line_code)
    ).all()

    rows = [
        {
            "line_code": record[0],
            "label": record[1],
            "kind": record[2],
            "employee_count": record[3],
            "total_amount": _decimal(record[4]),
            "rule_set_id": record[5],
        }
        for record in records
    ]

    return ReportResult(
        code="deductions_summary",
        title=f"Deductions and contributions - run #{run_id}",
        description=(
            "Per-line totals, with the statutory rule set each came from. The data "
            "a return is prepared from; no filing is performed."
        ),
        columns=[
            Column("line_code", "Line"),
            Column("label", "Description"),
            Column("kind", "Kind"),
            Column("employee_count", "Employees", "number"),
            Column("total_amount", "Total", "money"),
            Column("rule_set_id", "Rule set", "number"),
        ],
        rows=rows,
        filters={"run_id": run_id},
        totals={"total_amount": _decimal(sum(Decimal(row["total_amount"]) for row in rows))},
    )


def payslip_index(
    session: Session,
    *,
    year: int | None = None,
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Every payslip, with its run's status and whether it was released."""
    statement = (
        select(
            Payslip.id,
            Payslip.employee_code,
            Payslip.employee_name,
            PayrollPeriod.year,
            PayrollPeriod.month,
            PayrollRun.id,
            PayrollRun.status,
            Payslip.gross_earnings,
            Payslip.total_deductions,
            Payslip.net_pay,
            Payslip.published_at,
        )
        .join(PayrollRun, PayrollRun.id == Payslip.run_id)
        .join(PayrollPeriod, PayrollPeriod.id == PayrollRun.period_id)
        .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc(), Payslip.employee_code)
        .limit(MAX_ROWS)
    )
    statement = _employee_scope(statement, employee_ids=employee_ids, column=Payslip.employee_id)
    if year is not None:
        statement = statement.where(PayrollPeriod.year == year)

    records = session.execute(statement).all()
    rows = [
        {
            "payslip_id": record[0],
            "employee_code": record[1],
            "employee_name": record[2],
            "period": f"{record[3]}-{record[4]:02d}",
            "run_id": record[5],
            "run_status": record[6],
            "gross_earnings": _decimal(record[7]),
            "total_deductions": _decimal(record[8]),
            "net_pay": _decimal(record[9]),
            "released": record[10] is not None,
        }
        for record in records
    ]

    return ReportResult(
        code="payslip_index",
        title="Payslips",
        description="Every payslip, its run's status, and whether it has been released.",
        columns=[
            Column("payslip_id", "Payslip", "number"),
            Column("employee_code", "Code"),
            Column("employee_name", "Employee"),
            Column("period", "Period"),
            Column("run_id", "Run", "number"),
            Column("run_status", "Run status"),
            Column("gross_earnings", "Gross", "money"),
            Column("total_deductions", "Deductions", "money"),
            Column("net_pay", "Net pay", "money"),
            Column("released", "Released", "boolean"),
        ],
        rows=rows,
        filters={"year": year},
        totals={"net_pay": _decimal(sum(Decimal(row["net_pay"]) for row in rows))},
    )


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportDef:
    code: str
    title: str
    description: str
    permission: str
    #: Which filters the report accepts, so the interface can build its form
    #: from the backend rather than hard-coding a second copy of this list.
    parameters: list[str]


REPORTS: tuple[ReportDef, ...] = (
    ReportDef(
        "attendance_summary",
        "Attendance summary",
        "Per employee for a period: how the days classified, and payable days.",
        "report.attendance",
        ["start", "end", "department_id"],
    ),
    ReportDef(
        "attendance_register",
        "Daily attendance register",
        "Every recorded day in the period, as the engine classified it.",
        "report.attendance",
        ["start", "end"],
    ),
    ReportDef(
        "unsettled_days",
        "Unsettled attendance",
        "Days the punches could not settle, and whether a correction is pending.",
        "report.attendance",
        ["start", "end"],
    ),
    ReportDef(
        "overtime",
        "Overtime",
        "Minutes recorded. No amount is derived; the multiplier is unverified.",
        "report.attendance",
        ["start", "end"],
    ),
    ReportDef(
        "leave_balances",
        "Leave balances",
        "Summed from the append-only ledger as of a date.",
        "report.leave",
        ["as_of"],
    ),
    ReportDef(
        "compoff_position",
        "Comp-off position",
        "Credit by credit, with what is left and when it expires.",
        "report.leave",
        ["as_of"],
    ),
    ReportDef(
        "employee_master",
        "Employee register",
        "Identity and employment fields. No bank, PAN or Aadhaar data.",
        "report.employee",
        ["include_exited"],
    ),
    ReportDef(
        "payroll_register",
        "Payroll register",
        "A run as a flat register, one row per employee with every component.",
        "report.payroll",
        ["run_id"],
    ),
    ReportDef(
        "deductions_summary",
        "Deductions and contributions",
        "Per-line totals for a run, with the rule set behind each.",
        "report.payroll",
        ["run_id"],
    ),
    ReportDef(
        "payslip_index",
        "Payslips",
        "Every payslip, its run's status, and whether it was released.",
        "report.payroll",
        ["year"],
    ),
)

REPORTS_BY_CODE: dict[str, ReportDef] = {item.code: item for item in REPORTS}


def run_report(
    session: Session,
    code: str,
    *,
    parameters: dict[str, Any],
    employee_ids: list[int] | None = None,
) -> ReportResult:
    """Dispatch to one report by code.

    One entry point so the JSON endpoint, the CSV export and the Excel export
    cannot drift apart: they all call this, and a new report is added in one
    place.
    """
    definition = REPORTS_BY_CODE.get(code)
    if definition is None:
        raise NotFound(f"No report called {code!r}.")

    today = date.today()
    start = parameters.get("start") or today.replace(day=1)
    end = parameters.get("end") or today
    as_of = parameters.get("as_of") or today

    if code == "attendance_summary":
        return attendance_summary(
            session,
            start=start,
            end=end,
            employee_ids=employee_ids,
            department_id=parameters.get("department_id"),
        )
    if code == "attendance_register":
        return attendance_register(session, start=start, end=end, employee_ids=employee_ids)
    if code == "unsettled_days":
        return unsettled_days(session, start=start, end=end, employee_ids=employee_ids)
    if code == "overtime":
        return overtime_report(session, start=start, end=end, employee_ids=employee_ids)
    if code == "leave_balances":
        return leave_balances(session, as_of=as_of, employee_ids=employee_ids)
    if code == "compoff_position":
        return compoff_position(session, as_of=as_of, employee_ids=employee_ids)
    if code == "employee_master":
        return employee_master(
            session,
            include_exited=bool(parameters.get("include_exited")),
            employee_ids=employee_ids,
        )
    if code in ("payroll_register", "deductions_summary"):
        run_id = parameters.get("run_id")
        if not run_id:
            raise ValidationFailed(
                f"The {definition.title.lower()} needs a payroll run id.",
                details=[{"field": "run_id", "message": "Required."}],
            )
        if code == "payroll_register":
            return payroll_register(session, run_id=int(run_id))
        return deductions_summary(session, run_id=int(run_id))
    if code == "payslip_index":
        return payslip_index(session, year=parameters.get("year"), employee_ids=employee_ids)

    raise NotFound(f"No report called {code!r}.")  # pragma: no cover - unreachable


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


def to_csv(result: ReportResult) -> bytes:
    """CSV, with a header row and the filters recorded at the top.

    Written with a UTF-8 BOM. Excel reads a BOM-less UTF-8 CSV as the system
    code page and mangles any non-ASCII name in it, and an employee's name is
    exactly the kind of thing that would be mangled.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")

    writer.writerow([result.title])
    if result.filters:
        writer.writerow(
            ["Filters:"]
            + [f"{key}={value}" for key, value in result.filters.items() if value is not None]
        )
    writer.writerow([])
    writer.writerow([column.label for column in result.columns])

    for row in result.rows:
        writer.writerow([_csv_cell(row.get(column.key)) for column in result.columns])

    if result.totals:
        writer.writerow([])
        writer.writerow(["Totals:"] + [f"{key}={value}" for key, value in result.totals.items()])
    if result.truncated:
        writer.writerow([])
        writer.writerow([f"Truncated at {MAX_ROWS} rows. Narrow the filters."])

    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def to_xlsx(result: ReportResult) -> bytes:
    """A real .xlsx, not a CSV with a misleading extension.

    Numbers are written as numbers so a spreadsheet can total them, but money
    and day quantities go through ``Decimal`` first and are written with an
    explicit two-decimal format - openpyxl stores them as floats, which is
    acceptable in a spreadsheet nobody computes payroll from, while the system
    of record keeps its exact values.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = result.code[:31]

    sheet.append([result.title])
    sheet["A1"].font = Font(bold=True, size=13)
    if result.filters:
        sheet.append(
            [
                "Filters: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in result.filters.items()
                    if value is not None and not isinstance(value, dict)
                )
            ]
        )
    sheet.append([])

    header_row = sheet.max_row + 1
    sheet.append([column.label for column in result.columns])
    for index in range(1, len(result.columns) + 1):
        cell = sheet.cell(row=header_row, column=index)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row in result.rows:
        sheet.append([_xlsx_cell(row.get(column.key), column.kind) for column in result.columns])

    for index, column in enumerate(result.columns, start=1):
        letter = get_column_letter(index)
        width = max(len(column.label) + 2, 12)
        sheet.column_dimensions[letter].width = min(width, 40)
        if column.kind in ("money", "days"):
            for row_index in range(header_row + 1, sheet.max_row + 1):
                sheet.cell(row=row_index, column=index).number_format = "0.00"

    if result.totals:
        sheet.append([])
        sheet.append(["Totals"] + [f"{key}={value}" for key, value in result.totals.items()])
    if result.truncated:
        sheet.append([])
        sheet.append([f"Truncated at {MAX_ROWS} rows. Narrow the filters."])

    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _xlsx_cell(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if kind in ("money", "days"):
        try:
            return float(Decimal(str(value)))
        except Exception:  # pragma: no cover - defensive
            return str(value)
    if kind == "number":
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value)
    return str(value)
