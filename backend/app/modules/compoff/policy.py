"""Pure comp-off rules. No I/O.

Two decisions live here and both are worth stating:

**How much credit a day's work earns.** From the minutes the attendance engine
already computed, against configured thresholds. Nothing is inferred from the
shift length or guessed - a day that falls short of the half-day threshold earns
nothing rather than a fraction, because a company that wants fractional credit
would set a lower threshold.

**Which credits a request consumes.** Earliest-expiring first, so an employee
never loses a credit that could have been used. That ordering is the entire
reason comp-off is tracked as lots rather than a single balance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
HALF = Decimal("0.50")
ONE = Decimal("1.00")
QUANTUM = Decimal("0.01")


def _days(value: Decimal) -> Decimal:
    return Decimal(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)


#: Readable names for day types, so a refusal does not read "work on a working".
DAY_TYPE_LABELS = {
    "working": "working day",
    "weekly_off": "weekly off",
    "holiday": "holiday",
}


class CompoffRuleViolation(ValueError):
    """A comp-off rule was broken. Carries the field for the UI."""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        self.field_name = field_name
        super().__init__(message)


# ---------------------------------------------------------------------------
# Earning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreditRules:
    """The configured thresholds, as flat values."""

    qualifying_day_types: frozenset[str]
    min_minutes_full_credit: int
    min_minutes_half_credit: int
    expiry_days: int
    requires_verification: bool

    def __post_init__(self) -> None:
        if self.min_minutes_half_credit > self.min_minutes_full_credit:
            raise CompoffRuleViolation(
                "The half-day threshold cannot exceed the full-day threshold: "
                f"{self.min_minutes_half_credit} > {self.min_minutes_full_credit}. "
                "A day would then earn a full credit before it earned a half one.",
                field_name="compoff.min_minutes_half_credit",
            )


@dataclass(frozen=True, slots=True)
class CreditOutcome:
    """What a day's work earned, and why."""

    credit_days: Decimal
    expiry_date: date | None
    status: str
    reason: str

    @property
    def earns_nothing(self) -> bool:
        return self.credit_days <= ZERO


def evaluate_day(
    *,
    work_date: date,
    day_type: str,
    net_minutes: int,
    rules: CreditRules,
) -> CreditOutcome:
    """Decide what one attendance day earns.

    Returns an outcome rather than raising when nothing is earned: "worked an
    hour on a Sunday" is a normal answer during a bulk run, not an error.

    The day type is checked first. Work on an ordinary working day earns comp-off
    only if the company has configured ``working_day`` as qualifying - otherwise
    overtime on a normal day is overtime, not a day off owed, and conflating the
    two would silently grant leave for every late evening.
    """
    if day_type not in rules.qualifying_day_types:
        return CreditOutcome(
            credit_days=ZERO,
            expiry_date=None,
            status="rejected",
            reason=(
                f"Work on a {DAY_TYPE_LABELS.get(day_type, day_type)} does not "
                "qualify for comp-off under the current configuration."
            ),
        )

    if net_minutes >= rules.min_minutes_full_credit:
        credit = ONE
        basis = (
            f"{net_minutes} minutes worked, at or above the "
            f"{rules.min_minutes_full_credit}-minute full-day threshold"
        )
    elif net_minutes >= rules.min_minutes_half_credit:
        credit = HALF
        basis = (
            f"{net_minutes} minutes worked, at or above the "
            f"{rules.min_minutes_half_credit}-minute half-day threshold"
        )
    else:
        return CreditOutcome(
            credit_days=ZERO,
            expiry_date=None,
            status="rejected",
            reason=(
                f"{net_minutes} minutes worked is below the "
                f"{rules.min_minutes_half_credit}-minute half-day threshold, so no "
                "credit is earned."
            ),
        )

    # Zero means never expires. Measured from the earned date, so a credit for
    # work done in March expires three months after March regardless of when the
    # credit job happened to run.
    expiry = work_date + timedelta(days=rules.expiry_days) if rules.expiry_days > 0 else None

    status = "pending_verification" if rules.requires_verification else "available"
    note = (
        "Awaiting verification of the qualifying work."
        if rules.requires_verification
        else "Available immediately; verification is not required."
    )

    return CreditOutcome(
        credit_days=credit,
        expiry_date=expiry,
        status=status,
        reason=f"{basis}. {note}",
    )


# ---------------------------------------------------------------------------
# Balance and allocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreditLot:
    """A credit lot reduced to what allocation arithmetic needs."""

    credit_id: int
    earned_date: date
    credit_days: Decimal
    allocated_days: Decimal
    expiry_date: date | None
    status: str

    @property
    def remaining(self) -> Decimal:
        return _days(Decimal(self.credit_days) - Decimal(self.allocated_days))

    def usable_on(self, on_date: date) -> bool:
        """Whether this lot can fund time off taken on a date.

        A credit must not be spent on a day **after** it expires - that is the
        point of the expiry - and it cannot be spent before it was earned.
        """
        if self.status != "available" or self.remaining <= ZERO:
            return False
        if self.earned_date > on_date:
            return False
        return self.expiry_date is None or self.expiry_date >= on_date


@dataclass(frozen=True, slots=True)
class CompoffBalance:
    as_of: date
    available_days: Decimal
    """Live, unexpired, unallocated credit."""

    pending_verification_days: Decimal
    """Earned but not yet usable. Shown separately so it is not mistaken for spendable."""

    committed_days: Decimal
    """
    Held by applications awaiting a decision.

    Reported alongside ``available_days``, not deducted from it: the credits were
    allocated at submission, so the lots already exclude them.
    """

    expiring_soon_days: Decimal
    expiring_on: date | None
    expired_days: Decimal
    lots: tuple[CreditLot, ...] = ()


def compute_balance(
    lots: list[CreditLot],
    *,
    as_of: date,
    pending_allocation_days: Decimal = ZERO,
    expiring_within_days: int = 15,
) -> CompoffBalance:
    """Derive a comp-off balance from its lots.

    ``pending_verification_days`` is kept apart from ``available_days`` on
    purpose: credit for work HR has not yet confirmed is not spendable, and
    showing one total would have an employee plan around a day they may not get.

    ``pending_allocation_days`` is **reported**, not subtracted. Credits are
    allocated when a request is submitted, so each lot's ``allocated_days``
    already excludes the held amount from its remainder - subtracting it again
    would count the same hold twice and show a negative balance.
    """
    horizon = as_of + timedelta(days=expiring_within_days)

    available = ZERO
    pending = ZERO
    expired = ZERO
    expiring = ZERO
    expiring_on: date | None = None

    for lot in lots:
        if lot.status == "pending_verification":
            pending += lot.remaining
            continue
        if lot.status != "available":
            continue
        if lot.expiry_date is not None and lot.expiry_date < as_of:
            # Past its date but the expiry job has not run yet. Counted as
            # expired, not available, so the figure does not lie between runs.
            expired += lot.remaining
            continue

        available += lot.remaining
        if lot.expiry_date is not None and lot.expiry_date <= horizon:
            expiring += lot.remaining
            if expiring_on is None or lot.expiry_date < expiring_on:
                expiring_on = lot.expiry_date

    return CompoffBalance(
        as_of=as_of,
        available_days=_days(available),
        pending_verification_days=_days(pending),
        committed_days=_days(pending_allocation_days),
        expiring_soon_days=_days(expiring),
        expiring_on=expiring_on,
        expired_days=_days(expired),
        lots=tuple(lots),
    )


@dataclass(frozen=True, slots=True)
class Allocation:
    credit_id: int
    days: Decimal
    expiry_date: date | None


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    allocations: tuple[Allocation, ...]
    allocated_days: Decimal
    shortfall_days: Decimal

    @property
    def is_complete(self) -> bool:
        return self.shortfall_days <= ZERO


def plan_allocation(
    *, lots: list[CreditLot], required_days: Decimal, on_date: date
) -> AllocationPlan:
    """Choose which credits to spend, earliest-expiring first.

    The ordering matters and is not arbitrary: spending the credit closest to
    expiry means an employee never loses one they could have used. Within the
    same expiry date the oldest earned credit goes first, which keeps the result
    deterministic - two runs of the same request must allocate identically, or a
    re-approval could draw on different lots.

    A shortfall is **returned, not raised**: the caller decides whether to refuse
    the request or let it proceed, and a plan that says "two days short" is more
    useful than an exception.
    """
    if required_days <= ZERO:
        raise CompoffRuleViolation(
            "A comp-off request must consume more than zero days.",
            field_name="total_days",
        )

    usable = [lot for lot in lots if lot.usable_on(on_date)]
    # None sorts last: a credit that never expires is spent only after the ones
    # that do.
    usable.sort(
        key=lambda lot: (
            lot.expiry_date is None,
            lot.expiry_date or date.max,
            lot.earned_date,
            lot.credit_id,
        )
    )

    remaining = _days(required_days)
    chosen: list[Allocation] = []

    for lot in usable:
        if remaining <= ZERO:
            break
        take = min(lot.remaining, remaining)
        if take <= ZERO:
            continue
        chosen.append(
            Allocation(credit_id=lot.credit_id, days=_days(take), expiry_date=lot.expiry_date)
        )
        remaining = _days(remaining - take)

    return AllocationPlan(
        allocations=tuple(chosen),
        allocated_days=_days(_days(required_days) - remaining),
        shortfall_days=_days(max(remaining, ZERO)),
    )


# ---------------------------------------------------------------------------
# Day expansion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OffDay:
    off_date: date
    portion: str
    day_fraction: Decimal
    day_type: str


def expand_off_days(
    *,
    start: date,
    end: date,
    day_types: dict[date, str],
    start_portion: str = "full",
    end_portion: str = "full",
    half_day_enabled: bool,
) -> tuple[tuple[OffDay, ...], Decimal]:
    """Turn a requested range into the days comp-off will be taken on.

    Only working days are consumed - taking comp-off on a day that was already a
    holiday would spend a credit for nothing, so those dates are dropped rather
    than silently charged.
    """
    if end < start:
        raise CompoffRuleViolation(
            "The end date cannot be before the start date.", field_name="end_date"
        )
    if not half_day_enabled and (start_portion != "full" or end_portion != "full"):
        raise CompoffRuleViolation("Half-day comp-off is not enabled.", field_name="start_portion")

    span = (end - start).days + 1
    dates = [start + timedelta(days=offset) for offset in range(span)]

    days: list[OffDay] = []
    for index, day in enumerate(dates):
        kind = day_types.get(day, "working")
        if kind != "working":
            continue

        portion = "full"
        if index == 0 and start_portion != "full":
            portion = start_portion
        elif index == span - 1 and end_portion != "full":
            portion = end_portion

        days.append(
            OffDay(
                off_date=day,
                portion=portion,
                day_fraction=HALF if portion != "full" else ONE,
                day_type=kind,
            )
        )

    total = _days(sum((day.day_fraction for day in days), ZERO))
    return tuple(days), total


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def credit_key(*, employee_id: int, earned_date: date) -> str:
    """Identifies one day's credit for one employee.

    Mirrors the unique constraint, so the intent is visible in both places: a
    day worked earns credit once, however often attendance is recomputed.
    """
    return f"compoff:{employee_id}:{earned_date.isoformat()}"


def expiry_key(*, credit_id: int) -> str:
    return f"compoff-expiry:{credit_id}"
