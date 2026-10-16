"""Business policy configuration endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import AuditCtx, DbSession, require
from app.modules.auth.models import User
from app.modules.settings import service
from app.modules.settings.models import SettingChangeHistory
from app.modules.settings.schemas import (
    ConfigurationHealthResponse,
    SettingHistoryResponse,
    SettingResponse,
    UpdateSettingRequest,
)

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get(
    "",
    response_model=list[SettingResponse],
    summary="List business policy settings",
)
def list_settings(
    session: DbSession,
    _actor: Annotated[User, Depends(require("settings.read"))],
    category: Annotated[str | None, Query(description="Filter by category")] = None,
    unconfirmed_only: Annotated[
        bool, Query(description="Only settings still holding a placeholder default")
    ] = False,
) -> list[SettingResponse]:
    rows = service.list_settings(session, category=category, unconfirmed_only=unconfirmed_only)
    return [SettingResponse.from_row(row) for row in rows]


@router.get(
    "/health",
    response_model=ConfigurationHealthResponse,
    summary="Summary of outstanding configuration decisions",
)
def configuration_health(
    session: DbSession,
    _actor: Annotated[User, Depends(require("settings.read"))],
) -> ConfigurationHealthResponse:
    """Drives the HR/Admin dashboard warning about unconfirmed policy values."""
    return ConfigurationHealthResponse(**service.configuration_health(session))


@router.get(
    "/{key}",
    response_model=SettingResponse,
    summary="Read one setting",
)
def get_setting(
    key: str,
    session: DbSession,
    _actor: Annotated[User, Depends(require("settings.read"))],
) -> SettingResponse:
    return SettingResponse.from_row(service.get_row(session, key))


@router.put(
    "/{key}",
    response_model=SettingResponse,
    summary="Change one setting",
    responses={
        409: {"description": "Setting is not editable"},
        422: {"description": "Value fails the setting's declared constraints"},
    },
)
def update_setting(
    key: str,
    payload: UpdateSettingRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("settings.manage"))],
) -> SettingResponse:
    """Validate against the declared type and constraints, then record history.

    Confirming a setting flagged for legal review requires a source reference, so
    that a statutory value can always be traced back to its authority.
    """
    row = service.update_setting(
        session,
        key,
        payload.value,
        context=ctx,
        reason=payload.reason,
        confirm=payload.confirm,
        source_reference=payload.source_reference,
    )
    return SettingResponse.from_row(row)


@router.get(
    "/{key}/history",
    response_model=list[SettingHistoryResponse],
    summary="Change history for one setting",
)
def setting_history(
    key: str,
    session: DbSession,
    _actor: Annotated[User, Depends(require("audit.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SettingHistoryResponse]:
    """Lets a past attendance or payroll figure be explained by the value then in force."""
    service.get_row(session, key)  # 404 for an unknown key
    rows = session.scalars(
        select(SettingChangeHistory)
        .where(SettingChangeHistory.setting_key == key)
        .order_by(SettingChangeHistory.changed_at.desc())
        .limit(limit)
    ).all()
    return [
        SettingHistoryResponse(
            setting_key=row.setting_key,
            old_value=(row.old_value or {}).get("value"),
            new_value=(row.new_value or {}).get("value"),
            changed_at=row.changed_at,
            changed_by_user_id=row.changed_by_user_id,
            change_reason=row.change_reason,
        )
        for row in rows
    ]
