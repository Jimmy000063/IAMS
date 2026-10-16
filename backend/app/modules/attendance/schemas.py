"""Request and response models for the attendance module."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PunchType = Literal["in", "out", "auto"]
EventType = Literal["in", "out"]
AttendanceStatus = Literal[
    "present",
    "half_day",
    "absent",
    "on_leave",
    "holiday",
    "weekly_off",
    "incomplete",
    "missing_punch",
]


class PunchRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"punch_type": "auto"}, {"punch_type": "in"}]},
    )

    punch_type: PunchType = Field(
        default="auto",
        description=(
            "'auto' records whichever punch comes next, so the UI can offer one "
            "button. There is deliberately no timestamp field: the server "
            "generates it, because a browser clock can be set to any value."
        ),
    )


class PunchStateResponse(BaseModel):
    """What the punch control should show right now."""

    work_date: date
    timezone: str = Field(
        description="The employee's work-location timezone, used to date the punch."
    )
    server_time: datetime
    is_punched_in: bool
    next_punch: EventType
    session_count: int
    worked_minutes_so_far: int = Field(
        description="Includes time in the currently open session, if any."
    )
    shift_id: int | None
    shift_code: str | None
    shift_start: str
    shift_end: str
    has_shift_assigned: bool = Field(
        description=(
            "False means no shift assignment covers this date; company defaults "
            "were used and the day is flagged accordingly."
        )
    )
    status: str | None
    events: list[dict[str, Any]]


class PunchResponse(BaseModel):
    recorded: bool = Field(
        description=(
            "False when the punch was a duplicate within a few seconds. The "
            "request still succeeds and returns the unchanged state."
        )
    )
    event_type: str
    work_date: date
    day: AttendanceDayResponse
    message: str


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    started_at: datetime
    ended_at: datetime
    duration_minutes: int


class AttendanceDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    work_date: date
    day_type: str
    status: str

    shift_id: int | None
    first_in_at: datetime | None
    last_out_at: datetime | None

    worked_minutes: int
    break_minutes: int
    net_minutes: int
    late_minutes: int = Field(
        description=(
            "Measured lateness against the scheduled start, regardless of the "
            "grace period. Use `is_late` for whether it counts."
        )
    )
    early_exit_minutes: int
    overtime_minutes: int

    is_late: bool
    is_early_exit: bool
    has_missing_punch: bool

    payable_day_fraction: Decimal = Field(
        description="1.00 present, 0.50 half day, 0 otherwise. What payroll multiplies by."
    )
    leave_application_id: int | None
    is_manual_override: bool
    override_reason: str | None

    computed_at: datetime
    computed_from_event_count: int
    anomalies: list[str] | None = Field(
        default=None,
        description="Data problems found while computing, e.g. an unpaired punch.",
    )
    policy_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="The thresholds and rules that produced these figures.",
    )
    sessions: list[SessionResponse] = []


class AttendanceEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    event_type: str
    occurred_at: datetime
    work_date: date
    source: str
    timezone_used: str
    is_superseded: bool
    reason: str | None


class AttendanceSummaryResponse(BaseModel):
    from_: str = Field(alias="from")
    to: str
    days_recorded: int
    status_counts: dict[str, int]
    payable_days: str = Field(
        description="Exact decimal as a string; a float would reintroduce rounding error."
    )
    worked_minutes: int
    overtime_minutes: int
    late_days: int
    early_exit_days: int
    missing_punch_days: int
    days_with_anomalies: int

    model_config = ConfigDict(populate_by_name=True)


class ManualEventRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "employee_id": 7,
                    "event_type": "in",
                    "local_datetime": "2026-11-03T09:15:00",
                    "reason": "Badge reader was offline; arrival confirmed by security log",
                }
            ]
        },
    )

    employee_id: int
    event_type: EventType
    local_datetime: datetime = Field(
        description=(
            "Wall-clock time in the employee's work-location timezone, with no "
            "offset - how HR thinks about it. It is anchored to that zone server-side."
        )
    )
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _must_be_naive(self) -> ManualEventRequest:
        if self.local_datetime.tzinfo is not None:
            raise ValueError(
                "Send local wall-clock time without an offset; the employee's "
                "work-location timezone is applied server-side."
            )
        return self


class SupersedeEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class OverrideDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    work_date: date
    status: AttendanceStatus
    payable_day_fraction: Decimal = Field(ge=0, le=1, decimal_places=2)
    reason: str = Field(min_length=1, max_length=2000)


class UnsettledNotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_ids: list[int] | None = Field(
        default=None, description="Null examines every employee in the range."
    )
    start: date
    end: date


class UnsettledNotifyResponse(BaseModel):
    from_date: str = Field(alias="from")
    to: str
    days_examined: int
    notifications_created: int
    already_notified: int = Field(
        description="Days whose notification already existed, so nothing was written."
    )

    model_config = ConfigDict(populate_by_name=True)


class RecomputeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_ids: list[int] | None = Field(
        default=None, description="Null recomputes every employee in the range."
    )
    start: date
    end: date
    force_over_manual_override: bool = Field(
        default=False,
        description="Overwrite days HR has pinned. Recorded in the audit log.",
    )
    respect_lock: bool = Field(
        default=True,
        description=(
            "Honour `attendance.recompute_locked_after_days`, which prevents a "
            "policy change from silently rewriting old attendance."
        ),
    )


class RecomputeResponse(BaseModel):
    employees: int
    from_: str = Field(alias="from")
    to: str
    days_computed: int
    skipped_locked: int
    skipped_not_employed: int
    preserved_manual_overrides: int
    lock_cutoff: str | None

    model_config = ConfigDict(populate_by_name=True)


class RegularizationRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "work_date": "2026-11-03",
                    "local_in": "2026-11-03T09:10:00",
                    "local_out": "2026-11-03T18:40:00",
                    "reason": "Forgot to punch out; worked until 18:40",
                }
            ]
        },
    )

    work_date: date
    local_in: datetime | None = Field(
        default=None, description="Corrected punch-in, local wall-clock time, no offset."
    )
    local_out: datetime | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _validate(self) -> RegularizationRequest:
        if self.local_in is None and self.local_out is None:
            raise ValueError("Provide a punch-in time, a punch-out time, or both.")
        for value in (self.local_in, self.local_out):
            if value is not None and value.tzinfo is not None:
                raise ValueError(
                    "Send local wall-clock time without an offset; the employee's "
                    "work-location timezone is applied server-side."
                )
        return self


class RegularizationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    comment: str | None = Field(default=None, max_length=2000)


class RegularizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    employee_code: str | None = None
    employee_name: str | None = None
    work_date: date
    requested_in_at: datetime | None
    requested_out_at: datetime | None
    reason: str
    status: str
    current_stage: str
    status_before: str | None

    manager_decision: str | None
    manager_comment: str | None
    manager_decided_at: datetime | None
    hr_decision: str | None
    hr_comment: str | None
    hr_decided_at: datetime | None
    escalated_from_missing_manager: bool = Field(
        description=(
            "True when the manager stage was skipped because the employee had no "
            "active reporting manager."
        )
    )
    created_at: datetime


class TodayOverviewResponse(BaseModel):
    date: str
    active_employees: int
    status_counts: dict[str, int]
    present: int
    half_day: int
    absent: int
    on_leave: int
    weekly_off: int
    holiday: int
    incomplete: int
    late_arrivals: int
    missing_punches: int
    pending_regularizations: int
    not_yet_computed: int = Field(
        description=(
            "Active employees with no attendance row for today yet - not the same as absent."
        )
    )
