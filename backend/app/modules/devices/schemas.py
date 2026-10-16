"""Request and response models for device integration.

Secrets are write-only throughout: a device's API or webhook secret can be set
but never read back. Responses carry ``has_api_secret`` instead, which is what
an operator actually needs to know.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AdapterType = Literal["csv_upload", "webhook", "rest_poll", "simulator"]
DirectionMode = Literal["explicit", "alternate", "always_in", "always_out"]
RawEventStatus = Literal["pending", "mapped", "duplicate", "unmapped", "failed", "ignored"]
ReprocessableStatus = Literal["pending", "unmapped", "failed"]


class DeviceCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "code": "GATE-1",
                    "name": "Main gate reader",
                    "adapter_type": "webhook",
                    "direction_mode": "alternate",
                    "webhook_secret": "a-long-random-string-generated-by-you",
                    "config": {
                        "field_mapping": {
                            "employee_ref": "user_id",
                            "timestamp": "punch_time",
                            "timestamp_format": "ymd_hms",
                            "event_id": "id",
                        }
                    },
                },
                {
                    "code": "EXPORT",
                    "name": "Monthly CSV export",
                    "adapter_type": "csv_upload",
                    "direction_mode": "explicit",
                    "config": {
                        "delimiter": ",",
                        "field_mapping": {
                            "employee_ref": "EmpCode",
                            "timestamp": "PunchTime",
                            "timestamp_format": "dmy_hms_slash",
                            "direction": "InOut",
                            "direction_map": {"0": "in", "1": "out"},
                        },
                    },
                },
            ]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    adapter_type: AdapterType
    vendor: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    serial_number: str | None = Field(default=None, max_length=80)
    firmware_version: str | None = Field(default=None, max_length=40)

    work_location_id: int | None = None
    timezone_override: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "IANA zone used to interpret this device's naive timestamps. Leave "
            "empty to use the work location's zone, then the company default. "
            "The server's own timezone is never used."
        ),
    )

    config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Adapter-specific settings, including the field mapping. The adapter "
            "catalogue endpoint lists what each adapter accepts."
        ),
    )

    direction_mode: DirectionMode = Field(
        default="alternate",
        description=(
            "How a punch's direction is decided. 'explicit' trusts the device; "
            "'alternate' infers in/out from the previous punch; 'always_in' and "
            "'always_out' suit single-purpose readers."
        ),
    )
    clock_drift_tolerance_seconds: int = Field(
        default=300,
        ge=0,
        le=86_400,
        description=(
            "How far ahead of server time an event may be stamped before it is "
            "rejected. Device clocks drift; a punch from the future would credit "
            "unworked time."
        ),
    )
    allowed_source_ips: list[str] | None = Field(
        default=None,
        max_length=50,
        description=(
            "Optional allowlist for webhook deliveries. Defence in depth only - "
            "the HMAC signature is the actual authentication."
        ),
    )
    is_active: bool = True

    api_secret: str | None = Field(
        default=None,
        max_length=500,
        description="Write-only. Used when polling a device that requires a credential.",
    )
    webhook_secret: str | None = Field(
        default=None,
        min_length=16,
        max_length=500,
        description=(
            "Write-only. The HMAC-SHA256 key the device signs its deliveries with. "
            "At least 16 characters - this is the only thing standing between the "
            "webhook and anyone who knows its URL."
        ),
    )

    @field_validator("code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    adapter_type: AdapterType | None = None
    vendor: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    serial_number: str | None = Field(default=None, max_length=80)
    firmware_version: str | None = Field(default=None, max_length=40)
    work_location_id: int | None = None
    timezone_override: str | None = Field(default=None, max_length=64)
    config: dict[str, Any] | None = None
    direction_mode: DirectionMode | None = None
    clock_drift_tolerance_seconds: int | None = Field(default=None, ge=0, le=86_400)
    allowed_source_ips: list[str] | None = Field(default=None, max_length=50)
    is_active: bool | None = None

    api_secret: str | None = Field(
        default=None,
        max_length=500,
        description="Write-only. Send an empty string to clear it.",
    )
    webhook_secret: str | None = Field(default=None, max_length=500)


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    adapter_type: str
    vendor: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    work_location_id: int | None
    timezone_override: str | None
    config: dict[str, Any]
    direction_mode: str
    clock_drift_tolerance_seconds: int
    allowed_source_ips: list[str] | None
    is_active: bool

    sync_cursor: str | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_seen_at: datetime | None
    consecutive_failures: int

    # Presence, never the value.
    has_api_secret: bool
    has_webhook_secret: bool
    is_push_adapter: bool

    created_at: datetime
    updated_at: datetime


class MappingCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "external_employee_ref": "47",
                    "employee_id": 12,
                    "effective_from": "2026-04-01",
                    "notes": "Enrolment slot 47 on the main gate reader.",
                }
            ]
        },
    )

    external_employee_ref: str = Field(
        min_length=1,
        max_length=80,
        description="The device's own identifier for the person - its enrolment id.",
    )
    employee_id: int
    effective_from: date = Field(
        description=(
            "Mappings are dated because enrolment slots get reused. A punch is "
            "attributed using the mapping in force on the punch's own date."
        )
    )
    effective_to: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class MappingClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_to: date = Field(
        description="The last date this identifier referred to this employee."
    )


class MappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    external_employee_ref: str
    employee_id: int
    effective_from: date
    effective_to: date | None
    notes: str | None
    created_at: datetime


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: str | None = Field(
        default=None,
        max_length=60,
        description=(
            "Optional idempotency window. Two syncs with the same window are "
            "refused as a repeat, which is how a retried scheduled job avoids "
            "re-fetching."
        ),
    )


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    events_fetched: int
    events_accepted: int
    events_duplicate: int
    events_unmapped: int
    events_failed: int
    events_ignored: int
    attendance_days_recomputed: int
    cursor_before: str | None
    cursor_after: str | None
    error_message: str | None
    details: dict[str, Any] | None


class IngestResponse(BaseModel):
    """Per-outcome counts, deliberately not a single total.

    "fetched 200, accepted 3, unmapped 197" is a broken configuration, while
    "fetched 200, accepted 200" is a healthy sync. One number cannot tell them
    apart, so none is offered.
    """

    device_code: str
    status: Literal["succeeded", "partial", "failed"]
    fetched: int
    accepted: int
    duplicate: int
    unmapped: int
    failed: int
    ignored: int
    days_recomputed: int
    problems: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Up to 50 problems, each with a reason and the offending values.",
    )
    sync_run_id: int | None = None
    cursor_after: str | None = None


class RawEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    sync_run_id: int | None
    external_event_id: str
    external_employee_ref: str | None
    raw_timestamp: str | None
    raw_direction: str | None
    parsed_occurred_at: datetime | None
    parsed_direction: str | None
    timezone_used: str | None
    status: str
    employee_id: int | None
    attendance_event_id: int | None
    error_message: str | None
    process_attempts: int
    last_attempt_at: datetime | None
    received_at: datetime
    resolution_note: str | None
    raw_payload: dict[str, Any]


class ReprocessRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"statuses": ["unmapped", "failed"], "limit": 500}]},
    )

    statuses: list[ReprocessableStatus] = Field(
        default_factory=lambda: ["unmapped", "failed"],
        min_length=1,
        description=(
            "Already-mapped events are not reprocessable: they produced an "
            "attendance event, and re-applying would duplicate the punch."
        ),
    )
    limit: int = Field(default=500, ge=1, le=5000)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    create_mapping: bool = Field(
        default=True,
        description=(
            "Also record the device identifier against this employee, from the "
            "punch's own date, so future and other stored punches resolve too."
        ),
    )


class ConfigFieldResponse(BaseModel):
    key: str
    label: str
    required: bool
    help_text: str
    example: str


class AdapterResponse(BaseModel):
    adapter_type: str
    name: str
    mode: Literal["pull", "push"]
    maturity: Literal["tested", "generic", "development_only"]
    maturity_meaning: str
    summary: str
    requires_api_secret: bool
    requires_webhook_secret: bool
    config_fields: list[ConfigFieldResponse]
    caveats: list[str]


class AdapterCatalogueResponse(BaseModel):
    adapters: list[AdapterResponse]
    timestamp_formats: list[str]
    vendor_integration_notes: list[str] = Field(
        description=(
            "What is deliberately not implemented, and why. Surfaced so an "
            "operator looking for their machine gets a straight answer."
        )
    )


class DeviceHealthResponse(BaseModel):
    as_of: str
    devices_total: int
    devices_active: int
    quiet_after_minutes: int
    quiet_devices: list[dict[str, Any]] = Field(
        description=(
            "Active devices that have stopped reporting. This is the failure that "
            "raises no error - it just leaves a hole in attendance."
        )
    )
    never_reported: list[dict[str, Any]]
    failing_devices: list[dict[str, Any]]
    events_needing_attention: dict[str, int]
    events_needing_attention_total: int
