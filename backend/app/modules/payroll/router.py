"""Payroll endpoints.

Every permission in this module is distinct, and the lifecycle deliberately
requires several of them: ``payroll.calculate``, ``payroll.review``,
``payroll.approve`` and ``payroll.finalize`` are four separate grants, so a
single account cannot take a month's pay from calculation to payment unless
somebody has chosen to give it all four. The service layer additionally refuses
to let the same person review what they calculated, or approve what they
reviewed.

Self-service is narrow by design: an employee sees their own salary structure
and their own **published** payslips, and nothing else. A payslip from a run
still under review is not shown, because its figures may still change.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import NotFound, PolicyViolation
from app.modules.auth.models import User
from app.modules.employees import service as employees_service
from app.modules.payroll import runs as runs_service
from app.modules.payroll import service
from app.modules.payroll.schemas import (
    AdjustmentCreate,
    AdjustmentResponse,
    CalculateRequest,
    CalculateResponse,
    ComponentCreate,
    ComponentResponse,
    ComponentUpdate,
    ConfigurationReportResponse,
    EligibilityRequest,
    EligibilityResponse,
    ParametersRequest,
    PayslipResponse,
    PayslipSummary,
    PeriodResponse,
    PublishResponse,
    ReasonRequest,
    ReconciliationResponse,
    RegisterResponse,
    RevisionCreate,
    RevisionResponse,
    RuleSetCreate,
    RuleSetResponse,
    RunCreate,
    RunResponse,
    SettlementRequest,
    SettlementResponse,
    StatutorySummaryResponse,
    StructureCreate,
    StructureResponse,
    SupersedeRequest,
    VerifyRequest,
)

router = APIRouter(prefix="/payroll", tags=["Payroll"])


def _assert_can_read_salary(session: DbSession, user: User, employee_id: int) -> None:
    """Somebody's pay is visible to them, and to whoever holds `salary.read_all`.

    A manager does **not** see their team's salaries by virtue of being their
    manager: pay is more sensitive than attendance, and the brief's privacy
    tiers keep them apart. 404 rather than 403, so ids cannot be enumerated.
    """
    if user.has_permission("salary.read_all"):
        return
    if user.employee_id == employee_id and user.has_permission("salary.read_self"):
        return
    raise NotFound("Employee not found.")


# ------------------------------------------------------------- components


@router.get(
    "/components",
    response_model=list[ComponentResponse],
    summary="List salary components",
)
def list_components(
    session: DbSession,
    _dep: Annotated[User, Depends(require_any("salary.read_all", "payroll.read"))],
    include_inactive: bool = False,
) -> list[ComponentResponse]:
    """The company's payslip lines. None is seeded."""
    return [
        ComponentResponse.model_validate(item)
        for item in service.list_components(session, include_inactive=include_inactive)
    ]


@router.post(
    "/components",
    response_model=ComponentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Define a salary component",
)
def create_component(
    payload: ComponentCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("salary.manage"))],
) -> ComponentResponse:
    """Create a payslip line.

    The wage-base flags are explicit and default to the conservative reading,
    because whether a component counts towards PF, ESI or professional tax is a
    legal question the company answers - not something to infer from its name.
    """
    component = service.create_component(session, data=payload.model_dump(), context=context)
    return ComponentResponse.model_validate(component)


@router.patch(
    "/components/{component_id}",
    response_model=ComponentResponse,
    summary="Edit a salary component",
)
def update_component(
    component_id: int,
    payload: ComponentUpdate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("salary.manage"))],
) -> ComponentResponse:
    """Edit a component. Payslips already calculated keep their own figures."""
    component = service.update_component(
        session, component_id, data=payload.model_dump(exclude_unset=True), context=context
    )
    return ComponentResponse.model_validate(component)


# ------------------------------------------------------------- structures


@router.get(
    "/structures",
    response_model=list[StructureResponse],
    summary="List salary structures",
)
def list_structures(
    session: DbSession,
    _dep: Annotated[User, Depends(require_any("salary.read_all", "payroll.read"))],
    include_inactive: bool = False,
) -> list[StructureResponse]:
    return [
        StructureResponse.model_validate(item)
        for item in service.list_structures(session, include_inactive=include_inactive)
    ]


@router.post(
    "/structures",
    response_model=StructureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a salary structure",
)
def create_structure(
    payload: StructureCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("salary.manage"))],
) -> StructureResponse:
    """A reusable template. Assigning it copies the resolved amounts, so editing
    it later never changes anybody's existing pay."""
    structure = service.create_structure(session, data=payload.model_dump(), context=context)
    return StructureResponse.model_validate(structure)


# --------------------------------------------------------------- salaries


@router.post(
    "/revisions",
    response_model=RevisionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a salary revision",
)
def create_revision(
    payload: RevisionCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("salary.manage"))],
) -> RevisionResponse:
    """Effective-dated, never an edit.

    The components must add up to the stated gross, or the figure on the offer
    letter is not the figure being paid - so a structure built from percentages
    needs a balancing component.
    """
    revision = service.create_revision(session, data=payload.model_dump(), context=context)
    return RevisionResponse.model_validate(revision)


@router.get(
    "/revisions/{employee_id}",
    response_model=list[RevisionResponse],
    summary="An employee's salary history",
)
def list_revisions(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("salary.read_all", "salary.read_self"))],
) -> list[RevisionResponse]:
    """Every revision, newest first. Visible to the employee and to HR only."""
    _assert_can_read_salary(session, user, employee_id)
    return [
        RevisionResponse.model_validate(item)
        for item in service.revisions_for(session, employee_id)
    ]


@router.get(
    "/me/salary",
    response_model=RevisionResponse,
    summary="Your current salary structure",
)
def my_salary(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("salary.read_self"))],
) -> RevisionResponse:
    if user.employee_id is None:
        raise PolicyViolation(
            "This account is not linked to an employee record, so it has no salary."
        )
    revision = service.salary_on(session, employee_id=user.employee_id, as_of=date.today())
    if revision is None:
        raise NotFound("No salary revision is in force for you.")
    return RevisionResponse.model_validate(revision)


# -------------------------------------------------------------- statutory


@router.get(
    "/statutory/rule-sets",
    response_model=list[RuleSetResponse],
    summary="List statutory rule sets",
)
def list_rule_sets(
    session: DbSession,
    _dep: Annotated[User, Depends(require("statutory.read"))],
    domain: Annotated[str | None, Query()] = None,
    rule_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[RuleSetResponse]:
    """Every version, with its source and verification state.

    Nothing is seeded: no rate, ceiling or slab in this system was provided by
    its authors.
    """
    return [
        RuleSetResponse.model_validate(item)
        for item in service.list_rule_sets(session, domain=domain, status=rule_status)
    ]


@router.post(
    "/statutory/rule-sets",
    response_model=RuleSetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft statutory rule set",
)
def create_rule_set(
    payload: RuleSetCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("statutory.manage"))],
) -> RuleSetResponse:
    """Always created as a draft.

    A draft computes, so its effect can be seen, but a run that used one cannot
    be finalized while `payroll.block_finalize_on_unverified_rules` is on.
    """
    rule_set = service.create_rule_set(session, data=payload.model_dump(), context=context)
    return RuleSetResponse.model_validate(rule_set)


@router.put(
    "/statutory/rule-sets/{rule_set_id}/parameters",
    response_model=RuleSetResponse,
    summary="Replace a draft rule set's parameters",
)
def set_parameters(
    rule_set_id: int,
    payload: ParametersRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("statutory.manage"))],
) -> RuleSetResponse:
    """Only while it is a draft.

    A verified set's parameters are frozen by database trigger: the verification
    says somebody checked *these* numbers, so a change means a new
    effective-dated version.
    """
    rule_set = service.set_parameters(
        session,
        rule_set_id,
        parameters=[item.model_dump() for item in payload.parameters],
        context=context,
    )
    return RuleSetResponse.model_validate(rule_set)


@router.post(
    "/statutory/rule-sets/{rule_set_id}/verify",
    response_model=RuleSetResponse,
    summary="Verify a rule set against its published source",
)
def verify_rule_set(
    rule_set_id: int,
    payload: VerifyRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("statutory.verify"))],
) -> RuleSetResponse:
    """Record that these figures were checked, by whom, against what.

    This is the gate the brief asks for. Until it is passed, payroll using these
    figures cannot be finalized - which is how the system avoids claiming a
    legal calculation works when nobody has confirmed its inputs.
    """
    rule_set = service.verify_rule_set(
        session,
        rule_set_id,
        source_reference=payload.source_reference,
        note=payload.note,
        source_url=payload.source_url,
        source_published_on=payload.source_published_on,
        context=context,
    )
    return RuleSetResponse.model_validate(rule_set)


@router.post(
    "/statutory/rule-sets/{rule_set_id}/supersede",
    response_model=RuleSetResponse,
    summary="Retire a rule set",
)
def supersede_rule_set(
    rule_set_id: int,
    payload: SupersedeRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("statutory.manage"))],
) -> RuleSetResponse:
    """Retired, not deleted: past payslips cite it."""
    rule_set = service.supersede_rule_set(
        session, rule_set_id, reason=payload.reason, context=context
    )
    return RuleSetResponse.model_validate(rule_set)


@router.post(
    "/statutory/eligibility",
    response_model=EligibilityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set an employee's statutory eligibility",
)
def set_eligibility(
    payload: EligibilityRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("statutory.manage"))],
) -> EligibilityResponse:
    """Effective-dated, and required to exclude somebody as well as to include
    them: an exemption should be a decision with a reason, not missing data."""
    record = service.set_eligibility(session, data=payload.model_dump(), context=context)
    return EligibilityResponse.model_validate(record)


@router.get(
    "/statutory/eligibility/{employee_id}",
    response_model=list[EligibilityResponse],
    summary="An employee's statutory eligibility history",
)
def list_eligibility(
    employee_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("statutory.read"))],
) -> list[EligibilityResponse]:
    return [
        EligibilityResponse.model_validate(item)
        for item in service.eligibility_for(session, employee_id)
    ]


@router.get(
    "/readiness",
    response_model=ConfigurationReportResponse,
    summary="What is missing before payroll can be run",
)
def readiness(
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> ConfigurationReportResponse:
    """Deliberately blunt.

    Payroll run on unconfirmed placeholders produces numbers that look
    finished, so the gaps are listed plainly rather than hidden behind a green
    tick.
    """
    return ConfigurationReportResponse(**service.configuration_report(session))


# ------------------------------------------------------------------ runs


@router.get("/periods", response_model=list[PeriodResponse], summary="List payroll periods")
def list_periods(
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> list[PeriodResponse]:
    return [PeriodResponse.model_validate(item) for item in service.list_periods(session)]


@router.get("/runs", response_model=list[RunResponse], summary="List payroll runs")
def list_runs(
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[RunResponse]:
    return [
        RunResponse.model_validate(item)
        for item in runs_service.list_runs(session, year=year, status=run_status)
    ]


@router.post(
    "/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a payroll run",
)
def create_run(
    payload: RunCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.calculate"))],
) -> RunResponse:
    """One regular run per month, enforced by a unique key."""
    run = runs_service.create_run(
        session,
        year=payload.year,
        month=payload.month,
        run_type=payload.run_type,
        notes=payload.notes,
        context=context,
    )
    return RunResponse.model_validate(run)


@router.get("/runs/{run_id}", response_model=RunResponse, summary="One payroll run")
def get_run(
    run_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> RunResponse:
    return RunResponse.model_validate(runs_service.get_run(session, run_id))


@router.post(
    "/runs/{run_id}/calculate",
    response_model=CalculateResponse,
    summary="Calculate a run's payslips",
)
def calculate(
    run_id: int,
    payload: CalculateRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.calculate"))],
) -> CalculateResponse:
    """Build every payslip, replacing any from a previous calculation.

    Allowed only while the run is open. An employee whose configuration is
    incomplete is skipped with the reason, rather than abandoning the whole run.
    """
    result = runs_service.calculate(
        session, run_id, employee_ids=payload.employee_ids, context=context
    )
    return CalculateResponse(**result)


@router.post("/runs/{run_id}/review", response_model=RunResponse, summary="Mark a run reviewed")
def review(
    run_id: int,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.review"))],
) -> RunResponse:
    """Nobody may review a run they calculated: one pair of eyes over a whole
    company's pay is not a review."""
    return RunResponse.model_validate(
        runs_service.review(session, run_id, user=user, context=context)
    )


@router.post("/runs/{run_id}/approve", response_model=RunResponse, summary="Approve a run")
def approve(
    run_id: int,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.approve"))],
) -> RunResponse:
    """The approver may not be the reviewer."""
    return RunResponse.model_validate(
        runs_service.approve(session, run_id, user=user, context=context)
    )


@router.post("/runs/{run_id}/finalize", response_model=RunResponse, summary="Finalize a run")
def finalize(
    run_id: int,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.finalize"))],
) -> RunResponse:
    """After this the run is immutable, enforced by database trigger.

    Refused while the run used an unverified statutory rule set, unless the
    company has explicitly turned that protection off.
    """
    return RunResponse.model_validate(runs_service.finalize(session, run_id, context=context))


@router.post(
    "/runs/{run_id}/send-back",
    response_model=RunResponse,
    summary="Return a run to draft",
)
def send_back(
    run_id: int,
    payload: ReasonRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.calculate"))],
) -> RunResponse:
    """Explicit, because recalculating under a reviewer would invalidate their
    review without telling anybody."""
    return RunResponse.model_validate(
        runs_service.send_back(session, run_id, reason=payload.reason, context=context)
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse, summary="Cancel a run")
def cancel(
    run_id: int,
    payload: ReasonRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.calculate"))],
) -> RunResponse:
    """A finalized run cannot be cancelled; post a reversal instead."""
    return RunResponse.model_validate(
        runs_service.cancel(session, run_id, reason=payload.reason, context=context)
    )


@router.post(
    "/runs/{run_id}/publish",
    response_model=PublishResponse,
    summary="Release payslips to employees",
)
def publish(
    run_id: int,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.finalize"))],
) -> PublishResponse:
    """Separate from finalizing: the register may be ready for the bank before
    HR is ready for everybody to see their payslip. Idempotent."""
    return PublishResponse(**runs_service.publish_payslips(session, run_id, context=context))


# -------------------------------------------------------------- reporting


@router.get(
    "/runs/{run_id}/register",
    response_model=RegisterResponse,
    summary="The run as a register",
)
def register(
    run_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> RegisterResponse:
    """What a bank transfer or a statutory return is prepared from, by a human.
    Neither is performed by this system."""
    return RegisterResponse(**runs_service.register(session, run_id))


@router.get(
    "/runs/{run_id}/reconciliation",
    response_model=ReconciliationResponse,
    summary="Check a run against its own lines",
)
def reconciliation(
    run_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> ReconciliationResponse:
    """Every figure re-summed from the payslip lines.

    A payroll that cannot prove it adds up should not be paid, so this reads
    nothing from the stored totals except to compare against them.
    """
    return ReconciliationResponse(**runs_service.reconciliation(session, run_id))


@router.get(
    "/runs/{run_id}/statutory-summary",
    response_model=StatutorySummaryResponse,
    summary="Per-domain statutory totals",
)
def statutory_summary(
    run_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
) -> StatutorySummaryResponse:
    """The data a statutory filing is prepared from. No filing is performed."""
    return StatutorySummaryResponse(**runs_service.statutory_summary(session, run_id))


# --------------------------------------------------------------- payslips


@router.get(
    "/runs/{run_id}/payslips",
    response_model=list[PayslipSummary],
    summary="Payslips in a run",
)
def payslips_in_run(
    run_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("payslip.read_all"))],
) -> list[PayslipSummary]:
    return [
        PayslipSummary.model_validate(item)
        for item in runs_service.payslips_in_run(session, run_id)
    ]


@router.get(
    "/me/payslips",
    response_model=list[PayslipSummary],
    summary="Your payslips",
)
def my_payslips(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("payslip.read_self"))],
) -> list[PayslipSummary]:
    """Published payslips only.

    A payslip from a run still being reviewed is not shown, because its figures
    may still change and an employee should not have to watch their pay move.
    """
    if user.employee_id is None:
        raise PolicyViolation(
            "This account is not linked to an employee record, so it has no payslips."
        )
    return [
        PayslipSummary.model_validate(item)
        for item in runs_service.payslips_for_employee(
            session, employee_id=user.employee_id, published_only=True
        )
    ]


@router.get(
    "/payslips/{payslip_id}",
    response_model=PayslipResponse,
    summary="One payslip, with every line's basis",
)
def get_payslip(
    payslip_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("payslip.read_all", "payslip.read_self"))],
) -> PayslipResponse:
    """Each line carries what it was computed from, so a figure can be checked.

    An employee may read their own, and only once it has been published.
    """
    payslip = runs_service.get_payslip(session, payslip_id)
    if not user.has_permission("payslip.read_all") and (
        payslip.employee_id != user.employee_id or payslip.published_at is None
    ):
        # 404 rather than 403: an unpublished payslip's existence is not
        # something to confirm.
        raise NotFound("Payslip not found.")
    return PayslipResponse.model_validate(payslip)


# ------------------------------------------------------------ adjustments


@router.post(
    "/adjustments",
    response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post an arrear, bonus, recovery or reversal",
)
def create_adjustment(
    payload: AdjustmentCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.adjust"))],
) -> AdjustmentResponse:
    """The only way to correct a finalized run.

    The amount is always positive and the type decides the direction, so a
    "recovery of -500" - which would pay somebody by accident - cannot be
    expressed.
    """
    adjustment = service.create_adjustment(session, data=payload.model_dump(), context=context)
    return AdjustmentResponse.model_validate(adjustment)


@router.get(
    "/adjustments",
    response_model=list[AdjustmentResponse],
    summary="Pending adjustments",
)
def list_adjustments(
    session: DbSession,
    _dep: Annotated[User, Depends(require("payroll.read"))],
    employee_id: Annotated[int | None, Query()] = None,
) -> list[AdjustmentResponse]:
    return [
        AdjustmentResponse.model_validate(item)
        for item in service.pending_adjustments(session, employee_id=employee_id)
    ]


@router.post(
    "/adjustments/{adjustment_id}/cancel",
    response_model=AdjustmentResponse,
    summary="Cancel a pending adjustment",
)
def cancel_adjustment(
    adjustment_id: int,
    payload: ReasonRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.adjust"))],
) -> AdjustmentResponse:
    """One already paid cannot be cancelled; post a reversal instead."""
    adjustment = service.cancel_adjustment(
        session, adjustment_id, reason=payload.reason, context=context
    )
    return AdjustmentResponse.model_validate(adjustment)


# ------------------------------------------------------------- settlement


@router.post(
    "/settlements/{employee_id}/calculate",
    response_model=SettlementResponse,
    summary="Calculate a leaver's full and final settlement",
)
def calculate_settlement(
    employee_id: int,
    payload: SettlementRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.run_fnf"))],
) -> SettlementResponse:
    """Leave encashment, notice recovery and gratuity.

    Gratuity is computed **only** from a verified rule set. Without one the line
    is zero and the reason is recorded, because an invented gratuity figure
    would understate or overstate a statutory payment somebody is legally owed.
    """
    employees_service.get_employee(session, employee_id)
    settlement = runs_service.calculate_settlement(
        session,
        employee_id=employee_id,
        encashable_leave_days=payload.encashable_leave_days,
        notice_shortfall_days=payload.notice_shortfall_days,
        other_dues=payload.other_dues,
        other_recoveries=payload.other_recoveries,
        notes=payload.notes,
        context=context,
    )
    return SettlementResponse.model_validate(settlement)


@router.post(
    "/settlements/{employee_id}/approve",
    response_model=SettlementResponse,
    summary="Approve a settlement",
)
def approve_settlement(
    employee_id: int,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("payroll.approve"))],
) -> SettlementResponse:
    """Refused while a gratuity figure rests on an unverified rule set."""
    return SettlementResponse.model_validate(
        runs_service.approve_settlement(session, employee_id, context=context)
    )


@router.get(
    "/settlements/{employee_id}",
    response_model=SettlementResponse,
    summary="A settlement",
)
def get_settlement(
    employee_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require_any("payroll.read", "payroll.run_fnf"))],
) -> SettlementResponse:
    return SettlementResponse.model_validate(runs_service.get_settlement(session, employee_id))
