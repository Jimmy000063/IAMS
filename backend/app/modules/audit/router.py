"""The audit log viewer, and data-retention controls.

Why reading the audit log is itself audited
-------------------------------------------
The trail records who changed what, including who changed permissions and whose
bank details were opened. Reading it is therefore a privileged act with its own
consequences - somebody checking what a colleague did is exactly the thing an
organisation may later need to account for - so a search of the log writes its
own entry. That entry records the filters, not the results, which is enough to
reconstruct what was looked for without duplicating the data.

Retention
---------
Nothing is purged automatically. A retention period is a company decision with
legal consequences in both directions: too short destroys evidence an
investigation needs, too long keeps personal data nobody has a reason to hold.
The settings start unconfirmed, the purge is a deliberate command with a dry run
by default, and the audit log's own purge is deliberately the hardest: it needs
the owner database role, because ``DELETE`` on ``audit_logs`` is revoked from the
application role.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import AuditCtx, DbSession, require
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog, JobRun
from app.modules.audit.schemas import (
    AuditEntryResponse,
    AuditPage,
    JobRunResponse,
    RetentionReportResponse,
)
from app.modules.auth.models import User
from app.modules.settings import service as settings_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit"])

MAX_PAGE_SIZE = 200


@router.get(
    "/logs",
    response_model=AuditPage,
    summary="Search the audit log",
)
def search_logs(
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("audit.read"))],
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity_type: Annotated[str | None, Query(max_length=60)] = None,
    entity_id: Annotated[str | None, Query(max_length=60)] = None,
    actor_user_id: Annotated[int | None, Query()] = None,
    request_id: Annotated[str | None, Query(max_length=40)] = None,
    succeeded: Annotated[bool | None, Query()] = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> AuditPage:
    """Newest first, filtered and paginated.

    ``action`` matches as a prefix, so ``leave.`` returns everything the leave
    module recorded - which is how somebody actually searches a trail: by the
    area they are investigating, not by remembering an exact action name.

    The search itself is recorded. Looking through the audit log is a privileged
    act, and an organisation may later need to account for it.
    """
    statement = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    count_statement = select(func.count()).select_from(AuditLog)

    conditions = []
    if action:
        conditions.append(AuditLog.action.startswith(action))
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if entity_id:
        conditions.append(AuditLog.entity_id == str(entity_id))
    if actor_user_id is not None:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if request_id:
        conditions.append(AuditLog.request_id == request_id)
    if succeeded is not None:
        conditions.append(AuditLog.succeeded.is_(succeeded))
    if start:
        conditions.append(AuditLog.created_at >= datetime.combine(start, datetime.min.time()))
    if end:
        conditions.append(
            AuditLog.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time())
        )
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)

    total = session.scalar(count_statement) or 0
    rows = list(session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all())

    audit.record(
        session,
        action="audit.searched",
        context=context,
        entity_type="audit_log",
        entity_id=None,
        # The filters, not the results: enough to reconstruct what was looked
        # for without copying the data into a second place.
        after={
            "filters": {
                key: value
                for key, value in {
                    "action": action,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "actor_user_id": actor_user_id,
                    "request_id": request_id,
                    "succeeded": succeeded,
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                }.items()
                if value is not None
            },
            "matched": total,
        },
    )
    session.flush()

    return AuditPage(
        items=[AuditEntryResponse.model_validate(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
        pages=max((total + page_size - 1) // page_size, 1),
    )


@router.get(
    "/logs/actions",
    response_model=list[dict[str, Any]],
    summary="The actions present in the log, with counts",
)
def action_catalogue(
    session: DbSession,
    _dep: Annotated[User, Depends(require("audit.read"))],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    """What is actually in the trail.

    Read from the data rather than from a list of known actions, so an action a
    new module records appears here without anybody maintaining a catalogue.
    """
    rows = session.execute(
        select(AuditLog.action, func.count(), func.max(AuditLog.created_at))
        .group_by(AuditLog.action)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {"action": row[0], "count": row[1], "last_seen": row[2].isoformat() if row[2] else None}
        for row in rows
    ]


@router.get(
    "/job-runs",
    response_model=list[JobRunResponse],
    summary="Scheduled job history",
)
def job_runs(
    session: DbSession,
    _dep: Annotated[User, Depends(require("audit.read"))],
    job_name: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobRunResponse]:
    """What the scheduled jobs did, newest first.

    Every job in this system is idempotent and keyed, so this is also where a
    re-run shows as having written nothing - which is the evidence that the
    idempotency works in practice rather than only in tests.
    """
    statement = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
    if job_name:
        statement = statement.where(JobRun.job_name == job_name)
    return [JobRunResponse.model_validate(row) for row in session.scalars(statement).all()]


@router.get(
    "/retention",
    response_model=RetentionReportResponse,
    summary="What would be purged under the configured retention periods",
)
def retention_report(
    session: DbSession,
    _dep: Annotated[User, Depends(require("audit.read"))],
) -> RetentionReportResponse:
    """A dry run of retention, always.

    Nothing is deleted by looking. The counts answer two questions at once: how
    much personal data the system is holding beyond its stated period, and how
    much evidence a purge would destroy - and both deserve an answer before
    anybody runs one.
    """
    from app.modules.devices.models import DeviceRawEvent
    from app.modules.notifications.models import Notification

    def setting(key: str, default: int) -> tuple[int, bool]:
        row = settings_service.get_row(session, key)
        value = settings_service.coerce_value(row.value_type, row.unwrapped_value, key=key)
        return int(value if value is not None else default), row.is_confirmed

    audit_days, audit_confirmed = setting("retention.audit_log_days", 2555)
    device_days, device_confirmed = setting("retention.device_event_days", 365)
    notification_days, notification_confirmed = setting("retention.notification_days", 365)

    now = utc_now()
    audit_cutoff = now - timedelta(days=audit_days)
    device_cutoff = now - timedelta(days=device_days)
    notification_cutoff = now - timedelta(days=notification_days)

    return RetentionReportResponse(
        as_of=now,
        categories=[
            {
                "category": "audit_logs",
                "retention_days": audit_days,
                "confirmed": audit_confirmed,
                "cutoff": audit_cutoff.isoformat(),
                "rows_total": session.scalar(select(func.count()).select_from(AuditLog)) or 0,
                "rows_beyond_retention": session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.created_at < audit_cutoff)
                )
                or 0,
                "note": (
                    "Purging the audit log needs the owner database role: DELETE is "
                    "revoked from the application role on purpose."
                ),
            },
            {
                "category": "device_raw_events",
                "retention_days": device_days,
                "confirmed": device_confirmed,
                "cutoff": device_cutoff.isoformat(),
                "rows_total": session.scalar(select(func.count()).select_from(DeviceRawEvent)) or 0,
                "rows_beyond_retention": session.scalar(
                    select(func.count())
                    .select_from(DeviceRawEvent)
                    .where(DeviceRawEvent.received_at < device_cutoff)
                )
                or 0,
                "note": (
                    "Raw device events are the evidence behind a computed "
                    "attendance day. Purging them leaves the computed day intact "
                    "but unexplainable."
                ),
            },
            {
                "category": "notifications",
                "retention_days": notification_days,
                "confirmed": notification_confirmed,
                "cutoff": notification_cutoff.isoformat(),
                "rows_total": session.scalar(select(func.count()).select_from(Notification)) or 0,
                "rows_beyond_retention": session.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.created_at < notification_cutoff)
                )
                or 0,
                "note": (
                    "A notification is the record of somebody having been told. "
                    "Purging old ones is safe; purging recent ones removes that "
                    "evidence."
                ),
            },
        ],
        unconfirmed=[
            key
            for key, confirmed in (
                ("retention.audit_log_days", audit_confirmed),
                ("retention.device_event_days", device_confirmed),
                ("retention.notification_days", notification_confirmed),
            )
            if not confirmed
        ],
        note=(
            "Nothing is purged automatically. A retention period is a company "
            "decision with consequences in both directions, and the purge is a "
            "deliberate command that defaults to a dry run."
        ),
    )
