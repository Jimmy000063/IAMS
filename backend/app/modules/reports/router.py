"""Report endpoints, with CSV and Excel export.

One dispatcher, three representations
-------------------------------------
``GET /reports/{code}`` returns JSON and ``GET /reports/{code}/export`` returns
a file, both from the same :func:`service.run_report` call. A column added to a
report therefore appears in all three outputs without being declared again, and
the export cannot quietly disagree with what was on screen.

Scope, not just permission
--------------------------
Holding ``report.attendance`` says you may run attendance reports; it does not
say whose attendance. Every report is narrowed to the employees the caller may
already see - a manager's reports cover their own team, and HR's cover
everybody. A report is a different view of existing data, never a wider one.

Exports are audited
-------------------
A spreadsheet leaves the system and stops being access-controlled, so every
export records who took what, with which filters. That is the only thing that
makes an exported file traceable afterwards.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import NotFound, PermissionDenied
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.employees.models import Employee
from app.modules.reports import service
from app.modules.reports.schemas import (
    ReportCatalogueEntry,
    ReportResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])

#: Which permission widens a report from "my team" to "everybody", per family.
#: Read-all for the underlying data, not for reports: a report must not be a
#: back door to records the caller cannot open directly.
SCOPE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "report.attendance": ("attendance.read_all",),
    "report.leave": ("leave.read_all", "compoff.read_all"),
    "report.employee": ("employee.read",),
    "report.payroll": ("payslip.read_all", "payroll.read"),
}


def _visible_employee_ids(session: DbSession, user: User, *, permission: str) -> list[int] | None:
    """The employees this caller may include. None means everybody.

    A manager with no reports sees only themselves, which is an empty-ish list
    rather than None - and the service treats an empty list as "nobody", never
    as "no filter".
    """
    if any(user.has_permission(code) for code in SCOPE_PERMISSIONS.get(permission, ())):
        return None

    visible: set[int] = set()
    if user.employee_id is not None:
        visible.add(user.employee_id)
        visible |= employees_service.team_employee_ids(session, user.employee_id)
    return sorted(visible)


def _parameters(
    *,
    start: date | None,
    end: date | None,
    as_of: date | None,
    department_id: int | None,
    run_id: int | None,
    year: int | None,
    include_exited: bool,
) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "as_of": as_of,
        "department_id": department_id,
        "run_id": run_id,
        "year": year,
        "include_exited": include_exited,
    }


def _definition_or_404(code: str) -> service.ReportDef:
    definition = service.REPORTS_BY_CODE.get(code)
    if definition is None:
        raise NotFound(f"No report called {code!r}.")
    return definition


@router.get(
    "",
    response_model=list[ReportCatalogueEntry],
    summary="The reports this account can run",
)
def catalogue(
    user: CurrentUser,
    _dep: Annotated[
        User,
        Depends(
            require_any("report.attendance", "report.leave", "report.employee", "report.payroll")
        ),
    ],
) -> list[ReportCatalogueEntry]:
    """Filtered to what the caller may actually run.

    The parameter list comes from the backend so the interface builds its form
    from this rather than keeping a second copy of the filters.
    """
    return [
        ReportCatalogueEntry(
            code=item.code,
            title=item.title,
            description=item.description,
            permission=item.permission,
            parameters=item.parameters,
            scope=(
                "all"
                if any(
                    user.has_permission(code) for code in SCOPE_PERMISSIONS.get(item.permission, ())
                )
                else "team"
            ),
        )
        for item in service.REPORTS
        if user.has_permission(item.permission)
    ]


@router.get(
    "/{code}",
    response_model=ReportResponse,
    summary="Run a report",
    responses={404: {"description": "No report with that code"}},
)
def run(
    code: str,
    session: DbSession,
    user: CurrentUser,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
    department_id: Annotated[int | None, Query()] = None,
    run_id: Annotated[int | None, Query()] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    include_exited: bool = False,
) -> ReportResponse:
    """Run one report and return its columns and rows.

    Permission is checked against the report's own requirement, and the result
    is narrowed to the employees the caller may see - so two people running the
    same report legitimately get different row counts.
    """
    definition = _definition_or_404(code)
    if not user.has_permission(definition.permission):
        raise PermissionDenied(
            f"Running the {definition.title.lower()} needs {definition.permission}."
        )

    result = service.run_report(
        session,
        code,
        parameters=_parameters(
            start=start,
            end=end,
            as_of=as_of,
            department_id=department_id,
            run_id=run_id,
            year=year,
            include_exited=include_exited,
        ),
        employee_ids=_visible_employee_ids(session, user, permission=definition.permission),
    )
    return ReportResponse(**result.as_dict())


@router.get(
    "/{code}/export",
    summary="Export a report as CSV or Excel",
    response_class=Response,
    responses={
        200: {
            "content": {
                "text/csv": {},
                ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): {},
            },
            "description": "The report as a file",
        }
    },
)
def export(
    code: str,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("report.export"))],
    file_format: Annotated[str, Query(alias="format", pattern="^(csv|xlsx)$")] = "csv",
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    as_of: Annotated[date | None, Query()] = None,
    department_id: Annotated[int | None, Query()] = None,
    run_id: Annotated[int | None, Query()] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    include_exited: bool = False,
) -> Response:
    """The same data as a file, and the export is recorded.

    ``report.export`` is a separate permission from running a report: reading
    figures on screen and walking out with a spreadsheet of them are different
    acts, and the second one leaves the system's access controls behind. Every
    export is audited with the filters used, which is the only thing that makes
    a file traceable later.
    """
    definition = _definition_or_404(code)
    if not user.has_permission(definition.permission):
        raise PermissionDenied(
            f"Exporting the {definition.title.lower()} needs {definition.permission}."
        )

    parameters = _parameters(
        start=start,
        end=end,
        as_of=as_of,
        department_id=department_id,
        run_id=run_id,
        year=year,
        include_exited=include_exited,
    )
    result = service.run_report(
        session,
        code,
        parameters=parameters,
        employee_ids=_visible_employee_ids(session, user, permission=definition.permission),
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if file_format == "xlsx":
        payload = service.to_xlsx(result)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{code}-{stamp}.xlsx"
    else:
        payload = service.to_csv(result)
        media_type = "text/csv; charset=utf-8"
        filename = f"{code}-{stamp}.csv"

    audit.record(
        session,
        action="report.exported",
        context=context,
        entity_type="report",
        entity_id=code,
        after={
            "format": file_format,
            "rows": len(result.rows),
            "filters": {
                key: (value.isoformat() if isinstance(value, date) else value)
                for key, value in parameters.items()
                if value is not None
            },
            "scoped_to_team": _visible_employee_ids(session, user, permission=definition.permission)
            is not None,
        },
        extra={
            "note": (
                "An exported file leaves the system's access controls behind, so "
                "who took what is recorded here."
            )
        },
    )
    session.flush()

    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/meta/employees",
    response_model=list[dict[str, Any]],
    summary="Employees this account may report on",
)
def reportable_employees(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[
        User,
        Depends(
            require_any("report.attendance", "report.leave", "report.employee", "report.payroll")
        ),
    ],
) -> list[dict[str, Any]]:
    """For building a filter, scoped exactly as the reports are."""
    allowed = _visible_employee_ids(session, user, permission="report.attendance")
    statement = select(Employee.id, Employee.employee_code, Employee.first_name, Employee.last_name)
    if allowed is not None:
        statement = statement.where(Employee.id.in_(allowed or [-1]))
    return [
        {
            "id": row[0],
            "employee_code": row[1],
            "name": " ".join(part for part in (row[2], row[3]) if part),
        }
        for row in session.execute(statement.order_by(Employee.employee_code)).all()
    ]
