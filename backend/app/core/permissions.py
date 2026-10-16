"""Permission catalogue and default role definitions.

Authorization model
-------------------
Three layers, deliberately separated:

1. **Permission** - a fine-grained capability string such as ``leave.approve_hr``.
   Permissions are database rows; this module is the declarative source of truth
   that the seeder reconciles into the ``permissions`` table.
2. **Role** - a named bundle of permissions (``hr_admin``, ``employee``). Roles
   are also rows, so an administrator can later create, say, a "Payroll Officer"
   role without a code change.
3. **Relationship** - whether the acting user stands in the required relation to
   the record (owns it, or is the subject's reporting manager).

Layer 3 is why "reporting manager" is *not* a role. Managerial authority follows
the ``employees.reporting_manager_id`` tree, so every employee carries
``leave.approve_as_manager`` and the service layer still verifies that the
applicant actually reports to them. A role grant alone can never let someone
approve an arbitrary employee's leave.

Adding a permission later means appending to :data:`PERMISSIONS` and re-running
``iams seed-permissions``; no schema change is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str
    module: str
    description: str


def _p(code: str, module: str, description: str) -> PermissionDef:
    return PermissionDef(code=code, module=module, description=description)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
# Codes are "<module>.<action>" and are permanent identifiers - rename with a
# migration, never by editing a string in place.

PERMISSIONS: Final[tuple[PermissionDef, ...]] = (
    # -- system / settings ---------------------------------------------------
    _p("settings.read", "settings", "View company settings and business policy configuration"),
    _p("settings.manage", "settings", "Change company settings and business policy configuration"),
    _p("audit.read", "audit", "View audit logs"),
    _p("job.read", "system", "View scheduled job runs and their results"),
    _p("job.run", "system", "Trigger scheduled jobs manually"),
    # -- identity ------------------------------------------------------------
    _p("user.read", "auth", "View user accounts"),
    _p("user.manage", "auth", "Create, update, activate and deactivate user accounts"),
    _p("user.reset_password", "auth", "Issue temporary passwords for other users"),
    _p("role.read", "auth", "View roles and their permissions"),
    _p("role.manage", "auth", "Create roles and change role permission grants"),
    _p("profile.read_self", "auth", "View one's own account and profile"),
    _p("profile.update_self", "auth", "Update one's own permitted personal information"),
    # -- organisation --------------------------------------------------------
    _p("org.read", "org", "View departments, designations, work locations and groups"),
    _p("org.manage", "org", "Manage departments, designations, work locations and groups"),
    # -- employees -----------------------------------------------------------
    _p("employee.read", "employees", "View employee directory and profiles"),
    _p("employee.read_team", "employees", "View profiles of direct and indirect reports"),
    _p("employee.manage", "employees", "Onboard, update, transfer and offboard employees"),
    _p("employee.read_sensitive", "employees", "View statutory and bank details"),
    _p("employee.manage_sensitive", "employees", "Change statutory and bank details"),
    _p("employee.import", "employees", "Bulk import employees from CSV"),
    _p("employee.document_manage", "employees", "Upload and manage employment documents"),
    # -- shifts and calendar -------------------------------------------------
    _p("shift.read", "shifts", "View shifts and shift assignments"),
    _p("shift.manage", "shifts", "Define shifts and assign them to employees or locations"),
    _p("calendar.read", "shifts", "View work calendars and weekly-off patterns"),
    _p("calendar.manage", "shifts", "Manage work calendars, weekly offs and exceptions"),
    # -- attendance ----------------------------------------------------------
    _p("attendance.punch", "attendance", "Record one's own punch in / punch out"),
    _p("attendance.read_self", "attendance", "View one's own attendance"),
    _p("attendance.read_team", "attendance", "View attendance of direct and indirect reports"),
    _p("attendance.read_all", "attendance", "View attendance of all employees"),
    _p("attendance.manage", "attendance", "Make manual attendance corrections"),
    _p("attendance.recalculate", "attendance", "Re-run attendance computation for a date range"),
    _p("regularization.apply", "attendance", "Request a correction to one's own attendance"),
    _p(
        "regularization.approve_as_manager",
        "attendance",
        "Review reports' regularization requests",
    ),
    _p("regularization.approve_hr", "attendance", "Give final decision on regularization requests"),
    # -- device integration --------------------------------------------------
    _p("device.read", "integrations", "View attendance devices and synchronisation logs"),
    _p("device.manage", "integrations", "Configure attendance device integrations"),
    _p("device.sync", "integrations", "Trigger a device synchronisation run"),
    _p("device.reconcile", "integrations", "Resolve failed or unmapped device events"),
    # -- leave ---------------------------------------------------------------
    _p("leave.apply", "leave", "Apply for and cancel one's own leave"),
    _p("leave.read_self", "leave", "View one's own leave applications and balances"),
    _p("leave.read_team", "leave", "View reports' leave applications and balances"),
    _p("leave.read_all", "leave", "View all leave applications and balances"),
    _p("leave.approve_as_manager", "leave", "Review reports' leave applications"),
    _p("leave.approve_hr", "leave", "Give final approval or rejection on leave applications"),
    _p("leave.manage_policy", "leave", "Manage leave types, policies and accrual rules"),
    _p("leave.adjust_ledger", "leave", "Post manual leave balance adjustments"),
    _p("leave.run_year_end", "leave", "Run leave-year closing, carry-forward and expiry"),
    # -- comp-off ------------------------------------------------------------
    _p("compoff.apply", "compoff", "Apply for comp-off from one's own credits"),
    _p("compoff.read_self", "compoff", "View one's own comp-off credits and balance"),
    _p("compoff.read_all", "compoff", "View all comp-off credits and applications"),
    _p("compoff.verify", "compoff", "Verify or reject comp-off credits"),
    _p("compoff.approve_as_manager", "compoff", "Review reports' comp-off applications"),
    _p("compoff.approve_hr", "compoff", "Give final decision on comp-off applications"),
    _p("compoff.reverse", "compoff", "Reverse an incorrectly granted comp-off credit"),
    # -- holidays ------------------------------------------------------------
    _p("holiday.read", "holidays", "View holiday calendars"),
    _p("holiday.manage", "holidays", "Manage holiday calendars and imports"),
    _p("holiday.elect_optional", "holidays", "Choose one's own optional holidays"),
    # -- payroll -------------------------------------------------------------
    _p("salary.read_self", "payroll", "View one's own salary structure"),
    _p("salary.read_all", "payroll", "View all employees' salary structures"),
    _p("salary.manage", "payroll", "Create and revise employee salary structures"),
    _p("payroll.read", "payroll", "View payroll periods, runs and registers"),
    _p("payroll.calculate", "payroll", "Calculate or recalculate a draft payroll run"),
    _p("payroll.review", "payroll", "Mark a calculated payroll run as reviewed"),
    _p("payroll.approve", "payroll", "Approve a reviewed payroll run"),
    _p("payroll.finalize", "payroll", "Finalize an approved payroll run"),
    _p("payroll.adjust", "payroll", "Post arrears, recoveries and reversal adjustments"),
    _p("payroll.run_fnf", "payroll", "Process full-and-final settlements"),
    _p("payslip.read_self", "payroll", "View and download one's own payslips"),
    _p("payslip.read_all", "payroll", "View and download any employee's payslip"),
    _p("statutory.read", "payroll", "View statutory rule sets and parameters"),
    _p("statutory.manage", "payroll", "Create and edit statutory rule sets"),
    _p("statutory.verify", "payroll", "Mark a statutory rule set as verified against its source"),
    # -- announcements and notifications ------------------------------------
    _p("announcement.read", "notifications", "View published announcements"),
    _p("announcement.manage", "notifications", "Create, schedule and publish announcements"),
    _p("notification.read_self", "notifications", "View one's own notification centre"),
    # -- reports -------------------------------------------------------------
    _p("report.attendance", "reports", "Run attendance, overtime and missing-punch reports"),
    _p("report.leave", "reports", "Run leave balance and comp-off reports"),
    _p("report.employee", "reports", "Run employee record reports"),
    _p("report.payroll", "reports", "Run payroll, deduction and reconciliation reports"),
    _p("report.export", "reports", "Export report results to CSV or Excel"),
)

PERMISSIONS_BY_CODE: Final[dict[str, PermissionDef]] = {p.code: p for p in PERMISSIONS}

ALL_PERMISSION_CODES: Final[frozenset[str]] = frozenset(PERMISSIONS_BY_CODE)


# ---------------------------------------------------------------------------
# Default roles
# ---------------------------------------------------------------------------

ROLE_HR_ADMIN: Final = "hr_admin"
ROLE_EMPLOYEE: Final = "employee"


@dataclass(frozen=True, slots=True)
class RoleDef:
    code: str
    name: str
    description: str
    permission_codes: frozenset[str]
    is_system: bool = True


#: Permissions every employee holds. Note that the three ``approve_as_manager``
#: grants are present here: they are a *precondition* for review, and the service
#: layer additionally requires the applicant to report to the acting user.
_EMPLOYEE_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        "profile.read_self",
        "profile.update_self",
        "org.read",
        # Scoped to their own reports by visible_employee_scope(); an employee
        # with no reports sees only themselves.
        "employee.read_team",
        "attendance.punch",
        "attendance.read_self",
        "attendance.read_team",
        "regularization.apply",
        "regularization.approve_as_manager",
        "leave.apply",
        "leave.read_self",
        "leave.read_team",
        "leave.approve_as_manager",
        "compoff.apply",
        "compoff.read_self",
        "compoff.approve_as_manager",
        "holiday.read",
        "holiday.elect_optional",
        "salary.read_self",
        "payslip.read_self",
        "announcement.read",
        "notification.read_self",
        "shift.read",
        "calendar.read",
    }
)

ROLES: Final[tuple[RoleDef, ...]] = (
    RoleDef(
        code=ROLE_HR_ADMIN,
        name="HR / Admin",
        description=(
            "Full administrative access: employee lifecycle, configuration, "
            "approvals, payroll and reporting."
        ),
        permission_codes=ALL_PERMISSION_CODES,
    ),
    RoleDef(
        code=ROLE_EMPLOYEE,
        name="Employee",
        description="Self-service access plus review of direct reports' requests.",
        permission_codes=_EMPLOYEE_PERMISSIONS,
    ),
)

ROLES_BY_CODE: Final[dict[str, RoleDef]] = {r.code: r for r in ROLES}


def validate_catalogue() -> None:
    """Guard against typos in the declarations above. Called by the seeder and tests."""
    seen: set[str] = set()
    for perm in PERMISSIONS:
        if perm.code in seen:
            raise ValueError(f"Duplicate permission code: {perm.code}")
        if perm.code.count(".") != 1:
            raise ValueError(f"Permission code must be '<module>.<action>': {perm.code}")
        seen.add(perm.code)

    for role in ROLES:
        unknown = role.permission_codes - ALL_PERMISSION_CODES
        if unknown:
            raise ValueError(
                f"Role {role.code!r} grants permissions that are not in the catalogue: "
                f"{sorted(unknown)}"
            )
