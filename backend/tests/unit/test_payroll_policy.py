"""The payroll arithmetic, tested exactly.

No database. Every figure here is checked against a hand-computed expectation,
because "approximately right" is worthless in payroll: a rupee that nobody can
explain is a dispute, and a wrong statutory deduction is money taken from
somebody who did not owe it.

**No test here asserts a real statutory rate.** The rates used are invented for
the test and named as such. What is being tested is that the engine applies
whatever rate it is given, refuses to proceed without one, and never falls back
to a default.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.payroll import policy as p

pytestmark = pytest.mark.unit

D = Decimal
APRIL = (date(2026, 4, 1), date(2026, 4, 30))


def payable_input(**overrides) -> p.PayableDaysInput:
    defaults = dict(
        period_start=APRIL[0],
        period_end=APRIL[1],
        calendar_days=30,
        in_service_days=30,
        attendance_days_recorded=30,
        payable_fraction_total=D("30"),
    )
    defaults.update(overrides)
    return p.PayableDaysInput(**defaults)  # type: ignore[arg-type]


class TestMoney:
    def test_rounding_follows_the_configured_mode(self) -> None:
        assert p.money(D("1.005"), mode="ROUND_HALF_UP") == D("1.01")
        assert p.money(D("1.005"), mode="ROUND_DOWN") == D("1.00")
        # Banker's rounding takes 1.005 to 1.00 and 1.015 to 1.02.
        assert p.money(D("1.005"), mode="ROUND_HALF_EVEN") == D("1.00")
        assert p.money(D("1.015"), mode="ROUND_HALF_EVEN") == D("1.02")

    def test_an_unknown_mode_is_refused(self) -> None:
        """Rather than silently picking one, which would move money."""
        with pytest.raises(p.PayrollRuleViolation, match="rounding mode"):
            p.money(D("1.00"), mode="ROUND_SOMEHOW")

    def test_every_amount_is_a_decimal(self) -> None:
        assert isinstance(p.money(1), Decimal)
        assert p.money(D("0.1")) + p.money(D("0.2")) == D("0.30")


class TestPayableDays:
    def test_a_full_month_pays_in_full(self) -> None:
        result = p.compute_payable_days(payable_input(), basis="calendar_days")
        assert result.payable_days == D("30.00")
        assert result.lop_days == D("0.00")
        assert result.earning_ratio == D("1.000000")
        assert result.is_prorated is False

    def test_unpaid_days_reduce_the_ratio(self) -> None:
        result = p.compute_payable_days(payable_input(unpaid_days=D("3")), basis="calendar_days")
        assert result.payable_days == D("27.00")
        assert result.lop_days == D("3.00")
        assert result.earning_ratio == D("0.900000")

    def test_the_basis_changes_what_a_day_costs(self) -> None:
        """The single most consequential setting in payroll, which is why it is
        flagged for confirmation."""
        calendar = p.compute_payable_days(payable_input(unpaid_days=D("1")), basis="calendar_days")
        fixed = p.compute_payable_days(
            payable_input(unpaid_days=D("1")), basis="fixed_days", fixed_days=26
        )
        # One unpaid day out of 30 is a smaller share than out of 26.
        assert calendar.earning_ratio > fixed.earning_ratio
        assert calendar.earning_ratio == D("0.966667")
        # 26 - (1 * 26/30) = 26 - 0.87 = 25.13; 25.13 / 26.
        assert fixed.lop_days == D("0.87")
        assert fixed.payable_days == D("25.13")

    def test_the_working_days_basis_needs_the_calendar(self) -> None:
        """Only the calendar module knows how many working days a month had, so
        guessing is refused."""
        with pytest.raises(p.PayrollRuleViolation, match="working days"):
            p.compute_payable_days(payable_input(), basis="working_days")

        result = p.compute_payable_days(
            payable_input(unpaid_days=D("1")),
            basis="working_days",
            working_days_in_period=22,
        )
        assert result.days_in_period == D("22.00")

    def test_an_unknown_basis_is_refused(self) -> None:
        with pytest.raises(p.PayrollRuleViolation, match="payable-days basis"):
            p.compute_payable_days(payable_input(), basis="lunar_months")

    def test_joining_mid_month_prorates_without_a_separate_step(self) -> None:
        """Somebody in service for half the month earns half of it, and the
        proration comes out of the same arithmetic as loss of pay - so the two
        cannot drift apart."""
        result = p.compute_payable_days(payable_input(in_service_days=15), basis="calendar_days")
        assert result.payable_days == D("15.00")
        assert result.earning_ratio == D("0.500000")
        assert result.is_prorated is True
        assert "In service for 15 of 30 days" in result.notes[0]

    def test_joining_mid_month_with_unpaid_days_combines_both(self) -> None:
        result = p.compute_payable_days(
            payable_input(in_service_days=20, unpaid_days=D("2")), basis="calendar_days"
        )
        assert result.payable_days == D("18.00")
        assert result.reason == "Part-period service and unpaid days"

    def test_payable_days_never_go_negative(self) -> None:
        """More unpaid days than the entitlement is a data problem, not a reason
        to pay a negative salary."""
        result = p.compute_payable_days(
            payable_input(in_service_days=5, unpaid_days=D("10")), basis="calendar_days"
        )
        assert result.payable_days == D("0.00")
        assert any("held at zero" in note for note in result.notes)

    def test_an_empty_period_is_refused(self) -> None:
        with pytest.raises(p.PayrollRuleViolation, match="no days"):
            p.compute_payable_days(payable_input(calendar_days=0), basis="calendar_days")


def structure() -> list[p.ComponentSpec]:
    """A structure of the shape companies commonly use.

    The proportions are this test's own, not a recommendation: what is being
    tested is the arithmetic, and the percentages a company uses are its own
    decision with tax consequences.
    """
    return [
        p.ComponentSpec(
            1,
            "BASIC",
            "Basic",
            "earning",
            "percent_of_gross",
            percentage=D("50"),
            is_pf_wage=True,
            is_esi_wage=True,
            is_pt_wage=True,
            sequence=10,
        ),
        p.ComponentSpec(
            2,
            "HRA",
            "House rent allowance",
            "earning",
            "percent_of_component",
            percentage=D("40"),
            of_component_code="BASIC",
            is_esi_wage=True,
            is_pt_wage=True,
            sequence=20,
        ),
        p.ComponentSpec(
            3,
            "SPL",
            "Special allowance",
            "earning",
            "balance",
            is_esi_wage=True,
            is_pt_wage=True,
            sequence=90,
        ),
    ]


class TestEarnings:
    def test_a_structure_adds_up_to_the_agreed_gross(self) -> None:
        """What the balancing component exists for: percentages that round would
        otherwise leave the gross a few paise short of what was agreed."""
        lines, bases = p.compute_earnings(
            structure(), gross_monthly=D("50000.00"), earning_ratio=D("1")
        )
        assert bases.gross == D("50000.00")
        amounts = {line.line_code: line.amount for line in lines}
        assert amounts["BASIC"] == D("25000.00")
        assert amounts["HRA"] == D("10000.00")
        assert amounts["SPL"] == D("15000.00")

    def test_an_odd_gross_still_foots_exactly(self) -> None:
        lines, bases = p.compute_earnings(
            structure(), gross_monthly=D("37777.77"), earning_ratio=D("1")
        )
        assert bases.gross == D("37777.77")
        assert sum(line.amount for line in lines) == D("37777.77")

    def test_the_wage_bases_are_independent(self) -> None:
        """A component can count towards PF and not ESI. Deriving one base from
        another would be inventing a legal rule."""
        _, bases = p.compute_earnings(
            structure(), gross_monthly=D("50000.00"), earning_ratio=D("1")
        )
        assert bases.pf_wage == D("25000.00")
        assert bases.esi_wage == D("50000.00")
        assert bases.pf_wage != bases.esi_wage

    def test_proration_applies_to_each_component(self) -> None:
        lines, bases = p.compute_earnings(
            structure(), gross_monthly=D("50000.00"), earning_ratio=D("0.500000")
        )
        amounts = {line.line_code: line.amount for line in lines}
        assert amounts["BASIC"] == D("12500.00")
        assert bases.gross == D("25000.00")

    def test_a_component_can_be_exempt_from_proration(self) -> None:
        """A fixed monthly allowance may be paid in full regardless, which is a
        company decision recorded on the component."""
        specs = structure() + [
            p.ComponentSpec(
                4,
                "MOB",
                "Mobile reimbursement",
                "reimbursement",
                "fixed",
                monthly_amount=D("1000.00"),
                prorates_on_payable_days=False,
                is_taxable=False,
                sequence=95,
            )
        ]
        lines, _ = p.compute_earnings(
            specs, gross_monthly=D("50000.00"), earning_ratio=D("0.500000")
        )
        amounts = {line.line_code: line.amount for line in lines}
        assert amounts["MOB"] == D("1000.00")
        assert amounts["BASIC"] == D("12500.00")

    def test_two_balancing_components_are_refused(self) -> None:
        specs = structure() + [
            p.ComponentSpec(5, "OTH", "Other", "earning", "balance", sequence=91)
        ]
        with pytest.raises(p.PayrollRuleViolation, match="only one balancing"):
            p.compute_earnings(specs, gross_monthly=D("50000.00"), earning_ratio=D("1"))

    def test_components_exceeding_the_gross_are_refused(self) -> None:
        """Rather than producing a negative balancing component, which would
        look like a deduction nobody authorised."""
        specs = [
            p.ComponentSpec(
                1, "BASIC", "Basic", "earning", "fixed", monthly_amount=D("60000.00"), sequence=10
            ),
            p.ComponentSpec(2, "SPL", "Special", "earning", "balance", sequence=90),
        ]
        with pytest.raises(p.PayrollRuleViolation, match="exceeds the gross"):
            p.compute_earnings(specs, gross_monthly=D("50000.00"), earning_ratio=D("1"))

    def test_a_percentage_of_a_derived_component_is_refused(self) -> None:
        """Chained percentages make a payslip's arithmetic hard to follow and
        the order of evaluation significant, so they are refused outright."""
        specs = structure() + [
            p.ComponentSpec(
                6,
                "X",
                "Of HRA",
                "earning",
                "percent_of_component",
                percentage=D("10"),
                of_component_code="NOPE",
                sequence=30,
            )
        ]
        with pytest.raises(p.PayrollRuleViolation, match="depends on NOPE"):
            p.compute_earnings(specs, gross_monthly=D("50000.00"), earning_ratio=D("1"))

    def test_a_percentage_with_no_percentage_is_refused(self) -> None:
        specs = [p.ComponentSpec(1, "BASIC", "Basic", "earning", "percent_of_gross", sequence=10)]
        with pytest.raises(p.PayrollRuleViolation, match="no percentage"):
            p.compute_earnings(specs, gross_monthly=D("50000.00"), earning_ratio=D("1"))

    def test_every_line_explains_itself(self) -> None:
        """A figure a payslip cannot explain is a figure an employee cannot
        check."""
        lines, _ = p.compute_earnings(
            structure(), gross_monthly=D("50000.00"), earning_ratio=D("0.900000")
        )
        for line in lines:
            assert line.basis_description
            assert line.full_amount is not None


def rules(domain: str, parameters: dict, *, verified: bool = True) -> p.RuleSet:
    """A rule set with invented parameters, for testing the shape only."""
    return p.RuleSet(
        rule_set_id=1,
        domain=domain,
        version_label="test-only, invented figures",
        is_verified=verified,
        parameters=parameters,
    )


class TestStatutoryShape:
    """What is tested is the *shape*: a rate applied to a wage, a ceiling
    respected, a slab matched. The numbers are this test's own."""

    def test_a_rate_is_applied_to_the_wage(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("20000"),
                esi_wage=D("50000"),
                pt_wage=D("50000"),
                taxable=D("50000"),
            ),
            rule_sets={"epf": rules("epf", {"employee_rate_percent": "10"})},
            eligibility={"epf": True},
            enabled_domains=["epf"],
        )
        assert len(outcome.employee_lines) == 1
        line = outcome.employee_lines[0]
        assert line.amount == D("2000.00")
        assert line.basis_amount == D("20000")
        assert "10% of 20000" in (line.basis_description or "")

    def test_a_ceiling_caps_the_wage_used(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("40000"),
                esi_wage=D("0"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={
                "epf": rules("epf", {"employee_rate_percent": "10", "wage_ceiling": "15000"})
            },
            eligibility={"epf": True},
            enabled_domains=["epf"],
        )
        line = outcome.employee_lines[0]
        assert line.amount == D("1500.00")
        assert "ceiling" in (line.basis_description or "")

    def test_employer_contributions_are_not_employee_deductions(self) -> None:
        """The classic payslip error: showing a company cost as money taken from
        the employee."""
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("20000"),
                esi_wage=D("0"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={
                "epf": rules(
                    "epf",
                    {"employee_rate_percent": "10", "employer_rate_percent": "10"},
                )
            },
            eligibility={"epf": True},
            enabled_domains=["epf"],
        )
        assert len(outcome.employee_lines) == 1
        assert len(outcome.employer_lines) == 1
        assert outcome.employer_lines[0].component_type == "employer_contribution"

        lines = outcome.employee_lines + outcome.employer_lines
        totals = p.total_payslip(lines)
        # The employer share is reported but does not reduce net pay.
        assert totals.total_deductions == D("2000.00")
        assert totals.employer_contributions == D("2000.00")
        assert totals.net_pay == D("-2000.00")

    def test_a_missing_rate_is_an_error_not_a_zero(self) -> None:
        """A zero deduction looks like a finished calculation. This does not."""
        with pytest.raises(p.StatutoryRulesMissing, match="has no"):
            p.compute_statutory(
                bases=p.WageBases(
                    gross=D("50000"),
                    pf_wage=D("20000"),
                    esi_wage=D("50000"),
                    pt_wage=D("50000"),
                    taxable=D("50000"),
                ),
                rule_sets={"pt": rules("pt", {})},
                eligibility={"pt": True},
                enabled_domains=["pt"],
            )

    def test_a_missing_rule_set_is_an_error(self) -> None:
        with pytest.raises(p.StatutoryRulesMissing, match="no rule set is in force"):
            p.compute_statutory(
                bases=p.WageBases(
                    gross=D("1"),
                    pf_wage=D("1"),
                    esi_wage=D("1"),
                    pt_wage=D("1"),
                    taxable=D("1"),
                ),
                rule_sets={},
                eligibility={"epf": True},
                enabled_domains=["epf"],
            )

    def test_an_ineligible_employee_is_skipped_with_a_reason(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("20000"),
                esi_wage=D("50000"),
                pt_wage=D("50000"),
                taxable=D("50000"),
            ),
            rule_sets={"epf": rules("epf", {"employee_rate_percent": "10"})},
            eligibility={"epf": False},
            enabled_domains=["epf"],
        )
        assert outcome.employee_lines == []
        assert any("not eligible" in note for note in outcome.notes)

    def test_an_unverified_rule_set_computes_but_is_reported(self) -> None:
        """So a company can see what a draft would produce, while finalization
        stays blocked."""
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("20000"),
                esi_wage=D("0"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={"epf": rules("epf", {"employee_rate_percent": "10"}, verified=False)},
            eligibility={"epf": True},
            enabled_domains=["epf"],
        )
        assert outcome.employee_lines[0].amount == D("2000.00")
        assert outcome.unverified_domains == ["epf"]

    def test_a_wage_above_the_esi_limit_is_recorded_not_silently_zero(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("90000"),
                pf_wage=D("0"),
                esi_wage=D("90000"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={
                "esi": rules(
                    "esi",
                    {"employee_rate_percent": "1", "eligibility_wage_limit": "21000"},
                )
            },
            eligibility={"esi": True},
            enabled_domains=["esi"],
        )
        assert outcome.employee_lines == []
        assert any("above the configured limit" in note for note in outcome.notes)

    def test_rounding_to_a_multiple_is_honoured(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("1"),
                pf_wage=D("12345"),
                esi_wage=D("0"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={
                "epf": rules("epf", {"employee_rate_percent": "12", "round_to_nearest": "1"})
            },
            eligibility={"epf": True},
            enabled_domains=["epf"],
        )
        # 12% of 12345 is 1481.40, rounded to the nearest whole unit.
        assert outcome.employee_lines[0].amount == D("1481.00")

    def test_gratuity_is_not_a_monthly_deduction(self) -> None:
        outcome = p.compute_statutory(
            bases=p.WageBases(
                gross=D("50000"),
                pf_wage=D("0"),
                esi_wage=D("0"),
                pt_wage=D("0"),
                taxable=D("0"),
            ),
            rule_sets={"gratuity": rules("gratuity", {"days_per_year": "15"})},
            eligibility={"gratuity": True},
            enabled_domains=["gratuity"],
        )
        assert outcome.employee_lines == []
        assert any("settled at exit" in note for note in outcome.notes)


class TestSlabs:
    def test_the_matching_slab_is_charged(self) -> None:
        slabs = [
            {"up_to": 10000, "amount": 0},
            {"up_to": 20000, "amount": 150},
            {"up_to": None, "amount": 200},
        ]
        assert p.compute_slab(amount=D("5000"), slabs=slabs, domain="pt")[0] == D("0")
        assert p.compute_slab(amount=D("15000"), slabs=slabs, domain="pt")[0] == D("150")
        assert p.compute_slab(amount=D("99999"), slabs=slabs, domain="pt")[0] == D("200")

    def test_a_slab_boundary_is_inclusive(self) -> None:
        slabs = [{"up_to": 10000, "amount": 50}, {"up_to": None, "amount": 100}]
        assert p.compute_slab(amount=D("10000"), slabs=slabs, domain="pt")[0] == D("50")
        assert p.compute_slab(amount=D("10000.01"), slabs=slabs, domain="pt")[0] == D("100")

    def test_slabs_out_of_order_still_work(self) -> None:
        """Because a rule set is typed in by a human."""
        slabs = [{"up_to": None, "amount": 200}, {"up_to": 10000, "amount": 0}]
        assert p.compute_slab(amount=D("5000"), slabs=slabs, domain="pt")[0] == D("0")

    def test_an_open_top_slab_is_required(self) -> None:
        """Otherwise the highest earners would be charged nothing - a silent
        under-deduction, which is the worst kind."""
        slabs = [{"up_to": 10000, "amount": 50}]
        with pytest.raises(p.StatutoryRulesMissing, match="open upper bound"):
            p.compute_slab(amount=D("50000"), slabs=slabs, domain="pt")

    def test_a_slab_without_a_charge_is_refused(self) -> None:
        with pytest.raises(p.StatutoryRulesMissing, match="no 'amount'"):
            p.compute_slab(amount=D("1"), slabs=[{"up_to": None}], domain="pt")

    def test_progressive_bands_each_apply_to_their_own_slice(self) -> None:
        bands = [
            {"from": 0, "to": 100000, "rate": 0},
            {"from": 100000, "to": 200000, "rate": 10},
            {"from": 200000, "to": None, "rate": 20},
        ]
        # 0 on the first 100k, 10% of the next 100k, 20% of the remaining 50k.
        tax, description = p.compute_progressive(
            amount=D("250000"), slabs=bands, domain="tds", rounding="ROUND_HALF_UP"
        )
        assert tax == D("20000.00")
        assert "10% of 100000" in description

    def test_an_amount_below_every_band_is_untaxed(self) -> None:
        bands = [{"from": 100000, "to": None, "rate": 10}]
        tax, _ = p.compute_progressive(
            amount=D("50000"), slabs=bands, domain="tds", rounding="ROUND_HALF_UP"
        )
        assert tax == D("0.00")


class TestTotalsAndFlags:
    def test_net_pay_is_earnings_less_deductions_only(self) -> None:
        lines = [
            p.ComputedLine("BASIC", "Basic", "earning", D("30000.00")),
            p.ComputedLine("MOB", "Mobile", "reimbursement", D("1000.00")),
            p.ComputedLine("PF", "Provident fund", "deduction", D("1800.00")),
            p.ComputedLine("PF_ER", "PF employer", "employer_contribution", D("1800.00")),
        ]
        totals = p.total_payslip(lines)
        assert totals.gross_earnings == D("31000.00")
        assert totals.total_deductions == D("1800.00")
        assert totals.employer_contributions == D("1800.00")
        assert totals.net_pay == D("29200.00")

    def test_a_negative_net_is_flagged_not_blocked(self) -> None:
        """A recovery larger than the month's pay is occasionally correct, so it
        must stay possible - but nobody should approve it unseen."""
        totals = p.PayslipTotals(D("1000.00"), D("5000.00"), D("0.00"), D("-4000.00"))
        payable = p.compute_payable_days(payable_input(), basis="calendar_days")
        flags = p.review_flags(totals=totals, payable=payable)
        codes = {flag["code"] for flag in flags}
        assert "negative_net" in codes
        assert "deductions_exceed_earnings" in codes

    def test_a_large_change_from_last_month_is_flagged(self) -> None:
        totals = p.PayslipTotals(D("10000.00"), D("0.00"), D("0.00"), D("10000.00"))
        payable = p.compute_payable_days(payable_input(), basis="calendar_days")
        flags = p.review_flags(totals=totals, payable=payable, previous_net=D("50000.00"))
        assert any(flag["code"] == "large_variance" for flag in flags)

    def test_an_ordinary_payslip_raises_no_flags(self) -> None:
        totals = p.PayslipTotals(D("50000.00"), D("5000.00"), D("2000.00"), D("45000.00"))
        payable = p.compute_payable_days(payable_input(), basis="calendar_days")
        assert p.review_flags(totals=totals, payable=payable, previous_net=D("44000.00")) == []


class TestSettlement:
    def base(self, **overrides) -> p.SettlementInput:
        defaults = dict(
            monthly_gross=D("30000.00"),
            gratuity_wage=D("15000.00"),
            completed_service_years=D("6"),
            encashable_leave_days=D("10"),
            per_day_wage=D("1000.00"),
            notice_shortfall_days=D("0"),
        )
        defaults.update(overrides)
        return p.SettlementInput(**defaults)  # type: ignore[arg-type]

    def test_leave_encashment_and_notice_recovery_net_off(self) -> None:
        result = p.compute_settlement(self.base(notice_shortfall_days=D("4")), gratuity_rules=None)
        assert result.leave_encashment_amount == D("10000.00")
        assert result.notice_recovery_amount == D("4000.00")
        assert result.net_settlement == D("6000.00")

    def test_gratuity_is_not_invented_without_rules(self) -> None:
        """The most important test in this file. A plausible but invented
        gratuity figure would understate or overstate a statutory payment
        somebody is legally owed, and look equally right either way."""
        result = p.compute_settlement(self.base(), gratuity_rules=None)
        assert result.gratuity_amount == D("0.00")
        assert any("no verified rule set" in note for note in result.notes)

    def test_gratuity_uses_whatever_the_rule_set_says(self) -> None:
        result = p.compute_settlement(
            self.base(),
            gratuity_rules=rules(
                "gratuity",
                {"days_per_year": "15", "monthly_divisor": "26", "minimum_service_years": "5"},
            ),
        )
        # 15000 / 26 * 15 * 6, with the test's own invented parameters.
        assert result.gratuity_amount == D("51923.08")
        assert result.gratuity_rule_set_id == 1

    def test_service_below_the_configured_minimum_earns_none(self) -> None:
        result = p.compute_settlement(
            self.base(completed_service_years=D("3")),
            gratuity_rules=rules(
                "gratuity",
                {"days_per_year": "15", "monthly_divisor": "26", "minimum_service_years": "5"},
            ),
        )
        assert result.gratuity_amount == D("0.00")
        assert any("below the configured minimum" in note for note in result.notes)

    def test_a_configured_maximum_caps_it(self) -> None:
        result = p.compute_settlement(
            self.base(completed_service_years=D("30")),
            gratuity_rules=rules(
                "gratuity",
                {"days_per_year": "15", "monthly_divisor": "26", "maximum_amount": "100000"},
            ),
        )
        assert result.gratuity_amount == D("100000.00")
        assert any("capped" in note for note in result.notes)

    def test_an_unverified_gratuity_rule_set_says_so(self) -> None:
        result = p.compute_settlement(
            self.base(),
            gratuity_rules=rules(
                "gratuity",
                {"days_per_year": "15", "monthly_divisor": "26"},
                verified=False,
            ),
        )
        assert result.gratuity_amount > D("0")
        assert any("not verified" in note for note in result.notes)

    def test_a_zero_divisor_is_refused(self) -> None:
        with pytest.raises(p.StatutoryRulesMissing, match="divided by"):
            p.compute_settlement(
                self.base(),
                gratuity_rules=rules("gratuity", {"days_per_year": "15", "monthly_divisor": "0"}),
            )
