"""device integration

Revision ID: 0004
Revises: 0003
Created: 2026-10-15 11:46:11.381983

Adds the four device tables and connects attendance events to the device that
recorded them.

Two guarantees are enforced here rather than in application code:

* ``device_raw_events`` is **append-only in its raw columns**. A trigger permits
  the processing columns to change - that is how an event moves from
  ``unmapped`` to ``mapped`` - but refuses any rewrite of what the device
  actually sent. Without that, a reconciliation screen could quietly edit the
  evidence it is meant to explain.
* **At most one open mapping** per device identifier, via a partial unique
  index. Enrolment slots get reused, so two open mappings for the same
  identifier would make a punch's owner ambiguous.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("adapter_type", sa.String(length=30), nullable=False),
        sa.Column("vendor", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("serial_number", sa.String(length=80), nullable=True),
        sa.Column("firmware_version", sa.String(length=40), nullable=True),
        sa.Column("work_location_id", sa.BigInteger(), nullable=True),
        sa.Column("timezone_override", sa.String(length=64), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # Encrypted at the application layer with a key held separately from the
        # JWT secret, so a database dump alone does not yield device credentials.
        sa.Column("api_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("allowed_source_ips", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "direction_mode",
            sa.String(length=20),
            server_default=sa.text("'alternate'"),
            nullable=False,
        ),
        sa.Column(
            "clock_drift_tolerance_seconds",
            sa.Integer(),
            server_default=sa.text("300"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sync_cursor", sa.String(length=200), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(length=20), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "adapter_type IN ('csv_upload', 'webhook', 'rest_poll', 'simulator')",
            name=op.f("ck_devices_adapter_type_known"),
        ),
        sa.CheckConstraint(
            "direction_mode IN ('explicit', 'alternate', 'always_in', 'always_out')",
            name=op.f("ck_devices_direction_mode_known"),
        ),
        sa.CheckConstraint(
            "clock_drift_tolerance_seconds >= 0 AND clock_drift_tolerance_seconds <= 86400",
            name=op.f("ck_devices_drift_tolerance_in_range"),
        ),
        sa.CheckConstraint("code = upper(code)", name=op.f("ck_devices_code_upper")),
        sa.ForeignKeyConstraint(
            ["work_location_id"],
            ["work_locations.id"],
            name=op.f("fk_devices_work_location_id_work_locations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("code", name=op.f("uq_devices_code")),
    )
    op.create_index(op.f("ix_devices_is_active"), "devices", ["is_active"], unique=False)

    op.create_table(
        "device_employee_mappings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("external_employee_ref", sa.String(length=80), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_device_employee_mappings_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_device_employee_mappings_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_device_employee_mappings_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_device_employee_mappings_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_employee_mappings")),
    )
    op.create_index(
        "ix_device_employee_mappings_lookup",
        "device_employee_mappings",
        ["device_id", "external_employee_ref", "effective_from"],
        unique=False,
    )
    # One open mapping per identifier. Reusing an enrolment slot must be recorded
    # by closing the previous mapping, not by adding a second open one - two open
    # mappings would make a punch's owner ambiguous.
    op.create_index(
        "ix_device_employee_mappings_open_unique",
        "device_employee_mappings",
        ["device_id", "external_employee_ref"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    op.create_table(
        "device_sync_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("events_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_accepted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_duplicate", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_unmapped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_ignored", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "attendance_days_recomputed",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("cursor_before", sa.String(length=200), nullable=True),
        sa.Column("cursor_after", sa.String(length=200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("triggered_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name=op.f("ck_device_sync_runs_status_known"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'webhook', 'upload')",
            name=op.f("ck_device_sync_runs_trigger_known"),
        ),
        sa.CheckConstraint(
            "events_fetched >= 0 AND events_accepted >= 0 AND events_duplicate >= 0 "
            "AND events_unmapped >= 0 AND events_failed >= 0 AND events_ignored >= 0",
            name=op.f("ck_device_sync_runs_counts_non_negative"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name=op.f("ck_device_sync_runs_finished_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_device_sync_runs_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_user_id"],
            ["users.id"],
            name=op.f("fk_device_sync_runs_triggered_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_sync_runs")),
        # The idempotency guard. A retried scheduled job computes the same key
        # and is recognised as a repeat instead of re-fetching and re-applying.
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_device_sync_runs_idempotency_key")),
    )
    op.create_index(
        "ix_device_sync_runs_device_id_started_at",
        "device_sync_runs",
        ["device_id", "started_at"],
        unique=False,
    )

    op.create_table(
        "device_raw_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("sync_run_id", sa.BigInteger(), nullable=True),
        sa.Column("external_event_id", sa.String(length=160), nullable=False),
        sa.Column("external_employee_ref", sa.String(length=80), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_timestamp", sa.String(length=80), nullable=True),
        sa.Column("raw_direction", sa.String(length=40), nullable=True),
        sa.Column("parsed_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parsed_direction", sa.String(length=10), nullable=True),
        sa.Column("timezone_used", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("attendance_event_id", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("process_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'mapped', 'duplicate', 'unmapped', 'failed', 'ignored')",
            name=op.f("ck_device_raw_events_status_known"),
        ),
        sa.CheckConstraint(
            "process_attempts >= 0 AND process_attempts <= 100",
            name=op.f("ck_device_raw_events_attempts_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["attendance_event_id"],
            ["attendance_events.id"],
            name=op.f("fk_device_raw_events_attendance_event_id_attendance_events"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_device_raw_events_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_device_raw_events_employee_id_employees"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name=op.f("fk_device_raw_events_resolved_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["device_sync_runs.id"],
            name=op.f("fk_device_raw_events_sync_run_id_device_sync_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_raw_events")),
        # The deduplication guarantee. A device re-sending its buffer after an
        # outage is the normal case, and this is what makes it harmless.
        sa.UniqueConstraint(
            "device_id",
            "external_event_id",
            name=op.f("uq_device_raw_events_device_id_external_event_id"),
        ),
        comment="Append-only record of device output. Never rewrite; reprocess instead.",
    )
    op.create_index(
        "ix_device_raw_events_device_id_received_at",
        "device_raw_events",
        ["device_id", "received_at"],
        unique=False,
    )
    # Backs the reconciliation queue: a small slice of what becomes a large table.
    op.create_index(
        "ix_device_raw_events_needs_attention",
        "device_raw_events",
        ["device_id", "received_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'unmapped', 'failed')"),
    )
    op.create_index("ix_device_raw_events_status", "device_raw_events", ["status"], unique=False)

    # What the device sent is evidence and must stay as delivered. The processing
    # columns are expected to change - that is how an event moves from
    # 'unmapped' to 'mapped' once a mapping is added - but the raw columns are
    # refused by the database, so no amount of application-layer mistake can
    # rewrite the record a figure was derived from.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iams_guard_device_raw_event_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF  NEW.device_id             IS DISTINCT FROM OLD.device_id
             OR NEW.external_event_id     IS DISTINCT FROM OLD.external_event_id
             OR NEW.external_employee_ref IS DISTINCT FROM OLD.external_employee_ref
             OR NEW.raw_payload           IS DISTINCT FROM OLD.raw_payload
             OR NEW.raw_timestamp         IS DISTINCT FROM OLD.raw_timestamp
             OR NEW.raw_direction         IS DISTINCT FROM OLD.raw_direction
             OR NEW.received_at           IS DISTINCT FROM OLD.received_at
            THEN
                RAISE EXCEPTION
                    'device_raw_events keeps what the device sent unchanged; '
                    'reprocess the event instead of editing it (id=%)', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_device_raw_events_raw_immutable
            BEFORE UPDATE ON device_raw_events
            FOR EACH ROW EXECUTE FUNCTION iams_guard_device_raw_event_update()
        """
    )

    # Attendance events now name the device that recorded them. SET NULL on
    # delete: retiring a device must never delete the attendance it recorded.
    op.create_index(
        op.f("ix_attendance_events_device_id"),
        "attendance_events",
        ["device_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_attendance_events_device_id_devices"),
        "attendance_events",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    devices, device_employee_mappings, device_sync_runs,
                    device_raw_events
                TO iams_app;
                -- The runtime role may reprocess a stored event but never erase
                -- what a device sent.
                REVOKE DELETE ON device_raw_events FROM iams_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_readonly') THEN
                GRANT SELECT ON
                    devices, device_employee_mappings, device_sync_runs,
                    device_raw_events
                TO iams_readonly;
                -- Reporting has no business reading device credentials.
                REVOKE SELECT (api_secret_encrypted, webhook_secret_encrypted)
                    ON devices FROM iams_readonly;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_attendance_events_device_id_devices"),
        "attendance_events",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_attendance_events_device_id"), table_name="attendance_events")

    op.execute("DROP TRIGGER IF EXISTS trg_device_raw_events_raw_immutable ON device_raw_events")
    op.execute("DROP FUNCTION IF EXISTS iams_guard_device_raw_event_update()")

    # IF EXISTS throughout, so a downgrade that follows a partially-applied
    # upgrade still completes instead of stopping on the first missing object.
    for index_name in (
        "ix_device_raw_events_status",
        "ix_device_raw_events_needs_attention",
        "ix_device_raw_events_device_id_received_at",
        "ix_device_sync_runs_device_id_started_at",
        "ix_device_employee_mappings_open_unique",
        "ix_device_employee_mappings_lookup",
        "ix_devices_is_active",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    op.drop_table("device_raw_events")
    op.drop_table("device_sync_runs")
    op.drop_table("device_employee_mappings")
    op.drop_table("devices")
