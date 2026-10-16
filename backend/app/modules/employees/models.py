"""Employee records, employment history, restricted details and documents.

Retention policy
----------------
Employees are **never deleted**. Exit is recorded as ``employment_status =
'exited'`` plus ``date_of_exit``, because attendance, leave ledger, payroll and
audit rows must keep resolving to a real person long after they leave - for
statutory retention and for answering questions about a past payroll.

Privacy
-------
Three tiers of access, enforced by separate permissions:

* ``employee.read``            - directory and profile fields.
* ``employee.read_team``       - the same, limited to one's own reports.
* ``employee.read_sensitive``  - statutory identifiers and bank details.

Aadhaar is stored as **last four digits only**: the full number is not needed
for any function this system performs, so it is not collected. PAN and bank
account numbers are encrypted at rest (see ``app.core.crypto``) because they are
needed in full for payroll and statutory output.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PrimaryKey, TimestampMixin

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------
# These are code-level enumerations rather than configurable settings because
# application logic branches on them: payroll prorates on 'exited', attendance
# skips 'suspended'. Adding a value is a deliberate change with a migration.

EMPLOYMENT_TYPES = ("full_time", "part_time", "contract", "intern", "consultant")

EMPLOYMENT_STATUSES = ("probation", "active", "notice_period", "suspended", "exited")

EXIT_TYPES = ("resignation", "termination", "retirement", "end_of_contract", "other")

GENDERS = ("female", "male", "other", "not_disclosed")

MARITAL_STATUSES = ("single", "married", "other", "not_disclosed")

DOCUMENT_VISIBILITIES = ("employee_visible", "hr_only")


class Employee(Base, TimestampMixin):
    """A person employed by the company."""

    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "reporting_manager_id IS NULL OR reporting_manager_id <> id",
            name="no_self_manager",
        ),
        CheckConstraint(f"employment_type IN {EMPLOYMENT_TYPES}", name="employment_type_known"),
        CheckConstraint(
            f"employment_status IN {EMPLOYMENT_STATUSES}", name="employment_status_known"
        ),
        CheckConstraint(
            "date_of_exit IS NULL OR date_of_exit >= date_of_joining",
            name="exit_after_joining",
        ),
        CheckConstraint(
            "probation_end_date IS NULL OR probation_end_date >= date_of_joining",
            name="probation_after_joining",
        ),
        # An exited employee must have an exit date, and vice versa. Without
        # this, payroll proration and attendance both silently misbehave.
        CheckConstraint(
            "(employment_status = 'exited') = (date_of_exit IS NOT NULL)",
            name="exit_status_matches_date",
        ),
        # Normalisation enforced by the database, not only by the service layer.
        CheckConstraint("employee_code = upper(employee_code)", name="code_upper"),
        CheckConstraint(
            "work_email IS NULL OR work_email = lower(work_email)",
            name="work_email_lowercase",
        ),
        Index("ix_employees_department_id_employment_status", "department_id", "employment_status"),
        Index("ix_employees_reporting_manager_id", "reporting_manager_id"),
        # Expression indexes backing the case-insensitive directory search in
        # build_directory_query; without them those LIKE filters scan.
        Index("ix_employees_employee_code_lower", text("lower(employee_code)")),
        Index("ix_employees_name_lower", text("lower(first_name)"), text("lower(last_name)")),
        {"comment": "Never delete. Record exit via employment_status and date_of_exit."},
    )

    id: Mapped[PrimaryKey]

    employee_code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    """Company identifier, e.g. ITC0007. Also accepted as a sign-in identifier."""

    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    work_email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    personal_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    work_phone: Mapped[str | None] = mapped_column(String(25), nullable=True)
    personal_phone: Mapped[str | None] = mapped_column(String(25), nullable=True)

    emergency_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(25), nullable=True)
    emergency_contact_relation: Mapped[str | None] = mapped_column(String(50), nullable=True)

    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    designation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("designations.id", ondelete="RESTRICT"), nullable=True
    )
    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="RESTRICT"), nullable=True
    )
    reporting_manager_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )

    employment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    employment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'"), index=True
    )

    date_of_joining: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    probation_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_of_confirmation: Mapped[date | None] = mapped_column(Date, nullable=True)

    date_of_exit: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    exit_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_working_day: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    manager: Mapped[Employee | None] = relationship(
        remote_side="Employee.id",
        back_populates="direct_reports",
        foreign_keys=[reporting_manager_id],
    )
    direct_reports: Mapped[list[Employee]] = relationship(
        back_populates="manager", foreign_keys=[reporting_manager_id]
    )

    statutory_details: Mapped[EmployeeStatutoryDetails | None] = relationship(
        back_populates="employee", cascade="all, delete-orphan", uselist=False
    )
    bank_accounts: Mapped[list[EmployeeBankAccount]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    documents: Mapped[list[EmployeeDocument]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )
    # employee_employment_history has two foreign keys to employees
    # (employee_id and reporting_manager_id), so the join must be stated
    # explicitly or SQLAlchemy cannot tell which one this relationship follows.
    employment_history: Mapped[list[EmploymentHistory]] = relationship(
        back_populates="employee",
        cascade="all, delete-orphan",
        foreign_keys="EmploymentHistory.employee_id",
    )

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(part for part in parts if part)

    @property
    def is_currently_employed(self) -> bool:
        return self.employment_status != "exited"

    def is_employed_on(self, day: date) -> bool:
        """Whether the employee was on the rolls on a given date.

        Attendance and payroll both need this: an employee who joined on the
        15th has no attendance obligation on the 10th, and an exited employee
        must not accrue leave after their last working day.
        """
        if day < self.date_of_joining:
            return False
        end = self.last_working_day or self.date_of_exit
        return not (end and day > end)


class EmploymentHistory(Base, TimestampMixin):
    """Effective-dated snapshot of an employee's position.

    A new row is written on joining and on every transfer, promotion or manager
    change. Nothing is updated in place, so "which department was this person in
    last March" is answerable - which attendance and payroll reports require.
    """

    __tablename__ = "employee_employment_history"
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="period_ordered",
        ),
        Index(
            "ix_employee_employment_history_employee_id_effective_from",
            "employee_id",
            "effective_from",
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """NULL means this is the current row."""

    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    designation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("designations.id", ondelete="SET NULL"), nullable=True
    )
    work_location_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_locations.id", ondelete="SET NULL"), nullable=True
    )
    reporting_manager_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    employment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    employment_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    change_type: Mapped[str] = mapped_column(String(30), nullable=False)
    """joining | transfer | promotion | manager_change | status_change | exit"""

    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    employee: Mapped[Employee] = relationship(
        back_populates="employment_history", foreign_keys=[employee_id]
    )


class EmployeeStatutoryDetails(Base, TimestampMixin):
    """Statutory identifiers. Restricted to ``employee.read_sensitive``.

    The ``*_opted_in`` / ``*_eligible_override`` columns exist because statutory
    eligibility is not uniform: it depends on wage level, headcount, employee
    election and date of joining. Payroll must be able to record a per-employee
    determination rather than assume everyone is treated identically.
    """

    __tablename__ = "employee_statutory_details"
    __table_args__ = (
        # Four digits exactly. Enforced here as well as in the service layer so
        # a full Aadhaar number cannot reach this column by any route.
        CheckConstraint(
            "aadhaar_last4 IS NULL OR aadhaar_last4 ~ '^[0-9]{4}$'",
            name="aadhaar_last4_is_4_digits",
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    pf_uan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pf_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    esi_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    pan_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    """Encrypted at rest; needed in full for TDS output."""

    pan_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    """For display and matching without decrypting."""

    aadhaar_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    """Only the last four digits are collected - nothing here needs the full number."""

    #: Per-employee statutory determinations. NULL means "not yet determined",
    #: which payroll treats as a reason to flag rather than to assume.
    pf_opted_in: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    esi_eligible_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pt_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """State for professional tax; defaults from the work location when unset."""

    tax_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """Employee's declared income-tax regime. NULL until they declare."""

    employee: Mapped[Employee] = relationship(back_populates="statutory_details")


class EmployeeBankAccount(Base, TimestampMixin):
    """Salary bank account. Restricted to ``employee.read_sensitive``."""

    __tablename__ = "employee_bank_accounts"
    __table_args__ = (
        Index(
            "ix_employee_bank_accounts_primary",
            "employee_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )

    account_holder_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_number_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    account_number_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    ifsc_code: Mapped[str] = mapped_column(String(11), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """A partial unique index allows exactly one primary account per employee."""

    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    employee: Mapped[Employee] = relationship(back_populates="bank_accounts")


class StoredFile(Base):
    """An uploaded file.

    Content lives on the configured storage backend under a random
    ``storage_key``; the original filename is kept only for display, never used
    as a path. ``sha256`` gives integrity checking and cheap duplicate detection.
    """

    __tablename__ = "stored_files"
    __table_args__ = (CheckConstraint("size_bytes > 0", name="size_positive"),)

    id: Mapped[PrimaryKey]
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )


class EmployeeDocument(Base, TimestampMixin):
    """An employment document attached to an employee.

    ``visibility`` decides whether the employee can see their own copy: an
    offer letter is theirs to read, an internal performance note is not.
    """

    __tablename__ = "employee_documents"
    __table_args__ = (
        CheckConstraint(f"visibility IN {DOCUMENT_VISIBILITIES}", name="visibility_known"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="document_validity_ordered",
        ),
        Index("ix_employee_documents_employee_id_document_type", "employee_id", "document_type"),
    )

    id: Mapped[PrimaryKey]
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False
    )

    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'hr_only'")
    )
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    employee: Mapped[Employee] = relationship(back_populates="documents")
    file: Mapped[StoredFile] = relationship(lazy="joined")
