"""The attendance computation engine. Pure functions over values - no I/O.

Everything here takes plain data and returns plain data, which is what makes the
awkward cases - an overnight shift, a punch at 02:00, an employee who forgot to
punch out, a policy change applied retroactively - testable exactly, without a
database or a web request.

The model
---------
A **shift instance** is a concrete window on a concrete date: for work date D,
the shift runs from ``D + start_time`` to ``D + end_time`` (or ``D+1 +
end_time`` when it crosses midnight). Around that sits an **attribution
window**, widened by ``early_punch_window_minutes`` before the start and
``late_punch_window_minutes`` after the end.

A punch is attributed to the work date whose attribution window contains it.
That single rule handles the overnight case without any special-casing at the
call site: for a 22:00-06:00 shift, a punch at 02:00 on the 5th falls inside the
4th's window, so it belongs to the 4th - the date the shift started.

Measurements versus judgments
-----------------------------
``late_minutes`` is the actual lateness against the scheduled start, always.
``is_late`` is whether that exceeds the grace period. Keeping them apart means a
report can show "arrived 8 minutes late" while policy says the day was on time,
and changing the grace period does not rewrite the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

MINUTES_PER_DAY = 1440

RoundingMode = Literal["none", "nearest", "down", "up"]

FULL_DAY = Decimal("1.00")
HALF_DAY = Decimal("0.50")
NO_DAY = Decimal("0.00")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShiftSpec:
    """The shift rules the engine needs, with company defaults already merged in.

    Resolving "shift value or company setting" happens in the service layer, so
    this type has no optional policy fields and the engine has no fallbacks to
    reason about.
    """

    shift_id: int | None
    start_time: time
    end_time: time
    crosses_midnight: bool
    break_minutes: int
    grace_late_minutes: int
    grace_early_exit_minutes: int
    min_minutes_full_day: int
    min_minutes_half_day: int
    early_punch_window_minutes: int = 120
    late_punch_window_minutes: int = 300

    @property
    def scheduled_span_minutes(self) -> int:
        """Clock length of the shift window, break included."""
        start = _minutes_of_day(self.start_time)
        end = _minutes_of_day(self.end_time)
        return (MINUTES_PER_DAY - start + end) if self.crosses_midnight else (end - start)

    @property
    def scheduled_work_minutes(self) -> int:
        """Expected paid working time: the span minus the planned break."""
        return max(self.scheduled_span_minutes - self.break_minutes, 0)


@dataclass(frozen=True, slots=True)
class AttendancePolicy:
    """Company-level rules that are not part of the shift itself."""

    auto_deduct_break_minutes: int = 0
    rounding_mode: RoundingMode = "none"
    rounding_increment_minutes: int = 15
    overtime_enabled: bool = False
    overtime_min_minutes: int = 30
    missing_punch_status: str = "incomplete"

    def as_snapshot(self, shift: ShiftSpec) -> dict[str, object]:
        """The rules that produced a figure, stored alongside it.

        Without this, a figure computed under an old policy cannot be explained
        after the policy changes - which is exactly when someone asks.
        """
        return {
            "shift_id": shift.shift_id,
            "shift_start": shift.start_time.isoformat(),
            "shift_end": shift.end_time.isoformat(),
            "crosses_midnight": shift.crosses_midnight,
            "shift_break_minutes": shift.break_minutes,
            "scheduled_work_minutes": shift.scheduled_work_minutes,
            "grace_late_minutes": shift.grace_late_minutes,
            "grace_early_exit_minutes": shift.grace_early_exit_minutes,
            "min_minutes_full_day": shift.min_minutes_full_day,
            "min_minutes_half_day": shift.min_minutes_half_day,
            "auto_deduct_break_minutes": self.auto_deduct_break_minutes,
            "rounding_mode": self.rounding_mode,
            "rounding_increment_minutes": self.rounding_increment_minutes,
            "overtime_enabled": self.overtime_enabled,
            "overtime_min_minutes": self.overtime_min_minutes,
            "missing_punch_status": self.missing_punch_status,
        }


@dataclass(frozen=True, slots=True)
class PunchEvent:
    """A single punch, as the engine sees it."""

    event_id: int | None
    event_type: Literal["in", "out"]
    occurred_at: datetime
    """Must be timezone-aware."""


@dataclass(frozen=True, slots=True)
class DayContext:
    """Everything about the day that is not a punch."""

    work_date: date
    timezone_name: str
    day_type: Literal["working", "weekly_off", "holiday"]
    is_employed: bool = True
    on_approved_leave: bool = False
    leave_is_half_day: bool = False
    leave_application_id: int | None = None

    on_approved_compoff: bool = False
    compoff_is_half_day: bool = False
    """
    Approved compensatory off covering this day.

    Checked *after* leave: if both somehow cover the same date, the leave is the
    one that consumed a balance, so it takes precedence and the contradiction is
    left for HR rather than guessed at.
    """

    leave_is_paid: bool = True
    """
    Whether the approved leave covering this day is paid.

    This drives the payable fraction, so it is not cosmetic: paid leave is a
    payable day, unpaid leave is not, and getting it wrong would either pay for
    an unpaid absence or dock a legitimate holiday.
    """


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComputedSession:
    sequence: int
    started_at: datetime
    ended_at: datetime
    duration_minutes: int
    in_event_id: int | None
    out_event_id: int | None


@dataclass(frozen=True, slots=True)
class ComputedDay:
    work_date: date
    day_type: str
    status: str
    shift_id: int | None
    first_in_at: datetime | None
    last_out_at: datetime | None
    worked_minutes: int
    break_minutes: int
    net_minutes: int
    late_minutes: int
    early_exit_minutes: int
    overtime_minutes: int
    is_late: bool
    is_early_exit: bool
    has_missing_punch: bool
    payable_day_fraction: Decimal
    leave_application_id: int | None
    sessions: tuple[ComputedSession, ...] = ()
    anomalies: tuple[str, ...] = ()
    policy_snapshot: dict[str, object] = field(default_factory=dict)
    event_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minutes_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def shift_window(shift: ShiftSpec, work_date: date, tz) -> tuple[datetime, datetime]:
    """The scheduled start and end of this shift instance, as aware UTC.

    ``tz`` is a ``ZoneInfo``; it is passed in rather than resolved here so the
    engine stays free of configuration lookups.
    """
    start_local = datetime.combine(work_date, shift.start_time).replace(tzinfo=tz)
    end_date = work_date + timedelta(days=1) if shift.crosses_midnight else work_date
    end_local = datetime.combine(end_date, shift.end_time).replace(tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def attribution_window(shift: ShiftSpec, work_date: date, tz) -> tuple[datetime, datetime]:
    """The window within which a punch counts as belonging to ``work_date``."""
    start, end = shift_window(shift, work_date, tz)
    return (
        start - timedelta(minutes=shift.early_punch_window_minutes),
        end + timedelta(minutes=shift.late_punch_window_minutes),
    )


def attribute_work_date(
    punched_at: datetime,
    *,
    shift: ShiftSpec,
    tz,
) -> date:
    """Decide which shift date a punch belongs to.

    Checks the local calendar date and the one before it, and returns whichever
    shift instance's attribution window contains the punch. The previous date is
    tried **first** for an overnight shift, because that is the case the rule
    exists for: at 02:00 both windows may be open, and the punch belongs to the
    shift that is still running, not the one about to start.

    Falls back to the local calendar date when neither window matches - an
    out-of-hours punch on a day off, for instance - so a punch is never
    silently dropped.
    """
    if punched_at.tzinfo is None:
        raise ValueError("attribute_work_date() requires an aware datetime.")

    local_date = punched_at.astimezone(tz).date()
    candidates = (
        [local_date - timedelta(days=1), local_date]
        if shift.crosses_midnight
        else [local_date, local_date - timedelta(days=1)]
    )

    for candidate in candidates:
        window_start, window_end = attribution_window(shift, candidate, tz)
        if window_start <= punched_at <= window_end:
            return candidate
    return local_date


def round_minutes(minutes: int, mode: RoundingMode, increment: int) -> int:
    """Round worked minutes according to policy.

    Rounding is applied once, to the day's net total - not per session, which
    would compound the error across a multi-session day.
    """
    if mode == "none" or increment <= 1 or minutes <= 0:
        return minutes
    whole, remainder = divmod(minutes, increment)
    if remainder == 0:
        return minutes
    if mode == "down":
        return whole * increment
    if mode == "up":
        return (whole + 1) * increment
    # "nearest": exactly half rounds up, which is the common payroll convention.
    return (whole + 1) * increment if remainder * 2 >= increment else whole * increment


def pair_sessions(
    events: list[PunchEvent],
) -> tuple[list[ComputedSession], list[str], bool]:
    """Pair in/out punches into sessions, reporting what did not pair.

    Real punch data is messy, and each kind of mess needs a defined outcome
    rather than an exception:

    * **Two INs in a row** - the second is a duplicate (double-click, two
      devices). The first is kept, because it is the earlier arrival and
      therefore the one that matters for lateness.
    * **An OUT with nothing open** - recorded as an anomaly and skipped. This is
      usually the tail of a shift that began on the previous date.
    * **An unclosed IN at the end** - the missing-punch case. Flagged; no
      session is invented, because guessing a departure time would put a
      fabricated figure into payroll.
    * **A zero-length session** - skipped, since the database requires a
      positive duration.

    Returns ``(sessions, anomalies, has_missing_punch)``.
    """
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_type))
    sessions: list[ComputedSession] = []
    anomalies: list[str] = []

    open_in: PunchEvent | None = None
    sequence = 0

    for event in ordered:
        if event.event_type == "in":
            if open_in is not None:
                anomalies.append(
                    f"Duplicate punch-in at {event.occurred_at.isoformat()} ignored; "
                    f"already in since {open_in.occurred_at.isoformat()}."
                )
                continue
            open_in = event
            continue

        # event_type == "out"
        if open_in is None:
            anomalies.append(
                f"Punch-out at {event.occurred_at.isoformat()} has no matching "
                "punch-in and was ignored."
            )
            continue

        duration = int((event.occurred_at - open_in.occurred_at).total_seconds() // 60)
        if duration <= 0:
            anomalies.append(
                f"Punch-out at {event.occurred_at.isoformat()} is not after its "
                "punch-in; the pair was ignored."
            )
            open_in = None
            continue

        sequence += 1
        sessions.append(
            ComputedSession(
                sequence=sequence,
                started_at=open_in.occurred_at,
                ended_at=event.occurred_at,
                duration_minutes=duration,
                in_event_id=open_in.event_id,
                out_event_id=event.event_id,
            )
        )
        open_in = None

    has_missing_punch = open_in is not None
    if has_missing_punch and open_in is not None:
        anomalies.append(f"Punched in at {open_in.occurred_at.isoformat()} with no punch-out.")

    return sessions, anomalies, has_missing_punch


def gap_minutes(sessions: list[ComputedSession]) -> int:
    """Total time between sessions - breaks the employee punched out for."""
    total = 0
    for earlier, later in zip(sessions, sessions[1:], strict=False):
        gap = int((later.started_at - earlier.ended_at).total_seconds() // 60)
        total += max(gap, 0)
    return total


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def compute_day(
    *,
    context: DayContext,
    shift: ShiftSpec,
    policy: AttendancePolicy,
    events: list[PunchEvent],
    tz,
) -> ComputedDay | None:
    """Compute one employee-day from its punches, shift and policy.

    Returns ``None`` when the employee was not on the rolls - before joining or
    after the last working day. No row at all is the honest representation of
    "not applicable", and it is deliberately different from a row saying
    ``absent``.
    """
    if not context.is_employed:
        return None

    snapshot = policy.as_snapshot(shift)
    sessions, anomalies, has_missing_punch = pair_sessions(events)

    worked = sum(session.duration_minutes for session in sessions)
    breaks = gap_minutes(sessions)

    # An assumed break models "there is a lunch hour whether or not you punched
    # out for it". Only the shortfall is deducted, so punching out for a real
    # 45-minute break is not penalised twice when the assumption is 30.
    if policy.auto_deduct_break_minutes > 0 and worked > 0:
        shortfall = policy.auto_deduct_break_minutes - breaks
        if shortfall > 0:
            deducted = min(shortfall, worked)
            worked -= deducted
            breaks += deducted

    net = round_minutes(worked, policy.rounding_mode, policy.rounding_increment_minutes)

    first_in = sessions[0].started_at if sessions else None
    last_out = sessions[-1].ended_at if sessions else None
    if first_in is None and events:
        ins = [event for event in events if event.event_type == "in"]
        if ins:
            first_in = min(event.occurred_at for event in ins)

    scheduled_start, scheduled_end = shift_window(shift, context.work_date, tz)

    late_minutes = 0
    is_late = False
    if first_in is not None and context.day_type == "working":
        late_minutes = max(int((first_in - scheduled_start).total_seconds() // 60), 0)
        is_late = late_minutes > shift.grace_late_minutes

    early_exit_minutes = 0
    is_early_exit = False
    if last_out is not None and context.day_type == "working" and not has_missing_punch:
        early_exit_minutes = max(int((scheduled_end - last_out).total_seconds() // 60), 0)
        is_early_exit = early_exit_minutes > shift.grace_early_exit_minutes

    overtime = 0
    if policy.overtime_enabled:
        beyond = net - shift.scheduled_work_minutes
        # On a day off, all worked time is overtime - there is no schedule to
        # exceed. This is also what makes the comp-off credit in Milestone 6
        # computable from the same figure.
        if context.day_type != "working":
            beyond = net
        if beyond >= policy.overtime_min_minutes:
            overtime = beyond

    status, payable = _classify(
        context=context,
        shift=shift,
        policy=policy,
        net_minutes=net,
        has_events=bool(events),
        has_missing_punch=has_missing_punch,
    )

    return ComputedDay(
        work_date=context.work_date,
        day_type=context.day_type,
        status=status,
        shift_id=shift.shift_id,
        first_in_at=first_in,
        last_out_at=last_out,
        worked_minutes=worked,
        break_minutes=breaks,
        net_minutes=net,
        late_minutes=late_minutes,
        early_exit_minutes=early_exit_minutes,
        overtime_minutes=overtime,
        is_late=is_late,
        is_early_exit=is_early_exit,
        has_missing_punch=has_missing_punch,
        payable_day_fraction=payable,
        leave_application_id=context.leave_application_id,
        sessions=tuple(sessions),
        anomalies=tuple(anomalies),
        policy_snapshot=snapshot,
        event_count=len(events),
    )


def _classify(
    *,
    context: DayContext,
    shift: ShiftSpec,
    policy: AttendancePolicy,
    net_minutes: int,
    has_events: bool,
    has_missing_punch: bool,
) -> tuple[str, Decimal]:
    """Decide the day's status and payable fraction.

    The order is deliberate and is the part most likely to be questioned, so it
    is spelled out:

    1. **Holiday / weekly off** win over everything. Work on a day off does not
       become a "present" day that consumes an attendance obligation; it earns
       comp-off instead (Milestone 6), computed from ``overtime_minutes``.
    2. **Approved leave** next, and its payable fraction follows whether the
       leave is *paid*: a full day of paid leave is a payable day, unpaid leave
       pays nothing, and a half day pays its half plus the worked half if the
       employee met the half-day threshold. An employee on full-day leave who
       also punched in is left as ``on_leave`` and the punches remain on the
       record - resolving that is
       HR's call, not the engine's guess.
    3. **Approved comp-off** next, always payable - it compensates work already
       done on a day off, which attendance itself paid nothing for.
    4. **No punches at all** on a working day is ``absent``.
    5. **A missing punch-out** is governed by ``attendance.missing_punch_status``,
       which the brief requires not to default to absence. Hours worked are
       unknown, so the fraction is 0 until it is regularized; the status makes
       clear that is a data problem rather than a no-show.
    6. **Thresholds**: full day, then half day.
    7. **Below the half-day threshold** is ``absent`` - but ``worked_minutes``
       is still recorded, so "came in for 40 minutes" is visible in reports and
       is not the same thing as never arriving.
    """
    if context.day_type == "holiday":
        return "holiday", NO_DAY
    if context.day_type == "weekly_off":
        return "weekly_off", NO_DAY

    if context.on_approved_leave:
        # Paid leave is a payable day; unpaid leave is not. For a half day the
        # leave covers its half, and the worked half is added only if the
        # employee actually met the half-day threshold - so "half day leave,
        # came in for the afternoon" pays a full day and "half day leave, never
        # came in" pays half.
        if not context.leave_is_half_day:
            return "on_leave", FULL_DAY if context.leave_is_paid else NO_DAY

        leave_part = HALF_DAY if context.leave_is_paid else NO_DAY
        worked_part = (
            HALF_DAY
            if has_events and not has_missing_punch and net_minutes >= shift.min_minutes_half_day
            else NO_DAY
        )
        return "on_leave", leave_part + worked_part

    if context.on_approved_compoff:
        # Comp-off is always payable: it compensates work already done on a day
        # off, which attendance itself paid nothing for. Paying nothing again
        # would mean the employee worked the weekend for free.
        if not context.compoff_is_half_day:
            return "compoff", FULL_DAY
        worked_part = (
            HALF_DAY
            if has_events and not has_missing_punch and net_minutes >= shift.min_minutes_half_day
            else NO_DAY
        )
        return "compoff", HALF_DAY + worked_part

    if not has_events:
        return "absent", NO_DAY

    if has_missing_punch and net_minutes < shift.min_minutes_half_day:
        return policy.missing_punch_status, NO_DAY

    if net_minutes >= shift.min_minutes_full_day:
        return "present", FULL_DAY
    if net_minutes >= shift.min_minutes_half_day:
        return "half_day", HALF_DAY

    return "absent", NO_DAY


# ---------------------------------------------------------------------------
# Punch validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PunchDecision:
    """Whether a punch may be recorded, and what it would be."""

    allowed: bool
    event_type: Literal["in", "out"]
    work_date: date
    reason: str | None = None
    is_duplicate: bool = False


MIN_SECONDS_BETWEEN_PUNCHES = 30


def decide_punch(
    *,
    now: datetime,
    requested_type: Literal["in", "out", "auto"],
    existing_today: list[PunchEvent],
    shift: ShiftSpec,
    tz,
    is_employed: bool,
    web_punch_enabled: bool,
) -> PunchDecision:
    """Validate a self-service punch and work out what it means.

    ``auto`` lets the UI offer a single button: the next punch is an ``out`` if
    the employee is currently inside an open session, otherwise an ``in``.

    A repeat of the same punch type within
    :data:`MIN_SECONDS_BETWEEN_PUNCHES` is treated as a duplicate rather than an
    error - a double-tapped button should not produce a failure the employee has
    to interpret. The caller returns the existing state and records nothing.
    """
    work_date = attribute_work_date(now, shift=shift, tz=tz)

    if not web_punch_enabled:
        return PunchDecision(
            allowed=False,
            event_type="in",
            work_date=work_date,
            reason="Web punching is disabled in company settings.",
        )

    if not is_employed:
        return PunchDecision(
            allowed=False,
            event_type="in",
            work_date=work_date,
            reason=("You are not on the rolls for this date, so attendance cannot be recorded."),
        )

    _, _, currently_in = pair_sessions(existing_today)
    # "auto" toggles against the current state; an explicit request is honoured
    # as given and validated below.
    resolved: Literal["in", "out"] = (
        ("out" if currently_in else "in") if requested_type == "auto" else requested_type
    )

    if existing_today:
        last = max(existing_today, key=lambda event: event.occurred_at)
        if (
            last.event_type == resolved
            and (now - last.occurred_at).total_seconds() < MIN_SECONDS_BETWEEN_PUNCHES
        ):
            return PunchDecision(
                allowed=False,
                event_type=resolved,
                work_date=work_date,
                reason="That punch was already recorded a moment ago.",
                is_duplicate=True,
            )

    if resolved == "out" and not currently_in:
        return PunchDecision(
            allowed=False,
            event_type="out",
            work_date=work_date,
            reason="You are not currently punched in.",
        )
    if resolved == "in" and currently_in:
        return PunchDecision(
            allowed=False,
            event_type="in",
            work_date=work_date,
            reason="You are already punched in. Punch out first.",
        )

    return PunchDecision(allowed=True, event_type=resolved, work_date=work_date)
