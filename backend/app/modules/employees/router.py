"""Employee endpoints.

Access tiers, all enforced here and never relied upon from the client:

* ``employee.read``            - the whole directory.
* ``employee.read_team``       - own record plus direct and indirect reports.
* ``employee.read_sensitive``  - statutory identifiers and bank details.
* ``profile.read_self`` / ``profile.update_self`` - own record only.

A record outside the caller's scope returns **404, not 403**: confirming that an
employee id exists would leak the size and shape of the organisation to anyone
enumerating ids.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import AuditCtx, CurrentUser, DbSession, require, require_any
from app.core.errors import Conflict, NotFound, PermissionDenied, PolicyViolation
from app.core.pagination import Page, PageParams, paginate
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees import importer, service, storage
from app.modules.employees.models import (
    Employee,
    EmployeeDocument,
    EmploymentHistory,
    StoredFile,
)
from app.modules.employees.schemas import (
    BankAccountCreate,
    BankAccountResponse,
    EmployeeCreate,
    EmployeeDetail,
    EmployeeDocumentResponse,
    EmployeeSummary,
    EmployeeUpdate,
    EmploymentHistoryEntry,
    ExitRequest,
    ImportReportResponse,
    LinkAccountRequest,
    SelfProfileUpdate,
    StatutoryDetailsResponse,
    StatutoryDetailsUpdate,
)
from app.modules.org.models import Department, Designation, WorkLocation

router = APIRouter(prefix="/employees", tags=["Employees"])


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------


def _reference_names(session: Session, employee: Employee) -> dict[str, str | None]:
    department = session.get(Department, employee.department_id) if employee.department_id else None
    designation = (
        session.get(Designation, employee.designation_id) if employee.designation_id else None
    )
    location = (
        session.get(WorkLocation, employee.work_location_id) if employee.work_location_id else None
    )
    manager = (
        session.get(Employee, employee.reporting_manager_id)
        if employee.reporting_manager_id
        else None
    )
    return {
        "department_name": department.name if department else None,
        "designation_name": designation.name if designation else None,
        "work_location_name": location.name if location else None,
        "work_location_timezone": location.timezone if location else None,
        "reporting_manager_name": manager.full_name if manager else None,
        "reporting_manager_code": manager.employee_code if manager else None,
    }


def _to_summary(employee: Employee, names: dict[str, str | None]) -> EmployeeSummary:
    return EmployeeSummary(
        id=employee.id,
        employee_code=employee.employee_code,
        full_name=employee.full_name,
        work_email=employee.work_email,
        department_id=employee.department_id,
        department_name=names.get("department_name"),
        designation_id=employee.designation_id,
        designation_name=names.get("designation_name"),
        work_location_id=employee.work_location_id,
        work_location_name=names.get("work_location_name"),
        reporting_manager_id=employee.reporting_manager_id,
        employment_type=employee.employment_type,
        employment_status=employee.employment_status,
        date_of_joining=employee.date_of_joining,
        date_of_exit=employee.date_of_exit,
    )


def _to_detail(session: Session, employee: Employee) -> EmployeeDetail:
    names = _reference_names(session, employee)
    report_count = (
        session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(
                Employee.reporting_manager_id == employee.id,
                Employee.employment_status != "exited",
            )
        )
        or 0
    )
    linked_user_id = session.scalar(select(User.id).where(User.employee_id == employee.id))

    return EmployeeDetail(
        **_to_summary(employee, names).model_dump(),
        first_name=employee.first_name,
        middle_name=employee.middle_name,
        last_name=employee.last_name,
        date_of_birth=employee.date_of_birth,
        gender=employee.gender,
        marital_status=employee.marital_status,
        personal_email=employee.personal_email,
        work_phone=employee.work_phone,
        personal_phone=employee.personal_phone,
        emergency_contact_name=employee.emergency_contact_name,
        emergency_contact_phone=employee.emergency_contact_phone,
        emergency_contact_relation=employee.emergency_contact_relation,
        address_line1=employee.address_line1,
        address_line2=employee.address_line2,
        city=employee.city,
        state=employee.state,
        postal_code=employee.postal_code,
        probation_end_date=employee.probation_end_date,
        date_of_confirmation=employee.date_of_confirmation,
        exit_type=employee.exit_type,
        exit_reason=employee.exit_reason,
        last_working_day=employee.last_working_day,
        notes=employee.notes,
        reporting_manager_name=names.get("reporting_manager_name"),
        reporting_manager_code=names.get("reporting_manager_code"),
        work_location_timezone=names.get("work_location_timezone"),
        direct_report_count=report_count,
        linked_user_id=linked_user_id,
        has_statutory_details=employee.statutory_details is not None,
        has_bank_account=bool(employee.bank_accounts),
        created_at=employee.created_at,
        updated_at=employee.updated_at,
    )


# ---------------------------------------------------------------------------
# Self service
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=EmployeeDetail,
    summary="Your own employee record",
)
def my_profile(session: DbSession, user: CurrentUser) -> EmployeeDetail:
    """Available to every signed-in employee.

    Returns 409 when the account has no employee record yet - which is the case
    for the bootstrap administrator until HR links one.
    """
    if user.employee_id is None:
        # A state conflict, not a malformed request: 409 lets the client tell
        # "you have no employee record" apart from "your input was invalid".
        raise Conflict("Your account is not linked to an employee record yet. Contact HR.")
    return _to_detail(session, service.get_employee(session, user.employee_id))


@router.patch(
    "/me",
    response_model=EmployeeDetail,
    summary="Update your own permitted personal details",
)
def update_my_profile(
    payload: SelfProfileUpdate,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
) -> EmployeeDetail:
    """Only contact, address and emergency-contact fields are self-editable.

    Anything affecting pay, position or reporting line is HR-only, and the
    allow-list is applied in the service layer rather than inferred from this
    schema.
    """
    employee = service.update_own_profile(
        session,
        user=user,
        data=payload.model_dump(exclude_unset=True),
        context=ctx,
    )
    return _to_detail(session, employee)


@router.get(
    "/me/team",
    response_model=list[EmployeeSummary],
    summary="Your direct reports",
)
def my_team(session: DbSession, user: CurrentUser) -> list[EmployeeSummary]:
    """Direct reports only, for the manager view on the dashboard."""
    if user.employee_id is None:
        return []
    reports = session.scalars(
        select(Employee)
        .where(
            Employee.reporting_manager_id == user.employee_id,
            Employee.employment_status != "exited",
        )
        .order_by(Employee.first_name)
    ).all()
    return [_to_summary(report, _reference_names(session, report)) for report in reports]


# ---------------------------------------------------------------------------
# Directory
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=Page[EmployeeSummary],
    summary="List employees",
)
def list_employees(
    session: DbSession,
    params: Annotated[PageParams, Depends()],
    actor: Annotated[User, Depends(require_any("employee.read", "employee.read_team"))],
    q: Annotated[
        str | None, Query(max_length=120, description="Search code, name or email")
    ] = None,
    department_id: Annotated[int | None, Query()] = None,
    work_location_id: Annotated[int | None, Query()] = None,
    employment_status: Annotated[str | None, Query()] = None,
    employment_type: Annotated[str | None, Query()] = None,
    include_exited: Annotated[bool, Query(description="Include employees who have left")] = False,
) -> Page[EmployeeSummary]:
    """Results are narrowed to what the caller may see.

    A user with only ``employee.read_team`` sees themselves and their reports;
    HR sees everyone. Exited employees are excluded by default but retained
    and retrievable.
    """
    scope = service.visible_employee_scope(session, actor)
    statement = service.build_directory_query(
        scope=scope,
        search=q,
        department_id=department_id,
        work_location_id=work_location_id,
        employment_status=employment_status,
        employment_type=employment_type,
        include_exited=include_exited,
    )

    sortable = {
        "employee_code": Employee.employee_code,
        "first_name": Employee.first_name,
        "date_of_joining": Employee.date_of_joining,
        "employment_status": Employee.employment_status,
    }
    rows, total = paginate(
        session,
        statement,
        params,
        sortable=sortable,
        default_sort=Employee.employee_code,
    )
    return Page.build(
        [_to_summary(row, _reference_names(session, row)) for row in rows],
        total=total,
        params=params,
    )


@router.get(
    "/next-code",
    response_model=dict,
    summary="Suggest the next employee code",
)
def next_code(
    session: DbSession,
    _actor: Annotated[User, Depends(require("employee.manage"))],
    prefix: Annotated[str, Query(min_length=1, max_length=10)] = "ITC",
) -> dict:
    """A suggestion only; HR may use any code. Derived from the highest in use."""
    from app.modules.employees.policy import next_employee_code

    codes = [code for (code,) in session.execute(select(Employee.employee_code)).all()]
    return {"suggested_code": next_employee_code(codes, prefix=prefix)}


@router.get(
    "/import-template",
    response_class=PlainTextResponse,
    summary="Download the employee CSV import template",
)
def import_template(
    _actor: Annotated[User, Depends(require("employee.import"))],
) -> PlainTextResponse:
    return PlainTextResponse(
        importer.csv_template(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="employee-import-template.csv"'},
    )


@router.get(
    "/{employee_id}",
    response_model=EmployeeDetail,
    summary="Get one employee",
    responses={404: {"description": "Not found, or outside your access scope"}},
)
def get_employee(
    employee_id: int,
    session: DbSession,
    actor: Annotated[User, Depends(require_any("employee.read", "employee.read_team"))],
) -> EmployeeDetail:
    service.assert_can_read(session, actor, employee_id)
    return _to_detail(session, service.get_employee(session, employee_id))


@router.get(
    "/{employee_id}/history",
    response_model=list[EmploymentHistoryEntry],
    summary="Employment history for one employee",
)
def employment_history(
    employee_id: int,
    session: DbSession,
    actor: Annotated[User, Depends(require_any("employee.read", "employee.read_team"))],
) -> list[EmploymentHistoryEntry]:
    """Effective-dated periods, so a past department or manager is recoverable."""
    service.assert_can_read(session, actor, employee_id)
    rows = session.scalars(
        select(EmploymentHistory)
        .where(EmploymentHistory.employee_id == employee_id)
        .order_by(EmploymentHistory.effective_from.desc(), EmploymentHistory.id.desc())
    ).all()
    return [EmploymentHistoryEntry.model_validate(row) for row in rows]


# ---------------------------------------------------------------------------
# Lifecycle management
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=EmployeeDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Onboard an employee",
)
def create_employee(
    payload: EmployeeCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage"))],
) -> EmployeeDetail:
    """Creates the employee and opens their first employment-history period."""
    employee = service.create_employee(session, data=payload.model_dump(), context=ctx)
    return _to_detail(session, employee)


@router.patch(
    "/{employee_id}",
    response_model=EmployeeDetail,
    summary="Update an employee",
)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage"))],
) -> EmployeeDetail:
    """A change to department, designation, location, manager, type or status
    opens a new employment-history period rather than overwriting the old one.

    A reporting-manager assignment that would create a loop is rejected.
    """
    data = payload.model_dump(exclude_unset=True)
    effective_from = data.pop("effective_from", None)
    change_reason = data.pop("change_reason", None)
    employee = service.update_employee(
        session,
        employee_id,
        data=data,
        context=ctx,
        effective_from=effective_from,
        change_reason=change_reason,
    )
    return _to_detail(session, employee)


@router.post(
    "/{employee_id}/exit",
    response_model=EmployeeDetail,
    summary="Record an employee exit",
)
def exit_employee(
    employee_id: int,
    payload: ExitRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage"))],
) -> EmployeeDetail:
    """Offboards the employee and deactivates their sign-in account.

    Nothing is deleted: attendance, leave ledger, payslips and audit history are
    retained and still attributable. Any direct reports left without a manager
    are listed in the audit entry and surfaced on the HR dashboard.
    """
    employee = service.exit_employee(
        session,
        employee_id,
        date_of_exit=payload.date_of_exit,
        exit_type=payload.exit_type,
        exit_reason=payload.exit_reason,
        last_working_day=payload.last_working_day,
        context=ctx,
        deactivate_account=payload.deactivate_account,
    )
    return _to_detail(session, employee)


@router.post(
    "/{employee_id}/link-account",
    response_model=EmployeeDetail,
    summary="Link a sign-in account to an employee",
)
def link_account(
    employee_id: int,
    payload: LinkAccountRequest,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage", "user.manage"))],
) -> EmployeeDetail:
    """Once linked, the employee can sign in with their employee code."""
    service.link_user_account(
        session, employee_id=employee_id, user_id=payload.user_id, context=ctx
    )
    return _to_detail(session, service.get_employee(session, employee_id))


# ---------------------------------------------------------------------------
# Restricted details
# ---------------------------------------------------------------------------


def _statutory_response(employee: Employee) -> StatutoryDetailsResponse:
    """Build the restricted response, masking PAN.

    A shared helper rather than one endpoint calling the other, so that writing
    the details does not also emit a spurious "viewed" audit entry.
    """
    record = employee.statutory_details
    if record is None:
        return StatutoryDetailsResponse(
            employee_id=employee.id,
            pf_uan=None,
            pf_number=None,
            esi_number=None,
            pan_masked=None,
            aadhaar_last4=None,
            pf_opted_in=None,
            esi_eligible_override=None,
            pt_state=None,
            tax_regime=None,
        )
    return StatutoryDetailsResponse(
        employee_id=employee.id,
        pf_uan=record.pf_uan,
        pf_number=record.pf_number,
        esi_number=record.esi_number,
        pan_masked=f"XXXXXX{record.pan_last4}" if record.pan_last4 else None,
        aadhaar_last4=record.aadhaar_last4,
        pf_opted_in=record.pf_opted_in,
        esi_eligible_override=record.esi_eligible_override,
        pt_state=record.pt_state,
        tax_regime=record.tax_regime,
    )


def _masked_account(last4: str) -> str:
    """Display form for an encrypted account number."""
    return f"XXXXXXXX{last4}"


@router.get(
    "/{employee_id}/statutory",
    response_model=StatutoryDetailsResponse,
    summary="Statutory identifiers (restricted)",
)
def get_statutory(
    employee_id: int,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.read_sensitive"))],
) -> StatutoryDetailsResponse:
    """PAN is returned masked. The full value is never sent over the API."""
    employee = service.get_employee(session, employee_id)

    # Reading restricted data is itself auditable.
    audit.record(
        session,
        action="employee.statutory_details_viewed",
        context=ctx,
        entity_type="employee",
        entity_id=employee.id,
    )
    return _statutory_response(employee)


@router.put(
    "/{employee_id}/statutory",
    response_model=StatutoryDetailsResponse,
    summary="Set statutory identifiers (restricted)",
    responses={409: {"description": "Field encryption is not configured"}},
)
def set_statutory(
    employee_id: int,
    payload: StatutoryDetailsUpdate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage_sensitive"))],
) -> StatutoryDetailsResponse:
    """PAN is encrypted at rest. Only four Aadhaar digits are accepted.

    Requires ``IAMS_FIELD_ENCRYPTION_KEY``; without it the request is refused
    rather than storing plaintext.
    """
    service.upsert_statutory_details(
        session, employee_id, data=payload.model_dump(exclude_unset=True), context=ctx
    )
    return _statutory_response(service.get_employee(session, employee_id))


@router.get(
    "/{employee_id}/bank-accounts",
    response_model=list[BankAccountResponse],
    summary="Bank accounts (restricted)",
)
def list_bank_accounts(
    employee_id: int,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.read_sensitive"))],
) -> list[BankAccountResponse]:
    """Account numbers are returned masked; the full number never leaves the server."""
    employee = service.get_employee(session, employee_id)
    audit.record(
        session,
        action="employee.bank_details_viewed",
        context=ctx,
        entity_type="employee",
        entity_id=employee.id,
    )
    return [
        BankAccountResponse(
            id=account.id,
            employee_id=employee.id,
            account_holder_name=account.account_holder_name,
            account_number_masked=_masked_account(account.account_number_last4),
            ifsc_code=account.ifsc_code,
            bank_name=account.bank_name,
            branch_name=account.branch_name,
            account_type=account.account_type,
            is_primary=account.is_primary,
            is_verified=account.is_verified,
        )
        for account in sorted(employee.bank_accounts, key=lambda a: (not a.is_primary, a.id))
    ]


@router.post(
    "/{employee_id}/bank-accounts",
    response_model=BankAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a bank account (restricted)",
)
def add_bank_account(
    employee_id: int,
    payload: BankAccountCreate,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.manage_sensitive"))],
) -> BankAccountResponse:
    """The account number is encrypted at rest and never returned in full."""
    account = service.add_bank_account(session, employee_id, data=payload.model_dump(), context=ctx)
    return BankAccountResponse(
        id=account.id,
        employee_id=employee_id,
        account_holder_name=account.account_holder_name,
        account_number_masked=_masked_account(account.account_number_last4),
        ifsc_code=account.ifsc_code,
        bank_name=account.bank_name,
        branch_name=account.branch_name,
        account_type=account.account_type,
        is_primary=account.is_primary,
        is_verified=account.is_verified,
    )


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=ImportReportResponse,
    summary="Import employees from CSV",
    responses={422: {"description": "The file could not be read or has invalid rows"}},
)
def import_employees(
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.import"))],
    file: Annotated[UploadFile, File(description="CSV file, UTF-8")],
    dry_run: Annotated[
        bool,
        Form(description="Validate and preview without writing. Run this first."),
    ] = True,
) -> ImportReportResponse:
    """Validates every row and reports all problems at once.

    The import is all-or-nothing: if any row fails, nothing is written. A
    partial import of employee master data is worse than none, because the
    operator cannot tell what landed without comparing row by row.

    Preview and commit share one code path, so they cannot disagree about what
    is valid.
    """
    content = file.file.read(importer.MAX_BYTES + 1)
    report = importer.import_employees(session, content=content, context=ctx, dry_run=dry_run)
    return ImportReportResponse(**report.as_dict())


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def _can_access_document(user: User, document: EmployeeDocument) -> bool:
    if user.has_permission("employee.document_manage") or user.has_permission(
        "employee.read_sensitive"
    ):
        return True
    # An employee may read their own documents, but only those marked visible.
    return user.employee_id == document.employee_id and document.visibility == "employee_visible"


@router.get(
    "/{employee_id}/documents",
    response_model=list[EmployeeDocumentResponse],
    summary="List employment documents",
)
def list_documents(
    employee_id: int,
    session: DbSession,
    user: CurrentUser,
) -> list[EmployeeDocumentResponse]:
    """An employee sees only their own documents marked employee-visible."""
    is_own = user.employee_id == employee_id
    if not is_own and not user.has_permission("employee.read"):
        raise NotFound("Employee not found.")

    statement = select(EmployeeDocument).where(EmployeeDocument.employee_id == employee_id)
    if is_own and not user.has_permission("employee.document_manage"):
        statement = statement.where(EmployeeDocument.visibility == "employee_visible")

    return [
        EmployeeDocumentResponse(
            id=document.id,
            employee_id=document.employee_id,
            document_type=document.document_type,
            title=document.title,
            visibility=document.visibility,
            valid_from=document.valid_from,
            valid_to=document.valid_to,
            original_filename=document.file.original_filename,
            content_type=document.file.content_type,
            size_bytes=document.file.size_bytes,
            created_at=document.created_at,
        )
        for document in session.scalars(statement.order_by(EmployeeDocument.id.desc())).all()
    ]


@router.post(
    "/{employee_id}/documents",
    response_model=EmployeeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an employment document",
)
def upload_document(
    employee_id: int,
    session: DbSession,
    ctx: AuditCtx,
    _actor: Annotated[User, Depends(require("employee.document_manage"))],
    file: Annotated[UploadFile, File()],
    document_type: Annotated[str, Form(max_length=50)],
    title: Annotated[str, Form(max_length=200)],
    visibility: Annotated[str, Form()] = "hr_only",
) -> EmployeeDocumentResponse:
    """Accepts PDF, JPEG, PNG, DOCX and XLSX only.

    The declared type is checked against the file's magic bytes, so a renamed
    file is rejected. The stored filename is server-generated, so an uploaded
    name can never influence the storage path.
    """
    employee = service.get_employee(session, employee_id)

    if visibility not in {"employee_visible", "hr_only"}:
        raise PolicyViolation("Visibility must be 'employee_visible' or 'hr_only'.")

    saved = storage.save_upload(
        stream=file.file,
        filename=file.filename or "upload",
        declared_content_type=file.content_type,
    )

    stored = StoredFile(
        storage_key=saved.storage_key,
        original_filename=saved.original_filename,
        content_type=saved.content_type,
        size_bytes=saved.size_bytes,
        sha256=saved.sha256,
        uploaded_by_user_id=ctx.actor_user_id,
    )
    session.add(stored)
    session.flush()

    document = EmployeeDocument(
        employee_id=employee.id,
        file_id=stored.id,
        document_type=document_type.strip(),
        title=title.strip(),
        visibility=visibility,
        uploaded_by_user_id=ctx.actor_user_id,
    )
    session.add(document)
    session.flush()

    audit.record(
        session,
        action="employee.document_uploaded",
        context=ctx,
        entity_type="employee",
        entity_id=employee.id,
        after={
            "document_type": document.document_type,
            "title": document.title,
            "visibility": document.visibility,
            "sha256": saved.sha256,
        },
    )

    return EmployeeDocumentResponse(
        id=document.id,
        employee_id=employee.id,
        document_type=document.document_type,
        title=document.title,
        visibility=document.visibility,
        valid_from=None,
        valid_to=None,
        original_filename=stored.original_filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        created_at=document.created_at,
    )


@router.get(
    "/{employee_id}/documents/{document_id}/download",
    response_class=FileResponse,
    summary="Download an employment document",
)
def download_document(
    employee_id: int,
    document_id: int,
    session: DbSession,
    user: CurrentUser,
    ctx: AuditCtx,
) -> Response:
    """Always served as an attachment with sniffing disabled.

    Every download is recorded in the audit log, per the
    ``security.document_access_logged`` setting.
    """
    document = session.get(EmployeeDocument, document_id)
    if document is None or document.employee_id != employee_id:
        raise NotFound("Document not found.")

    if not _can_access_document(user, document):
        raise PermissionDenied("You do not have access to this document.")

    path = storage.resolve_path(document.file.storage_key)

    audit.record(
        session,
        action="employee.document_downloaded",
        context=ctx,
        entity_type="employee_document",
        entity_id=document.id,
        extra={"employee_id": employee_id, "title": document.title},
    )

    return FileResponse(
        path=path,
        media_type=document.file.content_type,
        filename=document.file.original_filename,
        headers={
            # Never render in the browser: a stored file must not execute in
            # the application's origin.
            "Content-Disposition": (f'attachment; filename="{document.file.original_filename}"'),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
