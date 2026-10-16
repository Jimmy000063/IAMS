"""Tests for the pure leave rules.

These are the figures an employee would argue about: how many days accrued, what
a mid-month joiner gets, how a range spanning a weekend is counted, and what a
balance comes to after thirty movements. All of it is a function over values, so
each case is pinned exactly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.leave.policy import (
    PERIODS_PER_YEAR,
    AccrualInput,
    ApplicationRequest,
    ClosingRules,
    LeaveDay,
    LeaveRuleViolation,
    LedgerMovement,
    OccupiedDay,
    TypeRules,
    accrual_key,
    accrual_periods,
    build_chain,
    carry_forward_in_key,
    carry_forward_out_key,
    check_eligibility,
    close_leave_year,
    compute_accrual,
    compute_balance,
    detect_overlap,
    encashment_key,
    expand_leave_days,
    lapse_key,
    leave_year_for,
    next_stage,
    period_containing,
    policy_snapshot,
    proration_fraction,
    round_days,
    split_paid_unpaid,
)

D = Decimal

# An April-to-March leave year, the configured default.
APRIL = {"start_month": 4, "start_day": 1}

# 2026-03-06 is a Friday; 07 and 08 are the weekend; 09 is the Monday.
FRI = date(2026, 3, 6)
SAT = date(2026, 3, 7)
SUN = date(2026, 3, 8)
MON = date(2026, 3, 9)
WEEKEND = {SAT: "weekly_off", SUN: "weekly_off"}


# ---------------------------------------------------------------- leave year


class TestLeaveYear:
    def test_a_date_before_the_start_belongs_to_the_previous_year(self) -> None:
        """The configurable start is why this is one function, not inline maths.

        With an April start, March 2026 is part of the year labelled 2025.
        Getting it wrong would carry balances forward at the wrong moment.
        """
        year = leave_year_for(date(2026, 3, 15), **APRIL)
        assert year.label == 2025
        assert year.start == date(2025, 4, 1)
        assert year.end == date(2026, 3, 31)

    def test_the_start_date_itself_begins_the_new_year(self) -> None:
        year = leave_year_for(date(2026, 4, 1), **APRIL)
        assert year.label == 2026
        assert year.start == date(2026, 4, 1)
        assert year.end == date(2027, 3, 31)

    def test_a_january_start_matches_the_calendar_year(self) -> None:
        year = leave_year_for(date(2026, 7, 1), start_month=1, start_day=1)
        assert (year.label, year.start, year.end) == (
            2026,
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

    def test_a_leap_year_is_366_days(self) -> None:
        year = leave_year_for(date(2024, 6, 1), start_month=1, start_day=1)
        assert year.total_days == 366

    def test_an_impossible_start_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            leave_year_for(date(2026, 1, 1), start_month=13, start_day=1)
        with pytest.raises(LeaveRuleViolation):
            # Capped at 28 so the start date exists in February too.
            leave_year_for(date(2026, 1, 1), start_month=1, start_day=31)


# ------------------------------------------------------------ accrual periods


class TestAccrualPeriods:
    def test_monthly_periods_are_anchored_to_the_leave_year_not_the_calendar(self) -> None:
        """An April leave year gets 1-30 April, not 1 April to 1 May."""
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        periods = accrual_periods(year, "monthly")

        assert len(periods) == 12
        assert (periods[0].start, periods[0].end) == (date(2026, 4, 1), date(2026, 4, 30))
        assert periods[0].key == "2026-04"
        assert (periods[-1].start, periods[-1].end) == (date(2027, 3, 1), date(2027, 3, 31))

    def test_quarters_follow_the_leave_year(self) -> None:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        quarters = accrual_periods(year, "quarterly")

        assert [q.key for q in quarters] == ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
        assert (quarters[0].start, quarters[0].end) == (date(2026, 4, 1), date(2026, 6, 30))

    def test_annual_is_one_period_covering_the_year(self) -> None:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        periods = accrual_periods(year, "annual")
        assert len(periods) == 1
        assert (periods[0].start, periods[0].end) == (year.start, year.end)

    def test_periods_tile_the_year_with_no_gap_or_overlap(self) -> None:
        """A gap would lose a month's accrual; an overlap would double it."""
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        for frequency in PERIODS_PER_YEAR:
            periods = accrual_periods(year, frequency)
            assert periods[0].start == year.start, frequency
            assert periods[-1].end == year.end, frequency
            total = sum(period.total_days for period in periods)
            assert total == year.total_days, frequency
            for earlier, later in zip(periods, periods[1:], strict=False):
                assert (later.start - earlier.end).days == 1, frequency

    def test_no_accrual_frequency_produces_no_periods(self) -> None:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        assert accrual_periods(year, "none") == []

    def test_an_unknown_frequency_is_refused(self) -> None:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        with pytest.raises(LeaveRuleViolation):
            accrual_periods(year, "fortnightly")

    def test_period_containing_finds_the_right_month(self) -> None:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        periods = accrual_periods(year, "monthly")
        found = period_containing(date(2026, 7, 15), periods)
        assert found is not None and found.key == "2026-07"


# -------------------------------------------------------------------- rounding


class TestRounding:
    @pytest.mark.parametrize(
        ("value", "mode", "expected"),
        [
            ("0.75", "none", "0.75"),
            ("0.75", "nearest_half", "1.0"),
            ("0.74", "nearest_half", "0.5"),
            ("0.75", "nearest_full", "1"),
            ("0.49", "nearest_full", "0"),
            ("0.75", "down_half", "0.5"),
            ("0.75", "up_half", "1.0"),
            ("1.20", "nearest_half", "1.0"),
            ("1.20", "up_half", "1.5"),
            ("1.20", "down_half", "1.0"),
            ("1.25", "nearest_half", "1.5"),
        ],
    )
    def test_modes(self, value: str, mode: str, expected: str) -> None:
        assert round_days(D(value), mode) == D(expected)

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            round_days(D("1"), "banker")


# ------------------------------------------------------------------ proration


class TestProration:
    @staticmethod
    def _april() -> object:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        return accrual_periods(year, "monthly")[0]

    def test_employed_throughout_earns_the_whole_period(self) -> None:
        assert proration_fraction(
            self._april(), employed_from=date(2020, 1, 1), employed_to=None
        ) == D("1")

    def test_a_mid_month_joiner_earns_the_part_they_were_present(self) -> None:
        """Without this, joining on the 28th earns a whole month's credit."""
        # April has 30 days; joining on the 16th leaves 15 days present.
        assert proration_fraction(
            self._april(), employed_from=date(2026, 4, 16), employed_to=None
        ) == D("0.50")

    def test_someone_who_joined_after_the_period_earns_nothing(self) -> None:
        assert proration_fraction(
            self._april(), employed_from=date(2026, 5, 1), employed_to=None
        ) == D("0")

    def test_a_mid_month_leaver_earns_only_the_part_served(self) -> None:
        assert proration_fraction(
            self._april(), employed_from=date(2020, 1, 1), employed_to=date(2026, 4, 15)
        ) == D("0.50")

    def test_accrual_starting_at_confirmation_shortens_the_period(self) -> None:
        assert proration_fraction(
            self._april(),
            employed_from=date(2020, 1, 1),
            employed_to=None,
            accrual_starts_on=date(2026, 4, 16),
        ) == D("0.50")

    def test_completed_months_credits_nothing_for_a_partial_month(self) -> None:
        """A harsher basis some companies use, so it is configurable not assumed."""
        assert proration_fraction(
            self._april(),
            employed_from=date(2026, 4, 16),
            employed_to=None,
            basis="completed_months",
        ) == D("0")
        assert proration_fraction(
            self._april(),
            employed_from=date(2026, 4, 1),
            employed_to=None,
            basis="completed_months",
        ) == D("1")

    def test_a_working_day_basis_needs_both_counts_supplied(self) -> None:
        """This module resolves no calendars, so it refuses rather than guessing."""
        with pytest.raises(LeaveRuleViolation):
            proration_fraction(
                self._april(),
                employed_from=date(2026, 4, 16),
                employed_to=None,
                basis="working_days",
            )

    def test_a_working_day_basis_uses_the_counts_given(self) -> None:
        assert proration_fraction(
            self._april(),
            employed_from=date(2026, 4, 16),
            employed_to=None,
            basis="working_days",
            working_days_in_period=22,
            eligible_working_days=11,
        ) == D("0.50")

    def test_an_unknown_basis_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            proration_fraction(
                self._april(),
                employed_from=None,
                employed_to=None,
                basis="phases_of_the_moon",
            )


# -------------------------------------------------------------------- accrual


class TestAccrual:
    @staticmethod
    def _period(index: int = 0, frequency: str = "monthly") -> object:
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        return accrual_periods(year, frequency)[index]

    def test_a_full_month_of_an_eighteen_day_quota(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"), frequency="monthly", rounding="nearest_half"
            ),
            employed_from=date(2020, 1, 1),
            employed_to=None,
        )
        assert result.days == D("1.5")
        assert result.was_prorated is False

    def test_a_mid_month_joiner_is_prorated_then_rounded_once(self) -> None:
        """18/12 = 1.5, halved to 0.75, rounded once to 1.0 - not 1.5."""
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"), frequency="monthly", rounding="nearest_half"
            ),
            employed_from=date(2026, 4, 16),
            employed_to=None,
        )
        assert result.raw_days == D("0.75")
        assert result.days == D("1.0")
        assert result.was_prorated is True
        assert "prorated" in result.reason

    def test_proration_can_be_switched_off(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"),
                frequency="monthly",
                rounding="none",
                prorate_on_joining=False,
            ),
            employed_from=date(2026, 4, 16),
            employed_to=None,
        )
        assert result.days == D("1.5")
        assert result.was_prorated is False

    def test_someone_not_employed_in_the_period_accrues_nothing(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"), frequency="monthly", rounding="nearest_half"
            ),
            employed_from=date(2026, 9, 1),
            employed_to=None,
        )
        assert result.is_zero
        assert "Not employed" in result.reason

    def test_a_type_that_does_not_accrue_returns_zero_with_an_explanation(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(annual_quota_days=D("0"), frequency="none", rounding="none"),
            employed_from=date(2020, 1, 1),
            employed_to=None,
        )
        assert result.is_zero
        assert "does not accrue" in result.reason

    def test_the_accumulation_ceiling_stops_accrual_rather_than_failing(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"),
                frequency="monthly",
                rounding="nearest_half",
                max_accumulation_days=D("30"),
            ),
            employed_from=date(2020, 1, 1),
            employed_to=None,
            current_balance=D("30"),
        )
        assert result.is_zero
        assert result.was_capped is True
        assert "ceiling" in result.reason

    def test_accrual_is_trimmed_to_fit_the_remaining_room(self) -> None:
        result = compute_accrual(
            period=self._period(),
            rules=AccrualInput(
                annual_quota_days=D("18"),
                frequency="monthly",
                rounding="nearest_half",
                max_accumulation_days=D("30"),
            ),
            employed_from=date(2020, 1, 1),
            employed_to=None,
            current_balance=D("29"),
        )
        assert result.days == D("1.00")
        assert result.was_capped is True

    def test_twelve_monthly_accruals_do_not_exceed_the_annual_quota(self) -> None:
        """The reason rounding is applied per period, not to a running total."""
        year = leave_year_for(date(2026, 6, 1), **APRIL)
        total = D("0")
        for period in accrual_periods(year, "monthly"):
            total += compute_accrual(
                period=period,
                rules=AccrualInput(
                    annual_quota_days=D("18"), frequency="monthly", rounding="nearest_half"
                ),
                employed_from=date(2020, 1, 1),
                employed_to=None,
            ).days
        assert total == D("18.0")

    def test_quarterly_accrual_splits_the_quota_four_ways(self) -> None:
        result = compute_accrual(
            period=self._period(frequency="quarterly"),
            rules=AccrualInput(annual_quota_days=D("12"), frequency="quarterly", rounding="none"),
            employed_from=date(2020, 1, 1),
            employed_to=None,
        )
        assert result.days == D("3.00")


class TestAccrualKey:
    def test_the_same_period_produces_the_same_key(self) -> None:
        """This is what stops a retried job crediting a balance twice."""
        first = accrual_key(employee_id=42, type_code="CL", period_key="2026-04")
        second = accrual_key(employee_id=42, type_code="CL", period_key="2026-04")
        assert first == second

    def test_keys_differ_by_employee_type_and_period(self) -> None:
        base = {"employee_id": 42, "type_code": "CL", "period_key": "2026-04"}
        assert (
            len(
                {
                    accrual_key(**base),
                    accrual_key(**{**base, "employee_id": 43}),
                    accrual_key(**{**base, "type_code": "SL"}),
                    accrual_key(**{**base, "period_key": "2026-05"}),
                }
            )
            == 4
        )


# ------------------------------------------------------------- day expansion


class TestDayExpansion:
    def test_a_weekend_inside_the_range_is_not_deducted_by_default(self) -> None:
        """Friday and Monday off costs two days, not four."""
        result = expand_leave_days(start=FRI, end=MON, day_types=WEEKEND)

        assert result.total_days == D("2.00")
        assert result.span_days == 4
        assert len(result.days) == 4, "all four days are recorded, two of them free"
        assert result.non_working_excluded == (SAT, SUN)

    def test_the_sandwich_rule_charges_the_interior_weekend(self) -> None:
        result = expand_leave_days(start=FRI, end=MON, day_types=WEEKEND, sandwich_rule=True)
        assert result.total_days == D("4.00")
        assert result.non_working_included == (SAT, SUN)
        assert "sandwich" in " ".join(result.notes)

    def test_the_sandwich_rule_never_charges_a_leading_weekend(self) -> None:
        """Applying from a Saturday must not charge for the Saturday."""
        result = expand_leave_days(start=SAT, end=MON, day_types=WEEKEND, sandwich_rule=True)
        assert result.total_days == D("1.00")
        assert result.non_working_included == ()

    def test_the_sandwich_rule_never_charges_a_trailing_weekend(self) -> None:
        result = expand_leave_days(start=FRI, end=SUN, day_types=WEEKEND, sandwich_rule=True)
        assert result.total_days == D("1.00")

    def test_a_calendar_day_type_consumes_every_day_in_the_range(self) -> None:
        """Some statutory types are defined in calendar days, per type not globally."""
        result = expand_leave_days(
            start=FRI, end=MON, day_types=WEEKEND, counts_non_working_days=True
        )
        assert result.total_days == D("4.00")
        assert "calendar days" in " ".join(result.notes)

    def test_a_calendar_day_type_charges_a_leading_weekend_too(self) -> None:
        result = expand_leave_days(
            start=SAT, end=MON, day_types=WEEKEND, counts_non_working_days=True
        )
        assert result.total_days == D("3.00")

    def test_half_day_portions_apply_to_the_ends_only(self) -> None:
        result = expand_leave_days(
            start=FRI,
            end=MON,
            day_types=WEEKEND,
            start_portion="second_half",
            end_portion="first_half",
        )
        assert result.total_days == D("1.00")
        charged = [day for day in result.days if day.counts_against_balance]
        assert [(day.portion, day.day_fraction) for day in charged] == [
            ("second_half", D("0.5")),
            ("first_half", D("0.5")),
        ]

    def test_a_middle_day_is_always_a_full_day(self) -> None:
        week = date(2026, 3, 10)  # Tuesday
        result = expand_leave_days(
            start=week,
            end=date(2026, 3, 12),
            day_types={},
            start_portion="second_half",
            end_portion="first_half",
        )
        middle = next(day for day in result.days if day.leave_date == date(2026, 3, 11))
        assert middle.portion == "full"
        assert result.total_days == D("2.00")

    def test_a_single_day_half_is_half_a_day(self) -> None:
        result = expand_leave_days(start=FRI, end=FRI, day_types={}, start_portion="first_half")
        assert result.total_days == D("0.50")

    def test_a_range_of_only_non_working_days_consumes_nothing(self) -> None:
        """The service refuses this, so the caller must be able to see it."""
        result = expand_leave_days(start=SAT, end=SUN, day_types=WEEKEND)
        assert result.total_days == D("0.00")

    def test_a_holiday_is_treated_like_a_weekly_off(self) -> None:
        result = expand_leave_days(start=FRI, end=MON, day_types={**WEEKEND, MON: "holiday"})
        assert result.total_days == D("1.00")

    def test_a_non_consumed_day_is_not_marked_paid_as_leave(self) -> None:
        result = expand_leave_days(start=FRI, end=MON, day_types=WEEKEND)
        weekend = [day for day in result.days if not day.counts_against_balance]
        assert all(day.is_paid is False for day in weekend)

    def test_an_unpaid_type_marks_every_consumed_day_unpaid(self) -> None:
        result = expand_leave_days(start=FRI, end=MON, day_types=WEEKEND, is_paid=False)
        charged = [day for day in result.days if day.counts_against_balance]
        assert all(day.is_paid is False for day in charged)

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            expand_leave_days(start=MON, end=FRI, day_types={})


# -------------------------------------------------------------------- balance


class TestBalance:
    @staticmethod
    def _movement(days: str, when: date, kind: str = "accrual", **extra) -> LedgerMovement:
        return LedgerMovement(
            txn_type=kind, days=D(days), effective_date=when, leave_year=2026, **extra
        )

    def test_the_balance_is_the_sum_of_the_ledger(self) -> None:
        balance = compute_balance(
            [
                self._movement("12", date(2026, 4, 1), "opening"),
                self._movement("1.5", date(2026, 5, 31)),
                self._movement("-2", date(2026, 6, 10), "deduction"),
            ],
            as_of=date(2026, 7, 1),
        )
        assert balance.ledger_days == D("11.50")
        assert balance.accrued_days == D("13.50")
        assert balance.used_days == D("2.00")

    def test_movements_after_the_as_of_date_are_excluded(self) -> None:
        """What makes "the balance on 31 March" answerable at all."""
        movements = [
            self._movement("12", date(2026, 4, 1), "opening"),
            self._movement("5", date(2026, 9, 1)),
        ]
        assert compute_balance(movements, as_of=date(2026, 5, 1)).ledger_days == D("12.00")
        assert compute_balance(movements, as_of=date(2026, 10, 1)).ledger_days == D("17.00")

    def test_pending_days_reduce_available_but_not_the_balance(self) -> None:
        """Five days with five pending is a balance of five and nothing to spend."""
        balance = compute_balance(
            [self._movement("5", date(2026, 4, 1), "opening")],
            as_of=date(2026, 5, 1),
            pending_days=D("5"),
        )
        assert balance.ledger_days == D("5.00")
        assert balance.pending_days == D("5.00")
        assert balance.available_days == D("0.00")

    def test_a_reversal_returns_the_days(self) -> None:
        balance = compute_balance(
            [
                self._movement("10", date(2026, 4, 1), "opening"),
                self._movement("-3", date(2026, 5, 1), "deduction"),
                self._movement("3", date(2026, 5, 20), "reversal"),
            ],
            as_of=date(2026, 6, 1),
        )
        assert balance.ledger_days == D("10.00")

    def test_a_negative_balance_is_reported_as_such(self) -> None:
        balance = compute_balance(
            [
                self._movement("1", date(2026, 4, 1), "opening"),
                self._movement("-3", date(2026, 5, 1), "deduction"),
            ],
            as_of=date(2026, 6, 1),
        )
        assert balance.ledger_days == D("-2.00")
        assert balance.is_negative is True

    def test_credit_expiring_soon_is_surfaced_with_its_date(self) -> None:
        balance = compute_balance(
            [
                self._movement(
                    "4", date(2026, 4, 1), "carry_forward_in", expires_on=date(2026, 6, 30)
                ),
                self._movement("8", date(2026, 4, 1), "opening"),
            ],
            as_of=date(2026, 6, 15),
            expiring_within_days=30,
        )
        assert balance.expiring_days == D("4.00")
        assert balance.expiring_on == date(2026, 6, 30)

    def test_leave_approved_for_a_future_date_is_committed_not_available(self) -> None:
        """A deduction is dated on the leave's own start, so the as-of total
        cannot see it. Without counting it separately an employee could book the
        same entitlement twice."""
        balance = compute_balance(
            [
                self._movement("10", date(2026, 4, 1), "opening"),
                self._movement("-3", date(2026, 11, 9), "deduction"),
            ],
            as_of=date(2026, 10, 15),
        )
        assert balance.ledger_days == D("10.00")
        assert balance.committed_days == D("3.00")
        assert balance.available_days == D("7.00")

    def test_cancelling_future_leave_releases_the_commitment(self) -> None:
        """The reversal must net off, or the days would be unavailable forever."""
        balance = compute_balance(
            [
                self._movement("10", date(2026, 4, 1), "opening"),
                self._movement("-3", date(2026, 11, 9), "deduction"),
                self._movement("3", date(2026, 11, 9), "reversal"),
            ],
            as_of=date(2026, 10, 15),
        )
        assert balance.committed_days == D("0.00")
        assert balance.available_days == D("10.00")

    def test_a_future_accrual_is_not_spendable_today(self) -> None:
        """It has not happened yet, so it neither raises the balance nor funds
        leave - and it is not a commitment either."""
        balance = compute_balance(
            [
                self._movement("10", date(2026, 4, 1), "opening"),
                self._movement("1", date(2026, 11, 30), "accrual"),
            ],
            as_of=date(2026, 10, 15),
        )
        assert balance.ledger_days == D("10.00")
        assert balance.committed_days == D("0.00")
        assert balance.available_days == D("10.00")

    def test_a_future_accrual_does_not_offset_a_future_commitment(self) -> None:
        balance = compute_balance(
            [
                self._movement("10", date(2026, 4, 1), "opening"),
                self._movement("1", date(2026, 11, 30), "accrual"),
                self._movement("-3", date(2026, 11, 9), "deduction"),
            ],
            as_of=date(2026, 10, 15),
        )
        assert balance.committed_days == D("3.00")
        assert balance.available_days == D("7.00")

    def test_an_empty_ledger_is_a_zero_balance_not_an_error(self) -> None:
        balance = compute_balance([], as_of=date(2026, 6, 1))
        assert balance.ledger_days == D("0.00")
        assert balance.available_days == D("0.00")


class TestPaidUnpaidSplit:
    def test_a_request_within_the_balance_is_fully_paid(self) -> None:
        split = split_paid_unpaid(
            requested_days=D("3"),
            available_days=D("5"),
            type_is_paid=True,
            deducts_balance=True,
            allow_negative=False,
        )
        assert (split.paid_days, split.unpaid_days) == (D("3"), D("0"))

    def test_a_request_beyond_the_balance_is_split_rather_than_refused(self) -> None:
        """Three days in hand and five needed: two unpaid, not a refusal."""
        split = split_paid_unpaid(
            requested_days=D("5"),
            available_days=D("3"),
            type_is_paid=True,
            deducts_balance=True,
            allow_negative=False,
        )
        assert (split.paid_days, split.unpaid_days) == (D("3"), D("2"))
        assert "unpaid" in split.reason

    def test_a_permitted_negative_balance_keeps_every_day_paid(self) -> None:
        split = split_paid_unpaid(
            requested_days=D("5"),
            available_days=D("3"),
            type_is_paid=True,
            deducts_balance=True,
            allow_negative=True,
        )
        assert (split.paid_days, split.unpaid_days) == (D("5"), D("0"))

    def test_an_unpaid_type_is_never_paid(self) -> None:
        split = split_paid_unpaid(
            requested_days=D("2"),
            available_days=D("99"),
            type_is_paid=False,
            deducts_balance=False,
            allow_negative=False,
        )
        assert (split.paid_days, split.unpaid_days) == (D("0"), D("2"))

    def test_a_type_with_no_balance_pays_without_deducting(self) -> None:
        split = split_paid_unpaid(
            requested_days=D("2"),
            available_days=D("0"),
            type_is_paid=True,
            deducts_balance=False,
            allow_negative=False,
        )
        assert (split.paid_days, split.unpaid_days) == (D("2"), D("0"))

    def test_a_zero_balance_makes_the_whole_request_unpaid(self) -> None:
        split = split_paid_unpaid(
            requested_days=D("2"),
            available_days=D("-1"),
            type_is_paid=True,
            deducts_balance=True,
            allow_negative=False,
        )
        assert (split.paid_days, split.unpaid_days) == (D("0"), D("2"))


# ---------------------------------------------------------------- eligibility


BASE_TYPE = TypeRules(code="CL", name="Casual leave")


def _eligibility(**overrides):
    request = overrides.pop(
        "request", ApplicationRequest(start_date=MON, end_date=MON, reason="Personal.")
    )
    defaults = {
        "request": request,
        "rules": overrides.pop("rules", BASE_TYPE),
        "today": date(2026, 3, 2),
        "date_of_joining": date(2025, 1, 1),
        "date_of_exit": None,
        "employee_gender": None,
        "computed_days": D("1"),
        "span_days": 1,
        "half_day_enabled_company_wide": True,
        "backdated_window_days": 30,
    }
    defaults.update(overrides)
    return check_eligibility(**defaults)


class TestEligibility:
    def test_a_straightforward_request_has_no_problems(self) -> None:
        assert _eligibility() == []

    def test_every_problem_is_reported_at_once(self) -> None:
        """So an applicant fixes one form instead of being refused four times."""
        problems = _eligibility(
            rules=TypeRules(
                code="SL",
                name="Sick leave",
                min_service_days=400,
                max_consecutive_days=1,
                requires_reason=True,
            ),
            request=ApplicationRequest(start_date=MON, end_date=date(2026, 3, 13), reason=""),
            # Joined recently, so the service rule bites as well as the span and
            # the missing reason.
            date_of_joining=date(2026, 1, 1),
            span_days=5,
            computed_days=D("5"),
        )
        fields = {problem.field_name for problem in problems}
        assert len(problems) >= 3
        assert {"leave_type_id", "end_date", "reason"} <= fields

    def test_leave_before_joining_is_refused(self) -> None:
        problems = _eligibility(date_of_joining=date(2026, 6, 1))
        assert any("not on the rolls" in problem.message for problem in problems)

    def test_leave_past_a_recorded_exit_is_refused(self) -> None:
        problems = _eligibility(date_of_exit=date(2026, 2, 1))
        assert any("exit date" in problem.message for problem in problems)

    def test_a_minimum_service_requirement_is_enforced(self) -> None:
        problems = _eligibility(
            rules=TypeRules(code="EL", name="Earned leave", min_service_days=365),
            date_of_joining=date(2026, 1, 1),
        )
        assert any("days of service" in problem.message for problem in problems)

    def test_a_gender_restricted_type_refuses_a_mismatch(self) -> None:
        problems = _eligibility(
            rules=TypeRules(code="ML", name="Maternity leave", gender_restriction="female"),
            employee_gender="male",
        )
        assert any("eligibility rules" in problem.message for problem in problems)

    def test_a_gender_restricted_type_refuses_rather_than_guessing_a_blank(self) -> None:
        """Guessing here would either deny an entitlement or grant a wrong one."""
        problems = _eligibility(
            rules=TypeRules(code="ML", name="Maternity leave", gender_restriction="female"),
            employee_gender=None,
        )
        assert any("no gender is recorded" in problem.message for problem in problems)

    def test_an_unrestricted_type_ignores_a_blank_gender(self) -> None:
        assert _eligibility(employee_gender=None) == []

    def test_backdating_beyond_the_window_is_refused(self) -> None:
        problems = _eligibility(
            request=ApplicationRequest(
                start_date=date(2026, 1, 1), end_date=date(2026, 1, 1), reason="x"
            ),
            today=date(2026, 3, 2),
            backdated_window_days=30,
        )
        assert any("days back" in problem.message for problem in problems)

    def test_backdating_within_the_window_is_allowed(self) -> None:
        assert (
            _eligibility(
                request=ApplicationRequest(
                    start_date=date(2026, 2, 25), end_date=date(2026, 2, 25), reason="x"
                ),
                today=date(2026, 3, 2),
                backdated_window_days=30,
            )
            == []
        )

    def test_a_zero_window_refuses_all_backdating(self) -> None:
        problems = _eligibility(
            request=ApplicationRequest(
                start_date=date(2026, 3, 1), end_date=date(2026, 3, 1), reason="x"
            ),
            today=date(2026, 3, 2),
            backdated_window_days=0,
        )
        assert any("not accepted" in problem.message for problem in problems)

    def test_insufficient_notice_is_refused(self) -> None:
        problems = _eligibility(
            rules=TypeRules(code="PL", name="Planned leave", min_notice_days=7),
            request=ApplicationRequest(
                start_date=date(2026, 3, 4), end_date=date(2026, 3, 4), reason="x"
            ),
            today=date(2026, 3, 2),
        )
        assert any("notice" in problem.message for problem in problems)

    def test_applying_too_far_ahead_is_refused(self) -> None:
        problems = _eligibility(
            request=ApplicationRequest(
                start_date=date(2028, 1, 1), end_date=date(2028, 1, 1), reason="x"
            ),
            max_future_days=365,
        )
        assert any("ahead" in problem.message for problem in problems)

    def test_a_consecutive_day_limit_is_enforced(self) -> None:
        problems = _eligibility(
            rules=TypeRules(code="CL", name="Casual leave", max_consecutive_days=3),
            span_days=5,
            computed_days=D("5"),
        )
        assert any("consecutive" in problem.message for problem in problems)

    def test_a_half_day_is_refused_when_the_company_disables_it(self) -> None:
        problems = _eligibility(
            request=ApplicationRequest(
                start_date=MON, end_date=MON, start_portion="first_half", reason="x"
            ),
            half_day_enabled_company_wide=False,
            computed_days=D("0.5"),
        )
        assert any("Half-day" in problem.message for problem in problems)

    def test_a_half_day_is_refused_when_the_type_disallows_it(self) -> None:
        problems = _eligibility(
            rules=TypeRules(code="ML", name="Maternity leave", allows_half_day=False),
            request=ApplicationRequest(
                start_date=MON, end_date=MON, start_portion="first_half", reason="x"
            ),
            computed_days=D("0.5"),
        )
        assert any("half day" in problem.message for problem in problems)

    def test_a_missing_reason_is_refused_when_required(self) -> None:
        problems = _eligibility(
            request=ApplicationRequest(start_date=MON, end_date=MON, reason="   ")
        )
        assert any(problem.field_name == "reason" for problem in problems)

    def test_an_attachment_is_required_above_the_threshold(self) -> None:
        rules_with_threshold = TypeRules(
            code="SL", name="Sick leave", attachment_required_above_days=D("2")
        )
        assert any(
            problem.field_name == "attachment_file_id"
            for problem in _eligibility(rules=rules_with_threshold, computed_days=D("3"))
        )
        assert (
            _eligibility(
                rules=rules_with_threshold,
                computed_days=D("3"),
                request=ApplicationRequest(
                    start_date=MON, end_date=MON, reason="x", has_attachment=True
                ),
            )
            == []
        )

    def test_at_or_below_the_threshold_needs_no_attachment(self) -> None:
        assert (
            _eligibility(
                rules=TypeRules(
                    code="SL", name="Sick leave", attachment_required_above_days=D("2")
                ),
                computed_days=D("2"),
            )
            == []
        )

    def test_a_request_consuming_no_days_is_refused(self) -> None:
        problems = _eligibility(computed_days=D("0"))
        assert any("working days" in problem.message for problem in problems)


# ------------------------------------------------------------------- overlap


def _day(when: date, portion: str = "full") -> LeaveDay:
    return LeaveDay(
        leave_date=when,
        portion=portion,  # type: ignore[arg-type]
        day_fraction=D("1") if portion == "full" else D("0.5"),
        day_type="working",
        counts_against_balance=True,
        is_paid=True,
    )


class TestOverlap:
    def test_a_day_already_approved_is_refused(self) -> None:
        problems = detect_overlap(
            proposed=(_day(MON),),
            occupied=[
                OccupiedDay(leave_date=MON, portion="full", application_id=7, status="approved")
            ],
        )
        assert len(problems) == 1
        assert "#7" in problems[0].message

    def test_a_day_held_by_a_pending_request_is_refused(self) -> None:
        problems = detect_overlap(
            proposed=(_day(MON),),
            occupied=[
                OccupiedDay(
                    leave_date=MON, portion="full", application_id=8, status="pending_manager"
                )
            ],
        )
        assert len(problems) == 1

    def test_two_different_halves_of_a_day_are_allowed(self) -> None:
        """So an employee can split a day between two leave types."""
        problems = detect_overlap(
            proposed=(_day(MON, "second_half"),),
            occupied=[
                OccupiedDay(
                    leave_date=MON, portion="first_half", application_id=9, status="approved"
                )
            ],
        )
        assert problems == []

    def test_the_same_half_twice_is_refused(self) -> None:
        problems = detect_overlap(
            proposed=(_day(MON, "first_half"),),
            occupied=[
                OccupiedDay(
                    leave_date=MON, portion="first_half", application_id=9, status="approved"
                )
            ],
        )
        assert len(problems) == 1

    def test_a_half_day_against_a_full_day_is_refused(self) -> None:
        problems = detect_overlap(
            proposed=(_day(MON, "first_half"),),
            occupied=[
                OccupiedDay(leave_date=MON, portion="full", application_id=9, status="approved")
            ],
        )
        assert len(problems) == 1

    def test_a_non_consumed_day_cannot_clash(self) -> None:
        weekend = LeaveDay(
            leave_date=SAT,
            portion="full",
            day_fraction=D("1"),
            day_type="weekly_off",
            counts_against_balance=False,
            is_paid=False,
        )
        problems = detect_overlap(
            proposed=(weekend,),
            occupied=[
                OccupiedDay(leave_date=SAT, portion="full", application_id=9, status="approved")
            ],
        )
        assert problems == []

    def test_free_dates_produce_no_problems(self) -> None:
        assert detect_overlap(proposed=(_day(MON),), occupied=[]) == []

    def test_one_problem_per_day_not_per_clashing_application(self) -> None:
        problems = detect_overlap(
            proposed=(_day(MON),),
            occupied=[
                OccupiedDay(leave_date=MON, portion="full", application_id=1, status="approved"),
                OccupiedDay(leave_date=MON, portion="full", application_id=2, status="approved"),
            ],
        )
        assert len(problems) == 1


# ------------------------------------------------------------ approval chain


class TestApprovalChain:
    def test_the_full_chain_is_manager_then_hr(self) -> None:
        steps = build_chain(["reporting_manager", "hr_admin"], manager_available=True)
        assert [step.stage for step in steps] == ["reporting_manager", "hr_admin"]
        assert [step.status for step in steps] == ["pending_manager", "pending_hr"]

    def test_a_missing_manager_routes_straight_to_hr(self) -> None:
        """Otherwise the request waits in a queue nobody can act on."""
        steps = build_chain(["reporting_manager", "hr_admin"], manager_available=False)
        assert [step.stage for step in steps] == ["hr_admin"]

    def test_next_stage_walks_the_chain_then_ends(self) -> None:
        steps = build_chain(["reporting_manager", "hr_admin"], manager_available=True)
        assert next_stage(steps, None).stage == "reporting_manager"  # type: ignore[union-attr]
        assert next_stage(steps, "reporting_manager").stage == "hr_admin"  # type: ignore[union-attr]
        assert next_stage(steps, "hr_admin") is None

    def test_an_unknown_stage_in_the_configured_chain_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            build_chain(["the_ceo"], manager_available=True)

    def test_a_chain_that_resolves_to_nothing_is_refused(self) -> None:
        """A chain with no stages would make the application undecidable."""
        with pytest.raises(LeaveRuleViolation):
            build_chain(["reporting_manager"], manager_available=False)

    def test_a_stage_outside_the_chain_is_refused(self) -> None:
        steps = build_chain(["hr_admin"], manager_available=False)
        with pytest.raises(LeaveRuleViolation):
            next_stage(steps, "reporting_manager")


# ------------------------------------------------------------------ snapshot


class TestPolicySnapshot:
    def test_the_snapshot_captures_what_governed_the_decision(self) -> None:
        """So "why was this allowed?" stays answerable after reconfiguration."""
        snapshot = policy_snapshot(
            rules=BASE_TYPE,
            annual_quota_days=D("12"),
            accrual_frequency="monthly",
            sandwich_rule=False,
            allow_negative=False,
            backdated_window_days=30,
            half_day_enabled=True,
            approval_chain=["reporting_manager", "hr_admin"],
        )
        assert snapshot["leave_type_code"] == "CL"
        assert snapshot["annual_quota_days"] == "12"
        assert snapshot["approval_chain"] == ["reporting_manager", "hr_admin"]
        assert snapshot["backdated_window_days"] == 30

    def test_quantities_are_stored_as_strings_not_floats(self) -> None:
        """A snapshot is JSONB; a float would not round-trip exactly."""
        snapshot = policy_snapshot(
            rules=TypeRules(code="SL", name="Sick", attachment_required_above_days=D("2.5")),
            annual_quota_days=D("7.5"),
            accrual_frequency="annual",
            sandwich_rule=False,
            allow_negative=False,
            backdated_window_days=30,
            half_day_enabled=True,
            approval_chain=["hr_admin"],
        )
        assert snapshot["annual_quota_days"] == "7.5"
        assert snapshot["attachment_required_above_days"] == "2.5"


# ---------------------------------------------------------------- year end


def _closing(
    rule: str = "carry_forward_then_lapse",
    cap: str | None = "5",
    months: int | None = 12,
    encashment: bool = False,
    encash_max: str | None = None,
) -> ClosingRules:
    return ClosingRules(
        year_end_rule=rule,
        carry_forward_cap_days=D(cap) if cap is not None else None,
        carry_forward_expires_after_months=months,
        encashment_allowed=encashment,
        encashment_max_days=D(encash_max) if encash_max is not None else None,
    )


NEXT_YEAR_START = date(2027, 4, 1)


class TestYearEndClosing:
    def test_the_three_outcomes_always_sum_to_the_closing_balance(self) -> None:
        """A reconciliation has to balance. Any rule that quietly dropped days
        would make it impossible to explain where entitlement went."""
        for rule in (
            "carry_forward_then_lapse",
            "carry_forward_then_encash",
            "lapse_all",
            "carry_forward_all",
        ):
            for balance in ("0", "3", "5", "12", "-2"):
                outcome = close_leave_year(
                    closing_balance=D(balance),
                    rules=_closing(rule=rule, encashment=True, encash_max="3"),
                    next_year_start=NEXT_YEAR_START,
                )
                assert outcome.is_balanced, f"{rule} with {balance} does not balance"

    def test_a_cap_carries_up_to_it_and_lapses_the_rest(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"), rules=_closing(cap="5"), next_year_start=NEXT_YEAR_START
        )
        assert outcome.carried_days == D("5.00")
        assert outcome.lapsed_days == D("7.00")
        assert outcome.encashed_days == D("0.00")

    def test_no_cap_carries_everything(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"), rules=_closing(cap=None), next_year_start=NEXT_YEAR_START
        )
        assert outcome.carried_days == D("12.00")
        assert outcome.lapsed_days == D("0.00")

    def test_a_zero_cap_carries_nothing(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"), rules=_closing(cap="0"), next_year_start=NEXT_YEAR_START
        )
        assert outcome.carried_days == D("0.00")
        assert outcome.lapsed_days == D("12.00")

    def test_encashment_takes_the_remainder_up_to_its_limit(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"),
            rules=_closing(
                rule="carry_forward_then_encash", cap="5", encashment=True, encash_max="3"
            ),
            next_year_start=NEXT_YEAR_START,
        )
        assert (outcome.carried_days, outcome.encashed_days, outcome.lapsed_days) == (
            D("5.00"),
            D("3.00"),
            D("4.00"),
        )

    def test_unlimited_encashment_takes_the_whole_remainder(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"),
            rules=_closing(
                rule="carry_forward_then_encash", cap="5", encashment=True, encash_max=None
            ),
            next_year_start=NEXT_YEAR_START,
        )
        assert (outcome.carried_days, outcome.encashed_days, outcome.lapsed_days) == (
            D("5.00"),
            D("7.00"),
            D("0.00"),
        )

    def test_encashment_asked_for_but_not_permitted_lapses_instead(self) -> None:
        """The company rule says encash, but this leave type forbids it. Saying so
        is better than silently encashing something that is not encashable."""
        outcome = close_leave_year(
            closing_balance=D("12"),
            rules=_closing(rule="carry_forward_then_encash", cap="5", encashment=False),
            next_year_start=NEXT_YEAR_START,
        )
        assert outcome.encashed_days == D("0.00")
        assert outcome.lapsed_days == D("7.00")
        assert "does not permit it" in outcome.reason

    def test_lapse_all_keeps_nothing(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"),
            rules=_closing(rule="lapse_all"),
            next_year_start=NEXT_YEAR_START,
        )
        assert outcome.lapsed_days == D("12.00")
        assert outcome.carried_expires_on is None

    def test_carry_forward_all_ignores_the_cap(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("12"),
            rules=_closing(rule="carry_forward_all", cap="5"),
            next_year_start=NEXT_YEAR_START,
        )
        assert outcome.carried_days == D("12.00")

    def test_a_negative_balance_is_carried_in_full_and_never_written_off(self) -> None:
        """An employee who overdrew still owes those days; lapsing the debt would
        quietly hand them back."""
        outcome = close_leave_year(
            closing_balance=D("-2"), rules=_closing(), next_year_start=NEXT_YEAR_START
        )
        assert outcome.carried_days == D("-2.00")
        assert outcome.lapsed_days == D("0.00")
        assert outcome.encashed_days == D("0.00")
        assert "negative" in outcome.reason

    def test_a_zero_balance_does_nothing(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("0"), rules=_closing(), next_year_start=NEXT_YEAR_START
        )
        assert outcome.does_nothing
        assert "nothing to close" in outcome.reason.lower()

    def test_carried_days_expire_a_whole_number_of_months_later(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("5"),
            rules=_closing(cap="5", months=6),
            next_year_start=date(2027, 4, 1),
        )
        assert outcome.carried_expires_on == date(2027, 9, 30), "six months, inclusive"

    def test_no_expiry_window_means_carried_days_never_lapse(self) -> None:
        outcome = close_leave_year(
            closing_balance=D("5"),
            rules=_closing(cap="5", months=None),
            next_year_start=NEXT_YEAR_START,
        )
        assert outcome.carried_expires_on is None

    def test_a_month_shift_clamps_to_the_shorter_month(self) -> None:
        """31 January plus one month must not produce 31 February."""
        outcome = close_leave_year(
            closing_balance=D("5"),
            rules=_closing(cap="5", months=1),
            next_year_start=date(2027, 1, 31),
        )
        assert outcome.carried_expires_on == date(2027, 2, 27)

    def test_an_unknown_year_end_rule_is_refused(self) -> None:
        with pytest.raises(LeaveRuleViolation):
            ClosingRules(
                year_end_rule="give_everyone_a_bonus",
                carry_forward_cap_days=None,
                carry_forward_expires_after_months=None,
                encashment_allowed=False,
                encashment_max_days=None,
            )


class TestYearEndKeys:
    def test_each_entry_type_has_its_own_key(self) -> None:
        """So a year-end run posts at most one of each kind per employee."""
        keys = {
            carry_forward_out_key(employee_id=1, type_code="CL", leave_year=2026),
            carry_forward_in_key(employee_id=1, type_code="CL", leave_year=2027),
            lapse_key(employee_id=1, type_code="CL", leave_year=2026),
            encashment_key(employee_id=1, type_code="CL", leave_year=2026),
        }
        assert len(keys) == 4

    def test_the_same_closing_year_produces_the_same_key(self) -> None:
        """This is what makes re-running the year-end job safe."""
        assert lapse_key(employee_id=1, type_code="CL", leave_year=2026) == lapse_key(
            employee_id=1, type_code="CL", leave_year=2026
        )

    def test_keys_differ_by_employee_type_and_year(self) -> None:
        base = {"employee_id": 1, "type_code": "CL", "leave_year": 2026}
        assert (
            len(
                {
                    lapse_key(**base),
                    lapse_key(**{**base, "employee_id": 2}),
                    lapse_key(**{**base, "type_code": "SL"}),
                    lapse_key(**{**base, "leave_year": 2027}),
                }
            )
            == 4
        )
