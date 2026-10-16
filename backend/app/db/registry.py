"""Single import point for every mapped class.

Alembic's autogenerate compares ``Base.metadata`` against the live database. A
model that no module has imported is absent from that metadata, and autogenerate
would silently emit a migration that drops its table. Importing every model here
- and importing only this module from ``alembic/env.py`` - makes that failure
mode impossible.

Add every new model module to this file as it is created.
"""

from __future__ import annotations

from app.db.base import Base

# Milestone 3 - attendance
from app.modules.attendance.models import (
    AttendanceDay,
    AttendanceEvent,
    AttendanceRegularization,
    AttendanceSession,
)

# Milestone 1 - identity, configuration, auditing
from app.modules.audit.models import AuditLog, JobRun
from app.modules.auth.models import (
    LoginAttempt,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserRole,
)

# Milestone 6 - comp-off
from app.modules.compoff.models import (
    CompoffAllocation,
    CompoffApplication,
    CompoffApplicationDay,
    CompoffApproval,
    CompoffCredit,
)

# Milestone 4 - device integration
from app.modules.devices.models import (
    Device,
    DeviceEmployeeMapping,
    DeviceRawEvent,
    DeviceSyncRun,
)
from app.modules.employees.models import (
    Employee,
    EmployeeBankAccount,
    EmployeeDocument,
    EmployeeStatutoryDetails,
    EmploymentHistory,
    StoredFile,
)

# Milestone 2 - organisation and employees
# Milestone 6 - holiday calendars
from app.modules.holidays.models import (
    Holiday,
    HolidayCalendar,
    HolidayImportRun,
    OptionalHolidayElection,
)

# Milestone 5 - leave management
from app.modules.leave.models import (
    EmployeeLeavePolicy,
    LeaveApplication,
    LeaveApplicationDay,
    LeaveApproval,
    LeaveLedgerEntry,
    LeavePolicy,
    LeaveType,
)

# Milestone 7 - announcements and the notification centre
from app.modules.notifications.models import (
    Announcement,
    AnnouncementRead,
    AnnouncementTarget,
    Notification,
    NotificationPreference,
)
from app.modules.org.models import (
    Department,
    Designation,
    EmployeeGroup,
    EmployeeGroupMember,
    WorkLocation,
)

# Milestone 8 - payroll, salary structures and statutory configuration
from app.modules.payroll.models import (
    EmployeeSalary,
    EmployeeSalaryComponent,
    EmployeeStatutoryEligibility,
    FnfSettlement,
    PayrollAdjustment,
    PayrollPeriod,
    PayrollRun,
    Payslip,
    PayslipLine,
    SalaryComponent,
    SalaryStructure,
    SalaryStructureComponent,
    StatutoryRuleParameter,
    StatutoryRuleSet,
)
from app.modules.settings.models import SettingChangeHistory, SystemSetting

# Milestone 3 - shifts and work calendars
from app.modules.shifts.models import (
    CalendarException,
    Shift,
    ShiftAssignment,
    WeeklyOffPattern,
    WorkCalendar,
)

__all__ = [
    "Announcement",
    "AnnouncementRead",
    "AnnouncementTarget",
    "AttendanceDay",
    "AttendanceEvent",
    "AttendanceRegularization",
    "AttendanceSession",
    "AuditLog",
    "Base",
    "CalendarException",
    "CompoffAllocation",
    "CompoffApplication",
    "CompoffApplicationDay",
    "CompoffApproval",
    "CompoffCredit",
    "Department",
    "Designation",
    "Device",
    "DeviceEmployeeMapping",
    "DeviceRawEvent",
    "DeviceSyncRun",
    "Employee",
    "EmployeeBankAccount",
    "EmployeeDocument",
    "EmployeeGroup",
    "EmployeeGroupMember",
    "EmployeeLeavePolicy",
    "EmployeeSalary",
    "EmployeeSalaryComponent",
    "EmployeeStatutoryDetails",
    "EmployeeStatutoryEligibility",
    "EmploymentHistory",
    "FnfSettlement",
    "Holiday",
    "HolidayCalendar",
    "HolidayImportRun",
    "JobRun",
    "LeaveApplication",
    "LeaveApplicationDay",
    "LeaveApproval",
    "LeaveLedgerEntry",
    "LeavePolicy",
    "LeaveType",
    "LoginAttempt",
    "Notification",
    "NotificationPreference",
    "OptionalHolidayElection",
    "PayrollAdjustment",
    "PayrollPeriod",
    "PayrollRun",
    "Payslip",
    "PayslipLine",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "SalaryComponent",
    "SalaryStructure",
    "SalaryStructureComponent",
    "SettingChangeHistory",
    "Shift",
    "ShiftAssignment",
    "StatutoryRuleParameter",
    "StatutoryRuleSet",
    "StoredFile",
    "SystemSetting",
    "User",
    "UserRole",
    "WeeklyOffPattern",
    "WorkCalendar",
    "WorkLocation",
]
