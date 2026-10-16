"""Administrative command line interface.

Run from the ``backend`` directory with the virtualenv active::

    python -m app.cli seed-permissions
    python -m app.cli seed-settings
    python -m app.cli bootstrap-admin --username admin --email hr@example.com
    python -m app.cli config-status
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.permissions import ROLE_HR_ADMIN
from app.core.security import generate_temporary_password, validate_password_strength
from app.core.timeutil import utc_now
from app.db.session import session_scope
from app.modules.audit import service as audit
from app.modules.auth import service as auth_service
from app.modules.auth.models import Role, User, UserRole
from app.modules.settings import service as settings_service

app = typer.Typer(
    name="iams",
    help="Ishvara Attendance Management System - administrative commands.",
    no_args_is_help=True,
    add_completion=False,
)


def _cli_context() -> audit.AuditContext:
    return audit.AuditContext(actor_label="cli", ip_address=None, user_agent="iams-cli")


def _require_migrated() -> None:
    """Refuse to run against a database that has not been migrated.

    Seeding into a half-created schema produces confusing errors much later, so
    the check is made explicit and the remedy is printed.
    """
    with session_scope() as session:
        try:
            revision = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
        except Exception:
            revision = None
    if not revision:
        typer.secho(
            "The database schema has not been migrated.\nRun 'alembic upgrade head' first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


@app.command("check-db")
def check_db() -> None:
    """Verify database connectivity and report the current migration revision."""
    settings = get_settings()
    # Print the host and database but never the password.
    safe_target = str(settings.database_url).split("@")[-1]
    typer.echo(f"Target: {safe_target}")
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
            revision = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
    except Exception as exc:
        typer.secho(f"Connection failed: {type(exc).__name__}", fg=typer.colors.RED, err=True)
        typer.secho(
            "Is the database running? Try: docker compose up -d postgres",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.secho("Database reachable.", fg=typer.colors.GREEN)
    typer.echo(f"Migration revision: {revision or 'none applied'}")
    if not revision:
        raise typer.Exit(code=1)


@app.command("seed-permissions")
def seed_permissions() -> None:
    """Reconcile the permission and role catalogue into the database. Idempotent."""
    _require_migrated()
    with session_scope() as session:
        result = auth_service.seed_permissions(session)
    typer.secho("Permissions and roles seeded.", fg=typer.colors.GREEN)
    for key, value in result.items():
        typer.echo(f"  {key.replace('_', ' ')}: {value}")


@app.command("seed-settings")
def seed_settings() -> None:
    """Reconcile the business settings catalogue. Never overwrites a stored value."""
    _require_migrated()
    with session_scope() as session:
        result = settings_service.seed_settings(session)
    typer.secho("Settings seeded.", fg=typer.colors.GREEN)
    for key, value in result.items():
        typer.echo(f"  {key}: {value}")


@app.command("bootstrap-admin")
def bootstrap_admin(
    username: Annotated[str, typer.Option(help="Username for the first HR/Admin account.")],
    email: Annotated[str | None, typer.Option(help="Work email address.")] = None,
    password: Annotated[
        str | None,
        typer.Option(
            help=(
                "Set an initial password. Omit to have one generated. Prefer "
                "omitting it: a password passed as an argument is recorded in "
                "your shell history."
            ),
            prompt=False,
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Create an additional HR/Admin account even if one already exists.",
        ),
    ] = False,
) -> None:
    """Create the first HR/Admin account.

    This is the only way an account comes into existence without an existing
    administrator, which is why it is a local command rather than an endpoint.

    It refuses to run if an HR/Admin account already exists, so it cannot be used
    to quietly add a second administrator to a live system; ``--force`` overrides
    that deliberately and is recorded in the audit log.

    The password is printed once to this terminal and must be changed at first
    sign-in. It is never written to a file or to the audit trail.
    """
    _require_migrated()

    with session_scope() as session:
        # Seeding first means a fresh database needs exactly one command.
        auth_service.seed_permissions(session)
        settings_service.seed_settings(session)

        existing_admins = (
            session.scalar(
                select(func.count())
                .select_from(User)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == ROLE_HR_ADMIN, User.is_active.is_(True))
            )
            or 0
        )

        if existing_admins and not force:
            typer.secho(
                f"An active HR/Admin account already exists ({existing_admins} found).\n"
                "Create further accounts through the application, or pass --force if "
                "you are certain.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(code=1)

        initial_password = password or generate_temporary_password(16)
        try:
            validate_password_strength(initial_password, username=username)
        except Exception as exc:
            typer.secho(f"Password rejected: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        result = auth_service.create_user(
            session,
            username=username,
            email=email,
            role_codes=[ROLE_HR_ADMIN],
            password=initial_password,
            must_change_password=True,
            context=_cli_context(),
        )

        audit.record(
            session,
            action="auth.admin_bootstrapped",
            context=_cli_context(),
            entity_type="user",
            entity_id=result.user.id,
            extra={"forced": force, "existing_admins": existing_admins},
        )
        created_username = result.user.username

    typer.secho("\nHR/Admin account created.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Username: {created_username}")
    typer.echo(f"  Password: {initial_password}")
    typer.secho(
        "\nThis password is shown once and is not stored in plaintext anywhere.\n"
        "You must change it at first sign-in.",
        fg=typer.colors.YELLOW,
    )
    if password:
        typer.secho(
            "Note: the password was passed as a command-line argument and is now "
            "in your shell history. Consider clearing it.",
            fg=typer.colors.YELLOW,
        )


@app.command("config-status")
def config_status() -> None:
    """Report business settings that are still placeholders or need legal verification.

    Exits non-zero when anything is outstanding, so it can gate a deployment
    check.
    """
    _require_migrated()
    with session_scope() as session:
        health = settings_service.configuration_health(session)

    typer.echo(f"Settings total:              {health['total']}")
    typer.secho(f"Confirmed:                   {health['confirmed']}", fg=typer.colors.GREEN)
    typer.secho(f"Unconfirmed (placeholders):  {health['unconfirmed']}", fg=typer.colors.YELLOW)
    typer.secho(
        f"Awaiting legal verification: {health['awaiting_legal_verification']}",
        fg=typer.colors.RED if health["awaiting_legal_verification"] else typer.colors.GREEN,
    )

    if health["legal_review_keys"]:
        typer.secho("\nRequire verification against an authoritative source:", bold=True)
        for key in health["legal_review_keys"]:
            typer.echo(f"  - {key}")

    if health["unconfirmed_keys"]:
        typer.secho("\nStill using a placeholder default:", bold=True)
        for key in health["unconfirmed_keys"]:
            typer.echo(f"  - {key}")

    if health["unconfirmed"]:
        typer.secho(
            "\nThese are working defaults, not company decisions. Payroll "
            "finalization is blocked on the statutory ones.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)


@app.command("benchmark-hash")
def benchmark_hash(
    rounds: Annotated[int, typer.Option(help="Number of hashes to time.")] = 5,
) -> None:
    """Time Argon2id with the configured parameters.

    Aim for roughly 0.1-0.5 seconds per hash on the production host: faster
    weakens offline cracking resistance, much slower turns sign-in into a
    denial-of-service vector.
    """
    import time

    from app.core.security import hash_password

    settings = get_settings()
    typer.echo(
        f"time_cost={settings.argon2_time_cost} "
        f"memory_cost={settings.argon2_memory_cost_kib} KiB "
        f"parallelism={settings.argon2_parallelism}"
    )

    sample = generate_temporary_password(20)
    start = time.perf_counter()
    for _ in range(rounds):
        hash_password(sample)
    average = (time.perf_counter() - start) / rounds

    typer.echo(f"Average: {average * 1000:.0f} ms per hash over {rounds} rounds")
    if average < 0.05:
        typer.secho(
            "Faster than recommended; consider raising memory_cost.", fg=typer.colors.YELLOW
        )
    elif average > 1.0:
        typer.secho(
            "Slower than recommended; consider lowering memory_cost.", fg=typer.colors.YELLOW
        )
    else:
        typer.secho("Within the recommended range.", fg=typer.colors.GREEN)


@app.command("generate-encryption-key")
def generate_encryption_key() -> None:
    """Print a new field-encryption key for IAMS_FIELD_ENCRYPTION_KEY.

    Required before bank account numbers or PAN can be stored. Keep it safe and
    back it up separately from the database: **losing it means losing the
    ability to read those fields.** It is deliberately not derived from
    IAMS_SECRET_KEY, so rotating the token-signing key stays a routine action.
    """
    from app.core.crypto import generate_key

    typer.echo(generate_key())
    typer.secho(
        "\nAdd this to backend/.env as IAMS_FIELD_ENCRYPTION_KEY.\n"
        "Back it up: without it, stored bank and PAN values cannot be decrypted.",
        fg=typer.colors.YELLOW,
    )


@app.command("rotate-encryption-key")
def rotate_encryption_key(
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Required. Re-encrypts stored values in place.")
    ] = False,
) -> None:
    """Re-encrypt stored values with the current primary key.

    Procedure: generate a new key, move the old one into
    ``IAMS_FIELD_ENCRYPTION_KEYS_RETIRED``, set the new one as
    ``IAMS_FIELD_ENCRYPTION_KEY``, run this, then remove the retired entry.
    """
    _require_migrated()
    if not confirm:
        typer.secho(
            "This rewrites every encrypted column. Re-run with --confirm.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)

    from app.core.crypto import decrypt, encrypt, is_configured
    from app.modules.employees.models import EmployeeBankAccount, EmployeeStatutoryDetails

    if not is_configured():
        typer.secho("IAMS_FIELD_ENCRYPTION_KEY is not set.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    rotated = 0
    with session_scope() as session:
        for account in session.scalars(select(EmployeeBankAccount)).all():
            plaintext = decrypt(account.account_number_encrypted)
            if plaintext:
                account.account_number_encrypted = encrypt(plaintext)
                rotated += 1
        for record in session.scalars(select(EmployeeStatutoryDetails)).all():
            if record.pan_encrypted:
                plaintext = decrypt(record.pan_encrypted)
                if plaintext:
                    record.pan_encrypted = encrypt(plaintext)
                    rotated += 1

    typer.secho(f"Re-encrypted {rotated} value(s).", fg=typer.colors.GREEN)
    typer.echo("You may now remove the old key from IAMS_FIELD_ENCRYPTION_KEYS_RETIRED.")


@app.command("purge-expired-sessions")
def purge_expired_sessions(
    older_than_days: Annotated[
        int, typer.Option(help="Delete revoked/expired tokens older than this many days.")
    ] = 30,
) -> None:
    """Delete refresh tokens that are long expired.

    Retained for a while after expiry so that token-reuse detection still has
    something to detect.
    """
    _require_migrated()
    from datetime import timedelta

    from app.modules.auth.models import RefreshToken

    cutoff = utc_now() - timedelta(days=older_than_days)
    with session_scope() as session:
        rows = list(
            session.scalars(select(RefreshToken).where(RefreshToken.expires_at < cutoff)).all()
        )
        for row in rows:
            session.delete(row)
    typer.secho(f"Deleted {len(rows)} expired refresh token(s).", fg=typer.colors.GREEN)


@app.command("sync-devices")
def sync_devices(
    force: Annotated[
        bool,
        typer.Option(
            help=(
                "Poll even when devices.auto_sync_enabled is off. Still idempotent: "
                "events are deduplicated on arrival."
            )
        ),
    ] = False,
) -> None:
    """Poll every pollable device once. Intended for a cron entry.

    Safe to run repeatedly. Two runs inside one interval window compute the same
    sync key, so the second fetches nothing; and even a forced re-fetch cannot
    double-count a punch, because events are deduplicated on their identifier.

    Web punching does not depend on this command, or on any device being
    reachable.
    """
    _require_migrated()
    from app.modules.devices import service as devices_service
    from app.modules.devices.adapters.base import load_builtin_adapters

    load_builtin_adapters()

    with session_scope() as session:
        result = devices_service.scheduled_sync(session, context=_cli_context())

    if result["status"] == "skipped":
        typer.secho(result["reason"], fg=typer.colors.YELLOW)
        return

    typer.secho(
        f"Window {result['window']}: polled {len(result['devices'])} device(s), "
        f"{result['accepted_total']} new punch(es).",
        fg=typer.colors.GREEN if result["status"] == "succeeded" else typer.colors.YELLOW,
    )
    for device in result["devices"]:
        if device.get("status") == "failed":
            reason = (device.get("problems") or [{}])[0].get("reason", "unknown")
            typer.secho(f"  {device['code']}: failed - {reason}", fg=typer.colors.RED)
        else:
            typer.echo(
                f"  {device['code']}: accepted {device.get('accepted', 0)}, "
                f"duplicate {device.get('duplicate', 0)}, "
                f"unmapped {device.get('unmapped', 0)}"
            )


@app.command("accrue-leave")
def accrue_leave(
    as_of: Annotated[
        str | None,
        typer.Option(
            help=(
                "YYYY-MM-DD. The accrual period containing this date is credited. "
                "Pass a date inside a past period to catch up a missed run."
            )
        ),
    ] = None,
) -> None:
    """Credit one accrual period's leave entitlement. Safe to run repeatedly.

    Each ledger entry is keyed ``accrual:<employee>:<type>:<period>``, so a
    second run reports what was already posted rather than crediting a balance
    twice - the most damaging failure in leave management, and the hardest to
    notice after the fact.

    Intended for a monthly cron entry shortly after each period ends.
    """
    _require_migrated()
    from datetime import date as date_type

    from app.modules.leave import service as leave_service

    parsed = None
    if as_of:
        try:
            parsed = date_type.fromisoformat(as_of)
        except ValueError:
            typer.secho(
                f"Could not read {as_of!r} as a date. Use YYYY-MM-DD.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None

    with session_scope() as session:
        result = leave_service.run_accrual(session, as_of=parsed, context=_cli_context())

    if result["period_key"] == "unknown":
        typer.secho(
            "No leave policy is in force for any employee, so nothing accrued. "
            "Create leave types and policies first.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho(
        f"Period {result['period_key']} (leave year {result['leave_year']}): "
        f"{result['entries_posted']} credit(s) posted, {result['days_credited']} day(s).",
        fg=typer.colors.GREEN,
    )
    if result["already_posted"]:
        typer.echo(f"  {result['already_posted']} were already credited and were not posted again.")
    for label, key in (
        ("no policy in force", "skipped_no_policy"),
        ("not employed in the period", "skipped_not_employed"),
        ("at the accumulation ceiling", "skipped_at_ceiling"),
    ):
        if result[key]:
            typer.echo(f"  {result[key]} skipped: {label}.")


@app.command("grant-compoff")
def grant_compoff(
    days_back: Annotated[
        int,
        typer.Option(help="How many days of attendance to examine, ending today."),
    ] = 14,
) -> None:
    """Create comp-off credits from qualifying work. Safe to run repeatedly.

    A day earns credit once, enforced by a unique constraint, so a re-run
    reports what was already credited instead of doubling an entitlement.
    Intended for a weekly cron entry; the look-back window covers days whose
    attendance was recomputed after the fact.
    """
    _require_migrated()
    from datetime import date as date_type
    from datetime import timedelta

    from app.modules.compoff import service as compoff_service

    end = date_type.today()
    start = end - timedelta(days=max(days_back, 0))

    with session_scope() as session:
        result = compoff_service.grant_credits(
            session, start=start, end=end, context=_cli_context()
        )

    typer.secho(
        f"{start} to {end}: examined {result['days_examined']} qualifying day(s), "
        f"created {result['credits_created']} credit(s) worth "
        f"{result['days_credited']} day(s).",
        fg=typer.colors.GREEN,
    )
    if result["already_credited"]:
        typer.echo(
            f"  {result['already_credited']} day(s) were already credited and were "
            "not credited again."
        )
    if result["not_qualifying"]:
        typer.echo(f"  {result['not_qualifying']} day(s) did not meet the minute thresholds.")


@app.command("expire-compoff")
def expire_compoff() -> None:
    """Close comp-off credits past their expiry date.

    Idempotent: it only touches credits still available, so a re-run finds
    nothing left to expire. Only the unspent remainder of a credit is lost.
    """
    _require_migrated()
    from app.modules.compoff import service as compoff_service

    with session_scope() as session:
        result = compoff_service.expire_credits(session, context=_cli_context())

    typer.secho(
        f"Expired {result['credits_expired']} credit(s), "
        f"{result['days_lost']} day(s) lost across "
        f"{result['employees_affected']} employee(s).",
        fg=typer.colors.GREEN if result["credits_expired"] == 0 else typer.colors.YELLOW,
    )


@app.command("close-leave-year")
def close_leave_year(
    leave_year: Annotated[
        int,
        typer.Option(
            help=(
                "The year to close, labelled by the calendar year it started in. "
                "A leave year starting in April 2026 is year 2026."
            )
        ),
    ],
    apply_changes: Annotated[
        bool,
        typer.Option(
            "--apply/--dry-run",
            help=(
                "Defaults to a dry run. Closing a year moves everybody's "
                "entitlement at once, so nothing is written unless you ask."
            ),
        ),
    ] = False,
) -> None:
    """Carry forward, encash and lapse what is left at a leave year's end.

    Safe to re-run: every entry is keyed by employee, leave type and closing
    year, so a second run posts nothing and reports what was already closed.
    """
    _require_migrated()
    from app.modules.leave import service as leave_service

    with session_scope() as session:
        result = leave_service.run_year_end(
            session,
            leave_year=leave_year,
            dry_run=not apply_changes,
            context=_cli_context(),
        )

    heading = "Would close" if result["dry_run"] else "Closed"
    typer.secho(
        f"{heading} leave year {leave_year} under the '{result['company_rule']}' rule: "
        f"{result['carried_days']} day(s) carried, "
        f"{result['encashed_days']} encashed, {result['lapsed_days']} lapsed.",
        fg=typer.colors.YELLOW if result["dry_run"] else typer.colors.GREEN,
    )
    typer.echo(
        f"  {result['employees_closed']} employee/type pair(s) closed; "
        f"{result['already_closed']} already closed; "
        f"{result['skipped_no_policy']} with no policy in force; "
        f"{result['skipped_nothing_to_close']} with nothing to close."
    )
    if result["dry_run"]:
        typer.secho(
            "  This was a dry run. Re-run with --apply to post the entries.",
            fg=typer.colors.YELLOW,
        )


@app.command("expire-carried-leave")
def expire_carried_leave() -> None:
    """Lapse carried-forward leave that reached its expiry date.

    Separate from the year-end job because they happen at different times: a
    balance carried into April with a six-month life expires in September.
    """
    _require_migrated()
    from app.modules.leave import service as leave_service

    with session_scope() as session:
        result = leave_service.expire_carried_forward(session, context=_cli_context())

    typer.secho(
        f"Examined {result['lots_examined']} carried lot(s): "
        f"{result['entries_posted']} expired, {result['days_expired']} day(s) lost.",
        fg=typer.colors.GREEN,
    )
    if result["already_expired"]:
        typer.echo(f"  {result['already_expired']} had already been expired.")
    if result["nothing_left_to_expire"]:
        typer.echo(
            f"  {result['nothing_left_to_expire']} had already been used, so nothing was lost."
        )


@app.command("notify-unsettled-attendance")
def notify_unsettled_attendance(
    days_back: int = typer.Option(7, "--days-back", min=1, max=60, help="How far back to look."),
) -> None:
    """Ask employees to correct days whose punches do not settle.

    Intended for a nightly cron entry. Today is never included, because an open
    shift is not a missing punch, and nothing is classified as absence - the
    notification asks for a correction. Safe to re-run: each notification is
    keyed by the date and the status, so a repeat run writes nothing.
    """
    _require_migrated()
    from datetime import date, timedelta

    from app.modules.attendance import service as attendance_service

    end = date.today()
    start = end - timedelta(days=days_back)

    with session_scope() as session:
        result = attendance_service.notify_unsettled_days(
            session, start=start, end=end, context=_cli_context()
        )

    typer.secho(
        f"Examined {result['days_examined']} unsettled day(s) from "
        f"{result['from']} to {result['to']}: "
        f"{result['notifications_created']} notification(s) created.",
        fg=typer.colors.GREEN,
    )
    if result["already_notified"]:
        typer.echo(
            f"  {result['already_notified']} had already been notified, so nothing "
            "was written again."
        )


@app.command("run-announcement-schedule")
def run_announcement_schedule() -> None:
    """Send notifications for announcements whose publication time has passed.

    Intended for a cron entry, but not required for correctness: an
    announcement's visibility is a query on its publication time, so a missed
    run delays the notification and never hides the announcement. Idempotent
    through a per-announcement, per-user key.
    """
    _require_migrated()
    from app.modules.notifications import service as notifications_service

    with session_scope() as session:
        result = notifications_service.publish_due_announcements(session, context=_cli_context())

    typer.secho(
        f"Published {result['published']} announcement(s), created "
        f"{result['notifications_created']} notification(s), expired "
        f"{result['expired']}.",
        fg=typer.colors.GREEN,
    )


@app.command("purge-expired-data")
def purge_expired_data(
    apply: bool = typer.Option(
        False, "--apply", help="Actually delete. Without this it is a dry run."
    ),
    category: str = typer.Option(
        "all",
        "--category",
        help="all, audit-logs, device-events or notifications.",
    ),
) -> None:
    """Delete data past its configured retention period.

    **Dry run by default**, because a purge destroys evidence as well as freeing
    space: an audit entry nobody can produce later is indistinguishable from a
    change nobody made. Nothing here runs on a schedule - the brief's own
    retention periods are a company decision, and the settings start
    unconfirmed.

    Purging the audit log needs the **owner** database role. ``DELETE`` on
    ``audit_logs`` is revoked from the application role on purpose, so this
    command refuses rather than appearing to succeed if it is run with the
    application's credentials.
    """
    _require_migrated()
    from datetime import timedelta

    from sqlalchemy import delete, func, select

    from app.core.timeutil import utc_now
    from app.modules.audit.models import AuditLog
    from app.modules.devices.models import DeviceRawEvent
    from app.modules.notifications.models import Notification
    from app.modules.settings import service as settings_service

    choices = {"all", "audit-logs", "device-events", "notifications"}
    if category not in choices:
        typer.secho(
            f"Unknown category {category!r}. One of: {', '.join(sorted(choices))}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    targets = [
        ("audit-logs", "retention.audit_log_days", AuditLog, AuditLog.created_at),
        (
            "device-events",
            "retention.device_event_days",
            DeviceRawEvent,
            DeviceRawEvent.received_at,
        ),
        (
            "notifications",
            "retention.notification_days",
            Notification,
            Notification.created_at,
        ),
    ]

    with session_scope() as session:
        now = utc_now()
        unconfirmed: list[str] = []
        for name, key, model, column in targets:
            if category not in ("all", name):
                continue

            row = settings_service.get_row(session, key)
            days = int(settings_service.coerce_value(row.value_type, row.unwrapped_value, key=key))
            if not row.is_confirmed:
                unconfirmed.append(key)
            cutoff = now - timedelta(days=days)

            count = (
                session.scalar(select(func.count()).select_from(model).where(column < cutoff)) or 0
            )
            typer.echo(
                f"{name}: {count} row(s) older than {days} day(s) "
                f"(before {cutoff.date().isoformat()})"
                + ("" if row.is_confirmed else "  [retention period unconfirmed]")
            )

            if not apply or count == 0:
                continue

            try:
                session.execute(delete(model).where(column < cutoff))
                session.flush()
            except Exception as error:  # pragma: no cover - depends on the role
                session.rollback()
                typer.secho(
                    f"  could not purge {name}: {error}. "
                    "  Purging the audit log needs the owner database role.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
            typer.secho(f"  deleted {count} row(s)", fg=typer.colors.GREEN)

    if not apply:
        typer.secho(
            "Dry run: nothing was deleted. Re-run with --apply to delete.",
            fg=typer.colors.YELLOW,
        )
    if unconfirmed:
        typer.secho(
            "Unconfirmed retention period(s): " + ", ".join(unconfirmed) + ". "
            "A retention period destroys evidence as well as data; confirm it "
            "deliberately before relying on this.",
            fg=typer.colors.YELLOW,
        )


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover
        typer.secho("\nInterrupted.", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
