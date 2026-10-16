"""Pure leave rules. No I/O, no database, no configuration lookups.

Everything here is a function over values, so the parts most likely to be
disputed - how many days accrued, what a mid-month joiner gets, how a range
spanning a weekend is counted - can be asserted exactly against a worked
example.

What is deliberately *not* decided here
---------------------------------------
No quota, accrual rate, statutory entitlement or rounding convention is
hard-coded. Every one arrives as an argument, sourced from a
:class:`~app.modules.leave.models.LeavePolicy` row or a company setting. The
project brief forbids inventing them, and a number buried in a function is the
hardest kind to find later.

The arithmetic conventions that *are* fixed here, because they are arithmetic
rather than policy:

* Leave quantities are :class:`~decimal.Decimal`, never float. A half day is
  ``0.5`` exactly, and a balance that has had thirty movements applied to it
  must still reconcile to the penny of a day.
* Proration is a fraction of the accrual period, not a whole-period credit, and
  the basis (calendar days, working days, completed months) is passed in.
* Rounding is applied **once**, to the period's accrual, not to a running total
  - rounding twice compounds the error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal

DayPortion = Literal["full", "first_half", "second_half"]
DayType = Literal["working", "weekly_off", "holiday"]
AccrualFrequency = Literal["none", "monthly", "quarterly", "half_yearly", "annual"]
RoundingMode = Literal["none", "nearest_half", "nearest_full", "down_half", "up_half"]
ProrationBasis = Literal["calendar_days", "working_days", "completed_months"]

ZERO = Decimal("0.00")
HALF = Decimal("0.50")
ONE = Decimal("1.00")
DAY_QUANTUM = Decimal("0.01")

#: Months per accrual period, by frequency. ``annual`` is one period a year.
PERIODS_PER_YEAR: dict[str, int] = {
    "monthly": 12,
    "quarterly": 4,
    "half_yearly": 2,
    "annual": 1,
}


#: Transaction types that consume entitlement, plus the reversal that undoes
#: one. Only these count towards a future commitment: a future accrual has not
#: happened yet and must not be treated as spendable.
FUTURE_COMMITMENT_TYPES: frozenset[str] = frozenset(
    {"deduction", "reversal", "encashment", "expiry", "carry_forward_out"}
)


def _days(value: Decimal) -> Decimal:
    """Normalise a leave quantity to two decimal places."""
    return Decimal(value).quantize(DAY_QUANTUM, rounding=ROUND_HALF_UP)


class LeaveRuleViolation(ValueError):
    """A leave rule was broken. Carries the field so the UI can point at it."""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        self.field_name = field_name
        super().__init__(message)


# ---------------------------------------------------------------------------
# Leave year
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaveYear:
    """One leave year, labelled by the calendar year it starts in."""

    label: int
    start: date
    end: date

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1


def leave_year_for(day: date, *, start_month: int, start_day: int) -> LeaveYear:
    """The leave year containing ``day``.

    The start is configurable (``leave.year_start_month`` / ``_day``), so this is
    not simply the calendar year: with an April start, 15 March 2026 belongs to
    the year labelled **2025**. Getting this wrong would carry forward balances
    at the wrong moment, which is why it is one function used everywhere rather
    than an inline calculation.
    """
    if not 1 <= start_month <= 12:
        raise LeaveRuleViolation(
            f"The leave year start month must be 1-12, not {start_month}.",
            field_name="leave.year_start_month",
        )
    if not 1 <= start_day <= 28:
        # Capped at 28 so the start date exists in every month, including
        # February in a non-leap year.
        raise LeaveRuleViolation(
            f"The leave year start day must be 1-28, not {start_day}.",
            field_name="leave.year_start_day",
        )

    label = day.year if (day.month, day.day) >= (start_month, start_day) else day.year - 1
    start = date(label, start_month, start_day)
    end = date(label + 1, start_month, start_day) - timedelta(days=1)
    return LeaveYear(label=label, start=start, end=end)


# ---------------------------------------------------------------------------
# Accrual periods
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccrualPeriod:
    """One accrual period inside a leave year."""

    index: int
    """1-based position in the year, used in the idempotency key."""

    start: date
    end: date
    key: str
    """Stable identifier, e.g. ``2026-04`` or ``2026-Q1``. Appears in the key."""

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days + 1


def _add_months(anchor: date, months: int) -> date:
    """Shift a date by whole months, keeping the day of month.

    The leave-year start day is capped at 28, so the target day always exists
    and no clamping is needed.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, anchor.day)


def accrual_periods(year: LeaveYear, frequency: str) -> list[AccrualPeriod]:
    """Every accrual period in a leave year.

    Periods are anchored to the **leave year start**, not to calendar months. A
    company whose leave year starts in April gets periods running 1 April to 30
    April, not 1 April to 1 May, and its quarters are Apr-Jun rather than
    Jan-Mar. Anchoring to the calendar instead would give a short first period
    and silently shift every accrual.
    """
    if frequency == "none":
        return []
    if frequency not in PERIODS_PER_YEAR:
        raise LeaveRuleViolation(
            f"Unknown accrual frequency {frequency!r}. Choose one of: "
            + ", ".join(sorted(PERIODS_PER_YEAR)),
            field_name="accrual_frequency",
        )

    count = PERIODS_PER_YEAR[frequency]
    months_each = 12 // count

    periods: list[AccrualPeriod] = []
    for index in range(count):
        start = _add_months(year.start, index * months_each)
        end = _add_months(year.start, (index + 1) * months_each) - timedelta(days=1)
        periods.append(
            AccrualPeriod(
                index=index + 1,
                start=start,
                end=end,
                key=_period_key(year, frequency, index + 1, start),
            )
        )
    return periods


def _period_key(year: LeaveYear, frequency: str, index: int, start: date) -> str:
    if frequency == "monthly":
        return f"{start.year:04d}-{start.month:02d}"
    if frequency == "quarterly":
        return f"{year.label}-Q{index}"
    if frequency == "half_yearly":
        return f"{year.label}-H{index}"
    return f"{year.label}-Y"


def period_containing(day: date, periods: list[AccrualPeriod]) -> AccrualPeriod | None:
    for period in periods:
        if period.start <= day <= period.end:
            return period
    return None


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


def round_days(value: Decimal, mode: str) -> Decimal:
    """Apply a rounding convention to a leave quantity.

    Applied **once**, to a period's accrual. Rounding a running total repeatedly
    compounds the error: twelve monthly accruals of 1.25 rounded each time to
    1.5 gives 18 days against a 15-day quota.

    The result is always quantized to two decimal places, so every leave
    quantity the API returns has the same shape - "1.00" rather than "1" in one
    place and "1.0" in another.
    """
    if mode == "none":
        rounded = value
    elif mode == "nearest_half":
        rounded = (value * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2
    elif mode == "nearest_full":
        rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    elif mode == "down_half":
        rounded = (value * 2).quantize(Decimal("1"), rounding=ROUND_FLOOR) / 2
    elif mode == "up_half":
        rounded = (value * 2).quantize(Decimal("1"), rounding=ROUND_CEILING) / 2
    else:
        raise LeaveRuleViolation(f"Unknown rounding mode {mode!r}.", field_name="accrual_rounding")
    return _days(rounded)


# ---------------------------------------------------------------------------
# Proration
# ---------------------------------------------------------------------------


def proration_fraction(
    period: AccrualPeriod,
    *,
    employed_from: date | None,
    employed_to: date | None,
    accrual_starts_on: date | None = None,
    basis: str = "calendar_days",
    working_days_in_period: int | None = None,
    eligible_working_days: int | None = None,
) -> Decimal:
    """What fraction of a period's accrual an employee earned.

    Returns ``0`` when they were not employed at all in the period and ``1``
    when they were employed throughout. A mid-period joiner gets the fraction
    they were actually there for - without this, someone joining on the 28th
    receives the same credit as someone who worked the whole month.

    ``accrual_starts_on`` moves the start later than the joining date, for a
    policy that accrues only after confirmation.

    The basis is a real policy choice, so it is passed in rather than assumed:

    ``calendar_days``
        Days present over days in the period. Simple and the usual default.
    ``working_days``
        Working days present over working days in the period. Needs both counts
        supplied, since this module does no calendar resolution.
    ``completed_months``
        Whole months only - a joiner part-way through a month earns nothing for
        it. Harsher, and some companies use it.
    """
    # Validated first. A misconfigured basis must not fall through to a full
    # period's accrual, which is what happened when the "employed throughout"
    # shortcut below was reached before this check.
    if basis not in ("calendar_days", "working_days", "completed_months"):
        raise LeaveRuleViolation(
            f"Unknown proration basis {basis!r}. Choose one of: calendar_days, "
            "working_days, completed_months.",
            field_name="basis",
        )

    start = max(
        employed_from or period.start,
        accrual_starts_on or period.start,
        period.start,
    )
    end = min(employed_to or period.end, period.end)

    if end < start:
        return ZERO
    if start <= period.start and end >= period.end:
        return ONE

    if basis == "completed_months":
        # A partial month earns nothing. For a monthly period that is all or
        # nothing; for a longer one it is the count of whole months covered.
        months = _whole_months_between(start, end, period)
        span_months = max(_whole_months_between(period.start, period.end, period), 1)
        return (Decimal(months) / Decimal(span_months)).quantize(
            DAY_QUANTUM, rounding=ROUND_HALF_UP
        )

    if basis == "working_days":
        if working_days_in_period is None or eligible_working_days is None:
            raise LeaveRuleViolation(
                "Proration on a working-day basis needs both working-day counts; "
                "this module does not resolve calendars.",
                field_name="basis",
            )
        if working_days_in_period <= 0:
            return ZERO
        return (Decimal(eligible_working_days) / Decimal(working_days_in_period)).quantize(
            DAY_QUANTUM, rounding=ROUND_HALF_UP
        )

    if basis != "calendar_days":
        raise LeaveRuleViolation(f"Unknown proration basis {basis!r}.", field_name="basis")

    present = (end - start).days + 1
    return (Decimal(present) / Decimal(period.total_days)).quantize(
        DAY_QUANTUM, rounding=ROUND_HALF_UP
    )


def _whole_months_between(start: date, end: date, period: AccrualPeriod) -> int:
    """Count month boundaries of the period fully covered by start..end."""
    months = 0
    cursor = period.start
    while cursor <= period.end:
        following = _add_months(cursor, 1)
        month_end = following - timedelta(days=1)
        if start <= cursor and end >= month_end:
            months += 1
        cursor = following
    return months


# ---------------------------------------------------------------------------
# Accrual
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccrualInput:
    """Everything needed to work out one period's accrual."""

    annual_quota_days: Decimal
    frequency: str
    rounding: str
    prorate_on_joining: bool = True
    prorate_on_exit: bool = True
    max_accumulation_days: Decimal | None = None
    proration_basis: str = "calendar_days"


@dataclass(frozen=True, slots=True)
class AccrualResult:
    """What a period's accrual came to, and why."""

    days: Decimal
    raw_days: Decimal
    """Before rounding and before the accumulation cap, for explanation."""

    fraction: Decimal
    was_prorated: bool
    was_capped: bool
    reason: str

    @property
    def is_zero(self) -> bool:
        return self.days == ZERO


def compute_accrual(
    *,
    period: AccrualPeriod,
    rules: AccrualInput,
    employed_from: date | None,
    employed_to: date | None,
    accrual_starts_on: date | None = None,
    current_balance: Decimal = ZERO,
    working_days_in_period: int | None = None,
    eligible_working_days: int | None = None,
) -> AccrualResult:
    """One employee's accrual for one period.

    Returns a result rather than raising on a zero outcome: "not employed for
    any of this period" is a normal answer during a bulk run, not an error.

    The accumulation cap stops accrual at the ceiling rather than rejecting it,
    and says so in ``reason``, because an employee at the cap should simply stop
    earning - not have the job fail.
    """
    if rules.frequency == "none":
        return AccrualResult(
            days=ZERO,
            raw_days=ZERO,
            fraction=ZERO,
            was_prorated=False,
            was_capped=False,
            reason="This leave type does not accrue; its balance is granted or adjusted.",
        )

    periods_per_year = PERIODS_PER_YEAR[rules.frequency]
    per_period = Decimal(rules.annual_quota_days) / Decimal(periods_per_year)

    fraction = ONE
    prorated = False

    # Whether to prorate depends on *which* edge is partial, so the two flags
    # are evaluated against the actual boundary rather than together.
    joins_mid = employed_from is not None and employed_from > period.start
    exits_mid = employed_to is not None and employed_to < period.end
    starts_accruing_mid = accrual_starts_on is not None and accrual_starts_on > period.start

    if (joins_mid or starts_accruing_mid) and not rules.prorate_on_joining:
        # Not prorating a joiner means a full credit, but someone who was not
        # employed at all in the period still gets nothing.
        if (employed_from is not None and employed_from > period.end) or (
            accrual_starts_on is not None and accrual_starts_on > period.end
        ):
            fraction = ZERO
    elif exits_mid and not rules.prorate_on_exit:
        if employed_to is not None and employed_to < period.start:
            fraction = ZERO
    else:
        fraction = proration_fraction(
            period,
            employed_from=employed_from,
            employed_to=employed_to,
            accrual_starts_on=accrual_starts_on,
            basis=rules.proration_basis,
            working_days_in_period=working_days_in_period,
            eligible_working_days=eligible_working_days,
        )
        prorated = fraction != ONE

    raw = (per_period * fraction).quantize(DAY_QUANTUM, rounding=ROUND_HALF_UP)
    rounded = round_days(raw, rules.rounding)

    if rounded <= ZERO:
        return AccrualResult(
            days=ZERO,
            raw_days=raw,
            fraction=fraction,
            was_prorated=prorated,
            was_capped=False,
            reason=(
                "Not employed during this period."
                if fraction == ZERO
                else "The prorated accrual rounded to zero."
            ),
        )

    capped = False
    if rules.max_accumulation_days is not None:
        room = Decimal(rules.max_accumulation_days) - current_balance
        if room <= ZERO:
            return AccrualResult(
                days=ZERO,
                raw_days=raw,
                fraction=fraction,
                was_prorated=prorated,
                was_capped=True,
                reason=(
                    f"Balance is already at the {rules.max_accumulation_days} day "
                    "accumulation ceiling, so nothing further accrued."
                ),
            )
        if rounded > room:
            rounded = room.quantize(DAY_QUANTUM, rounding=ROUND_HALF_UP)
            capped = True

    parts = [f"{per_period.normalize()} days per {rules.frequency} period"]
    if prorated:
        parts.append(f"prorated to {fraction} for part-period employment")
    if rules.rounding != "none" and rounded != raw:
        parts.append(f"rounded {raw} -> {rounded} ({rules.rounding})")
    if capped:
        parts.append("reduced to fit the accumulation ceiling")

    return AccrualResult(
        days=rounded,
        raw_days=raw,
        fraction=fraction,
        was_prorated=prorated,
        was_capped=capped,
        reason="; ".join(parts) + ".",
    )


# ---------------------------------------------------------------------------
# Day expansion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaveDay:
    """One day of an application, after the calendar has been applied."""

    leave_date: date
    portion: DayPortion
    day_fraction: Decimal
    day_type: DayType
    counts_against_balance: bool
    is_paid: bool


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    days: tuple[LeaveDay, ...]
    total_days: Decimal
    """Sum of the fractions that count against the balance."""

    span_days: int
    non_working_included: tuple[date, ...]
    """Non-working days that were counted, so the reason can be shown."""

    non_working_excluded: tuple[date, ...]
    notes: tuple[str, ...] = ()


def expand_leave_days(
    *,
    start: date,
    end: date,
    day_types: dict[date, DayType],
    start_portion: DayPortion = "full",
    end_portion: DayPortion = "full",
    counts_non_working_days: bool = False,
    sandwich_rule: bool = False,
    is_paid: bool = True,
) -> ExpansionResult:
    """Turn a date range into the days an application actually consumes.

    ``day_types`` is supplied by the caller from the shared calendar resolver,
    so leave and attendance cannot disagree about which days were working days.

    Three rules, each stated because each has a direct payroll consequence:

    1. **A non-working day inside the range is not consumed** by default. An
       employee taking Friday and Monday off spends two days, not four.
    2. **Unless the leave type counts calendar days** - some statutory types are
       defined that way. This is per type, not a global switch.
    3. **Or unless the sandwich rule is on**, in which case a non-working day
       *between* two consumed days is counted. Leading and trailing non-working
       days are never counted by the sandwich rule: applying from a Saturday
       should not charge for the Saturday.

    Half-day portions apply only to the first and last day of the range, which
    is what a half day means in practice - a middle day of a multi-day absence
    is a full day.
    """
    if end < start:
        raise LeaveRuleViolation(
            "The end date cannot be before the start date.", field_name="end_date"
        )

    span = (end - start).days + 1
    if start_portion != "full" and end_portion != "full" and span > 1:
        # Both ends half: legitimate (leave from Monday afternoon to Friday
        # morning), so allowed - noted only so the total is not a surprise.
        pass

    dates = [start + timedelta(days=offset) for offset in range(span)]
    resolved: list[tuple[date, DayType]] = [(day, day_types.get(day, "working")) for day in dates]

    working_positions = [index for index, (_day, kind) in enumerate(resolved) if kind == "working"]

    days: list[LeaveDay] = []
    included: list[date] = []
    excluded: list[date] = []
    notes: list[str] = []

    for index, (day, kind) in enumerate(resolved):
        is_working = kind == "working"

        if is_working or counts_non_working_days:
            counts = True
        elif sandwich_rule and working_positions:
            # Interior only: strictly between the first and last working day.
            counts = working_positions[0] < index < working_positions[-1]
        else:
            counts = False

        if not is_working:
            (included if counts else excluded).append(day)

        portion: DayPortion = "full"
        if counts:
            if index == 0 and start_portion != "full":
                portion = start_portion
            elif index == span - 1 and end_portion != "full":
                portion = end_portion

        fraction = HALF if portion != "full" else ONE

        days.append(
            LeaveDay(
                leave_date=day,
                portion=portion,
                day_fraction=fraction,
                day_type=kind,
                counts_against_balance=counts,
                # A day that is not consumed is not paid *as leave*; attendance
                # already treats a weekly off correctly on its own.
                is_paid=is_paid and counts,
            )
        )

    total = sum((day.day_fraction for day in days if day.counts_against_balance), ZERO)

    if counts_non_working_days and included:
        notes.append(
            "This leave type is counted in calendar days, so weekly offs and "
            "holidays inside the range are consumed."
        )
    elif sandwich_rule and included:
        notes.append(
            "The sandwich rule is in force, so non-working days between leave days are consumed."
        )
    if excluded:
        notes.append(f"{len(excluded)} non-working day(s) in the range are not deducted.")

    return ExpansionResult(
        days=tuple(days),
        total_days=Decimal(total).quantize(DAY_QUANTUM, rounding=ROUND_HALF_UP),
        span_days=span,
        non_working_included=tuple(included),
        non_working_excluded=tuple(excluded),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerMovement:
    """A ledger row reduced to what balance arithmetic needs."""

    txn_type: str
    days: Decimal
    effective_date: date
    leave_year: int
    expires_on: date | None = None


@dataclass(frozen=True, slots=True)
class Balance:
    """A balance, with each figure meaning exactly one thing.

    Three separate numbers rather than one, because they answer different
    questions and conflating them causes real errors:

    ``ledger_days``
        What the ledger sums to **as of** the given date. The historical,
        authoritative figure; this is what answers "what was my balance on 31
        March".
    ``pending_days``
        Committed by applications nobody has decided yet.
    ``committed_days``
        Already approved, but dated after ``as_of`` - leave booked for next
        month. These have left the ledger's as-of total untouched while being a
        genuine commitment.
    ``available_days``
        ``ledger_days - pending_days - committed_days``. The only figure a new
        application should be checked against.
    """

    as_of: date
    ledger_days: Decimal
    pending_days: Decimal
    committed_days: Decimal
    available_days: Decimal

    accrued_days: Decimal
    used_days: Decimal
    expiring_days: Decimal = ZERO
    expiring_on: date | None = None

    @property
    def is_negative(self) -> bool:
        return self.ledger_days < ZERO


def compute_balance(
    movements: list[LedgerMovement],
    *,
    as_of: date,
    pending_days: Decimal = ZERO,
    expiring_within_days: int = 30,
) -> Balance:
    """Derive a balance from ledger movements.

    Movements dated after ``as_of`` are excluded, which is what makes "what was
    the balance on 31 March" answerable - a stored balance column could never
    answer it.

    ``pending_days`` and ``committed_days`` are subtracted into
    ``available_days`` but **not** from ``ledger_days``. An employee with 5 days
    and 5 pending has a balance of 5 and nothing available; conflating the two
    would either let them apply twice for the same days, or make the balance
    look spent before anyone had approved it.

    ``committed_days`` exists because a deduction is dated on the **leave's own
    start date**, not the approval date - that is what makes carry-forward
    attribute it to the right leave year. Leave approved today for next month
    therefore does not change today's ledger total, and without counting it
    separately an employee could book the same entitlement twice.
    """
    applicable = [movement for movement in movements if movement.effective_date <= as_of]

    # Approved consumption dated after as_of: real commitments the as-of total
    # cannot see. A reversal of one is netted off, or cancelling future leave
    # would leave those days permanently unavailable. Future *credits* are
    # deliberately not netted in - an accrual that has not happened yet must not
    # fund leave taken today.
    committed = max(
        -sum(
            (
                movement.days
                for movement in movements
                if movement.effective_date > as_of and movement.txn_type in FUTURE_COMMITMENT_TYPES
            ),
            ZERO,
        ),
        ZERO,
    )

    ledger = sum((movement.days for movement in applicable), ZERO)
    accrued = sum((movement.days for movement in applicable if movement.days > ZERO), ZERO)
    used = sum((-movement.days for movement in applicable if movement.days < ZERO), ZERO)

    horizon = as_of + timedelta(days=expiring_within_days)
    expiring = ZERO
    expiring_on: date | None = None
    for movement in applicable:
        if movement.expires_on is not None and as_of <= movement.expires_on <= horizon:
            expiring += movement.days
            if expiring_on is None or movement.expires_on < expiring_on:
                expiring_on = movement.expires_on

    quantum = lambda value: Decimal(value).quantize(DAY_QUANTUM, rounding=ROUND_HALF_UP)  # noqa: E731

    return Balance(
        as_of=as_of,
        ledger_days=quantum(ledger),
        pending_days=quantum(pending_days),
        committed_days=quantum(committed),
        available_days=quantum(ledger - pending_days - committed),
        accrued_days=quantum(accrued),
        used_days=quantum(used + committed),
        expiring_days=quantum(max(expiring, ZERO)),
        expiring_on=expiring_on,
    )


@dataclass(frozen=True, slots=True)
class PaidUnpaidSplit:
    paid_days: Decimal
    unpaid_days: Decimal
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "paid_days", _days(self.paid_days))
        object.__setattr__(self, "unpaid_days", _days(self.unpaid_days))


def split_paid_unpaid(
    *,
    requested_days: Decimal,
    available_days: Decimal,
    type_is_paid: bool,
    deducts_balance: bool,
    allow_negative: bool,
) -> PaidUnpaidSplit:
    """Decide how much of a request is paid.

    A request exceeding the balance is not simply refused: splitting it means an
    employee who needs five days with three in hand can take all five, with two
    unpaid, rather than being forced into an unrecorded absence. Whether the
    balance may actually go negative is a policy decision passed in.
    """
    if not type_is_paid:
        return PaidUnpaidSplit(
            paid_days=ZERO,
            unpaid_days=requested_days,
            reason="This leave type is unpaid by definition.",
        )
    if not deducts_balance:
        return PaidUnpaidSplit(
            paid_days=requested_days,
            unpaid_days=ZERO,
            reason="This leave type has no balance to draw on; all days are paid.",
        )
    if available_days >= requested_days:
        return PaidUnpaidSplit(
            paid_days=requested_days,
            unpaid_days=ZERO,
            reason="Fully covered by the available balance.",
        )
    if allow_negative:
        return PaidUnpaidSplit(
            paid_days=requested_days,
            unpaid_days=ZERO,
            reason=(
                f"Only {available_days} day(s) available; a negative balance is "
                "permitted, so all days are paid."
            ),
        )

    paid = max(available_days, ZERO)
    return PaidUnpaidSplit(
        paid_days=paid,
        unpaid_days=requested_days - paid,
        reason=(
            f"{paid} day(s) covered by the balance; the remaining "
            f"{requested_days - paid} day(s) are unpaid."
        ),
    )


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeRules:
    """A leave type's restrictions, as flat values."""

    code: str
    name: str
    is_paid: bool = True
    deducts_balance: bool = True
    allows_half_day: bool = True
    counts_non_working_days: bool = False
    requires_reason: bool = True
    attachment_required_above_days: Decimal | None = None
    min_notice_days: int = 0
    max_consecutive_days: int | None = None
    min_service_days: int = 0
    gender_restriction: str = "any"
    allows_negative_balance: bool | None = None


@dataclass(frozen=True, slots=True)
class ApplicationRequest:
    """What an applicant asked for, before any calendar resolution."""

    start_date: date
    end_date: date
    start_portion: DayPortion = "full"
    end_portion: DayPortion = "full"
    reason: str | None = None
    has_attachment: bool = False


@dataclass(frozen=True, slots=True)
class EligibilityProblem:
    field_name: str | None
    message: str


def check_eligibility(
    *,
    request: ApplicationRequest,
    rules: TypeRules,
    today: date,
    date_of_joining: date,
    date_of_exit: date | None,
    employee_gender: str | None,
    computed_days: Decimal,
    span_days: int,
    half_day_enabled_company_wide: bool,
    backdated_window_days: int | None,
    max_future_days: int | None = None,
) -> list[EligibilityProblem]:
    """Every reason this application cannot be accepted.

    Returns **all** problems rather than the first, so an applicant fixes one
    form rather than discovering four refusals one at a time.
    """
    problems: list[EligibilityProblem] = []

    if request.end_date < request.start_date:
        problems.append(
            EligibilityProblem("end_date", "The end date cannot be before the start date.")
        )

    if computed_days <= ZERO:
        problems.append(
            EligibilityProblem(
                "start_date",
                "None of the selected dates are working days, so there is nothing to apply for.",
            )
        )

    # -- service and employment ---------------------------------------------
    if request.start_date < date_of_joining:
        problems.append(
            EligibilityProblem(
                "start_date",
                f"You were not on the rolls on {request.start_date.isoformat()}; "
                f"your joining date is {date_of_joining.isoformat()}.",
            )
        )
    if date_of_exit is not None and request.end_date > date_of_exit:
        problems.append(
            EligibilityProblem(
                "end_date",
                f"The leave extends past the recorded exit date {date_of_exit.isoformat()}.",
            )
        )

    if rules.min_service_days > 0:
        served = (request.start_date - date_of_joining).days
        if served < rules.min_service_days:
            problems.append(
                EligibilityProblem(
                    "leave_type_id",
                    f"{rules.name} needs {rules.min_service_days} days of service. "
                    f"On {request.start_date.isoformat()} you will have {max(served, 0)}.",
                )
            )

    # -- gender restriction --------------------------------------------------
    if rules.gender_restriction != "any":
        if employee_gender is None:
            problems.append(
                EligibilityProblem(
                    "leave_type_id",
                    f"{rules.name} is restricted, and no gender is recorded on your "
                    "profile. Ask HR to complete it rather than guessing.",
                )
            )
        elif employee_gender != rules.gender_restriction:
            problems.append(
                EligibilityProblem(
                    "leave_type_id",
                    f"{rules.name} is not available to you under its eligibility rules.",
                )
            )

    # -- notice and windows --------------------------------------------------
    if request.start_date < today and backdated_window_days is not None:
        days_back = (today - request.start_date).days
        if backdated_window_days == 0:
            problems.append(
                EligibilityProblem("start_date", "Backdated leave applications are not accepted.")
            )
        elif days_back > backdated_window_days:
            problems.append(
                EligibilityProblem(
                    "start_date",
                    f"Leave can only be applied for up to {backdated_window_days} "
                    f"days back. {request.start_date.isoformat()} is {days_back} days ago "
                    "- ask HR to record it.",
                )
            )
    elif rules.min_notice_days > 0:
        notice = (request.start_date - today).days
        if notice < rules.min_notice_days:
            problems.append(
                EligibilityProblem(
                    "start_date",
                    f"{rules.name} needs {rules.min_notice_days} day(s) of notice; "
                    f"this gives {notice}.",
                )
            )

    if max_future_days is not None and (request.start_date - today).days > max_future_days:
        problems.append(
            EligibilityProblem(
                "start_date",
                f"Leave cannot be applied for more than {max_future_days} days ahead.",
            )
        )

    # -- type limits ---------------------------------------------------------
    if rules.max_consecutive_days is not None and span_days > rules.max_consecutive_days:
        problems.append(
            EligibilityProblem(
                "end_date",
                f"{rules.name} allows at most {rules.max_consecutive_days} "
                f"consecutive day(s); this spans {span_days}.",
            )
        )

    half_requested = request.start_portion != "full" or request.end_portion != "full"
    if half_requested:
        if not half_day_enabled_company_wide:
            problems.append(EligibilityProblem("start_portion", "Half-day leave is not enabled."))
        elif not rules.allows_half_day:
            problems.append(
                EligibilityProblem("start_portion", f"{rules.name} cannot be taken as a half day.")
            )

    if rules.requires_reason and not (request.reason or "").strip():
        problems.append(EligibilityProblem("reason", f"{rules.name} requires a reason."))

    threshold = rules.attachment_required_above_days
    if threshold is not None and computed_days > threshold and not request.has_attachment:
        problems.append(
            EligibilityProblem(
                "attachment_file_id",
                f"{rules.name} needs a supporting document for more than "
                f"{threshold} day(s); this application is {computed_days} day(s).",
            )
        )

    return problems


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OccupiedDay:
    """A day already committed by another application."""

    leave_date: date
    portion: DayPortion
    application_id: int
    status: str


def detect_overlap(
    *, proposed: tuple[LeaveDay, ...], occupied: list[OccupiedDay]
) -> list[EligibilityProblem]:
    """Find days already taken by another open or approved application.

    Two half days on the same date are allowed only when they are *different*
    halves - a first-half and a second-half leave together make a full day, and
    refusing that would stop an employee splitting a day between two leave
    types, which is a legitimate thing to do.
    """
    taken: dict[date, list[OccupiedDay]] = {}
    for entry in occupied:
        taken.setdefault(entry.leave_date, []).append(entry)

    problems: list[EligibilityProblem] = []
    for day in proposed:
        if not day.counts_against_balance:
            continue
        existing = taken.get(day.leave_date)
        if not existing:
            continue

        for other in existing:
            compatible = (
                day.portion != "full" and other.portion != "full" and day.portion != other.portion
            )
            if compatible:
                continue
            problems.append(
                EligibilityProblem(
                    "start_date",
                    f"{day.leave_date.isoformat()} is already covered by application "
                    f"#{other.application_id} ({other.status}).",
                )
            )
            break

    return problems


# ---------------------------------------------------------------------------
# Approval chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainStep:
    stage: str
    status: str
    """The application status while awaiting this stage."""


STAGE_STATUS = {
    "reporting_manager": "pending_manager",
    "hr_admin": "pending_hr",
}


def build_chain(chain: list[str], *, manager_available: bool) -> list[ChainStep]:
    """The stages this application must pass, in order.

    A missing manager is handled here rather than at decision time: the
    application is routed to HR from the start, so it never sits in a queue
    nobody can act on. The skip is recorded as a stage decision so the history
    shows why there is no manager approval.
    """
    steps: list[ChainStep] = []
    for stage in chain:
        if stage not in STAGE_STATUS:
            raise LeaveRuleViolation(
                f"Unknown approval stage {stage!r}. Known stages: "
                + ", ".join(sorted(STAGE_STATUS)),
                field_name="leave.approval_chain",
            )
        if stage == "reporting_manager" and not manager_available:
            continue
        steps.append(ChainStep(stage=stage, status=STAGE_STATUS[stage]))

    if not steps:
        raise LeaveRuleViolation(
            "The approval chain resolved to no stages, so nobody could decide this "
            "application. Check leave.approval_chain.",
            field_name="leave.approval_chain",
        )
    return steps


def next_stage(steps: list[ChainStep], current: str | None) -> ChainStep | None:
    """The stage after ``current``, or None when the chain is complete."""
    if current is None:
        return steps[0] if steps else None
    for index, step in enumerate(steps):
        if step.stage == current:
            return steps[index + 1] if index + 1 < len(steps) else None
    raise LeaveRuleViolation(
        f"Stage {current!r} is not part of this application's chain.",
        field_name="current_stage",
    )


# ---------------------------------------------------------------------------
# Idempotency keys
# ---------------------------------------------------------------------------


def accrual_key(*, employee_id: int, type_code: str, period_key: str) -> str:
    """Deterministic key for one employee's accrual in one period.

    The whole point: a retried accrual job computes the same key and the unique
    constraint rejects the duplicate, so a balance cannot be credited twice. The
    key deliberately excludes the run id and the posting date, which would differ
    between runs and defeat it.
    """
    return f"accrual:{employee_id}:{type_code}:{period_key}"


def deduction_key(*, application_id: int) -> str:
    return f"deduction:application:{application_id}"


def reversal_key(*, application_id: int, entry_id: int) -> str:
    return f"reversal:application:{application_id}:entry:{entry_id}"


def opening_key(*, employee_id: int, type_code: str, leave_year: int) -> str:
    return f"opening:{employee_id}:{type_code}:{leave_year}"


def adjustment_key(*, employee_id: int, type_code: str, token: str) -> str:
    """Manual adjustments carry a caller-supplied token.

    An adjustment is a deliberate human act, so the token comes from the request
    rather than being derived - two genuinely separate corrections of the same
    size on the same day must both be possible, while a double-submitted form
    must not post twice.
    """
    return f"adjustment:{employee_id}:{type_code}:{token}"


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def policy_snapshot(
    *,
    rules: TypeRules,
    annual_quota_days: Decimal | None,
    accrual_frequency: str | None,
    sandwich_rule: bool,
    allow_negative: bool,
    backdated_window_days: int | None,
    half_day_enabled: bool,
    approval_chain: list[str],
) -> dict[str, object]:
    """The values that governed one application, for later explanation.

    Stored on the application so a decision stays explicable after the policy,
    the settings and the approval chain have all been changed. Without it,
    "why was this allowed?" becomes unanswerable.
    """
    return {
        "leave_type_code": rules.code,
        "is_paid": rules.is_paid,
        "deducts_balance": rules.deducts_balance,
        "counts_non_working_days": rules.counts_non_working_days,
        "min_notice_days": rules.min_notice_days,
        "max_consecutive_days": rules.max_consecutive_days,
        "attachment_required_above_days": (
            str(rules.attachment_required_above_days)
            if rules.attachment_required_above_days is not None
            else None
        ),
        "annual_quota_days": str(annual_quota_days) if annual_quota_days is not None else None,
        "accrual_frequency": accrual_frequency,
        "sandwich_rule_enabled": sandwich_rule,
        "allow_negative_balance": allow_negative,
        "backdated_window_days": backdated_window_days,
        "half_day_enabled": half_day_enabled,
        "approval_chain": list(approval_chain),
    }


# ---------------------------------------------------------------------------
# Leave-year closing
# ---------------------------------------------------------------------------

#: What happens to an unused balance at the end of a leave year.
YEAR_END_RULES = (
    "carry_forward_then_lapse",
    "carry_forward_then_encash",
    "lapse_all",
    "carry_forward_all",
)


@dataclass(frozen=True, slots=True)
class ClosingRules:
    """The policy values that govern one leave type's year end."""

    year_end_rule: str
    carry_forward_cap_days: Decimal | None
    carry_forward_expires_after_months: int | None
    encashment_allowed: bool
    encashment_max_days: Decimal | None

    def __post_init__(self) -> None:
        if self.year_end_rule not in YEAR_END_RULES:
            raise LeaveRuleViolation(
                f"Unknown year-end rule {self.year_end_rule!r}. Choose one of: "
                + ", ".join(sorted(YEAR_END_RULES)),
                field_name="leave.year_end_closing_rule",
            )


@dataclass(frozen=True, slots=True)
class ClosingOutcome:
    """How a closing balance is split between carry-forward, encashment and lapse.

    The three figures always sum to the closing balance. That is the property
    worth holding onto: a reconciliation report has to balance, and any rule
    that quietly dropped days would make it impossible to explain where an
    employee's entitlement went.
    """

    closing_balance: Decimal
    carried_days: Decimal
    encashed_days: Decimal
    lapsed_days: Decimal
    carried_expires_on: date | None
    reason: str

    @property
    def is_balanced(self) -> bool:
        return _days(self.carried_days + self.encashed_days + self.lapsed_days) == _days(
            self.closing_balance
        )

    @property
    def does_nothing(self) -> bool:
        return self.carried_days == ZERO and self.encashed_days == ZERO and self.lapsed_days == ZERO


def close_leave_year(
    *,
    closing_balance: Decimal,
    rules: ClosingRules,
    next_year_start: date,
) -> ClosingOutcome:
    """Split a closing balance into carried, encashed and lapsed days.

    A **negative** closing balance is carried in full and never lapses or is
    encashed: an employee who has overdrawn their entitlement still owes those
    days, and wiping the debt at year end would quietly hand them back.

    Nothing is invented here. The cap, the expiry window and whether encashment
    is permitted all arrive from the policy, and the rule itself from a company
    setting.
    """
    balance = _days(closing_balance)

    if balance < ZERO:
        return ClosingOutcome(
            closing_balance=balance,
            carried_days=balance,
            encashed_days=ZERO,
            lapsed_days=ZERO,
            carried_expires_on=None,
            reason=(
                "The balance is negative, so the full amount is carried forward as a "
                "debit. Lapsing it would write off days the employee has already taken."
            ),
        )

    if balance == ZERO:
        return ClosingOutcome(
            closing_balance=ZERO,
            carried_days=ZERO,
            encashed_days=ZERO,
            lapsed_days=ZERO,
            carried_expires_on=None,
            reason="Nothing to close; the balance is zero.",
        )

    if rules.year_end_rule == "lapse_all":
        return ClosingOutcome(
            closing_balance=balance,
            carried_days=ZERO,
            encashed_days=ZERO,
            lapsed_days=balance,
            carried_expires_on=None,
            reason="The year-end rule lapses the whole unused balance.",
        )

    if rules.year_end_rule == "carry_forward_all":
        return ClosingOutcome(
            closing_balance=balance,
            carried_days=balance,
            encashed_days=ZERO,
            lapsed_days=ZERO,
            carried_expires_on=_carry_expiry(next_year_start, rules),
            reason="The year-end rule carries the whole balance forward.",
        )

    # The two capped rules share the carry-forward step and differ only in what
    # happens to the remainder.
    cap = rules.carry_forward_cap_days
    carried = balance if cap is None else min(balance, _days(cap))
    remainder = _days(balance - carried)

    parts: list[str] = []
    if cap is None:
        parts.append("no carry-forward cap is set, so the whole balance carries")
    else:
        parts.append(f"carried up to the {_days(cap)} day cap")

    if remainder <= ZERO:
        return ClosingOutcome(
            closing_balance=balance,
            carried_days=carried,
            encashed_days=ZERO,
            lapsed_days=ZERO,
            carried_expires_on=_carry_expiry(next_year_start, rules),
            reason="; ".join(parts) + ".",
        )

    if rules.year_end_rule == "carry_forward_then_encash" and rules.encashment_allowed:
        limit = rules.encashment_max_days
        encashed = remainder if limit is None else min(remainder, _days(limit))
        lapsed = _days(remainder - encashed)
        parts.append(
            f"{encashed} day(s) encashed"
            + ("" if limit is None else f" up to the {_days(limit)} day encashment limit")
        )
        if lapsed > ZERO:
            parts.append(f"{lapsed} day(s) lapsed")
        return ClosingOutcome(
            closing_balance=balance,
            carried_days=carried,
            encashed_days=encashed,
            lapsed_days=lapsed,
            carried_expires_on=_carry_expiry(next_year_start, rules),
            reason="; ".join(parts) + ".",
        )

    if rules.year_end_rule == "carry_forward_then_encash" and not rules.encashment_allowed:
        parts.append(
            f"{remainder} day(s) lapsed - the year-end rule asks for encashment, but "
            "this leave type does not permit it"
        )
    else:
        parts.append(f"{remainder} day(s) lapsed")

    return ClosingOutcome(
        closing_balance=balance,
        carried_days=carried,
        encashed_days=ZERO,
        lapsed_days=remainder,
        carried_expires_on=_carry_expiry(next_year_start, rules),
        reason="; ".join(parts) + ".",
    )


def _carry_expiry(next_year_start: date, rules: ClosingRules) -> date | None:
    """When carried days lapse, or None if they never do."""
    months = rules.carry_forward_expires_after_months
    if months is None:
        return None
    return _add_months_clamped(next_year_start, months) - timedelta(days=1)


def _add_months_clamped(anchor: date, months: int) -> date:
    """Shift by whole months, clamping the day to the target month's length.

    Unlike the accrual-period helper this cannot assume a day of 28 or less,
    because the leave-year start is configurable only up to 28 but an expiry is
    measured from it plus an arbitrary number of months.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return (date(year + 1, 1, 1) - date(year, 12, 1)).days
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def carry_forward_out_key(*, employee_id: int, type_code: str, leave_year: int) -> str:
    """One carry-forward-out per employee, type and closing year.

    This is what makes the year-end job safe to re-run: the brief requires that
    re-running must not duplicate leave credits, deductions or encashment
    entries, and these keys are how that is enforced at the database rather than
    remembered by the caller.
    """
    return f"cf_out:{employee_id}:{type_code}:{leave_year}"


def carry_forward_in_key(*, employee_id: int, type_code: str, leave_year: int) -> str:
    return f"cf_in:{employee_id}:{type_code}:{leave_year}"


def lapse_key(*, employee_id: int, type_code: str, leave_year: int) -> str:
    return f"lapse:{employee_id}:{type_code}:{leave_year}"


def encashment_key(*, employee_id: int, type_code: str, leave_year: int) -> str:
    return f"encash:{employee_id}:{type_code}:{leave_year}"


def carry_expiry_key(*, employee_id: int, type_code: str, entry_id: int) -> str:
    """Expiring one carried-forward lot that reached its date."""
    return f"cf_expiry:{employee_id}:{type_code}:{entry_id}"
