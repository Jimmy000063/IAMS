"""Tests for the attendance computation engine. No database.

This is the most consequential logic in the system - it decides whether someone
was present, and therefore what they are paid - so the awkward cases are tested
exhaustively and explicitly: overnight shifts, punches either side of midnight,
unpaired punches, grace boundaries, rounding, and days off.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.modules.attendance.policy import (
    AttendancePolicy,
    DayContext,
    PunchEvent,
    ShiftSpec,
    attribute_work_date,
    compute_day,
    decide_punch,
    gap_minutes,
    pair_sessions,
    round_minutes,
    shift_window,
)

pytestmark = pytest.mark.unit

IST = ZoneInfo("Asia/Kolkata")

#: 09:30-18:30 with a one-hour break: 480 scheduled working minutes.
DAY_SHIFT = ShiftSpec(
    shift_id=1,
    start_time=time(9, 30),
    end_time=time(18, 30),
    crosses_midnight=False,
    break_minutes=60,
    grace_late_minutes=15,
    grace_early_exit_minutes=15,
    min_minutes_full_day=480,
    min_minutes_half_day=240,
)

#: 22:00-06:00 with a 45-minute break: 435 scheduled working minutes.
NIGHT_SHIFT = ShiftSpec(
    shift_id=2,
    start_time=time(22, 0),
    end_time=time(6, 0),
    crosses_midnight=True,
    break_minutes=45,
    grace_late_minutes=15,
    grace_early_exit_minutes=15,
    min_minutes_full_day=420,
    min_minutes_half_day=210,
)

PLAIN_POLICY = AttendancePolicy()


def ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """An aware UTC instant from an IST wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(UTC)


def punches(*pairs: tuple[str, datetime]) -> list[PunchEvent]:
    return [
        PunchEvent(event_id=index + 1, event_type=kind, occurred_at=when)  # type: ignore[arg-type]
        for index, (kind, when) in enumerate(pairs)
    ]


def working_day(day: date) -> DayContext:
    return DayContext(work_date=day, timezone_name="Asia/Kolkata", day_type="working")


class TestShiftMath:
    def test_day_shift_span_and_work_minutes(self) -> None:
        assert DAY_SHIFT.scheduled_span_minutes == 540
        assert DAY_SHIFT.scheduled_work_minutes == 480

    def test_night_shift_span_wraps_midnight(self) -> None:
        assert NIGHT_SHIFT.scheduled_span_minutes == 480
        assert NIGHT_SHIFT.scheduled_work_minutes == 435

    def test_day_shift_window_is_same_day(self) -> None:
        start, end = shift_window(DAY_SHIFT, date(2026, 11, 3), IST)
        assert start == ist(2026, 11, 3, 9, 30)
        assert end == ist(2026, 11, 3, 18, 30)

    def test_night_shift_window_ends_the_next_day(self) -> None:
        start, end = shift_window(NIGHT_SHIFT, date(2026, 11, 3), IST)
        assert start == ist(2026, 11, 3, 22, 0)
        assert end == ist(2026, 11, 4, 6, 0)


class TestWorkDateAttribution:
    """Which shift date a punch belongs to.

    The overnight case is the reason this exists: without it, a 02:00 punch
    would be filed against the wrong date and the night shift would appear as
    two half-days.
    """

    def test_day_shift_punch_uses_its_own_date(self) -> None:
        assert attribute_work_date(ist(2026, 11, 3, 9, 25), shift=DAY_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_night_shift_punch_before_midnight(self) -> None:
        assert attribute_work_date(ist(2026, 11, 3, 21, 55), shift=NIGHT_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_night_shift_punch_after_midnight_belongs_to_the_previous_date(self) -> None:
        """The core overnight rule: 02:00 on the 4th is the 3rd's shift."""
        assert attribute_work_date(ist(2026, 11, 4, 2, 0), shift=NIGHT_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_night_shift_punch_out_at_shift_end(self) -> None:
        assert attribute_work_date(ist(2026, 11, 4, 6, 5), shift=NIGHT_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_night_shift_overtime_within_the_late_window(self) -> None:
        # 300-minute late window from 06:00 reaches 11:00.
        assert attribute_work_date(ist(2026, 11, 4, 8, 30), shift=NIGHT_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_night_shift_punch_outside_every_window_falls_back(self) -> None:
        """Never silently dropped: it lands on its own local date."""
        assert attribute_work_date(ist(2026, 11, 4, 15, 0), shift=NIGHT_SHIFT, tz=IST) == date(
            2026, 11, 4
        )

    def test_early_arrival_within_the_early_window(self) -> None:
        # 120-minute early window before 09:30 reaches back to 07:30.
        assert attribute_work_date(ist(2026, 11, 3, 8, 0), shift=DAY_SHIFT, tz=IST) == date(
            2026, 11, 3
        )

    def test_timezone_matters_not_the_server(self) -> None:
        """A punch at 00:30 IST is still the previous UTC day.

        If attribution used UTC, this punch would be filed against the wrong
        date for every employee in India.
        """
        dubai = ZoneInfo("Asia/Dubai")
        moment = datetime(2026, 11, 4, 0, 30, tzinfo=IST).astimezone(UTC)
        assert moment.astimezone(UTC).date() == date(2026, 11, 3), "it is still the 3rd in UTC"
        assert attribute_work_date(moment, shift=DAY_SHIFT, tz=IST) == date(2026, 11, 4)
        # Interpreted in a different zone, the same instant is a different date.
        assert attribute_work_date(moment, shift=DAY_SHIFT, tz=dubai) == date(2026, 11, 3)

    def test_naive_datetime_is_refused(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            attribute_work_date(datetime(2026, 11, 3, 9, 0), shift=DAY_SHIFT, tz=IST)


class TestSessionPairing:
    def test_simple_pair(self) -> None:
        sessions, anomalies, missing = pair_sessions(
            punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 18, 30)))
        )
        assert len(sessions) == 1
        assert sessions[0].duration_minutes == 540
        assert anomalies == []
        assert missing is False

    def test_multiple_sessions(self) -> None:
        sessions, anomalies, missing = pair_sessions(
            punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 13, 0)),
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 30)),
            )
        )
        assert [item.duration_minutes for item in sessions] == [210, 270]
        assert gap_minutes(sessions) == 60
        assert anomalies == []
        assert missing is False

    def test_events_out_of_order_are_sorted(self) -> None:
        sessions, _, _ = pair_sessions(
            punches(("out", ist(2026, 11, 3, 18, 0)), ("in", ist(2026, 11, 3, 9, 0)))
        )
        assert len(sessions) == 1
        assert sessions[0].duration_minutes == 540

    def test_duplicate_punch_in_keeps_the_earlier_one(self) -> None:
        """The first arrival is the one that matters for lateness."""
        sessions, anomalies, _ = pair_sessions(
            punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("in", ist(2026, 11, 3, 10, 0)),
                ("out", ist(2026, 11, 3, 18, 30)),
            )
        )
        assert len(sessions) == 1
        assert sessions[0].started_at == ist(2026, 11, 3, 9, 30)
        assert len(anomalies) == 1
        assert "Duplicate punch-in" in anomalies[0]

    def test_punch_out_without_a_punch_in_is_reported(self) -> None:
        sessions, anomalies, missing = pair_sessions(punches(("out", ist(2026, 11, 3, 18, 30))))
        assert sessions == []
        assert missing is False
        assert "no matching punch-in" in anomalies[0]

    def test_unclosed_punch_in_is_a_missing_punch(self) -> None:
        sessions, anomalies, missing = pair_sessions(punches(("in", ist(2026, 11, 3, 9, 30))))
        assert sessions == []
        assert missing is True
        assert "no punch-out" in anomalies[0]

    def test_no_departure_time_is_invented(self) -> None:
        """Guessing would put a fabricated figure into payroll."""
        sessions, _, missing = pair_sessions(
            punches(
                ("in", ist(2026, 11, 3, 9, 0)),
                ("out", ist(2026, 11, 3, 13, 0)),
                ("in", ist(2026, 11, 3, 14, 0)),
            )
        )
        assert len(sessions) == 1, "only the completed pair counts"
        assert missing is True

    def test_zero_length_session_is_skipped(self) -> None:
        moment = ist(2026, 11, 3, 9, 30)
        sessions, anomalies, _ = pair_sessions(punches(("in", moment), ("out", moment)))
        assert sessions == []
        assert anomalies

    def test_no_events_is_not_an_anomaly(self) -> None:
        assert pair_sessions([]) == ([], [], False)


class TestRounding:
    @pytest.mark.parametrize(
        "minutes,mode,increment,expected",
        [
            (487, "none", 15, 487),
            (487, "down", 15, 480),
            (487, "up", 15, 495),
            (487, "nearest", 15, 480),
            (488, "nearest", 15, 495),
            (480, "nearest", 15, 480),
            (0, "up", 15, 0),
            (7, "nearest", 15, 0),
            (8, "nearest", 15, 15),
            (100, "none", 1, 100),
        ],
    )
    def test_rounding_modes(self, minutes: int, mode: str, increment: int, expected: int) -> None:
        assert round_minutes(minutes, mode, increment) == expected  # type: ignore[arg-type]

    def test_exact_half_rounds_up(self) -> None:
        """The common payroll convention, stated explicitly so it is not a surprise."""
        assert round_minutes(487, "nearest", 14) == 490


class TestComputeDayBasics:
    def test_full_day_present(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 13, 0)),
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 30)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.status == "present"
        assert result.worked_minutes == 480
        assert result.break_minutes == 60
        assert result.net_minutes == 480
        assert result.payable_day_fraction == Decimal("1.00")
        assert result.is_late is False
        assert result.anomalies == ()
        assert len(result.sessions) == 2

    def test_half_day_when_between_thresholds(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 14, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.net_minutes == 270
        assert result.status == "half_day"
        assert result.payable_day_fraction == Decimal("0.50")

    def test_below_half_day_is_absent_but_minutes_are_kept(self) -> None:
        """ "Came in for 40 minutes" must stay visible in reports."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 10, 10))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "absent"
        assert result.payable_day_fraction == Decimal("0.00")
        assert result.worked_minutes == 40, "the minutes are recorded even so"

    def test_no_punches_on_a_working_day_is_absent(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.status == "absent"
        assert result.worked_minutes == 0
        assert result.first_in_at is None

    def test_not_employed_returns_no_row(self) -> None:
        """No row means "not applicable" - deliberately not the same as absent."""
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                is_employed=False,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is None

    def test_policy_snapshot_is_recorded(self) -> None:
        """Without it, a figure cannot be explained after the policy changes."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        snapshot = result.policy_snapshot
        assert snapshot["min_minutes_full_day"] == 480
        assert snapshot["grace_late_minutes"] == 15
        assert snapshot["scheduled_work_minutes"] == 480


class TestLatenessAndEarlyExit:
    def test_within_grace_is_not_late_but_lateness_is_measured(self) -> None:
        """Measurement and judgment are separate, deliberately."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 40)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 10
        assert result.is_late is False

    def test_exactly_at_the_grace_boundary_is_not_late(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 45)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 15
        assert result.is_late is False, "grace is inclusive"

    def test_one_minute_past_grace_is_late(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 46)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 16
        assert result.is_late is True

    def test_early_arrival_is_not_negative_lateness(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 0)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 0

    def test_early_exit_beyond_grace(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 17, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.early_exit_minutes == 90
        assert result.is_early_exit is True

    def test_lateness_is_not_measured_on_a_day_off(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 7),
                timezone_name="Asia/Kolkata",
                day_type="weekly_off",
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 7, 11, 0)), ("out", ist(2026, 11, 7, 15, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 0
        assert result.is_late is False


class TestOvernightShift:
    def test_night_shift_spanning_midnight_is_one_present_day(self) -> None:
        """The whole point of work-date attribution.

        Without it this would be filed as two partial days either side of
        midnight, and the employee would appear to have worked two half-days.
        """
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=NIGHT_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 22, 0)), ("out", ist(2026, 11, 4, 6, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.work_date == date(2026, 11, 3)
        assert result.worked_minutes == 480
        assert result.status == "present"
        assert result.late_minutes == 0
        assert result.is_early_exit is False

    def test_night_shift_with_a_break_across_midnight(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=NIGHT_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(
                ("in", ist(2026, 11, 3, 22, 0)),
                ("out", ist(2026, 11, 3, 23, 45)),
                ("in", ist(2026, 11, 4, 0, 30)),
                ("out", ist(2026, 11, 4, 6, 0)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.worked_minutes == 105 + 330
        assert result.break_minutes == 45
        assert result.status == "present"

    def test_night_shift_late_arrival(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=NIGHT_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 22, 40)), ("out", ist(2026, 11, 4, 6, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.late_minutes == 40
        assert result.is_late is True

    def test_night_shift_early_exit_after_midnight(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=NIGHT_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 22, 0)), ("out", ist(2026, 11, 4, 4, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.early_exit_minutes == 120
        assert result.is_early_exit is True
        assert result.worked_minutes == 360
        assert result.status == "half_day", "360 is above the 210 half-day threshold"


class TestMissingPunch:
    def test_missing_punch_out_uses_the_configured_status(self) -> None:
        """The brief forbids treating a missing punch as absence by default."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(missing_punch_status="incomplete"),
            events=punches(("in", ist(2026, 11, 3, 9, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "incomplete"
        assert result.has_missing_punch is True
        assert result.payable_day_fraction == Decimal("0.00")
        assert result.first_in_at == ist(2026, 11, 3, 9, 30)
        assert result.last_out_at is None

    def test_configurable_to_missing_punch_status(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(missing_punch_status="missing_punch"),
            events=punches(("in", ist(2026, 11, 3, 9, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "missing_punch"

    def test_a_full_day_plus_a_dangling_punch_still_counts(self) -> None:
        """Enough completed time means the day stands, flagged for review.

        Penalising a full day's work because of a stray third punch would be
        the wrong outcome; the flag and the anomaly make it visible instead.
        """
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 18, 30)),
                ("in", ist(2026, 11, 3, 19, 0)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.status == "present"
        assert result.has_missing_punch is True
        assert result.anomalies

    def test_early_exit_is_not_claimed_when_the_punch_is_missing(self) -> None:
        """Departure is unknown, so no early-exit figure is invented."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.early_exit_minutes == 0
        assert result.is_early_exit is False


class TestDaysOff:
    def test_weekly_off_with_no_work(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 7),
                timezone_name="Asia/Kolkata",
                day_type="weekly_off",
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.status == "weekly_off"
        assert result.payable_day_fraction == Decimal("0.00")

    def test_work_on_a_weekly_off_stays_weekly_off(self) -> None:
        """It does not become a "present" day.

        Work on a day off earns comp-off (Milestone 6), computed from the
        recorded minutes - it does not satisfy an attendance obligation that did
        not exist.
        """
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 7),
                timezone_name="Asia/Kolkata",
                day_type="weekly_off",
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 7, 10, 0)), ("out", ist(2026, 11, 7, 18, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "weekly_off"
        assert result.worked_minutes == 480, "the minutes are kept for comp-off"
        assert result.payable_day_fraction == Decimal("0.00")

    def test_holiday_behaves_the_same(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 12, 25),
                timezone_name="Asia/Kolkata",
                day_type="holiday",
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 12, 25, 10, 0)), ("out", ist(2026, 12, 25, 16, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "holiday"
        assert result.worked_minutes == 360

    def test_all_work_on_a_day_off_is_overtime(self) -> None:
        """There is no schedule to exceed, so the whole lot counts."""
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 7),
                timezone_name="Asia/Kolkata",
                day_type="weekly_off",
            ),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(overtime_enabled=True, overtime_min_minutes=30),
            events=punches(("in", ist(2026, 11, 7, 10, 0)), ("out", ist(2026, 11, 7, 15, 0))),
            tz=IST,
        )
        assert result is not None
        assert result.overtime_minutes == 300


class TestLeave:
    def test_approved_paid_leave_is_a_payable_day(self) -> None:
        """Paid leave pays. The employee is absent but entitled to the day."""
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
                leave_is_paid=True,
                leave_application_id=42,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.status == "on_leave"
        assert result.leave_application_id == 42
        assert result.payable_day_fraction == Decimal("1.00")

    def test_approved_unpaid_leave_pays_nothing(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
                leave_is_paid=False,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.status == "on_leave"
        assert result.payable_day_fraction == Decimal("0.00")

    def test_half_day_paid_leave_with_no_work_pays_half(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
                leave_is_half_day=True,
                leave_is_paid=True,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.payable_day_fraction == Decimal("0.50")

    def test_half_day_leave_plus_working_the_other_half_pays_a_full_day(self) -> None:
        """The leave covers its half; the worked half is earned separately."""
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
                leave_is_half_day=True,
                leave_is_paid=True,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 30)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.status == "on_leave"
        assert result.payable_day_fraction == Decimal("1.00")

    def test_half_day_unpaid_leave_plus_work_pays_only_the_worked_half(self) -> None:
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
                leave_is_half_day=True,
                leave_is_paid=False,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 30)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.payable_day_fraction == Decimal("0.50")

    def test_punching_while_on_leave_keeps_the_leave_status(self) -> None:
        """Resolving the contradiction is HR's call, not the engine's guess."""
        result = compute_day(
            context=DayContext(
                work_date=date(2026, 11, 3),
                timezone_name="Asia/Kolkata",
                day_type="working",
                on_approved_leave=True,
            ),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.status == "on_leave"
        # 09:30-18:30 with no break punched and no automatic deduction: the
        # shift's scheduled break is not subtracted from actual worked time.
        assert result.worked_minutes == 540, "the punches are still on the record"


class TestBreaksAndOvertime:
    def test_automatic_break_deduction_when_nobody_punched_out(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(auto_deduct_break_minutes=30),
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 18, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.worked_minutes == 510
        assert result.break_minutes == 30

    def test_only_the_shortfall_is_deducted(self) -> None:
        """A real 45-minute break is not penalised again by a 30-minute assumption."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(auto_deduct_break_minutes=30),
            events=punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 13, 0)),
                ("in", ist(2026, 11, 3, 13, 45)),
                ("out", ist(2026, 11, 3, 18, 30)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.break_minutes == 45, "the real break already exceeds the assumption"
        assert result.worked_minutes == 210 + 285

    def test_no_deduction_when_nothing_was_worked(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(auto_deduct_break_minutes=30),
            events=[],
            tz=IST,
        )
        assert result is not None
        assert result.worked_minutes == 0
        assert result.break_minutes == 0

    def test_overtime_disabled_by_default(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=PLAIN_POLICY,
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 21, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.overtime_minutes == 0

    def test_overtime_beyond_the_scheduled_day(self) -> None:
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(overtime_enabled=True, overtime_min_minutes=30),
            events=punches(("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 21, 30))),
            tz=IST,
        )
        assert result is not None
        assert result.net_minutes == 720
        assert result.overtime_minutes == 240

    def test_overtime_below_the_threshold_is_ignored(self) -> None:
        # Break punched out, so worked time is 210 + 290 = 500 against a
        # 480-minute scheduled day: 20 minutes over, under the 30-minute bar.
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(overtime_enabled=True, overtime_min_minutes=30),
            events=punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 13, 0)),
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 50)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.net_minutes == 500
        assert result.overtime_minutes == 0, "20 minutes is under the 30-minute threshold"

    def test_rounding_applies_once_to_the_day_not_per_session(self) -> None:
        """Per-session rounding would compound the error across the day."""
        result = compute_day(
            context=working_day(date(2026, 11, 3)),
            shift=DAY_SHIFT,
            policy=AttendancePolicy(rounding_mode="down", rounding_increment_minutes=15),
            events=punches(
                ("in", ist(2026, 11, 3, 9, 30)),
                ("out", ist(2026, 11, 3, 13, 7)),
                ("in", ist(2026, 11, 3, 14, 0)),
                ("out", ist(2026, 11, 3, 18, 37)),
            ),
            tz=IST,
        )
        assert result is not None
        assert result.worked_minutes == 217 + 277
        assert result.net_minutes == 480, "494 rounded down to the nearest 15"


class TestPunchDecision:
    def test_first_punch_of_the_day_is_in(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 9, 30),
            requested_type="auto",
            existing_today=[],
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is True
        assert decision.event_type == "in"
        assert decision.work_date == date(2026, 11, 3)

    def test_auto_punch_toggles_to_out(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 18, 30),
            requested_type="auto",
            existing_today=punches(("in", ist(2026, 11, 3, 9, 30))),
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is True
        assert decision.event_type == "out"

    def test_auto_punch_returns_to_in_after_a_break(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 14, 0),
            requested_type="auto",
            existing_today=punches(
                ("in", ist(2026, 11, 3, 9, 30)), ("out", ist(2026, 11, 3, 13, 0))
            ),
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.event_type == "in"

    def test_double_click_is_a_duplicate_not_an_error(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 9, 30) + timedelta(seconds=3),
            requested_type="in",
            existing_today=punches(("in", ist(2026, 11, 3, 9, 30))),
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is False
        assert decision.is_duplicate is True

    def test_a_deliberate_repeat_later_is_refused_not_duplicated(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 11, 0),
            requested_type="in",
            existing_today=punches(("in", ist(2026, 11, 3, 9, 30))),
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is False
        assert decision.is_duplicate is False
        assert "already punched in" in (decision.reason or "")

    def test_punch_out_without_being_in_is_refused(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 18, 0),
            requested_type="out",
            existing_today=[],
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is False
        assert "not currently punched in" in (decision.reason or "")

    def test_disabled_web_punching_is_refused(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 9, 30),
            requested_type="auto",
            existing_today=[],
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=False,
        )
        assert decision.allowed is False
        assert "disabled" in (decision.reason or "")

    def test_not_employed_is_refused(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 3, 9, 30),
            requested_type="auto",
            existing_today=[],
            shift=DAY_SHIFT,
            tz=IST,
            is_employed=False,
            web_punch_enabled=True,
        )
        assert decision.allowed is False
        assert "not on the rolls" in (decision.reason or "")

    def test_night_shift_punch_out_after_midnight_targets_the_start_date(self) -> None:
        decision = decide_punch(
            now=ist(2026, 11, 4, 6, 0),
            requested_type="auto",
            existing_today=punches(("in", ist(2026, 11, 3, 22, 0))),
            shift=NIGHT_SHIFT,
            tz=IST,
            is_employed=True,
            web_punch_enabled=True,
        )
        assert decision.allowed is True
        assert decision.event_type == "out"
        assert decision.work_date == date(2026, 11, 3)
