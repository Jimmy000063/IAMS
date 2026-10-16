"""Tests for the pure comp-off rules.

Two things matter most here. **What a day's work earns** - because an employee
who worked a Sunday will check it. And **which credits a request spends** -
because each credit expires on its own date, and spending the wrong one means
losing a day that could have been used.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.compoff.policy import (
    AllocationPlan,
    CompoffRuleViolation,
    CreditLot,
    CreditRules,
    compute_balance,
    credit_key,
    evaluate_day,
    expand_off_days,
    plan_allocation,
)

D = Decimal

SUNDAY = date(2026, 11, 8)
MONDAY = date(2026, 11, 9)

STANDARD = CreditRules(
    qualifying_day_types=frozenset({"weekly_off", "holiday"}),
    min_minutes_full_credit=480,
    min_minutes_half_credit=240,
    expiry_days=90,
    requires_verification=True,
)


class TestCreditRules:
    def test_a_half_threshold_above_the_full_one_is_refused(self) -> None:
        """A day would earn a full credit before it earned a half one."""
        with pytest.raises(CompoffRuleViolation) as error:
            CreditRules(
                qualifying_day_types=frozenset({"weekly_off"}),
                min_minutes_full_credit=240,
                min_minutes_half_credit=480,
                expiry_days=90,
                requires_verification=True,
            )
        assert error.value.field_name == "compoff.min_minutes_half_credit"


class TestEvaluateDay:
    def test_a_full_day_on_a_weekly_off_earns_one_credit(self) -> None:
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="weekly_off", net_minutes=480, rules=STANDARD
        )
        assert outcome.credit_days == D("1.00")
        assert outcome.status == "pending_verification"
        assert outcome.expiry_date == date(2027, 2, 6), "90 days after the earned date"

    def test_a_half_day_earns_half(self) -> None:
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="weekly_off", net_minutes=300, rules=STANDARD
        )
        assert outcome.credit_days == D("0.50")

    def test_below_the_half_threshold_earns_nothing_rather_than_a_fraction(self) -> None:
        """A company wanting fractional credit would set a lower threshold."""
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="weekly_off", net_minutes=200, rules=STANDARD
        )
        assert outcome.earns_nothing
        assert "below the 240-minute half-day threshold" in outcome.reason

    def test_work_on_a_working_day_does_not_qualify_by_default(self) -> None:
        """Overtime on a normal day is overtime, not a day off owed. Conflating
        the two would grant leave for every late evening."""
        outcome = evaluate_day(
            work_date=MONDAY, day_type="working", net_minutes=600, rules=STANDARD
        )
        assert outcome.earns_nothing
        assert "working day does not qualify" in outcome.reason

    def test_a_working_day_qualifies_when_configured_to(self) -> None:
        rules = CreditRules(
            qualifying_day_types=frozenset({"weekly_off", "holiday", "working"}),
            min_minutes_full_credit=480,
            min_minutes_half_credit=240,
            expiry_days=90,
            requires_verification=True,
        )
        outcome = evaluate_day(work_date=MONDAY, day_type="working", net_minutes=600, rules=rules)
        assert outcome.credit_days == D("1.00")

    def test_a_holiday_qualifies(self) -> None:
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="holiday", net_minutes=540, rules=STANDARD
        )
        assert outcome.credit_days == D("1.00")

    def test_no_verification_makes_the_credit_usable_at_once(self) -> None:
        rules = CreditRules(
            qualifying_day_types=frozenset({"weekly_off"}),
            min_minutes_full_credit=480,
            min_minutes_half_credit=240,
            expiry_days=90,
            requires_verification=False,
        )
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="weekly_off", net_minutes=480, rules=rules
        )
        assert outcome.status == "available"
        assert "verification is not required" in outcome.reason

    def test_a_zero_expiry_window_means_the_credit_never_expires(self) -> None:
        rules = CreditRules(
            qualifying_day_types=frozenset({"weekly_off"}),
            min_minutes_full_credit=480,
            min_minutes_half_credit=240,
            expiry_days=0,
            requires_verification=False,
        )
        outcome = evaluate_day(
            work_date=SUNDAY, day_type="weekly_off", net_minutes=480, rules=rules
        )
        assert outcome.expiry_date is None

    def test_expiry_is_measured_from_the_earned_date_not_today(self) -> None:
        """So a credit for March work expires three months after March, however
        late the credit job happens to run."""
        early = evaluate_day(
            work_date=date(2026, 3, 1), day_type="weekly_off", net_minutes=480, rules=STANDARD
        )
        assert early.expiry_date == date(2026, 5, 30)


class TestCreditKey:
    def test_one_key_per_employee_per_day(self) -> None:
        """Mirrors the unique constraint: a day earns credit once."""
        assert credit_key(employee_id=7, earned_date=SUNDAY) == credit_key(
            employee_id=7, earned_date=SUNDAY
        )
        assert credit_key(employee_id=7, earned_date=SUNDAY) != credit_key(
            employee_id=8, earned_date=SUNDAY
        )
        assert credit_key(employee_id=7, earned_date=SUNDAY) != credit_key(
            employee_id=7, earned_date=MONDAY
        )


def _lot(
    credit_id: int,
    earned: date,
    days: str,
    allocated: str = "0.00",
    expiry: date | None = None,
    status: str = "available",
) -> CreditLot:
    return CreditLot(
        credit_id=credit_id,
        earned_date=earned,
        credit_days=D(days),
        allocated_days=D(allocated),
        expiry_date=expiry,
        status=status,
    )


class TestUsability:
    def test_an_unverified_lot_is_not_usable(self) -> None:
        lot = _lot(1, date(2026, 9, 1), "1.00", status="pending_verification")
        assert lot.usable_on(date(2026, 10, 1)) is False

    def test_a_fully_spent_lot_is_not_usable(self) -> None:
        assert (
            _lot(1, date(2026, 9, 1), "1.00", allocated="1.00").usable_on(date(2026, 10, 1))
            is False
        )

    def test_a_lot_cannot_fund_a_day_after_it_expires(self) -> None:
        """The whole point of the expiry date."""
        lot = _lot(1, date(2026, 9, 1), "1.00", expiry=date(2026, 9, 30))
        assert lot.usable_on(date(2026, 9, 30)) is True
        assert lot.usable_on(date(2026, 10, 1)) is False

    def test_a_lot_cannot_be_spent_before_it_was_earned(self) -> None:
        lot = _lot(1, date(2026, 10, 15), "1.00")
        assert lot.usable_on(date(2026, 10, 1)) is False

    def test_a_never_expiring_lot_is_usable_indefinitely(self) -> None:
        assert _lot(1, date(2020, 1, 1), "1.00").usable_on(date(2030, 1, 1)) is True


class TestAllocation:
    def test_the_earliest_expiring_credit_is_spent_first(self) -> None:
        """So an employee never loses a credit they could have used."""
        plan = plan_allocation(
            lots=[
                _lot(1, date(2026, 9, 1), "1.00", expiry=date(2026, 11, 30)),
                _lot(2, date(2026, 8, 1), "1.00", expiry=date(2026, 10, 31)),
            ],
            required_days=D("1.00"),
            on_date=date(2026, 10, 20),
        )
        assert [item.credit_id for item in plan.allocations] == [2]
        assert plan.is_complete

    def test_a_request_spans_several_lots_in_expiry_order(self) -> None:
        plan = plan_allocation(
            lots=[
                _lot(1, date(2026, 9, 1), "1.00", expiry=date(2026, 11, 30)),
                _lot(2, date(2026, 8, 1), "1.00", expiry=date(2026, 10, 31)),
                _lot(3, date(2026, 10, 1), "0.50"),
            ],
            required_days=D("1.50"),
            on_date=date(2026, 10, 20),
        )
        assert [(item.credit_id, item.days) for item in plan.allocations] == [
            (2, D("1.00")),
            (1, D("0.50")),
        ]
        assert plan.allocated_days == D("1.50")

    def test_a_never_expiring_credit_is_spent_last(self) -> None:
        plan = plan_allocation(
            lots=[
                _lot(1, date(2026, 1, 1), "1.00"),
                _lot(2, date(2026, 9, 1), "1.00", expiry=date(2026, 12, 31)),
            ],
            required_days=D("1.00"),
            on_date=date(2026, 10, 20),
        )
        assert [item.credit_id for item in plan.allocations] == [2]

    def test_a_tie_on_expiry_is_broken_by_the_earned_date(self) -> None:
        """Determinism matters: two runs of the same request must allocate the
        same lots, or a re-approval could draw on different credits."""
        lots = [
            _lot(1, date(2026, 9, 10), "1.00", expiry=date(2026, 12, 31)),
            _lot(2, date(2026, 9, 1), "1.00", expiry=date(2026, 12, 31)),
        ]
        first = plan_allocation(lots=lots, required_days=D("1.00"), on_date=date(2026, 10, 1))
        second = plan_allocation(
            lots=list(reversed(lots)), required_days=D("1.00"), on_date=date(2026, 10, 1)
        )
        assert [item.credit_id for item in first.allocations] == [2]
        assert [item.credit_id for item in second.allocations] == [2]

    def test_a_shortfall_is_returned_not_raised(self) -> None:
        """A plan saying "two days short" is more useful than an exception."""
        plan = plan_allocation(
            lots=[_lot(1, date(2026, 9, 1), "1.00")],
            required_days=D("3.00"),
            on_date=date(2026, 10, 1),
        )
        assert plan.allocated_days == D("1.00")
        assert plan.shortfall_days == D("2.00")
        assert not plan.is_complete

    def test_no_usable_lots_gives_a_full_shortfall(self) -> None:
        plan = plan_allocation(
            lots=[_lot(1, date(2026, 9, 1), "1.00", status="pending_verification")],
            required_days=D("1.00"),
            on_date=date(2026, 10, 1),
        )
        assert plan.allocations == ()
        assert plan.shortfall_days == D("1.00")

    def test_a_partly_spent_lot_contributes_only_its_remainder(self) -> None:
        plan = plan_allocation(
            lots=[_lot(1, date(2026, 9, 1), "1.00", allocated="0.50")],
            required_days=D("1.00"),
            on_date=date(2026, 10, 1),
        )
        assert plan.allocations[0].days == D("0.50")
        assert plan.shortfall_days == D("0.50")

    def test_a_zero_request_is_refused(self) -> None:
        with pytest.raises(CompoffRuleViolation):
            plan_allocation(lots=[], required_days=D("0"), on_date=date(2026, 10, 1))

    def test_a_plan_is_a_value_not_a_mutation(self) -> None:
        """Planning must not change the lots; the service applies the result."""
        lots = [_lot(1, date(2026, 9, 1), "1.00")]
        plan_allocation(lots=lots, required_days=D("1.00"), on_date=date(2026, 10, 1))
        assert lots[0].allocated_days == D("0.00")
        assert isinstance(
            plan_allocation(lots=lots, required_days=D("1.00"), on_date=date(2026, 10, 1)),
            AllocationPlan,
        )


class TestBalance:
    def test_verified_and_unverified_credit_are_kept_apart(self) -> None:
        """Credit HR has not confirmed is not spendable, so one total would have
        an employee plan around a day they may not get."""
        balance = compute_balance(
            [
                _lot(1, date(2026, 9, 1), "1.00"),
                _lot(2, date(2026, 9, 8), "1.00", status="pending_verification"),
            ],
            as_of=date(2026, 10, 1),
        )
        assert balance.available_days == D("1.00")
        assert balance.pending_verification_days == D("1.00")

    def test_a_lot_past_its_date_is_counted_as_expired_not_available(self) -> None:
        """So the figure does not lie in the window before the expiry job runs."""
        balance = compute_balance(
            [_lot(1, date(2026, 7, 1), "1.00", expiry=date(2026, 9, 30))],
            as_of=date(2026, 10, 15),
        )
        assert balance.available_days == D("0.00")
        assert balance.expired_days == D("1.00")

    def test_credit_expiring_soon_is_surfaced_with_its_date(self) -> None:
        balance = compute_balance(
            [
                _lot(1, date(2026, 9, 1), "1.00", expiry=date(2026, 10, 20)),
                _lot(2, date(2026, 9, 5), "1.00", expiry=date(2027, 3, 31)),
            ],
            as_of=date(2026, 10, 15),
            expiring_within_days=15,
        )
        assert balance.expiring_soon_days == D("1.00")
        assert balance.expiring_on == date(2026, 10, 20)

    def test_the_lots_own_allocation_is_what_reduces_available(self) -> None:
        """A two-day lot with one day held leaves one available, and the hold is
        reported separately rather than subtracted a second time."""
        balance = compute_balance(
            [_lot(1, date(2026, 9, 1), "2.00", allocated="1.00")],
            as_of=date(2026, 10, 1),
            pending_allocation_days=D("1.00"),
        )
        assert balance.available_days == D("1.00")
        assert balance.committed_days == D("1.00")

    def test_a_held_credit_is_not_deducted_twice(self) -> None:
        """Credits are allocated at submission, so each lot's remainder already
        excludes the hold. Subtracting the pending days again would count the
        same hold twice and show a negative balance."""
        balance = compute_balance(
            [_lot(1, date(2026, 9, 1), "1.00", allocated="1.00")],
            as_of=date(2026, 10, 1),
            pending_allocation_days=D("1.00"),
        )
        assert balance.available_days == D("0.00")
        assert balance.committed_days == D("1.00")

    def test_a_reversed_or_rejected_lot_counts_for_nothing(self) -> None:
        balance = compute_balance(
            [
                _lot(1, date(2026, 9, 1), "1.00", status="reversed"),
                _lot(2, date(2026, 9, 2), "1.00", status="rejected"),
                _lot(3, date(2026, 9, 3), "1.00", status="expired"),
            ],
            as_of=date(2026, 10, 1),
        )
        assert balance.available_days == D("0.00")
        assert balance.pending_verification_days == D("0.00")

    def test_no_lots_is_a_zero_balance_not_an_error(self) -> None:
        balance = compute_balance([], as_of=date(2026, 10, 1))
        assert balance.available_days == D("0.00")


class TestOffDayExpansion:
    def test_only_working_days_are_consumed(self) -> None:
        """Taking comp-off on a holiday would spend a credit for nothing."""
        days, total = expand_off_days(
            start=date(2026, 11, 6),
            end=date(2026, 11, 9),
            day_types={
                date(2026, 11, 7): "weekly_off",
                date(2026, 11, 8): "weekly_off",
            },
            half_day_enabled=True,
        )
        assert total == D("2.00")
        assert [day.off_date for day in days] == [date(2026, 11, 6), date(2026, 11, 9)]

    def test_a_half_day_consumes_half_a_credit(self) -> None:
        days, total = expand_off_days(
            start=MONDAY,
            end=MONDAY,
            day_types={},
            start_portion="first_half",
            half_day_enabled=True,
        )
        assert total == D("0.50")
        assert days[0].day_fraction == D("0.50")

    def test_half_days_are_refused_when_disabled(self) -> None:
        with pytest.raises(CompoffRuleViolation) as error:
            expand_off_days(
                start=MONDAY,
                end=MONDAY,
                day_types={},
                start_portion="first_half",
                half_day_enabled=False,
            )
        assert error.value.field_name == "start_portion"

    def test_a_range_of_only_non_working_days_consumes_nothing(self) -> None:
        _days, total = expand_off_days(
            start=date(2026, 11, 7),
            end=date(2026, 11, 8),
            day_types={
                date(2026, 11, 7): "weekly_off",
                date(2026, 11, 8): "weekly_off",
            },
            half_day_enabled=True,
        )
        assert total == D("0.00")

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(CompoffRuleViolation):
            expand_off_days(start=MONDAY, end=SUNDAY, day_types={}, half_day_enabled=True)
