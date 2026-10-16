"""What a notification says, and the key that stops it being said twice.

Pure functions. No database, no clock, no session - which is what makes the
deduplication rule testable: a key is a function of the fact alone.

The dedupe key rule
-------------------
A key names **the fact, not the attempt**. It may contain the application id,
the decision stage, the attendance date, the announcement id, the user id. It
must never contain the run id, the current time, a sequence number or anything
else that differs between two runs reporting the same thing - because the whole
point is that the second run inserts nothing.

Where a fact can legitimately recur, the recurrence is *in* the key. A missing
punch on 3 March is one fact, keyed by the date; a missing punch on 4 March is
another. A leave application that is approved and then cancelled produces two
notifications, because "approved" and "cancelled" are different events, and the
stage is part of the key for the same reason - a manager's approval and HR's
approval are two things the applicant should hear about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Draft:
    """A notification before it has a recipient resolved."""

    category: str
    title: str
    body: str
    dedupe_key: str
    link_path: str
    priority: str = "normal"
    entity_type: str | None = None
    entity_id: str | int | None = None


def _days(value: Decimal | str) -> str:
    """Day counts read as '1', '1.5' - not '1.00', which looks like money."""
    quantity = Decimal(str(value)).normalize()
    if quantity == quantity.to_integral_value():
        quantity = quantity.quantize(Decimal(1))
    return str(quantity)


#: How a chain stage reads in a sentence. The key keeps the raw stage, because
#: that is the identifier; only the prose is translated.
_STAGE_LABELS: dict[str, str] = {
    "reporting_manager": "manager",
    "manager": "manager",
    "hr_admin": "HR",
    "hr": "HR",
    "final": "final",
}


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage.replace("_", " "))


def _span(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} to {end.isoformat()}"


# ---------------------------------------------------------------------- leave


def leave_decision(
    *,
    application_id: int,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
    approved: bool,
    is_final: bool,
    comment: str | None = None,
    unpaid_days: Decimal | None = None,
) -> Draft:
    """What the applicant is told about one stage's decision.

    An intermediate approval is reported as well as the final one: "your manager
    approved it, HR is next" is the information somebody planning a trip
    actually needs, and silence until the end reads as nothing having happened.
    """
    span = _span(start_date, end_date)
    quantity = _days(total_days)

    if not approved:
        title = f"{leave_type_name} declined for {span}"
        body = (
            f"Your request for {quantity} day(s) was declined at the {_stage_label(stage)} stage."
        )
        if comment:
            body += f" Reason given: {comment}"
        priority = "important"
    elif is_final:
        title = f"{leave_type_name} approved for {span}"
        body = f"Your request for {quantity} day(s) is approved."
        if unpaid_days is not None and Decimal(str(unpaid_days)) > 0:
            # Said plainly, because it changes somebody's pay: the leave is
            # granted either way, but part of it is without pay.
            body += (
                f" {_days(unpaid_days)} day(s) of it will be unpaid, as the "
                "available balance did not cover the whole request."
            )
            priority = "important"
        else:
            priority = "normal"
    else:
        title = f"{leave_type_name} approved at the {_stage_label(stage)} stage"
        body = (
            f"Your request for {quantity} day(s) from {span} passed the "
            f"{_stage_label(stage)} "
            "stage and is now awaiting the next approver."
        )
        priority = "normal"

    outcome = "approved" if approved else "rejected"
    return Draft(
        category="leave",
        title=title,
        body=body,
        dedupe_key=f"leave:{application_id}:{stage}:{outcome}",
        link_path=f"/leave/applications/{application_id}",
        priority=priority,
        entity_type="leave_application",
        entity_id=application_id,
    )


def leave_awaiting_approval(
    *,
    application_id: int,
    applicant_name: str,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
) -> Draft:
    """What an approver is told when something lands in their queue."""
    return Draft(
        category="leave",
        title=f"{applicant_name} requested {leave_type_name}",
        body=(
            f"{_days(total_days)} day(s) from {_span(start_date, end_date)}, "
            f"awaiting your {_stage_label(stage)} decision."
        ),
        dedupe_key=f"leave:{application_id}:awaiting:{stage}",
        link_path=f"/leave/approvals?application={application_id}",
        entity_type="leave_application",
        entity_id=application_id,
    )


def leave_cancelled(
    *,
    application_id: int,
    leave_type_name: str,
    start_date: date,
    end_date: date,
    days_returned: Decimal,
    by_whom: str,
) -> Draft:
    return Draft(
        category="leave",
        title=f"{leave_type_name} for {_span(start_date, end_date)} cancelled",
        body=(
            f"Cancelled by {by_whom}. {_days(days_returned)} day(s) have been "
            "returned to your balance."
        ),
        dedupe_key=f"leave:{application_id}:cancelled",
        link_path=f"/leave/applications/{application_id}",
        priority="important",
        entity_type="leave_application",
        entity_id=application_id,
    )


# -------------------------------------------------------------------- comp-off


def compoff_credited(
    *, credit_id: int, earned_date: date, credit_days: Decimal, needs_verification: bool
) -> Draft:
    """Credit for working a day off.

    Whether it is spendable yet is stated, because credit awaiting verification
    is not something to plan a day off around.
    """
    if needs_verification:
        body = (
            f"{_days(credit_days)} day(s) of comp-off credit for working on "
            f"{earned_date.isoformat()}. It becomes available once HR has "
            "verified it."
        )
    else:
        body = (
            f"{_days(credit_days)} day(s) of comp-off credit for working on "
            f"{earned_date.isoformat()} is now available."
        )
    return Draft(
        category="compoff",
        title=f"Comp-off credited for {earned_date.isoformat()}",
        body=body,
        dedupe_key=f"compoff_credit:{credit_id}:granted",
        link_path="/compoff",
        entity_type="compoff_credit",
        entity_id=credit_id,
    )


def compoff_credit_decided(
    *, credit_id: int, earned_date: date, credit_days: Decimal, approved: bool, reason: str | None
) -> Draft:
    if approved:
        title = f"Comp-off credit for {earned_date.isoformat()} verified"
        body = f"{_days(credit_days)} day(s) is now available to use."
        priority = "normal"
    else:
        title = f"Comp-off credit for {earned_date.isoformat()} rejected"
        body = f"{_days(credit_days)} day(s) was not credited."
        if reason:
            body += f" Reason given: {reason}"
        priority = "important"
    return Draft(
        category="compoff",
        title=title,
        body=body,
        dedupe_key=f"compoff_credit:{credit_id}:{'verified' if approved else 'rejected'}",
        link_path="/compoff",
        priority=priority,
        entity_type="compoff_credit",
        entity_id=credit_id,
    )


def compoff_expiring(*, credit_id: int, expires_on: date, remaining_days: Decimal) -> Draft:
    """A warning while the credit can still be used.

    Keyed on the credit and its expiry date, so a daily reminder job warns once
    per credit rather than once per run.
    """
    return Draft(
        category="compoff",
        title=f"{_days(remaining_days)} day(s) of comp-off expires on {expires_on.isoformat()}",
        body=(
            "Comp-off credit that is not used by its expiry date is lost. Apply "
            "before then if you intend to take it."
        ),
        dedupe_key=f"compoff_credit:{credit_id}:expiring:{expires_on.isoformat()}",
        link_path="/compoff",
        priority="important",
        entity_type="compoff_credit",
        entity_id=credit_id,
    )


def compoff_expired(*, credit_id: int, expires_on: date, days_lost: Decimal) -> Draft:
    return Draft(
        category="compoff",
        title=f"{_days(days_lost)} day(s) of comp-off expired",
        body=(
            f"Credit that expired on {expires_on.isoformat()} has been removed from your balance."
        ),
        dedupe_key=f"compoff_credit:{credit_id}:expired",
        link_path="/compoff",
        entity_type="compoff_credit",
        entity_id=credit_id,
    )


def compoff_decision(
    *,
    application_id: int,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
    approved: bool,
    is_final: bool,
    comment: str | None = None,
) -> Draft:
    span = _span(start_date, end_date)
    if not approved:
        title = f"Comp-off declined for {span}"
        body = (
            f"Your request for {_days(total_days)} day(s) was declined at the "
            f"{_stage_label(stage)} stage."
        )
        if comment:
            body += f" Reason given: {comment}"
        priority = "important"
    elif is_final:
        title = f"Comp-off approved for {span}"
        body = f"Your request for {_days(total_days)} day(s) is approved."
        priority = "normal"
    else:
        title = f"Comp-off approved at the {_stage_label(stage)} stage"
        body = f"Your request for {span} is now awaiting the next approver."
        priority = "normal"

    outcome = "approved" if approved else "rejected"
    return Draft(
        category="compoff",
        title=title,
        body=body,
        dedupe_key=f"compoff:{application_id}:{stage}:{outcome}",
        link_path=f"/compoff/applications/{application_id}",
        priority=priority,
        entity_type="compoff_application",
        entity_id=application_id,
    )


def compoff_awaiting_approval(
    *,
    application_id: int,
    applicant_name: str,
    start_date: date,
    end_date: date,
    total_days: Decimal,
    stage: str,
) -> Draft:
    return Draft(
        category="compoff",
        title=f"{applicant_name} requested comp-off",
        body=(
            f"{_days(total_days)} day(s) from {_span(start_date, end_date)}, "
            f"awaiting your {_stage_label(stage)} decision."
        ),
        dedupe_key=f"compoff:{application_id}:awaiting:{stage}",
        link_path=f"/compoff/approvals?application={application_id}",
        entity_type="compoff_application",
        entity_id=application_id,
    )


# --------------------------------------------------------------- attendance


def missing_punch(*, work_date: date, status: str) -> Draft:
    """A day that cannot be settled from the punches recorded.

    Deliberately not called an absence. The brief forbids treating a missing
    punch as absence automatically, and so does this text: it asks for a
    correction rather than announcing a consequence.
    """
    return Draft(
        category="attendance",
        title=f"Attendance needs attention for {work_date.isoformat()}",
        body=(
            f"This day is recorded as '{status.replace('_', ' ')}'. If you "
            "worked, raise a correction so it can be reviewed - it has not been "
            "treated as an absence."
        ),
        dedupe_key=f"attendance:{work_date.isoformat()}:needs_attention:{status}",
        link_path=f"/attendance?date={work_date.isoformat()}",
        priority="important",
        entity_type="attendance_day",
        entity_id=work_date.isoformat(),
    )


def regularization_decision(
    *,
    request_id: int,
    work_date: date,
    approved: bool,
    stage: str,
    comment: str | None = None,
) -> Draft:
    if approved:
        title = f"Attendance correction approved for {work_date.isoformat()}"
        body = "The day has been recomputed with the corrected times."
        priority = "normal"
    else:
        title = f"Attendance correction declined for {work_date.isoformat()}"
        body = "The day is unchanged."
        if comment:
            body += f" Reason given: {comment}"
        priority = "important"
    return Draft(
        category="regularization",
        title=title,
        body=body,
        dedupe_key=(
            f"regularization:{request_id}:{stage}:{'approved' if approved else 'rejected'}"
        ),
        link_path=f"/attendance/corrections/{request_id}",
        priority=priority,
        entity_type="attendance_regularization",
        entity_id=request_id,
    )


def regularization_awaiting_approval(
    *, request_id: int, applicant_name: str, work_date: date, stage: str
) -> Draft:
    return Draft(
        category="regularization",
        title=f"{applicant_name} raised an attendance correction",
        body=f"For {work_date.isoformat()}, awaiting your {_stage_label(stage)} decision.",
        dedupe_key=f"regularization:{request_id}:awaiting:{stage}",
        link_path=f"/attendance/corrections/{request_id}",
        entity_type="attendance_regularization",
        entity_id=request_id,
    )


# -------------------------------------------------------------------- shifts


def shift_changed(
    *, assignment_id: int, shift_name: str, effective_from: date, effective_to: date | None
) -> Draft:
    """A shift change, which people need to know before it starts.

    Keyed on the assignment, so re-saving the same assignment does not notify
    again, while a genuinely new assignment does.
    """
    span = (
        f"from {effective_from.isoformat()}"
        if effective_to is None
        else f"from {effective_from.isoformat()} to {effective_to.isoformat()}"
    )
    return Draft(
        category="shift",
        title=f"You are on the {shift_name} shift {span}",
        body=(
            "Your shift assignment has changed. Check the timings before your "
            "next working day, as late and early-exit marking follow the new "
            "shift."
        ),
        dedupe_key=f"shift_assignment:{assignment_id}:assigned",
        link_path="/attendance",
        priority="important",
        entity_type="shift_assignment",
        entity_id=assignment_id,
    )


# ------------------------------------------------------------------ holidays


def holiday_calendar_published(
    *, calendar_id: int, calendar_name: str, year: int, holiday_count: int
) -> Draft:
    return Draft(
        category="holiday",
        title=f"The {year} holiday list is published",
        body=(
            f"{calendar_name} lists {holiday_count} holiday(s). If it includes "
            "optional or restricted holidays, choose yours before taking them."
        ),
        dedupe_key=f"holiday_calendar:{calendar_id}:published",
        link_path="/holidays",
        entity_type="holiday_calendar",
        entity_id=calendar_id,
    )


# ------------------------------------------------------------------- payroll


def payslip_published(*, payslip_id: int, period_label: str) -> Draft:
    """Used by payroll when a payslip is released.

    Deliberately contains no figures: a notification is not a secure channel for
    pay, and the payslip itself is behind authentication.
    """
    return Draft(
        category="payroll",
        title=f"Your payslip for {period_label} is available",
        body="Sign in to view or download it.",
        dedupe_key=f"payslip:{payslip_id}:published",
        link_path="/payslips",
        entity_type="payslip",
        entity_id=payslip_id,
    )


# ------------------------------------------------------------------- account


def password_changed(*, user_id: int, changed_at_iso: str) -> Draft:
    """A security notice, keyed on the instant so every change is reported.

    The one case where the key legitimately contains a timestamp: each change is
    a distinct fact, and suppressing the second one would hide exactly the event
    somebody needs to see.
    """
    return Draft(
        category="account",
        title="Your password was changed",
        body=(
            "If this was not you, tell your administrator immediately. Existing "
            "sessions on other devices have been signed out."
        ),
        dedupe_key=f"account:{user_id}:password_changed:{changed_at_iso}",
        link_path="/profile",
        priority="critical",
        entity_type="user",
        entity_id=user_id,
    )
