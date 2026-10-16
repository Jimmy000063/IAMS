"""Declarative catalogue of company-wide business settings.

This is the seed definition for the ``system_settings`` table. ``iams
seed-settings`` inserts missing keys and updates presentation metadata (label,
description, constraints) but **never overwrites a stored value** - an
administrator's decision always wins over a code default.

Two flags carry the honesty requirements from the brief:

``confirmed=True``
    The company has actually decided this. Only the defaults explicitly stated
    in the project brief are marked confirmed.

``legal_review=True``
    The value has statutory consequences. Payroll refuses to finalize while a
    setting it depends on is still unconfirmed, rather than proceeding with a
    guess.

Statutory *rates, ceilings and slabs* are deliberately absent from this file.
They belong in the effective-dated ``statutory_rule_sets`` tables (Milestone 8),
because a single key/value row cannot express a rule that changes over time and
varies by employee eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final


@dataclass(frozen=True, slots=True)
class SettingDef:
    key: str
    category: str
    label: str
    description: str
    value_type: str
    default: Any
    constraints: dict[str, Any] | None = None
    editable: bool = True
    confirmed: bool = False
    legal_review: bool = False
    tags: tuple[str, ...] = field(default=())


VALUE_TYPES: Final[frozenset[str]] = frozenset(
    {"string", "integer", "decimal", "boolean", "time", "date", "json", "enum"}
)

WEEKDAYS: Final[list[str]] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


SETTINGS: Final[tuple[SettingDef, ...]] = (
    # ---------------------------------------------------------------- company
    SettingDef(
        key="company.legal_name",
        category="company",
        label="Registered legal name",
        description="Legal entity name as it appears on payslips and statutory reports.",
        value_type="string",
        default="Ishvara Tech Consulting LLP",
        confirmed=True,
    ),
    SettingDef(
        key="company.display_name",
        category="company",
        label="Display name",
        description="Short name shown in the application header and emails.",
        value_type="string",
        default="Ishvara Tech",
        confirmed=True,
    ),
    SettingDef(
        key="company.default_timezone",
        category="company",
        label="Default timezone",
        description=(
            "Fallback IANA timezone for employees whose work location has none set. "
            "Attendance dates always prefer the work-location timezone."
        ),
        value_type="string",
        default="Asia/Kolkata",
        confirmed=True,
    ),
    SettingDef(
        key="company.working_week",
        category="company",
        label="Default working week",
        description=(
            "Weekdays that are working days by default. Confirmed in the project "
            "brief as Monday to Friday. Work calendars may override per location."
        ),
        value_type="json",
        default=["mon", "tue", "wed", "thu", "fri"],
        constraints={"item_choices": WEEKDAYS, "min_items": 0, "max_items": 7},
        confirmed=True,
    ),
    SettingDef(
        key="company.financial_year_start_month",
        category="company",
        label="Financial year start month",
        description="Month the statutory financial year begins. Used by payroll and TDS reporting.",
        value_type="integer",
        default=4,
        constraints={"min": 1, "max": 12},
        legal_review=True,
    ),
    # ------------------------------------------------------------ attendance
    SettingDef(
        key="attendance.web_punch_enabled",
        category="attendance",
        label="Web punching enabled",
        description=(
            "Allow employees to punch in and out from the web application. "
            "Confirmed in the brief; must keep working independently of any device."
        ),
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    SettingDef(
        key="attendance.grace_late_minutes",
        category="attendance",
        label="Late arrival grace period (minutes)",
        description="Minutes after shift start before an arrival counts as late.",
        value_type="integer",
        default=15,
        constraints={"min": 0, "max": 240, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.grace_early_exit_minutes",
        category="attendance",
        label="Early departure grace period (minutes)",
        description="Minutes before shift end that a departure is still not early.",
        value_type="integer",
        default=15,
        constraints={"min": 0, "max": 240, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.min_minutes_full_day",
        category="attendance",
        label="Minimum worked minutes for a full day",
        description="Net worked minutes required to be marked present for a full day.",
        value_type="integer",
        default=480,
        constraints={"min": 60, "max": 960, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.min_minutes_half_day",
        category="attendance",
        label="Minimum worked minutes for a half day",
        description="Net worked minutes required to be credited a half day.",
        value_type="integer",
        default=240,
        constraints={"min": 30, "max": 600, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.rounding_mode",
        category="attendance",
        label="Worked-time rounding mode",
        description="How net worked time is rounded before status is decided.",
        value_type="enum",
        default="none",
        constraints={"choices": ["none", "nearest", "down", "up"]},
    ),
    SettingDef(
        key="attendance.rounding_increment_minutes",
        category="attendance",
        label="Rounding increment (minutes)",
        description="Increment used when rounding mode is not 'none'.",
        value_type="integer",
        default=15,
        constraints={"min": 1, "max": 60, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.missing_punch_status",
        category="attendance",
        label="Status when a punch is missing",
        description=(
            "Status applied when an employee punched in but never out. The brief "
            "requires that a missing punch is not automatically treated as absence."
        ),
        value_type="enum",
        default="incomplete",
        constraints={"choices": ["incomplete", "missing_punch", "absent"]},
        confirmed=True,
    ),
    SettingDef(
        key="attendance.auto_deduct_break_minutes",
        category="attendance",
        label="Automatic break deduction (minutes)",
        description=(
            "Minutes deducted from worked time when no break punches exist. "
            "Set to 0 to rely only on actual break punches."
        ),
        value_type="integer",
        default=0,
        constraints={"min": 0, "max": 180, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.overtime_enabled",
        category="attendance",
        label="Track overtime",
        description="Whether worked time beyond the shift is accumulated as overtime.",
        value_type="boolean",
        default=False,
    ),
    SettingDef(
        key="attendance.overtime_min_minutes",
        category="attendance",
        label="Minimum minutes to qualify as overtime",
        description="Extra minutes beyond the shift before overtime begins to accrue.",
        value_type="integer",
        default=30,
        constraints={"min": 0, "max": 240, "unit": "minutes"},
    ),
    SettingDef(
        key="attendance.regularization_window_days",
        category="attendance",
        label="Regularization window (days)",
        description="How many days back an employee may request an attendance correction.",
        value_type="integer",
        default=30,
        constraints={"min": 0, "max": 365, "unit": "days"},
    ),
    SettingDef(
        key="attendance.recompute_locked_after_days",
        category="attendance",
        label="Lock attendance recomputation after (days)",
        description=(
            "Past attendance older than this is not recomputed automatically when "
            "a shift or calendar changes, so history is not silently rewritten."
        ),
        value_type="integer",
        default=45,
        constraints={"min": 0, "max": 365, "unit": "days"},
    ),
    # --------------------------------------------------------------- devices
    SettingDef(
        key="devices.quiet_alert_after_minutes",
        category="devices",
        label="Flag a device as silent after (minutes)",
        description=(
            "How long an active device may go without reporting before it is "
            "flagged on the device health screen. A device that stops reporting "
            "raises no error on its own - it just leaves a growing hole in "
            "attendance that would otherwise surface at month end."
        ),
        value_type="integer",
        default=1440,
        constraints={"min": 15, "max": 20_160, "unit": "minutes"},
    ),
    SettingDef(
        key="devices.auto_sync_enabled",
        category="devices",
        label="Poll devices on a schedule",
        description=(
            "Whether the scheduled job polls devices that support it. Web punching "
            "works regardless; this only affects hardware integration."
        ),
        value_type="boolean",
        default=False,
    ),
    SettingDef(
        key="devices.auto_sync_interval_minutes",
        category="devices",
        label="Scheduled poll interval (minutes)",
        description=(
            "How often the scheduled job polls each pollable device. Shorter "
            "intervals detect punches sooner and load the device more."
        ),
        value_type="integer",
        default=30,
        constraints={"min": 5, "max": 1440, "unit": "minutes"},
    ),
    # ----------------------------------------------------------------- leave
    SettingDef(
        key="leave.year_start_month",
        category="leave",
        label="Leave year start month",
        description="Month in which the leave year begins. Configurable per the brief.",
        value_type="integer",
        default=4,
        constraints={"min": 1, "max": 12},
    ),
    SettingDef(
        key="leave.year_start_day",
        category="leave",
        label="Leave year start day",
        description="Day of month on which the leave year begins.",
        value_type="integer",
        default=1,
        constraints={"min": 1, "max": 28},
    ),
    SettingDef(
        key="leave.approval_chain",
        category="leave",
        label="Leave approval chain",
        description=(
            "Ordered approval stages. Confirmed in the brief as reporting manager "
            "followed by HR/Admin."
        ),
        value_type="json",
        default=["reporting_manager", "hr_admin"],
        constraints={"item_choices": ["reporting_manager", "hr_admin"]},
        confirmed=True,
    ),
    SettingDef(
        key="leave.manager_missing_behaviour",
        category="leave",
        label="When a reporting manager is missing",
        description=(
            "How to route the manager stage when an employee has no active "
            "reporting manager. 'escalate_to_hr' skips straight to HR/Admin."
        ),
        value_type="enum",
        default="escalate_to_hr",
        constraints={"choices": ["escalate_to_hr", "escalate_to_department_head", "block"]},
    ),
    SettingDef(
        key="leave.manager_sla_days",
        category="leave",
        label="Manager review SLA (days)",
        description=(
            "Days after which an unreviewed request is escalated. Set to 0 to "
            "disable automatic escalation."
        ),
        value_type="integer",
        default=3,
        constraints={"min": 0, "max": 30, "unit": "days"},
    ),
    SettingDef(
        key="leave.backdated_window_days",
        category="leave",
        label="Backdated application window (days)",
        description="How many days back an employee may apply for leave.",
        value_type="integer",
        default=30,
        constraints={"min": 0, "max": 365, "unit": "days"},
    ),
    SettingDef(
        key="leave.future_window_days",
        category="leave",
        label="Forward application window (days)",
        description=(
            "How far ahead leave may be applied for. Prevents a request landing in "
            "a leave year whose entitlement has not been decided yet."
        ),
        value_type="integer",
        default=365,
        constraints={"min": 1, "max": 730, "unit": "days"},
    ),
    SettingDef(
        key="leave.proration_basis",
        category="leave",
        label="Proration basis for part-period accrual",
        description=(
            "How a mid-period joiner's or leaver's accrual is prorated. "
            "'calendar_days' is days present over days in the period; "
            "'working_days' uses the work calendar; 'completed_months' credits "
            "whole months only. This has a direct effect on entitlement, so "
            "confirm it against company practice before relying on accrual figures."
        ),
        value_type="enum",
        default="calendar_days",
        constraints={"choices": ["calendar_days", "working_days", "completed_months"]},
    ),
    SettingDef(
        key="leave.allow_negative_balance",
        category="leave",
        label="Allow negative leave balance",
        description="Whether approval may push a leave balance below zero.",
        value_type="boolean",
        default=False,
    ),
    SettingDef(
        key="leave.half_day_enabled",
        category="leave",
        label="Allow half-day leave",
        description="Whether employees may apply for first-half or second-half leave.",
        value_type="boolean",
        default=True,
    ),
    SettingDef(
        key="leave.sandwich_rule_enabled",
        category="leave",
        label="Apply sandwich rule",
        description=(
            "Whether holidays and weekly offs falling between leave days are "
            "counted as leave. Has direct payroll consequences."
        ),
        value_type="boolean",
        default=False,
    ),
    SettingDef(
        key="leave.year_end_closing_rule",
        category="leave",
        label="Leave year closing rule",
        description="What happens to unused balance at leave-year end.",
        value_type="enum",
        default="carry_forward_then_lapse",
        constraints={
            "choices": [
                "carry_forward_then_lapse",
                "carry_forward_then_encash",
                "lapse_all",
                "carry_forward_all",
            ]
        },
    ),
    # -------------------------------------------------------------- holidays
    SettingDef(
        key="holiday.max_optional_elections",
        category="holiday",
        label="Optional holidays an employee may choose",
        description=(
            "How many optional or restricted holidays an employee may elect per "
            "calendar year. The point of a restricted holiday is that a few are "
            "chosen from a longer published list."
        ),
        value_type="integer",
        default=2,
        constraints={"min": 0, "max": 20},
    ),
    SettingDef(
        key="holiday.election_cutoff_days",
        category="holiday",
        label="Choose an optional holiday at least (days) ahead",
        description=(
            "Minimum notice for electing an optional holiday. Zero allows choosing "
            "it on the day, which makes it hard to plan cover."
        ),
        value_type="integer",
        default=7,
        constraints={"min": 0, "max": 180, "unit": "days"},
    ),
    # --------------------------------------------------------------- compoff
    SettingDef(
        key="compoff.enabled",
        category="compoff",
        label="Comp-off enabled",
        description="Whether compensatory off can be earned and applied for.",
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    SettingDef(
        key="compoff.requires_hr_verification",
        category="compoff",
        label="Require HR verification of credits",
        description=(
            "Comp-off credit is only usable after HR verifies the qualifying work. "
            "Confirmed in the brief."
        ),
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    SettingDef(
        key="compoff.qualifying_day_types",
        category="compoff",
        label="Qualifying day types",
        description="Day types on which work can earn comp-off credit.",
        value_type="json",
        default=["weekly_off", "holiday"],
        constraints={"item_choices": ["weekly_off", "holiday", "working_day"]},
        confirmed=True,
    ),
    SettingDef(
        key="compoff.min_minutes_full_credit",
        category="compoff",
        label="Minutes worked for a full-day credit",
        description="Net minutes of qualifying work that earn one full comp-off day.",
        value_type="integer",
        default=480,
        constraints={"min": 60, "max": 960, "unit": "minutes"},
    ),
    SettingDef(
        key="compoff.min_minutes_half_credit",
        category="compoff",
        label="Minutes worked for a half-day credit",
        description="Net minutes of qualifying work that earn half a comp-off day.",
        value_type="integer",
        default=240,
        constraints={"min": 30, "max": 600, "unit": "minutes"},
    ),
    SettingDef(
        key="compoff.expiry_days",
        category="compoff",
        label="Comp-off expiry (days)",
        description="Days after the earned date within which comp-off must be used.",
        value_type="integer",
        default=90,
        constraints={"min": 0, "max": 730, "unit": "days"},
    ),
    SettingDef(
        key="compoff.half_day_usage_enabled",
        category="compoff",
        label="Allow half-day comp-off usage",
        description="Whether a comp-off credit may be consumed in half-day units.",
        value_type="boolean",
        default=True,
    ),
    # --------------------------------------------------------------- payroll
    SettingDef(
        key="payroll.currency",
        category="payroll",
        label="Payroll currency",
        description="ISO currency code used for all salary and payslip amounts.",
        value_type="string",
        default="INR",
        confirmed=True,
    ),
    SettingDef(
        key="payroll.statutory_jurisdiction",
        category="payroll",
        label="Statutory jurisdiction",
        description="Country whose statutory modules apply. Confirmed as India in the brief.",
        value_type="string",
        default="IN",
        confirmed=True,
    ),
    SettingDef(
        key="payroll.payable_days_basis",
        category="payroll",
        label="Payable days basis",
        description=(
            "Denominator for per-day salary. This choice materially changes every "
            "loss-of-pay and proration amount and must be confirmed before payroll "
            "is finalized."
        ),
        value_type="enum",
        default="calendar_days",
        constraints={"choices": ["calendar_days", "working_days", "fixed_days"]},
        legal_review=True,
    ),
    SettingDef(
        key="payroll.fixed_payable_days",
        category="payroll",
        label="Fixed payable days per month",
        description="Used only when payable days basis is 'fixed_days'.",
        value_type="integer",
        default=26,
        constraints={"min": 1, "max": 31, "unit": "days"},
        legal_review=True,
    ),
    SettingDef(
        key="payroll.rounding_mode",
        category="payroll",
        label="Monetary rounding mode",
        description="Decimal rounding applied to computed payroll amounts.",
        value_type="enum",
        default="ROUND_HALF_UP",
        constraints={"choices": ["ROUND_HALF_UP", "ROUND_HALF_EVEN", "ROUND_DOWN"]},
    ),
    SettingDef(
        key="payroll.pay_day_of_month",
        category="payroll",
        label="Salary pay day",
        description="Day of the following month on which salary is paid.",
        value_type="integer",
        default=1,
        constraints={"min": 1, "max": 28},
    ),
    SettingDef(
        key="payroll.professional_tax_states",
        category="payroll",
        label="Professional tax states",
        description=(
            "States where the company is registered for professional tax. Slabs "
            "are configured per state in the statutory rule sets, not here. Leave "
            "empty until verified."
        ),
        value_type="json",
        default=[],
        legal_review=True,
    ),
    SettingDef(
        key="payroll.enabled_statutory_modules",
        category="payroll",
        label="Enabled statutory modules",
        description=(
            "Statutory computations to run. Each still requires a verified, "
            "effective-dated rule set before payroll can be finalized."
        ),
        value_type="json",
        default=[],
        constraints={"item_choices": ["epf", "eps", "edli", "esi", "pt", "tds", "gratuity"]},
        legal_review=True,
    ),
    SettingDef(
        key="payroll.block_finalize_on_unverified_rules",
        category="payroll",
        label="Block finalization on unverified statutory rules",
        description=(
            "When enabled, a payroll run cannot be finalized while any statutory "
            "rule set it used is unverified. Strongly recommended."
        ),
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    # -------------------------------------------------------------- security
    SettingDef(
        key="security.public_registration_enabled",
        category="security",
        label="Public self-registration",
        description=(
            "Permanently disabled: the brief specifies HR-created accounts only. "
            "Shown read-only for transparency."
        ),
        value_type="boolean",
        default=False,
        editable=False,
        confirmed=True,
    ),
    SettingDef(
        key="security.force_password_change_on_first_login",
        category="security",
        label="Force password change on first sign-in",
        description="Require employees to replace an HR-issued temporary password.",
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    SettingDef(
        key="security.temporary_password_validity_days",
        category="security",
        label="Temporary password validity (days)",
        description="Days an HR-issued temporary password remains usable.",
        value_type="integer",
        default=7,
        constraints={"min": 1, "max": 90, "unit": "days"},
    ),
    SettingDef(
        key="security.document_access_logged",
        category="security",
        label="Log document access",
        description="Record an audit entry whenever an employment document is downloaded.",
        value_type="boolean",
        default=True,
        confirmed=True,
    ),
    # -------------------------------------------------------------- retention
    #
    # None of these is confirmed, and nothing is purged automatically. A
    # retention period has consequences in both directions: too short destroys
    # evidence an investigation needs, too long keeps personal data nobody has a
    # reason to hold. Both are the company's decision, not a sensible default.
    SettingDef(
        key="retention.audit_log_days",
        category="retention",
        label="Audit log retention",
        description=(
            "How long audit entries are kept before a purge may remove them. The "
            "placeholder is seven years, which is a common Indian statutory "
            "retention period for employment records - it has not been verified "
            "for this company and must be confirmed."
        ),
        value_type="integer",
        default=2555,
        constraints={"min": 90, "max": 36500, "unit": "days"},
        legal_review=True,
    ),
    SettingDef(
        key="retention.device_event_days",
        category="retention",
        label="Raw device event retention",
        description=(
            "How long raw punch events from devices are kept. They are the "
            "evidence behind a computed attendance day; purging them leaves the "
            "computed day intact but no longer explainable."
        ),
        value_type="integer",
        default=365,
        constraints={"min": 30, "max": 3650, "unit": "days"},
    ),
    SettingDef(
        key="retention.notification_days",
        category="retention",
        label="Notification retention",
        description=(
            "How long in-app notifications are kept. A notification is the record "
            "of somebody having been told, so recent ones are worth keeping."
        ),
        value_type="integer",
        default=365,
        constraints={"min": 30, "max": 3650, "unit": "days"},
    ),
    # --------------------------------------------------------- notifications
    SettingDef(
        key="notifications.in_app_enabled",
        category="notifications",
        label="In-app notifications",
        description="The in-app notification centre. Always available; no external service.",
        value_type="boolean",
        default=True,
        editable=False,
        confirmed=True,
    ),
    SettingDef(
        key="notifications.email_enabled",
        category="notifications",
        label="Email notifications",
        description=(
            "Optional. Requires SMTP configuration in the environment; the system "
            "works fully without it."
        ),
        value_type="boolean",
        default=False,
        confirmed=True,
    ),
)

SETTINGS_BY_KEY: Final[dict[str, SettingDef]] = {s.key: s for s in SETTINGS}


def validate_catalogue() -> None:
    """Guard against declaration mistakes. Called by the seeder and by tests."""
    seen: set[str] = set()
    for setting in SETTINGS:
        if setting.key in seen:
            raise ValueError(f"Duplicate setting key: {setting.key}")
        seen.add(setting.key)
        if setting.value_type not in VALUE_TYPES:
            raise ValueError(
                f"Setting {setting.key!r} has unknown value_type {setting.value_type!r}"
            )
        if "." not in setting.key:
            raise ValueError(f"Setting key must be '<category>.<name>': {setting.key}")
        if not setting.key.startswith(f"{setting.category}."):
            raise ValueError(
                f"Setting {setting.key!r} does not match its category {setting.category!r}"
            )
        if setting.value_type == "enum":
            choices = (setting.constraints or {}).get("choices")
            if not choices:
                raise ValueError(f"Enum setting {setting.key!r} must declare 'choices'.")
            if setting.default not in choices:
                raise ValueError(
                    f"Enum setting {setting.key!r} default {setting.default!r} is not a choice."
                )


def unconfirmed_keys() -> list[str]:
    """Keys still carrying a placeholder default, for the open-items register."""
    return [s.key for s in SETTINGS if not s.confirmed]


def legal_review_keys() -> list[str]:
    """Keys whose values must be verified against an authoritative source."""
    return [s.key for s in SETTINGS if s.legal_review]
