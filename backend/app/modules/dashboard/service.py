"""Dashboard aggregates.

Every figure here comes from a real query. Where a metric depends on a module
that has not been built yet - today's attendance, pending leave approvals - the
response reports it as unavailable with the milestone that will deliver it,
rather than returning a zero that reads as "nobody is absent today".
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.employees.models import Employee
from app.modules.employees.service import reports_needing_reassignment, team_employee_ids
from app.modules.org.models import Department, WorkLocation
from app.modules.settings import service as settings_service

#: Metrics whose data source arrives in a later milestone. Reported explicitly
#: so the UI can show "not yet available" instead of a misleading zero.
PENDING_METRICS: dict[str, str] = {
    "on_leave_today": "Milestone 5",
    "leave_balances": "Milestone 5",
    "pending_leave_approvals": "Milestone 5",
    "pending_compoff_verifications": "Milestone 6",
    "compoff_balance": "Milestone 6",
    "upcoming_holidays": "Milestone 6",
    "unread_notifications": "Milestone 7",
    "announcements": "Milestone 7",
    "payroll_status": "Milestone 8",
    "available_payslips": "Milestone 8",
}


def hr_dashboard(session: Session) -> dict[str, Any]:
    """Counts and lists for the HR/Admin overview."""
    today = date.today()

    total_active = (
        session.scalar(
            select(func.count()).select_from(Employee).where(Employee.employment_status != "exited")
        )
        or 0
    )

    by_status = {
        status: count
        for status, count in session.execute(
            select(Employee.employment_status, func.count())
            .group_by(Employee.employment_status)
            .order_by(Employee.employment_status)
        ).all()
    }

    by_type = {
        employment_type: count
        for employment_type, count in session.execute(
            select(Employee.employment_type, func.count())
            .where(Employee.employment_status != "exited")
            .group_by(Employee.employment_type)
        ).all()
    }

    by_department = [
        {"department_id": dept_id, "department_name": name or "Unassigned", "count": count}
        for dept_id, name, count in session.execute(
            select(Employee.department_id, Department.name, func.count())
            .outerjoin(Department, Department.id == Employee.department_id)
            .where(Employee.employment_status != "exited")
            .group_by(Employee.department_id, Department.name)
            .order_by(func.count().desc())
        ).all()
    ]

    by_location = [
        {"work_location_id": loc_id, "work_location_name": name or "Unassigned", "count": count}
        for loc_id, name, count in session.execute(
            select(Employee.work_location_id, WorkLocation.name, func.count())
            .outerjoin(WorkLocation, WorkLocation.id == Employee.work_location_id)
            .where(Employee.employment_status != "exited")
            .group_by(Employee.work_location_id, WorkLocation.name)
            .order_by(func.count().desc())
        ).all()
    ]

    recent_joiners = [
        {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "date_of_joining": employee.date_of_joining.isoformat(),
        }
        for employee in session.scalars(
            select(Employee)
            .where(
                Employee.date_of_joining >= today - timedelta(days=30),
                Employee.employment_status != "exited",
            )
            .order_by(Employee.date_of_joining.desc())
            .limit(10)
        ).all()
    ]

    upcoming_joiners = [
        {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "date_of_joining": employee.date_of_joining.isoformat(),
        }
        for employee in session.scalars(
            select(Employee)
            .where(Employee.date_of_joining > today, Employee.employment_status != "exited")
            .order_by(Employee.date_of_joining)
            .limit(10)
        ).all()
    ]

    # Probation periods ending soon need a confirmation decision, so they are
    # surfaced rather than left to be noticed.
    probation_ending = [
        {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "probation_end_date": employee.probation_end_date.isoformat()
            if employee.probation_end_date
            else None,
        }
        for employee in session.scalars(
            select(Employee)
            .where(
                Employee.employment_status == "probation",
                Employee.probation_end_date.isnot(None),
                Employee.probation_end_date <= today + timedelta(days=30),
            )
            .order_by(Employee.probation_end_date)
            .limit(20)
        ).all()
    ]

    # Employees with no usable approver. Their leave requests would stall, so
    # this is a data-quality problem worth putting on the dashboard.
    orphaned = [
        {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        }
        for employee in reports_needing_reassignment(session)[:20]
    ]

    accounts_unlinked = (
        session.scalar(
            select(func.count())
            .select_from(Employee)
            .outerjoin(User, User.employee_id == Employee.id)
            .where(Employee.employment_status != "exited", User.id.is_(None))
        )
        or 0
    )

    from app.modules.attendance import service as attendance_service

    return {
        "as_of": today.isoformat(),
        "headcount": {
            "active_total": total_active,
            "by_status": by_status,
            "by_employment_type": by_type,
            "by_department": by_department,
            "by_work_location": by_location,
        },
        # Real figures from Milestone 3. `not_yet_computed` is reported
        # separately from `absent`: an employee with no row yet has not been
        # marked absent, and collapsing the two would misreport absence.
        "attendance_today": attendance_service.today_overview(session),
        "joiners": {"recent": recent_joiners, "upcoming": upcoming_joiners},
        "attention": {
            "probation_ending_soon": probation_ending,
            "reports_needing_manager": orphaned,
            "employees_without_account": accounts_unlinked,
        },
        "configuration": settings_service.configuration_health(session),
        "not_yet_available": PENDING_METRICS,
    }


def employee_dashboard(session: Session, user: User) -> dict[str, Any]:
    """The signed-in employee's own overview."""
    today = date.today()

    if user.employee_id is None:
        return {
            "as_of": today.isoformat(),
            "employee": None,
            "message": (
                "Your account is not linked to an employee record yet. "
                "Contact HR to have it linked."
            ),
            "not_yet_available": PENDING_METRICS,
        }

    employee = session.get(Employee, user.employee_id)
    if employee is None:  # pragma: no cover - defensive
        return {
            "as_of": today.isoformat(),
            "employee": None,
            "not_yet_available": PENDING_METRICS,
        }

    manager = (
        session.get(Employee, employee.reporting_manager_id)
        if employee.reporting_manager_id
        else None
    )
    location = (
        session.get(WorkLocation, employee.work_location_id) if employee.work_location_id else None
    )
    department = session.get(Department, employee.department_id) if employee.department_id else None

    direct_reports = (
        session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.reporting_manager_id == employee.id,
                Employee.employment_status != "exited",
            )
        )
        or 0
    )

    tenure_days = (today - employee.date_of_joining).days

    from app.modules.attendance import service as attendance_service

    # Month to date, which is the period an employee actually asks about.
    month_start = today.replace(day=1)
    attendance = attendance_service.month_summary(
        session, employee_id=employee.id, start=month_start, end=today
    )
    punch_state = attendance_service.current_state(session, employee.id)

    return {
        "as_of": today.isoformat(),
        "attendance_this_month": attendance,
        "punch_state": {
            "work_date": punch_state["work_date"].isoformat(),
            "is_punched_in": punch_state["is_punched_in"],
            "next_punch": punch_state["next_punch"],
            "worked_minutes_so_far": punch_state["worked_minutes_so_far"],
            "shift_code": punch_state["shift_code"],
            "shift_start": punch_state["shift_start"],
            "shift_end": punch_state["shift_end"],
            "has_shift_assigned": punch_state["has_shift_assigned"],
            "status": punch_state["status"],
        },
        "employee": {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
            "department_name": department.name if department else None,
            "work_location_name": location.name if location else None,
            "work_location_timezone": location.timezone if location else None,
            "employment_status": employee.employment_status,
            "employment_type": employee.employment_type,
            "date_of_joining": employee.date_of_joining.isoformat(),
            "tenure_days": max(tenure_days, 0),
            "is_pre_joining": tenure_days < 0,
            "probation_end_date": employee.probation_end_date.isoformat()
            if employee.probation_end_date
            else None,
            "manager_name": manager.full_name if manager else None,
            "manager_code": manager.employee_code if manager else None,
        },
        "team": {
            "direct_reports": direct_reports,
            "total_reports": len(team_employee_ids(session, employee.id)),
            "is_manager": direct_reports > 0,
        },
        "not_yet_available": PENDING_METRICS,
    }
