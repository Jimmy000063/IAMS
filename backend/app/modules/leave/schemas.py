"""Request and response models for leave.

Quantities are `Decimal`, which Pydantic serialises as a JSON **string**
(`"2.00"`, not `2.0`). That is deliberate and matches the rest of the project: a
JSON number is an IEEE double, and a leave balance that has had thirty movements
applied to it must still reconcile exactly. Clients parse these as decimals, not
floats.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DayPortion = Literal["full", "first_half", "second_half"]
AccrualFrequency = Literal["none", "monthly", "quarterly", "half_yearly", "annual"]
RoundingMode = Literal["none", "nearest_half", "nearest_full", "down_half", "up_half"]
GenderRestriction = Literal["any", "female", "male"]
ApplicationStatus = Literal[
    "pending_manager", "pending_hr", "approved", "rejected", "withdrawn", "cancelled"
]


# --------------------------------------------------------------- leave types


class LeaveTypeCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "code": "CL",
                    "name": "Casual leave",
                    "is_paid": True,
                    "allows_half_day": True,
                    "requires_reason": True,
                },
                {
                    "code": "LWP",
                    "name": "Leave without pay",
                    "is_paid": False,
                    "deducts_balance": False,
                    "requires_reason": True,
                },
            ]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    is_paid: bool = Field(
        default=True,
        description="Whether a day of this leave is a payable day. Drives payroll.",
    )
    deducts_balance: bool = Field(
        default=True,
        description=(
            "False for a type with no quota, such as leave without pay: the "
            "application is still recorded and approved, but nothing is deducted."
        ),
    )
    allows_half_day: bool = True
    allows_negative_balance: bool | None = Field(
        default=None,
        description="Null inherits the company setting; set to override for this type.",
    )
    counts_non_working_days: bool = Field(
        default=False,
        description=(
            "Whether weekly offs and holidays inside the range are consumed. Some "
            "statutory types are counted in calendar days; ordinary leave usually "
            "is not. This has a direct payroll consequence, so it is not assumed."
        ),
    )
    requires_reason: bool = True
    attachment_required_above_days: Decimal | None = Field(
        default=None, ge=0, le=999, description="Null means a document is never required."
    )
    min_notice_days: int = Field(default=0, ge=0, le=365)
    max_consecutive_days: int | None = Field(default=None, ge=1, le=999)
    min_service_days: int = Field(default=0, ge=0, le=3650)
    gender_restriction: GenderRestriction = Field(
        default="any",
        description="Only for types whose statutory basis is gender-specific.",
    )
    requires_legal_review: bool = Field(
        default=False,
        description=(
            "Marks a type whose entitlement comes from statute and has not been "
            "verified against a legal reference here."
        ),
    )
    display_order: int = Field(default=100, ge=0, le=9999)
    colour: str | None = Field(default=None, max_length=20)
    is_active: bool = True


class LeaveTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_paid: bool | None = None
    deducts_balance: bool | None = None
    allows_half_day: bool | None = None
    allows_negative_balance: bool | None = None
    counts_non_working_days: bool | None = None
    requires_reason: bool | None = None
    attachment_required_above_days: Decimal | None = Field(default=None, ge=0, le=999)
    min_notice_days: int | None = Field(default=None, ge=0, le=365)
    max_consecutive_days: int | None = Field(default=None, ge=1, le=999)
    min_service_days: int | None = Field(default=None, ge=0, le=3650)
    gender_restriction: GenderRestriction | None = None
    requires_legal_review: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=9999)
    colour: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class LeaveTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_paid: bool
    deducts_balance: bool
    allows_half_day: bool
    allows_negative_balance: bool | None
    counts_non_working_days: bool
    requires_reason: bool
    attachment_required_above_days: Decimal | None
    min_notice_days: int
    max_consecutive_days: int | None
    min_service_days: int
    gender_restriction: str
    requires_legal_review: bool
    display_order: int
    colour: str | None
    is_active: bool


# ------------------------------------------------------------------ policies


class LeavePolicyCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "leave_type_id": 1,
                    "name": "Casual leave 2026-27",
                    "effective_from": "2026-04-01",
                    "annual_quota_days": "12.00",
                    "accrual_frequency": "monthly",
                    "accrual_rounding": "nearest_half",
                    "prorate_on_joining": True,
                }
            ]
        },
    )

    leave_type_id: int
    name: str = Field(min_length=1, max_length=120)
    effective_from: date = Field(
        description=(
            "Policies are effective-dated, not edited. To change entitlement from "
            "a date, create a policy from that date and close the previous one."
        )
    )
    effective_to: date | None = None

    annual_quota_days: Decimal = Field(default=Decimal("0"), ge=0, le=999)
    accrual_frequency: AccrualFrequency = Field(
        default="annual",
        description=(
            "Periods are anchored to the leave year start, not the calendar - a "
            "year starting in April has quarters running April to June."
        ),
    )
    accrual_rounding: RoundingMode = Field(
        default="nearest_half",
        description="Applied once per period, never to a running total.",
    )

    prorate_on_joining: bool = Field(
        default=True,
        description=(
            "Whether a mid-period joiner accrues a part period. Without it, "
            "someone joining on the 28th gets the same credit as someone who "
            "worked the whole month."
        ),
    )
    prorate_on_exit: bool = True
    accrue_from_confirmation: bool = Field(
        default=False, description="Start accruing only after probation ends."
    )

    max_accumulation_days: Decimal | None = Field(
        default=None, ge=0, le=999, description="Ceiling on the balance; accrual stops there."
    )
    carry_forward_cap_days: Decimal | None = Field(default=None, ge=0, le=999)
    carry_forward_expires_after_months: int | None = Field(default=None, ge=1, le=120)
    encashment_allowed: bool = False
    encashment_max_days: Decimal | None = Field(default=None, ge=0, le=999)

    notes: str | None = Field(default=None, max_length=2000)
    requires_legal_review: bool = Field(
        default=False,
        description="Set when the quota comes from statute rather than company choice.",
    )

    @model_validator(mode="after")
    def _check_period(self) -> LeavePolicyCreate:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        return self


class LeavePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    leave_type_id: int
    name: str
    effective_from: date
    effective_to: date | None
    annual_quota_days: Decimal
    accrual_frequency: str
    accrual_rounding: str
    prorate_on_joining: bool
    prorate_on_exit: bool
    accrue_from_confirmation: bool
    max_accumulation_days: Decimal | None
    carry_forward_cap_days: Decimal | None
    carry_forward_expires_after_months: int | None
    encashment_allowed: bool
    encashment_max_days: Decimal | None
    notes: str | None
    requires_legal_review: bool


class ClosePeriodRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_to: date = Field(description="The last date this remains in force.")


class PolicyAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    leave_policy_id: int
    effective_from: date
    effective_to: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class PolicyAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type_id: int
    leave_policy_id: int
    effective_from: date
    effective_to: date | None
    notes: str | None


# -------------------------------------------------------------- applications


class LeaveApplyRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "leave_type_id": 1,
                    "start_date": "2026-11-16",
                    "end_date": "2026-11-18",
                    "reason": "Family function.",
                },
                {
                    "leave_type_id": 1,
                    "start_date": "2026-11-16",
                    "end_date": "2026-11-16",
                    "start_portion": "second_half",
                    "reason": "Medical appointment.",
                },
            ]
        },
    )

    leave_type_id: int
    start_date: date
    end_date: date
    start_portion: DayPortion = Field(
        default="full",
        description=(
            "Half-day portions apply to the first and last day of the range only; "
            "a middle day of a multi-day absence is a full day."
        ),
    )
    end_portion: DayPortion = Field(
        default="full",
        description="Ignored for a single-day application, which takes start_portion.",
    )
    reason: str | None = Field(default=None, max_length=2000)
    contact_during_leave: str | None = Field(default=None, max_length=120)
    attachment_file_id: int | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> LeaveApplyRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class LeaveApplyOnBehalfRequest(LeaveApplyRequest):
    """HR recording leave for an employee who could not apply themselves.

    Flagged on the application rather than disguised as self-service, so the
    record shows who actually raised it.
    """

    employee_id: int


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Recorded against this stage and never overwritten.",
    )


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Required. Cancelling approved leave reverses a ledger entry and "
            "recomputes attendance, so the record needs to say why."
        ),
    )


class LeaveDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    leave_date: date
    portion: str
    day_fraction: Decimal
    is_paid: bool
    day_type: str
    counts_against_balance: bool


class LeaveApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    stage: str
    decision: str
    decided_by_user_id: int | None
    decided_by_employee_id: int | None
    decided_at: datetime
    comment: str | None
    was_auto_skipped: bool


class LeaveApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    leave_type_id: int
    leave_policy_id: int | None
    start_date: date
    end_date: date
    total_days: Decimal
    paid_days: Decimal
    unpaid_days: Decimal
    reason: str | None
    contact_during_leave: str | None
    status: str
    current_stage: str | None
    escalated_from_missing_manager: bool
    is_backdated: bool
    applied_on_behalf: bool
    decided_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    supersedes_application_id: int | None
    attachment_file_id: int | None
    policy_snapshot: dict[str, Any] | None
    created_at: datetime
    days: list[LeaveDayResponse] = Field(default_factory=list)
    approvals: list[LeaveApprovalResponse] = Field(default_factory=list)


class LeaveApplicationSummary(BaseModel):
    """The list view: enough to decide, without the day-by-day detail."""

    id: int
    employee_id: int
    employee_code: str | None = None
    employee_name: str | None = None
    leave_type_id: int
    leave_type_code: str | None = None
    leave_type_name: str | None = None
    start_date: date
    end_date: date
    total_days: Decimal
    paid_days: Decimal
    unpaid_days: Decimal
    status: str
    current_stage: str | None
    reason: str | None
    is_backdated: bool
    escalated_from_missing_manager: bool
    applied_on_behalf: bool
    created_at: datetime


# ----------------------------------------------------------------- balances


class LeaveBalanceResponse(BaseModel):
    leave_type_id: int
    leave_type_code: str
    leave_type_name: str
    colour: str | None
    is_paid: bool
    deducts_balance: bool
    allows_half_day: bool
    counts_non_working_days: bool = Field(
        description=(
            "Whether weekly offs and holidays inside a range are consumed. Shown "
            "to the applicant, because it changes what a Friday-to-Monday absence "
            "costs."
        )
    )
    requires_reason: bool
    requires_legal_review: bool = Field(
        description=(
            "True when this entitlement comes from statute and has not been "
            "verified against a legal reference in this system."
        )
    )

    leave_year: int
    leave_year_start: date
    leave_year_end: date
    annual_quota_days: Decimal | None
    accrual_frequency: str | None
    has_policy: bool = Field(
        description="False means no entitlement is configured, so nothing can be drawn."
    )

    balance_days: Decimal = Field(description="The ledger sum. The authoritative figure.")
    pending_days: Decimal = Field(description="Committed by applications awaiting a decision.")
    committed_days: Decimal = Field(
        description=(
            "Already approved but dated after the as-of date - leave booked for "
            "next month. It has not changed the as-of ledger total, but it is a "
            "genuine commitment."
        )
    )
    available_days: Decimal = Field(
        description=(
            "balance minus pending minus committed. The only figure a new "
            "application is checked against."
        )
    )
    accrued_days: Decimal
    used_days: Decimal
    expiring_days: Decimal
    expiring_on: date | None


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    leave_type_id: int
    txn_type: str
    days: Decimal = Field(description="Signed: positive credits, negative consumes.")
    effective_date: date
    leave_year: int
    expires_on: date | None
    application_id: int | None
    reverses_entry_id: int | None
    reason: str | None
    created_at: datetime


class AdjustmentRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "employee_id": 12,
                    "leave_type_id": 1,
                    "days": "2.5",
                    "effective_date": "2026-04-01",
                    "reason": "Opening balance correction agreed with the employee.",
                    "token": "2026-04-01-correction-1",
                }
            ]
        },
    )

    employee_id: int
    leave_type_id: int
    days: Decimal = Field(description="Signed. Positive credits the balance, negative reduces it.")
    effective_date: date
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Required. An unexplained adjustment is exactly what the ledger exists to prevent."
        ),
    )
    token: str = Field(
        min_length=1,
        max_length=80,
        description=(
            "Idempotency token from the client. Makes a double-submitted form "
            "harmless while still allowing two genuine corrections of the same size."
        ),
    )

    @model_validator(mode="after")
    def _non_zero(self) -> AdjustmentRequest:
        if self.days == 0:
            raise ValueError("days must not be zero")
        return self


class OpeningBalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    leave_type_id: int
    days: Decimal = Field(gt=0, le=999)
    leave_year: int | None = Field(default=None, description="Defaults to the current leave year.")
    reason: str = Field(default="Opening balance", max_length=2000)


class AccrualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = Field(
        default=None,
        description=(
            "The accrual period containing this date is credited. Pass a date "
            "inside a past period to catch up a missed run."
        ),
    )
    leave_type_id: int | None = None
    employee_id: int | None = None


class AccrualRunResponse(BaseModel):
    as_of: str
    leave_year: int
    period_key: str
    employees_considered: int
    entries_posted: int
    already_posted: int = Field(
        description=(
            "Entries rejected by the idempotency key, i.e. already credited. A "
            "re-run reports these rather than crediting twice."
        )
    )
    skipped_no_policy: int
    skipped_not_employed: int
    skipped_at_ceiling: int
    days_credited: str
    problems: list[dict[str, Any]] = Field(default_factory=list)


class LeavePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leave_type_id: int
    start_date: date
    end_date: date
    start_portion: DayPortion = "full"
    end_portion: DayPortion = "full"
    reason: str | None = Field(default=None, max_length=2000)
    employee_id: int | None = Field(
        default=None,
        description="HR previewing on an employee's behalf. Omit for yourself.",
    )


class LeavePreviewResponse(BaseModel):
    """What an application would cost, computed by the same code that applies it.

    Exists so the form can show "2 days, the weekend is not deducted" before
    submission without the browser reimplementing the calendar and sandwich
    rules - a duplicate that would drift and mislead the applicant.
    """

    leave_type_code: str
    total_days: Decimal
    paid_days: Decimal
    unpaid_days: Decimal
    span_days: int
    available_days: Decimal
    split_reason: str
    notes: list[str]
    days: list[LeaveDayResponse]
    problems: list[dict[str, Any]] = Field(
        description="Every reason it would be refused, not just the first."
    )
    can_submit: bool


class YearEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leave_year: int = Field(
        ge=2000,
        le=2100,
        description="The year being closed, labelled by the calendar year it started in.",
    )
    leave_type_id: int | None = None
    employee_id: int | None = None
    dry_run: bool = Field(
        default=True,
        description=(
            "Defaults to true. Closing a year moves everybody's entitlement at "
            "once, so the default is to show the outcome rather than apply it."
        ),
    )


class ExpireCarriedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None


class LeaveCalendarEntry(BaseModel):
    """One person's absence, for the team calendar."""

    employee_id: int
    employee_code: str
    employee_name: str
    leave_date: date
    portion: str
    leave_type_code: str
    leave_type_name: str
    colour: str | None
    status: str
