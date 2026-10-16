"""Request and response models for payroll.

Every monetary field is a ``Decimal``. Pydantic serialises a Decimal as a JSON
**string** - ``"1800.00"``, not ``1800.0`` - which is deliberate: a float would
lose paise in the browser's own number handling, and a payslip that disagrees
with itself by a paisa is a dispute.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ComponentType = Literal["earning", "deduction", "employer_contribution", "reimbursement"]
CalculationType = Literal[
    "fixed",
    "percent_of_component",
    "percent_of_gross",
    "percent_of_ctc",
    "balance",
    "statutory",
    "attendance_based",
]
StatutoryDomain = Literal["epf", "eps", "edli", "esi", "pt", "tds", "gratuity", "lwf"]
RunStatus = Literal["draft", "calculated", "reviewed", "approved", "finalized", "cancelled"]
RunType = Literal["regular", "supplementary", "fnf"]
AdjustmentType = Literal["arrear", "bonus", "recovery", "reversal"]


# --------------------------------------------------------------- components


class ComponentCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "code": "BASIC",
                    "name": "Basic salary",
                    "component_type": "earning",
                    "calculation_type": "percent_of_gross",
                    "percentage": "50",
                    "is_pf_wage": True,
                    "is_esi_wage": True,
                    "is_pt_wage": True,
                }
            ]
        },
    )

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    component_type: ComponentType
    calculation_type: CalculationType

    of_component_id: int | None = Field(
        default=None, description="Required for percent_of_component."
    )
    percentage: Decimal | None = Field(default=None, gt=0, le=100)
    statutory_domain: StatutoryDomain | None = Field(
        default=None, description="Required for a statutory component."
    )

    is_pf_wage: bool = Field(
        default=False,
        description=(
            "Whether this counts towards the provident-fund wage. Explicit per "
            "component, because it is a legal question the company answers; it is "
            "never inferred from the component's name."
        ),
    )
    is_esi_wage: bool = False
    is_pt_wage: bool = False
    is_taxable: bool = True
    prorates_on_payable_days: bool = Field(
        default=True,
        description="Whether the amount reduces when payable days are short.",
    )
    show_on_payslip: bool = True
    display_order: int = Field(default=100, ge=0, le=9999)
    requires_legal_review: bool = Field(
        default=False,
        description=(
            "Set when the company has not established this component's statutory treatment."
        ),
    )

    @field_validator("code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class ComponentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    percentage: Decimal | None = Field(default=None, gt=0, le=100)
    is_pf_wage: bool | None = None
    is_esi_wage: bool | None = None
    is_pt_wage: bool | None = None
    is_taxable: bool | None = None
    prorates_on_payable_days: bool | None = None
    show_on_payslip: bool | None = None
    display_order: int | None = Field(default=None, ge=0, le=9999)
    is_active: bool | None = None
    requires_legal_review: bool | None = None


class ComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    component_type: str
    calculation_type: str
    of_component_id: int | None
    percentage: Decimal | None
    statutory_domain: str | None
    is_pf_wage: bool
    is_esi_wage: bool
    is_pt_wage: bool
    is_taxable: bool
    prorates_on_payable_days: bool
    show_on_payslip: bool
    display_order: int
    is_active: bool
    requires_legal_review: bool


# --------------------------------------------------------------- structures


class StructureComponentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: int
    fixed_amount: Decimal | None = Field(default=None, ge=0)
    percentage: Decimal | None = Field(default=None, gt=0, le=100)
    sequence: int | None = Field(default=None, ge=0, le=9999)


class StructureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    components: list[StructureComponentIn] = Field(default_factory=list, max_length=60)

    @field_validator("code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class StructureComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component_id: int
    fixed_amount: Decimal | None
    percentage: Decimal | None
    sequence: int


class StructureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    is_active: bool
    components: list[StructureComponentResponse]


# ----------------------------------------------------------------- salaries


class RevisionCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "employee_id": 1,
                    "effective_from": "2026-04-01",
                    "gross_monthly": "50000.00",
                    "structure_id": 1,
                    "revision_reason": "Annual increment",
                }
            ]
        },
    )

    employee_id: int
    effective_from: date
    effective_to: date | None = None
    gross_monthly: Decimal = Field(gt=0, description="Monthly gross, in the configured currency.")
    ctc_annual: Decimal | None = Field(default=None, ge=0)
    structure_id: int | None = None
    components: list[StructureComponentIn] = Field(
        default_factory=list,
        max_length=60,
        description=(
            "Per-employee amounts, overriding the structure's defaults. The "
            "resolved figures are stored on the revision, so editing a structure "
            "later never changes what somebody is already paid."
        ),
    )
    revision_reason: str | None = Field(default=None, max_length=200)
    notes: str | None = None


class SalaryComponentAmount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    component_id: int
    monthly_amount: Decimal
    basis_description: str | None
    sequence: int


class RevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    structure_id: int | None
    effective_from: date
    effective_to: date | None
    gross_monthly: Decimal
    ctc_annual: Decimal | None
    revision_reason: str | None
    notes: str | None
    components: list[SalaryComponentAmount]
    created_at: datetime


# ---------------------------------------------------------------- statutory


class ParameterIn(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "key": "employee_rate_percent",
                    "label": "Employee contribution rate",
                    "numeric_value": "12",
                    "unit": "percent",
                },
                {
                    "key": "slabs",
                    "label": "Monthly slabs",
                    "json_value": [
                        {"up_to": 10000, "amount": 0},
                        {"up_to": None, "amount": 200},
                    ],
                },
            ]
        },
    )

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    numeric_value: Decimal | None = None
    json_value: Any | None = None
    unit: str | None = Field(default=None, max_length=30)
    notes: str | None = None

    @model_validator(mode="after")
    def _exactly_one_value(self) -> ParameterIn:
        if (self.numeric_value is None) == (self.json_value is None):
            raise ValueError(
                "A parameter is either a number or a table: provide exactly one of "
                "numeric_value and json_value."
            )
        return self


class RuleSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: StatutoryDomain
    version_label: str = Field(
        min_length=1,
        max_length=50,
        description="How this version is referred to, e.g. 'FY 2026-27'.",
    )
    effective_from: date
    effective_to: date | None = None
    jurisdiction: str = Field(default="IN", min_length=2, max_length=2)
    state_code: str | None = Field(
        default=None,
        max_length=10,
        description="For per-state domains such as professional tax.",
    )
    source_reference: str | None = Field(
        default=None,
        description=(
            "The published document these figures come from. Required to verify "
            "the rule set, and therefore to finalize a payroll that uses it."
        ),
    )
    source_url: str | None = Field(default=None, max_length=500)
    source_published_on: date | None = None
    notes: str | None = None
    parameters: list[ParameterIn] = Field(default_factory=list, max_length=60)


class ParameterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    numeric_value: Decimal | None
    json_value: Any | None
    unit: str | None
    notes: str | None


class RuleSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    jurisdiction: str
    state_code: str | None
    version_label: str
    effective_from: date
    effective_to: date | None
    status: str
    source_reference: str | None
    source_url: str | None
    source_published_on: date | None
    verified_by_user_id: int | None
    verified_at: datetime | None
    verification_note: str | None
    notes: str | None
    parameters: list[ParameterResponse]


class ParametersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: list[ParameterIn] = Field(min_length=1, max_length=60)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_reference: str = Field(
        min_length=5,
        description="The published source these figures were checked against.",
    )
    source_url: str | None = Field(default=None, max_length=500)
    source_published_on: date | None = None
    note: str | None = None


class SupersedeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class EligibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    domain: StatutoryDomain
    is_eligible: bool
    effective_from: date
    effective_to: date | None = None
    reason: str | None = Field(default=None, description="Required when excluding an employee.")


class EligibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    domain: str
    is_eligible: bool
    effective_from: date
    effective_to: date | None
    reason: str | None


# ------------------------------------------------------------------- runs


class PeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    year: int
    month: int
    start_date: date
    end_date: date
    pay_date: date | None
    is_closed: bool


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    run_type: RunType = "regular"
    notes: str | None = None


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_id: int
    run_type: str
    status: str
    idempotency_key: str
    notes: str | None
    rule_set_snapshot: dict[str, Any] | None
    settings_snapshot: dict[str, Any] | None
    unverified_domains: list[str] | None
    employee_count: int
    total_gross: Decimal
    total_deductions: Decimal
    total_employer_cost: Decimal
    total_net: Decimal
    calculated_at: datetime | None
    reviewed_at: datetime | None
    approved_at: datetime | None
    finalized_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime


class CalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_ids: list[int] | None = Field(
        default=None,
        max_length=2000,
        description="Null calculates everybody in service during the period.",
    )


class SkippedEmployee(BaseModel):
    employee_id: int
    employee_code: str
    reason: str


class CalculateResponse(BaseModel):
    run_id: int
    status: str
    period: str
    payslips: int
    skipped: list[SkippedEmployee]
    total_gross: str
    total_deductions: str
    total_employer_cost: str
    total_net: str
    unverified_domains: list[str]
    currency: str


class ReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class PublishResponse(BaseModel):
    run_id: int
    published: int
    notifications_created: int
    period: str


# --------------------------------------------------------------- payslips


class PayslipLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_code: str
    label: str
    component_type: str
    amount: Decimal
    full_amount: Decimal | None
    basis_amount: Decimal | None
    basis_description: str | None = Field(
        description="What the figure was computed from, so it can be checked."
    )
    statutory_rule_set_id: int | None
    sequence: int


class PayslipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    employee_id: int
    employee_code: str
    employee_name: str
    designation_name: str | None
    department_name: str | None
    work_location_name: str | None
    days_in_period: Decimal
    payable_days: Decimal
    lop_days: Decimal
    paid_leave_days: Decimal
    present_days: Decimal
    weekly_off_days: Decimal
    holiday_days: Decimal
    overtime_minutes: int
    gross_earnings: Decimal
    total_deductions: Decimal
    employer_contributions: Decimal
    net_pay: Decimal
    is_prorated: bool
    proration_reason: str | None
    review_flags: list[dict[str, Any]] | None
    calculation_notes: dict[str, Any] | None
    published_at: datetime | None
    lines: list[PayslipLineResponse]


class PayslipSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    employee_code: str
    employee_name: str
    payable_days: Decimal
    lop_days: Decimal
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    published_at: datetime | None
    review_flags: list[dict[str, Any]] | None


# ------------------------------------------------------------ adjustments


class AdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    adjustment_type: AdjustmentType
    amount: Decimal = Field(
        gt=0,
        description=(
            "Always positive. The type decides whether it is paid or recovered, "
            "so a signed amount cannot pay somebody by accident."
        ),
    )
    label: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3)
    relates_to_period_id: int | None = Field(
        default=None,
        description="The period the money belongs to, which is not the run that pays it.",
    )
    is_taxable: bool = True
    idempotency_key: str | None = Field(default=None, max_length=160)


class AdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    adjustment_type: str
    amount: Decimal
    label: str
    reason: str
    relates_to_period_id: int | None
    applied_in_run_id: int | None
    is_taxable: bool
    status: str
    applied_at: datetime | None
    created_at: datetime


# ------------------------------------------------------------- settlement


class SettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    encashable_leave_days: Decimal = Field(default=Decimal("0"), ge=0)
    notice_shortfall_days: Decimal = Field(default=Decimal("0"), ge=0)
    other_dues: Decimal = Field(default=Decimal("0"), ge=0)
    other_recoveries: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    exit_date: date
    last_working_date: date | None
    status: str
    leave_encashment_days: Decimal
    leave_encashment_amount: Decimal
    notice_shortfall_days: Decimal
    notice_recovery_amount: Decimal
    gratuity_amount: Decimal
    gratuity_rule_set_id: int | None
    other_dues: Decimal
    other_recoveries: Decimal
    net_settlement: Decimal
    computation_notes: dict[str, Any] | None
    notes: str | None
    approved_at: datetime | None
    paid_at: datetime | None


# --------------------------------------------------------------- reporting


class RegisterRow(BaseModel):
    payslip_id: int
    employee_id: int
    employee_code: str
    employee_name: str
    department: str | None
    payable_days: str
    lop_days: str
    earnings: dict[str, str]
    deductions: dict[str, str]
    employer_contributions: dict[str, str]
    gross_earnings: str
    total_deductions: str
    net_pay: str
    flags: list[str]


class RegisterResponse(BaseModel):
    run_id: int
    period: str
    status: str
    run_type: str
    employee_count: int
    total_gross: str
    total_deductions: str
    total_employer_cost: str
    total_net: str
    rule_sets_used: dict[str, Any]
    settings_used: dict[str, Any]
    unverified_domains: list[str]
    rows: list[RegisterRow]


class ReconciliationResponse(BaseModel):
    run_id: int
    balanced: bool = Field(
        description=(
            "Every figure re-summed from the payslip lines. False means the "
            "stored totals disagree with their own lines."
        )
    )
    payslip_count: int
    lines_gross: str
    lines_deductions: str
    lines_employer: str
    payslips_gross: str
    payslips_deductions: str
    payslips_net: str
    run_gross: str
    run_deductions: str
    run_net: str
    run_employer_cost: str
    mismatched: list[dict[str, Any]]


class StatutoryLineSummary(BaseModel):
    line_code: str
    label: str
    component_type: str
    employee_count: int
    total_amount: str
    rule_set_id: int | None


class StatutorySummaryResponse(BaseModel):
    run_id: int
    period: str
    status: str
    lines: list[StatutoryLineSummary]
    rule_sets_used: dict[str, Any]
    note: str


class DomainReadiness(BaseModel):
    domain: str
    has_rule_set: bool
    status: str | None
    version: str | None
    parameter_count: int
    source_reference: str | None


class ConfigurationReportResponse(BaseModel):
    ready: bool
    blockers: list[str]
    enabled_statutory_modules: list[str]
    domains: list[DomainReadiness]
    active_components: int
    employees_without_salary: int
    unconfirmed_settings: list[str]
