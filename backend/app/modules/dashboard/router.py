"""Dashboard endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, DbSession, require
from app.modules.auth.models import User
from app.modules.dashboard import service

router = APIRouter(prefix="/dashboard", tags=["Dashboards"])


@router.get(
    "/me",
    response_model=dict,
    summary="Your own dashboard",
)
def my_dashboard(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    """Profile summary, team size and tenure.

    Metrics that depend on modules not yet built are listed under
    ``not_yet_available`` with the milestone that delivers them, rather than
    returned as zeros that would read as real figures.
    """
    return service.employee_dashboard(session, user)


@router.get(
    "/hr",
    response_model=dict,
    summary="HR/Admin dashboard",
)
def hr_dashboard(
    session: DbSession,
    _actor: Annotated[User, Depends(require("employee.read"))],
) -> dict[str, Any]:
    """Headcount breakdowns, joiners, items needing attention, config health.

    ``attention.reports_needing_manager`` lists active employees whose reporting
    manager is missing or has exited - their approval requests would otherwise
    stall with no visible cause.
    """
    return service.hr_dashboard(session)
