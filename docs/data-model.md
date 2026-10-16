# Data model

Roughly 70 tables when complete, in ten clusters. **51 exist today**; tables
marked with a later milestone are planned, and their design notes are here so
the shape of the finished schema is visible.

ASCII only, deliberately: these diagrams were corrupted once by a shell
re-encoding the file, and box-drawing characters are not worth the risk.

## 1. Identity and access [built, M1]

```
permissions --< role_permissions >-- roles --< user_roles >-- users
                                                                |
                                              refresh_tokens ---+
                                              login_attempts ---+
```

| Table | Notes |
|---|---|
| `users` | `username` unique and lowercase (check constraint). `employee_id` nullable - the bootstrap admin predates any employee record. Deactivated, never deleted, so history keeps a resolvable actor. |
| `roles` | `is_system` marks the two roles the app depends on; they may gain permissions but must not be deleted. |
| `permissions` | Reconciled from `app/core/permissions.py`. Codes are permanent identifiers. 80 of them. |
| `refresh_tokens` | Stores the SHA-256 digest only. `replaced_by_id` is set on rotation, which is how replay is distinguished from an ordinary revocation. |
| `login_attempts` | Keyed on the *submitted* identifier, so attempts against non-existent usernames are throttled too. |

## 2. Organisation [built, M2]

| Table | Key columns |
|---|---|
| `departments` | `code`, `parent_id` (self-FK, loop-checked), `head_employee_id` |
| `designations` | `code`, `level` |
| `work_locations` | `code`, `timezone` - **drives attendance date derivation**, validated against the IANA database on write |
| `employee_groups`, `employee_group_members` | For targeting announcements and holiday calendars |

## 3. Employees [built, M2]

| Table | Notes |
|---|---|
| `employees` | `employee_code` unique and upper-case; self-FK `reporting_manager_id`; `employment_status`, `date_of_joining`, `date_of_exit`. A check constraint ties `status = 'exited'` to the presence of an exit date. |
| `employee_employment_history` | Effective-dated rows. A transfer, promotion, manager change or exit closes the open period and opens a new one. |
| `employee_statutory_details` | UAN, PF/ESI numbers, PAN (encrypted), Aadhaar last four only. Behind `employee.read_sensitive`. |
| `employee_bank_accounts` | Account number encrypted; a partial unique index allows exactly one primary per employee. |
| `employee_documents` -> `stored_files` | `stored_files` holds a server-generated storage key, SHA-256, size and content type. |

Records are preserved after exit - attendance, leave, payroll and audit history
must remain intact and attributable.

## 4. Shifts and working calendar [built, M3]

```
weekly_off_patterns --< work_calendars --< calendar_exceptions
                              |
employees --< shift_assignments >-- shifts
```

| Table | Notes |
|---|---|
| `shifts` | `start_time`, `end_time`, derived `crosses_midnight`, break minutes. Grace periods and full/half-day thresholds are nullable and inherit the company setting when unset. `early_punch_window_minutes` and `late_punch_window_minutes` define the attribution window. |
| `shift_assignments` | `employee_id`, `shift_id`, `effective_from/to`. An **exclusion constraint** (gist, employee plus daterange) prevents overlap, because the service check cannot hold under concurrent writes. |
| `weekly_off_patterns` | Seven booleans rather than JSON, so "is Saturday off for this pattern" is answerable in SQL. |
| `work_calendars` | A pattern plus exceptions, optionally per location. Partial unique indexes enforce exactly one company default and at most one active calendar per location. |
| `calendar_exceptions` | Per date, `working` or `non_working`. A `working` exception beats both the weekend and a holiday. |

## 5. Attendance [built, M3]

```
attendance_events   (raw, immutable, append-only)
        |
        |  paired into
        v
attendance_sessions ---> attendance_days   (derived, recomputable)
                              ^
                              |
                  attendance_regularizations
```

| Table | Critical constraints |
|---|---|
| `attendance_events` | `unique(source, external_event_id)` for idempotent ingest. A trigger rejects any `UPDATE` to the core columns, permitting only the supersede bookkeeping; `DELETE` is revoked from the runtime role. Server timestamps only. |
| `attendance_days` | `unique(employee_id, work_date)`. Holds `status`, the minute figures, the `shift_id` actually used, `payable_day_fraction` as `NUMERIC(4,2)`, a `policy_snapshot`, and any `anomalies`. Entirely derived - safe to delete and rebuild. |
| `attendance_sessions` | One completed in/out pair, so "when were you actually here" is answerable and not merely summed. |
| `attendance_regularizations` | Two-stage decision with full history. A partial unique index allows one *open* request per employee per date; decided ones do not block. |

The separation is the point: a policy change is applied by recomputing, not by
editing figures, and a correction adds an event rather than rewriting history.
`policy_snapshot` is what lets a figure computed under an old policy still be
explained after the policy changes.

## 6. Device integration [built, M4]

| Table | Notes |
|---|---|
| `devices` | `adapter_type` (checked against the known set), `config` JSONB holding the field mapping, `direction_mode`, `clock_drift_tolerance_seconds`, `timezone_override`, `sync_cursor`, `last_seen_at`, `consecutive_failures`. Credentials live in `api_secret_encrypted` and `webhook_secret_encrypted`, encrypted under the field key and never returned; `SELECT` on those two columns is revoked from the read-only role. |
| `device_employee_mappings` | Which employee a device identifier referred to, **and when**. Partial unique index on `(device_id, external_employee_ref) WHERE effective_to IS NULL` allows one open mapping, because enrolment slots get reused and two open mappings would make a punch's owner ambiguous. |
| `device_sync_runs` | `unique(idempotency_key)` -- a retried scheduled run is recognised as a repeat rather than re-fetching. Counts are **per outcome** (fetched, accepted, duplicate, unmapped, failed, ignored) because one total cannot distinguish a healthy sync from a broken mapping. |
| `device_raw_events` | `unique(device_id, external_event_id)` = deduplication. Raw payload retained always, even for events that could not be interpreted. `status` is one of pending / mapped / duplicate / unmapped / failed / ignored. A partial index on the pending/unmapped/failed slice backs the reconciliation queue. |

`attendance_events.device_id` references `devices` with `ON DELETE SET NULL`:
retiring a device must never delete the attendance it recorded.

**Immutability.** A trigger rejects any UPDATE that changes what the device
actually sent -- `raw_payload`, `raw_timestamp`, `raw_direction`,
`external_event_id`, `external_employee_ref`, `received_at`. The processing
columns are expected to change; that is how an event moves from `unmapped` to
`mapped` once a mapping exists. `DELETE` is revoked from the runtime role.

**Timestamps.** `parsed_occurred_at` is `TIMESTAMPTZ` in UTC and
`timezone_used` records the zone the naive value was anchored to, so an
attribution can be re-explained even after a device is moved. The format is
taken from the device's declared `timestamp_format`, never guessed.

**Honesty.** Adapters carry a maturity level (`tested`, `generic`,
`development_only`). No adapter is written against a specific biometric or
access-control product, and none is described as integrated. Adding one requires
that vendor's protocol documentation and a real unit to test against.

## 7. Leave [built, M5]

| Table | Notes |
|---|---|
| `leave_types` | Paid flag, whether it deducts a balance at all, half-day support, calendar-day counting, attachment threshold, notice, consecutive and service limits, gender restriction, `requires_legal_review` |
| `leave_policies` | Effective-dated per type: quota, accrual frequency, rounding, proration flags, max accumulation, carry-forward cap and expiry, encashment. `unique(leave_type_id, effective_from)` so "which rules applied on this date" has one answer. |
| `employee_leave_policies` | Effective-dated assignment. Partial unique index on `(employee_id, leave_type_id) WHERE effective_to IS NULL` - one open assignment, or the entitlement would be ambiguous. |
| `leave_applications` | Pins `leave_policy_id` and a `policy_snapshot`, so a past decision stays explicable after everything is reconfigured. Carries the `paid_days` / `unpaid_days` split that payroll needs. `supersedes_application_id` makes a modification a new row rather than an edit. |
| `leave_application_days` | One row per calendar day, with `portion` (full / first half / second half), `day_fraction`, `is_paid`, the `day_type` at submission, and `counts_against_balance`. A non-consumed day is still stored, so a calendar shows the full span the employee was away. |
| `leave_approvals` | One row per stage decision with a sequence. **Append-only by trigger** - a rejected-then-approved history must stay legible. `was_auto_skipped` explains a missing manager stage. |
| `leave_ledger_entries` | **Append-only by trigger**, `UPDATE`/`DELETE` revoked from the runtime role. `txn_type` is one of opening, accrual, deduction, reversal, adjustment, carry_forward_in, carry_forward_out, expiry, encashment, compoff_credit. `idempotency_key` unique. `days` is signed, so the balance is a plain `SUM` no caller can get wrong. |

**Balance is a `SUM` over the ledger, never a mutable column.** A manually
editable balance field cannot be reconciled or explained, and is the usual
source of disputes.

**Dates carry two different meanings and both are stored.** `effective_date` is
the date a movement belongs to - a deduction is dated on the leave's own start,
and an accrual for March posted late still belongs to March. `leave_year` is
the year it counts towards for carry-forward, which is not the calendar year
because the leave year's start month is configurable. Conflating them would
either misdate a balance or carry it forward at the wrong moment.

**Idempotency keys are deterministic and exclude everything that varies between
runs.** `accrual:<employee>:<type>:<period>` omits the run id and the posting
date, which is what makes a retried accrual job insert nothing rather than
credit a balance twice.

## 8. Comp-off [built, M6]

| Table | Notes |
|---|---|
| `compoff_credits` | A **lot**: `earned_date`, `attendance_day_id` as the proof, `credit_days`, `allocated_days`, `expiry_date`, `status`, verifier, `reversal_of_id`. `unique(employee_id, earned_date)` prevents double credit; `allocated_days <= credit_days` by check constraint; a trigger refuses any rewrite of the earned facts. |
| `compoff_applications`, `compoff_application_days` | Consumption, day by day with portions |
| `compoff_allocations` | **Which lot each request drew on, and how much.** `unique(application_id, credit_id)`; the credit is `ON DELETE RESTRICT`. |
| `compoff_approvals` | One row per stage decision, append-only by trigger |

**Lots and allocations rather than a signed ledger** - a deliberate change from
the original plan, which had `compoff_ledger_entries`. Accrued leave is fungible,
so a ledger sum is the right model for it. Comp-off is not: each credit expires
on its own date, so consuming a day has to say *which* credit it spent, or expiry
cannot be computed. A balance is `SUM(credit_days) - SUM(allocated_days)` over
live lots, which is both explainable and lot-accurate, and a third representation
of the same facts could only drift from them.

Credits come from `attendance_days` on a day whose `day_type` is configured as
qualifying, using the net minutes the engine already recorded. Consumption is
earliest-expiring first, so no credit is lost that could have been used.

## 9. Holidays [built, M6]

| Table | Notes |
|---|---|
| `holiday_calendars` | Per calendar year, optionally scoped to a work location or employee group. `unique(code, year)`. `is_published` gates the effect: a draft affects nothing, so a list can be prepared and checked before it marks dates non-working for everyone in scope. |
| `holidays` | `holiday_type` is one of public, company, optional, restricted. `unique(calendar_id, holiday_date)` so "is this optional" has one answer. |
| `optional_holiday_elections` | An employee's chosen optional holidays. A withdrawal sets the status rather than deleting the row, because attendance may already have treated the day as non-working and the record is what explains it. |
| `holiday_import_runs` | `unique(idempotency_key)` derived from the calendar and the content, so the same list imports once |

Applicable calendars are **unioned**, not chosen between: a regional list
supplements the national one. These feed `CalendarSpec.holiday_dates`, which the
day-type resolver has accepted since Milestone 3 - so attendance, leave and
comp-off see the same holidays with no change to the resolver, which is why
`calendar_spec` now takes an optional `employee_id` (elected optional holidays
make the set per-employee).

## 10. Payroll [built, M8]

```
salary_components --< salary_structure_components >-- salary_structures
                                                              |
employees --< employee_salaries (revisions) >-----------------+
                     |
payroll_periods --< payroll_runs --< payslips --< payslip_lines
                          |
                          +-- statutory rule set version (pins the rules used)
```

| Table | Notes |
|---|---|
| `salary_components` | Component type (earning, deduction, employer contribution, reimbursement), calculation type, and the wage-base flags: `is_pf_wage`, `is_esi_wage`, `is_pt_wage`, `is_taxable`. `prorates_on_payable_days` decides whether the amount shrinks in a short month. A check constraint refuses a percentage-of-component with no base, and the service refuses a percentage of an already-derived component. |
| `salary_structures`, `salary_structure_components` | A reusable template. A template only: a revision copies the resolved amounts, so editing a structure later cannot change what somebody is already paid. |
| `employee_salaries` | Effective-dated revisions; never edited in place. `unique(employee_id, effective_from)`, and the previous revision is closed the day before the next begins. |
| `employee_salary_components` | The **resolved** monthly amount per component, with the basis it came from. Resolved once, at the revision, where somebody can check it - not re-derived on every payroll run. |
| `payroll_periods`, `payroll_runs` | Lifecycle: draft, calculated, reviewed, approved, finalized, cancelled. `payroll_runs.idempotency_key` is unique over the period and run type, so two regular runs for one month are impossible. A finalized run is immutable by trigger. |
| `payslips` | `unique(run_id, employee_id)`; payable days, LOP days, the day-type breakdown, totals, and `review_flags` - what a human should look at, which flags but never blocks. Immutable by trigger once its run is finalized. |
| `payslip_lines` | Per-component amount **plus the basis** it was computed from, and for a statutory line the rule set that produced it. "PF 1800" cannot be checked by the person it was deducted from; "10% of 20000, the configured ceiling" can. |
| `payroll_adjustments` | Arrears, bonuses, recoveries, reversals - the only way to correct a finalized run. The amount is always positive and the type decides the direction; `unique(idempotency_key)` so a retried import pays nobody twice. |
| `statutory_rule_sets` | Per domain (epf, eps, edli, esi, pt, tds, gratuity, lwf), effective-dated and versioned, with `status` (draft, verified, superseded), `source_reference` and verifier. A check constraint refuses the verified status without a source, a verifier and a timestamp. `unique(domain, jurisdiction, state_code, effective_from)` **nulls not distinct**, so two national rule sets cannot take effect on the same date. |
| `statutory_rule_parameters` | The individual rates, ceilings and slabs. Exactly one of `numeric_value` and `json_value` by check constraint - a rate cannot be stored as a string that happens to parse, and a slab table needs no table of its own. Frozen by trigger, against update *and* delete, once the rule set is verified. |
| `employee_statutory_eligibility` | Per employee, per domain, effective-dated, because coverage changes. A row is required to **exclude** somebody as well as to include them, with a reason: an exemption should be a recorded decision, not missing data. |
| `fnf_settlements` | Full and final settlement. `gratuity_rule_set_id` cites the rules used; with none verified, the gratuity line is zero and the reason is recorded. |

**Nothing in these tables is seeded.** No rate, ceiling, slab or salary
component. A statutory figure entered without a verified source produces a wrong
deduction that looks right, which is worse than a refusal, so the schema makes
the refusal possible: a draft rule set computes but cannot be finalized against,
and verification requires a cited source.

`payroll_runs` records the statutory rule-set version it used **and** the
settings in force (`settings_snapshot`). That is what makes a past payroll
**reproducible** after either changes - the single most important property of
the payroll schema.

Payable days come from summing `attendance_days.payable_day_fraction`, which the
attendance engine already stores as an exact decimal, so payroll and attendance
cannot disagree. A day with no attendance record, or one left `incomplete` or
`missing_punch`, is paid in full and flagged: the brief forbids treating a
missing punch as absence, and docking pay on a gap in the records would be the
same mistake with money attached.

Every amount is `NUMERIC(14, 2)` and every day count `NUMERIC(6, 2)`. No
monetary value is a float anywhere in this system, in Python or in the
database.

## 11. Announcements and notifications [built, M7]

| Table | Notes |
|---|---|
| `announcements` | Title, body, optional summary, `priority`, `status` (draft, scheduled, published, expired, withdrawn). `publish_at` and `expires_at` carry the schedule; `pin_until` keeps a notice at the top. `notifications_sent_at` records that fan-out has happened, so a republish cannot notify twice. `attachment_file_id` and `image_file_id` reference `stored_files`. Withdrawal keeps the row, with `withdrawn_at` and `withdrawal_reason`. |
| `announcement_targets` | `target_type` is one of all, department, work_location, employee_group, employee, with `(target_type = 'all') = (target_id IS NULL)` by check constraint. `target_id` is deliberately **not** a foreign key: it points at one of four tables, and the service validates it against the right one on write. |
| `announcement_reads` | One row per announcement and employee; `read_at` immutable by trigger, `acknowledged_at` separate and never withdrawable. Opening a critical notice is not the same as confirming it. |
| `notifications` | One row per person per fact. `dedupe_key` **unique** - the guarantee the whole feature rests on. Belongs to `user_id` (who signs in) with an optional `employee_id`; `link_path` is an application path, never a URL, since the host differs per deployment. |
| `notification_preferences` | `unique(user_id, category, channel)`. A row exists **only** where somebody has asked for silence, so the absence of a row means "notify me" and the default needs no backfill. |

**Announcements and notifications are separate tables, not one "message"
table.** An announcement is a single company artefact with an audience, a
schedule and a read report; a notification is one line in one person's centre.
Merging them would mean either duplicating a notice's text per recipient or
losing the per-person read state.

**The dedupe key names the fact, not the attempt.** It may contain the
application id, the decision stage, the attendance date, the announcement id,
the user id - never the run id, the posting time or a sequence number. That is
what makes every emitting job safely re-runnable, and it is enforced by the
unique constraint rather than by each caller remembering to read before writing,
which two concurrent runs would defeat. Where a fact legitimately recurs, the
recurrence is *in* the key: a missing punch on 3 March and one on 4 March are
two facts, and a manager's approval and HR's approval of one application are two
things the applicant should hear about.

**Visibility is a query, not a state somebody sets.** A published announcement
is one whose `publish_at` has passed, so a missed scheduler run delays the
*notification* and never hides the announcement. The scheduled job only sends
what the time has already made visible.

Both hot paths are partial indexes - `ix_notifications_unread WHERE read_at IS
NULL` for the badge and `ix_announcements_live WHERE status = 'published'` for
the feed. Both are small slices of tables that only grow, so indexing the whole
of either would cost more to maintain than it returns.

Delivery is **in-app only**. `channel` admits email, sms and push so an optional
adapter can be added without a migration, but nothing requires a paid service.

## 12. System [built, M1]

| Table | Notes |
|---|---|
| `system_settings` | JSONB value with a declared `value_type` and `constraints`; `is_confirmed` and `requires_legal_review` carry the honesty flags |
| `setting_change_history` | Append-only; indexed by key and time, so "what was the grace period on 3 March" is a cheap query |
| `audit_logs` | Append-only, enforced by trigger. Before and after values, actor, IP, request id. |
| `job_runs` | `idempotency_key` unique - the mechanism that makes re-running a scheduled job safe |

## Shared invariants

1. **Effective dating over mutation.** Anything that changes over time - shifts,
   salaries, policies, statutory rules - is a new effective-dated row, never an
   update. Historical calculations stay explicable.
2. **Ledgers over balances.** Leave and comp-off balances are derived from
   append-only transactions.
3. **Raw separate from computed.** Attendance events and device events are kept
   as received; summaries are derived and recomputable.
4. **Uniqueness as the idempotency mechanism.** Database constraints, not
   application checks, prevent duplicate credits, punches and overlapping
   assignments - they hold under concurrency and retries.
5. **Decimal money, timezone-aware instants, local calendar dates.**
6. **Immutability where it matters, enforced in the database.** Triggers protect
   `audit_logs`, `attendance_events`, `leave_ledger_entries`, `leave_approvals`,
   `compoff_approvals`, the earned facts of `compoff_credits` and the raw columns
   of `device_raw_events` from being rewritten, and privileges are revoked from the runtime role rather
   than relying on application discipline.
