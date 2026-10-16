"""Payroll configuration: components, structures, revisions, statutory rules.

The run lifecycle and the calculation itself live in :mod:`runs`; this module is
everything that has to be set up before a payroll can be calculated, plus the
queries that read it back.

Two things are enforced here rather than left to the interface
--------------------------------------------------------------
**A salary revision is never edited.** Changing somebody's pay creates a new
effective-dated row and closes the previous one the day before it starts, so a
payslip from last March can still be explained. The component amounts are
*copied* at revision time rather than resolved from the structure during
payroll, because a structure edited in June must not change what April paid.

**A statutory rule set cannot be verified without a source and a verifier.** The
check constraint enforces the shape; this module enforces the act - verification
is a separate permissioned call that records who checked it against which
published document, and a verified set's parameters are frozen by trigger.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, PolicyViolation, ValidationFailed
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.employees.models import Employee
from app.modules.payroll import policy
from app.modules.payroll.models import (
    STATUTORY_DOMAINS,
    EmployeeSalary,
    EmployeeSalaryComponent,
    EmployeeStatutoryEligibility,
    PayrollAdjustment,
    PayrollPeriod,
    SalaryComponent,
    SalaryStructure,
    SalaryStructureComponent,
    StatutoryRuleParameter,
    StatutoryRuleSet,
)
from app.modules.settings import service as settings_service

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Salary components
# ---------------------------------------------------------------------------


def create_component(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> SalaryComponent:
    """Define a payslip line the company uses.

    Nothing is seeded, so this is how every component comes into existence. The
    wage-base flags default to the conservative reading - taxable, not a PF or
    ESI wage - because wrongly *including* a component in a statutory base
    over-deducts from an employee, and wrongly excluding it under-deducts from
    the company; of the two, the former takes money from the person with less
    recourse.
    """
    code = data["code"].strip().upper()
    if session.scalar(select(SalaryComponent).where(SalaryComponent.code == code)):
        raise Conflict(f"A salary component with the code {code} already exists.")

    of_component_id = data.get("of_component_id")
    if data["calculation_type"] == "percent_of_component":
        if of_component_id is None:
            raise ValidationFailed(
                "A percentage-of-component needs the component it applies to.",
                details=[{"field": "of_component_id", "message": "Required."}],
            )
        base = session.get(SalaryComponent, of_component_id)
        if base is None:
            raise ValidationFailed(
                "The component this percentage applies to does not exist.",
                details=[{"field": "of_component_id", "message": "Unknown."}],
            )
        if base.calculation_type in ("percent_of_component", "balance"):
            raise PolicyViolation(
                f"{base.code} is itself derived, so a percentage of it would make "
                "the payslip's arithmetic depend on evaluation order. Use a "
                "percentage of gross instead."
            )

    if data["calculation_type"] == "statutory" and not data.get("statutory_domain"):
        raise ValidationFailed(
            "A statutory component needs the domain that computes it.",
            details=[{"field": "statutory_domain", "message": "Required."}],
        )

    component = SalaryComponent(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        component_type=data["component_type"],
        calculation_type=data["calculation_type"],
        of_component_id=of_component_id,
        percentage=data.get("percentage"),
        statutory_domain=data.get("statutory_domain"),
        is_pf_wage=data.get("is_pf_wage", False),
        is_esi_wage=data.get("is_esi_wage", False),
        is_pt_wage=data.get("is_pt_wage", False),
        is_taxable=data.get("is_taxable", True),
        prorates_on_payable_days=data.get("prorates_on_payable_days", True),
        show_on_payslip=data.get("show_on_payslip", True),
        display_order=data.get("display_order", 100),
        requires_legal_review=data.get("requires_legal_review", False),
        created_by_user_id=context.actor_user_id,
    )
    session.add(component)
    session.flush()

    audit.record(
        session,
        action="salary.component_created",
        context=context,
        entity_type="salary_component",
        entity_id=component.id,
        after={
            "code": component.code,
            "type": component.component_type,
            "calculation": component.calculation_type,
            "pf_wage": component.is_pf_wage,
            "esi_wage": component.is_esi_wage,
            "taxable": component.is_taxable,
        },
    )
    return component


def update_component(
    session: Session, component_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> SalaryComponent:
    """Edit a component.

    A component already used by a salary revision can still be edited - a
    mislabelled line has to be fixable - but the revisions keep their copied
    amounts, so nothing already paid changes. Changing a wage-base flag is
    audited prominently, because it alters future statutory deductions.
    """
    component = get_component(session, component_id)
    before = {
        "name": component.name,
        "is_pf_wage": component.is_pf_wage,
        "is_esi_wage": component.is_esi_wage,
        "is_pt_wage": component.is_pt_wage,
        "is_taxable": component.is_taxable,
        "is_active": component.is_active,
    }

    for field in (
        "name",
        "description",
        "percentage",
        "is_pf_wage",
        "is_esi_wage",
        "is_pt_wage",
        "is_taxable",
        "prorates_on_payable_days",
        "show_on_payslip",
        "display_order",
        "is_active",
        "requires_legal_review",
    ):
        if field in data:
            setattr(component, field, data[field])
    component.updated_by_user_id = context.actor_user_id
    session.flush()

    wage_base_changed = any(
        before[key] != getattr(component, key)
        for key in ("is_pf_wage", "is_esi_wage", "is_pt_wage", "is_taxable")
    )
    audit.record(
        session,
        action="salary.component_updated",
        context=context,
        entity_type="salary_component",
        entity_id=component.id,
        before=before,
        after={
            "name": component.name,
            "is_pf_wage": component.is_pf_wage,
            "is_esi_wage": component.is_esi_wage,
            "is_pt_wage": component.is_pt_wage,
            "is_taxable": component.is_taxable,
            "is_active": component.is_active,
        },
        extra=(
            {
                "note": (
                    "A wage-base flag changed. Future statutory deductions will "
                    "differ; payslips already calculated are unaffected."
                )
            }
            if wage_base_changed
            else None
        ),
    )
    return component


def get_component(session: Session, component_id: int) -> SalaryComponent:
    component = session.get(SalaryComponent, component_id)
    if component is None:
        raise NotFound("Salary component not found.")
    return component


def list_components(session: Session, *, include_inactive: bool = False) -> list[SalaryComponent]:
    statement = select(SalaryComponent).order_by(
        SalaryComponent.display_order, SalaryComponent.code
    )
    if not include_inactive:
        statement = statement.where(SalaryComponent.is_active.is_(True))
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


def create_structure(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> SalaryStructure:
    """A reusable template of components.

    A template only: assigning it to an employee copies the resolved amounts, so
    editing the structure later never changes anybody's existing pay.
    """
    code = data["code"].strip().upper()
    if session.scalar(select(SalaryStructure).where(SalaryStructure.code == code)):
        raise Conflict(f"A salary structure with the code {code} already exists.")

    structure = SalaryStructure(
        code=code,
        name=data["name"].strip(),
        description=data.get("description"),
        created_by_user_id=context.actor_user_id,
    )
    session.add(structure)
    session.flush()

    for index, item in enumerate(data.get("components") or []):
        component = get_component(session, item["component_id"])
        session.add(
            SalaryStructureComponent(
                structure_id=structure.id,
                component_id=component.id,
                fixed_amount=item.get("fixed_amount"),
                percentage=item.get("percentage"),
                sequence=item.get("sequence", (index + 1) * 10),
            )
        )
    session.flush()
    session.refresh(structure, attribute_names=["components"])

    audit.record(
        session,
        action="salary.structure_created",
        context=context,
        entity_type="salary_structure",
        entity_id=structure.id,
        after={"code": structure.code, "components": len(structure.components)},
    )
    return structure


def get_structure(session: Session, structure_id: int) -> SalaryStructure:
    structure = session.scalar(
        select(SalaryStructure)
        .where(SalaryStructure.id == structure_id)
        .options(selectinload(SalaryStructure.components))
    )
    if structure is None:
        raise NotFound("Salary structure not found.")
    return structure


def list_structures(session: Session, *, include_inactive: bool = False) -> list[SalaryStructure]:
    statement = (
        select(SalaryStructure)
        .options(selectinload(SalaryStructure.components))
        .order_by(SalaryStructure.code)
    )
    if not include_inactive:
        statement = statement.where(SalaryStructure.is_active.is_(True))
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Employee salary revisions
# ---------------------------------------------------------------------------


def _resolve_components(
    session: Session,
    *,
    gross_monthly: Decimal,
    structure: SalaryStructure | None,
    overrides: list[dict[str, Any]],
    rounding: str,
) -> list[tuple[SalaryComponent, Decimal, str]]:
    """Work out each component's monthly amount for a revision.

    The arithmetic happens **here**, once, where somebody can check it - not on
    every payroll run. An explicit override beats the structure's default, which
    is how one person's allowance can differ without a structure of their own.
    """
    specs: list[policy.ComponentSpec] = []
    by_code: dict[str, SalaryComponent] = {}
    override_by_id = {item["component_id"]: item for item in overrides}

    rows: list[tuple[SalaryComponent, Decimal | None, Decimal | None, int]] = []
    if structure is not None:
        for item in sorted(structure.components, key=lambda c: c.sequence):
            component = get_component(session, item.component_id)
            override = override_by_id.pop(component.id, None)
            rows.append(
                (
                    component,
                    (override or {}).get("fixed_amount", item.fixed_amount),
                    (override or {}).get("percentage", item.percentage),
                    item.sequence,
                )
            )
    for component_id, override in override_by_id.items():
        component = get_component(session, component_id)
        rows.append(
            (
                component,
                override.get("fixed_amount"),
                override.get("percentage"),
                component.display_order,
            )
        )

    if not rows:
        raise ValidationFailed(
            "A salary revision needs at least one component. Assign a structure "
            "or list the components explicitly."
        )

    for component, fixed, percentage, sequence in rows:
        by_code[component.code] = component
        base_code = None
        if component.of_component_id is not None:
            base = session.get(SalaryComponent, component.of_component_id)
            base_code = base.code if base else None
        specs.append(
            policy.ComponentSpec(
                component_id=component.id,
                code=component.code,
                label=component.name,
                component_type=component.component_type,
                calculation_type=component.calculation_type,
                monthly_amount=fixed if fixed is not None else ZERO,
                percentage=percentage if percentage is not None else component.percentage,
                of_component_code=base_code,
                statutory_domain=component.statutory_domain,
                is_pf_wage=component.is_pf_wage,
                is_esi_wage=component.is_esi_wage,
                is_pt_wage=component.is_pt_wage,
                is_taxable=component.is_taxable,
                prorates_on_payable_days=component.prorates_on_payable_days,
                sequence=sequence,
            )
        )

    try:
        lines, bases = policy.compute_earnings(
            specs, gross_monthly=gross_monthly, earning_ratio=Decimal("1"), rounding=rounding
        )
    except policy.PayrollRuleViolation as error:
        raise PolicyViolation(str(error)) from error

    if bases.gross != gross_monthly:
        # The components must add up to the agreed gross, or the figure on the
        # offer letter is not the figure being paid.
        raise PolicyViolation(
            f"The components total {bases.gross}, which does not equal the gross "
            f"of {gross_monthly}. Add a balancing component, or correct the "
            "amounts so they foot."
        )

    return [
        (by_code[line.line_code], line.amount, line.basis_description or "")
        for line in lines
        if line.line_code in by_code
    ]


def create_revision(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> EmployeeSalary:
    """Record a salary revision, closing the one before it.

    Effective-dated rather than edited: a raise from 1 July leaves June's
    payslip explainable. A revision cannot start before the employee joined, and
    one that would start before an existing revision is refused rather than
    silently reordering the history.
    """
    employee = session.get(Employee, data["employee_id"])
    if employee is None:
        raise NotFound("Employee not found.")

    effective_from: date = data["effective_from"]
    if effective_from < employee.date_of_joining:
        raise PolicyViolation(
            f"{employee.employee_code} joined on "
            f"{employee.date_of_joining.isoformat()}, so a salary cannot take "
            "effect before that."
        )

    gross = Decimal(str(data["gross_monthly"]))
    if gross <= ZERO:
        raise ValidationFailed(
            "The monthly gross must be positive.",
            details=[{"field": "gross_monthly", "message": "Must be greater than zero."}],
        )

    existing = list(
        session.scalars(
            select(EmployeeSalary)
            .where(EmployeeSalary.employee_id == employee.id)
            .order_by(EmployeeSalary.effective_from)
        ).all()
    )
    if any(row.effective_from == effective_from for row in existing):
        raise Conflict(
            f"{employee.employee_code} already has a revision effective "
            f"{effective_from.isoformat()}."
        )
    later = [row for row in existing if row.effective_from > effective_from]
    if later:
        raise PolicyViolation(
            f"{employee.employee_code} has a later revision effective "
            f"{later[0].effective_from.isoformat()}. Insert revisions in order, "
            "so the history reads as it happened."
        )

    structure = get_structure(session, data["structure_id"]) if data.get("structure_id") else None
    rounding = settings_service.get_value(session, "payroll.rounding_mode", default="ROUND_HALF_UP")
    resolved = _resolve_components(
        session,
        gross_monthly=gross,
        structure=structure,
        overrides=data.get("components") or [],
        rounding=rounding,
    )

    # Close the open revision the day before this one starts.
    for row in existing:
        if row.effective_to is None:
            row.effective_to = effective_from - timedelta(days=1)

    revision = EmployeeSalary(
        employee_id=employee.id,
        structure_id=structure.id if structure else None,
        effective_from=effective_from,
        effective_to=data.get("effective_to"),
        ctc_annual=data.get("ctc_annual"),
        gross_monthly=gross,
        revision_reason=data.get("revision_reason"),
        notes=data.get("notes"),
        created_by_user_id=context.actor_user_id,
    )
    session.add(revision)
    session.flush()

    for component, amount, basis in resolved:
        session.add(
            EmployeeSalaryComponent(
                employee_salary_id=revision.id,
                component_id=component.id,
                monthly_amount=amount,
                basis_description=basis,
                sequence=component.display_order,
            )
        )
    session.flush()
    session.refresh(revision, attribute_names=["components"])

    audit.record(
        session,
        action="salary.revision_created",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "employee_code": employee.employee_code,
            "effective_from": effective_from.isoformat(),
            "gross_monthly": str(gross),
            "ctc_annual": str(revision.ctc_annual) if revision.ctc_annual else None,
            "components": {component.code: str(amount) for component, amount, _ in resolved},
            "reason": revision.revision_reason,
        },
    )
    return revision


def salary_on(session: Session, *, employee_id: int, as_of: date) -> EmployeeSalary | None:
    """The revision in force on a date.

    A date-range query rather than "the latest row", so a payroll run for March
    uses March's salary even after a July raise has been recorded.
    """
    return session.scalar(
        select(EmployeeSalary)
        .where(
            EmployeeSalary.employee_id == employee_id,
            EmployeeSalary.effective_from <= as_of,
            or_(EmployeeSalary.effective_to.is_(None), EmployeeSalary.effective_to >= as_of),
        )
        .options(selectinload(EmployeeSalary.components))
        .order_by(EmployeeSalary.effective_from.desc())
        .limit(1)
    )


def revisions_for(session: Session, employee_id: int) -> list[EmployeeSalary]:
    return list(
        session.scalars(
            select(EmployeeSalary)
            .where(EmployeeSalary.employee_id == employee_id)
            .options(selectinload(EmployeeSalary.components))
            .order_by(EmployeeSalary.effective_from.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# Statutory rule sets
# ---------------------------------------------------------------------------


def create_rule_set(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> StatutoryRuleSet:
    """Create a draft rule set.

    Always a draft. Verification is a separate act requiring a cited source,
    because the whole point of this table is that no statutory figure enters
    payroll unchecked.
    """
    domain = data["domain"]
    if domain not in STATUTORY_DOMAINS:
        raise ValidationFailed(
            f"Unknown statutory domain {domain!r}.",
            details=[{"field": "domain", "message": "Unknown."}],
        )

    rule_set = StatutoryRuleSet(
        domain=domain,
        jurisdiction=data.get("jurisdiction", "IN"),
        state_code=data.get("state_code"),
        version_label=data["version_label"].strip(),
        effective_from=data["effective_from"],
        effective_to=data.get("effective_to"),
        status="draft",
        source_reference=data.get("source_reference"),
        source_url=data.get("source_url"),
        source_published_on=data.get("source_published_on"),
        notes=data.get("notes"),
        created_by_user_id=context.actor_user_id,
    )
    session.add(rule_set)
    try:
        session.flush()
    except IntegrityError as error:
        raise Conflict(
            f"A {domain.upper()} rule set already takes effect on "
            f"{rule_set.effective_from.isoformat()} for that jurisdiction."
        ) from error

    for item in data.get("parameters") or []:
        _add_parameter(session, rule_set=rule_set, data=item)
    session.flush()
    session.refresh(rule_set, attribute_names=["parameters"])

    audit.record(
        session,
        action="statutory.rule_set_created",
        context=context,
        entity_type="statutory_rule_set",
        entity_id=rule_set.id,
        after={
            "domain": rule_set.domain,
            "version": rule_set.version_label,
            "effective_from": rule_set.effective_from.isoformat(),
            "parameters": len(rule_set.parameters),
            "status": "draft",
        },
    )
    return rule_set


def _add_parameter(
    session: Session, *, rule_set: StatutoryRuleSet, data: dict[str, Any]
) -> StatutoryRuleParameter:
    numeric = data.get("numeric_value")
    json_value = data.get("json_value")
    if (numeric is None) == (json_value is None):
        raise ValidationFailed(
            "A parameter is either a number or a table, not both and not neither.",
            details=[{"field": "numeric_value", "message": "Provide exactly one."}],
        )
    parameter = StatutoryRuleParameter(
        rule_set_id=rule_set.id,
        key=data["key"].strip(),
        label=data["label"].strip(),
        numeric_value=Decimal(str(numeric)) if numeric is not None else None,
        json_value=json_value,
        unit=data.get("unit"),
        notes=data.get("notes"),
    )
    session.add(parameter)
    return parameter


def set_parameters(
    session: Session,
    rule_set_id: int,
    *,
    parameters: list[dict[str, Any]],
    context: audit.AuditContext,
) -> StatutoryRuleSet:
    """Replace a draft rule set's parameters.

    Only while it is a draft. A verified set's parameters are frozen by trigger:
    the verification is a statement that somebody checked *these* numbers, so
    changing them afterwards would leave the signature attached to figures
    nobody checked. A change means a new effective-dated version.
    """
    rule_set = get_rule_set(session, rule_set_id)
    if rule_set.status != "draft":
        raise Conflict(
            f"This rule set is {rule_set.status}. Create a new effective-dated "
            "version rather than changing verified figures."
        )

    before = {p.key: str(p.numeric_value or p.json_value) for p in rule_set.parameters}
    for existing in list(rule_set.parameters):
        session.delete(existing)
    session.flush()

    for item in parameters:
        _add_parameter(session, rule_set=rule_set, data=item)
    session.flush()
    session.refresh(rule_set, attribute_names=["parameters"])

    audit.record(
        session,
        action="statutory.parameters_set",
        context=context,
        entity_type="statutory_rule_set",
        entity_id=rule_set.id,
        before=before,
        after={p.key: str(p.numeric_value or p.json_value) for p in rule_set.parameters},
    )
    return rule_set


def verify_rule_set(
    session: Session,
    rule_set_id: int,
    *,
    source_reference: str,
    note: str | None,
    source_url: str | None,
    source_published_on: date | None,
    context: audit.AuditContext,
) -> StatutoryRuleSet:
    """Mark a rule set as checked against its published source.

    The source is required and recorded, and so is who verified it and when.
    This is the gate the brief asks for: with
    ``payroll.block_finalize_on_unverified_rules`` on - the default - no payroll
    can be finalized using figures nobody has signed off.

    A rule set with no parameters cannot be verified: there would be nothing to
    have checked.
    """
    rule_set = get_rule_set(session, rule_set_id)
    if rule_set.status == "verified":
        raise Conflict("This rule set is already verified.")
    if rule_set.status == "superseded":
        raise Conflict("This rule set has been superseded.")
    if not rule_set.parameters:
        raise PolicyViolation(
            "This rule set has no parameters, so there is nothing to verify. Add "
            "the rates, ceilings or slabs first."
        )
    if not source_reference.strip():
        raise ValidationFailed(
            "Verification requires the published source the figures came from.",
            details=[{"field": "source_reference", "message": "Required."}],
        )

    rule_set.status = "verified"
    rule_set.source_reference = source_reference.strip()
    rule_set.verification_note = note
    if source_url:
        rule_set.source_url = source_url
    if source_published_on:
        rule_set.source_published_on = source_published_on
    rule_set.verified_by_user_id = context.actor_user_id
    rule_set.verified_at = utc_now()
    session.flush()

    audit.record(
        session,
        action="statutory.rule_set_verified",
        context=context,
        entity_type="statutory_rule_set",
        entity_id=rule_set.id,
        after={
            "domain": rule_set.domain,
            "version": rule_set.version_label,
            "source_reference": rule_set.source_reference,
            "source_url": rule_set.source_url,
            "parameters": {
                p.key: str(p.numeric_value if p.numeric_value is not None else p.json_value)
                for p in rule_set.parameters
            },
        },
        extra={
            "note": (
                "Verification is a statement that these figures were checked "
                "against the cited source. The parameters are now frozen."
            )
        },
    )
    return rule_set


def supersede_rule_set(
    session: Session, rule_set_id: int, *, reason: str, context: audit.AuditContext
) -> StatutoryRuleSet:
    """Retire a rule set without deleting it.

    Kept because past payroll runs cite it: deleting the rules a payslip was
    computed from would make that payslip unexplainable, which is exactly what
    this schema exists to prevent.
    """
    rule_set = get_rule_set(session, rule_set_id)
    if rule_set.status == "superseded":
        raise Conflict("This rule set is already superseded.")

    rule_set.status = "superseded"
    rule_set.notes = ((rule_set.notes or "") + f"\nSuperseded: {reason.strip()}").strip()
    session.flush()

    audit.record(
        session,
        action="statutory.rule_set_superseded",
        context=context,
        entity_type="statutory_rule_set",
        entity_id=rule_set.id,
        after={"reason": reason.strip()},
    )
    return rule_set


def get_rule_set(session: Session, rule_set_id: int) -> StatutoryRuleSet:
    rule_set = session.scalar(
        select(StatutoryRuleSet)
        .where(StatutoryRuleSet.id == rule_set_id)
        .options(selectinload(StatutoryRuleSet.parameters))
    )
    if rule_set is None:
        raise NotFound("Statutory rule set not found.")
    return rule_set


def list_rule_sets(
    session: Session, *, domain: str | None = None, status: str | None = None
) -> list[StatutoryRuleSet]:
    statement = (
        select(StatutoryRuleSet)
        .options(selectinload(StatutoryRuleSet.parameters))
        .order_by(
            StatutoryRuleSet.domain,
            StatutoryRuleSet.effective_from.desc(),
        )
    )
    if domain:
        statement = statement.where(StatutoryRuleSet.domain == domain)
    if status:
        statement = statement.where(StatutoryRuleSet.status == status)
    return list(session.scalars(statement).all())


def rule_set_in_force(
    session: Session, *, domain: str, as_of: date, state_code: str | None = None
) -> StatutoryRuleSet | None:
    """The rule set that applies to a domain on a date.

    Prefers a verified set over a draft covering the same date, so a draft being
    prepared for next year cannot quietly take over this month's calculation.
    A state-specific set wins over a national one for the same domain, which is
    how professional tax works.
    """
    conditions = [
        StatutoryRuleSet.domain == domain,
        StatutoryRuleSet.status != "superseded",
        StatutoryRuleSet.effective_from <= as_of,
        or_(StatutoryRuleSet.effective_to.is_(None), StatutoryRuleSet.effective_to >= as_of),
    ]
    if state_code:
        conditions.append(
            or_(StatutoryRuleSet.state_code == state_code, StatutoryRuleSet.state_code.is_(None))
        )
    else:
        conditions.append(StatutoryRuleSet.state_code.is_(None))

    candidates = list(
        session.scalars(
            select(StatutoryRuleSet)
            .where(and_(*conditions))
            .options(selectinload(StatutoryRuleSet.parameters))
            .order_by(StatutoryRuleSet.effective_from.desc())
        ).all()
    )
    if not candidates:
        return None

    def rank(row: StatutoryRuleSet) -> tuple[int, int, date]:
        return (
            0 if row.status == "verified" else 1,
            0 if (state_code and row.state_code == state_code) else 1,
            row.effective_from,
        )

    return sorted(candidates, key=rank)[0]


def as_policy_rule_set(rule_set: StatutoryRuleSet) -> policy.RuleSet:
    """Hand a rule set to the pure engine as plain values."""
    return policy.RuleSet(
        rule_set_id=rule_set.id,
        domain=rule_set.domain,
        version_label=rule_set.version_label,
        is_verified=rule_set.status == "verified",
        parameters={
            p.key: (p.numeric_value if p.numeric_value is not None else p.json_value)
            for p in rule_set.parameters
        },
    )


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def set_eligibility(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> EmployeeStatutoryEligibility:
    """Record whether one employee is covered by one domain, from a date.

    A row is required to exclude somebody as well as to include them. An
    exemption should be a decision with a reason attached, not the absence of
    data - which is indistinguishable from nobody having looked.
    """
    employee = session.get(Employee, data["employee_id"])
    if employee is None:
        raise NotFound("Employee not found.")
    if data["domain"] not in STATUTORY_DOMAINS:
        raise ValidationFailed(
            f"Unknown statutory domain {data['domain']!r}.",
            details=[{"field": "domain", "message": "Unknown."}],
        )
    if not data.get("is_eligible") and not (data.get("reason") or "").strip():
        raise ValidationFailed(
            "Excluding an employee from a statutory domain needs a reason.",
            details=[{"field": "reason", "message": "Required when not eligible."}],
        )

    effective_from: date = data["effective_from"]
    for row in session.scalars(
        select(EmployeeStatutoryEligibility).where(
            EmployeeStatutoryEligibility.employee_id == employee.id,
            EmployeeStatutoryEligibility.domain == data["domain"],
            EmployeeStatutoryEligibility.effective_to.is_(None),
        )
    ).all():
        if row.effective_from >= effective_from:
            raise Conflict(
                f"{employee.employee_code} already has a {data['domain'].upper()} "
                f"record from {row.effective_from.isoformat()}."
            )
        row.effective_to = effective_from - timedelta(days=1)

    record = EmployeeStatutoryEligibility(
        employee_id=employee.id,
        domain=data["domain"],
        is_eligible=bool(data["is_eligible"]),
        effective_from=effective_from,
        effective_to=data.get("effective_to"),
        reason=data.get("reason"),
        created_by_user_id=context.actor_user_id,
    )
    session.add(record)
    session.flush()

    audit.record(
        session,
        action="statutory.eligibility_set",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "employee_code": employee.employee_code,
            "domain": record.domain,
            "is_eligible": record.is_eligible,
            "effective_from": effective_from.isoformat(),
            "reason": record.reason,
        },
    )
    return record


def eligibility_on(session: Session, *, employee_id: int, as_of: date) -> dict[str, bool]:
    """Every domain's eligibility for one employee on a date.

    A domain with no record is **not** eligible. Defaulting to eligible would
    deduct from somebody nobody had decided was covered, which is money taken
    on an assumption.
    """
    rows = session.scalars(
        select(EmployeeStatutoryEligibility)
        .where(
            EmployeeStatutoryEligibility.employee_id == employee_id,
            EmployeeStatutoryEligibility.effective_from <= as_of,
            or_(
                EmployeeStatutoryEligibility.effective_to.is_(None),
                EmployeeStatutoryEligibility.effective_to >= as_of,
            ),
        )
        .order_by(EmployeeStatutoryEligibility.effective_from)
    ).all()
    return {row.domain: row.is_eligible for row in rows}


def eligibility_for(session: Session, employee_id: int) -> list[EmployeeStatutoryEligibility]:
    return list(
        session.scalars(
            select(EmployeeStatutoryEligibility)
            .where(EmployeeStatutoryEligibility.employee_id == employee_id)
            .order_by(
                EmployeeStatutoryEligibility.domain,
                EmployeeStatutoryEligibility.effective_from.desc(),
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


def ensure_period(
    session: Session, *, year: int, month: int, context: audit.AuditContext
) -> PayrollPeriod:
    """The period for a month, created on first use.

    Created rather than required up front because a month's existence is not a
    decision anybody needs to make - its dates follow from the calendar.
    """
    if not 1 <= month <= 12:
        raise ValidationFailed(
            "The month must be between 1 and 12.",
            details=[{"field": "month", "message": "Out of range."}],
        )

    existing = session.scalar(
        select(PayrollPeriod).where(PayrollPeriod.year == year, PayrollPeriod.month == month)
    )
    if existing is not None:
        return existing

    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    pay_day = settings_service.get_value(session, "payroll.pay_day_of_month", default=1)
    pay_date = date(year + (month == 12), (month % 12) + 1, 1) + timedelta(days=int(pay_day) - 1)

    period = PayrollPeriod(
        year=year,
        month=month,
        start_date=start,
        end_date=end,
        pay_date=pay_date,
        created_by_user_id=context.actor_user_id,
    )
    session.add(period)
    session.flush()

    audit.record(
        session,
        action="payroll.period_created",
        context=context,
        entity_type="payroll_period",
        entity_id=period.id,
        after={"year": year, "month": month, "pay_date": pay_date.isoformat()},
    )
    return period


def list_periods(session: Session, *, limit: int = 36) -> list[PayrollPeriod]:
    return list(
        session.scalars(
            select(PayrollPeriod)
            .order_by(PayrollPeriod.year.desc(), PayrollPeriod.month.desc())
            .limit(limit)
        ).all()
    )


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------


def create_adjustment(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> PayrollAdjustment:
    """Post an arrear, bonus, recovery or reversal for a future run.

    The only way to correct a finalized run. The amount is always positive and
    the type decides the direction: a signed amount plus a type would allow a
    "recovery of -500", which pays somebody by accident.

    The key is derived from the employee, type, amount and reason, so importing
    the same list of arrears twice pays each person once.
    """
    employee = session.get(Employee, data["employee_id"])
    if employee is None:
        raise NotFound("Employee not found.")

    amount = Decimal(str(data["amount"]))
    if amount <= ZERO:
        raise ValidationFailed(
            "The amount must be positive. The adjustment type decides whether it "
            "is paid or recovered.",
            details=[{"field": "amount", "message": "Must be greater than zero."}],
        )

    reason = data["reason"].strip()
    key = data.get("idempotency_key") or (
        f"{employee.id}:{data['adjustment_type']}:{amount}:"
        f"{data.get('relates_to_period_id') or 'none'}:{reason[:60]}"
    )

    adjustment = PayrollAdjustment(
        employee_id=employee.id,
        adjustment_type=data["adjustment_type"],
        amount=amount,
        label=data["label"].strip(),
        reason=reason,
        relates_to_period_id=data.get("relates_to_period_id"),
        is_taxable=data.get("is_taxable", True),
        idempotency_key=key[:160],
        created_by_user_id=context.actor_user_id,
    )
    session.add(adjustment)
    try:
        session.flush()
    except IntegrityError as error:
        raise Conflict(
            "An identical adjustment already exists for this employee. It has not been added twice."
        ) from error

    audit.record(
        session,
        action="payroll.adjustment_created",
        context=context,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "employee_code": employee.employee_code,
            "type": adjustment.adjustment_type,
            "amount": str(amount),
            "label": adjustment.label,
            "relates_to_period_id": adjustment.relates_to_period_id,
        },
    )
    return adjustment


def cancel_adjustment(
    session: Session, adjustment_id: int, *, reason: str, context: audit.AuditContext
) -> PayrollAdjustment:
    """Cancel a pending adjustment. One already applied cannot be cancelled."""
    adjustment = session.get(PayrollAdjustment, adjustment_id)
    if adjustment is None:
        raise NotFound("Adjustment not found.")
    if adjustment.status == "applied":
        raise Conflict(
            "This adjustment has already been paid in a run. Post a reversal "
            "instead, so both movements stay on the record."
        )
    if adjustment.status == "cancelled":
        raise Conflict("This adjustment is already cancelled.")

    adjustment.status = "cancelled"
    adjustment.reason = f"{adjustment.reason}\nCancelled: {reason.strip()}"
    session.flush()

    audit.record(
        session,
        action="payroll.adjustment_cancelled",
        context=context,
        entity_type="payroll_adjustment",
        entity_id=adjustment.id,
        after={"reason": reason.strip()},
    )
    return adjustment


def pending_adjustments(
    session: Session, *, employee_id: int | None = None
) -> list[PayrollAdjustment]:
    statement = (
        select(PayrollAdjustment)
        .where(PayrollAdjustment.status == "pending")
        .order_by(PayrollAdjustment.created_at)
    )
    if employee_id is not None:
        statement = statement.where(PayrollAdjustment.employee_id == employee_id)
    return list(session.scalars(statement).all())


# ---------------------------------------------------------------------------
# Configuration health
# ---------------------------------------------------------------------------


def configuration_report(session: Session) -> dict[str, Any]:
    """What is missing before payroll can be run honestly.

    Deliberately blunt. Payroll that runs on unconfirmed placeholders produces
    numbers that look finished, so the gaps are listed plainly rather than
    hidden behind a green tick.
    """
    enabled = settings_service.get_value(session, "payroll.enabled_statutory_modules", default=[])
    enabled = list(enabled) if isinstance(enabled, list) else []

    today = date.today()
    domains: list[dict[str, Any]] = []
    for domain in enabled:
        rule_set = rule_set_in_force(session, domain=domain, as_of=today)
        domains.append(
            {
                "domain": domain,
                "has_rule_set": rule_set is not None,
                "status": rule_set.status if rule_set else None,
                "version": rule_set.version_label if rule_set else None,
                "parameter_count": len(rule_set.parameters) if rule_set else 0,
                "source_reference": rule_set.source_reference if rule_set else None,
            }
        )

    component_count = session.scalar(
        select(func.count()).select_from(SalaryComponent).where(SalaryComponent.is_active.is_(True))
    )
    employees_without_salary = session.scalar(
        select(func.count())
        .select_from(Employee)
        .where(
            Employee.employment_status != "exited",
            ~Employee.id.in_(select(EmployeeSalary.employee_id)),
        )
    )
    unconfirmed = [
        row.key
        for row in settings_service.list_settings(session, category="payroll")
        if not row.is_confirmed
    ]

    blockers: list[str] = []
    if not component_count:
        blockers.append(
            "No salary components are defined, so no payslip can be produced. "
            "Their names and proportions are a company decision."
        )
    if enabled and any(not item["has_rule_set"] for item in domains):
        missing = [item["domain"] for item in domains if not item["has_rule_set"]]
        blockers.append(
            f"Enabled statutory module(s) with no rule set in force: {', '.join(missing)}."
        )
    draft = [item["domain"] for item in domains if item["status"] == "draft"]
    if draft:
        blockers.append(
            f"Unverified rule set(s) in use: {', '.join(draft)}. A run using them "
            "cannot be finalized while "
            "'payroll.block_finalize_on_unverified_rules' is enabled."
        )
    if unconfirmed:
        blockers.append(
            f"{len(unconfirmed)} payroll setting(s) await confirmation: {', '.join(unconfirmed)}."
        )
    if employees_without_salary:
        blockers.append(f"{employees_without_salary} active employee(s) have no salary revision.")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "enabled_statutory_modules": enabled,
        "domains": domains,
        "active_components": component_count or 0,
        "employees_without_salary": employees_without_salary or 0,
        "unconfirmed_settings": unconfirmed,
    }
