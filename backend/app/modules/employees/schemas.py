"""Request and response models for the employees module.

Three response shapes, matching the three access tiers:

* :class:`EmployeeSummary`  - directory listing.
* :class:`EmployeeDetail`   - full profile, no restricted fields.
* :class:`EmployeeSensitive`- statutory and bank data, masked even here.

Keeping them as separate models is what stops a restricted field leaking into a
listing response by accident: it is not a field the model has.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.employees.models import (
    DOCUMENT_VISIBILITIES,
    EMPLOYMENT_STATUSES,
    EMPLOYMENT_TYPES,
    EXIT_TYPES,
    GENDERS,
    MARITAL_STATUSES,
)

EmploymentType = Literal["full_time", "part_time", "contract", "intern", "consultant"]
EmploymentStatus = Literal["probation", "active", "notice_period", "suspended", "exited"]
ExitType = Literal["resignation", "termination", "retirement", "end_of_contract", "other"]
Gender = Literal["female", "male", "other", "not_disclosed"]
MaritalStatus = Literal["single", "married", "other", "not_disclosed"]
DocumentVisibility = Literal["employee_visible", "hr_only"]


def _check_vocabularies_match() -> None:
    """Keep the API's Literal unions in step with the database vocabularies.

    A mismatch means the API accepts a value the check constraint then rejects
    with an opaque conflict, so this fails loudly at import time instead. Not an
    ``assert``, because assertions are stripped under ``python -O``.
    """
    pairs = (
        ("employment_type", EMPLOYMENT_TYPES, get_args(EmploymentType)),
        ("employment_status", EMPLOYMENT_STATUSES, get_args(EmploymentStatus)),
        ("exit_type", EXIT_TYPES, get_args(ExitType)),
        ("gender", GENDERS, get_args(Gender)),
        ("marital_status", MARITAL_STATUSES, get_args(MaritalStatus)),
        ("document_visibility", DOCUMENT_VISIBILITIES, get_args(DocumentVisibility)),
    )
    for name, model_values, schema_values in pairs:
        if set(model_values) != set(schema_values):
            raise RuntimeError(
                f"{name}: models declare {sorted(model_values)} but schemas declare "
                f"{sorted(schema_values)}. Update both and add a migration."
            )


_check_vocabularies_match()


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(
        # A mistyped field name must fail, not be quietly dropped from a new
        # employee's master record.
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "employee_code": "ITC0007",
                    "first_name": "Priya",
                    "last_name": "Nair",
                    "work_email": "priya.nair@example.com",
                    "employment_type": "full_time",
                    "employment_status": "probation",
                    "date_of_joining": "2026-11-03",
                    "department_id": 1,
                    "designation_id": 2,
                    "work_location_id": 1,
                }
            ]
        },
    )

    employee_code: str = Field(min_length=2, max_length=30)
    first_name: str = Field(min_length=1, max_length=80)
    middle_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)

    date_of_birth: date | None = None
    gender: Gender | None = None
    marital_status: MaritalStatus | None = None

    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    work_phone: str | None = Field(default=None, max_length=25)
    personal_phone: str | None = Field(default=None, max_length=25)

    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=25)
    emergency_contact_relation: str | None = Field(default=None, max_length=50)

    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)

    department_id: int | None = None
    designation_id: int | None = None
    work_location_id: int | None = None
    reporting_manager_id: int | None = None

    employment_type: EmploymentType
    employment_status: EmploymentStatus = "active"
    date_of_joining: date
    probation_end_date: date | None = None
    date_of_confirmation: date | None = None

    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("employment_status")
    @classmethod
    def _not_born_exited(cls, value: str) -> str:
        """An employee cannot be created already exited.

        Doing so would leave no employment history for the period they worked,
        which attendance and payroll reports depend on. Create them active, then
        record the exit.
        """
        if value == "exited":
            raise ValueError(
                "Create the employee as active and then record their exit, so the "
                "employment history is complete."
            )
        return value


class EmployeeUpdate(BaseModel):
    """All fields optional; only those supplied are changed.

    Unknown fields are rejected rather than ignored: a mistyped field name in an
    HR update would otherwise look like it succeeded while changing nothing.
    """

    model_config = ConfigDict(extra="forbid")

    employee_code: str | None = Field(default=None, min_length=2, max_length=30)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    middle_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)

    date_of_birth: date | None = None
    gender: Gender | None = None
    marital_status: MaritalStatus | None = None

    work_email: EmailStr | None = None
    personal_email: EmailStr | None = None
    work_phone: str | None = Field(default=None, max_length=25)
    personal_phone: str | None = Field(default=None, max_length=25)

    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=25)
    emergency_contact_relation: str | None = Field(default=None, max_length=50)

    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)

    department_id: int | None = None
    designation_id: int | None = None
    work_location_id: int | None = None
    reporting_manager_id: int | None = None

    employment_type: EmploymentType | None = None
    employment_status: EmploymentStatus | None = None
    date_of_joining: date | None = None
    probation_end_date: date | None = None
    date_of_confirmation: date | None = None

    notes: str | None = Field(default=None, max_length=4000)

    effective_from: date | None = Field(
        default=None,
        description=(
            "Date the change takes effect, used to open the new employment "
            "history period. Defaults to today."
        ),
    )
    change_reason: str | None = Field(default=None, max_length=1000)


class SelfProfileUpdate(BaseModel):
    """The only fields an employee may change about themselves.

    ``extra="forbid"`` matters here. Pydantic's default is to ignore unknown
    fields, so an employee sending ``{"employment_type": "consultant"}`` would
    get a 200 and a response that looks successful while the field was silently
    dropped - leaving them to believe the change applied. Rejecting the request
    outright is the honest answer, and it makes the privilege boundary explicit
    rather than incidental.
    """

    model_config = ConfigDict(extra="forbid")

    personal_email: EmailStr | None = None
    personal_phone: str | None = Field(default=None, max_length=25)
    emergency_contact_name: str | None = Field(default=None, max_length=120)
    emergency_contact_phone: str | None = Field(default=None, max_length=25)
    emergency_contact_relation: str | None = Field(default=None, max_length=50)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)
    marital_status: MaritalStatus | None = None


class EmployeeSummary(BaseModel):
    """Directory row. No contact detail beyond the work address."""

    id: int
    employee_code: str
    full_name: str
    work_email: str | None
    department_id: int | None
    department_name: str | None = None
    designation_id: int | None
    designation_name: str | None = None
    work_location_id: int | None
    work_location_name: str | None = None
    reporting_manager_id: int | None
    employment_type: str
    employment_status: str
    date_of_joining: date
    date_of_exit: date | None


class EmployeeDetail(EmployeeSummary):
    """Full profile. Statutory and bank data are in a separate response."""

    middle_name: str | None
    first_name: str
    last_name: str | None
    date_of_birth: date | None
    gender: str | None
    marital_status: str | None
    personal_email: str | None
    work_phone: str | None
    personal_phone: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    emergency_contact_relation: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    probation_end_date: date | None
    date_of_confirmation: date | None
    exit_type: str | None
    exit_reason: str | None
    last_working_day: date | None
    notes: str | None
    reporting_manager_name: str | None = None
    reporting_manager_code: str | None = None
    work_location_timezone: str | None = None
    direct_report_count: int = 0
    linked_user_id: int | None = None
    has_statutory_details: bool = False
    has_bank_account: bool = False
    created_at: datetime
    updated_at: datetime


class EmploymentHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    effective_from: date
    effective_to: date | None
    department_id: int | None
    designation_id: int | None
    work_location_id: int | None
    reporting_manager_id: int | None
    employment_type: str | None
    employment_status: str | None
    change_type: str
    change_reason: str | None


class StatutoryDetailsUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "pf_uan": "100123456789",
                    "pan": "ABCDE1234F",
                    "aadhaar_last4": "6789",
                    "pf_opted_in": True,
                }
            ]
        }
    )

    pf_uan: str | None = Field(default=None, max_length=20, description="12 digits")
    pf_number: str | None = Field(default=None, max_length=40)
    esi_number: str | None = Field(default=None, max_length=20)
    pan: str | None = Field(
        default=None,
        max_length=10,
        description="Stored encrypted; only the last four digits are returned.",
    )
    aadhaar_last4: str | None = Field(
        default=None,
        max_length=4,
        description=(
            "Last four digits only. This system does not store full Aadhaar "
            "numbers, and submitting one is rejected."
        ),
    )
    pf_opted_in: bool | None = Field(
        default=None,
        description="Null means not yet determined; payroll flags rather than assumes.",
    )
    esi_eligible_override: bool | None = None
    pt_state: str | None = Field(
        default=None, max_length=80, description="Defaults from the work location when unset."
    )
    tax_regime: str | None = Field(
        default=None, max_length=20, description="Null until the employee declares."
    )


class StatutoryDetailsResponse(BaseModel):
    """Restricted response. PAN is returned masked, never in full."""

    employee_id: int
    pf_uan: str | None
    pf_number: str | None
    esi_number: str | None
    pan_masked: str | None = Field(description="e.g. XXXXXX234F")
    aadhaar_last4: str | None
    pf_opted_in: bool | None
    esi_eligible_override: bool | None
    pt_state: str | None
    tax_regime: str | None


class BankAccountCreate(BaseModel):
    account_holder_name: str = Field(min_length=1, max_length=120)
    account_number: str = Field(
        min_length=6, max_length=30, description="Stored encrypted at rest."
    )
    ifsc_code: str = Field(min_length=11, max_length=11)
    bank_name: str = Field(min_length=1, max_length=120)
    branch_name: str | None = Field(default=None, max_length=120)
    account_type: str | None = Field(default=None, max_length=20)
    is_primary: bool = True


class BankAccountResponse(BaseModel):
    """Restricted response. The account number is never returned in full."""

    id: int
    employee_id: int
    account_holder_name: str
    account_number_masked: str
    ifsc_code: str
    bank_name: str
    branch_name: str | None
    account_type: str | None
    is_primary: bool
    is_verified: bool


class ExitRequest(BaseModel):
    date_of_exit: date
    exit_type: ExitType
    exit_reason: str | None = Field(default=None, max_length=2000)
    last_working_day: date | None = None
    deactivate_account: bool = Field(
        default=True,
        description="Deactivates the sign-in account. History is always preserved.",
    )


class LinkAccountRequest(BaseModel):
    user_id: int


class ImportErrorItem(BaseModel):
    row: int
    column: str | None
    message: str


class ImportPreviewItem(BaseModel):
    row: int
    employee_code: str
    name: str
    work_email: str | None
    employment_type: str
    date_of_joining: str
    reporting_manager_code: str | None


class ImportReportResponse(BaseModel):
    ok: bool = Field(description="True when no row has an error.")
    dry_run: bool
    total_rows: int
    valid_rows: int
    error_count: int
    errors: list[ImportErrorItem]
    created_codes: list[str]
    preview: list[ImportPreviewItem]


class EmployeeDocumentResponse(BaseModel):
    id: int
    employee_id: int
    document_type: str
    title: str
    visibility: str
    valid_from: date | None
    valid_to: date | None
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: Any
