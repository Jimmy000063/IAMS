"""Request and response models for the organisation module."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DepartmentCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30, examples=["ENG"])
    name: str = Field(min_length=1, max_length=120, examples=["Engineering"])
    description: str | None = Field(default=None, max_length=2000)
    parent_id: int | None = None
    is_active: bool = True


class DepartmentUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: int | None = None
    head_employee_id: int | None = None
    is_active: bool | None = None


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    parent_id: int | None
    head_employee_id: int | None
    is_active: bool


class DesignationCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30, examples=["SE2"])
    name: str = Field(min_length=1, max_length=120, examples=["Software Engineer II"])
    description: str | None = Field(default=None, max_length=2000)
    level: int | None = Field(default=None, ge=0, le=100)
    is_active: bool = True


class DesignationUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    level: int | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class DesignationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    level: int | None
    is_active: bool


class WorkLocationCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "BLR",
                    "name": "Bengaluru Office",
                    "timezone": "Asia/Kolkata",
                    "city": "Bengaluru",
                    "state": "Karnataka",
                }
            ]
        }
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "IANA timezone name. Attendance dates for employees at this location "
            "are derived in this zone, so it must be correct."
        ),
    )
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(
        default=None,
        max_length=80,
        description="Relevant to professional tax, which is levied per state.",
    )
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str = Field(default="IN", min_length=2, max_length=2)
    is_active: bool = True


class WorkLocationUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=30)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    is_active: bool | None = None


class WorkLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    timezone: str
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country_code: str
    is_active: bool


class EmployeeGroupCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class EmployeeGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_active: bool
    member_count: int = 0


class GroupMembersRequest(BaseModel):
    employee_ids: list[int] = Field(
        description="Replaces the group's membership entirely.", max_length=5000
    )
