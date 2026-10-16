"""Pure employee business-rule tests. No database."""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.employees import policy
from app.modules.employees.policy import LifecycleDates, RuleError

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 14)


class TestEmployeeCode:
    @pytest.mark.parametrize(
        "raw,expected",
        [("itc0001", "ITC0001"), ("  ITC0002  ", "ITC0002"), ("a.b-c_1", "A.B-C_1")],
    )
    def test_normalisation(self, raw: str, expected: str) -> None:
        assert policy.validate_employee_code(raw) == expected

    @pytest.mark.parametrize("raw", ["", "x", "-abc", ".abc", "has space", "a" * 31, "a@b"])
    def test_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(RuleError):
            policy.validate_employee_code(raw)


class TestIdentifierFormats:
    @pytest.mark.parametrize("value", ["SBIN0001234", "hdfc0000123"])
    def test_accepts_valid_ifsc(self, value: str) -> None:
        assert policy.validate_ifsc(value) == value.upper()

    @pytest.mark.parametrize("value", ["SBIN1001234", "SBI0001234", "SBIN000123", "x"])
    def test_rejects_invalid_ifsc(self, value: str) -> None:
        with pytest.raises(RuleError):
            policy.validate_ifsc(value)

    def test_accepts_valid_pan(self) -> None:
        assert policy.validate_pan("abcde1234f") == "ABCDE1234F"

    @pytest.mark.parametrize("value", ["ABCD1234F", "ABCDE12345", "ABCDE1234", "1BCDE1234F"])
    def test_rejects_invalid_pan(self, value: str) -> None:
        with pytest.raises(RuleError):
            policy.validate_pan(value)

    def test_accepts_12_digit_uan(self) -> None:
        assert policy.validate_uan("100123456789") == "100123456789"

    @pytest.mark.parametrize("value", ["12345", "1001234567890", "10012345678a"])
    def test_rejects_invalid_uan(self, value: str) -> None:
        with pytest.raises(RuleError):
            policy.validate_uan(value)

    def test_accepts_four_aadhaar_digits(self) -> None:
        assert policy.validate_aadhaar_last4("6789") == "6789"

    @pytest.mark.parametrize("value", ["123456789012", "678", "67890", "abcd"])
    def test_rejects_anything_but_four_digits(self, value: str) -> None:
        """A full Aadhaar number must be rejected, never silently truncated.

        Truncating would mean the user believed they stored a full number while
        the system kept a fragment - and the full number would have transited
        the API, which it must not.
        """
        with pytest.raises(RuleError):
            policy.validate_aadhaar_last4(value)


class TestLifecycleDates:
    def test_accepts_a_sensible_sequence(self) -> None:
        policy.validate_lifecycle(
            LifecycleDates(
                date_of_joining=date(2026, 4, 1),
                probation_end_date=date(2026, 10, 1),
                date_of_confirmation=date(2026, 10, 2),
                last_working_day=date(2027, 3, 31),
                date_of_exit=date(2027, 3, 31),
            ),
            today=TODAY,
        )

    def test_allows_a_future_joining_date(self) -> None:
        """Pre-boarding is normal: HR creates the record before the start date."""
        policy.validate_lifecycle(LifecycleDates(date_of_joining=date(2026, 12, 1)), today=TODAY)

    def test_rejects_an_absurdly_future_joining_date(self) -> None:
        with pytest.raises(RuleError):
            policy.validate_lifecycle(LifecycleDates(date_of_joining=date(2031, 1, 1)), today=TODAY)

    @pytest.mark.parametrize(
        "dates",
        [
            LifecycleDates(date_of_joining=date(2026, 4, 1), probation_end_date=date(2026, 3, 1)),
            LifecycleDates(date_of_joining=date(2026, 4, 1), date_of_confirmation=date(2026, 3, 1)),
            LifecycleDates(date_of_joining=date(2026, 4, 1), last_working_day=date(2026, 3, 1)),
            LifecycleDates(date_of_joining=date(2026, 4, 1), date_of_exit=date(2026, 3, 1)),
            LifecycleDates(
                date_of_joining=date(2026, 1, 1),
                last_working_day=date(2026, 6, 30),
                date_of_exit=date(2026, 5, 1),
            ),
        ],
    )
    def test_rejects_inverted_orderings(self, dates: LifecycleDates) -> None:
        with pytest.raises(RuleError):
            policy.validate_lifecycle(dates, today=TODAY)

    def test_exit_requires_a_date_and_type(self) -> None:
        with pytest.raises(RuleError):
            policy.validate_exit(
                employment_status="exited", date_of_exit=None, exit_type="resignation"
            )
        with pytest.raises(RuleError):
            policy.validate_exit(
                employment_status="exited", date_of_exit=date(2026, 5, 1), exit_type=None
            )

    def test_exit_date_requires_exited_status(self) -> None:
        """Otherwise payroll would prorate for someone still marked active."""
        with pytest.raises(RuleError):
            policy.validate_exit(
                employment_status="active",
                date_of_exit=date(2026, 5, 1),
                exit_type="resignation",
            )

    def test_active_without_exit_data_is_fine(self) -> None:
        policy.validate_exit(employment_status="active", date_of_exit=None, exit_type=None)


class TestReportingCycles:
    def test_no_manager_is_safe(self) -> None:
        assert (
            policy.detect_reporting_cycle(employee_id=1, proposed_manager_id=None, manager_of={})
            is None
        )

    def test_self_assignment_is_a_cycle(self) -> None:
        assert policy.detect_reporting_cycle(
            employee_id=1, proposed_manager_id=1, manager_of={}
        ) == [1, 1]

    def test_simple_chain_is_safe(self) -> None:
        # 3 -> 2 -> (new) 1 is a tree, not a cycle.
        assert (
            policy.detect_reporting_cycle(employee_id=3, proposed_manager_id=2, manager_of={2: 1})
            is None
        )

    def test_two_step_cycle_is_detected(self) -> None:
        """1 manages 2; making 2 manage 1 closes the loop."""
        cycle = policy.detect_reporting_cycle(
            employee_id=1, proposed_manager_id=2, manager_of={2: 1}
        )
        assert cycle is not None
        assert cycle[0] == cycle[-1] == 1

    def test_deep_cycle_is_detected(self) -> None:
        # 5 -> 4 -> 3 -> 2 -> 1; assigning 5 as 1's manager closes it.
        manager_of = {5: 4, 4: 3, 3: 2, 2: 1}
        cycle = policy.detect_reporting_cycle(
            employee_id=1, proposed_manager_id=5, manager_of=manager_of
        )
        assert cycle is not None
        assert cycle[-1] == 1

    def test_terminates_on_a_pre_existing_cycle(self) -> None:
        """A corrupt tree must not hang the request."""
        manager_of = {2: 3, 3: 2}
        result = policy.detect_reporting_cycle(
            employee_id=1, proposed_manager_id=2, manager_of=manager_of
        )
        assert result is not None


class TestSubordinates:
    def test_collects_the_whole_subtree(self) -> None:
        reports_of = {1: [2, 3], 2: [4, 5], 5: [6]}
        assert policy.collect_subordinates(1, reports_of) == {2, 3, 4, 5, 6}

    def test_leaf_has_no_reports(self) -> None:
        assert policy.collect_subordinates(4, {1: [2, 3], 2: [4]}) == set()

    def test_terminates_on_a_cycle(self) -> None:
        reports_of = {1: [2], 2: [3], 3: [1]}
        assert policy.collect_subordinates(1, reports_of) == {2, 3}


class TestNextEmployeeCode:
    def test_first_code_when_none_exist(self) -> None:
        assert policy.next_employee_code([], prefix="ITC") == "ITC0001"

    def test_continues_from_the_highest(self) -> None:
        codes = ["ITC0001", "ITC0007", "ITC0003"]
        assert policy.next_employee_code(codes, prefix="ITC") == "ITC0008"

    def test_ignores_other_prefixes_and_shapes(self) -> None:
        codes = ["ITC0002", "CONS0099", "ITC-TEMP", "itc0004"]
        assert policy.next_employee_code(codes, prefix="ITC") == "ITC0005"

    def test_respects_width(self) -> None:
        assert policy.next_employee_code([], prefix="E", width=6) == "E000001"
