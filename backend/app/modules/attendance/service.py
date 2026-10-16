"""Attendance: punching, recomputation, manual correction and regularization."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    PolicyViolation,
    ValidationFailed,
)
from app.core.timeutil import local_datetime_to_utc, resolve_timezone, utc_now
from app.modules.attendance.models import (
    AttendanceDay,
    AttendanceEvent,
    AttendanceRegularization,
    AttendanceSession,
)
from app.modules.attendance.policy import (
    AttendancePolicy,
    ComputedDay,
    DayContext,
    PunchEvent,
    ShiftSpec,
    attribute_work_date,
    compute_day,
    decide_punch,
)
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.compoff import service as compoff_service
from app.modules.employees.models import Employee

# Attendance reads approved leave; leave recomputes attendance after a decision.
# The reverse direction is a local import inside the leave service, so the
# module-level dependency only runs one way and there is no import cycle.
from app.modules.leave import service as leave_service
from app.modules.notifications import hooks as notification_hooks
from app.modules.settings import service as settings_service
from app.modules.shifts import service as shifts_service
from app.modules.shifts.policy import date_range, resolve_day_type

logger = logging.getLogger(__name__)

#: Guard on bulk recomputation. A wider range is done in batches by the CLI,
#: not in a request that would hold a transaction open for minutes.
MAX_RECOMPUTE_DAYS = 400

#: Statuses that mean the day could not be settled from the punches recorded.
#: Neither is an absence: one has an unclosed punch pair, the other has none of
#: the punches the shift expected.
UNSETTLED_STATUSES: frozenset[str] = frozenset({"missing_punch", "incomplete"})


# ---------------------------------------------------------------------------
# Policy assembly
# ---------------------------------------------------------------------------


def load_policy(session: Session) -> AttendancePolicy:
    """Read the company attendance policy from settings."""
    return AttendancePolicy(
        auto_deduct_break_minutes=settings_service.get_value(
            session, "attendance.auto_deduct_break_minutes", default=0
        ),
        rounding_mode=settings_service.get_value(
            session, "attendance.rounding_mode", default="none"
        ),
        rounding_increment_minutes=settings_service.get_value(
            session, "attendance.rounding_increment_minutes", default=15
        ),
        overtime_enabled=settings_service.get_value(
            session, "attendance.overtime_enabled", default=False
        ),
        overtime_min_minutes=settings_service.get_value(
            session, "attendance.overtime_min_minutes", default=30
        ),
        missing_punch_status=settings_service.get_value(
            session, "attendance.missing_punch_status", default="incomplete"
        ),
    )


def build_shift_spec(session: Session, shift: Any | None) -> ShiftSpec:
    """Merge a shift's own values with the company defaults it inherits.

    Done here rather than in the engine so the engine has no fallbacks to reason
    about: by the time it runs, every threshold has a concrete value.

    With no shift assigned, a notional shift is built entirely from company
    settings. That keeps an unassigned employee's attendance computable - and
    the resulting day carries ``shift_id = None``, which the UI surfaces as "no
    shift assigned" rather than pretending one existed.
    """
    grace_late = settings_service.get_value(session, "attendance.grace_late_minutes", default=15)
    grace_early = settings_service.get_value(
        session, "attendance.grace_early_exit_minutes", default=15
    )
    full_day = settings_service.get_value(session, "attendance.min_minutes_full_day", default=480)
    half_day = settings_service.get_value(session, "attendance.min_minutes_half_day", default=240)

    if shift is None:
        return ShiftSpec(
            shift_id=None,
            start_time=time(9, 0),
            end_time=time(18, 0),
            crosses_midnight=False,
            break_minutes=0,
            grace_late_minutes=grace_late,
            grace_early_exit_minutes=grace_early,
            min_minutes_full_day=full_day,
            min_minutes_half_day=half_day,
        )

    return ShiftSpec(
        shift_id=shift.id,
        start_time=shift.start_time,
        end_time=shift.end_time,
        crosses_midnight=shift.crosses_midnight,
        break_minutes=shift.break_minutes,
        grace_late_minutes=(
            shift.grace_late_minutes if shift.grace_late_minutes is not None else grace_late
        ),
        grace_early_exit_minutes=(
            shift.grace_early_exit_minutes
            if shift.grace_early_exit_minutes is not None
            else grace_early
        ),
        min_minutes_full_day=(
            shift.min_minutes_full_day if shift.min_minutes_full_day is not None else full_day
        ),
        min_minutes_half_day=(
            shift.min_minutes_half_day if shift.min_minutes_half_day is not None else half_day
        ),
        early_punch_window_minutes=shift.early_punch_window_minutes,
        late_punch_window_minutes=shift.late_punch_window_minutes,
    )


# ---------------------------------------------------------------------------
# Punching
# ---------------------------------------------------------------------------


def _employee_for_user(session: Session, user: User) -> Employee:
    if user.employee_id is None:
        raise Conflict(
            "Your account is not linked to an employee record, so attendance "
            "cannot be recorded. Contact HR."
        )
    employee = session.get(Employee, user.employee_id)
    if employee is None:  # pragma: no cover - FK makes this unreachable
        raise NotFound("Employee not found.")
    return employee


@dataclass(frozen=True, slots=True)
class PunchResult:
    """Outcome of a self-service punch."""

    day: AttendanceDay
    event_type: str
    recorded: bool
    """False for a duplicate within a few seconds; the state is unchanged."""


def punch(
    session: Session,
    *,
    user: User,
    requested_type: str,
    context: audit.AuditContext,
) -> PunchResult:
    """Record a self-service punch and recompute the affected day.

    The timestamp is :func:`utc_now` - never anything supplied by the client.
    A browser clock can be set to any value, and trusting it would let an
    employee record a punch for a time they were not present.

    A double-click is reported as success with the unchanged state rather than
    as an error the employee has to interpret.
    """
    employee = _employee_for_user(session, user)

    web_enabled = settings_service.get_value(session, "attendance.web_punch_enabled", default=True)

    now = utc_now()
    timezone_name = shifts_service.employee_timezone(session, employee.id)
    tz = resolve_timezone(timezone_name)

    # The shift is resolved for the punch's own local date first, purely to get
    # the attribution window; the work date it yields may differ for an
    # overnight shift, and the shift is then re-resolved for that date.
    probe_date = now.astimezone(tz).date()
    shift, calendar_override = shifts_service.resolve_shift_for(session, employee.id, probe_date)
    spec = build_shift_spec(session, shift)
    work_date = attribute_work_date(now, shift=spec, tz=tz)

    if work_date != probe_date:
        shift, calendar_override = shifts_service.resolve_shift_for(session, employee.id, work_date)
        spec = build_shift_spec(session, shift)

    existing = _punch_events(session, employee.id, work_date)

    decision = decide_punch(
        now=now,
        requested_type=requested_type,  # type: ignore[arg-type]
        existing_today=existing,
        shift=spec,
        tz=tz,
        is_employed=employee.is_employed_on(work_date),
        web_punch_enabled=web_enabled,
    )

    if not decision.allowed:
        if decision.is_duplicate:
            day = recompute_day(session, employee_id=employee.id, work_date=work_date)
            if day is None:  # pragma: no cover - duplicate implies an existing day
                raise PolicyViolation(decision.reason or "Punch rejected.")
            return PunchResult(day=day, event_type=decision.event_type, recorded=False)
        raise PolicyViolation(decision.reason or "Punch rejected.")

    event = AttendanceEvent(
        employee_id=employee.id,
        event_type=decision.event_type,
        occurred_at=now,
        work_date=decision.work_date,
        source="web",
        timezone_used=timezone_name,
        client_ip=context.ip_address,
        user_agent=context.user_agent[:255] if context.user_agent else None,
        recorded_by_user_id=user.id,
    )
    session.add(event)

    try:
        session.flush()
    except IntegrityError as exc:  # pragma: no cover - web punches have no external id
        session.rollback()
        raise Conflict("That punch was already recorded.") from exc

    day = recompute_day(
        session,
        employee_id=employee.id,
        work_date=decision.work_date,
        calendar_override_id=calendar_override,
    )

    audit.record(
        session,
        action=f"attendance.punched_{decision.event_type}",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "event_type": decision.event_type,
            "occurred_at": now.isoformat(),
            "work_date": decision.work_date.isoformat(),
            "timezone": timezone_name,
            "source": "web",
        },
    )
    session.flush()
    if day is None:  # pragma: no cover - decide_punch already verified employment
        raise PolicyViolation("Attendance could not be recorded for that date.")
    return PunchResult(day=day, event_type=decision.event_type, recorded=True)


def _punch_events(session: Session, employee_id: int, work_date: date) -> list[PunchEvent]:
    """Live punch events for a day, in the engine's shape.

    Superseded events are excluded: they stay in the table for audit, but a
    replaced punch must not contribute to the figure.
    """
    rows = session.scalars(
        select(AttendanceEvent)
        .where(
            AttendanceEvent.employee_id == employee_id,
            AttendanceEvent.work_date == work_date,
            AttendanceEvent.is_superseded.is_(False),
        )
        .order_by(AttendanceEvent.occurred_at)
    ).all()
    return [
        PunchEvent(event_id=row.id, event_type=row.event_type, occurred_at=row.occurred_at)
        for row in rows
    ]


def current_state(session: Session, employee_id: int) -> dict[str, Any]:
    """What the punch button should say, and the day so far.

    Driven by the same ``pair_sessions`` the engine uses, so the button cannot
    disagree with the computed day.
    """
    from app.modules.attendance.policy import pair_sessions

    timezone_name = shifts_service.employee_timezone(session, employee_id)
    tz = resolve_timezone(timezone_name)
    now = utc_now()

    probe_date = now.astimezone(tz).date()
    shift, _ = shifts_service.resolve_shift_for(session, employee_id, probe_date)
    spec = build_shift_spec(session, shift)
    work_date = attribute_work_date(now, shift=spec, tz=tz)

    events = _punch_events(session, employee_id, work_date)
    sessions, _anomalies, currently_in = pair_sessions(events)

    day = session.scalar(
        select(AttendanceDay).where(
            AttendanceDay.employee_id == employee_id,
            AttendanceDay.work_date == work_date,
        )
    )

    worked_so_far = sum(item.duration_minutes for item in sessions)
    if currently_in and events:
        open_in = max(
            (event for event in events if event.event_type == "in"),
            key=lambda event: event.occurred_at,
        )
        worked_so_far += int((now - open_in.occurred_at).total_seconds() // 60)

    return {
        "work_date": work_date,
        "timezone": timezone_name,
        "server_time": now,
        "is_punched_in": currently_in,
        "next_punch": "out" if currently_in else "in",
        "session_count": len(sessions),
        "worked_minutes_so_far": worked_so_far,
        "shift_id": spec.shift_id,
        "shift_code": shift.code if shift else None,
        "shift_start": spec.start_time.isoformat(),
        "shift_end": spec.end_time.isoformat(),
        "has_shift_assigned": shift is not None,
        "status": day.status if day else None,
        "events": [
            {
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
    }


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------


def recompute_day(
    session: Session,
    *,
    employee_id: int,
    work_date: date,
    calendar_override_id: int | None = None,
    force_over_manual_override: bool = False,
) -> AttendanceDay | None:
    """Rebuild one employee-day from its events.

    Idempotent: running it twice produces the same row. That matters because it
    is called after every punch, after every correction, and by the nightly job.

    A row marked ``is_manual_override`` is left alone unless
    ``force_over_manual_override`` is set - HR's explicit decision must not be
    quietly undone by a recomputation.

    Returns ``None`` when the employee was not on the rolls that day, having
    removed any stale row.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")

    existing = session.scalar(
        select(AttendanceDay).where(
            AttendanceDay.employee_id == employee_id,
            AttendanceDay.work_date == work_date,
        )
    )

    if existing is not None and existing.is_manual_override and not force_over_manual_override:
        return existing

    shift, assignment_calendar = shifts_service.resolve_shift_for(session, employee_id, work_date)
    calendar = shifts_service.resolve_calendar_for(
        session,
        employee_id,
        calendar_override_id=calendar_override_id or assignment_calendar,
    )
    calendar_spec = shifts_service.calendar_spec(
        session, calendar, start=work_date, end=work_date, employee_id=employee_id
    )
    day_type = resolve_day_type(work_date, calendar_spec)

    shift_spec = build_shift_spec(session, shift)
    policy = load_policy(session)
    timezone_name = shifts_service.employee_timezone(session, employee_id)
    tz = resolve_timezone(timezone_name)

    events = _punch_events(session, employee_id, work_date)

    # Only *approved* leave counts. A pending request must not mark a day as
    # leave, or an employee could avoid being marked absent simply by applying.
    leave_day = leave_service.approved_leave_on(
        session, employee_id=employee_id, work_date=work_date
    )

    # Comp-off taken, which is paid time off distinct from leave.
    compoff_day = compoff_service.approved_compoff_on(
        session, employee_id=employee_id, work_date=work_date
    )

    computed = compute_day(
        context=DayContext(
            work_date=work_date,
            timezone_name=timezone_name,
            day_type=day_type,
            is_employed=employee.is_employed_on(work_date),
            on_approved_leave=leave_day is not None,
            on_approved_compoff=compoff_day is not None,
            compoff_is_half_day=bool(compoff_day and compoff_day.portion != "full"),
            leave_is_half_day=bool(leave_day and leave_day.portion != "full"),
            leave_is_paid=bool(leave_day and leave_day.is_paid),
            leave_application_id=leave_day.application_id if leave_day else None,
        ),
        shift=shift_spec,
        policy=policy,
        events=events,
        tz=tz,
    )

    if computed is None:
        if existing is not None:
            session.delete(existing)
            session.flush()
        return None

    return _persist(session, employee_id=employee_id, computed=computed, existing=existing)


def _persist(
    session: Session,
    *,
    employee_id: int,
    computed: ComputedDay,
    existing: AttendanceDay | None,
) -> AttendanceDay:
    """Write a computed day, replacing its sessions."""
    row = existing or AttendanceDay(employee_id=employee_id, work_date=computed.work_date)

    row.day_type = computed.day_type
    row.status = computed.status
    row.shift_id = computed.shift_id
    row.first_in_at = computed.first_in_at
    row.last_out_at = computed.last_out_at
    row.worked_minutes = computed.worked_minutes
    row.break_minutes = computed.break_minutes
    row.net_minutes = computed.net_minutes
    row.late_minutes = computed.late_minutes
    row.early_exit_minutes = computed.early_exit_minutes
    row.overtime_minutes = computed.overtime_minutes
    row.is_late = computed.is_late
    row.is_early_exit = computed.is_early_exit
    row.has_missing_punch = computed.has_missing_punch
    row.payable_day_fraction = computed.payable_day_fraction
    row.leave_application_id = computed.leave_application_id
    row.computed_at = utc_now()
    row.computed_from_event_count = computed.event_count
    row.policy_snapshot = computed.policy_snapshot
    row.anomalies = list(computed.anomalies) or None

    if existing is None:
        session.add(row)
    session.flush()

    # Sessions are fully replaced rather than reconciled: they are derived data,
    # and rebuilding is simpler and cannot leave a stale pair behind.
    session.execute(delete(AttendanceSession).where(AttendanceSession.attendance_day_id == row.id))
    for item in computed.sessions:
        session.add(
            AttendanceSession(
                attendance_day_id=row.id,
                sequence=item.sequence,
                started_at=item.started_at,
                ended_at=item.ended_at,
                duration_minutes=item.duration_minutes,
                in_event_id=item.in_event_id,
                out_event_id=item.out_event_id,
            )
        )
    session.flush()
    return row


def recompute_range(
    session: Session,
    *,
    employee_ids: list[int] | None,
    start: date,
    end: date,
    context: audit.AuditContext,
    force_over_manual_override: bool = False,
    respect_lock: bool = True,
) -> dict[str, Any]:
    """Recompute a date range for some or all employees.

    ``respect_lock`` honours ``attendance.recompute_locked_after_days``: days
    older than that are skipped. The brief requires that a schedule change does
    not silently rewrite history, and this is where that is enforced - an
    operator who really means to can pass ``respect_lock=False``, which is
    recorded in the audit entry.
    """
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")

    span = (end - start).days + 1
    if span > MAX_RECOMPUTE_DAYS:
        raise ValidationFailed(
            f"That range covers {span} days; the maximum is {MAX_RECOMPUTE_DAYS}. "
            "Use the CLI for a larger backfill."
        )

    lock_days = settings_service.get_value(
        session, "attendance.recompute_locked_after_days", default=45
    )
    today = date.today()
    locked_before = today - timedelta(days=lock_days) if respect_lock and lock_days else None

    if employee_ids:
        targets = list(session.scalars(select(Employee).where(Employee.id.in_(employee_ids))).all())
        missing = set(employee_ids) - {employee.id for employee in targets}
        if missing:
            raise ValidationFailed(f"Unknown employee id(s): {sorted(missing)}.")
    else:
        targets = list(
            session.scalars(select(Employee).where(Employee.date_of_joining <= end)).all()
        )

    computed = skipped_locked = skipped_not_employed = preserved_override = 0

    for employee in targets:
        for day in date_range(start, end):
            if locked_before is not None and day < locked_before:
                skipped_locked += 1
                continue
            if not employee.is_employed_on(day):
                skipped_not_employed += 1
                continue

            before = session.scalar(
                select(AttendanceDay.is_manual_override).where(
                    AttendanceDay.employee_id == employee.id,
                    AttendanceDay.work_date == day,
                )
            )
            if before and not force_over_manual_override:
                preserved_override += 1
                continue

            recompute_day(
                session,
                employee_id=employee.id,
                work_date=day,
                force_over_manual_override=force_over_manual_override,
            )
            computed += 1

    result = {
        "employees": len(targets),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days_computed": computed,
        "skipped_locked": skipped_locked,
        "skipped_not_employed": skipped_not_employed,
        "preserved_manual_overrides": preserved_override,
        "lock_cutoff": locked_before.isoformat() if locked_before else None,
    }

    audit.record(
        session,
        action="attendance.recomputed",
        context=context,
        entity_type="attendance",
        entity_id=None,
        extra={**result, "respected_lock": respect_lock, "forced": force_over_manual_override},
    )
    session.flush()
    return result


def notify_unsettled_days(
    session: Session,
    *,
    start: date,
    end: date,
    employee_ids: list[int] | None = None,
    context: audit.AuditContext,
) -> dict[str, Any]:
    """Ask employees to correct days the punches do not settle.

    Separate from recompute, and deliberately so: recompute runs during the
    working day, when a day with one punch is simply somebody still at work.
    This job looks only at days that are over.

    Nothing here classifies a day as absence. The brief forbids that, and the
    notification asks for a correction rather than announcing a consequence -
    the day keeps whatever status the engine computed.

    Idempotent through the notification key, which is the date and the status:
    running it nightly tells each person once per unsettled day, and a re-run on
    the same data writes nothing.
    """
    if end < start:
        raise ValidationFailed("The end date cannot precede the start date.")

    # Only days that are finished. "Today" is excluded whatever its status,
    # because an open shift is not a missing punch yet.
    cutoff = min(end, date.today() - timedelta(days=1))
    if cutoff < start:
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days_examined": 0,
            "notifications_created": 0,
            "already_notified": 0,
        }

    statement = select(AttendanceDay).where(
        AttendanceDay.work_date >= start,
        AttendanceDay.work_date <= cutoff,
        AttendanceDay.status.in_(UNSETTLED_STATUSES),
    )
    if employee_ids:
        statement = statement.where(AttendanceDay.employee_id.in_(employee_ids))

    examined = created = 0
    for day in session.scalars(statement.order_by(AttendanceDay.work_date)).all():
        examined += 1
        created += notification_hooks.missing_punch(
            session,
            employee_id=day.employee_id,
            work_date=day.work_date,
            status=day.status,
        )

    result = {
        "from": start.isoformat(),
        "to": cutoff.isoformat(),
        "days_examined": examined,
        "notifications_created": created,
        "already_notified": examined - created,
    }
    audit.record(
        session,
        action="attendance.unsettled_days_notified",
        context=context,
        entity_type="job",
        entity_id="attendance_unsettled",
        extra=result,
    )
    session.flush()
    return result


# ---------------------------------------------------------------------------
# Manual correction
# ---------------------------------------------------------------------------


def add_manual_event(
    session: Session,
    *,
    employee_id: int,
    event_type: str,
    local_datetime: datetime,
    reason: str,
    context: audit.AuditContext,
    source: str = "manual",
) -> AttendanceDay | None:
    """Insert a punch on an employee's behalf and recompute.

    ``local_datetime`` is naive wall-clock time in the employee's work-location
    timezone - which is how HR thinks about it ("she arrived at 09:15") - and is
    anchored to that zone here.

    A correction adds an event; it never edits or deletes an existing one. The
    original punches stay on the record, so the correction is visible as a
    correction rather than as a changed history.
    """
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")
    if not reason or not reason.strip():
        raise ValidationFailed(
            "A reason is required for a manual attendance entry.",
            details=[{"field": "reason", "message": "Required."}],
        )

    timezone_name = shifts_service.employee_timezone(session, employee_id)
    tz = resolve_timezone(timezone_name)
    occurred_at = local_datetime_to_utc(local_datetime, timezone_name)

    probe_date = occurred_at.astimezone(tz).date()
    shift, _ = shifts_service.resolve_shift_for(session, employee_id, probe_date)
    spec = build_shift_spec(session, shift)
    work_date = attribute_work_date(occurred_at, shift=spec, tz=tz)

    if not employee.is_employed_on(work_date):
        raise PolicyViolation(
            f"{employee.employee_code} was not on the rolls on {work_date.isoformat()}."
        )

    event = AttendanceEvent(
        employee_id=employee_id,
        event_type=event_type,
        occurred_at=occurred_at,
        work_date=work_date,
        source=source,
        timezone_used=timezone_name,
        reason=reason.strip(),
        recorded_by_user_id=context.actor_user_id,
    )
    session.add(event)
    session.flush()

    audit.record(
        session,
        action="attendance.manual_event_added",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        after={
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "local_time": local_datetime.isoformat(),
            "work_date": work_date.isoformat(),
            "source": source,
            "reason": reason.strip(),
        },
    )

    return recompute_day(
        session,
        employee_id=employee_id,
        work_date=work_date,
        force_over_manual_override=True,
    )


def supersede_event(
    session: Session, *, event_id: int, reason: str, context: audit.AuditContext
) -> AttendanceDay | None:
    """Mark a punch as superseded so it stops counting, without deleting it.

    Used when a device reports a spurious punch. The row stays for audit and
    reconciliation; the recomputation ignores it.
    """
    event = session.get(AttendanceEvent, event_id)
    if event is None:
        raise NotFound("Attendance event not found.")
    if event.is_superseded:
        raise Conflict("That event is already superseded.")
    if not reason or not reason.strip():
        raise ValidationFailed(
            "A reason is required.", details=[{"field": "reason", "message": "Required."}]
        )

    event.is_superseded = True
    event.reason = reason.strip()
    session.flush()

    audit.record(
        session,
        action="attendance.event_superseded",
        context=context,
        entity_type="attendance_event",
        entity_id=event.id,
        before={"is_superseded": False},
        after={"is_superseded": True, "reason": reason.strip()},
        extra={"employee_id": event.employee_id, "work_date": event.work_date.isoformat()},
    )

    return recompute_day(
        session,
        employee_id=event.employee_id,
        work_date=event.work_date,
        force_over_manual_override=True,
    )


def override_day_status(
    session: Session,
    *,
    employee_id: int,
    work_date: date,
    status: str,
    payable_day_fraction: Any,
    reason: str,
    context: audit.AuditContext,
) -> AttendanceDay:
    """Set a day's status directly, pinning it against recomputation.

    The escape hatch for cases the engine cannot know about. It is deliberately
    sticky - subsequent recomputations preserve it - and deliberately loud:
    the row is flagged, the reason is required, and the audit entry records both
    the old and new values.
    """
    if not reason or not reason.strip():
        raise ValidationFailed(
            "A reason is required for a manual override.",
            details=[{"field": "reason", "message": "Required."}],
        )

    employee = session.get(Employee, employee_id)
    if employee is None:
        raise NotFound("Employee not found.")
    if not employee.is_employed_on(work_date):
        raise PolicyViolation(
            f"{employee.employee_code} was not on the rolls on {work_date.isoformat()}."
        )

    row = recompute_day(session, employee_id=employee_id, work_date=work_date)
    if row is None:  # pragma: no cover - employment already checked
        raise PolicyViolation("No attendance day exists for that date.")

    before = {"status": row.status, "payable_day_fraction": str(row.payable_day_fraction)}

    row.status = status
    row.payable_day_fraction = payable_day_fraction
    row.is_manual_override = True
    row.override_reason = reason.strip()
    row.override_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="attendance.day_overridden",
        context=context,
        entity_type="employee",
        entity_id=employee_id,
        before=before,
        after={"status": status, "payable_day_fraction": str(payable_day_fraction)},
        extra={"work_date": work_date.isoformat(), "reason": reason.strip()},
    )
    return row


# ---------------------------------------------------------------------------
# Regularization
# ---------------------------------------------------------------------------


def request_regularization(
    session: Session,
    *,
    user: User,
    work_date: date,
    local_in: datetime | None,
    local_out: datetime | None,
    reason: str,
    context: audit.AuditContext,
) -> AttendanceRegularization:
    """Raise a correction request for one's own attendance.

    Routed to the reporting manager, then HR. When the employee has no active
    manager, the manager stage is skipped according to
    ``leave.manager_missing_behaviour`` - otherwise the request would sit
    forever with nobody able to act on it.
    """
    employee = _employee_for_user(session, user)

    if local_in is None and local_out is None:
        raise ValidationFailed(
            "Provide a corrected punch-in time, a punch-out time, or both.",
            details=[{"field": "requested_in_at", "message": "At least one is required."}],
        )
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required.", details=[{"field": "reason", "message": "Required."}]
        )

    window_days = settings_service.get_value(
        session, "attendance.regularization_window_days", default=30
    )
    today = date.today()
    if work_date > today:
        raise PolicyViolation(
            "Attendance cannot be regularized for a future date.",
            details=[{"field": "work_date", "message": "Must not be in the future."}],
        )
    if window_days and (today - work_date).days > window_days:
        raise PolicyViolation(
            f"Attendance can only be corrected within {window_days} days. "
            f"{work_date.isoformat()} is outside that window - ask HR to make a "
            "manual correction.",
            details=[{"field": "work_date", "message": "Outside the allowed window."}],
        )
    if not employee.is_employed_on(work_date):
        raise PolicyViolation(f"You were not on the rolls on {work_date.isoformat()}.")

    open_request = session.scalar(
        select(AttendanceRegularization).where(
            AttendanceRegularization.employee_id == employee.id,
            AttendanceRegularization.work_date == work_date,
            AttendanceRegularization.status.in_(["pending_manager", "pending_hr"]),
        )
    )
    if open_request is not None:
        raise Conflict(
            f"You already have a request awaiting a decision for {work_date.isoformat()}.",
            details=[{"field": "work_date", "message": "A request is already open."}],
        )

    timezone_name = shifts_service.employee_timezone(session, employee.id)
    requested_in = local_datetime_to_utc(local_in, timezone_name) if local_in is not None else None
    requested_out = (
        local_datetime_to_utc(local_out, timezone_name) if local_out is not None else None
    )
    if requested_in and requested_out and requested_out <= requested_in:
        raise ValidationFailed(
            "The punch-out time must be after the punch-in time.",
            details=[{"field": "requested_out_at", "message": "Must be after punch-in."}],
        )

    current = session.scalar(
        select(AttendanceDay).where(
            AttendanceDay.employee_id == employee.id,
            AttendanceDay.work_date == work_date,
        )
    )

    manager_missing = employee.reporting_manager_id is None
    if not manager_missing:
        manager = session.get(Employee, employee.reporting_manager_id)
        manager_missing = manager is None or manager.employment_status == "exited"

    behaviour = settings_service.get_value(
        session, "leave.manager_missing_behaviour", default="escalate_to_hr"
    )
    if manager_missing and behaviour == "block":
        raise PolicyViolation(
            "You have no active reporting manager, so this request cannot be "
            "routed. Ask HR to assign one."
        )

    request = AttendanceRegularization(
        employee_id=employee.id,
        work_date=work_date,
        requested_in_at=requested_in,
        requested_out_at=requested_out,
        reason=reason.strip(),
        status="pending_hr" if manager_missing else "pending_manager",
        current_stage="hr_admin" if manager_missing else "reporting_manager",
        status_before=current.status if current else None,
        escalated_from_missing_manager=manager_missing,
    )
    session.add(request)
    session.flush()

    audit.record(
        session,
        action="attendance.regularization_requested",
        context=context,
        entity_type="attendance_regularization",
        entity_id=request.id,
        after={
            "work_date": work_date.isoformat(),
            "status": request.status,
            "status_before": request.status_before,
            "escalated": manager_missing,
        },
    )
    notification_hooks.regularization_awaiting_approval(
        session,
        request_id=request.id,
        employee_id=request.employee_id,
        work_date=request.work_date,
        stage="manager" if not manager_missing else "hr",
    )
    return request


def _assert_is_manager_of(session: Session, *, user: User, employee_id: int) -> None:
    """Verify the relationship, not merely the permission.

    Holding ``regularization.approve_as_manager`` is a precondition; it does not
    say *whose* requests you may review. Without this check, any employee could
    approve any other employee's correction.
    """
    if user.employee_id is None:
        raise PermissionDenied(
            "Your account is not linked to an employee record, so you cannot review requests."
        )
    subject = session.get(Employee, employee_id)
    if subject is None:
        raise NotFound("Employee not found.")
    if subject.reporting_manager_id != user.employee_id:
        raise PermissionDenied("You can only review requests from your own direct reports.")


def decide_regularization(
    session: Session,
    *,
    request_id: int,
    user: User,
    approve: bool,
    comment: str | None,
    context: audit.AuditContext,
) -> AttendanceRegularization:
    """Record a decision at whichever stage the request is currently at.

    An employee can never decide their own request, regardless of permissions -
    including an HR user who happens to be the subject.
    """
    request = session.get(AttendanceRegularization, request_id)
    if request is None:
        raise NotFound("Regularization request not found.")

    if request.status in {"approved", "rejected", "cancelled"}:
        raise Conflict(f"That request was already {request.status.replace('_', ' ')}.")

    if user.employee_id is not None and user.employee_id == request.employee_id:
        raise PermissionDenied("You cannot decide your own regularization request.")

    decision = "approved" if approve else "rejected"
    # Captured before the branch moves it on: the stage that decided is part of
    # the notification's dedupe key, and by the end of this function
    # ``current_stage`` is the next one.
    stage_decided = "manager" if request.current_stage == "reporting_manager" else "hr"
    now = utc_now()

    if request.current_stage == "reporting_manager":
        if not user.has_permission("regularization.approve_as_manager"):
            raise PermissionDenied("You do not have permission to review requests.")
        _assert_is_manager_of(session, user=user, employee_id=request.employee_id)

        request.manager_user_id = user.id
        request.manager_decision = decision
        request.manager_comment = comment
        request.manager_decided_at = now

        if approve:
            # Manager approval advances; it does not apply the change. HR gives
            # the final decision, per the confirmed two-stage chain.
            request.status = "pending_hr"
            request.current_stage = "hr_admin"
        else:
            request.status = "rejected"
            request.current_stage = "complete"

    elif request.current_stage == "hr_admin":
        if not user.has_permission("regularization.approve_hr"):
            raise PermissionDenied("Only HR/Admin can give the final decision on this request.")
        request.hr_user_id = user.id
        request.hr_decision = decision
        request.hr_comment = comment
        request.hr_decided_at = now
        request.status = "approved" if approve else "rejected"
        request.current_stage = "complete"
    else:  # pragma: no cover - guarded by the status check above
        raise Conflict("That request is not awaiting a decision.")

    session.flush()

    if request.status == "approved":
        _apply_regularization(session, request=request, context=context)

    audit.record(
        session,
        action=f"attendance.regularization_{request.status}",
        context=context,
        entity_type="attendance_regularization",
        entity_id=request.id,
        after={
            "status": request.status,
            "stage": request.current_stage,
            "decision": decision,
            "comment": comment,
        },
        extra={
            "employee_id": request.employee_id,
            "work_date": request.work_date.isoformat(),
        },
    )
    if request.status in ("approved", "rejected"):
        notification_hooks.regularization_decided(
            session,
            request_id=request.id,
            employee_id=request.employee_id,
            work_date=request.work_date,
            approved=request.status == "approved",
            stage=stage_decided,
            comment=comment,
        )
    if request.status == "pending_hr":
        notification_hooks.regularization_awaiting_approval(
            session,
            request_id=request.id,
            employee_id=request.employee_id,
            work_date=request.work_date,
            stage="hr",
        )
    session.flush()
    return request


def _apply_regularization(
    session: Session,
    *,
    request: AttendanceRegularization,
    context: audit.AuditContext,
) -> None:
    """Turn an approved request into attendance events, then recompute.

    Approval inserts events rather than writing figures: the correction then
    goes through the same engine as every other punch, and shows up in the raw
    record as a ``regularization`` source instead of silently altering a total.
    """
    timezone_name = shifts_service.employee_timezone(session, request.employee_id)
    reason = f"Regularization #{request.id}: {request.reason}"

    #: Any live punches for that day are superseded first. Leaving them would
    #: pair the approved times against the original ones and produce a figure
    #: nobody asked for.
    for event in session.scalars(
        select(AttendanceEvent).where(
            AttendanceEvent.employee_id == request.employee_id,
            AttendanceEvent.work_date == request.work_date,
            AttendanceEvent.is_superseded.is_(False),
        )
    ).all():
        event.is_superseded = True
        event.reason = (event.reason or "") + f" [superseded by regularization #{request.id}]"

    for event_type, occurred_at in (
        ("in", request.requested_in_at),
        ("out", request.requested_out_at),
    ):
        if occurred_at is None:
            continue
        session.add(
            AttendanceEvent(
                employee_id=request.employee_id,
                event_type=event_type,
                occurred_at=occurred_at,
                work_date=request.work_date,
                source="regularization",
                external_event_id=f"reg-{request.id}-{event_type}",
                timezone_used=timezone_name,
                reason=reason,
                recorded_by_user_id=context.actor_user_id,
            )
        )
    session.flush()

    recompute_day(
        session,
        employee_id=request.employee_id,
        work_date=request.work_date,
        force_over_manual_override=True,
    )


def cancel_regularization(
    session: Session, *, request_id: int, user: User, context: audit.AuditContext
) -> AttendanceRegularization:
    """Withdraw one's own request, while it is still undecided."""
    request = session.get(AttendanceRegularization, request_id)
    if request is None:
        raise NotFound("Regularization request not found.")
    if user.employee_id != request.employee_id:
        raise PermissionDenied("You can only withdraw your own request.")
    if request.status not in {"pending_manager", "pending_hr"}:
        raise Conflict(f"That request is already {request.status.replace('_', ' ')}.")

    request.status = "cancelled"
    request.current_stage = "complete"
    session.flush()

    audit.record(
        session,
        action="attendance.regularization_cancelled",
        context=context,
        entity_type="attendance_regularization",
        entity_id=request.id,
    )
    return request


def pending_for_reviewer(session: Session, user: User) -> list[AttendanceRegularization]:
    """Requests this user can act on right now.

    Returns the manager-stage requests from their own reports, plus every
    HR-stage request when they hold the HR permission. Scoped here rather than
    in the endpoint so a reviewer is never shown something they cannot decide.
    """
    results: list[AttendanceRegularization] = []

    if user.employee_id is not None and user.has_permission("regularization.approve_as_manager"):
        report_ids = session.scalars(
            select(Employee.id).where(Employee.reporting_manager_id == user.employee_id)
        ).all()
        if report_ids:
            results.extend(
                session.scalars(
                    select(AttendanceRegularization)
                    .where(
                        AttendanceRegularization.employee_id.in_(report_ids),
                        AttendanceRegularization.status == "pending_manager",
                    )
                    .order_by(AttendanceRegularization.work_date)
                ).all()
            )

    if user.has_permission("regularization.approve_hr"):
        results.extend(
            session.scalars(
                select(AttendanceRegularization)
                .where(AttendanceRegularization.status == "pending_hr")
                .order_by(AttendanceRegularization.work_date)
            ).all()
        )

    return results


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def day_range(session: Session, *, employee_id: int, start: date, end: date) -> list[AttendanceDay]:
    return list(
        session.scalars(
            select(AttendanceDay)
            .where(
                AttendanceDay.employee_id == employee_id,
                AttendanceDay.work_date >= start,
                AttendanceDay.work_date <= end,
            )
            .order_by(AttendanceDay.work_date)
        ).all()
    )


def month_summary(session: Session, *, employee_id: int, start: date, end: date) -> dict[str, Any]:
    """Totals for a period, computed from the stored day rows.

    ``payable_days`` sums ``payable_day_fraction`` rather than counting
    statuses, so it cannot drift from what payroll will use.
    """
    rows = day_range(session, employee_id=employee_id, start=start, end=end)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days_recorded": len(rows),
        "status_counts": counts,
        # Returned as a string so the exact Decimal survives JSON; a float
        # would reintroduce the rounding error the Decimal column avoids.
        "payable_days": str(sum((row.payable_day_fraction for row in rows), start=Decimal("0"))),
        "worked_minutes": sum(row.net_minutes for row in rows),
        "overtime_minutes": sum(row.overtime_minutes for row in rows),
        "late_days": sum(1 for row in rows if row.is_late),
        "early_exit_days": sum(1 for row in rows if row.is_early_exit),
        "missing_punch_days": sum(1 for row in rows if row.has_missing_punch),
        "days_with_anomalies": sum(1 for row in rows if row.anomalies),
    }


def today_overview(session: Session) -> dict[str, Any]:
    """Company-wide counts for today, for the HR dashboard."""
    today = date.today()
    rows = session.execute(
        select(AttendanceDay.status, func.count())
        .where(AttendanceDay.work_date == today)
        .group_by(AttendanceDay.status)
    ).all()
    counts = {status: count for status, count in rows}

    active = (
        session.scalar(
            select(func.count()).select_from(Employee).where(Employee.employment_status != "exited")
        )
        or 0
    )

    late = (
        session.scalar(
            select(func.count())
            .select_from(AttendanceDay)
            .where(AttendanceDay.work_date == today, AttendanceDay.is_late.is_(True))
        )
        or 0
    )
    missing = (
        session.scalar(
            select(func.count())
            .select_from(AttendanceDay)
            .where(
                AttendanceDay.work_date == today,
                AttendanceDay.has_missing_punch.is_(True),
            )
        )
        or 0
    )
    pending = (
        session.scalar(
            select(func.count())
            .select_from(AttendanceRegularization)
            .where(AttendanceRegularization.status.in_(["pending_manager", "pending_hr"]))
        )
        or 0
    )

    return {
        "date": today.isoformat(),
        "active_employees": active,
        "status_counts": counts,
        "present": counts.get("present", 0),
        "half_day": counts.get("half_day", 0),
        "absent": counts.get("absent", 0),
        "on_leave": counts.get("on_leave", 0),
        "weekly_off": counts.get("weekly_off", 0),
        "holiday": counts.get("holiday", 0),
        "incomplete": counts.get("incomplete", 0) + counts.get("missing_punch", 0),
        "late_arrivals": late,
        "missing_punches": missing,
        "pending_regularizations": pending,
        "not_yet_computed": max(active - sum(counts.values()), 0),
    }
