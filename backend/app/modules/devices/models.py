"""Attendance device registry, raw event store and synchronisation logs.

What this module does and does not claim
----------------------------------------
It provides a **provider-independent** ingestion pipeline: adapters produce raw
events in a common shape, and one shared path deduplicates them, maps them to
employees, turns them into attendance events and recomputes the affected days.

It does **not** claim support for any particular biometric or access-control
machine. The brief is explicit about that, and it is the right call: every
vendor has its own protocol, authentication and quirks, and an adapter written
against a datasheet rather than a real device is a liability. The generic
adapters here (CSV, webhook, REST polling) work against devices whose output can
be described by a field mapping; a vendor-specific adapter is added once its
protocol is in hand and has been tested against the hardware.

Web punching never depends on any of this.

Three tables, three jobs
------------------------
* ``devices`` - what we poll or accept pushes from, and how to interpret it.
* ``device_raw_events`` - every event exactly as received, forever. This is the
  forensic record: if a mapping was wrong or an adapter had a bug, the original
  payload is still there to reprocess.
* ``device_sync_runs`` - one row per synchronisation attempt, with counts and
  cursor movement, so a silent failure is visible rather than inferred from
  missing attendance.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Jsonb, PrimaryKey, TimestampMixin

#: Adapter implementations that exist and are tested. Deliberately generic:
#: a vendor name here would imply a tested integration with that hardware.
ADAPTER_TYPES = ("csv_upload", "webhook", "rest_poll", "simulator")

#: How a device reports direction. Many machines report only "a punch happened"
#: and leave in/out to be inferred, which is why ``alternate`` exists.
DIRECTION_MODES = ("explicit", "alternate", "always_in", "always_out")

#: Lifecycle of a raw event.
#:
#: ``pending``   received, not yet processed
#: ``mapped``    turned into an attendance event
#: ``duplicate`` already seen; kept for the record, not re-applied
#: ``unmapped``  no employee matches the device's identifier - needs a human
#: ``failed``    could not be parsed or applied; the error is recorded
#: ``ignored``   deliberately skipped, e.g. a device health heartbeat
RAW_EVENT_STATUSES = ("pending", "mapped", "duplicate", "unmapped", "failed", "ignored")

SYNC_TRIGGERS = ("manual", "scheduled", "webhook", "upload")

SYNC_STATUSES = ("running", "succeeded", "partial", "failed")


class Device(Base, TimestampMixin):
    """An attendance device or feed.

    ``config`` holds adapter-specific settings - endpoint URL, field mapping,
    direction handling. Secrets (API keys, webhook signing secrets) are **not**
    kept there: they go in the encrypted columns below, so a config dump or an
    API response cannot leak them.
    """

    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint(f"adapter_type IN {ADAPTER_TYPES}", name="adapter_type_known"),
        CheckConstraint(f"direction_mode IN {DIRECTION_MODES}", name="direction_mode_known"),
        CheckConstraint("code = upper(code)", name="code_upper"),
        CheckConstraint(
            "clock_drift_tolerance_seconds >= 0 AND clock_drift_tolerance_seconds <= 86400",
            name="drift_tolerance_in_range",
        ),
    )

    id: Mapped[PrimaryKey]
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    adapter_type: Mapped[str] = mapped_column(String(30), nullable=False)

    #: Free-form vendor and model, for the operator's own records. Recording it
    #: here does not imply a tested integration with that model.
    vendor: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="RESTRICT"), nullable=True
    )
    """Where the device sits. Used to resolve the timezone when the device
    reports a local time with no offset."""

    timezone_override: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """
    IANA zone to interpret naive device timestamps in. Falls back to the work
    location, then the company default. A device in a different zone from its
    nominal location is common enough to need this.
    """

    config: Mapped[dict[str, Any]] = mapped_column(
        Jsonb, nullable=False, server_default=text("'{}'::jsonb")
    )
    """Non-secret adapter settings: endpoint, field mapping, direction map."""

    api_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    """Credential for a pull adapter. Encrypted at rest, never returned."""

    webhook_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    """HMAC signing secret for a push adapter. Encrypted at rest, never returned."""

    allowed_source_ips: Mapped[list[str] | None] = mapped_column(Jsonb, nullable=True)
    """
    Optional allowlist for webhook delivery. Defence in depth only - the HMAC
    signature is the real control, since a source address can be spoofed and is
    rewritten by proxies.
    """

    direction_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'alternate'")
    )
    """
    Default is ``alternate`` because the common case is a machine that reports
    a punch without saying whether it was an entry or an exit.
    """

    clock_drift_tolerance_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )
    """
    How far a device timestamp may sit in the future before it is rejected.
    Device clocks drift; accepting a punch stamped two hours ahead would let an
    employee appear to have worked time they had not.
    """

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )

    #: Synchronisation state. ``sync_cursor`` is adapter-defined: a timestamp,
    #: a sequence number, whatever the device uses to mean "everything after
    #: this". Stored opaquely so the pipeline does not need to know.
    sync_cursor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """Last time the device produced anything at all. Drives the "device has
    gone quiet" warning, which is the failure nobody notices otherwise."""

    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    mappings: Mapped[list[DeviceEmployeeMapping]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    sync_runs: Mapped[list[DeviceSyncRun]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )

    @property
    def is_push_adapter(self) -> bool:
        return self.adapter_type == "webhook"

    @property
    def has_api_secret(self) -> bool:
        return self.api_secret_encrypted is not None

    @property
    def has_webhook_secret(self) -> bool:
        return self.webhook_secret_encrypted is not None


class DeviceEmployeeMapping(Base, TimestampMixin):
    """Maps a device's own identifier for a person to an employee.

    Biometric machines identify people by an enrolment number of their own
    ("user 47"), not by the company's employee code. Without this table the
    pipeline has nothing to join on, and this is the single most common reason
    real-world device integrations silently drop punches.

    Effective-dated, because enrolment slots get reused after someone leaves.
    Mapping "user 47" to the person who holds it *now* must not silently
    reattribute last year's punches to them.
    """

    __tablename__ = "device_employee_mappings"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="period_ordered"
        ),
        Index(
            "ix_device_employee_mappings_lookup",
            "device_id",
            "external_employee_ref",
            "effective_from",
        ),
        # One live mapping per device identifier at a time. A reused enrolment
        # slot must have the previous mapping closed first.
        Index(
            "ix_device_employee_mappings_open_unique",
            "device_id",
            "external_employee_ref",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    id: Mapped[PrimaryKey]
    device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    external_employee_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    """The device's identifier, as a string - enrolment numbers are not always
    numeric, and leading zeros matter."""

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    device: Mapped[Device] = relationship(back_populates="mappings")

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


class DeviceSyncRun(Base):
    """One synchronisation attempt.

    ``idempotency_key`` is what makes a retried scheduled run safe: the key is
    derived from the device and the window, so a duplicate run fails on insert
    rather than fetching and re-applying the same events.

    Counts are kept per outcome rather than as a single total, because
    "fetched 200, accepted 3, unmapped 197" is a configuration problem and
    "fetched 200, accepted 200" is a healthy sync - and a single number cannot
    tell them apart.
    """

    __tablename__ = "device_sync_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(f"trigger IN {SYNC_TRIGGERS}", name="trigger_known"),
        CheckConstraint(f"status IN {SYNC_STATUSES}", name="status_known"),
        CheckConstraint(
            "events_fetched >= 0 AND events_accepted >= 0 AND events_duplicate >= 0 "
            "AND events_unmapped >= 0 AND events_failed >= 0 AND events_ignored >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"
        ),
        Index("ix_device_sync_runs_device_id_started_at", "device_id", "started_at"),
    )

    id: Mapped[PrimaryKey]
    device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    events_accepted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    events_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    events_unmapped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    events_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    events_ignored: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    attendance_days_recomputed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    cursor_before: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(String(200), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(Jsonb, nullable=True)

    triggered_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    device: Mapped[Device] = relationship(back_populates="sync_runs")

    @property
    def needs_attention(self) -> bool:
        return self.status in {"failed", "partial"} or bool(
            self.events_unmapped or self.events_failed
        )


class DeviceRawEvent(Base):
    """An event exactly as the device reported it. Append-only.

    Retained verbatim and indefinitely, including the original payload. That is
    what makes the pipeline recoverable: a wrong field mapping, a timezone
    mistake or an adapter bug can be fixed and the events reprocessed, because
    the input survived.

    ``unique(device_id, external_event_id)`` is the deduplication mechanism. A
    device that re-sends its whole buffer after a network outage - which they
    do - cannot create duplicate punches.
    """

    __tablename__ = "device_raw_events"
    __table_args__ = (
        UniqueConstraint("device_id", "external_event_id"),
        CheckConstraint(f"status IN {RAW_EVENT_STATUSES}", name="status_known"),
        CheckConstraint(
            "process_attempts >= 0 AND process_attempts <= 100",
            name="attempts_in_range",
        ),
        Index("ix_device_raw_events_device_id_received_at", "device_id", "received_at"),
        Index("ix_device_raw_events_status", "status"),
        # The reconciliation queue reads only the rows needing a human.
        Index(
            "ix_device_raw_events_needs_attention",
            "device_id",
            "received_at",
            postgresql_where=text("status IN ('pending', 'unmapped', 'failed')"),
        ),
        {"comment": "Append-only record of device output. Never rewrite; reprocess instead."},
    )

    id: Mapped[PrimaryKey]
    device_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    sync_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("device_sync_runs.id", ondelete="SET NULL"), nullable=True
    )

    external_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    """
    The device's own identifier for this event. When a device provides none, the
    adapter derives a stable one from the content (see
    ``policy.derive_event_id``) so deduplication still works.
    """

    external_employee_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(Jsonb, nullable=False)
    """The event as received. The forensic record."""

    raw_timestamp: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """The timestamp as a string, before parsing - so a parsing mistake is
    diagnosable from the stored row."""

    raw_direction: Mapped[str | None] = mapped_column(String(40), nullable=True)

    parsed_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parsed_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timezone_used: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    attendance_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("attendance_events.id", ondelete="SET NULL"), nullable=True
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    #: Set when an operator resolves an unmapped or failed event by hand.
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
