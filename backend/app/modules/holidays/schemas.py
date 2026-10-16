"""Request and response models for holiday calendars."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HolidayType = Literal["public", "company", "optional", "restricted"]


class CalendarCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"code": "IN-2027", "name": "India 2027", "year": 2027},
                {
                    "code": "KA-2027",
                    "name": "Karnataka 2027 regional",
                    "year": 2027,
                    "work_location_id": 1,
                },
            ]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=2000, le=2100, description="The calendar year the list covers.")

    work_location_id: int | None = Field(
        default=None,
        description=(
            "Scope. Leave both scopes empty for a list that applies to everyone. "
            "Applicable calendars are unioned, so a regional list supplements the "
            "national one rather than replacing it."
        ),
    )
    employee_group_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class CalendarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    year: int
    work_location_id: int | None
    employee_group_id: int | None
    description: str | None
    is_active: bool
    is_published: bool = Field(
        description=(
            "An unpublished calendar affects nothing. Publishing marks its dates as "
            "non-working for everyone in scope, so it is a deliberate separate act."
        )
    )
    published_at: datetime | None
    holiday_count: int = 0
    electable_count: int = 0


class HolidayCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"holiday_date": "2027-01-26", "name": "Republic Day"},
                {
                    "holiday_date": "2027-03-29",
                    "name": "Holi",
                    "holiday_type": "restricted",
                    "region": "North India",
                },
            ]
        },
    )

    holiday_date: date
    name: str = Field(min_length=1, max_length=120)
    holiday_type: HolidayType = Field(
        default="public",
        description=(
            "'public' and 'company' apply automatically. 'optional' and "
            "'restricted' only apply to an employee who elected them."
        ),
    )
    is_paid: bool = True
    region: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class HolidayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    calendar_id: int
    holiday_date: date
    name: str
    holiday_type: str
    is_paid: bool
    region: str | None
    notes: str | None
    is_active: bool
    requires_election: bool


class ImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holiday_date: date
    name: str = Field(min_length=1, max_length=120)
    holiday_type: HolidayType = "public"
    region: str | None = Field(default=None, max_length=80)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[ImportRow] = Field(
        min_length=1,
        max_length=400,
        description=(
            "Keyed on the content, so importing the same list twice does nothing the second time."
        ),
    )


class ImportResponse(BaseModel):
    calendar_code: str
    rows_received: int
    created: int
    updated: int
    rejected: int
    already_imported: bool = Field(
        description="True when this exact list had already been imported."
    )
    problems: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-row problems. One bad date does not reject the whole list.",
    )


class UnpublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "Required. Withdrawing a published calendar turns non-working days back "
            "into working days for everyone in scope."
        ),
    )


class EmployeeHolidayEntry(BaseModel):
    """A holiday as it affects one employee."""

    holiday_id: int
    holiday_date: date
    name: str
    holiday_type: str
    region: str | None
    is_paid: bool
    requires_election: bool
    is_elected: bool
    applies: bool = Field(
        description=(
            "Whether this is actually a non-working day for this employee. An "
            "optional holiday they have not chosen is listed but does not apply."
        )
    )


class OptionalHolidayOption(BaseModel):
    holiday_id: int
    holiday_date: date
    name: str
    holiday_type: str
    region: str | None
    is_elected: bool
    has_passed: bool


class ElectionResponse(BaseModel):
    election_id: int
    status: str
    elected_at: datetime
    holiday_id: int
    holiday_date: date
    name: str
    holiday_type: str
    year: int


class ElectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holiday_id: int


class ElectionLimitsResponse(BaseModel):
    year: int
    max_elections: int
    elected_count: int
    remaining: int
