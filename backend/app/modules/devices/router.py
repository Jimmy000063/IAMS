"""Device integration endpoints.

Authorization note: every endpoint here is permission-checked in the backend
except one - the webhook receiver, which a device calls and which therefore
cannot carry a bearer token. That endpoint is authenticated by an HMAC-SHA256
signature over the raw request body, verified before the body is parsed. It is
the only unauthenticated write path in the application and is deliberately
narrow: it can create device events for one device and nothing else.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.deps import (
    AnonymousAuditCtx,
    AuditCtx,
    DbSession,
    get_client_ip,
    require,
)
from app.core.errors import (
    AuthenticationFailed,
    NotFound,
    PolicyViolation,
    ValidationFailed,
)
from app.modules.auth.models import User
from app.modules.devices import service
from app.modules.devices.adapters.base import (
    UNIMPLEMENTED_VENDOR_NOTES,
    all_descriptions,
)
from app.modules.devices.models import Device, DeviceRawEvent, DeviceSyncRun
from app.modules.devices.policy import (
    TIMESTAMP_FORMATS,
    DeviceDataError,
    check_source_ip,
    verify_signature,
)
from app.modules.devices.schemas import (
    AdapterCatalogueResponse,
    AdapterResponse,
    ConfigFieldResponse,
    DeviceCreate,
    DeviceHealthResponse,
    DeviceResponse,
    DeviceUpdate,
    IngestResponse,
    MappingClose,
    MappingCreate,
    MappingResponse,
    RawEventResponse,
    ReprocessRequest,
    ResolveRequest,
    SyncRequest,
    SyncRunResponse,
)

router = APIRouter(prefix="/devices", tags=["Attendance Devices"])

MATURITY_MEANING = {
    "tested": (
        "Implemented and covered by automated tests against this contract. Whether "
        "your machine can produce this format is a question about the machine."
    ),
    "generic": (
        "Implemented and tested against a stub, but never exercised against "
        "physical hardware. Verify it against your unit before relying on it for "
        "payroll."
    ),
    "development_only": (
        "A development aid. Refuses to run outside a local or test environment so "
        "it cannot fabricate attendance in a live system."
    ),
}

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


# ------------------------------------------------------------------ catalogue


@router.get(
    "/adapters",
    response_model=AdapterCatalogueResponse,
    summary="What device integrations exist, and what they honestly are",
)
def adapter_catalogue(
    _user: Annotated[User, Depends(require("device.read"))],
) -> AdapterCatalogueResponse:
    """List the available adapters with their maturity stated plainly.

    ``vendor_integration_notes`` says what is deliberately not implemented. An
    operator looking for a named biometric product gets a straight answer here
    rather than discovering the gap after configuring a device.
    """
    return AdapterCatalogueResponse(
        adapters=[
            AdapterResponse(
                adapter_type=description.adapter_type,
                name=description.name,
                mode=description.mode,
                maturity=description.maturity,
                maturity_meaning=MATURITY_MEANING[description.maturity],
                summary=description.summary,
                requires_api_secret=description.requires_api_secret,
                requires_webhook_secret=description.requires_webhook_secret,
                config_fields=[
                    ConfigFieldResponse(
                        key=item.key,
                        label=item.label,
                        required=item.required,
                        help_text=item.help_text,
                        example=item.example,
                    )
                    for item in description.config_fields
                ],
                caveats=list(description.caveats),
            )
            for description in all_descriptions()
        ],
        timestamp_formats=sorted(TIMESTAMP_FORMATS),
        vendor_integration_notes=list(UNIMPLEMENTED_VENDOR_NOTES),
    )


@router.get(
    "/health",
    response_model=DeviceHealthResponse,
    summary="Device health, including devices that have gone silent",
)
def device_health(
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
) -> DeviceHealthResponse:
    """Surface the failure that otherwise goes unnoticed.

    A device that stops reporting raises no error - it leaves a slowly growing
    hole in attendance that surfaces at month end. ``quiet_devices`` names them
    while the gap is still small enough to fix.
    """
    return DeviceHealthResponse(**service.health_overview(session))


# --------------------------------------------------------------------- devices


def _device_response(device: Device) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        code=device.code,
        name=device.name,
        description=device.description,
        adapter_type=device.adapter_type,
        vendor=device.vendor,
        model=device.model,
        serial_number=device.serial_number,
        firmware_version=device.firmware_version,
        work_location_id=device.work_location_id,
        timezone_override=device.timezone_override,
        config=device.config,
        direction_mode=device.direction_mode,
        clock_drift_tolerance_seconds=device.clock_drift_tolerance_seconds,
        allowed_source_ips=device.allowed_source_ips,
        is_active=device.is_active,
        sync_cursor=device.sync_cursor,
        last_sync_at=device.last_sync_at,
        last_sync_status=device.last_sync_status,
        last_seen_at=device.last_seen_at,
        consecutive_failures=device.consecutive_failures,
        has_api_secret=device.has_api_secret,
        has_webhook_secret=device.has_webhook_secret,
        is_push_adapter=device.is_push_adapter,
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


@router.get("", response_model=list[DeviceResponse], summary="List devices")
def list_devices(
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
    include_inactive: Annotated[bool, Query()] = False,
) -> list[DeviceResponse]:
    return [
        _device_response(device)
        for device in service.list_devices(session, include_inactive=include_inactive)
    ]


@router.post(
    "",
    response_model=DeviceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a device",
)
def create_device(
    payload: DeviceCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.manage"))],
) -> DeviceResponse:
    """Register a device and validate its adapter configuration immediately.

    The configuration is checked now rather than at the first sync, so a bad
    field mapping surfaces while the person who wrote it is still looking at the
    screen.
    """
    device = service.create_device(
        session, data=payload.model_dump(exclude_unset=True), context=context
    )
    return _device_response(device)


@router.get("/{device_id}", response_model=DeviceResponse, summary="Read a device")
def read_device(
    device_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
) -> DeviceResponse:
    return _device_response(service.get_device(session, device_id))


@router.patch("/{device_id}", response_model=DeviceResponse, summary="Update a device")
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.manage"))],
) -> DeviceResponse:
    device = service.update_device(
        session, device_id, data=payload.model_dump(exclude_unset=True), context=context
    )
    return _device_response(device)


@router.get(
    "/{device_id}/webhook-url",
    summary="The delivery URL to configure on the device",
)
def webhook_url(
    device_id: int,
    request: Request,
    session: DbSession,
    _user: Annotated[User, Depends(require("device.manage"))],
) -> dict[str, object]:
    """Tell the operator exactly what to configure on the device.

    Returns the URL and the signing instructions, never the secret itself - a
    secret that can be read back is one that can leak from a browser history, a
    screenshot or a support ticket.
    """
    device = service.get_device(session, device_id)
    if not device.is_push_adapter:
        raise PolicyViolation(
            f"{device.code} is polled rather than pushed to; it has no webhook URL."
        )

    base = str(request.base_url).rstrip("/")
    settings = get_settings()
    path = f"{settings.api_v1_prefix}/devices/webhook/{device.code}"

    return {
        "url": f"{base}{path}",
        "method": "POST",
        "content_type": "application/json",
        "signature_header": device.config.get("signature_header", "X-Device-Signature"),
        "signature_algorithm": "HMAC-SHA256, hex digest of the raw request body",
        "secret_configured": device.has_webhook_secret,
        "notes": [
            "Sign the exact bytes you send. Re-serialising the body before signing "
            "will produce a different digest and the delivery will be rejected.",
            "A 'sha256=' prefix on the header value is accepted.",
            "Retrying a failed delivery is safe: events are deduplicated by their "
            "id, so a repeat cannot double-count a punch.",
            "The secret cannot be read back. If it has been lost, set a new one - "
            "deliveries signed with the old one will then be rejected.",
        ],
    }


# -------------------------------------------------------------------- mappings


@router.get(
    "/{device_id}/mappings",
    response_model=list[MappingResponse],
    summary="List a device's employee mappings",
)
def list_mappings(
    device_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
) -> list[MappingResponse]:
    service.get_device(session, device_id)
    return [
        MappingResponse.model_validate(mapping)
        for mapping in service.list_mappings(session, device_id)
    ]


@router.post(
    "/{device_id}/mappings",
    response_model=MappingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Map a device identifier to an employee",
)
def add_mapping(
    device_id: int,
    payload: MappingCreate,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.manage"))],
) -> MappingResponse:
    """Record which employee a device identifier refers to, and from when.

    Dated rather than absolute: enrolment slots get reused, and a punch from
    last March must resolve to whoever held that slot in March.
    """
    mapping = service.add_mapping(
        session,
        device_id,
        external_employee_ref=payload.external_employee_ref,
        employee_id=payload.employee_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        notes=payload.notes,
        context=context,
    )
    return MappingResponse.model_validate(mapping)


@router.post(
    "/mappings/{mapping_id}/close",
    response_model=MappingResponse,
    summary="End a device mapping",
)
def close_mapping(
    mapping_id: int,
    payload: MappingClose,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.manage"))],
) -> MappingResponse:
    mapping = service.close_mapping(
        session, mapping_id, effective_to=payload.effective_to, context=context
    )
    return MappingResponse.model_validate(mapping)


# ------------------------------------------------------------------ ingestion


@router.post(
    "/{device_id}/sync",
    response_model=IngestResponse,
    summary="Poll a device now",
)
def sync_now(
    device_id: int,
    payload: SyncRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.sync"))],
) -> IngestResponse:
    """Run a synchronisation immediately.

    Returns **200 with ``status: "failed"``** when the device could not be
    reached or its response could not be read. The run is recorded either way;
    raising would roll back the record that says it failed. A 4xx here means the
    request itself was rejected - the device is inactive, or is one that pushes
    rather than one that is polled.
    """
    result = service.sync_device(
        session,
        device_id=device_id,
        trigger="manual",
        context=context,
        window=payload.window,
    )
    return IngestResponse(**{key: value for key, value in result.items() if key != "replayed"})


@router.post(
    "/{device_id}/upload",
    response_model=IngestResponse,
    summary="Upload a punch log exported from a device",
)
async def upload_log(
    device_id: int,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.sync"))],
    file: Annotated[UploadFile, File(description="The device's exported punch log.")],
) -> IngestResponse:
    """Ingest a file exported from a device.

    The pragmatic path for a machine with no network integration: almost every
    attendance device can export a punch log. Re-uploading a file that overlaps
    a previous one is safe - the overlapping punches are recognised as
    duplicates.
    """
    device = service.get_device(session, device_id)

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise ValidationFailed(
            f"The file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
            details=[{"field": "file", "message": "Too large."}],
        )
    if not body:
        raise ValidationFailed(
            "The uploaded file is empty.",
            details=[{"field": "file", "message": "Empty."}],
        )

    result = service.ingest_pushed_body(
        session,
        device=device,
        body=body,
        content_type=file.content_type,
        trigger="upload",
        context=context,
    )
    return IngestResponse(**result)


@router.post(
    "/webhook/{device_code}",
    response_model=IngestResponse,
    summary="Receive punches pushed by a device (signature-authenticated)",
    # Excluded from the authenticated schema group deliberately; the signature
    # is the credential, and documenting it is how an integrator configures it.
    openapi_extra={
        "security": [],
        "description": (
            "Called by the device, not by a person. There is no bearer token: a "
            "device will not perform a sign-in flow. Authentication is an "
            "HMAC-SHA256 hex digest of the raw request body in the signature "
            "header, verified before the body is parsed."
        ),
    },
)
async def receive_webhook(
    device_code: str,
    request: Request,
    session: DbSession,
    context: AnonymousAuditCtx,
) -> IngestResponse:
    """The one unauthenticated write path in the application.

    Order of checks matters and is deliberate:

    1. The device must exist, be active and have a webhook secret. A device
       without a secret is refused rather than accepted unauthenticated.
    2. The optional source-address allowlist, as defence in depth only.
    3. The HMAC signature, over the **raw bytes**, before any parsing. Verifying
       after parsing would let an attacker alter semantically-equivalent content
       without breaking the signature.

    A failing signature returns 401 with no detail about why, and the body is
    never parsed or stored.
    """
    device = session.scalar(select(Device).where(Device.code == device_code.strip().upper()))
    if device is None or not device.is_active:
        # Same response for an unknown code and an inactive device, so this
        # endpoint cannot be used to enumerate which device codes exist.
        raise NotFound("No active device with that code.")

    if not device.is_push_adapter:
        raise PolicyViolation(f"{device.code} is polled rather than pushed to; it has no webhook.")

    if device.webhook_secret_encrypted is None:
        raise PolicyViolation(
            f"{device.code} has no webhook secret configured, so a delivery cannot "
            "be authenticated. Set one before pointing the device at this URL."
        )

    body = await request.body()

    header_name = str(device.config.get("signature_header", "X-Device-Signature"))
    secret = decrypt(device.webhook_secret_encrypted)
    if secret is None:  # pragma: no cover - a stored secret must decrypt
        raise PolicyViolation(
            f"{device.code}'s webhook secret could not be decrypted. Check the "
            "field encryption key, then set the secret again."
        )

    try:
        check_source_ip(get_client_ip(request), device.allowed_source_ips)
        verify_signature(
            body=body,
            provided_signature=request.headers.get(header_name),
            secret=secret,
        )
    except DeviceDataError as error:
        # The reason is recorded for the operator but not returned: telling a
        # caller whether it failed on the address or the digest tells them how
        # to get closer.
        service.record_rejected_delivery(
            device_code=device.code, reason=str(error), context=context
        )
        raise AuthenticationFailed("The delivery could not be authenticated.") from error

    result = service.ingest_pushed_body(
        session,
        device=device,
        body=body,
        content_type=request.headers.get("content-type"),
        trigger="webhook",
        context=context,
    )
    return IngestResponse(**result)


# ------------------------------------------------------------- reconciliation


@router.get(
    "/{device_id}/sync-runs",
    response_model=list[SyncRunResponse],
    summary="A device's synchronisation history",
)
def list_sync_runs(
    device_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SyncRunResponse]:
    service.get_device(session, device_id)
    runs = session.scalars(
        select(DeviceSyncRun)
        .where(DeviceSyncRun.device_id == device_id)
        .order_by(DeviceSyncRun.started_at.desc())
        .limit(limit)
    ).all()
    return [SyncRunResponse.model_validate(run) for run in runs]


@router.get(
    "/{device_id}/events",
    response_model=list[RawEventResponse],
    summary="A device's stored events, for reconciliation",
)
def list_raw_events(
    device_id: int,
    session: DbSession,
    _user: Annotated[User, Depends(require("device.read"))],
    event_status: Annotated[
        str | None,
        Query(
            alias="status",
            description=(
                "Filter by outcome. 'unmapped' and 'failed' are the reconciliation "
                "queue - events a device sent that could not be attributed."
            ),
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[RawEventResponse]:
    """Everything a device sent, including what could not be used.

    Nothing a device delivers is discarded, which is what makes a configuration
    mistake correctable after the fact rather than a permanent hole.
    """
    service.get_device(session, device_id)

    statement = select(DeviceRawEvent).where(DeviceRawEvent.device_id == device_id)
    if event_status:
        statement = statement.where(DeviceRawEvent.status == event_status)

    rows = session.scalars(statement.order_by(DeviceRawEvent.received_at.desc()).limit(limit)).all()
    return [RawEventResponse.model_validate(row) for row in rows]


@router.post(
    "/{device_id}/reprocess",
    summary="Re-apply stored events after fixing a mapping",
)
def reprocess(
    device_id: int,
    payload: ReprocessRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.reconcile"))],
) -> dict[str, object]:
    """Re-run stored events through the pipeline.

    This is why raw events are kept. A wrong field mapping or a missing employee
    mapping can be fixed and the stored events re-applied, without asking the
    device to re-send anything. Already-mapped events are refused: they produced
    an attendance event, and re-applying would duplicate the punch.
    """
    return service.reprocess_events(
        session,
        device_id=device_id,
        statuses=list(payload.statuses),
        limit=payload.limit,
        context=context,
    )


@router.post(
    "/events/{raw_event_id}/resolve",
    summary="Attribute an unmapped event to an employee",
)
def resolve_event(
    raw_event_id: int,
    payload: ResolveRequest,
    session: DbSession,
    context: AuditCtx,
    _user: Annotated[User, Depends(require("device.reconcile"))],
) -> dict[str, object]:
    """Attach an event to an employee by hand, and optionally learn the mapping.

    With ``create_mapping`` the identifier is recorded from the punch's own date
    forward, so other stored events for the same identifier become resolvable
    too and future punches need no intervention.
    """
    return service.resolve_unmapped(
        session,
        raw_event_id=raw_event_id,
        employee_id=payload.employee_id,
        create_mapping=payload.create_mapping,
        context=context,
    )
