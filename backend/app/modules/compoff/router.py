"""Compensatory off endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import NotFound
from app.modules.auth.models import User
from app.modules.compoff import service
from app.modules.compoff.schemas import (
    CancelRequest,
    CompoffApplicationResponse,
    CompoffApplicationSummary,
    CompoffApplyOnBehalfRequest,
    CompoffApplyRequest,
    CompoffBalanceResponse,
    CreditDecisionRequest,
    CreditResponse,
    CreditReversalRequest,
    DecisionRequest,
    ExpiryRunRequest,
    ExpiryRunResponse,
    GrantRunRequest,
    GrantRunResponse,
    ManualCreditRequest,
    WithdrawRequest,
)
from app.modules.employees import service as employees_service

router = APIRouter(prefix="/compoff", tags=["Comp-off"])


def _visible_employee_ids(session: DbSession, user: User) -> list[int] | None:
    """Which employees this caller may see comp-off for. None means everyone."""
    if user.has_permission("compoff.read_all"):
        return None
    visible: set[int] = set()
    if user.employee_id is not None:
        visible.add(user.employee_id)
        if user.has_permission("compoff.approve_as_manager"):
            visible |= employees_service.team_employee_ids(session, user.employee_id)
    return sorted(visible)


def _assert_can_see(session: DbSession, user: User, employee_id: int) -> None:
    allowed = _visible_employee_ids(session, user)
    if allowed is not None and employee_id not in allowed:
        # 404 rather than 403, so employee ids cannot be enumerated.
        raise NotFound("Employee not found.")


# --------------------------------------------------------------- self service


@router.get(
    "/me/balance",
    response_model=CompoffBalanceResponse,
    summary="Your comp-off balance",
)
def my_balance(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("compoff.read_self"))],
    as_of: Annotated[date | None, Query()] = None,
) -> CompoffBalanceResponse:
    """Available, awaiting verification and committed, kept apart.

    Credit for work HR has not yet confirmed is not spendable, so showing one
    total would have you plan around a day you may not get.
    """
    employee = service.employee_for_user(session, user)
    balance = service.balance_for(session, employee_id=employee.id, as_of=as_of)
    return CompoffBalanceResponse(
        as_of=balance.as_of,
        available_days=balance.available_days,
        pending_verification_days=balance.pending_verification_days,
        committed_days=balance.committed_days,
        expiring_soon_days=balance.expiring_soon_days,
        expiring_on=balance.expiring_on,
        expired_days=balance.expired_days,
    )


@router.get(
    "/me/credits",
    response_model=list[CreditResponse],
    summary="Your comp-off credits",
)
def my_credits(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("compoff.read_self"))],
) -> list[CreditResponse]:
    """Each credit with the day it was earned on and when it expires.

    Comp-off is tracked as lots rather than a single balance precisely because
    each one expires on its own date.
    """
    employee = service.employee_for_user(session, user)
    return [
        CreditResponse.model_validate(item)
        for item in service.credits_for(session, employee_id=employee.id)
    ]


@router.get(
    "/me/applications",
    response_model=list[CompoffApplicationSummary],
    summary="Your comp-off requests",
)
def my_applications(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("compoff.read_self"))],
) -> list[CompoffApplicationSummary]:
    employee = service.employee_for_user(session, user)
    applications = service.list_applications(session, employee_ids=[employee.id])
    return [CompoffApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.post(
    "/me/applications",
    response_model=CompoffApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apply for comp-off",
)
def apply(
    payload: CompoffApplyRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.apply"))],
) -> CompoffApplicationResponse:
    """Request time off against credits you have already earned.

    The credits are **held at submission**, not at approval: one about to expire
    must be reserved for this request rather than left available for another, and
    you need to know now whether your credits cover it.

    Unlike leave there is no unpaid fallback. Comp-off is time already earned, so
    a request beyond your credits is refused rather than split.
    """
    application = service.apply_for_compoff(
        session, user=user, data=payload.model_dump(exclude_unset=True), context=context
    )
    return CompoffApplicationResponse.model_validate(application)


@router.post(
    "/applications/{application_id}/withdraw",
    response_model=CompoffApplicationResponse,
    summary="Withdraw an undecided request",
)
def withdraw(
    application_id: int,
    payload: WithdrawRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.apply"))],
) -> CompoffApplicationResponse:
    """Releases the credits it was holding, so they return to your balance."""
    application = service.withdraw(
        session,
        application_id=application_id,
        user=user,
        reason=payload.reason,
        context=context,
    )
    return CompoffApplicationResponse.model_validate(application)


@router.post(
    "/applications/{application_id}/cancel",
    response_model=CompoffApplicationResponse,
    summary="Cancel approved comp-off",
)
def cancel(
    application_id: int,
    payload: CancelRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require_any("compoff.apply", "compoff.approve_hr"))],
) -> CompoffApplicationResponse:
    """Returns the credits with their **original expiry dates**.

    Cancelling does not extend them: an employee who cancels a day a week before
    a credit lapses still has only that week to use it.
    """
    application = service.cancel(
        session,
        application_id=application_id,
        user=user,
        reason=payload.reason,
        context=context,
    )
    return CompoffApplicationResponse.model_validate(application)


# ------------------------------------------------------------------ approvals


@router.get(
    "/approvals/pending",
    response_model=list[CompoffApplicationSummary],
    summary="Comp-off requests you can decide now",
)
def pending_approvals(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("compoff.approve_as_manager", "compoff.approve_hr"))],
) -> list[CompoffApplicationSummary]:
    applications = service.pending_for_approver(session, user=user)
    return [CompoffApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.post(
    "/applications/{application_id}/decide",
    response_model=CompoffApplicationResponse,
    summary="Approve or reject one stage",
)
def decide(
    application_id: int,
    payload: DecisionRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require_any("compoff.approve_as_manager", "compoff.approve_hr"))],
) -> CompoffApplicationResponse:
    """Enforced in the backend: no self-approval, the manager stage needs the
    actual reporting relationship, and stages run in order.

    Rejection releases the held credits, so they go back to the employee rather
    than being stranded against a request that will never happen.
    """
    application = service.decide(
        session,
        application_id=application_id,
        user=user,
        approve=payload.approve,
        comment=payload.comment,
        context=context,
    )
    return CompoffApplicationResponse.model_validate(application)


# --------------------------------------------------------------- credit admin


@router.get(
    "/credits/pending-verification",
    response_model=list[CreditResponse],
    summary="Credits awaiting verification",
)
def pending_verification(
    session: DbSession,
    _dep: Annotated[User, Depends(require("compoff.verify"))],
) -> list[CreditResponse]:
    """Earned from attendance but not yet usable.

    The brief requires comp-off to come from verified qualifying work, so a
    credit waits here until someone confirms it.
    """
    return [CreditResponse.model_validate(item) for item in service.pending_verification(session)]


@router.post(
    "/credits/{credit_id}/decide",
    response_model=CreditResponse,
    summary="Verify or reject an earned credit",
)
def decide_credit(
    credit_id: int,
    payload: CreditDecisionRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.verify"))],
) -> CreditResponse:
    credit = service.decide_credit(
        session,
        credit_id=credit_id,
        approve=payload.approve,
        note=payload.note,
        context=context,
    )
    return CreditResponse.model_validate(credit)


@router.post(
    "/credits/{credit_id}/reverse",
    response_model=CreditResponse,
    summary="Reverse a credit granted in error",
)
def reverse_credit(
    credit_id: int,
    payload: CreditReversalRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.reverse"))],
) -> CreditResponse:
    """The credit is marked reversed, never deleted.

    Deleting it would remove the evidence that it was granted, which is the one
    thing an employee disputing their balance needs to see. A credit already
    spent cannot be reversed - the time off has been taken.
    """
    credit = service.reverse_credit(
        session, credit_id=credit_id, reason=payload.reason, context=context
    )
    return CreditResponse.model_validate(credit)


@router.post(
    "/credits/manual",
    response_model=CreditResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a credit by hand",
)
def manual_credit(
    payload: ManualCreditRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.verify"))],
) -> CreditResponse:
    """For a case attendance cannot prove. Flagged as manual and needs a reason."""
    credit = service.grant_manual_credit(
        session,
        employee_id=payload.employee_id,
        earned_date=payload.earned_date,
        credit_days=payload.credit_days,
        reason=payload.reason,
        context=context,
    )
    return CreditResponse.model_validate(credit)


@router.post(
    "/credits/grant",
    response_model=GrantRunResponse,
    summary="Create credits from attendance",
)
def grant_credits(
    payload: GrantRunRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.verify"))],
) -> GrantRunResponse:
    """Read attendance and create credits for qualifying work.

    Safe to run repeatedly: a day earns credit once, enforced by a unique
    constraint, so a re-run reports ``already_credited`` instead of doubling an
    entitlement. Only days whose type is configured as qualifying are
    considered - overtime on an ordinary working day is overtime, not a day off
    owed.
    """
    result = service.grant_credits(
        session,
        start=payload.start,
        end=payload.end,
        employee_id=payload.employee_id,
        context=context,
    )
    return GrantRunResponse(**result)


@router.post(
    "/credits/expire",
    response_model=ExpiryRunResponse,
    summary="Close credits past their expiry date",
)
def expire_credits(
    payload: ExpiryRunRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.verify"))],
) -> ExpiryRunResponse:
    """Idempotent: it only touches credits still available, so a re-run finds
    nothing left to expire. Only the unspent remainder is lost."""
    return ExpiryRunResponse(
        **service.expire_credits(session, as_of=payload.as_of, context=context)
    )


# ------------------------------------------------------------- administration


@router.get(
    "/applications",
    response_model=list[CompoffApplicationSummary],
    summary="Comp-off requests, scoped to what you may see",
)
def list_applications(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("compoff.read_all", "compoff.read_self"))],
    employee_id: Annotated[int | None, Query()] = None,
    application_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CompoffApplicationSummary]:
    if employee_id is not None:
        _assert_can_see(session, user, employee_id)
        employee_ids: list[int] | None = [employee_id]
    else:
        employee_ids = _visible_employee_ids(session, user)

    applications = service.list_applications(
        session,
        employee_ids=employee_ids,
        statuses=[application_status] if application_status else None,
        limit=limit,
    )
    return [CompoffApplicationSummary(**row) for row in service.summarise(session, applications)]


@router.get(
    "/applications/{application_id}",
    response_model=CompoffApplicationResponse,
    summary="One request in full, with its credits and decisions",
)
def read_application(
    application_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("compoff.read_all", "compoff.read_self"))],
) -> CompoffApplicationResponse:
    application = service.get_application(session, application_id)
    _assert_can_see(session, user, application.employee_id)
    return CompoffApplicationResponse.model_validate(application)


@router.post(
    "/applications/on-behalf",
    response_model=CompoffApplicationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record comp-off for an employee",
)
def apply_on_behalf(
    payload: CompoffApplyOnBehalfRequest,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("compoff.approve_hr"))],
) -> CompoffApplicationResponse:
    data = payload.model_dump(exclude_unset=True)
    data.pop("employee_id", None)
    application = service.apply_for_compoff(
        session,
        user=user,
        data=data,
        context=context,
        on_behalf_of_employee_id=payload.employee_id,
    )
    return CompoffApplicationResponse.model_validate(application)


@router.get(
    "/employees/{employee_id}/balance",
    response_model=CompoffBalanceResponse,
    summary="One employee's comp-off balance",
)
def employee_balance(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("compoff.read_all", "compoff.approve_as_manager"))],
    as_of: Annotated[date | None, Query()] = None,
) -> CompoffBalanceResponse:
    _assert_can_see(session, user, employee_id)
    balance = service.balance_for(session, employee_id=employee_id, as_of=as_of)
    return CompoffBalanceResponse(
        as_of=balance.as_of,
        available_days=balance.available_days,
        pending_verification_days=balance.pending_verification_days,
        committed_days=balance.committed_days,
        expiring_soon_days=balance.expiring_soon_days,
        expiring_on=balance.expiring_on,
        expired_days=balance.expired_days,
    )


@router.get(
    "/employees/{employee_id}/credits",
    response_model=list[CreditResponse],
    summary="One employee's comp-off credits",
)
def employee_credits(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require_any("compoff.read_all", "compoff.approve_as_manager"))],
) -> list[CreditResponse]:
    _assert_can_see(session, user, employee_id)
    return [
        CreditResponse.model_validate(item)
        for item in service.credits_for(session, employee_id=employee_id)
    ]
