"""Device management and the shared ingestion pipeline.

Every adapter feeds one path: :func:`ingest`. Deduplication, employee mapping,
attendance-event creation and recomputation happen there and nowhere else, so a
new adapter cannot get those parts wrong in a novel way.

The pipeline in order
---------------------
1. **Store** every payload as a ``device_raw_event``, whatever happens next.
   Nothing a device sent is discarded.
2. **Deduplicate** on ``(device_id, external_event_id)``. A device re-sending
   its buffer after an outage is the normal case, not an edge case.
3. **Map** the device's identifier to an employee, using the mapping effective
   on the punch's own date - so a reused enrolment slot does not reattribute
   last year's punches.
4. **Apply** as an attendance event, then recompute the affected days.
5. **Report** per-outcome counts, and leave anything needing a human in a
   queue rather than failing silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt, is_configured
from app.core.errors import (
    Conflict,
    NotFound,
    PolicyViolation,
    ValidationFailed,
)
from app.core.timeutil import resolve_timezone, utc_now, validate_timezone_name
from app.modules.attendance import service as attendance_service
from app.modules.attendance.models import AttendanceEvent
from app.modules.attendance.policy import attribute_work_date
from app.modules.audit import service as audit
from app.modules.devices.adapters.base import (
    FetchResult,
    PullAdapter,
    PushAdapter,
    get_adapter,
)
from app.modules.devices.models import Device, DeviceEmployeeMapping, DeviceRawEvent, DeviceSyncRun
from app.modules.devices.policy import (
    DeviceDataError,
    FieldMapping,
    NormalisedEvent,
    RejectedEvent,
    next_cursor_from_events,
    normalise_batch,
    sync_idempotency_key,
    validate_cursor,
)
from app.modules.employees.models import Employee
from app.modules.org.models import WorkLocation
from app.modules.shifts import service as shifts_service

logger = logging.getLogger(__name__)

MAX_RAW_EVENTS_PER_INGEST = 20_000


def _rule(error: DeviceDataError) -> ValidationFailed:
    return ValidationFailed(
        str(error),
        details=[{"field": error.field_name, "message": str(error)}],
    )


# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------


def _normalise_code(code: str) -> str:
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise ValidationFailed(
            "A device code is required.",
            details=[{"field": "code", "message": "Required."}],
        )
    return cleaned


def _validate_adapter_config(adapter_type: str, config: dict[str, Any]) -> None:
    adapter = get_adapter(adapter_type)
    try:
        adapter.validate_config(config)
        # Parsing the field mapping here means a bad mapping is rejected at
        # configuration time rather than at the first sync.
        FieldMapping.from_config(config)
    except DeviceDataError as error:
        raise _rule(error) from error


def create_device(session: Session, *, data: dict[str, Any], context: audit.AuditContext) -> Device:
    code = _normalise_code(data["code"])
    if session.scalar(select(func.count()).select_from(Device).where(Device.code == code)):
        raise Conflict(
            f"Device code {code!r} is already in use.",
            details=[{"field": "code", "message": "Already in use."}],
        )

    adapter_type = data["adapter_type"]
    config = dict(data.get("config") or {})
    _validate_adapter_config(adapter_type, config)

    if data.get("timezone_override"):
        try:
            validate_timezone_name(data["timezone_override"])
        except ValueError as exc:
            raise ValidationFailed(
                str(exc),
                details=[{"field": "timezone_override", "message": str(exc)}],
            ) from exc

    if (
        data.get("work_location_id") is not None
        and session.get(WorkLocation, data["work_location_id"]) is None
    ):
        raise ValidationFailed(
            "The work location does not exist.",
            details=[{"field": "work_location_id", "message": "Unknown."}],
        )

    device = Device(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        adapter_type=adapter_type,
        vendor=data.get("vendor"),
        model=data.get("model"),
        serial_number=data.get("serial_number"),
        firmware_version=data.get("firmware_version"),
        work_location_id=data.get("work_location_id"),
        timezone_override=data.get("timezone_override"),
        config=config,
        direction_mode=data.get("direction_mode", "alternate"),
        clock_drift_tolerance_seconds=data.get("clock_drift_tolerance_seconds", 300),
        allowed_source_ips=data.get("allowed_source_ips"),
        is_active=data.get("is_active", True),
    )

    _apply_secrets(device, data)
    session.add(device)
    session.flush()

    audit.record(
        session,
        action="device.created",
        context=context,
        entity_type="device",
        entity_id=device.id,
        # Config is recorded but secrets are separate encrypted columns and
        # never appear here.
        after={
            "code": device.code,
            "name": device.name,
            "adapter_type": device.adapter_type,
            "direction_mode": device.direction_mode,
            "work_location_id": device.work_location_id,
            "has_api_secret": device.has_api_secret,
            "has_webhook_secret": device.has_webhook_secret,
        },
    )
    return device


def _apply_secrets(device: Device, data: dict[str, Any]) -> None:
    """Encrypt and store any supplied secrets.

    Refuses rather than storing plaintext when field encryption is unconfigured
    - a device credential in the clear is worse than a device that cannot be
    configured yet.
    """
    for key, attribute in (
        ("api_secret", "api_secret_encrypted"),
        ("webhook_secret", "webhook_secret_encrypted"),
    ):
        value = data.get(key)
        if value is None:
            continue
        if not str(value).strip():
            setattr(device, attribute, None)
            continue
        if not is_configured():
            raise PolicyViolation(
                "Field encryption is not configured, so a device secret cannot be "
                "stored. Set IAMS_FIELD_ENCRYPTION_KEY first."
            )
        setattr(device, attribute, encrypt(str(value)))


def update_device(
    session: Session, device_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Device:
    device = get_device(session, device_id)
    before = {
        "name": device.name,
        "adapter_type": device.adapter_type,
        "direction_mode": device.direction_mode,
        "is_active": device.is_active,
        "config": device.config,
    }

    if "code" in data:
        code = _normalise_code(data["code"])
        clash = session.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.code == code, Device.id != device_id)
        )
        if clash:
            raise Conflict(f"Device code {code!r} is already in use.")
        device.code = code

    if "config" in data or "adapter_type" in data:
        adapter_type = data.get("adapter_type", device.adapter_type)
        config = dict(data.get("config", device.config) or {})
        _validate_adapter_config(adapter_type, config)
        device.adapter_type = adapter_type
        device.config = config

    if data.get("timezone_override"):
        try:
            validate_timezone_name(data["timezone_override"])
        except ValueError as exc:
            raise ValidationFailed(
                str(exc),
                details=[{"field": "timezone_override", "message": str(exc)}],
            ) from exc

    for attribute in (
        "name",
        "description",
        "vendor",
        "model",
        "serial_number",
        "firmware_version",
        "work_location_id",
        "timezone_override",
        "direction_mode",
        "clock_drift_tolerance_seconds",
        "allowed_source_ips",
        "is_active",
    ):
        if attribute in data:
            setattr(device, attribute, data[attribute])

    _apply_secrets(device, data)
    session.flush()

    audit.record(
        session,
        action="device.updated",
        context=context,
        entity_type="device",
        entity_id=device.id,
        before=before,
        after={
            "name": device.name,
            "adapter_type": device.adapter_type,
            "direction_mode": device.direction_mode,
            "is_active": device.is_active,
            "config": device.config,
        },
        extra={
            "note": (
                "A field-mapping change affects future ingestion only. Use the "
                "reprocess endpoint to re-apply it to stored raw events."
            )
        },
    )
    return device


def get_device(session: Session, device_id: int) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise NotFound("Device not found.")
    return device


def list_devices(session: Session, *, include_inactive: bool = False) -> list[Device]:
    statement = select(Device).order_by(Device.code)
    if not include_inactive:
        statement = statement.where(Device.is_active.is_(True))
    return list(session.scalars(statement).all())


def device_timezone(session: Session, device: Device) -> str:
    """The zone used to interpret this device's naive timestamps.

    Precedence: the device's own override, then its work location, then the
    company default. The server's timezone is never consulted.
    """
    if device.timezone_override:
        return device.timezone_override
    if device.work_location_id is not None:
        name = session.scalar(
            select(WorkLocation.timezone).where(WorkLocation.id == device.work_location_id)
        )
        if name:
            return name
    from app.core.config import get_settings

    return get_settings().default_timezone


# ---------------------------------------------------------------------------
# Employee mapping
# ---------------------------------------------------------------------------


def add_mapping(
    session: Session,
    device_id: int,
    *,
    external_employee_ref: str,
    employee_id: int,
    effective_from: date,
    effective_to: date | None,
    notes: str | None,
    context: audit.AuditContext,
) -> DeviceEmployeeMapping:
    """Map a device identifier to an employee, for a period.

    An existing open mapping for the same identifier is refused rather than
    silently replaced: reusing an enrolment slot is a real event that should be
    recorded by closing the old mapping, not by overwriting it.
    """
    device = get_device(session, device_id)
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ValidationFailed(
            "The employee does not exist.",
            details=[{"field": "employee_id", "message": "Unknown."}],
        )

    reference = str(external_employee_ref).strip()
    if not reference:
        raise ValidationFailed(
            "A device identifier is required.",
            details=[{"field": "external_employee_ref", "message": "Required."}],
        )

    if effective_to is not None and effective_to < effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    open_existing = session.scalar(
        select(DeviceEmployeeMapping).where(
            DeviceEmployeeMapping.device_id == device_id,
            DeviceEmployeeMapping.external_employee_ref == reference,
            DeviceEmployeeMapping.effective_to.is_(None),
        )
    )
    if open_existing is not None:
        raise Conflict(
            f"Identifier {reference!r} on {device.code} is already mapped to employee "
            f"#{open_existing.employee_id}. Close that mapping first - enrolment "
            "slots get reused, and overwriting would reattribute historical punches.",
            details=[{"field": "external_employee_ref", "message": "Already mapped."}],
        )

    mapping = DeviceEmployeeMapping(
        device_id=device_id,
        external_employee_ref=reference,
        employee_id=employee_id,
        effective_from=effective_from,
        effective_to=effective_to,
        notes=notes,
        created_by_user_id=context.actor_user_id,
    )
    session.add(mapping)
    session.flush()

    audit.record(
        session,
        action="device.mapping_added",
        context=context,
        entity_type="device",
        entity_id=device_id,
        after={
            "external_employee_ref": reference,
            "employee_id": employee_id,
            "employee_code": employee.employee_code,
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
        },
    )
    return mapping


def close_mapping(
    session: Session, mapping_id: int, *, effective_to: date, context: audit.AuditContext
) -> DeviceEmployeeMapping:
    mapping = session.get(DeviceEmployeeMapping, mapping_id)
    if mapping is None:
        raise NotFound("Device mapping not found.")
    if effective_to < mapping.effective_from:
        raise ValidationFailed(
            "The end date cannot precede the start date.",
            details=[{"field": "effective_to", "message": "Must not precede the start."}],
        )

    before = mapping.effective_to
    mapping.effective_to = effective_to
    session.flush()

    audit.record(
        session,
        action="device.mapping_closed",
        context=context,
        entity_type="device",
        entity_id=mapping.device_id,
        before={"effective_to": before.isoformat() if before else None},
        after={"effective_to": effective_to.isoformat()},
    )
    return mapping


def list_mappings(session: Session, device_id: int) -> list[DeviceEmployeeMapping]:
    return list(
        session.scalars(
            select(DeviceEmployeeMapping)
            .where(DeviceEmployeeMapping.device_id == device_id)
            .order_by(
                DeviceEmployeeMapping.external_employee_ref,
                DeviceEmployeeMapping.effective_from.desc(),
            )
        ).all()
    )


def resolve_employee(
    session: Session, *, device_id: int, reference: str, on_date: date
) -> int | None:
    """Which employee a device identifier referred to on a given date.

    Date-scoped deliberately: enrolment slots get reused, and a punch from
    March must resolve to whoever held that slot in March, not whoever holds it
    now.
    """
    candidates = session.scalars(
        select(DeviceEmployeeMapping).where(
            DeviceEmployeeMapping.device_id == device_id,
            DeviceEmployeeMapping.external_employee_ref == reference,
        )
    ).all()
    covering = [mapping for mapping in candidates if mapping.covers(on_date)]
    if not covering:
        return None
    # Most recent decision wins if data contains an overlap the index did not
    # prevent (closed periods can legitimately overlap after manual editing).
    chosen = max(covering, key=lambda mapping: (mapping.effective_from, mapping.id))
    return chosen.employee_id


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IngestReport:
    device_code: str
    fetched: int = 0
    accepted: int = 0
    duplicate: int = 0
    unmapped: int = 0
    failed: int = 0
    ignored: int = 0
    days_recomputed: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failed and not self.accepted:
            return "failed"
        if self.failed or self.unmapped:
            return "partial"
        return "succeeded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_code": self.device_code,
            "status": self.status,
            "fetched": self.fetched,
            "accepted": self.accepted,
            "duplicate": self.duplicate,
            "unmapped": self.unmapped,
            "failed": self.failed,
            "ignored": self.ignored,
            "days_recomputed": self.days_recomputed,
            "problems": self.problems[:50],
        }


def ingest(
    session: Session,
    *,
    device: Device,
    payloads: list[dict[str, Any]],
    sync_run: DeviceSyncRun | None,
    context: audit.AuditContext,
) -> IngestReport:
    """The one path every adapter feeds.

    Stores every payload, deduplicates, maps, applies and recomputes. Returns
    per-outcome counts rather than a single total, because "fetched 200,
    accepted 3, unmapped 197" is a configuration failure while "fetched 200,
    accepted 200" is a healthy sync - and one number cannot distinguish them.
    """
    report = IngestReport(device_code=device.code)

    if len(payloads) > MAX_RAW_EVENTS_PER_INGEST:
        raise ValidationFailed(
            f"That batch contains {len(payloads)} events; the maximum is "
            f"{MAX_RAW_EVENTS_PER_INGEST}. Split it into smaller batches."
        )

    report.fetched = len(payloads)
    if not payloads:
        return report

    timezone_name = device_timezone(session, device)
    tz = resolve_timezone(timezone_name)

    try:
        mapping = FieldMapping.from_config(device.config)
    except DeviceDataError as error:
        raise _rule(error) from error

    normalised, rejected = normalise_batch(
        payloads,
        device_code=device.code,
        mapping=mapping,
        direction_mode=device.direction_mode,
        tz=tz,
        timezone_name=timezone_name,
        now=utc_now(),
        drift_tolerance_seconds=device.clock_drift_tolerance_seconds,
        previous_direction_by_ref=_last_directions(
            session, device=device, events_hint=payloads, mapping=mapping, tz=tz
        ),
    )

    # Rejections are stored too: nothing a device sent is thrown away, and the
    # reconciliation queue is how an operator finds out the mapping is wrong.
    for rejection in rejected:
        _store_rejection(session, device=device, sync_run=sync_run, rejection=rejection)
        if rejection.is_ignored:
            report.ignored += 1
        else:
            report.failed += 1
            report.problems.append(
                {
                    "reason": rejection.reason,
                    "field": rejection.field_name,
                    "employee_ref": rejection.external_employee_ref,
                    "raw_timestamp": rejection.raw_timestamp,
                }
            )

    affected: set[tuple[int, date]] = set()

    for event in normalised:
        outcome = _apply_event(session, device=device, event=event, sync_run=sync_run, tz=tz)
        if outcome.status == "duplicate":
            report.duplicate += 1
        elif outcome.status == "unmapped":
            report.unmapped += 1
            report.problems.append(
                {
                    "reason": (
                        f"No employee is mapped to device identifier "
                        f"{event.external_employee_ref!r} on "
                        f"{outcome.work_date.isoformat() if outcome.work_date else 'that date'}."
                    ),
                    "field": "external_employee_ref",
                    "employee_ref": event.external_employee_ref,
                    "raw_timestamp": event.raw_timestamp,
                }
            )
        elif outcome.status == "failed":
            report.failed += 1
            report.problems.append(
                {
                    "reason": outcome.error or "Could not be applied.",
                    "field": None,
                    "employee_ref": event.external_employee_ref,
                    "raw_timestamp": event.raw_timestamp,
                }
            )
        else:
            report.accepted += 1
            if outcome.employee_id is not None and outcome.work_date is not None:
                affected.add((outcome.employee_id, outcome.work_date))

    # Recompute once per affected employee-day, not once per event: a day with
    # four punches would otherwise be rebuilt four times.
    for employee_id, work_date in sorted(affected):
        try:
            attendance_service.recompute_day(session, employee_id=employee_id, work_date=work_date)
            report.days_recomputed += 1
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Failed to recompute attendance for employee %s on %s",
                employee_id,
                work_date,
            )

    device.last_seen_at = utc_now()
    session.flush()
    return report


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    employee_id: int | None = None
    work_date: date | None = None
    error: str | None = None


def _apply_event(
    session: Session,
    *,
    device: Device,
    event: NormalisedEvent,
    sync_run: DeviceSyncRun | None,
    tz,
) -> _Outcome:
    """Store one normalised event and apply it if it is new and mappable."""
    existing = session.scalar(
        select(DeviceRawEvent).where(
            DeviceRawEvent.device_id == device.id,
            DeviceRawEvent.external_event_id == event.external_event_id,
        )
    )
    if existing is not None:
        # Already seen. The row stays as the record that it was re-delivered.
        return _Outcome(status="duplicate")

    raw = DeviceRawEvent(
        device_id=device.id,
        sync_run_id=sync_run.id if sync_run else None,
        external_event_id=event.external_event_id,
        external_employee_ref=event.external_employee_ref,
        raw_payload=event.raw_payload,
        raw_timestamp=event.raw_timestamp,
        raw_direction=event.raw_direction,
        parsed_occurred_at=event.occurred_at,
        parsed_direction=event.direction,
        timezone_used=event.timezone_used,
        status="pending",
        process_attempts=1,
        last_attempt_at=utc_now(),
    )

    # A savepoint, not a plain flush: losing the unique-constraint race must
    # discard this one event, not the sync run and every event already applied
    # in this transaction.
    try:
        with session.begin_nested():
            session.add(raw)
            session.flush()
    except IntegrityError:
        # Lost a race with a concurrent delivery of the same event. The unique
        # constraint is doing its job; treat it as the duplicate it is.
        return _Outcome(status="duplicate")

    return _process_raw_event(session, device=device, raw=raw, tz=tz)


def _process_raw_event(session: Session, *, device: Device, raw: DeviceRawEvent, tz) -> _Outcome:
    """Map a stored raw event to an employee and create the attendance event."""
    if raw.parsed_occurred_at is None or raw.parsed_direction is None:
        raw.status = "failed"
        raw.error_message = "The event has no parsed timestamp or direction."
        session.flush()
        return _Outcome(status="failed", error=raw.error_message)

    # The punch's own local date decides which mapping applies, so a reused
    # enrolment slot cannot reattribute old punches.
    punch_local_date = raw.parsed_occurred_at.astimezone(tz).date()
    employee_id = resolve_employee(
        session,
        device_id=device.id,
        reference=raw.external_employee_ref or "",
        on_date=punch_local_date,
    )

    if employee_id is None:
        raw.status = "unmapped"
        raw.error_message = (
            f"No employee mapped to device identifier "
            f"{raw.external_employee_ref!r} on {punch_local_date.isoformat()}."
        )
        session.flush()
        return _Outcome(status="unmapped", work_date=punch_local_date)

    employee = session.get(Employee, employee_id)
    if employee is None:  # pragma: no cover - FK prevents this
        raw.status = "failed"
        raw.error_message = f"Mapped employee #{employee_id} no longer exists."
        session.flush()
        return _Outcome(status="failed", error=raw.error_message)

    # The shift date is derived through the same attendance policy the web
    # punch uses, so a device punch on an overnight shift lands on the same
    # date as a web punch would.
    employee_tz_name = shifts_service.employee_timezone(session, employee_id)
    employee_tz = resolve_timezone(employee_tz_name)
    shift, _calendar = shifts_service.resolve_shift_for(
        session, employee_id, raw.parsed_occurred_at.astimezone(employee_tz).date()
    )
    spec = attendance_service.build_shift_spec(session, shift)
    work_date = attribute_work_date(raw.parsed_occurred_at, shift=spec, tz=employee_tz)

    if not employee.is_employed_on(work_date):
        raw.status = "failed"
        raw.error_message = (
            f"{employee.employee_code} was not on the rolls on "
            f"{work_date.isoformat()}; the punch was not applied."
        )
        raw.employee_id = employee_id
        session.flush()
        return _Outcome(status="failed", error=raw.error_message)

    attendance_event = AttendanceEvent(
        employee_id=employee_id,
        event_type=raw.parsed_direction,
        occurred_at=raw.parsed_occurred_at,
        work_date=work_date,
        source="device",
        # Namespaced so a device event id cannot collide with an import's.
        external_event_id=f"dev{device.id}:{raw.external_event_id}"[:120],
        device_id=device.id,
        timezone_used=employee_tz_name,
        raw_payload=raw.raw_payload,
    )

    try:
        with session.begin_nested():
            session.add(attendance_event)
            session.flush()
    except IntegrityError:
        # The attendance event already exists - this raw event duplicates one
        # already applied under a different raw id. The savepoint rolled back
        # only the insert, so the raw row is still here to be marked.
        raw.status = "duplicate"
        raw.error_message = "An attendance event already exists for this punch."
        session.flush()
        return _Outcome(status="duplicate")

    raw.status = "mapped"
    raw.employee_id = employee_id
    raw.attendance_event_id = attendance_event.id
    raw.error_message = None
    session.flush()

    return _Outcome(status="mapped", employee_id=employee_id, work_date=work_date)


def _last_directions(
    session: Session,
    *,
    device: Device,
    events_hint: list[dict[str, Any]],
    mapping: FieldMapping,
    tz,
) -> dict[str, str]:
    """Seed ``alternate`` direction inference from what is already stored.

    Without this, every batch would restart the alternation and a punch-out
    arriving in a new batch would be recorded as a punch-in. Only the references
    present in this batch are looked up, so a large device roster does not cost
    a full scan.
    """
    from app.modules.devices.policy import read_path

    references: set[str] = set()
    for payload in events_hint:
        if not isinstance(payload, dict):
            continue
        value = read_path(payload, mapping.employee_ref)
        if value is not None and str(value).strip():
            references.add(str(value).strip())

    if not references:
        return {}

    # The most recent successfully-mapped direction per reference.
    rows = session.execute(
        select(
            DeviceRawEvent.external_employee_ref,
            DeviceRawEvent.parsed_direction,
            DeviceRawEvent.parsed_occurred_at,
        )
        .where(
            DeviceRawEvent.device_id == device.id,
            DeviceRawEvent.external_employee_ref.in_(references),
            DeviceRawEvent.status == "mapped",
            DeviceRawEvent.parsed_direction.isnot(None),
        )
        .order_by(DeviceRawEvent.parsed_occurred_at.desc())
    ).all()

    latest: dict[str, str] = {}
    for reference, direction, _occurred in rows:
        if reference not in latest:
            latest[reference] = direction
    return latest


def _store_rejection(
    session: Session,
    *,
    device: Device,
    sync_run: DeviceSyncRun | None,
    rejection: RejectedEvent,
) -> None:
    """Persist an uninterpretable event so it is visible, not lost."""
    import hashlib
    import json

    external_id = rejection.external_event_id
    if not external_id:
        # Content-derived so a re-delivered bad event does not pile up.
        digest = hashlib.sha256(
            json.dumps(rejection.raw_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        external_id = f"rejected-{digest[:40]}"

    exists = session.scalar(
        select(func.count())
        .select_from(DeviceRawEvent)
        .where(
            DeviceRawEvent.device_id == device.id,
            DeviceRawEvent.external_event_id == external_id,
        )
    )
    if exists:
        return

    row = DeviceRawEvent(
        device_id=device.id,
        sync_run_id=sync_run.id if sync_run else None,
        external_event_id=external_id[:160],
        external_employee_ref=(
            rejection.external_employee_ref[:80] if rejection.external_employee_ref else None
        ),
        raw_payload=rejection.raw_payload,
        raw_timestamp=(rejection.raw_timestamp[:80] if rejection.raw_timestamp else None),
        status="ignored" if rejection.is_ignored else "failed",
        error_message=rejection.reason,
        process_attempts=1,
        last_attempt_at=utc_now(),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:  # pragma: no cover - raced with an identical rejection
        pass


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


def _open_sync_run(
    session: Session,
    *,
    device: Device,
    trigger: str,
    window: str,
    context: audit.AuditContext,
) -> DeviceSyncRun:
    """Create the run row first, so a repeat is caught before any work happens.

    A key collision means this exact window has already been synchronised. The
    existing run is returned rather than an error raised, which is the useful
    idempotency semantic: a retried scheduled job gets the original outcome and
    fetches nothing. :func:`sync_device` checks ``finished_at`` to tell a fresh
    run from a replayed one.
    """
    key = sync_idempotency_key(device_code=device.code, trigger=trigger, window=window)

    existing = session.scalar(select(DeviceSyncRun).where(DeviceSyncRun.idempotency_key == key))
    if existing is not None:
        return existing

    run = DeviceSyncRun(
        device_id=device.id,
        idempotency_key=key,
        trigger=trigger,
        status="running",
        started_at=utc_now(),
        cursor_before=device.sync_cursor,
        triggered_by_user_id=context.actor_user_id,
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:
        # Two requests opened the same window at once; the constraint settled it.
        found = session.scalar(select(DeviceSyncRun).where(DeviceSyncRun.idempotency_key == key))
        if found is None:  # pragma: no cover - the row must exist to have conflicted
            raise
        return found
    return run


def _replayed(run: DeviceSyncRun) -> dict[str, Any]:
    """The outcome of a run that had already happened, reported as a replay."""
    return {
        "device_code": run.device.code if run.device else "",
        "status": run.status if run.status != "running" else "failed",
        "fetched": run.events_fetched,
        "accepted": 0,
        "duplicate": run.events_accepted + run.events_duplicate,
        "unmapped": run.events_unmapped,
        "failed": run.events_failed,
        "ignored": run.events_ignored,
        "days_recomputed": 0,
        "problems": [
            {
                "reason": (
                    "This device and window were already synchronised at "
                    f"{run.started_at.isoformat()}. Nothing was fetched or applied "
                    "again; the original outcome is reported above."
                ),
                "field": None,
                "employee_ref": None,
                "raw_timestamp": None,
            }
        ],
        "sync_run_id": run.id,
        "cursor_after": run.cursor_after,
        "replayed": True,
    }


def _close_sync_run(
    session: Session,
    *,
    run: DeviceSyncRun,
    device: Device,
    report: IngestReport,
    cursor_after: str | None,
    error: str | None = None,
) -> None:
    run.finished_at = utc_now()
    run.events_fetched = report.fetched
    run.events_accepted = report.accepted
    run.events_duplicate = report.duplicate
    run.events_unmapped = report.unmapped
    run.events_failed = report.failed
    run.events_ignored = report.ignored
    run.attendance_days_recomputed = report.days_recomputed
    run.cursor_after = cursor_after
    run.error_message = error
    run.details = {"problems": report.problems[:50]}
    run.status = "failed" if error else report.status

    device.last_sync_at = run.finished_at
    device.last_sync_status = run.status
    if cursor_after:
        device.sync_cursor = cursor_after
    device.consecutive_failures = device.consecutive_failures + 1 if run.status == "failed" else 0
    session.flush()


def _fail_run(
    session: Session,
    *,
    run: DeviceSyncRun,
    device: Device,
    context: audit.AuditContext,
    trigger: str,
    reason: str,
    fetched: int = 0,
) -> dict[str, Any]:
    """Close a run as failed and report it, without raising.

    Returning rather than raising is deliberate: the request transaction commits,
    so the failed run, the device's failure counter and the audit entry all
    survive. The response carries ``status: "failed"``, so the caller is told
    plainly - it is not a success dressed up as one.
    """
    report = IngestReport(device_code=device.code, fetched=fetched)
    report.failed = 0
    report.problems.append(
        {"reason": reason, "field": None, "employee_ref": None, "raw_timestamp": None}
    )

    _close_sync_run(
        session,
        run=run,
        device=device,
        report=report,
        cursor_after=device.sync_cursor,
        error=reason,
    )
    audit.record(
        session,
        action="device.sync_failed",
        context=context,
        entity_type="device",
        entity_id=device.id,
        succeeded=False,
        extra={"error": reason, "trigger": trigger, "sync_run_id": run.id},
    )
    session.flush()
    return {
        **report.as_dict(),
        "status": "failed",
        "sync_run_id": run.id,
        "cursor_after": device.sync_cursor,
    }


def sync_device(
    session: Session,
    *,
    device_id: int,
    trigger: str,
    context: audit.AuditContext,
    window: str | None = None,
) -> dict[str, Any]:
    """Poll a pull adapter and ingest whatever it returns.

    A device that cannot be reached, or whose response cannot be read, does
    **not** raise. The run is closed as ``failed`` with the reason and returned,
    because raising would roll back the very record that says the sync failed -
    and an integration whose failures leave no trace is worse than one that
    fails loudly.
    """
    device = get_device(session, device_id)

    if not device.is_active:
        raise PolicyViolation(
            f"Device {device.code} is inactive. Activate it before synchronising."
        )

    adapter = get_adapter(device.adapter_type)
    if not isinstance(adapter, PullAdapter):
        raise PolicyViolation(
            f"{device.code} uses the {device.adapter_type!r} adapter, which the "
            "device pushes to. There is nothing to poll - it delivers to its "
            "webhook URL, or you upload a file."
        )

    resolved_window = window or utc_now().strftime("%Y%m%dT%H%M")
    run = _open_sync_run(
        session, device=device, trigger=trigger, window=resolved_window, context=context
    )
    if run.finished_at is not None:
        return _replayed(run)

    secret: str | None = None
    if device.api_secret_encrypted is not None:
        secret = decrypt(device.api_secret_encrypted)

    report = IngestReport(device_code=device.code)
    cursor_after: str | None = device.sync_cursor

    try:
        result: FetchResult = adapter.fetch(
            config=device.config, secret=secret, cursor=device.sync_cursor
        )
    except DeviceDataError as error:
        return _fail_run(
            session,
            run=run,
            device=device,
            context=context,
            trigger=trigger,
            reason=str(error),
        )
    except Exception as exc:
        # The device's own exception text is not surfaced: for an HTTP adapter it
        # can contain the credential that was just sent to it.
        logger.exception("Device %s sync raised", device.code)
        return _fail_run(
            session,
            run=run,
            device=device,
            context=context,
            trigger=trigger,
            reason=(
                f"{type(exc).__name__} while contacting the device. See the server "
                "log for detail; it is not repeated here because it can contain the "
                "device credential."
            ),
        )

    try:
        report = ingest(
            session, device=device, payloads=result.payloads, sync_run=run, context=context
        )
    except ValidationFailed as error:
        # A bad field mapping. Recorded against the run so the operator can see
        # which configuration change broke ingestion and when.
        return _fail_run(
            session,
            run=run,
            device=device,
            context=context,
            trigger=trigger,
            reason=str(error),
            fetched=len(result.payloads),
        )

    # Prefer the device's own cursor; otherwise advance to the newest event.
    try:
        cursor_after = validate_cursor(result.next_cursor) or cursor_after
    except DeviceDataError:
        logger.warning("Device %s returned an unusable cursor; keeping the old one", device.code)

    if result.next_cursor is None:
        timezone_name = device_timezone(session, device)
        tz = resolve_timezone(timezone_name)
        mapping = FieldMapping.from_config(device.config)
        normalised, _ = normalise_batch(
            result.payloads,
            device_code=device.code,
            mapping=mapping,
            direction_mode=device.direction_mode,
            tz=tz,
            timezone_name=timezone_name,
            now=utc_now(),
            drift_tolerance_seconds=device.clock_drift_tolerance_seconds,
        )
        cursor_after = next_cursor_from_events(normalised, previous=device.sync_cursor)

    _close_sync_run(session, run=run, device=device, report=report, cursor_after=cursor_after)

    audit.record(
        session,
        action="device.synced",
        context=context,
        entity_type="device",
        entity_id=device.id,
        extra={**report.as_dict(), "trigger": trigger, "sync_run_id": run.id},
    )
    session.flush()
    return {**report.as_dict(), "sync_run_id": run.id, "cursor_after": cursor_after}


def ingest_pushed_body(
    session: Session,
    *,
    device: Device,
    body: bytes,
    content_type: str | None,
    trigger: str,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Ingest a body delivered by a push adapter - a webhook or an upload."""
    adapter = get_adapter(device.adapter_type)
    if not isinstance(adapter, PushAdapter):
        raise PolicyViolation(
            f"{device.code} uses the {device.adapter_type!r} adapter, which is polled "
            "rather than pushed to."
        )

    window = f"{utc_now().strftime('%Y%m%dT%H%M%S%f')}"
    run = _open_sync_run(session, device=device, trigger=trigger, window=window, context=context)

    try:
        payloads = adapter.extract(config=device.config, body=body, content_type=content_type)
    except DeviceDataError as error:
        # Recorded, not raised: a device that keeps POSTing an unreadable body
        # should show up in the sync log rather than only in a 4xx the device
        # discards.
        return _fail_run(
            session,
            run=run,
            device=device,
            context=context,
            trigger=trigger,
            reason=str(error),
        )

    try:
        report = ingest(session, device=device, payloads=payloads, sync_run=run, context=context)
    except ValidationFailed as error:
        return _fail_run(
            session,
            run=run,
            device=device,
            context=context,
            trigger=trigger,
            reason=str(error),
            fetched=len(payloads),
        )

    _close_sync_run(session, run=run, device=device, report=report, cursor_after=device.sync_cursor)

    audit.record(
        session,
        action="device.events_received",
        context=context,
        entity_type="device",
        entity_id=device.id,
        extra={**report.as_dict(), "trigger": trigger, "sync_run_id": run.id},
    )
    session.flush()
    return {**report.as_dict(), "sync_run_id": run.id}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reprocess_events(
    session: Session,
    *,
    device_id: int,
    statuses: list[str],
    limit: int,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Re-apply stored raw events after fixing a mapping or configuration.

    This is why raw events are kept: a wrong field mapping or a missing employee
    mapping is correctable after the fact, without asking the device to re-send
    anything.

    ``mapped`` events are deliberately not reprocessable here - they already
    produced an attendance event, and re-applying would duplicate it.
    """
    device = get_device(session, device_id)

    allowed = {"pending", "unmapped", "failed"}
    requested = set(statuses) & allowed
    if not requested:
        raise ValidationFailed(
            f"Choose at least one of: {', '.join(sorted(allowed))}. Already-mapped "
            "events cannot be reprocessed, since that would duplicate the punch.",
            details=[{"field": "statuses", "message": "Nothing reprocessable selected."}],
        )

    rows = list(
        session.scalars(
            select(DeviceRawEvent)
            .where(
                DeviceRawEvent.device_id == device_id,
                DeviceRawEvent.status.in_(requested),
            )
            .order_by(DeviceRawEvent.parsed_occurred_at.nulls_last())
            .limit(limit)
        ).all()
    )

    timezone_name = device_timezone(session, device)
    tz = resolve_timezone(timezone_name)

    counts = {"mapped": 0, "unmapped": 0, "failed": 0, "duplicate": 0}
    affected: set[tuple[int, date]] = set()

    for raw in rows:
        raw.process_attempts += 1
        raw.last_attempt_at = utc_now()
        outcome = _process_raw_event(session, device=device, raw=raw, tz=tz)
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
        if outcome.status == "mapped" and outcome.employee_id and outcome.work_date:
            affected.add((outcome.employee_id, outcome.work_date))

    for employee_id, work_date in sorted(affected):
        attendance_service.recompute_day(session, employee_id=employee_id, work_date=work_date)

    result = {
        "device_code": device.code,
        "examined": len(rows),
        **counts,
        "days_recomputed": len(affected),
    }

    audit.record(
        session,
        action="device.events_reprocessed",
        context=context,
        entity_type="device",
        entity_id=device_id,
        extra=result,
    )
    session.flush()
    return result


def resolve_unmapped(
    session: Session,
    *,
    raw_event_id: int,
    employee_id: int,
    create_mapping: bool,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Attach an unmapped event to an employee, optionally creating the mapping.

    ``create_mapping`` is the useful path: the same device identifier will keep
    arriving, so recording the mapping fixes every future punch as well as this
    one. The mapping starts from the punch's own date, not today, so other
    stored events for the same identifier become resolvable too.
    """
    raw = session.get(DeviceRawEvent, raw_event_id)
    if raw is None:
        raise NotFound("Device event not found.")
    if raw.status not in {"unmapped", "failed", "pending"}:
        raise Conflict(
            f"That event is {raw.status}; only pending, unmapped or failed events "
            "can be resolved by hand."
        )

    employee = session.get(Employee, employee_id)
    if employee is None:
        raise ValidationFailed(
            "The employee does not exist.",
            details=[{"field": "employee_id", "message": "Unknown."}],
        )

    device = get_device(session, raw.device_id)
    timezone_name = device_timezone(session, device)
    tz = resolve_timezone(timezone_name)

    created_mapping = False
    if create_mapping and raw.external_employee_ref:
        existing = session.scalar(
            select(DeviceEmployeeMapping).where(
                DeviceEmployeeMapping.device_id == device.id,
                DeviceEmployeeMapping.external_employee_ref == raw.external_employee_ref,
                DeviceEmployeeMapping.effective_to.is_(None),
            )
        )
        if existing is None:
            start = (
                raw.parsed_occurred_at.astimezone(tz).date()
                if raw.parsed_occurred_at
                else date.today()
            )
            add_mapping(
                session,
                device.id,
                external_employee_ref=raw.external_employee_ref,
                employee_id=employee_id,
                effective_from=min(start, employee.date_of_joining)
                if employee.date_of_joining > start
                else start,
                effective_to=None,
                notes=f"Created while resolving device event #{raw.id}.",
                context=context,
            )
            created_mapping = True

    raw.resolved_by_user_id = context.actor_user_id
    raw.resolution_note = f"Manually attributed to {employee.employee_code}." + (
        " Mapping created." if created_mapping else ""
    )
    raw.process_attempts += 1
    raw.last_attempt_at = utc_now()
    session.flush()

    outcome = _process_raw_event(session, device=device, raw=raw, tz=tz)
    if outcome.status == "mapped" and outcome.employee_id and outcome.work_date:
        attendance_service.recompute_day(
            session, employee_id=outcome.employee_id, work_date=outcome.work_date
        )

    audit.record(
        session,
        action="device.event_resolved",
        context=context,
        entity_type="device",
        entity_id=device.id,
        extra={
            "raw_event_id": raw.id,
            "employee_code": employee.employee_code,
            "mapping_created": created_mapping,
            "outcome": outcome.status,
        },
    )
    session.flush()
    return {
        "raw_event_id": raw.id,
        "outcome": outcome.status,
        "mapping_created": created_mapping,
        "employee_code": employee.employee_code,
    }


def scheduled_sync(
    session: Session, *, context: audit.AuditContext, force: bool = False
) -> dict[str, Any]:
    """Poll every pollable device once for the current interval window.

    Idempotent twice over, which is what the brief requires of a scheduled job:

    * The **window** is the current interval slot, not the current instant, so a
      scheduler that fires twice in one slot computes the same sync key and the
      second run fetches nothing.
    * Each device's **events** are deduplicated on arrival regardless, so even a
      forced re-fetch cannot double-count a punch.

    One device failing never stops the others: a dead reader must not block the
    whole estate's attendance.
    """
    from app.modules.settings import service as settings_service

    enabled = bool(settings_service.get_value(session, "devices.auto_sync_enabled", default=False))
    if not enabled and not force:
        return {
            "status": "skipped",
            "reason": (
                "devices.auto_sync_enabled is off. Web punching is unaffected; this "
                "only governs hardware polling."
            ),
            "devices": [],
        }

    interval = int(
        settings_service.get_value(session, "devices.auto_sync_interval_minutes", default=30)
    )
    interval = max(interval, 1)

    # The window is the interval slot, so every firing within one slot shares a
    # key. Minute-of-day divided by the interval is stable and readable in the
    # sync log, unlike a hash.
    now = utc_now()
    slot = (now.hour * 60 + now.minute) // interval
    window = f"{now.strftime('%Y%m%d')}-{slot:04d}"

    results: list[dict[str, Any]] = []
    for device in list_devices(session):
        adapter = get_adapter(device.adapter_type)
        if not isinstance(adapter, PullAdapter):
            continue
        try:
            outcome = sync_device(
                session,
                device_id=device.id,
                trigger="scheduled",
                context=context,
                window=window,
            )
            results.append({"code": device.code, **outcome})
        except Exception as exc:
            # A dead reader must not stop the rest of the estate from syncing.
            logger.exception("Scheduled sync of %s failed", device.code)
            results.append(
                {
                    "code": device.code,
                    "status": "failed",
                    "problems": [{"reason": f"{type(exc).__name__}", "field": None}],
                }
            )

    return {
        "status": "succeeded" if all(r.get("status") != "failed" for r in results) else "partial",
        "window": window,
        "interval_minutes": interval,
        "devices": results,
        "accepted_total": sum(int(r.get("accepted", 0)) for r in results),
    }


def record_rejected_delivery(*, device_code: str, reason: str, context: audit.AuditContext) -> None:
    """Record a webhook delivery that failed authentication, durably.

    Written in its own committed transaction, because the caller then raises and
    the request transaction is rolled back. Sharing it would erase the only
    evidence that someone is probing the webhook with bad signatures - the same
    trap that silently disables a failed-sign-in counter.

    A failure to write here never masks the authentication error the caller is
    about to raise.
    """
    from app.db.session import session_scope

    try:
        with session_scope() as side_session:
            audit.record(
                side_session,
                action="device.delivery_rejected",
                context=context,
                entity_type="device",
                entity_id=device_code,
                succeeded=False,
                extra={"reason": reason},
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("Could not record a rejected device delivery for %s", device_code)


def health_overview(session: Session) -> dict[str, Any]:
    """Device health, including the failure nobody notices.

    A device that stops reporting raises no error - it just produces a slowly
    growing hole in attendance that surfaces at month end. ``quiet_devices``
    makes that visible.
    """
    from app.modules.settings import service as settings_service

    now = utc_now()
    quiet_after_minutes = settings_service.get_value(
        session, "devices.quiet_alert_after_minutes", default=1440
    )

    devices = list_devices(session, include_inactive=True)
    quiet: list[dict[str, Any]] = []
    never_reported: list[dict[str, Any]] = []

    for device in devices:
        if not device.is_active:
            continue
        if device.last_seen_at is None:
            never_reported.append({"id": device.id, "code": device.code, "name": device.name})
        elif now - device.last_seen_at > timedelta(minutes=quiet_after_minutes):
            quiet.append(
                {
                    "id": device.id,
                    "code": device.code,
                    "name": device.name,
                    "last_seen_at": device.last_seen_at.isoformat(),
                    "silent_for_hours": round(
                        (now - device.last_seen_at).total_seconds() / 3600, 1
                    ),
                }
            )

    pending_counts = {
        status: count
        for status, count in session.execute(
            select(DeviceRawEvent.status, func.count())
            .where(DeviceRawEvent.status.in_(["pending", "unmapped", "failed"]))
            .group_by(DeviceRawEvent.status)
        ).all()
    }

    failing = [
        {
            "id": device.id,
            "code": device.code,
            "consecutive_failures": device.consecutive_failures,
            "last_sync_status": device.last_sync_status,
        }
        for device in devices
        if device.consecutive_failures > 0
    ]

    return {
        "as_of": now.isoformat(),
        "devices_total": len(devices),
        "devices_active": sum(1 for device in devices if device.is_active),
        "quiet_after_minutes": quiet_after_minutes,
        "quiet_devices": quiet,
        "never_reported": never_reported,
        "failing_devices": failing,
        "events_needing_attention": pending_counts,
        "events_needing_attention_total": sum(pending_counts.values()),
    }
