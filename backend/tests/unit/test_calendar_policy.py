"""Tests for shift and calendar resolution. No database."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.shifts.policy import (
    AssignmentWindow,
    CalendarSpec,
    date_range,
    overlaps,
    resolve_assignment,
    resolve_day_type,
    working_days_between,
)

pytestmark = pytest.mark.unit

#: The company default confirmed in the brief: Monday to Friday working.
MON_FRI = CalendarSpec(weekly_off_days=frozenset({5, 6}), exceptions={})


class TestDayTypeResolution:
    @pytest.mark.parametrize(
        "day,expected",
        [
            (date(2026, 11, 2), "working"),  # Monday
            (date(2026, 11, 6), "working"),  # Friday
            (date(2026, 11, 7), "weekly_off"),  # Saturday
            (date(2026, 11, 8), "weekly_off"),  # Sunday
        ],
    )
    def test_weekly_off_pattern(self, day: date, expected: str) -> None:
        assert resolve_day_type(day, MON_FRI) == expected

    def test_holiday_beats_a_working_weekday(self) -> None:
        spec = CalendarSpec(
            weekly_off_days=frozenset({5, 6}),
            exceptions={},
            holiday_dates=frozenset({date(2026, 12, 25)}),
        )
        assert resolve_day_type(date(2026, 12, 25), spec) == "holiday"

    def test_non_working_exception_reports_holiday(self) -> None:
        """A declared closure is a holiday, not a weekly off.

        The distinction matters downstream: comp-off eligibility and leave
        deduction treat the two differently.
        """
        spec = CalendarSpec(
            weekly_off_days=frozenset({5, 6}),
            exceptions={date(2026, 11, 4): "non_working"},
        )
        assert resolve_day_type(date(2026, 11, 4), spec) == "holiday"

    def test_working_exception_beats_the_weekend(self) -> None:
        """The "working Saturday to make up for it" case."""
        spec = CalendarSpec(
            weekly_off_days=frozenset({5, 6}),
            exceptions={date(2026, 11, 7): "working"},
        )
        assert resolve_day_type(date(2026, 11, 7), spec) == "working"

    def test_working_exception_beats_a_holiday(self) -> None:
        """Highest precedence, or it could not express that intent at all."""
        spec = CalendarSpec(
            weekly_off_days=frozenset({5, 6}),
            exceptions={date(2026, 12, 25): "working"},
            holiday_dates=frozenset({date(2026, 12, 25)}),
        )
        assert resolve_day_type(date(2026, 12, 25), spec) == "working"

    def test_six_day_week(self) -> None:
        spec = CalendarSpec(weekly_off_days=frozenset({6}), exceptions={})
        assert resolve_day_type(date(2026, 11, 7), spec) == "working"
        assert resolve_day_type(date(2026, 11, 8), spec) == "weekly_off"

    def test_no_weekly_offs_at_all(self) -> None:
        spec = CalendarSpec(weekly_off_days=frozenset(), exceptions={})
        assert all(
            resolve_day_type(day, spec) == "working"
            for day in date_range(date(2026, 11, 2), date(2026, 11, 8))
        )


class TestWorkingDaysBetween:
    def test_counts_a_full_week(self) -> None:
        days = working_days_between(date(2026, 11, 2), date(2026, 11, 8), MON_FRI)
        assert len(days) == 5
        assert days[0] == date(2026, 11, 2)
        assert days[-1] == date(2026, 11, 6)

    def test_november_2026_has_21_working_days(self) -> None:
        days = working_days_between(date(2026, 11, 1), date(2026, 11, 30), MON_FRI)
        assert len(days) == 21

    def test_holidays_are_excluded(self) -> None:
        spec = CalendarSpec(
            weekly_off_days=frozenset({5, 6}),
            exceptions={},
            holiday_dates=frozenset({date(2026, 11, 3), date(2026, 11, 4)}),
        )
        assert len(working_days_between(date(2026, 11, 2), date(2026, 11, 6), spec)) == 3

    def test_inverted_range_is_empty(self) -> None:
        assert working_days_between(date(2026, 11, 8), date(2026, 11, 2), MON_FRI) == []

    def test_single_day_range(self) -> None:
        assert working_days_between(date(2026, 11, 3), date(2026, 11, 3), MON_FRI) == [
            date(2026, 11, 3)
        ]


class TestAssignmentResolution:
    def test_no_assignments(self) -> None:
        assert resolve_assignment([], date(2026, 11, 3)) is None

    def test_open_ended_assignment_covers_later_dates(self) -> None:
        window = AssignmentWindow(1, 10, date(2026, 1, 1), None)
        assert resolve_assignment([window], date(2026, 11, 3)) is window

    def test_date_before_the_assignment_is_uncovered(self) -> None:
        window = AssignmentWindow(1, 10, date(2026, 6, 1), None)
        assert resolve_assignment([window], date(2026, 1, 1)) is None

    def test_closed_assignment_boundaries_are_inclusive(self) -> None:
        window = AssignmentWindow(1, 10, date(2026, 6, 1), date(2026, 6, 30))
        assert resolve_assignment([window], date(2026, 6, 1)) is window
        assert resolve_assignment([window], date(2026, 6, 30)) is window
        assert resolve_assignment([window], date(2026, 7, 1)) is None

    def test_picks_the_right_period_from_a_history(self) -> None:
        """Effective dating is what keeps June's figures explicable in July."""
        june = AssignmentWindow(1, 10, date(2026, 6, 1), date(2026, 6, 30))
        july = AssignmentWindow(2, 20, date(2026, 7, 1), None)
        assignments = [june, july]
        resolved_june = resolve_assignment(assignments, date(2026, 6, 15))
        resolved_july = resolve_assignment(assignments, date(2026, 7, 15))
        assert resolved_june is not None and resolved_june.shift_id == 10
        assert resolved_july is not None and resolved_july.shift_id == 20

    def test_overlap_resolves_to_the_most_recent_decision(self) -> None:
        """The database prevents overlaps, but legacy data may contain them.

        Deterministic beats arbitrary: the later ``effective_from`` wins, so the
        same input always produces the same answer regardless of row order.
        """
        older = AssignmentWindow(1, 10, date(2026, 1, 1), None)
        newer = AssignmentWindow(2, 20, date(2026, 6, 1), None)
        for ordering in ([older, newer], [newer, older]):
            resolved = resolve_assignment(ordering, date(2026, 11, 3))
            assert resolved is not None and resolved.shift_id == 20

    def test_same_start_date_breaks_on_the_higher_id(self) -> None:
        first = AssignmentWindow(1, 10, date(2026, 6, 1), None)
        second = AssignmentWindow(2, 20, date(2026, 6, 1), None)
        resolved = resolve_assignment([first, second], date(2026, 6, 15))
        assert resolved is not None and resolved.shift_id == 20


class TestOverlapDetection:
    @pytest.mark.parametrize(
        "a_from,a_to,b_from,b_to,expected",
        [
            # Identical closed ranges.
            ("2026-01-01", "2026-06-30", "2026-01-01", "2026-06-30", True),
            # Adjacent, not overlapping - this is the legitimate handover.
            ("2026-01-01", "2026-06-30", "2026-07-01", "2026-12-31", False),
            # One day of overlap.
            ("2026-01-01", "2026-07-01", "2026-07-01", "2026-12-31", True),
            # Two open-ended ranges always overlap.
            ("2026-01-01", None, "2026-06-01", None, True),
            # An open-ended range swallows every later closed one.
            ("2026-01-01", None, "2027-01-01", "2027-06-30", True),
            # A closed range before an open-ended one does not.
            ("2026-01-01", "2026-06-30", "2026-07-01", None, False),
            # Fully contained.
            ("2026-01-01", "2026-12-31", "2026-06-01", "2026-06-30", True),
        ],
    )
    def test_overlaps(
        self,
        a_from: str,
        a_to: str | None,
        b_from: str,
        b_to: str | None,
        expected: bool,
    ) -> None:
        assert (
            overlaps(
                date.fromisoformat(a_from),
                date.fromisoformat(a_to) if a_to else None,
                date.fromisoformat(b_from),
                date.fromisoformat(b_to) if b_to else None,
            )
            is expected
        )

    def test_is_symmetric(self) -> None:
        a = (date(2026, 1, 1), date(2026, 6, 30))
        b = (date(2026, 6, 1), None)
        assert overlaps(*a, *b) == overlaps(*b, *a)


class TestDateRange:
    def test_inclusive_both_ends(self) -> None:
        days = date_range(date(2026, 11, 1), date(2026, 11, 3))
        assert days == [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]

    def test_single_day(self) -> None:
        assert date_range(date(2026, 11, 1), date(2026, 11, 1)) == [date(2026, 11, 1)]

    def test_inverted_is_empty(self) -> None:
        assert date_range(date(2026, 11, 3), date(2026, 11, 1)) == []

    def test_spans_a_month_boundary(self) -> None:
        assert len(date_range(date(2026, 11, 28), date(2026, 12, 2))) == 5

    def test_spans_a_leap_day(self) -> None:
        days = date_range(date(2028, 2, 27), date(2028, 3, 1))
        assert date(2028, 2, 29) in days
        assert len(days) == 4
