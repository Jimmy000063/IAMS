"""The dedupe-key rule, tested without a database.

The question every test here asks is the same one: given the same fact twice,
does the key come out the same? That property is what makes a retried job safe,
and it is a property of pure functions, so it can be pinned down cheaply and
exactly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.notifications import policy

pytestmark = pytest.mark.unit

MONDAY = date(2026, 3, 2)
FRIDAY = date(2026, 3, 6)


class TestKeysAreStable:
    """The same fact must produce the same key, every time, for every caller."""

    def test_a_leave_decision_key_does_not_vary_between_calls(self) -> None:
        def build() -> str:
            return policy.leave_decision(
                application_id=41,
                leave_type_name="Earned leave",
                start_date=MONDAY,
                end_date=FRIDAY,
                total_days=Decimal("5.00"),
                stage="hr_admin",
                approved=True,
                is_final=True,
            ).dedupe_key

        assert build() == build() == "leave:41:hr_admin:approved"

    def test_a_missing_punch_key_is_the_date_and_status(self) -> None:
        """A nightly job re-examining the same day must produce the same key."""
        key = policy.missing_punch(work_date=MONDAY, status="missing_punch").dedupe_key
        assert key == "attendance:2026-03-02:needs_attention:missing_punch"

    def test_an_expiry_warning_key_includes_the_expiry_date(self) -> None:
        """A daily reminder warns once per credit, not once per run."""
        key = policy.compoff_expiring(
            credit_id=9, expires_on=FRIDAY, remaining_days=Decimal("1.00")
        ).dedupe_key
        assert key == "compoff_credit:9:expiring:2026-03-06"

    def test_no_key_carries_a_run_id_or_a_timestamp(self) -> None:
        """The one exception is a password change, where each change is a fact
        in its own right and suppressing the second would hide it."""
        drafts = [
            policy.leave_decision(
                application_id=1,
                leave_type_name="Earned leave",
                start_date=MONDAY,
                end_date=MONDAY,
                total_days=Decimal("1.00"),
                stage="manager",
                approved=True,
                is_final=False,
            ),
            policy.compoff_credited(
                credit_id=2,
                earned_date=MONDAY,
                credit_days=Decimal("1.00"),
                needs_verification=True,
            ),
            policy.missing_punch(work_date=MONDAY, status="incomplete"),
            policy.shift_changed(
                assignment_id=3,
                shift_name="Night",
                effective_from=MONDAY,
                effective_to=None,
            ),
            policy.holiday_calendar_published(
                calendar_id=4, calendar_name="India 2026", year=2026, holiday_count=11
            ),
            policy.payslip_published(payslip_id=5, period_label="March 2026"),
        ]
        for draft in drafts:
            assert "run" not in draft.dedupe_key
            # A key that contained a clock reading would differ between runs.
            assert "T" not in draft.dedupe_key.replace("attendance:", "")


class TestKeysSeparateDistinctFacts:
    """Deduplication must not swallow something genuinely new."""

    def test_two_stages_of_one_application_are_two_facts(self) -> None:
        manager = policy.leave_decision(
            application_id=7,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="manager",
            approved=True,
            is_final=False,
        )
        hr = policy.leave_decision(
            application_id=7,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="hr_admin",
            approved=True,
            is_final=True,
        )
        assert manager.dedupe_key != hr.dedupe_key

    def test_approval_and_rejection_of_one_stage_differ(self) -> None:
        shared = dict(
            application_id=7,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="hr_admin",
            is_final=True,
        )
        approved = policy.leave_decision(**shared, approved=True)
        rejected = policy.leave_decision(**shared, approved=False)
        assert approved.dedupe_key != rejected.dedupe_key

    def test_two_unsettled_dates_are_two_facts(self) -> None:
        first = policy.missing_punch(work_date=MONDAY, status="missing_punch")
        second = policy.missing_punch(work_date=FRIDAY, status="missing_punch")
        assert first.dedupe_key != second.dedupe_key

    def test_a_changed_status_on_one_date_is_reported(self) -> None:
        """An incomplete day that later reads as a missing punch has changed in
        a way the employee needs to act on, so it is not suppressed."""
        incomplete = policy.missing_punch(work_date=MONDAY, status="incomplete")
        missing = policy.missing_punch(work_date=MONDAY, status="missing_punch")
        assert incomplete.dedupe_key != missing.dedupe_key

    def test_cancellation_is_not_the_approval_it_cancels(self) -> None:
        approved = policy.leave_decision(
            application_id=12,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="final",
            approved=True,
            is_final=True,
        )
        cancelled = policy.leave_cancelled(
            application_id=12,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            days_returned=Decimal("1.00"),
            by_whom="you",
        )
        assert approved.dedupe_key != cancelled.dedupe_key


class TestWhatTheySay:
    def test_an_unsettled_day_is_not_called_an_absence(self) -> None:
        """The brief forbids treating a missing punch as absence, and the
        wording has to match the behaviour."""
        draft = policy.missing_punch(work_date=MONDAY, status="missing_punch")
        assert "absent" not in draft.body.lower() or "not been treated as an absence" in draft.body
        assert "correction" in draft.body

    def test_part_unpaid_leave_says_so(self) -> None:
        """It changes somebody's pay, so it is stated rather than implied."""
        draft = policy.leave_decision(
            application_id=3,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=FRIDAY,
            total_days=Decimal("5.00"),
            stage="final",
            approved=True,
            is_final=True,
            unpaid_days=Decimal("2.00"),
        )
        assert "2 day(s) of it will be unpaid" in draft.body
        assert draft.priority == "important"

    def test_day_counts_read_as_days_not_money(self) -> None:
        draft = policy.leave_decision(
            application_id=3,
            leave_type_name="Casual leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("0.50"),
            stage="final",
            approved=True,
            is_final=True,
        )
        assert "0.5 day(s)" in draft.body
        assert "0.50" not in draft.body

    def test_a_payslip_notice_carries_no_figures(self) -> None:
        """A notification is not a secure channel for pay."""
        draft = policy.payslip_published(payslip_id=8, period_label="March 2026")
        assert not any(character.isdigit() for character in draft.body)
        assert draft.link_path == "/payslips"

    def test_a_single_day_span_is_not_printed_twice(self) -> None:
        draft = policy.compoff_decision(
            application_id=4,
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="final",
            approved=True,
            is_final=True,
        )
        assert draft.title.count("2026-03-02") == 1

    def test_a_rejection_carries_the_reason_given(self) -> None:
        draft = policy.leave_decision(
            application_id=5,
            leave_type_name="Earned leave",
            start_date=MONDAY,
            end_date=MONDAY,
            total_days=Decimal("1.00"),
            stage="manager",
            approved=False,
            is_final=True,
            comment="Release is that week.",
        )
        assert "Release is that week." in draft.body

    def test_pending_verification_is_said_plainly(self) -> None:
        """Credit awaiting verification is not something to plan around."""
        draft = policy.compoff_credited(
            credit_id=6,
            earned_date=MONDAY,
            credit_days=Decimal("1.00"),
            needs_verification=True,
        )
        assert "verified" in draft.body
        available = policy.compoff_credited(
            credit_id=6,
            earned_date=MONDAY,
            credit_days=Decimal("1.00"),
            needs_verification=False,
        )
        assert "available" in available.body

    def test_every_link_is_an_application_path(self) -> None:
        """Not a URL: the host differs per deployment, and a stored absolute
        link would send somebody to the wrong environment."""
        drafts = [
            policy.leave_decision(
                application_id=1,
                leave_type_name="Earned leave",
                start_date=MONDAY,
                end_date=MONDAY,
                total_days=Decimal("1.00"),
                stage="final",
                approved=True,
                is_final=True,
            ),
            policy.regularization_decision(
                request_id=2, work_date=MONDAY, approved=True, stage="hr"
            ),
            policy.holiday_calendar_published(
                calendar_id=3, calendar_name="India 2026", year=2026, holiday_count=1
            ),
        ]
        for draft in drafts:
            assert draft.link_path.startswith("/")
            assert "://" not in draft.link_path
