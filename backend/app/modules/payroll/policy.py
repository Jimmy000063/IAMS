"""The payroll calculation, as pure functions.

No database, no clock, no settings lookup. Everything the arithmetic needs is
passed in, which is what lets the rules be tested exactly - and payroll
arithmetic is the one part of this system where "probably right" is worthless.

Two rules govern everything here
--------------------------------
**Decimal, never float.** Every amount and every day count is a ``Decimal``.
Rounding happens once, at the end of each computed figure, using the mode the
company configured. A float would make a payslip that does not add up, and the
discrepancy would be a rupee nobody can explain.

**No statutory number is written down in this file.** Not a rate, not a ceiling,
not a slab. The functions below know the *shape* of each computation - that EPF
is a percentage of a wage subject to a ceiling, that professional tax is a slab
table - and take the numbers from a verified rule set. A missing parameter
raises :class:`StatutoryRulesMissing` rather than falling back to a default,
because a plausible-looking wrong deduction is worse than a refusal: the
employee loses money and nobody notices.

Why there is no formula language
--------------------------------
A free-text formula evaluated against payroll data is both a security hole and
unauditable: nobody can say what last March's payslip would have computed. A
component is fixed, a percentage of another component, a percentage of gross or
CTC, the balancing figure, or statutory. A company needing anything else records
a fixed amount per employee, which is at least explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

ZERO = Decimal("0.00")
ONE = Decimal("1")
HUNDRED = Decimal("100")

ROUNDING_MODES: dict[str, str] = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_DOWN": ROUND_DOWN,
}


class PayrollRuleViolation(Exception):
    """The inputs cannot produce a payslip. Carries a human-readable reason."""


class StatutoryRulesMissing(PayrollRuleViolation):
    """A statutory computation was asked for without the numbers to do it.

    Deliberately an error and not a zero. A zero deduction looks like a
    completed calculation; this does not.
    """


def money(value: Decimal | int | str, *, mode: str = "ROUND_HALF_UP") -> Decimal:
    """Round to two places using the configured mode.

    Applied once per computed figure rather than at every intermediate step, so
    a percentage of a percentage does not accumulate rounding error.
    """
    rounding = ROUNDING_MODES.get(mode)
    if rounding is None:
        raise PayrollRuleViolation(
            f"Unknown rounding mode {mode!r}. Configure 'payroll.rounding_mode'."
        )
    return Decimal(value).quantize(Decimal("0.01"), rounding=rounding)


def plain(value: Decimal) -> str:
    """A number as somebody would write it, for a payslip's own prose.

    Rates are stored to six decimal places and wages to two, so without this a
    payslip line reads "10.000000% of 20000.000000". Trailing zeros go, and the
    result never comes back in scientific notation - ``normalize()`` alone turns
    ``10.000000`` into ``1E+1``, which would be worse than what it fixed.
    """
    normalised = value.normalize()
    if normalised.as_tuple().exponent > 0:
        normalised = normalised.quantize(Decimal(1))
    return f"{normalised:f}"


def day_quantity(value: Decimal | int | str) -> Decimal:
    """Day counts to two places, so a half day is exact."""
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Payable days
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PayableDaysInput:
    """What the attendance side of the month amounts to.

    ``payable_fraction_total`` is the sum of
    ``attendance_days.payable_day_fraction`` - the exact decimal the attendance
    engine already stored when it classified each day. Payroll does not
    re-derive it from punches: one engine decides what a day was worth, and
    payroll reads that, so the two cannot disagree.
    """

    period_start: date
    period_end: date
    calendar_days: int
    #: Days the employee was actually in service this period.
    in_service_days: int
    #: Days the attendance engine produced any classification for.
    attendance_days_recorded: int
    payable_fraction_total: Decimal
    present_days: Decimal = ZERO
    paid_leave_days: Decimal = ZERO
    weekly_off_days: Decimal = ZERO
    holiday_days: Decimal = ZERO
    unpaid_days: Decimal = ZERO
    overtime_minutes: int = 0


@dataclass(frozen=True, slots=True)
class PayableDays:
    days_in_period: Decimal
    payable_days: Decimal
    lop_days: Decimal
    #: The fraction of a full month's salary this person earns.
    earning_ratio: Decimal
    is_prorated: bool
    reason: str
    notes: list[str] = field(default_factory=list)


def compute_payable_days(
    data: PayableDaysInput,
    *,
    basis: str,
    fixed_days: int = 26,
    working_days_in_period: int | None = None,
) -> PayableDays:
    """Payable days, loss of pay, and the ratio salary is multiplied by.

    The **basis** is the denominator, and it changes every loss-of-pay and
    proration amount in the system - which is why the setting that chooses it is
    flagged for confirmation and this function refuses an unknown value instead
    of guessing:

    * ``calendar_days`` - the month's own length, so a day of unpaid leave costs
      1/31 in March and 1/28 in February.
    * ``working_days`` - only the days the company expected work, so a day of
      unpaid leave costs more. Requires the working-day count to be supplied,
      because only the calendar module can say what it is.
    * ``fixed_days`` - a constant, typically 26 or 30, so the cost of a day is
      the same every month.

    Proration on joining or exit falls out of the same arithmetic: somebody in
    service for part of the period has fewer days in their denominator, so no
    separate proration step is needed and the two cannot drift apart.
    """
    if data.calendar_days <= 0:
        raise PayrollRuleViolation("The period has no days in it.")

    if basis == "calendar_days":
        denominator = Decimal(data.calendar_days)
        full_period = Decimal(data.calendar_days)
    elif basis == "working_days":
        if working_days_in_period is None or working_days_in_period <= 0:
            raise PayrollRuleViolation(
                "The working-days basis needs the number of working days in the "
                "period, which comes from the work calendar."
            )
        denominator = Decimal(working_days_in_period)
        full_period = Decimal(working_days_in_period)
    elif basis == "fixed_days":
        if fixed_days <= 0:
            raise PayrollRuleViolation(
                "The fixed-days basis needs a positive number of days. "
                "Configure 'payroll.fixed_payable_days'."
            )
        denominator = Decimal(fixed_days)
        full_period = Decimal(fixed_days)
    else:
        raise PayrollRuleViolation(
            f"Unknown payable-days basis {basis!r}. Configure "
            "'payroll.payable_days_basis' to one of calendar_days, working_days "
            "or fixed_days."
        )

    notes: list[str] = []
    in_service = Decimal(data.in_service_days)
    partial_service = data.in_service_days < data.calendar_days

    # Somebody in service for part of the month earns that part of the month.
    # Scaling the denominator rather than adding a separate proration step means
    # the two can never disagree.
    if partial_service:
        service_ratio = in_service / Decimal(data.calendar_days)
        entitled = (denominator * service_ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        notes.append(
            f"In service for {data.in_service_days} of {data.calendar_days} days, "
            f"so the entitlement is {entitled} of {denominator} days."
        )
    else:
        entitled = denominator

    unpaid = day_quantity(max(data.unpaid_days, ZERO))

    # Unpaid days are deducted in the basis's own units. On a 26-day basis one
    # unpaid calendar day costs 26/31 of a day's pay, not a whole one, which is
    # why the scaling is explicit rather than a subtraction of raw days.
    if basis == "calendar_days":
        unpaid_in_basis = unpaid
    else:
        unpaid_in_basis = (unpaid * denominator / Decimal(data.calendar_days)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if unpaid > ZERO:
            notes.append(
                f"{unpaid} unpaid day(s) scaled to {unpaid_in_basis} day(s) on the "
                f"{basis.replace('_', ' ')} basis."
            )

    payable = day_quantity(max(entitled - unpaid_in_basis, ZERO))
    if entitled - unpaid_in_basis < ZERO:
        notes.append(
            "Unpaid days exceeded the entitlement, so payable days were held at "
            "zero rather than going negative."
        )

    ratio = (payable / full_period) if full_period > ZERO else ZERO
    # Six places: the ratio multiplies every component, so rounding it to two
    # would move real money on a large salary.
    ratio = ratio.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    if partial_service and unpaid > ZERO:
        reason = "Part-period service and unpaid days"
    elif partial_service:
        reason = "Part-period service"
    elif unpaid > ZERO:
        reason = "Unpaid days"
    else:
        reason = "Full period"

    return PayableDays(
        days_in_period=day_quantity(full_period),
        payable_days=payable,
        lop_days=day_quantity(unpaid_in_basis),
        earning_ratio=min(ratio, ONE) if not partial_service else ratio,
        is_prorated=partial_service or unpaid > ZERO,
        reason=reason,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """A salary component as the engine needs it."""

    component_id: int
    code: str
    label: str
    component_type: str
    calculation_type: str
    monthly_amount: Decimal = ZERO
    percentage: Decimal | None = None
    of_component_code: str | None = None
    statutory_domain: str | None = None
    is_pf_wage: bool = False
    is_esi_wage: bool = False
    is_pt_wage: bool = False
    is_taxable: bool = True
    prorates_on_payable_days: bool = True
    sequence: int = 100


@dataclass(frozen=True, slots=True)
class ComputedLine:
    line_code: str
    label: str
    component_type: str
    amount: Decimal
    full_amount: Decimal | None = None
    basis_amount: Decimal | None = None
    basis_description: str | None = None
    component_id: int | None = None
    statutory_rule_set_id: int | None = None
    sequence: int = 100


@dataclass(frozen=True, slots=True)
class WageBases:
    """The wages each statutory computation applies to.

    Separate figures because they are genuinely different: a component can count
    towards PF but not ESI, and the flags that decide it are per component and
    set by the company. Deriving one base from another would be an invented
    legal rule.
    """

    gross: Decimal
    pf_wage: Decimal
    esi_wage: Decimal
    pt_wage: Decimal
    taxable: Decimal


def compute_earnings(
    components: list[ComponentSpec],
    *,
    gross_monthly: Decimal,
    earning_ratio: Decimal,
    rounding: str = "ROUND_HALF_UP",
) -> tuple[list[ComputedLine], WageBases]:
    """Resolve every non-statutory component for one employee and one period.

    Order matters, so it is explicit: fixed and percentage-of-salary components
    first, then percentage-of-component (which needs its base already computed),
    then the balancing component last. A ``balance`` component absorbs whatever
    is left of the gross, which is how a structure built from percentages still
    adds up to the agreed figure exactly - without it, rounding would leave a
    few paise unaccounted for and the payslip would not foot.

    The returned gross is the **sum of the rounded lines**, not the full gross
    multiplied by the ratio. The two can differ by a paisa, and the sum of the
    lines is the right answer: it is what the payslip shows, and a gross that
    did not equal its own lines would be indefensible to the person reading it.
    """
    resolved_full: dict[str, Decimal] = {}
    lines: list[ComputedLine] = []

    earners = [c for c in components if c.component_type in ("earning", "reimbursement")]
    others = [c for c in components if c.component_type == "deduction"]

    # Pass 1 - anything that does not depend on another component.
    for spec in earners:
        if spec.calculation_type == "fixed":
            resolved_full[spec.code] = money(spec.monthly_amount, mode=rounding)
        elif spec.calculation_type == "percent_of_gross":
            if spec.percentage is None:
                raise PayrollRuleViolation(
                    f"Component {spec.code} is a percentage of gross but has no percentage."
                )
            resolved_full[spec.code] = money(
                gross_monthly * spec.percentage / HUNDRED, mode=rounding
            )
        elif spec.calculation_type == "percent_of_ctc":
            if spec.percentage is None:
                raise PayrollRuleViolation(
                    f"Component {spec.code} is a percentage of CTC but has no percentage."
                )
            resolved_full[spec.code] = money(
                gross_monthly * spec.percentage / HUNDRED, mode=rounding
            )
        elif spec.calculation_type == "attendance_based":
            resolved_full[spec.code] = money(spec.monthly_amount, mode=rounding)

    # Pass 2 - percentages of another component.
    for spec in earners:
        if spec.calculation_type != "percent_of_component":
            continue
        if spec.percentage is None or spec.of_component_code is None:
            raise PayrollRuleViolation(
                f"Component {spec.code} is a percentage of another component but "
                "does not say which, or by how much."
            )
        base = resolved_full.get(spec.of_component_code)
        if base is None:
            raise PayrollRuleViolation(
                f"Component {spec.code} depends on {spec.of_component_code}, which "
                "is not part of this employee's structure or is itself derived. "
                "Chained percentages are not supported; use a percentage of gross."
            )
        resolved_full[spec.code] = money(base * spec.percentage / HUNDRED, mode=rounding)

    # Pass 3 - the balancing component takes what is left of the gross.
    balance_specs = [c for c in earners if c.calculation_type == "balance"]
    if len(balance_specs) > 1:
        raise PayrollRuleViolation(
            "A structure can have only one balancing component; two would make "
            "the split between them arbitrary."
        )
    if balance_specs:
        assigned = sum(
            (resolved_full.get(c.code, ZERO) for c in earners if c.calculation_type != "balance"),
            ZERO,
        )
        remainder = money(gross_monthly - assigned, mode=rounding)
        if remainder < ZERO:
            raise PayrollRuleViolation(
                f"The fixed and percentage components total {assigned}, which "
                f"exceeds the gross of {gross_monthly}. The balancing component "
                "cannot be negative."
            )
        resolved_full[balance_specs[0].code] = remainder

    # Now apply the period's earning ratio and build the lines.
    def line_for(spec: ComponentSpec) -> ComputedLine:
        full = resolved_full.get(spec.code, ZERO)
        if spec.prorates_on_payable_days and earning_ratio != ONE:
            amount = money(full * earning_ratio, mode=rounding)
            basis = f"{full} for a full period, at {earning_ratio} of it"
        else:
            amount = full
            basis = (
                "Paid in full regardless of payable days"
                if not spec.prorates_on_payable_days and earning_ratio != ONE
                else "Full period"
            )
        return ComputedLine(
            line_code=spec.code,
            label=spec.label,
            component_type=spec.component_type,
            amount=amount,
            full_amount=full,
            basis_amount=full,
            basis_description=basis,
            component_id=spec.component_id,
            sequence=spec.sequence,
        )

    for spec in sorted(earners, key=lambda c: (c.sequence, c.code)):
        lines.append(line_for(spec))

    # Fixed, non-statutory deductions the company has configured, such as a
    # recurring advance recovery. Statutory ones come from the rule sets.
    for spec in sorted(others, key=lambda c: (c.sequence, c.code)):
        if spec.calculation_type == "statutory":
            continue
        amount = money(spec.monthly_amount, mode=rounding)
        lines.append(
            ComputedLine(
                line_code=spec.code,
                label=spec.label,
                component_type="deduction",
                amount=amount,
                full_amount=amount,
                basis_description="Fixed deduction",
                component_id=spec.component_id,
                sequence=spec.sequence,
            )
        )

    earned = {line.line_code: line.amount for line in lines if line.component_type != "deduction"}
    by_code = {c.code: c for c in earners}

    def base_sum(predicate: str) -> Decimal:
        return money(
            sum(
                (amount for code, amount in earned.items() if getattr(by_code[code], predicate)),
                ZERO,
            ),
            mode=rounding,
        )

    bases = WageBases(
        gross=money(sum(earned.values(), ZERO), mode=rounding),
        pf_wage=base_sum("is_pf_wage"),
        esi_wage=base_sum("is_esi_wage"),
        pt_wage=base_sum("is_pt_wage"),
        taxable=base_sum("is_taxable"),
    )
    return lines, bases


# ---------------------------------------------------------------------------
# Statutory computations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuleSet:
    """A statutory rule set as the engine needs it: numbers plus provenance."""

    rule_set_id: int
    domain: str
    version_label: str
    is_verified: bool
    parameters: dict[str, Any]

    def numeric(self, key: str, *, required: bool = True) -> Decimal | None:
        value = self.parameters.get(key)
        if value is None:
            if required:
                raise StatutoryRulesMissing(
                    f"The {self.domain.upper()} rule set '{self.version_label}' has no "
                    f"'{key}'. Add it to the rule set; payroll will not assume a value."
                )
            return None
        if isinstance(value, list):
            raise StatutoryRulesMissing(
                f"'{key}' in the {self.domain.upper()} rule set is a table, not a number."
            )
        return Decimal(str(value))

    def table(self, key: str) -> list[dict[str, Any]]:
        value = self.parameters.get(key)
        if not isinstance(value, list) or not value:
            raise StatutoryRulesMissing(
                f"The {self.domain.upper()} rule set '{self.version_label}' has no "
                f"'{key}' table. Add the slabs; payroll will not assume them."
            )
        return value


def _rate_of_wage(
    *,
    wage: Decimal,
    rule_set: RuleSet,
    rate_key: str,
    ceiling_key: str = "wage_ceiling",
    floor_key: str = "wage_floor",
    rounding: str,
) -> tuple[Decimal, Decimal, str]:
    """A percentage of a wage, subject to an optional ceiling and floor.

    The shape shared by EPF, EPS, EDLI and ESI. The *shape* is all that is
    hard-coded: the rate, the ceiling and whether a ceiling applies at all come
    from the rule set, and a missing rate is an error rather than a zero.

    Returns the amount, the wage actually used, and a description of how - which
    is what lets a payslip explain itself.
    """
    rate = rule_set.numeric(rate_key)
    assert rate is not None  # numeric() raises when required and missing
    ceiling = rule_set.numeric(ceiling_key, required=False)
    floor = rule_set.numeric(floor_key, required=False)

    applicable = wage
    description = f"{plain(rate)}% of {plain(wage)}"

    if ceiling is not None and applicable > ceiling:
        applicable = ceiling
        description = (
            f"{plain(rate)}% of {plain(ceiling)}, the configured ceiling (wage {plain(wage)})"
        )
    if floor is not None and applicable < floor:
        applicable = floor
        description = f"{plain(rate)}% of {plain(floor)}, the configured floor (wage {plain(wage)})"

    amount = money(applicable * rate / HUNDRED, mode=rounding)

    rounding_to = rule_set.numeric("round_to_nearest", required=False)
    if rounding_to is not None and rounding_to > 0:
        multiples = (amount / rounding_to).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        amount = money(multiples * rounding_to, mode=rounding)
        description += f", rounded to the nearest {plain(rounding_to)}"

    return amount, applicable, description


def compute_slab(
    *, amount: Decimal, slabs: list[dict[str, Any]], domain: str
) -> tuple[Decimal, str]:
    """Find the matching slab and return its charge.

    Used for professional tax and for any other table-driven deduction. Each
    slab is ``{"up_to": <amount or null>, "amount": <charge>}``, inclusive of
    its upper bound and ordered ascending. A wage above every slab falls to the
    last one, which is why the final slab should have a null ``up_to``; if it
    does not, the highest earners would be charged nothing, so that case raises
    instead.
    """
    ordered = sorted(
        slabs,
        key=lambda row: (
            row.get("up_to") is None,
            Decimal(str(row.get("up_to"))) if row.get("up_to") is not None else ZERO,
        ),
    )
    for row in ordered:
        if "amount" not in row:
            raise StatutoryRulesMissing(
                f"A {domain.upper()} slab has no 'amount'. Every slab needs a charge."
            )
        upper = row.get("up_to")
        if upper is None or amount <= Decimal(str(upper)):
            charge = Decimal(str(row["amount"]))
            bound = "and above" if upper is None else f"up to {plain(Decimal(str(upper)))}"
            return charge, f"slab {bound}, on {plain(amount)}"

    raise StatutoryRulesMissing(
        f"No {domain.upper()} slab covers {amount}. The highest slab needs an open "
        "upper bound, or the highest earners would be charged nothing."
    )


def compute_progressive(
    *, amount: Decimal, slabs: list[dict[str, Any]], domain: str, rounding: str
) -> tuple[Decimal, str]:
    """Tax computed slab by slab, each rate applying only to its own band.

    The shape income tax uses. Nothing about rates, bands, exemptions, rebates
    or regimes is decided here: the bands come from the rule set, and this
    function only does the banded arithmetic. It is therefore **not** a complete
    income-tax computation, which is why TDS stays optional and unverified until
    the company's own rules are entered and checked.
    """
    ordered = sorted(
        slabs,
        key=lambda row: Decimal(str(row.get("from", 0))),
    )
    total = ZERO
    parts: list[str] = []
    for row in ordered:
        lower = Decimal(str(row.get("from", 0)))
        upper = row.get("to")
        rate = Decimal(str(row.get("rate", 0)))
        if amount <= lower:
            continue
        top = amount if upper is None else min(amount, Decimal(str(upper)))
        band = top - lower
        if band <= ZERO:
            continue
        part = money(band * rate / HUNDRED, mode=rounding)
        total += part
        parts.append(f"{plain(rate)}% of {plain(band)}")
    if not parts:
        return ZERO, "no band applies"
    return money(total, mode=rounding), " + ".join(parts)


@dataclass(frozen=True, slots=True)
class StatutoryOutcome:
    employee_lines: list[ComputedLine]
    employer_lines: list[ComputedLine]
    unverified_domains: list[str]
    notes: list[str]


def compute_statutory(
    *,
    bases: WageBases,
    rule_sets: dict[str, RuleSet],
    eligibility: dict[str, bool],
    enabled_domains: list[str],
    rounding: str = "ROUND_HALF_UP",
) -> StatutoryOutcome:
    """Every enabled statutory deduction and employer contribution.

    A domain is computed only when the company enabled it *and* the employee is
    eligible *and* a rule set exists for the date. Each of the three is a
    separate decision recorded in a different place, and none is inferred:

    * enabled - ``payroll.enabled_statutory_modules``, a company registration
      question.
    * eligible - ``employee_statutory_eligibility``, per employee and
      effective-dated, because coverage changes.
    * the numbers - a rule set, which must cite a source to be verified.

    An unverified rule set still computes, so a company can see what a draft
    would produce, but the domain is reported in ``unverified_domains`` and
    finalization is blocked while that list is non-empty.
    """
    employee_lines: list[ComputedLine] = []
    employer_lines: list[ComputedLine] = []
    unverified: list[str] = []
    notes: list[str] = []

    for domain in enabled_domains:
        if not eligibility.get(domain, False):
            notes.append(
                f"{domain.upper()}: the employee is not eligible, so nothing was computed."
            )
            continue
        rule_set = rule_sets.get(domain)
        if rule_set is None:
            raise StatutoryRulesMissing(
                f"{domain.upper()} is enabled and the employee is eligible, but no "
                "rule set is in force for this period. Create one and verify it "
                "against its published source."
            )
        if not rule_set.is_verified:
            unverified.append(domain)

        if domain in ("epf", "eps", "edli"):
            _add_provident(
                domain=domain,
                rule_set=rule_set,
                bases=bases,
                rounding=rounding,
                employee_lines=employee_lines,
                employer_lines=employer_lines,
            )
        elif domain == "esi":
            _add_esi(
                rule_set=rule_set,
                bases=bases,
                rounding=rounding,
                employee_lines=employee_lines,
                employer_lines=employer_lines,
                notes=notes,
            )
        elif domain in ("pt", "lwf"):
            charge, description = compute_slab(
                amount=bases.pt_wage if domain == "pt" else bases.gross,
                slabs=rule_set.table("slabs"),
                domain=domain,
            )
            employee_lines.append(
                ComputedLine(
                    line_code=domain.upper(),
                    label="Professional tax" if domain == "pt" else "Labour welfare fund",
                    component_type="deduction",
                    amount=money(charge, mode=rounding),
                    basis_amount=bases.pt_wage if domain == "pt" else bases.gross,
                    basis_description=description,
                    statutory_rule_set_id=rule_set.rule_set_id,
                    sequence=820 if domain == "pt" else 830,
                )
            )
        elif domain == "tds":
            monthly, description = _monthly_tds(
                rule_set=rule_set, taxable_monthly=bases.taxable, rounding=rounding
            )
            employee_lines.append(
                ComputedLine(
                    line_code="TDS",
                    label="Income tax deducted at source",
                    component_type="deduction",
                    amount=monthly,
                    basis_amount=bases.taxable,
                    basis_description=description,
                    statutory_rule_set_id=rule_set.rule_set_id,
                    sequence=840,
                )
            )
        elif domain == "gratuity":
            # Gratuity is a liability accrued over service and paid at exit, not
            # a monthly deduction. It belongs to the settlement, not here.
            notes.append(
                "Gratuity is settled at exit rather than deducted monthly, so it "
                "does not appear on a monthly payslip."
            )

    return StatutoryOutcome(
        employee_lines=employee_lines,
        employer_lines=employer_lines,
        unverified_domains=sorted(set(unverified)),
        notes=notes,
    )


def _add_provident(
    *,
    domain: str,
    rule_set: RuleSet,
    bases: WageBases,
    rounding: str,
    employee_lines: list[ComputedLine],
    employer_lines: list[ComputedLine],
) -> None:
    """Provident-fund family lines, from whatever rates the rule set holds.

    Employee and employer shares are separate parameters because they are
    separate rates in law and only one of them is deducted from pay. A rule set
    may define either or both; what it does not define is not computed.
    """
    labels = {
        "epf": ("Provident fund", "Provident fund (employer)"),
        "eps": ("Pension scheme", "Pension scheme (employer)"),
        "edli": ("Deposit-linked insurance", "Deposit-linked insurance (employer)"),
    }
    employee_label, employer_label = labels[domain]

    if rule_set.numeric("employee_rate_percent", required=False) is not None:
        amount, applicable, description = _rate_of_wage(
            wage=bases.pf_wage,
            rule_set=rule_set,
            rate_key="employee_rate_percent",
            rounding=rounding,
        )
        employee_lines.append(
            ComputedLine(
                line_code=f"{domain.upper()}_EE",
                label=employee_label,
                component_type="deduction",
                amount=amount,
                basis_amount=applicable,
                basis_description=description,
                statutory_rule_set_id=rule_set.rule_set_id,
                sequence=800,
            )
        )

    if rule_set.numeric("employer_rate_percent", required=False) is not None:
        amount, applicable, description = _rate_of_wage(
            wage=bases.pf_wage,
            rule_set=rule_set,
            rate_key="employer_rate_percent",
            rounding=rounding,
        )
        employer_lines.append(
            ComputedLine(
                line_code=f"{domain.upper()}_ER",
                label=employer_label,
                component_type="employer_contribution",
                amount=amount,
                basis_amount=applicable,
                basis_description=description,
                statutory_rule_set_id=rule_set.rule_set_id,
                sequence=900,
            )
        )


def _add_esi(
    *,
    rule_set: RuleSet,
    bases: WageBases,
    rounding: str,
    employee_lines: list[ComputedLine],
    employer_lines: list[ComputedLine],
    notes: list[str],
) -> None:
    """ESI lines, including the exemption threshold if the rule set defines one.

    The threshold is read from the rule set rather than assumed, and when a wage
    is above it nothing is deducted and the reason is recorded - a silent zero
    would be indistinguishable from a bug.
    """
    threshold = rule_set.numeric("eligibility_wage_limit", required=False)
    if threshold is not None and bases.esi_wage > threshold:
        notes.append(
            f"ESI: the wage of {bases.esi_wage} is above the configured limit of "
            f"{threshold}, so no contribution was computed."
        )
        return

    if rule_set.numeric("employee_rate_percent", required=False) is not None:
        amount, applicable, description = _rate_of_wage(
            wage=bases.esi_wage,
            rule_set=rule_set,
            rate_key="employee_rate_percent",
            rounding=rounding,
        )
        employee_lines.append(
            ComputedLine(
                line_code="ESI_EE",
                label="Employees' state insurance",
                component_type="deduction",
                amount=amount,
                basis_amount=applicable,
                basis_description=description,
                statutory_rule_set_id=rule_set.rule_set_id,
                sequence=810,
            )
        )

    if rule_set.numeric("employer_rate_percent", required=False) is not None:
        amount, applicable, description = _rate_of_wage(
            wage=bases.esi_wage,
            rule_set=rule_set,
            rate_key="employer_rate_percent",
            rounding=rounding,
        )
        employer_lines.append(
            ComputedLine(
                line_code="ESI_ER",
                label="Employees' state insurance (employer)",
                component_type="employer_contribution",
                amount=amount,
                basis_amount=applicable,
                basis_description=description,
                statutory_rule_set_id=rule_set.rule_set_id,
                sequence=910,
            )
        )


def _monthly_tds(
    *, rule_set: RuleSet, taxable_monthly: Decimal, rounding: str
) -> tuple[Decimal, str]:
    """One month's share of the year's tax on an annualised income.

    Annualise, apply the bands, divide by the months in the year. This is a
    deliberately simple projection: it has no exemptions, deductions, rebates,
    surcharge, cess, regime choice or previous-employer income, all of which a
    real computation needs. TDS therefore stays an optional module and a company
    enabling it is choosing a projection, which the rule set's own notes should
    say.
    """
    months = rule_set.numeric("months_in_year", required=False) or Decimal("12")
    annual = taxable_monthly * months
    tax, description = compute_progressive(
        amount=annual, slabs=rule_set.table("slabs"), domain="tds", rounding=rounding
    )
    monthly = money(tax / months, mode=rounding)
    return (
        monthly,
        f"{description} on an annualised {plain(annual)}, divided by {plain(months)}",
    )


# ---------------------------------------------------------------------------
# Assembling a payslip
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PayslipTotals:
    gross_earnings: Decimal
    total_deductions: Decimal
    employer_contributions: Decimal
    net_pay: Decimal


def total_payslip(lines: list[ComputedLine], *, rounding: str = "ROUND_HALF_UP") -> PayslipTotals:
    """Add up the lines.

    Employer contributions are **not** subtracted from net pay: they are a
    company cost shown for transparency, and treating them as a deduction is the
    classic payslip error that makes an employee think they were underpaid.
    """
    gross = sum(
        (line.amount for line in lines if line.component_type in ("earning", "reimbursement")),
        ZERO,
    )
    deductions = sum((line.amount for line in lines if line.component_type == "deduction"), ZERO)
    employer = sum(
        (line.amount for line in lines if line.component_type == "employer_contribution"), ZERO
    )
    return PayslipTotals(
        gross_earnings=money(gross, mode=rounding),
        total_deductions=money(deductions, mode=rounding),
        employer_contributions=money(employer, mode=rounding),
        net_pay=money(gross - deductions, mode=rounding),
    )


def review_flags(
    *,
    totals: PayslipTotals,
    payable: PayableDays,
    previous_net: Decimal | None = None,
    variance_percent: Decimal = Decimal("25"),
) -> list[dict[str, str]]:
    """What a human should look at before approving.

    Flags, not blocks. A negative net pay is usually a recovery larger than the
    month's earnings, which is occasionally correct and must stay possible - but
    nobody should approve it without seeing it. Blocking would make an
    exceptional but legitimate payslip impossible to process at all.
    """
    flags: list[dict[str, str]] = []

    if totals.net_pay < ZERO:
        flags.append(
            {
                "code": "negative_net",
                "message": (
                    f"Net pay is {totals.net_pay}. Deductions exceed earnings - "
                    "usually a recovery larger than the month's pay."
                ),
            }
        )
    if totals.gross_earnings == ZERO:
        flags.append(
            {
                "code": "zero_gross",
                "message": "No earnings were computed. Check the salary revision and payable days.",
            }
        )
    if payable.payable_days == ZERO:
        flags.append(
            {
                "code": "no_payable_days",
                "message": "No payable days in this period. Check attendance and leave.",
            }
        )
    if totals.total_deductions > totals.gross_earnings:
        flags.append(
            {
                "code": "deductions_exceed_earnings",
                "message": (
                    f"Deductions of {totals.total_deductions} exceed earnings of "
                    f"{totals.gross_earnings}."
                ),
            }
        )
    if previous_net is not None and previous_net > ZERO:
        change = (totals.net_pay - previous_net) / previous_net * HUNDRED
        if abs(change) >= variance_percent:
            direction = "higher" if change > ZERO else "lower"
            flags.append(
                {
                    "code": "large_variance",
                    "message": (
                        f"Net pay is {abs(change).quantize(Decimal('0.1'))}% {direction} "
                        f"than last period ({previous_net} to {totals.net_pay})."
                    ),
                }
            )
    return flags


# ---------------------------------------------------------------------------
# Full and final settlement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettlementInput:
    monthly_gross: Decimal
    #: The wage gratuity is computed on, which is not the gross: it is normally
    #: basic plus dearness allowance, and which components count is a company
    #: decision recorded on the components themselves.
    gratuity_wage: Decimal
    completed_service_years: Decimal
    encashable_leave_days: Decimal
    per_day_wage: Decimal
    notice_shortfall_days: Decimal
    other_dues: Decimal = ZERO
    other_recoveries: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class Settlement:
    leave_encashment_amount: Decimal
    notice_recovery_amount: Decimal
    gratuity_amount: Decimal
    net_settlement: Decimal
    notes: list[str]
    gratuity_rule_set_id: int | None = None


def compute_settlement(
    data: SettlementInput,
    *,
    gratuity_rules: RuleSet | None,
    rounding: str = "ROUND_HALF_UP",
) -> Settlement:
    """A leaver's settlement.

    **Gratuity is computed only from a verified rule set.** There is no fallback
    formula, because an invented one would understate or overstate a statutory
    payment somebody is legally owed - and the figure looks equally plausible
    either way. Without rules, the gratuity line is zero and a note says why, so
    the gap is visible rather than silent.
    """
    notes: list[str] = []

    encashment = money(data.encashable_leave_days * data.per_day_wage, mode=rounding)
    if data.encashable_leave_days > ZERO:
        notes.append(
            f"{data.encashable_leave_days} day(s) encashed at {data.per_day_wage} per day."
        )

    recovery = money(data.notice_shortfall_days * data.per_day_wage, mode=rounding)
    if data.notice_shortfall_days > ZERO:
        notes.append(
            f"{data.notice_shortfall_days} day(s) of notice shortfall recovered at "
            f"{data.per_day_wage} per day."
        )

    gratuity = ZERO
    rule_set_id: int | None = None
    if gratuity_rules is None:
        notes.append(
            "Gratuity was not computed: no verified rule set is in force. Enter "
            "the company's gratuity rules with their source before settling, or "
            "record the amount as an other due."
        )
    else:
        minimum_years = gratuity_rules.numeric("minimum_service_years", required=False)
        if minimum_years is not None and data.completed_service_years < minimum_years:
            notes.append(
                f"Gratuity was not computed: {data.completed_service_years} years of "
                f"service is below the configured minimum of {minimum_years}."
            )
        else:
            days_per_year = gratuity_rules.numeric("days_per_year")
            divisor = gratuity_rules.numeric("monthly_divisor")
            assert days_per_year is not None and divisor is not None
            if divisor == ZERO:
                raise StatutoryRulesMissing(
                    "The gratuity rule set's 'monthly_divisor' is zero, which cannot be divided by."
                )
            raw = data.gratuity_wage / divisor * days_per_year * data.completed_service_years
            gratuity = money(raw, mode=rounding)
            ceiling = gratuity_rules.numeric("maximum_amount", required=False)
            if ceiling is not None and gratuity > ceiling:
                notes.append(
                    f"Gratuity of {gratuity} was capped at the configured maximum of {ceiling}."
                )
                gratuity = money(ceiling, mode=rounding)
            notes.append(
                f"Gratuity: {data.gratuity_wage} / {divisor} x {days_per_year} x "
                f"{data.completed_service_years} years."
            )
            rule_set_id = gratuity_rules.rule_set_id
            if not gratuity_rules.is_verified:
                notes.append(
                    "The gratuity rule set used is not verified, so this figure must "
                    "not be paid until it is."
                )

    net = money(
        encashment + gratuity + data.other_dues - recovery - data.other_recoveries,
        mode=rounding,
    )
    return Settlement(
        leave_encashment_amount=encashment,
        notice_recovery_amount=recovery,
        gratuity_amount=gratuity,
        net_settlement=net,
        notes=notes,
        gratuity_rule_set_id=rule_set_id,
    )
