"""Request and response models for compensatory off.

As with leave, day quantities are `Decimal` and serialise as JSON **strings**
(`"1.00"`). A JSON number is an IEEE double, and a comp-off balance made of half
days has to reconcile against its lots exactly.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DayPortion = Literal["full", "first_half", "second_half"]
CreditStatus = Literal["pending_verification", "available", "rejected", "expired", "reversed"]
ApplicationStatus = Literal[
    "pending_manager", "pending_hr", "approved", "rejected", "withdrawn", "cancelled"
]


class CreditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    earned_date: date
    attendance_day_id: int | None = Field(
        description=(
            "The attendance day that proves the qualifying work. Null only for an "
            "explicit manual grant."
        )
    )
    source: str
    day_type: str
    worked_minutes: int
    credit_days: Decimal
    allocated_days: Decimal = Field(description="How much of this lot has been spent.")
    expiry_date: date | None = Field(description="Null never expires.")
    status: str
    verified_at: datetime | None
    decision_note: str | None
    reversal_of_id: int | None
    created_at: datetime


class CompoffBalanceResponse(BaseModel):
    as_of: date
    available_days: Decimal = Field(
        description="Live, verified, unexpired, unallocated credit. What can be taken."
    )
    pending_verification_days: Decimal = Field(
        description=(
            "Earned but not yet verified, so not spendable. Kept separate so it is "
            "not mistaken for available."
        )
    )
    committed_days: Decimal = Field(description="Held by requests awaiting a decision.")
    expiring_soon_days: Decimal
    expiring_on: date | None
    expired_days: Decimal = Field(
        description=(
            "Past its date but not yet closed by the expiry job. Counted here rather "
            "than in available, so the figure does not lie between runs."
        )
    )


class ManualCreditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    earned_date: date
    credit_days: Decimal = Field(gt=0, le=2)
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Required. A credit with no attendance behind it is exactly what an "
            "auditor would question."
        ),
    )


class CreditDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: str | None = Field(default=None, max_length=2000)


class CreditReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class GrantRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    employee_id: int | None = None

    @model_validator(mode="after")
    def _ordered(self) -> GrantRunRequest:
        if self.end < self.start:
            raise ValueError("end must not precede start")
        return self


class GrantRunResponse(BaseModel):
    from_: str = Field(alias="from")
    to: str
    days_examined: int
    credits_created: int
    already_credited: int = Field(
        description=(
            "Days that already had a credit. A re-run reports these rather than crediting twice."
        )
    )
    not_qualifying: int
    days_credited: str
    problems: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ExpiryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None


class ExpiryRunResponse(BaseModel):
    as_of: str
    credits_expired: int
    days_lost: str
    employees_affected: int


class CompoffApplyRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "start_date": "2026-11-16",
                    "end_date": "2026-11-16",
                    "reason": "Taking the comp-off earned on the 8th.",
                }
            ]
        },
    )

    start_date: date
    end_date: date
    start_portion: DayPortion = "full"
    end_portion: DayPortion = Field(default="full", description="Ignored for a single-day request.")
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _ordered(self) -> CompoffApplyRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class CompoffApplyOnBehalfRequest(CompoffApplyRequest):
    employee_id: int


class CompoffDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    off_date: date
    portion: str
    day_fraction: Decimal
    day_type: str


class CompoffAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    credit_id: int
    days: Decimal
    allocated_at: datetime


class CompoffApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    stage: str
    decision: str
    decided_by_user_id: int | None
    decided_by_employee_id: int | None
    decided_at: datetime
    comment: str | None
    was_auto_skipped: bool


class CompoffApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    start_date: date
    end_date: date
    total_days: Decimal
    reason: str | None
    status: str
    current_stage: str | None
    escalated_from_missing_manager: bool
    decided_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    days: list[CompoffDayResponse] = Field(default_factory=list)
    allocations: list[CompoffAllocationResponse] = Field(
        default_factory=list,
        description=(
            "Which credit lots this request drew on. Recorded because each credit "
            "expires on its own date, so expiry cannot be computed without it."
        ),
    )
    approvals: list[CompoffApprovalResponse] = Field(default_factory=list)


class CompoffApplicationSummary(BaseModel):
    id: int
    employee_id: int
    employee_code: str | None = None
    employee_name: str | None = None
    start_date: date
    end_date: date
    total_days: Decimal
    status: str
    current_stage: str | None
    reason: str | None
    escalated_from_missing_manager: bool
    created_at: datetime


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    comment: str | None = Field(default=None, max_length=2000)


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
