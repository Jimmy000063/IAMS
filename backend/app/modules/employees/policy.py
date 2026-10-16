"""Pure employee business rules. No database access, no I/O.

Keeping these as functions over values means the awkward cases - a reporting
cycle, an exit date before joining, a code collision on import - are testable
without a database, and the service layer stays readable.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from app.modules.employees.models import (
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    EXIT_TYPES,
    GENDERS,
    MARITAL_STATUSES,
)

EMPLOYEE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,29}$")

#: Loose on purpose. Indian mobile numbers, landlines with STD codes and
#: international numbers all have to pass, and rejecting a valid number is worse
#: than accepting an odd-looking one that HR can correct.
PHONE_PATTERN = re.compile(r"^[+]?[0-9][0-9 ()-]{5,24}$")

IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
UAN_PATTERN = re.compile(r"^[0-9]{12}$")
AADHAAR_LAST4_PATTERN = re.compile(r"^[0-9]{4}$")


class RuleError(ValueError):
    """A business-rule violation, carrying the field it applies to."""

    def __init__(self, field: str | None, message: str) -> None:
        self.field = field
        super().__init__(message)


def normalise_employee_code(code: str) -> str:
    """Trim and upper-case. Codes are compared case-insensitively."""
    return code.strip().upper()


def validate_employee_code(code: str) -> str:
    normalised = normalise_employee_code(code)
    if not EMPLOYEE_CODE_PATTERN.match(normalised):
        raise RuleError(
            "employee_code",
            "Employee code must be 2-30 characters of letters, digits, dot, "
            "underscore or hyphen, starting with a letter or digit.",
        )
    return normalised


def validate_choice(field: str, value: str | None, allowed: Iterable[str]) -> str | None:
    if value is None or value == "":
        return None
    options = tuple(allowed)
    if value not in options:
        raise RuleError(field, f"Must be one of: {', '.join(options)}.")
    return value


def validate_phone(field: str, value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not PHONE_PATTERN.match(cleaned):
        raise RuleError(field, "Enter a valid phone number.")
    return cleaned


def validate_ifsc(value: str) -> str:
    cleaned = value.strip().upper()
    if not IFSC_PATTERN.match(cleaned):
        raise RuleError(
            "ifsc_code",
            "IFSC must be 11 characters: four letters, a zero, then six letters or digits.",
        )
    return cleaned


def validate_pan(value: str) -> str:
    cleaned = value.strip().upper()
    if not PAN_PATTERN.match(cleaned):
        raise RuleError("pan", "PAN must be five letters, four digits, then a letter.")
    return cleaned


def validate_uan(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not UAN_PATTERN.match(cleaned):
        raise RuleError("pf_uan", "UAN must be exactly 12 digits.")
    return cleaned


def validate_aadhaar_last4(value: str | None) -> str | None:
    """Accepts only four digits.

    If someone pastes a full Aadhaar number this rejects it rather than
    truncating, because silently storing a fragment of a number the user
    believed was being stored whole is the wrong behaviour - and the full
    number must not transit the API at all.
    """
    if not value:
        return None
    cleaned = value.strip()
    if not AADHAAR_LAST4_PATTERN.match(cleaned):
        raise RuleError(
            "aadhaar_last4",
            "Enter only the last four digits of the Aadhaar number. "
            "This system does not store the full number.",
        )
    return cleaned


def validate_account_number(value: str) -> str:
    cleaned = "".join(char for char in value.strip() if char.isalnum())
    if not (6 <= len(cleaned) <= 20):
        raise RuleError("account_number", "Account number must be 6-20 characters.")
    return cleaned


# ---------------------------------------------------------------------------
# Lifecycle dates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LifecycleDates:
    date_of_joining: date
    probation_end_date: date | None = None
    date_of_confirmation: date | None = None
    last_working_day: date | None = None
    date_of_exit: date | None = None


def validate_lifecycle(dates: LifecycleDates, *, today: date) -> None:
    """Check the ordering of employment dates.

    A future joining date is allowed - pre-boarding is normal. A joining date
    far in the past is also allowed, since historical records get back-loaded.
    What is not allowed is an ordering that makes attendance or payroll
    nonsensical.
    """
    if dates.date_of_joining > today.replace(year=today.year + 2):
        raise RuleError("date_of_joining", "Joining date is more than two years ahead.")

    if dates.probation_end_date and dates.probation_end_date < dates.date_of_joining:
        raise RuleError("probation_end_date", "Probation cannot end before joining.")

    if dates.date_of_confirmation and dates.date_of_confirmation < dates.date_of_joining:
        raise RuleError("date_of_confirmation", "Confirmation cannot precede joining.")

    if dates.last_working_day and dates.last_working_day < dates.date_of_joining:
        raise RuleError("last_working_day", "Last working day cannot precede joining.")

    if dates.date_of_exit:
        if dates.date_of_exit < dates.date_of_joining:
            raise RuleError("date_of_exit", "Exit date cannot precede joining.")
        if dates.last_working_day and dates.date_of_exit < dates.last_working_day:
            raise RuleError(
                "date_of_exit",
                "Exit date cannot be before the last working day.",
            )


def validate_exit(
    *,
    employment_status: str,
    date_of_exit: date | None,
    exit_type: str | None,
) -> None:
    """Exit status and exit data must agree.

    The database enforces the status/date pairing too; this produces a readable
    message instead of a constraint violation.
    """
    if employment_status == "exited":
        if date_of_exit is None:
            raise RuleError("date_of_exit", "An exit date is required to mark an employee exited.")
        if exit_type is None:
            raise RuleError("exit_type", "An exit type is required to mark an employee exited.")
    elif date_of_exit is not None:
        raise RuleError(
            "employment_status",
            "An exit date was supplied, so employment status must be 'exited'.",
        )


def validate_employment_fields(
    *,
    employment_type: str,
    employment_status: str,
    gender: str | None = None,
    marital_status: str | None = None,
    exit_type: str | None = None,
) -> None:
    # These two are required, so an empty value must fail rather than pass
    # through validate_choice's "None means not supplied" path.
    if employment_type not in EMPLOYMENT_TYPES:
        raise RuleError("employment_type", f"Must be one of: {', '.join(EMPLOYMENT_TYPES)}.")
    if employment_status not in EMPLOYMENT_STATUSES:
        raise RuleError("employment_status", f"Must be one of: {', '.join(EMPLOYMENT_STATUSES)}.")
    validate_choice("gender", gender, GENDERS)
    validate_choice("marital_status", marital_status, MARITAL_STATUSES)
    validate_choice("exit_type", exit_type, EXIT_TYPES)


# ---------------------------------------------------------------------------
# Reporting hierarchy
# ---------------------------------------------------------------------------


def detect_reporting_cycle(
    *,
    employee_id: int,
    proposed_manager_id: int | None,
    manager_of: Mapping[int, int | None],
) -> list[int] | None:
    """Return the cycle this assignment would create, or None if it is safe.

    ``manager_of`` maps employee id to current manager id. A cycle in the
    reporting tree is not a cosmetic problem: leave approval walks upward to
    find an approver, and a cycle would make that walk run forever.

    Returns the path from ``employee_id`` back to itself, so the caller can name
    the people involved in the error message.
    """
    if proposed_manager_id is None:
        return None
    if proposed_manager_id == employee_id:
        return [employee_id, employee_id]

    path = [employee_id]
    seen = {employee_id}
    current: int | None = proposed_manager_id

    while current is not None:
        path.append(current)
        if current == employee_id:
            return path
        if current in seen:
            # A pre-existing cycle elsewhere in the tree. Not caused by this
            # change, but reported rather than looped on forever.
            return path
        seen.add(current)
        current = manager_of.get(current)

    return None


def collect_subordinates(root_employee_id: int, reports_of: Mapping[int, list[int]]) -> set[int]:
    """All direct and indirect reports of an employee.

    Used for team-scoped reads. Iterative rather than recursive, and guards
    against a pre-existing cycle so a corrupt tree degrades to a wrong answer
    rather than a crashed request.
    """
    found: set[int] = set()
    queue = list(reports_of.get(root_employee_id, []))
    while queue:
        current = queue.pop()
        if current in found or current == root_employee_id:
            continue
        found.add(current)
        queue.extend(reports_of.get(current, []))
    return found


def next_employee_code(existing_codes: Iterable[str], *, prefix: str, width: int = 4) -> str:
    """Suggest the next sequential code for a prefix, e.g. ITC0008.

    A suggestion only - HR may override. Derived from the highest existing
    number rather than a count, so deleting nothing and skipping numbers both
    behave sensibly.
    """
    highest = 0
    pattern = re.compile(rf"^{re.escape(prefix.upper())}(\d+)$")
    for code in existing_codes:
        match = pattern.match(normalise_employee_code(code))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix.upper()}{highest + 1:0{width}d}"
