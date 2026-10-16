"""leave management

Revision ID: 0005
Revises: 0004
Created: 2026-10-15 13:40:21.101473


The guarantees enforced here rather than in application code:

* ``leave_ledger_entries`` is **append-only**, by trigger. A balance is the sum
  of this table, so a rewritten row would silently change an employee's
  entitlement with nothing to show what happened. A mistake is corrected by
  posting a reversal, which keeps both the error and the fix on the record.
  ``UPDATE`` and ``DELETE`` are revoked from the runtime role as well.
* ``idempotency_key`` is unique. This is what makes a retried accrual job
  harmless - the duplicate insert fails at the database rather than crediting a
  balance twice, which is the most damaging failure in leave management and the
  hardest to notice.
* **One open policy assignment** per employee and leave type, via a partial
  unique index. Two would make "which entitlement applies" ambiguous and the
  resolver would have to choose arbitrarily.
* One policy per leave type per start date, so "which rules were in force on
  this date" has exactly one answer.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leave_types",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_paid", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("deducts_balance", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("allows_half_day", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("allows_negative_balance", sa.Boolean(), nullable=True),
        sa.Column(
            "counts_non_working_days", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("requires_reason", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "attachment_required_above_days", sa.Numeric(precision=6, scale=2), nullable=True
        ),
        sa.Column("min_notice_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_consecutive_days", sa.Integer(), nullable=True),
        sa.Column("min_service_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "gender_restriction",
            sa.String(length=10),
            server_default=sa.text("'any'"),
            nullable=False,
        ),
        sa.Column(
            "requires_legal_review", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("colour", sa.String(length=20), nullable=True),
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
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "gender_restriction IN ('any', 'female', 'male')",
            name=op.f("ck_leave_types_gender_restriction_known"),
        ),
        sa.CheckConstraint(
            "attachment_required_above_days IS NULL OR attachment_required_above_days >= 0",
            name=op.f("ck_leave_types_attachment_threshold_non_negative"),
        ),
        sa.CheckConstraint("code = upper(code)", name=op.f("ck_leave_types_code_upper")),
        sa.CheckConstraint(
            "max_consecutive_days IS NULL OR max_consecutive_days > 0",
            name=op.f("ck_leave_types_max_consecutive_positive"),
        ),
        sa.CheckConstraint(
            "min_notice_days >= 0 AND min_notice_days <= 365",
            name=op.f("ck_leave_types_notice_in_range"),
        ),
        sa.CheckConstraint(
            "min_service_days >= 0 AND min_service_days <= 3650",
            name=op.f("ck_leave_types_min_service_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_types_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_types_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_types")),
        sa.UniqueConstraint("code", name=op.f("uq_leave_types_code")),
    )
    op.create_index("ix_leave_types_active", "leave_types", ["is_active"], unique=False)
    op.create_table(
        "leave_policies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("leave_type_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "annual_quota_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "accrual_frequency",
            sa.String(length=20),
            server_default=sa.text("'annual'"),
            nullable=False,
        ),
        sa.Column(
            "accrual_rounding",
            sa.String(length=20),
            server_default=sa.text("'nearest_half'"),
            nullable=False,
        ),
        sa.Column(
            "prorate_on_joining", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("prorate_on_exit", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "accrue_from_confirmation",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("max_accumulation_days", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("carry_forward_cap_days", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("carry_forward_expires_after_months", sa.Integer(), nullable=True),
        sa.Column(
            "encashment_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("encashment_max_days", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "requires_legal_review", sa.Boolean(), server_default=sa.text("false"), nullable=False
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
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "accrual_frequency IN ('none', 'monthly', 'quarterly', 'half_yearly', 'annual')",
            name=op.f("ck_leave_policies_frequency_known"),
        ),
        sa.CheckConstraint(
            "accrual_rounding IN ('none', 'nearest_half', 'nearest_full', 'down_half', 'up_half')",
            name=op.f("ck_leave_policies_rounding_known"),
        ),
        sa.CheckConstraint(
            "annual_quota_days >= 0", name=op.f("ck_leave_policies_quota_non_negative")
        ),
        sa.CheckConstraint(
            "carry_forward_cap_days IS NULL OR carry_forward_cap_days >= 0",
            name=op.f("ck_leave_policies_carry_forward_non_negative"),
        ),
        sa.CheckConstraint(
            "carry_forward_expires_after_months IS NULL OR carry_forward_expires_after_months > 0",
            name=op.f("ck_leave_policies_expiry_positive"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_leave_policies_period_ordered"),
        ),
        sa.CheckConstraint(
            "encashment_max_days IS NULL OR encashment_max_days >= 0",
            name=op.f("ck_leave_policies_encashment_non_negative"),
        ),
        sa.CheckConstraint(
            "max_accumulation_days IS NULL OR max_accumulation_days >= 0",
            name=op.f("ck_leave_policies_accumulation_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_policies_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_leave_policies_leave_type_id_leave_types"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_policies_updated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_policies")),
        sa.UniqueConstraint("leave_type_id", "effective_from", name="one_per_type_per_date"),
    )
    op.create_index(
        "ix_leave_policies_type_from",
        "leave_policies",
        ["leave_type_id", "effective_from"],
        unique=False,
    )
    op.create_table(
        "employee_leave_policies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_type_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_policy_id", sa.BigInteger(), nullable=False),
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
            name=op.f("ck_employee_leave_policies_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_employee_leave_policies_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_leave_policies_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["leave_policy_id"],
            ["leave_policies.id"],
            name=op.f("fk_employee_leave_policies_leave_policy_id_leave_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_employee_leave_policies_leave_type_id_leave_types"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_leave_policies")),
    )
    op.create_index(
        "ix_employee_leave_policies_lookup",
        "employee_leave_policies",
        ["employee_id", "leave_type_id", "effective_from"],
        unique=False,
    )
    op.create_index(
        "ix_employee_leave_policies_open_unique",
        "employee_leave_policies",
        ["employee_id", "leave_type_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.create_table(
        "leave_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_type_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_policy_id", sa.BigInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("total_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "paid_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "unpaid_days",
            sa.Numeric(precision=6, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("contact_during_leave", sa.String(length=120), nullable=True),
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
        sa.Column("is_backdated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("applied_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "applied_on_behalf", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_application_id", sa.BigInteger(), nullable=True),
        sa.Column("policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attachment_file_id", sa.BigInteger(), nullable=True),
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
            name=op.f("ck_leave_applications_stage_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_manager', 'pending_hr', 'approved', 'rejected', 'withdrawn', 'cancelled')",
            name=op.f("ck_leave_applications_status_known"),
        ),
        sa.CheckConstraint(
            "end_date >= start_date", name=op.f("ck_leave_applications_dates_ordered")
        ),
        sa.CheckConstraint(
            "paid_days >= 0 AND unpaid_days >= 0",
            name=op.f("ck_leave_applications_split_non_negative"),
        ),
        sa.CheckConstraint(
            "total_days > 0", name=op.f("ck_leave_applications_total_days_positive")
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_applications_applied_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_file_id"],
            ["stored_files.id"],
            name=op.f("fk_leave_applications_attachment_file_id_stored_files"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_applications_cancelled_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_leave_applications_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["leave_policy_id"],
            ["leave_policies.id"],
            name=op.f("fk_leave_applications_leave_policy_id_leave_policies"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_leave_applications_leave_type_id_leave_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_application_id"],
            ["leave_applications.id"],
            name=op.f("fk_leave_applications_supersedes_application_id_leave_applications"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_applications")),
    )
    op.create_index(
        "ix_leave_applications_employee_dates",
        "leave_applications",
        ["employee_id", "start_date", "end_date"],
        unique=False,
    )
    op.create_index(
        "ix_leave_applications_open",
        "leave_applications",
        ["current_stage", "start_date"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending_manager','pending_hr')"),
    )
    op.create_index("ix_leave_applications_status", "leave_applications", ["status"], unique=False)
    op.create_table(
        "leave_application_days",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_date", sa.Date(), nullable=False),
        sa.Column(
            "portion", sa.String(length=20), server_default=sa.text("'full'"), nullable=False
        ),
        sa.Column(
            "day_fraction",
            sa.Numeric(precision=4, scale=2),
            server_default=sa.text("1.00"),
            nullable=False,
        ),
        sa.Column("is_paid", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("day_type", sa.String(length=20), nullable=False),
        sa.Column(
            "counts_against_balance", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint(
            "portion IN ('full', 'first_half', 'second_half')",
            name=op.f("ck_leave_application_days_portion_known"),
        ),
        sa.CheckConstraint(
            "day_fraction > 0 AND day_fraction <= 1",
            name=op.f("ck_leave_application_days_fraction_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["leave_applications.id"],
            name=op.f("fk_leave_application_days_application_id_leave_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_application_days")),
        sa.UniqueConstraint("application_id", "leave_date", name="one_row_per_date"),
    )
    op.create_index(
        "ix_leave_application_days_date", "leave_application_days", ["leave_date"], unique=False
    )
    op.create_table(
        "leave_approvals",
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
            name=op.f("ck_leave_approvals_decision_known"),
        ),
        sa.CheckConstraint(
            "stage IN ('reporting_manager', 'hr_admin')",
            name=op.f("ck_leave_approvals_stage_known"),
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_leave_approvals_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["leave_applications.id"],
            name=op.f("fk_leave_approvals_application_id_leave_applications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_employee_id"],
            ["employees.id"],
            name=op.f("fk_leave_approvals_decided_by_employee_id_employees"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_approvals_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_approvals")),
        sa.UniqueConstraint("application_id", "sequence", name="one_per_sequence"),
    )
    op.create_index(
        "ix_leave_approvals_application", "leave_approvals", ["application_id"], unique=False
    )
    op.create_table(
        "leave_ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("leave_type_id", sa.BigInteger(), nullable=False),
        sa.Column("txn_type", sa.String(length=30), nullable=False),
        sa.Column("days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("leave_year", sa.Integer(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("application_id", sa.BigInteger(), nullable=True),
        sa.Column("reverses_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("leave_policy_id", sa.BigInteger(), nullable=True),
        sa.Column("job_run_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "txn_type IN ('opening', 'accrual', 'deduction', 'reversal', 'adjustment', 'carry_forward_in', 'carry_forward_out', 'expiry', 'encashment', 'compoff_credit')",
            name=op.f("ck_leave_ledger_entries_txn_type_known"),
        ),
        sa.CheckConstraint("days <> 0", name=op.f("ck_leave_ledger_entries_days_non_zero")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["leave_applications.id"],
            name=op.f("fk_leave_ledger_entries_application_id_leave_applications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_leave_ledger_entries_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_leave_ledger_entries_employee_id_employees"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["job_runs.id"],
            name=op.f("fk_leave_ledger_entries_job_run_id_job_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["leave_policy_id"],
            ["leave_policies.id"],
            name=op.f("fk_leave_ledger_entries_leave_policy_id_leave_policies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["leave_type_id"],
            ["leave_types.id"],
            name=op.f("fk_leave_ledger_entries_leave_type_id_leave_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_entry_id"],
            ["leave_ledger_entries.id"],
            name=op.f("fk_leave_ledger_entries_reverses_entry_id_leave_ledger_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_ledger_entries")),
        sa.UniqueConstraint("idempotency_key", name="idempotency_key_unique"),
        comment="Append-only. Balance is SUM(days); never store a balance column.",
    )
    op.create_index(
        "ix_leave_ledger_application", "leave_ledger_entries", ["application_id"], unique=False
    )
    op.create_index(
        "ix_leave_ledger_balance",
        "leave_ledger_entries",
        ["employee_id", "leave_type_id", "effective_date"],
        unique=False,
    )

    # The ledger IS the balance. A rewritten or deleted row would change an
    # employee's entitlement with nothing left to show it happened, so the
    # database refuses both. A mistake is corrected by posting a reversal, which
    # keeps the error and the fix side by side.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iams_guard_leave_ledger()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'leave_ledger_entries is append-only; post a reversal instead of '
                'changing entry %', COALESCE(OLD.id, NEW.id)
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_ledger_append_only
            BEFORE UPDATE OR DELETE ON leave_ledger_entries
            FOR EACH ROW EXECUTE FUNCTION iams_guard_leave_ledger()
        """
    )

    # An approval decision is history. Appending the next stage is how the chain
    # advances; editing a recorded decision is not.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION iams_guard_leave_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'leave_approvals is append-only; a decision cannot be rewritten '
                '(id=%)', OLD.id
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_leave_approvals_append_only
            BEFORE UPDATE ON leave_approvals
            FOR EACH ROW EXECUTE FUNCTION iams_guard_leave_approval()
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    leave_types, leave_policies, employee_leave_policies,
                    leave_applications, leave_application_days, leave_approvals,
                    leave_ledger_entries
                TO iams_app;
                -- The runtime role appends ledger entries and approval
                -- decisions; it never rewrites or removes one.
                REVOKE UPDATE, DELETE ON leave_ledger_entries FROM iams_app;
                REVOKE UPDATE ON leave_approvals FROM iams_app;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_readonly') THEN
                GRANT SELECT ON
                    leave_types, leave_policies, employee_leave_policies,
                    leave_applications, leave_application_days, leave_approvals,
                    leave_ledger_entries
                TO iams_readonly;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_leave_approvals_append_only ON leave_approvals")
    op.execute("DROP FUNCTION IF EXISTS iams_guard_leave_approval()")
    op.execute("DROP TRIGGER IF EXISTS trg_leave_ledger_append_only ON leave_ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS iams_guard_leave_ledger()")

    op.drop_index("ix_leave_ledger_balance", table_name="leave_ledger_entries")
    op.drop_index("ix_leave_ledger_application", table_name="leave_ledger_entries")
    op.drop_table("leave_ledger_entries")
    op.drop_index("ix_leave_approvals_application", table_name="leave_approvals")
    op.drop_table("leave_approvals")
    op.drop_index("ix_leave_application_days_date", table_name="leave_application_days")
    op.drop_table("leave_application_days")
    op.drop_index("ix_leave_applications_status", table_name="leave_applications")
    op.drop_index(
        "ix_leave_applications_open",
        table_name="leave_applications",
        postgresql_where=sa.text("status IN ('pending_manager','pending_hr')"),
    )
    op.drop_index("ix_leave_applications_employee_dates", table_name="leave_applications")
    op.drop_table("leave_applications")
    op.drop_index(
        "ix_employee_leave_policies_open_unique",
        table_name="employee_leave_policies",
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.drop_index("ix_employee_leave_policies_lookup", table_name="employee_leave_policies")
    op.drop_table("employee_leave_policies")
    op.drop_index("ix_leave_policies_type_from", table_name="leave_policies")
    op.drop_table("leave_policies")
    op.drop_index("ix_leave_types_active", table_name="leave_types")
    op.drop_table("leave_types")
