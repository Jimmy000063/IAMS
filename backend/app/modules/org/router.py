"""Organisation structure endpoints.

Reads require ``org.read``, which every employee holds — the directory of
departments and locations is ordinary company information. Writes require
``org.manage``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AuditCtx, DbSession, require
from app.modules.auth.models import User
from app.modules.org import service
from app.modules.org.schemas import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdate,
    DesignationCreate,
    DesignationResponse,
    DesignationUpdate,
    EmployeeGroupCreate,
    EmployeeGroupResponse,
    GroupMembersRequest,
    WorkLocationCreate,
    WorkLocationResponse,
    WorkLocationUpdate,
)

router = APIRouter(prefix="/org", tags=["Organisation"])

IncludeInactive = Annotated[
    bool, Query(description="Include deactivated records, which are retained for history")
]


# ----------------------------------------------------------------- departments


@router.get(
    "/departments",
    response_model=list[DepartmentResponse],
    summary="List departments",
)
def list_departments(
    session: DbSession,
    _actor: Annotated[User, Depends(require("org.read"))],
    include_inactive: IncludeInactive = False,
) -> list[DepartmentResponse]:
    return [
        DepartmentResponse.model_validate(row)
        for row in service.list_departments(session, include_inactive=include_inactive)
    ]


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a department",
)
def create_department(
    payload: DepartmentCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> DepartmentResponse:
    department = service.create_department(session, data=payload.model_dump(), context=ctx)
    return DepartmentResponse.model_validate(department)


@router.patch(
    "/departments/{department_id}",
    response_model=DepartmentResponse,
    summary="Update a department",
)
def update_department(
    department_id: int,
    payload: DepartmentUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> DepartmentResponse:
    """A parent that would create a loop in the hierarchy is rejected."""
    department = service.update_department(
        session,
        department_id,
        data=payload.model_dump(exclude_unset=True),
        context=ctx,
    )
    return DepartmentResponse.model_validate(department)


# ---------------------------------------------------------------- designations


@router.get(
    "/designations",
    response_model=list[DesignationResponse],
    summary="List designations",
)
def list_designations(
    session: DbSession,
    _actor: Annotated[User, Depends(require("org.read"))],
    include_inactive: IncludeInactive = False,
) -> list[DesignationResponse]:
    return [
        DesignationResponse.model_validate(row)
        for row in service.list_designations(session, include_inactive=include_inactive)
    ]


@router.post(
    "/designations",
    response_model=DesignationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a designation",
)
def create_designation(
    payload: DesignationCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> DesignationResponse:
    designation = service.create_designation(session, data=payload.model_dump(), context=ctx)
    return DesignationResponse.model_validate(designation)


@router.patch(
    "/designations/{designation_id}",
    response_model=DesignationResponse,
    summary="Update a designation",
)
def update_designation(
    designation_id: int,
    payload: DesignationUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> DesignationResponse:
    designation = service.update_designation(
        session,
        designation_id,
        data=payload.model_dump(exclude_unset=True),
        context=ctx,
    )
    return DesignationResponse.model_validate(designation)


# -------------------------------------------------------------- work locations


@router.get(
    "/work-locations",
    response_model=list[WorkLocationResponse],
    summary="List work locations",
)
def list_work_locations(
    session: DbSession,
    _actor: Annotated[User, Depends(require("org.read"))],
    include_inactive: IncludeInactive = False,
) -> list[WorkLocationResponse]:
    return [
        WorkLocationResponse.model_validate(row)
        for row in service.list_work_locations(session, include_inactive=include_inactive)
    ]


@router.post(
    "/work-locations",
    response_model=WorkLocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work location",
    responses={422: {"description": "Unknown IANA timezone name"}},
)
def create_work_location(
    payload: WorkLocationCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> WorkLocationResponse:
    """The timezone is validated against the IANA database.

    This is the most consequential field here: attendance derives each
    employee's working day in their location's timezone, so an unknown or wrong
    value would silently misdate every punch at this location.
    """
    location = service.create_work_location(session, data=payload.model_dump(), context=ctx)
    return WorkLocationResponse.model_validate(location)


@router.patch(
    "/work-locations/{location_id}",
    response_model=WorkLocationResponse,
    summary="Update a work location",
)
def update_work_location(
    location_id: int,
    payload: WorkLocationUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> WorkLocationResponse:
    """A timezone change is recorded prominently in the audit log.

    It changes how attendance dates are derived going forward for everyone at
    this location; historical ``attendance_days`` rows are not rewritten.
    """
    location = service.update_work_location(
        session,
        location_id,
        data=payload.model_dump(exclude_unset=True),
        context=ctx,
    )
    return WorkLocationResponse.model_validate(location)


# ---------------------------------------------------------------------- groups


@router.get(
    "/groups",
    response_model=list[EmployeeGroupResponse],
    summary="List employee groups",
)
def list_groups(
    session: DbSession,
    _actor: Annotated[User, Depends(require("org.read"))],
    include_inactive: IncludeInactive = False,
) -> list[EmployeeGroupResponse]:
    return [
        EmployeeGroupResponse(
            id=group.id,
            code=group.code,
            name=group.name,
            description=group.description,
            is_active=group.is_active,
            member_count=len(group.members),
        )
        for group in service.list_groups(session, include_inactive=include_inactive)
    ]


@router.post(
    "/groups",
    response_model=EmployeeGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an employee group",
)
def create_group(
    payload: EmployeeGroupCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> EmployeeGroupResponse:
    """Groups target announcements and holiday calendars at arbitrary cohorts."""
    group = service.create_group(session, data=payload.model_dump(), context=ctx)
    return EmployeeGroupResponse(
        id=group.id,
        code=group.code,
        name=group.name,
        description=group.description,
        is_active=group.is_active,
        member_count=0,
    )


@router.put(
    "/groups/{group_id}/members",
    response_model=EmployeeGroupResponse,
    summary="Replace a group's membership",
)
def set_group_members(
    group_id: int,
    payload: GroupMembersRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("org.manage"))],
) -> EmployeeGroupResponse:
    group = service.set_group_members(session, group_id, payload.employee_ids, context=ctx)
    return EmployeeGroupResponse(
        id=group.id,
        code=group.code,
        name=group.name,
        description=group.description,
        is_active=group.is_active,
        member_count=len(group.members),
    )
