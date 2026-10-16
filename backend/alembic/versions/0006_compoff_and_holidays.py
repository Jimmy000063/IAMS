"""compoff and holidays

Revision ID: 0006
Revises: 0005
Created: 2026-10-15 15:17:49.152465


Comp-off is modelled as **lots and allocations**, not a signed ledger. Each
credit expires on its own date, so consuming "one day" has to record which
credit it drew on - otherwise expiry cannot be computed correctly. The
guarantees enforced here:

* ``unique(employee_id, earned_date)`` on ``compoff_credits`` - a day worked
  earns credit **once**, however often its attendance is recomputed or the
  credit job is re-run.
* ``allocated_days <= credit_days`` by check constraint, so no bug can
  over-draw a lot.
* ``compoff_allocations.credit_id`` is ``ON DELETE RESTRICT`` - a consumed
  credit cannot be deleted out from under its use.

Holiday calendars are scoped and unioned rather than global, because an employee
in one state does not get another state's holiday list.

It also renames four constraints that migration 0005 hand-named, by dropping and
re-creating them. An explicit constraint name bypasses the project's naming
convention and lands in the schema-wide index namespace, so the obvious name
(``one_row_per_date``) blocked any other table from using it - which is exactly
what happened when ``compoff_application_days`` wanted the same name. The
convention name, ``uq_<table>_<columns>``, cannot collide.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holiday_calendars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("work_location_id", sa.BigInteger(), nullable=True),
        sa.Column("employee_group_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("code = upper(code)", name=op.f("ck_holiday_calendars_code_upper")),
        sa.CheckConstraint(
            "year >= 2000 AND year <= 2100", name=op.f("ck_holiday_calendars_year_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_holiday_calendars_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_group_id"],
            ["employee_groups.id"],
            name=op.f("fk_holiday_calendars_employee_group_id_employee_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_holiday_calendars_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["work_location_id"],
            ["work_locations.id"],
            name=op.f("fk_holiday_calendars_work_location_id_work_locations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_calendars")),
        sa.UniqueConstraint("code", "year", name=op.f("uq_holiday_calendars_code_year")),
    )
    op.create_index(
        "ix_holiday_calendars_year_active", "holiday_calendars", ["year", "is_active"], unique=False
    )
    op.create_table(
        "compoff_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("total_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending_manager'"),
            nullable=False,
        ),
        sa.Column("current_stage", sa.String(length=30), nullable=True),
        sa.Column(
            "escalated_from_missing_manager",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("applied_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
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
            "current_stage IS NULL OR current_stage IN ('reporting_manager', 'hr_admin')",
            name=op.f("ck_compoff_applications_stage_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_manager', 'pending_hr', 'approved', 'rejected', 'withdrawn', 'cancelled')",
            name=op.f("ck_compoff_applications_status_known"),
        ),
        sa.CheckConstraint(
            "end_date >= start_date", name=op.f("ck_compoff_applications_dates_ordered")
        ),
        sa.CheckConstraint(
            "total_days > 0", name=op.f("ck_compoff_applications_total_days_positive")
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"],
            ["users.id"],
            name=op.f("fk_compoff_applications_applied_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_compoff_applications_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compoff_applications")),
    )
    op.create_index(
        "ix_compoff_applications_employee",
        "compoff_applications",
        ["employee_id", "start_date"],
        unique=False,
    )
    op.create_index(
        "ix_compoff_applications_open",
        "compoff_applications",
        ["current_stage", "start_date"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending_manager','pending_hr')"),
    )
    op.create_table(
        "holiday_import_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rows_received", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_created", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_updated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("problems", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("imported_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["holiday_calendars.id"],
            name=op.f("fk_holiday_import_runs_calendar_id_holiday_calendars"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["users.id"],
            name=op.f("fk_holiday_import_runs_imported_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_import_runs")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_holiday_import_runs_idempotency_key")),
    )
    op.create_index(
        "ix_holiday_import_runs_calendar",
        "holiday_import_runs",
        ["calendar_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "holidays",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("calendar_id", sa.BigInteger(), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "holiday_type", sa.String(length=20), server_default=sa.text("'public'"), nullable=False
        ),
        sa.Column("is_paid", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            "holiday_type IN ('public', 'company', 'optional', 'restricted')",
            name=op.f("ck_holidays_holiday_type_known"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["holiday_calendars.id"],
            name=op.f("fk_holidays_calendar_id_holiday_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holidays")),
        sa.UniqueConstraint(
            "calendar_id", "holiday_date", name=op.f("uq_holidays_calendar_id_holiday_date")
        ),
    )
    op.create_index(
        "ix_holidays_calendar_type", "holidays", ["calendar_id", "holiday_type"], unique=False
    )
    op.create_index("ix_holidays_date", "holidays", ["holiday_date"], unique=False)
    op.create_table(
        "compoff_application_days",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("off_date", sa.Date(), nullable=False),
        sa.Column(
            "portion", sa.String(length=20), server_default=sa.text("'full'"), nullable=False
        ),
        sa.Column(
            "day_fraction",
            sa.Numeric(precision=4, scale=2),
            server_default=sa.text("1.00"),
            nullable=False,
        ),
        sa.Column("day_type", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "portion IN ('full', 'first_half', 'second_half')",
            name=op.f("ck_compoff_application_days_portion_known"),
        ),
        sa.CheckConstraint(
            "day_fraction > 0 AND day_fraction <= 1",
            name=op.f("ck_compoff_application_days_fraction_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["compoff_applications.id"],
            name=op.f("fk_compoff_application_days_application_id_compoff_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compoff_application_days")),
        sa.UniqueConstraint(
            "application_id",
            "off_date",
            name=op.f("uq_compoff_application_days_application_id_off_date"),
        ),
    )
    op.create_index(
        "ix_compoff_application_days_date", "compoff_application_days", ["off_date"], unique=False
    )
    op.create_table(
        "compoff_approvals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=30), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decided_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_by_employee_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "was_auto_skipped", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'skipped')",
            name=op.f("ck_compoff_approvals_decision_known"),
        ),
        sa.CheckConstraint(
            "stage IN ('reporting_manager', 'hr_admin')",
            name=op.f("ck_compoff_approvals_stage_known"),
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_compoff_approvals_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["compoff_applications.id"],
            name=op.f("fk_compoff_approvals_application_id_compoff_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_employee_id"],
            ["employees.id"],
            name=op.f("fk_compoff_approvals_decided_by_employee_id_employees"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_compoff_approvals_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compoff_approvals")),
        sa.UniqueConstraint(
            "application_id", "sequence", name=op.f("uq_compoff_approvals_application_id_sequence")
        ),
    )
    op.create_table(
        "compoff_credits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("earned_date", sa.Date(), nullable=False),
        sa.Column("attendance_day_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "source", sa.String(length=20), server_default=sa.text("'attendance'"), nullable=False
        ),
        sa.Column("day_type", sa.String(length=20), nullable=False),
        sa.Column("worked_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("credit_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "allocated_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'pending_verification'"),
            nullable=False,
        ),
        sa.Column("verified_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("reversal_of_id", sa.BigInteger(), nullable=True),
        sa.Column("policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            "source IN ('attendance', 'manual')", name=op.f("ck_compoff_credits_source_known")
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification', 'available', 'rejected', 'expired', 'reversed')",
            name=op.f("ck_compoff_credits_status_known"),
        ),
        sa.CheckConstraint(
            "allocated_days >= 0 AND allocated_days <= credit_days",
            name=op.f("ck_compoff_credits_allocated_within_credit"),
        ),
        sa.CheckConstraint(
            "credit_days > 0 AND credit_days <= 2", name=op.f("ck_compoff_credits_credit_days_sane")
        ),
        sa.CheckConstraint(
            "expiry_date IS NULL OR expiry_date >= earned_date",
            name=op.f("ck_compoff_credits_expiry_after_earned"),
        ),
        sa.CheckConstraint(
            "worked_minutes >= 0", name=op.f("ck_compoff_credits_worked_minutes_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["attendance_day_id"],
            ["attendance_days.id"],
            name=op.f("fk_compoff_credits_attendance_day_id_attendance_days"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_compoff_credits_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id"],
            ["compoff_credits.id"],
            name=op.f("fk_compoff_credits_reversal_of_id_compoff_credits"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            name=op.f("fk_compoff_credits_verified_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compoff_credits")),
        sa.UniqueConstraint(
            "employee_id", "earned_date", name=op.f("uq_compoff_credits_employee_id_earned_date")
        ),
    )
    op.create_index(
        "ix_compoff_credits_employee_status",
        "compoff_credits",
        ["employee_id", "status"],
        unique=False,
    )
    op.create_index("ix_compoff_credits_expiry", "compoff_credits", ["expiry_date"], unique=False)
    op.create_index(
        "ix_compoff_credits_usable",
        "compoff_credits",
        ["employee_id", "expiry_date"],
        unique=False,
        postgresql_where=sa.text("status = 'available' AND allocated_days < credit_days"),
    )
    op.create_table(
        "optional_holiday_elections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("holiday_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'elected'"), nullable=False
        ),
        sa.Column(
            "elected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elected_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "status IN ('elected', 'withdrawn')",
            name=op.f("ck_optional_holiday_elections_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["elected_by_user_id"],
            ["users.id"],
            name=op.f("fk_optional_holiday_elections_elected_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_optional_holiday_elections_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["holiday_id"],
            ["holidays.id"],
            name=op.f("fk_optional_holiday_elections_holiday_id_holidays"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_optional_holiday_elections")),
        sa.UniqueConstraint(
            "employee_id",
            "holiday_id",
            name=op.f("uq_optional_holiday_elections_employee_id_holiday_id"),
        ),
    )
    op.create_index(
        "ix_optional_holiday_elections_employee",
        "optional_holiday_elections",
        ["employee_id", "status"],
        unique=False,
    )
    op.create_table(
        "compoff_allocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("credit_id", sa.BigInteger(), nullable=False),
        sa.Column("days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "allocated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("days > 0", name=op.f("ck_compoff_allocations_days_positive")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["compoff_applications.id"],
            name=op.f("fk_compoff_allocations_application_id_compoff_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credit_id"],
            ["compoff_credits.id"],
            name=op.f("fk_compoff_allocations_credit_id_compoff_credits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compoff_allocations")),
        sa.UniqueConstraint(
            "application_id",
            "credit_id",
            name=op.f("uq_compoff_allocations_application_id_credit_id"),
        ),
    )
    op.create_index(
        "ix_compoff_allocations_credit", "compoff_allocations", ["credit_id"], unique=False
    )
    op.drop_constraint(op.f("one_row_per_date"), "leave_application_days", type_="unique")
    op.create_unique_constraint(
        op.f("uq_leave_application_days_application_id_leave_date"),
        "leave_application_days",
        ["application_id", "leave_date"],
    )
    op.drop_constraint(op.f("one_per_sequence"), "leave_approvals", type_="unique")
    op.create_unique_constraint(
        op.f("uq_leave_approvals_application_id_sequence"),
        "leave_approvals",
        ["application_id", "sequence"],
    )
    op.drop_constraint(op.f("idempotency_key_unique"), "leave_ledger_entries", type_="unique")
    op.create_unique_constraint(
        op.f("uq_leave_ledger_entries_idempotency_key"), "leave_ledger_entries", ["idempotency_key"]
    )
    op.drop_constraint(op.f("one_per_type_per_date"), "leave_policies", type_="unique")
    op.create_unique_constraint(
        op.f("uq_leave_policies_leave_type_id_effective_from"),
        "leave_policies",
        ["leave_type_id", "effective_from"],
    )

    # A credit's factual columns are evidence: which day was worked, for how
    # long, and what it was worth. The processing columns are expected to change
    # - that is how a credit moves from pending_verification to available, and
    # how allocated_days tracks consumption - but the facts are refused by the
    # database, so no application-layer mistake can rewrite the basis of a
    # credit someone was granted.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iams_guard_compoff_credit_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF  NEW.employee_id    IS DISTINCT FROM OLD.employee_id
             OR NEW.earned_date    IS DISTINCT FROM OLD.earned_date
             OR NEW.source         IS DISTINCT FROM OLD.source
             OR NEW.day_type       IS DISTINCT FROM OLD.day_type
             OR NEW.worked_minutes IS DISTINCT FROM OLD.worked_minutes
             OR NEW.credit_days    IS DISTINCT FROM OLD.credit_days
            THEN
                RAISE EXCEPTION
                    'compoff_credits keeps what was earned unchanged; reverse the '
                    'credit instead of editing it (id=%)', OLD.id
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_compoff_credits_facts_immutable
            BEFORE UPDATE ON compoff_credits
            FOR EACH ROW EXECUTE FUNCTION iams_guard_compoff_credit_update()
        """
    )

    # An approval decision is history, as it is for leave.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iams_guard_compoff_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'compoff_approvals is append-only; a decision cannot be rewritten '
                '(id=%)', OLD.id
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_compoff_approvals_append_only
            BEFORE UPDATE ON compoff_approvals
            FOR EACH ROW EXECUTE FUNCTION iams_guard_compoff_approval()
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    holiday_calendars, holidays, optional_holiday_elections,
                    holiday_import_runs, compoff_credits, compoff_applications,
                    compoff_application_days, compoff_allocations, compoff_approvals
                TO iams_app;
                -- The runtime role records decisions and allocations; it never
                -- erases one. A wrong credit is reversed, not deleted.
                REVOKE DELETE ON compoff_credits, compoff_allocations FROM iams_app;
                REVOKE UPDATE ON compoff_approvals FROM iams_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_readonly') THEN
                GRANT SELECT ON
                    holiday_calendars, holidays, optional_holiday_elections,
                    holiday_import_runs, compoff_credits, compoff_applications,
                    compoff_application_days, compoff_allocations, compoff_approvals
                TO iams_readonly;
            END IF;
        END
        $$
        """
    )

    # Comp-off taken is its own attendance status: it is paid time off, but
    # distinct from leave because it consumed no leave balance and payroll and
    # reports need to tell the two apart.
    op.drop_constraint("status_known", "attendance_days", type_="check")
    op.create_check_constraint(
        "status_known",
        "attendance_days",
        "status IN ('present','half_day','absent','on_leave','compoff','holiday',"
        "'weekly_off','incomplete','missing_punch')",
    )


def downgrade() -> None:
    op.drop_constraint("status_known", "attendance_days", type_="check")
    op.create_check_constraint(
        "status_known",
        "attendance_days",
        "status IN ('present','half_day','absent','on_leave','holiday',"
        "'weekly_off','incomplete','missing_punch')",
    )

    op.execute("DROP TRIGGER IF EXISTS trg_compoff_approvals_append_only ON compoff_approvals")
    op.execute("DROP FUNCTION IF EXISTS iams_guard_compoff_approval()")
    op.execute("DROP TRIGGER IF EXISTS trg_compoff_credits_facts_immutable ON compoff_credits")
    op.execute("DROP FUNCTION IF EXISTS iams_guard_compoff_credit_update()")

    op.drop_constraint(
        op.f("uq_leave_policies_leave_type_id_effective_from"), "leave_policies", type_="unique"
    )
    op.create_unique_constraint(
        op.f("one_per_type_per_date"),
        "leave_policies",
        ["leave_type_id", "effective_from"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_constraint(
        op.f("uq_leave_ledger_entries_idempotency_key"), "leave_ledger_entries", type_="unique"
    )
    op.create_unique_constraint(
        op.f("idempotency_key_unique"),
        "leave_ledger_entries",
        ["idempotency_key"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_constraint(
        op.f("uq_leave_approvals_application_id_sequence"), "leave_approvals", type_="unique"
    )
    op.create_unique_constraint(
        op.f("one_per_sequence"),
        "leave_approvals",
        ["application_id", "sequence"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_constraint(
        op.f("uq_leave_application_days_application_id_leave_date"),
        "leave_application_days",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("one_row_per_date"),
        "leave_application_days",
        ["application_id", "leave_date"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_index("ix_compoff_allocations_credit", table_name="compoff_allocations")
    op.drop_table("compoff_allocations")
    op.drop_index("ix_optional_holiday_elections_employee", table_name="optional_holiday_elections")
    op.drop_table("optional_holiday_elections")
    op.drop_index(
        "ix_compoff_credits_usable",
        table_name="compoff_credits",
        postgresql_where=sa.text("status = 'available' AND allocated_days < credit_days"),
    )
    op.drop_index("ix_compoff_credits_expiry", table_name="compoff_credits")
    op.drop_index("ix_compoff_credits_employee_status", table_name="compoff_credits")
    op.drop_table("compoff_credits")
    op.drop_table("compoff_approvals")
    op.drop_index("ix_compoff_application_days_date", table_name="compoff_application_days")
    op.drop_table("compoff_application_days")
    op.drop_index("ix_holidays_date", table_name="holidays")
    op.drop_index("ix_holidays_calendar_type", table_name="holidays")
    op.drop_table("holidays")
    op.drop_index("ix_holiday_import_runs_calendar", table_name="holiday_import_runs")
    op.drop_table("holiday_import_runs")
    op.drop_index(
        "ix_compoff_applications_open",
        table_name="compoff_applications",
        postgresql_where=sa.text("status IN ('pending_manager','pending_hr')"),
    )
    op.drop_index("ix_compoff_applications_employee", table_name="compoff_applications")
    op.drop_table("compoff_applications")
    op.drop_index("ix_holiday_calendars_year_active", table_name="holiday_calendars")
    op.drop_table("holiday_calendars")
