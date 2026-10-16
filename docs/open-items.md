# Open items register

Decisions this system needs but does not have. Every item here ships as a
**configurable record**, not as a hardcoded value, and anything with statutory
consequences is flagged so payroll refuses to finalize until it is verified.

This file is the human-readable view. The machine-readable source of truth is the
`system_settings` table -- run `iams config-status` or open **Settings** in the
app for the live state.

Two kinds of item:

| Tag | Meaning |
|---|---|
| `needs-company-decision` | Ishvara Tech must decide. A working default is in place meanwhile. |
| `needs-legal-verification` | Must be checked against an authoritative source before it is used for pay. |

---

## 1. Confirmed in the project brief

These are decided and ship with `is_confirmed = true`.

| Setting | Value |
|---|---|
| `company.working_week` | Monday - Friday |
| `leave.approval_chain` | Reporting manager, then HR/Admin |
| `security.public_registration_enabled` | `false` -- permanently, not editable |
| `attendance.web_punch_enabled` | `true`, and must work without any device |
| `attendance.missing_punch_status` | `incomplete` -- never auto-marked absent |
| `compoff.requires_hr_verification` | `true` |
| `compoff.qualifying_day_types` | Weekly offs and holidays |
| `payroll.statutory_jurisdiction` | India |
| `payroll.currency` | INR |
| `payroll.block_finalize_on_unverified_rules` | `true` |
| `company.legal_name` | Ishvara Tech Consulting LLP |

---

## 2. `needs-company-decision`

Working defaults are in effect. None of these is a legal question -- they are
Ishvara Tech's own policy.

### Attendance
| Setting | Default | Question |
|---|---|---|
| `attendance.grace_late_minutes` | 15 min | How long after shift start is an arrival still on time? |
| `attendance.grace_early_exit_minutes` | 15 min | How early may someone leave without it counting? |
| `attendance.min_minutes_full_day` | 480 | Net minutes for a full present day. |
| `attendance.min_minutes_half_day` | 240 | Net minutes for a half day. |
| `attendance.rounding_mode` / `_increment_minutes` | `none` / 15 | Is worked time rounded before status is decided? |
| `attendance.auto_deduct_break_minutes` | 0 | Deduct an unrecorded break, or rely on break punches? |
| `attendance.overtime_enabled` / `_min_minutes` | `false` / 30 | Is overtime tracked, and from what threshold? |
| `attendance.regularization_window_days` | 30 | How far back may a correction be requested? |
| `attendance.recompute_locked_after_days` | 45 | After how long is past attendance immune to recalculation? |

### Leave
| Setting | Default | Question |
|---|---|---|
| `leave.year_start_month` / `_day` | 1 April | When does the leave year begin? |
| `leave.year_end_closing_rule` | `carry_forward_then_lapse` | What happens to unused balance at year end? |
| `leave.backdated_window_days` | 30 | How far back may leave be applied for? |
| `leave.allow_negative_balance` | `false` | May approval push a balance below zero? |
| `leave.half_day_enabled` | `true` | Are half-day applications allowed? |
| `leave.sandwich_rule_enabled` | `false` | Do holidays inside a leave span count as leave? **Affects pay.** |
| `leave.manager_missing_behaviour` | `escalate_to_hr` | Where does the manager stage go when there is no manager? |
| `leave.manager_sla_days` | 3 | After how long is an unreviewed request escalated? |

Still to be defined as records (Milestone 5), not settings:
- **Leave types** and their names, paid/unpaid status, gender restrictions.
- **Annual quotas** per leave type.
- **Accrual** frequency, probation eligibility, joining-date proration, rounding.
- **Carry-forward** caps and expiry windows per leave type.

### Comp-off
| Setting | Default | Question |
|---|---|---|
| `compoff.min_minutes_full_credit` | 480 | Qualifying work for a full comp-off day. |
| `compoff.min_minutes_half_credit` | 240 | Qualifying work for half a day. |
| `compoff.expiry_days` | 90 | How long is a credit usable? |
| `compoff.half_day_usage_enabled` | `true` | May a credit be spent in half days? |

### Payroll
| Decision | Where it lives | Question |
|---|---|---|
| Salary components and their proportions | `salary_components` records | Which lines a payslip has, and what each is worth. None is seeded: "basic" and "HRA" look universal but their proportions are a company decision with tax consequences. |
| Which components count towards each statutory wage | `is_pf_wage`, `is_esi_wage`, `is_pt_wage`, `is_taxable` per component | A legal question per component, never inferred from its name. A component whose treatment is unsettled carries `requires_legal_review`. |
| `payroll.payable_days_basis` | setting, **gates calculation** | Calendar days, working days or a fixed number. One unpaid day costs 1/30 on one basis and 1/26 on another, so payroll refuses to run until this is confirmed. |
| `payroll.enabled_statutory_modules` | setting | Which domains the company is registered for. Each still needs a verified rule set. |
| Which employees each domain covers | `employee_statutory_eligibility` | Effective-dated, and required to exclude somebody as well as to include them. A domain with no record is **not** eligible, because deducting on an assumption takes money from somebody nobody decided was covered. |
| Whether gratuity components use the PF wage | the components' own flags | The settlement computes gratuity on the PF wage; using the whole gross would overstate it. |

### Retention
All three start **unconfirmed**, and nothing is purged automatically. A
retention period destroys evidence as well as data: too short and an audit entry
nobody can produce later is indistinguishable from a change nobody made; too
long and the company holds personal data it has no reason to keep.

| Setting | Placeholder | Question |
|---|---|---|
| `retention.audit_log_days` | 2555 (seven years) | How long must the trail be kept? The placeholder is a commonly cited Indian statutory period for employment records; it has **not** been verified for this company. |
| `retention.device_event_days` | 365 | How long are raw punch events kept? They are the evidence behind a computed attendance day. |
| `retention.notification_days` | 365 | How long are in-app notifications kept? A notification is the record of somebody having been told. |

Purging the audit log needs the owner database role, so the application cannot
erase its own trail even if asked to.

### Announcements and notifications
No settings were added for these, which is itself a decision worth recording:
the behaviour that might have been configurable is instead per announcement, so
the person writing a notice chooses it rather than an administrator fixing it
once for everyone.

| Decision | Where it lives | Question |
|---|---|---|
| Who a notice reaches | `announcement_targets`, per announcement | Chosen when it is written, as the union of departments, locations, groups and named people. |
| Whether publishing notifies | `announcements.notify_on_publish` | On by default; a minor notice can be published quietly. |
| Whether a notice needs acknowledging | `announcements.priority = critical` | Which notices are important enough to demand an explicit confirmation is a company judgement. |
| How long an unsettled day waits before the employee is asked to correct it | the `--days-back` argument to `notify-unsettled-attendance` | Driven by the cron entry, since it depends how often the job runs. |
| Which categories an individual mutes | `notification_preferences` | Each person's own choice; the default is to notify. |

### Other
| Setting | Default | Question |
|---|---|---|
| `payroll.pay_day_of_month` | 1 | Which day of the following month is salary paid? |
| `payroll.rounding_mode` | `ROUND_HALF_UP` | How are computed amounts rounded? |
| `security.temporary_password_validity_days` | 7 | How long does an HR-issued password stay usable? |

Also undecided, and held outside `system_settings` because they are per-entity
records: **shift definitions** (Milestone 3), **work locations and their
timezones** (Milestone 2), **holiday calendars** (Milestone 6), and
**departments and designations** (Milestone 2).

---

## 3. `needs-legal-verification`

Since Milestone 8 these are not merely listed: each one lives in a
`statutory_rule_sets` version that must cite a published source before it can be
verified, and a payroll run built on an unverified set **cannot be finalized**.
The parameters of a verified set are frozen by database trigger, so a change is
a new effective-dated version rather than an edit to figures somebody has
already signed off. None has been entered.

**Nothing in this section has been verified. No rate, ceiling, slab or threshold
has been entered into this system.** The statutory engine (Milestone 8) stores
rules as effective-dated `statutory_rule_sets` rows that must be explicitly
marked verified, with a cited source, before a payroll run can be finalized.

| Area | What must be established | Status |
|---|---|---|
| **EPF** | Wage definition, employee and employer rates, wage ceiling, voluntary contribution handling, eligibility on joining | Not configured |
| **EPS** | Applicability, rate, ceiling, diversion from employer share | Not configured |
| **EDLI** | Applicability, rate, ceiling | Not configured |
| **PF admin charges** | Applicable charge basis and rate | Not configured |
| **ESI** | Wage threshold, employee and employer rates, contribution periods, treatment on crossing the threshold mid-period | Not configured |
| **Professional tax** | Registered state(s) and each state's slab table and deduction frequency | No state recorded |
| **TDS** | Applicable regime(s), slabs, surcharge, cess, employee declarations, perquisite valuation, Form 16 | Not configured |
| **Gratuity** | Eligibility service period, wage definition, formula, ceiling | Not configured |
| **Bonus** | Applicability, eligibility wage limit, calculation basis | Not configured |
| **Minimum wages** | Applicable schedule per state and skill category | Not configured |
| **Overtime** | Statutory multiplier and eligibility | Not configured |
| **Leave entitlement floors** | Statutory minimum leave under the applicable Shops & Establishments Act | Not configured |

Settings that gate the above:

| Setting | Default | Why it needs verification |
|---|---|---|
| `payroll.payable_days_basis` | `calendar_days` | Changes **every** loss-of-pay and proration amount. |
| `payroll.fixed_payable_days` | 26 | Used only with the `fixed_days` basis. |
| `payroll.enabled_statutory_modules` | `[]` *(empty)* | Nothing statutory runs until explicitly enabled. |
| `payroll.professional_tax_states` | `[]` *(empty)* | Slabs are per state; no state is assumed. |
| `company.financial_year_start_month` | 4 | Drives TDS and statutory reporting periods. |

**How the system behaves while these are unset.** `settings.require_confirmed()`
raises `configuration_missing` rather than returning a placeholder, so a payroll
code path that needs a real decision fails loudly instead of producing a
plausible but wrong figure. See
`tests/integration/test_settings_api.py::TestRequireConfirmed`.

---

## 4. Deliberately not claimed

Features whose architecture exists but whose external dependency has not been
exercised. These are not described as working anywhere in the product:

- **Integration with a specific biometric or access-control product.** The
  framework exists (Milestone 4) and the generic adapters are tested against
  their own contracts, but **no physical device has been exercised against this
  code**. Writing a vendor adapter needs that vendor's protocol documentation
  and a real unit to test against; until both exist, an adapter is listed as
  `generic`, never `tested`. The `rest_poll` adapter is in exactly that state.
  Web punching does not depend on any of it.

  *What is needed to close this:* the make and model of the units being bought,
  their protocol or API documentation, and one unit on the network to test
  against. With a machine that can export CSV, call a webhook or expose a JSON
  endpoint, the generic adapters may suffice with configuration alone.
- **Statutory leave entitlements.** No leave type is seeded and no statutory
  quota has been entered. Maternity, and any other entitlement set by law, has a
  mandated figure this project has not been given a verified reference for;
  creating it with an invented quota would be worse than an empty list. A type or
  policy whose entitlement comes from statute carries `requires_legal_review`,
  which is shown to employees rather than quietly trusted.

  *What is needed to close this:* the entitlements the company is actually bound
  by, with the statute or legal advice they come from. The machinery to express
  them - quota, accrual frequency, proration, calendar-day counting, carry-forward
  and encashment - is built and tested; only the numbers are missing.

- **Proration basis.** `leave.proration_basis` defaults to calendar days, which
  is the common convention but is a real policy choice affecting entitlement.
  Confirm it against company practice before relying on accrual figures.

- **Holiday dates for any year.** None are seeded. They vary by year, state and
  company, and a wrong one marks a working day as non-working for everyone on
  that calendar, in attendance and in payroll. HR publishes each year's list;
  the import accepts a pasted list and refuses a duplicate.

- **Leave encashment payment.** Year-end closing records the leave leaving the
  balance as an `encashment` entry. **Paying it is a payroll action** and
  Milestone 8 has not built that yet, so the entry is currently a record of
  intent rather than of a payment made.

- **Bank salary transfers.** Payroll produces a register; it does not move money.
  No bank format, file or API is implemented.

- **TDS is a projection, not a tax computation.** The engine annualises taxable
  income and applies whatever bands a company enters. It has **no** exemptions,
  deductions, rebates, surcharge, cess, regime choice, previous-employer income
  or investment declarations. A company enabling the module is choosing a
  projection, and should say so on its payslips; a full computation is a
  separate piece of work.

- **Overtime is measured but not paid.** The attendance engine computes overtime
  minutes and the payslip carries them, but no amount is derived from them. The
  multiplier is a legal rate, not a company preference, so it belongs in a
  verified statutory rule set; until one exists, a company that pays overtime
  should post it as an adjustment, which keeps the figure explicit and
  attributable.

- **Gratuity, bonus and leave-encashment formulas.** The shapes exist and are
  driven entirely by rule-set parameters. With no verified rule set, gratuity is
  zero and the settlement records why, and a settlement resting on an unverified
  set cannot be approved.
- **Statutory e-filing** (EPF ECR, ESI, TDS returns). Out of scope until the
  relevant portal integration is built and tested.
- **Email, SMS and push notification delivery.** Optional, and **no adapter is
  implemented**. The in-app centre (Milestone 7) is the only channel. The
  `notifications.channel` column admits `email`, `sms` and `push` so one can be
  added without a migration, but a notification is currently seen when somebody
  signs in, not pushed to them. Nothing in the system depends on a paid
  provider, which is what the brief requires.

- **Payroll publication notifications.** The hook and its wording exist and are
  tested (`notifications.hooks.payslip_published`, deliberately carrying no
  figures, since a notification is not a secure channel for pay). Nothing calls
  it yet, because payroll arrives in Milestone 8.
- **Free hosting suitability.** Evaluated in Milestone 10. Confidential payroll
  data must not be placed on a provider whose terms and security have not been
  reviewed.
