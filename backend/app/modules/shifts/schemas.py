"""Request and response models for shifts and work calendars."""

from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ExceptionType = Literal["non_working", "working"]
WeekdayKey = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ShiftCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "code": "GEN",
                    "name": "General shift",
                    "start_time": "09:30",
                    "end_time": "18:30",
                    "break_minutes": 60,
                },
                {
                    "code": "NIGHT",
                    "name": "Night shift",
                    "start_time": "22:00",
                    "end_time": "06:00",
                    "break_minutes": 45,
                    "description": "Crosses midnight; punches are attributed to the start date.",
                },
            ]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    start_time: time
    end_time: time = Field(
        description=(
            "If this is at or before the start time, the shift is treated as crossing midnight."
        )
    )
    break_minutes: int = Field(default=0, ge=0, lt=1440)

    grace_late_minutes: int | None = Field(
        default=None, ge=0, le=240, description="Null inherits the company setting."
    )
    grace_early_exit_minutes: int | None = Field(default=None, ge=0, le=240)
    min_minutes_full_day: int | None = Field(default=None, ge=1, le=1440)
    min_minutes_half_day: int | None = Field(default=None, ge=1, le=1440)

    early_punch_window_minutes: int = Field(
        default=120,
        ge=0,
        le=720,
        description="How long before the shift start a punch still belongs to it.",
    )
    late_punch_window_minutes: int = Field(
        default=300,
        ge=0,
        le=720,
        description="How long after the shift end a punch still belongs to it.",
    )

    work_location_id: int | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> ShiftCreate:
        if (
            self.min_minutes_full_day is not None
            and self.min_minutes_half_day is not None
            and self.min_minutes_half_day > self.min_minutes_full_day
        ):
            raise ValueError("The half-day threshold cannot be higher than the full-day threshold.")
        return self


class ShiftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(default=None, ge=0, lt=1440)
    grace_late_minutes: int | None = Field(default=None, ge=0, le=240)
    grace_early_exit_minutes: int | None = Field(default=None, ge=0, le=240)
    min_minutes_full_day: int | None = Field(default=None, ge=1, le=1440)
    min_minutes_half_day: int | None = Field(default=None, ge=1, le=1440)
    early_punch_window_minutes: int | None = Field(default=None, ge=0, le=720)
    late_punch_window_minutes: int | None = Field(default=None, ge=0, le=720)
    work_location_id: int | None = None
    is_active: bool | None = None


class ShiftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    start_time: time
    end_time: time
    crosses_midnight: bool
    break_minutes: int
    scheduled_minutes: int = Field(
        description="Paid scheduled duration: the shift span minus the planned break."
    )
    grace_late_minutes: int | None
    grace_early_exit_minutes: int | None
    min_minutes_full_day: int | None
    min_minutes_half_day: int | None
    early_punch_window_minutes: int
    late_punch_window_minutes: int
    work_location_id: int | None
    is_active: bool


class WeeklyOffPatternCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"code": "MONFRI", "name": "Monday to Friday", "off_days": ["sat", "sun"]}]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    off_days: list[WeekdayKey] = Field(
        default_factory=lambda: ["sat", "sun"],
        description="Weekdays that are non-working. At least one day must remain working.",
    )
    is_active: bool = True


class WeeklyOffPatternResponse(BaseModel):
    id: int
    code: str
    name: str
    off_days: list[str]
    is_active: bool


class WorkCalendarCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    weekly_off_pattern_id: int
    work_location_id: int | None = Field(
        default=None, description="Null makes this a company-wide calendar."
    )
    is_default: bool = Field(
        default=False,
        description=(
            "The fallback when an employee's location has no calendar. Setting "
            "this clears the flag on any other calendar."
        ),
    )
    is_active: bool = True


class WorkCalendarResponse(BaseModel):
    id: int
    code: str
    name: str
    weekly_off_pattern_id: int
    weekly_off_pattern_code: str
    off_days: list[str]
    work_location_id: int | None
    is_default: bool
    is_active: bool
    exception_count: int = 0


class CalendarExceptionCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "exception_date": "2026-12-25",
                    "exception_type": "non_working",
                    "reason": "Company closure",
                },
                {
                    "exception_date": "2026-12-27",
                    "exception_type": "working",
                    "reason": "Working Saturday to compensate",
                },
            ]
        },
    )

    exception_date: date
    exception_type: ExceptionType = Field(
        description=(
            "'non_working' closes a working day; 'working' opens a weekend or "
            "holiday. A 'working' exception takes precedence over everything else."
        )
    )
    reason: str = Field(min_length=1, max_length=200)


class CalendarExceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calendar_id: int
    exception_date: date
    exception_type: str
    reason: str


class ShiftAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    shift_id: int
    effective_from: date
    effective_to: date | None = Field(
        default=None, description="Null leaves the assignment open-ended."
    )
    work_calendar_id: int | None = Field(
        default=None,
        description="Overrides the location's calendar for this employee and period.",
    )
    notes: str | None = Field(default=None, max_length=2000)


class ShiftAssignmentCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_to: date


class ShiftAssignmentResponse(BaseModel):
    id: int
    employee_id: int
    shift_id: int
    shift_code: str
    shift_name: str
    effective_from: date
    effective_to: date | None
    work_calendar_id: int | None
    notes: str | None
    is_current: bool


class DayTypeResponse(BaseModel):
    """Resolved calendar classification for a date, with the reason."""

    work_date: date
    day_type: str
    source: str = Field(
        description=(
            "Which rule decided it: 'calendar_exception', 'holiday', "
            "'weekly_off_pattern' or 'default'."
        )
    )
    reason: str | None = None
