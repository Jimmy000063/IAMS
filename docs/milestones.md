# Implementation milestones

A feature counts as done only when UI, API, persistence, authorization,
validation, error handling and tests work together. "Scaffolded" means the
interface exists but no behaviour is claimed.

---

## Milestone 1 -- Foundation -- COMPLETE

Configuration, database, identity, auditing, administrator bootstrap, and a
working vertical slice through the whole stack.

**Backend**
- Settings module with typed validation, constraints, change history and the
  `is_confirmed` / `requires_legal_review` honesty flags. 50 settings seeded.
- Permission catalogue: 79 permissions, 2 system roles, 102 grants.
- Authentication: Argon2id, JWT access tokens, rotating opaque refresh tokens,
  reuse detection, lockout, forced change of temporary passwords.
- Audit log with key-name redaction; append-only enforced by a database trigger.
- Alembic revision `0001` with check constraints, a partial index and privilege
  revocation. `alembic check` reports no divergence from the models.
- CLI: `check-db`, `seed-permissions`, `seed-settings`, `bootstrap-admin`,
  `config-status`, `benchmark-hash`, `purge-expired-sessions`.

**Frontend**
- Vite + React 19 + TypeScript (strict, `exactOptionalPropertyTypes`).
- Design tokens, accessible UI primitives, role-based navigation, four-state
  data views, toasts, dialogs, keyboard operability.
- Working sign-in, forced password change, dashboard, and a complete settings
  management screen driven entirely by the backend catalogue.

**Verified** -- 194 backend tests, 14 frontend tests, typecheck, lint, production
build, and a manual end-to-end pass from the browser's own proxy path through to
the database.

---

## Milestone 2 -- Organisation, employees, dashboards -- COMPLETE

**Backend** (migration `0002`, 11 new tables, 44 new endpoints)
- Departments (nested, loop-checked), designations, work locations with
  **IANA-validated timezones**, employee groups.
- Employees with effective-dated `employee_employment_history`; onboarding,
  transfer, promotion, manager change and exit each open a new period.
- Reporting tree with cycle detection -- a loop would make the leave-approval
  walk in M5 run forever, so it is rejected at assignment time.
- Statutory identifiers and bank accounts behind `employee.read_sensitive`,
  with **PAN and account numbers encrypted at rest** (Fernet, key separate from
  the token-signing key) and returned only masked. Aadhaar: last four digits
  only -- the full number is never accepted.
- Document upload with magic-byte verification, server-generated storage keys,
  attachment-only download, and an audit entry per download.
- CSV import: one code path for preview and commit, all-or-nothing, duplicate
  detection within the file and against the database, forward manager
  references, day-first date parsing (month-first is refused as ambiguous).
- Employee-code and work-email sign-in via the linked account.
- Employee and HR dashboards from real queries, with unbuilt metrics named by
  milestone rather than returned as zero.

**Frontend**
- Employee directory (URL-encoded filters, search, pagination), detail page with
  on-demand restricted sections, create/edit form, CSV import wizard,
  self-service profile page, organisation management, rebuilt dashboards.

**Verified** -- 377 backend tests, 14 frontend tests, `alembic check` clean,
migration reversible, lint/typecheck/build clean, and a manual end-to-end pass
covering org setup, onboarding, transfer history, encryption-at-rest (confirmed
by querying the database directly), CSV import, employee-code sign-in, data
isolation and the self-service boundary.

**Two design corrections made during this milestone**, both caught by tests:
`PATCH /employees/me` silently ignored privileged fields and returned 200 (now
`extra="forbid"` -> 422), and `resolve_timezone` fell back to the default so an
invalid timezone was accepted at write time (now a strict
`validate_timezone_name`).

## Milestone 3 -- Attendance engine and shifts -- COMPLETE

**The engine** (`modules/attendance/policy.py`, pure functions, 76 tests)
- A punch is attributed to the **shift date**, derived in the employee's
  work-location timezone via an attribution window around the shift instance.
  For an overnight shift that is the date the shift *started* -- a 02:00 punch on
  the 5th belongs to the 4th. Without this, a night shift reads as two
  half-days either side of midnight.
- Session pairing with a defined outcome for every kind of messy data:
  duplicate punch-in (keep the earlier), orphan punch-out (record and skip),
  unclosed punch-in (flag; **invent no departure time**), zero-length pair.
- Measurements kept separate from judgments: `late_minutes` is the actual
  lateness, `is_late` is whether it exceeds grace. Changing the grace period
  does not rewrite the measurement.
- Break deduction takes only the shortfall, so a real 45-minute break is not
  penalised twice by a 30-minute assumption. Rounding applies once to the day,
  not per session.
- `payable_day_fraction` is a stored `Decimal` that payroll multiplies by, so
  status and pay cannot disagree.

**Shifts and calendars**
- Shifts with derived `crosses_midnight`; thresholds left null inherit the
  company setting, so a night shift overrides only what differs.
- Effective-dated assignments, with an **exclusion constraint** preventing
  overlap -- the service check cannot hold under concurrent writes.
- Work calendars: weekly-off pattern plus date exceptions, with a `working`
  exception beating both weekends and holidays. One resolver, shared with leave
  from Milestone 5, so the two engines cannot disagree.

**Guarantees enforced by the database**
- `attendance_events` is immutable: a trigger rejects any UPDATE to the core
  columns, permitting only the supersede bookkeeping. `DELETE` is revoked from
  the runtime role.
- Recomputation is idempotent, honours
  `attendance.recompute_locked_after_days`, and preserves HR's manual overrides.

**Regularization** -- employee -> manager -> HR. Nobody decides their own request;
a manager's authority is checked against the reporting tree, not just the
permission; approval **inserts events and recomputes** rather than editing
figures. A missing manager escalates straight to HR.

**Frontend** -- a custom date picker (replacing the native one, shading
non-working days), a month calendar colour-coded by status with per-day session
detail, a punch widget whose elapsed clock is anchored to the server's time,
shift timeline bars, a calendar exception editor on the month grid, HR tabs for
today/by-employee/approvals/anomalies.

**Verified** -- 550 backend tests, 14 frontend tests, `alembic check` clean,
migration reversible to base and back. Against the live stack: the overnight
punch pair produced **one** present day on the 3rd; the immutability trigger and
the overlap exclusion constraint both fired; a missing punch became `incomplete`
with payable 0.00, not absent; three recomputations left 10 days and 1 session
unchanged; and the approval chain applied nothing until HR decided.

## Milestone 4 -- Device integration framework -- COMPLETE
*Depends on: M3*

**The honest part first.** No adapter is written against any specific biometric
or access-control product, and none is described as integrated. Vendor protocols
differ in transport, authentication, time handling and event semantics; an
adapter built from a datasheet alone cannot be trusted with attendance data.
Adding one needs the protocol documentation *and* a unit to test against. The
`maturity` field on every adapter states what it actually is -- `tested`,
`generic` or `development_only` -- and the catalogue endpoint surfaces that to
the operator rather than leaving it to be discovered after configuration.

**Adapters** -- `csv_upload` and `webhook` are implemented and tested against
their contracts; `rest_poll` is tested against a stub and marked `generic`
because no hardware has been polled with it; `simulator` is `development_only`
and refuses to run outside a local or test environment, so it cannot fabricate
attendance in a live system. An adapter's only job is to produce raw payloads --
it never parses timestamps, infers direction, deduplicates or writes.

**One ingestion path** -- every adapter feeds `devices.service.ingest`, so a new
adapter cannot get the hard parts wrong in its own novel way. In order: store
every payload, deduplicate, map to an employee, apply as an attendance event,
recompute the affected days once each, report per outcome.

**Interpretation rules, each stated rather than inferred**
- Timestamp formats are **declared, never sniffed**: `03/04/2026` is a different
  day under day-first and month-first, and the wrong choice is invisible until a
  month-end report.
- Naive timestamps are anchored to the device's declared zone (device override,
  then work location, then company default) -- **never the server's**.
- The shift date is then derived through the same attendance policy a web punch
  uses, so an overnight shift lands on the date it started.
- A punch stamped beyond the drift tolerance into the future is **refused**;
  device clocks drift and some reset after a power cut.
- Direction is inferred only when the device does not report it, the inference
  is flagged in the data, and a batch is sorted chronologically first -- a
  buffered device often delivers newest-first after an outage.
- An inferred direction is **excluded** from a derived event id, so reprocessing
  in a different order cannot defeat deduplication.

**Guarantees enforced by the database**
- `unique(device_id, external_event_id)` -- a device re-sending its buffer
  cannot double-count a punch. Devices that number nothing get an id derived
  from the content.
- `device_raw_events` raw columns are immutable by trigger; the processing
  columns may change, which is how an event moves from `unmapped` to `mapped`.
  `DELETE` is revoked from the runtime role.
- A partial unique index allows **one open mapping** per device identifier --
  two would make a punch's owner ambiguous.
- `unique(idempotency_key)` on sync runs -- a retried scheduled job is
  recognised as a repeat and fetches nothing.

**Reused enrolment slots** -- mappings are effective-dated, and a punch resolves
through the mapping in force on *its own* date. Slot 47 belonging to one employee
until March and another afterwards keeps February's punches with the first.

**Nothing is discarded** -- events that cannot be interpreted or attributed are
stored with their reason and queued. A wrong field mapping or a missing employee
mapping is then correctable after the fact: fix it and reprocess, without asking
the device to re-send. Already-applied events are refused for reprocessing,
since that would duplicate the punch.

**The webhook** -- the only unauthenticated write path in the application, and a
test asserts it stays that way. A device will not perform a sign-in, so
authentication is **HMAC-SHA256 over the raw request body**, verified before any
parsing; verifying after would let an attacker alter semantically-equivalent
content. A device with no secret is refused rather than accepted
unauthenticated. The refusal does not say which check failed, and a rejected
delivery is audited in its own committed transaction so the evidence survives
the rollback the 401 causes.

**Outbound polling** is validated against server-side request forgery: http(s)
only, no credentials in the URL, cloud-metadata and link-local addresses always
refused, private and loopback addresses only with an explicit opt-in -- a LAN
device legitimately is a private address. A failed poll never echoes the device's
own error text, which can contain the credential just sent to it.

**Failures stay visible** -- a device that cannot be reached, or whose body
cannot be read, closes the run as `failed` and **returns** it rather than
raising, because raising would roll back the very record saying it failed. A
device that goes silent raises no error at all, so the health screen names quiet
devices explicitly while the gap is still small enough to fix.

**Scheduled sync** -- `iams sync-devices`, idempotent twice over: the window is
the current interval slot rather than the instant, and events deduplicate on
arrival regardless. One dead reader never stops the rest. Off by default;
`devices.auto_sync_enabled` governs only hardware polling, never web punching.

**Frontend** -- four tabs: health (quiet, never-reported and failing devices),
the device register with per-adapter actions, a reconciliation queue showing
exactly what the device sent and letting an operator attribute an event and
learn the mapping in one step, and the adapter catalogue with each adapter's
maturity and caveats shown rather than buried. The create form is driven by the
catalogue, so a backend adapter surfaces its settings without a frontend change.

**Verified** -- 685 backend tests (131 new), 21 frontend tests, `alembic check`
clean, migration 0004 reversible and re-applied. Against the live stack, 40
checks passed: an unsigned and a wrongly-signed delivery were both refused with
401 and stored nothing; the refusal named no specific failed check; a signed
delivery for an unmapped identifier was queued with its payload intact; the same
batch re-delivered counted as a duplicate; mapping the identifier and
reprocessing applied the stored punch and produced one attendance day -- on
**2026-03-09**, not the 10th, because that employee is on a 22:00--06:00 shift,
proving the device path uses the same attribution rule as a web punch; a lone
punch-in became `incomplete` with payable `0.00`; a second open mapping was
refused 409; a punch dated 2031 was refused naming the device clock; an
unreadable body produced a recorded failed run with its reason; the database
refused to rewrite stored device output; a cloud metadata URL was refused; the
webhook secret had zero plaintext matches in the database; and exactly one
unauthenticated write endpoint exists.

## Milestone 5 -- Leave management -- COMPLETE
*Depends on: M2, M3*

**The honest part first.** No leave types are seeded. The statutory ones have
legally mandated entitlements this project has not been given verified
references for, and inventing a quota would be worse than an empty list. A type
or policy whose entitlement comes from statute carries `requires_legal_review`,
surfaced to employees and in the configuration health report. No statutory
entitlement has been entered anywhere in this system.

**The central decision: a balance is a `SUM`, never a column.** A mutable
balance field cannot be reconciled or explained, and is the usual source of
disputes. Every movement is an append-only row with a type, a reason, an actor
and a link to its cause; a trigger refuses `UPDATE` and `DELETE`, and both are
revoked from the runtime role. A mistake is corrected by a reversal, keeping the
error and the fix side by side.

**Four balance figures** -- `balance_days` (the ledger as of a date),
`pending_days` (awaiting a decision), `committed_days` (approved but dated
later) and `available_days` (the only one a new request is checked against).
A deduction is dated on the leave's own start date so carry-forward attributes
it to the right leave year; that means leave approved today for next month does
not move today's total, and `committed_days` is what stops an employee booking
the same entitlement twice. A reversal nets off the commitment, so cancelling
future leave releases the days.

**Effective dating** -- policies are superseded, not edited, and each
application pins the policy version plus a snapshot of the settings in force.
Raising a quota in July cannot change how a March application was decided.
A partial unique index allows one open policy assignment per employee and type,
and one policy per type per start date, so "which rules applied" has exactly one
answer.

**Accrual, idempotent twice over** -- keyed
`accrual:<employee>:<type>:<period>`, with the unique constraint rejecting a
duplicate so a retried run reports `already_posted` rather than crediting twice.
Periods are anchored to the **leave year**, not the calendar, so an April year
has quarters running April to June and the periods tile the year with no gap or
overlap. Proration is a fraction of the period, on a configurable basis
(calendar days, working days or completed months), and rounding is applied
**once** per period -- twelve monthly accruals of 1.25 rounded each time would
give 18 days against a 15-day quota. The accumulation ceiling stops accrual
rather than failing. Available as `iams accrue-leave` for a cron entry.

**Approval** -- manager then HR, with three rules enforced on the server rather
than assumed from the UI: nobody decides their own application whatever
permissions they hold; a manager stage requires the actual reporting
relationship; and stages run in order. Only the final stage deducts, so a
request a manager approved and HR refused never touches the balance. A missing
manager routes straight to HR and records the skip as a stage decision, so a
history with no manager row explains itself. Decisions are append-only, enforced
by trigger.

**Day counting** uses the shared calendar resolver, the same one attendance
uses, so the two cannot disagree. A non-working day inside a range is not
consumed by default; the sandwich rule charges only the ones *between* two leave
days (never a leading or trailing weekend); a type defined in calendar days
charges all of them. Half-day portions apply to the ends of a range only.

**Over-balance requests are split, not refused** -- five days needed with three
in hand becomes three paid and two unpaid, with the per-day split stored for
payroll. Forcing a choice between a refusal and an unrecorded absence helps
nobody.

**Attendance integration** -- approving or cancelling leave recomputes the
affected days, so a payslip is never built from attendance that still says
"absent". Paid leave is a payable day, unpaid leave pays nothing, and a half day
pays its half plus the worked half if the employee met the half-day threshold.
Only *approved* leave counts; a pending request must not mark a day as leave, or
an employee could avoid being marked absent simply by applying.

**A preview endpoint** runs the same expansion and checks without writing, so
the form can show "3 days, the Sunday is not deducted" before submission rather
than the browser reimplementing the calendar rules and drifting from them.

**Frontend** -- balances with each figure's meaning shown, an application form
whose cost preview comes from the backend, the full decision history including
the policy snapshot, a per-employee ledger view, and an admin page with the
approval queue, leave types, policies and accrual.

**Two bugs this milestone found and fixed in earlier work:** `ActorMixin`
declared its foreign keys with `use_alter=True`, which emits nothing inside
`create_table` -- so `created_by_user_id` had no referential integrity at all;
and the attendance engine returned `payable 0` for approved leave, correct only
while no leave existed. Paid leave is now a payable day, with the M3 test
expectation corrected.

**Verified** -- 857 backend tests (172 new), 32 frontend tests, `alembic check`
clean, migration 0005 reversible and re-applied. Against the live stack, 44
checks passed: a second policy from the same date refused 409; an opening
balance granted once and the repeat crediting nothing; the preview reporting 3
days across a 4-day span and naming the non-working day -- including honouring a
*working Saturday* calendar exception, which proves leave and attendance share
one resolver; a pending request leaving the balance at 10 with 7 available; an
overlapping request refused naming the clashing application; approval producing
exactly one deduction row and three `on_leave` attendance days at `payable 1.00`;
a decided application refused a second decision; the database refusing to rewrite
or delete a ledger entry and refusing to rewrite a recorded decision;
cancellation posting a reversal beside the deduction, returning the days and
reverting attendance to `absent`; a re-run of accrual posting nothing and
reporting `already_posted`; one accrual row in the database, not two; and a
double-submitted adjustment refused 409.

## Milestone 6 -- Comp-off, carry-forward, holidays -- COMPLETE
*Depends on: M5, M3*

**The honest part first.** No holiday is seeded. Dates vary by year, state and
company, and an invented one would mark a working day as non-working for
everyone on that calendar - in attendance and in payroll. HR publishes each
year's list.

**Comp-off is lots, not a ledger -- a deliberate departure from the plan.** The
data-model note had it as a signed ledger like leave. That is wrong for
comp-off: accrued leave is fungible, but each comp-off credit **expires on its
own date**, so spending "a day" has to record *which* credit it drew on or expiry
cannot be computed correctly. It is therefore `compoff_credits` (lots) plus
`compoff_allocations`, consumed **earliest-expiring first** so an employee never
loses a credit they could have used. A third signed representation of the same
facts could only drift from the lots.

**Proof, not a claim** -- a credit cites the `attendance_day` behind it: the day
type and the minutes the engine computed from punches. `unique(employee_id,
earned_date)` means a day earns credit **once**, however often attendance is
recomputed or the job re-runs. A trigger refuses any rewrite of the earned facts
while permitting the processing columns to change, and a check constraint makes
over-drawing a lot impossible. Work on an ordinary working day earns nothing
unless the company configures it to; otherwise every late evening would grant
leave.

**Credits are held at submission**, not at approval, so one about to expire is
reserved for the request in front of it rather than left for another. A
rejection or withdrawal releases them; a cancellation returns them **with their
original expiry dates**, because cancelling should not extend a credit's life.
Comp-off taken is its own attendance status, always payable - it compensates work
on a day off that attendance itself paid nothing for.

**Holiday calendars are scoped and unioned**, not global: a calendar may be
limited to a work location or an employee group, and the applicable ones add
together so a regional list supplements the national one. Publishing is separate
from editing because it marks those dates non-working for everyone in scope at
once; a draft affects nothing. Optional and restricted holidays apply **only** to
an employee who elected them, limited per year, so the holiday set is genuinely
per-employee.

They feed `CalendarSpec.holiday_dates` - the field the day-type resolver has
accepted since Milestone 3 - so attendance, leave and comp-off all see the same
holidays through one resolver, exactly as the data-model note promised, with no
change to the resolver itself.

**Leave-year closing balances.** The closing figure splits into carried,
encashed and lapsed and the three always sum back to it; a test asserts that for
every rule and every balance, because a reconciliation has to balance and a rule
that quietly dropped days would make entitlement unexplainable. A negative
balance is carried in full rather than written off - an employee who overdrew
still owes those days. Carry-forward posts **two** entries, out of the closing
year and into the next, so each year's arithmetic is self-contained. Carried days
with a limited life expire on their own schedule rather than at the next year
end, and only the unused portion lapses.

**Idempotent, as the brief requires.** Every closing entry is keyed by employee,
leave type and closing year. A re-run posts nothing and says `already_closed`
rather than the ambiguous "nothing to close" it would otherwise report once the
first run had zeroed the balance.

**Four scheduled jobs** -- `iams grant-compoff`, `iams expire-compoff`,
`iams close-leave-year` (dry run by default) and `iams expire-carried-leave`.

**Frontend** -- a holidays page showing what applies and what can still be
chosen, a comp-off page listing credits individually with their expiry dates, and
three new admin tabs: comp-off verification and approvals, holiday calendar
management with bulk import, and year-end closing with a preview and the
reconciliation table.

**Two bugs found and fixed:** the comp-off balance subtracted held days twice
(once from the lot, once as pending), showing a negative balance; and migration
0005 had hand-named four constraints, which put them in the schema-wide index
namespace and blocked `compoff_application_days` from using the obvious name -
0006 renames them to the convention.

**Test suite made usable.** The suite had grown to nine minutes because the
database fixtures were `autouse`: every pure-arithmetic unit test waited for a
full migration run, re-seeded the 80-permission catalogue and then truncated
fifty-one tables. Database setup is now opt-in, the catalogue is seeded once per
session, sign-in tokens are memoised per test, and `pytest-xdist` runs four
workers each with its own database. Unit tests went from 116s to **0.9s**, and
the whole suite from ~9 minutes to **3m30s**.

**Verified** -- 998 backend tests (141 new), 32 frontend tests, `alembic check`
clean, migration 0006 reversible and re-applied. Against the live stack, 42
checks passed: an empty calendar refused publication; a draft showing no
holidays; publishing making the date a `holiday` in attendance while an unelected
restricted day stayed `working`; a duplicate import doing nothing; a day's work
credited once and a re-run reporting `already_credited` with one row in the
database; the database refusing to rewrite earned facts or over-allocate a lot;
an unverified credit unavailable but reported as awaiting verification; a request
recording the exact credit it drew on; approval producing `compoff/1.00` in
attendance; cancellation returning the credit and releasing the allocation;
expiry idempotent; a future leave year refused closure with a reason; and a
year-end re-run closing nothing further.

## Milestone 7 -- Announcements and notifications -- COMPLETE
*Depends on: M2*

**One rule holds this milestone together: a notification is keyed by the fact,
never by the attempt.** `notifications.dedupe_key` is unique, and every caller
derives it from the thing being reported - the application and the stage that
decided it, the attendance date, the announcement and the user - never from a
run id or a clock reading. A retried scheduler run, a re-published announcement
or a re-processed attendance day therefore inserts nothing the second time, and
the database enforces that rather than each caller remembering to read before
writing, which two concurrent runs would defeat.

Where a fact legitimately recurs, the recurrence is in the key. A missing punch
on 3 March and one on 4 March are two facts. A manager's approval and HR's
approval of one application are two things the applicant should hear about -
"your manager approved it, HR is next" is what somebody planning a trip needs,
and silence until the end reads as nothing having happened. The one key that
carries a timestamp is a password change, because each change is a distinct fact
and suppressing the second would hide exactly the event somebody needs to see.

**Announcements and notifications are separate tables**, not one "message"
table. An announcement is a single company artefact with an audience, a schedule
and a read report; a notification is one line in one person's centre. Merging
them would mean either duplicating a notice's text per recipient or losing the
per-person read state.

**Visibility is a query, not a state a job sets.** A published announcement is
one whose `publish_at` has passed, so a missed scheduler run delays the
*notification* and never hides the announcement - the job only notifies about
what the clock has already made visible. Drafting is separate from publishing
because publishing reaches everybody at once. A published notice can still be
corrected, with the audit entry noting that people may have read the earlier
text, and withdrawal needs a reason and keeps the row: there is no delete,
because people have already read it.

**The audience is resolved on the server.** Targets are the union of all,
department, work location, employee group and named employee - an announcement
legitimately goes to two offices, or a department plus one person. One aimed
elsewhere is not in the feed response at all, and its attachment is 404 rather
than 403, so a guessed id cannot confirm the notice exists. The feed endpoint
takes no employee id, so there is nothing to tamper with.
`announcement_targets.target_id` cannot be a foreign key - it points at one of
four tables - so the service validates it against the right one on write, and a
check constraint stops "everyone" carrying a stray id.

**Reading is not acknowledging.** Opening a closure notice is not the same as
confirming you have understood it, so `acknowledged_at` is a separate column
that a critical announcement asks for explicitly. `read_at` is immutable by
trigger - when somebody first read a notice is a fact - and an acknowledgement
cannot be withdrawn. The read report answers "did everyone see it", naming who
has not.

**Attachments and images** reuse the upload path from Milestone 2: the declared
content type is checked against the file's magic bytes, the stored name is
server-generated so an uploaded name cannot influence the storage path, and
every download is served as an attachment with sniffing disabled and recorded in
the audit log. A PDF cannot be set as the banner image.

**Muting silences the notification, not the announcement.** A preference row
exists only where somebody has asked for silence, so the absence of a row means
"notify me" and the default needed no backfill. A muted employee is still in the
audience and still sees the notice in their feed.

**Hooks, not coupling.** Leave, comp-off, attendance, shifts, holidays and auth
each call one named function in `notifications.hooks` and know nothing about how
a notification is stored, keyed or muted. Every hook swallows its own failures:
an approved leave application that could not be announced is still approved, and
rolling the approval back would be far worse than a missing line in somebody's
centre. Approvals route to whoever can actually act - the employee's own manager
for a manager stage, the holders of the matching `approve_hr` permission
otherwise, read from the same grants the authorization layer enforces.

**Missing punches are a separate job, deliberately.** Recompute runs during the
working day, when a day with one punch is simply somebody still at work, so
`iams notify-unsettled-attendance` looks only at days that are over and never
includes today. Nothing is classified as absence - the brief forbids that, and
the wording matches it: the notification asks for a correction rather than
announcing a consequence.

**Delivery is in-app only.** `channel` admits email, sms and push so an optional
adapter can be added later without a migration, but nothing here needs a paid
service to work.

**Two scheduled jobs** -- `iams notify-unsettled-attendance` and
`iams run-announcement-schedule`, both safe to re-run.

**Frontend** -- a polled unread badge in the top bar with a drop-down of recent
items, a notification centre with category filters, dismissal and per-category
muting, an announcement feed with pinned notices first and acknowledgement for
critical ones, and an authoring page with targeting, scheduling, uploads,
withdrawal and the read report. Polling pauses while the tab is hidden; a
WebSocket would be a second transport to secure and keep alive for something
that is not time-critical.

**Payroll publication is the one listed notification not yet wired**, because
payroll does not exist until Milestone 8. The hook and its wording are written
and unit-tested - `hooks.payslip_published`, deliberately carrying no figures,
since a notification is not a secure channel for pay - and M8 calls it when a
payslip is released.

**Verified** -- 1059 backend tests (61 new) in 2m43s, 32 frontend tests,
`alembic check` clean, migration 0007 reversible and re-applied, frontend
typecheck, lint, format and build clean. Against the live stack, 75 checks
passed across two runs: the unique key and both immutability triggers present,
with the database refusing to rewrite a notification or back-date a read
receipt; a draft reaching nobody; publishing twice refused and writing no second
notification; a scheduled run publishing once and a re-run publishing nothing;
zero duplicate keys anywhere in the table after every run; muting leaving the
audience unchanged while that account went un-notified; withdrawal keeping the
row and refusing republication; a manager told a request was waiting while the
applicant was not told about their own; the manager stage and the final approval
arriving as two notifications with distinct keys; a settled application refusing
a further decision and writing nothing; cancellation reported separately; a
shift change announced with its timings; and both CLI jobs idempotent.

Two gaps the live run exposed and closed: an account with no employee record has
nowhere to record a read receipt, so the UI no longer offers it; and the
attachment endpoints were added after the first run showed the columns had no
way to be filled.

## Milestone 8 -- Payroll and statutory configuration -- COMPLETE
*Depends on: M3, M5, M6*

**The honest part first, and it is the whole design.** Not one statutory figure
in this system was provided by its authors. No rate, no ceiling, no slab, and no
salary component either. A rate entered without a verified source produces a
wrong deduction that *looks right* - the employee loses money and nobody
notices - so the schema is built so that such a figure cannot quietly become the
record of what was paid:

* A `statutory_rule_sets` row starts as a **draft**. It computes, so a company
  can see what its figures would do, but the run that used it cannot be
  finalized.
* **Verification** is a separate, permissioned act that records who checked the
  figures, when, and against which published document. A check constraint
  refuses the verified status without all three, and a rule set with no
  parameters cannot be verified at all - there would be nothing to have checked.
* Once verified, the parameters are **frozen by trigger**, against update *and*
  deletion. The verification says somebody checked *these* numbers; a change
  means a new effective-dated version. (This surfaced during verification when
  it blocked a cleanup script, which is the trigger working.)
* `payroll.block_finalize_on_unverified_rules` is on by default, and
  finalization is refused while any rule set the run used is a draft. That
  setting can be turned off, and the error says so plainly, because a company
  that insists is entitled to - but it will have said so deliberately.

**What makes a past payroll explainable.** A run records the id and version of
every rule set it used and the settings in force when it ran, so changing a rate
or the payable-days basis next month cannot retrospectively alter what was paid.
Each payslip line stores its **basis**: not "PF 1800" but "10% of 20000, the
configured ceiling (wage 25000)". A figure an employee cannot check is a figure
they cannot dispute.

**Payable days come from attendance, not from a second calculation.** The engine
sums `attendance_days.payable_day_fraction` - the exact decimal the attendance
engine already stored - so payroll and attendance cannot disagree about how much
of a month somebody worked. Proration on joining or exit falls out of the same
arithmetic rather than being a separate step, so the two cannot drift apart.

A day with **no** attendance record, or one the engine left as `incomplete` or
`missing_punch`, is **paid in full and flagged**. The brief forbids treating a
missing punch as absence, and docking pay on a gap in the records would be that
same mistake with money attached.

**The payable-days basis is confirmed, not assumed.** It is the denominator of
every loss-of-pay and proration amount in the system, so `require_confirmed`
gates the calculation: payroll refuses to run until somebody has chosen between
calendar days, working days and a fixed number. One unpaid day costs 1/30 on one
basis and 1/26 on another, and that is a company decision, not a default.

**Separation of duties is enforced, not documented.** `payroll.calculate`,
`payroll.review`, `payroll.approve` and `payroll.finalize` are four distinct
permissions, and the service additionally refuses to let the same person review
what they calculated or approve what they reviewed. One pair of eyes over a whole
company's pay is not a review. A reviewed run cannot be recalculated either: the
numbers moving under a reviewer would make the review meaningless, so it must be
sent back explicitly, with a reason.

**A finalized run is immutable**, enforced by database trigger on both the run
and its payslips - it is the record of money that moved, and of what the bank
transfer and the statutory filings were made from. A correction is a
`payroll_adjustments` row applied in a later run, with the period the money
belongs to recorded separately from the run that pays it, which is what makes an
arrear explainable as "March's shortfall, paid in May". The amount is always
positive and the type decides the direction, so "a recovery of -500" - which
pays somebody by accident - cannot be expressed.

**Reconciliation re-sums everything from the payslip lines** rather than reading
the stored totals, and compares. A payroll that cannot prove it adds up should
not be paid. Employer contributions are reported but never subtracted from net
pay: that conflation is the classic payslip error that makes an employee think
they were underpaid.

**Gratuity is not invented.** With no verified rule set the line is zero and the
reason is recorded, and a settlement whose gratuity rests on an unverified set
cannot be approved. An invented formula would understate or overstate a
statutory payment somebody is legally owed, and would look equally plausible
either way.

**Decimal throughout.** Every amount is `NUMERIC(14, 2)` and every day count
`NUMERIC(6, 2)`; a verification check confirms no payroll table has a
floating-point column. Rounding happens once per computed figure, in the mode
the company configured, and a payslip's gross is the **sum of its own rounded
lines** - a gross that did not equal its lines would be indefensible to the
person reading it.

**Overtime is measured but not paid.** The payslip carries the overtime minutes
the attendance engine computed, but no amount is derived from them: the
multiplier is a legal rate rather than a company preference, so it belongs in a
verified rule set. A company paying overtime today posts it as an adjustment,
which keeps the figure explicit and attributable. Recorded in `open-items.md`.

**TDS is a projection, and says so.** The engine does banded arithmetic on an
annualised income from whatever slabs the rule set holds. It has no exemptions,
deductions, rebates, surcharge, cess, regime choice or previous-employer income,
so a company enabling it is choosing a projection. That is recorded in
`open-items.md` rather than implied by the feature's existence.

**Milestone 7's payroll hook is now wired**: releasing a finalized run's
payslips notifies each employee once, keyed per payslip, and the notification
deliberately carries no figures, since it is not a secure channel for pay.

**A project-wide bug found and fixed here.** SQLAlchemy stores Python `None` in
a JSONB column as the JSON value `null`, which is *not* SQL NULL - so
`IS NOT NULL` reads true for a column nobody set. It broke a new check
constraint outright, and would have made "payslips with review flags" count
every payslip. All nine modules with JSONB columns now use one shared
`Jsonb` type with `none_as_null=True`; the dev database already held 274 such
rows, which is how long the trap had been sitting there unnoticed.

**Frontend** -- an employee payslip page showing every line with its basis and
keeping employer contributions visibly separate from deductions, and a four-tab
payroll workspace: the run lifecycle with its review flags shown rather than
hidden, statutory rule sets with their sources and the verification gate,
components with their wage-base flags as columns, and adjustments.

**Verified** -- 1166 backend tests (107 new: 50 on the pure engine, 57 through
the HTTP stack), 32 frontend tests, `alembic check` clean, migration 0008
reversible and re-applied, frontend typecheck, lint, format and build clean.
Against the live stack, **60 checks passed**: the schema refusing a verified
rule set with no source; one rule set per domain and date with NULL states
treated as equal; no floating-point column anywhere in payroll; a percentage of
a derived component refused; components that do not foot refused; an empty rule
set unverifiable; an exclusion without a reason refused; calculation refused
until the payable-days basis was confirmed; a statutory line citing its rule set
and reading "10% of 20000, the configured ceiling (wage 25000)"; the employer
share reported without reducing net pay; the run adding up to its own lines;
review refused to the account that calculated it; recalculation refused on a
reviewed run; finalization refused on unverified rules and allowed once
verified; the database refusing to reopen a finalized run or rewrite a finalized
payslip; a verified rule set's parameters frozen against both update and delete;
releasing payslips twice publishing nothing further; an identical adjustment
refused; a negative amount refused; and a finalized run's figures unmoved after
a later rate was entered, still naming the version it used.

Two things the live run could not exercise, both covered by the integration
tests instead: the payslip notification itself, because the dev employee with a
salary has no linked account to notify, and the full settlement computation,
because that employee has not left.

## Milestone 9 -- Reports, exports, hardening -- COMPLETE
*Depends on: all*

**Reports read; they never compute.** Every figure comes from the table a module
already wrote: payable days from the fractions the attendance engine stored,
balances summed from the leave ledger, totals from payslips. A report that
recomputed anything would be a second implementation of that rule, and the two
would eventually disagree - at which point nobody could say which was right. A
live check compares the payroll report's net total with the payroll module's
own: both read the same rows, so they agree by construction rather than by
coincidence.

**Ten reports**, all from one dispatcher: attendance summary, daily register,
unsettled attendance, overtime, leave balances, comp-off position, employee
register, payroll register, deductions and contributions, and a payslip index.
The catalogue, the columns and the accepted filters are all served by the
backend, so the interface keeps no second copy of which reports exist, and a
report cannot disagree with itself about its own columns.

**A report is never a wider view than the module it reads.** Holding
`report.attendance` says you may run attendance reports; it does not say whose.
Each report is narrowed to the employees the caller can already see - a
manager's covers their team, HR's covers everybody - and the catalogue says
which, so somebody comparing two exports can see why the totals differ. An
empty scope list means "nobody", never "no filter"; getting that the wrong way
round is how a scoped report leaks a whole company, so it is a named case in the
code and a test.

**Export is a separate permission, and every export is audited.** Reading
figures on screen and walking out with a spreadsheet of them are different acts:
the file leaves the system's access controls behind, and the audit entry - who,
what, which filters - is the only thing that makes it traceable afterwards.
CSV is written with a UTF-8 BOM, because Excel reads a BOM-less UTF-8 file as
the system code page and mangles exactly the non-ASCII names an employee
register is full of. Excel export is a real `.xlsx` through `openpyxl`, not a
CSV with a misleading extension, with numbers written as numbers so a
spreadsheet can total them.

**The employee register deliberately omits bank details, PAN and Aadhaar.**
They are encrypted at rest and sit behind their own permissions; an exportable
spreadsheet of them would be the easiest thing in the system to produce and the
easiest to leak.

**Reading the audit log is itself audited.** The trail records who changed
permissions and whose bank details were opened, so searching it is a privileged
act an organisation may later need to account for. The entry records the
**filters, not the results** - enough to reconstruct what was looked for without
copying the data into a second place. Actions match as a prefix, because that is
how somebody actually searches a trail: by the area under investigation, not by
remembering an exact action name. The action catalogue is read from the data, so
an action a new module records appears without anybody maintaining a list.

**Retention is a decision, not a default.** Three periods exist as settings and
all three start **unconfirmed**; nothing is purged automatically and there is no
scheduled job. The report counts rows beyond each period without deleting
anything, and `iams purge-expired-data` is a dry run unless given `--apply`,
naming any unconfirmed period as it goes - a retention period destroys evidence
as well as data, and an audit entry nobody can produce later is
indistinguishable from a change nobody made. Purging the audit log needs the
**owner** database role, because `DELETE` is revoked from the application role
on purpose: purging the trail should not be something the application can do.

**The restore is tested, not described.** `infra/backup/backup.py verify` takes
a fresh dump, restores it into a scratch database beside the original, and
compares the schema version and the row count of every table. Run against the
development database: **71 tables, 926 rows, schema version 0008, every count
matching**. A backup procedure that has never been run is a guess; a restore
procedure that has never been tested is worse than none, because it produces
confidence without capability. The script deliberately does no encryption, no
upload and no scheduling, and says so when it finishes: a payroll dump is the
most sensitive file this system produces, and where it lives is the operator's
decision.

**Security review** is written up in `docs/security.md`, after the features it
describes were built and tested. It records the controls, the verification
results, and ten known gaps - no independent review, no per-IP rate limiting, no
two-factor authentication, environmental key handling among them - because a
security document that lists only strengths is marketing.

**Frontend code-splitting.** Every authenticated page is now loaded on demand,
with one `Suspense` boundary around the outlet so a late page shows the shell
and a loading panel rather than a blank screen. The entry bundle went from
**915 kB to 494 kB**, with each page in its own chunk; the largest is 54 kB. The
shell, the guards and the two pages an unauthenticated visitor needs stay in the
entry bundle - splitting the sign-in page would add a round trip before anybody
could log in.

**Verified** -- 1193 backend tests (27 new), 32 frontend tests, `alembic check`
clean, frontend typecheck, lint, format and build clean. Against the live stack,
**53 checks passed**: all ten reports running against real data; the payroll
report agreeing with the payroll module; the employee register free of bank and
identity columns; the overtime report deriving no amount; a CSV with its BOM and
an `.xlsx` with a real workbook content type, both served as attachments with
sniffing disabled and both audited with their filters; an unsupported format
refused; the audit log searchable by prefix and paginated, with the search
itself recorded and the database refusing to rewrite an entry; retention
reporting without deleting and naming its unconfirmed periods; the purge
defaulting to a dry run; 401 on reports, exports and the audit log without
authentication; and the backup restoring into a scratch database with every row
intact.

One thing the live run could not exercise: the team-narrowing of a scoped
report, because no dev account holds a report permission without a read-all
grant. It is covered by an integration test that builds exactly that account.

## Milestone 10 -- Verification, documentation, hosting evaluation -- COMPLETE
*Depends on: all*

**End-to-end tests.** One employee walked through every module in order: an
organisation, an employee, an account, a shift and a calendar, a published
holiday, two real punches through the engine, a month of attendance, leave
refused to self-service and recorded by HR instead, a comp-off credit earned
once, salary components and a revision, a statutory rule set, the whole payroll
lifecycle including the verification gate, a released payslip, and finally the
reports and the audit trail that describe the lot.

It is deliberately one long test. The order is the point - each step depends on
the state the previous one left - and splitting it would mean either
re-establishing that state repeatedly or quietly testing something else. Three
more tests cover the properties that only make sense across modules: that an
employee is confined to their own records everywhere, that a fresh installation
invents nothing, and that payroll refuses to run on an unconfirmed
payable-days basis.

**The end-to-end test found a real inconsistency, which is what it is for.**
An employee applying for leave 190 days back is refused with "ask HR to record
it" - but HR's own on-behalf endpoint applied the same window, so the refusal
was a false promise. The window exists to stop an *employee* retro-fitting
leave; HR recording it is a different act, already flagged on the application
and audited. The on-behalf path is now exempt, narrowly: every other rule still
applies, including the balance.

**Documentation.** Three new documents, each written for a different reader:

* [operations.md](operations.md) - the operator runbook. First-run order, the
  nine scheduled jobs and the three deliberately *not* scheduled, backups,
  routine tasks, a symptom-to-cause table, upgrades, and a plain list of what
  the system does not do.
* [user-guide.md](user-guide.md) - for employees, managers and HR. What each
  role sees and why, in the terms the people using it would use.
* [hosting.md](hosting.md) - the free-hosting evaluation.

**Free hosting, evaluated honestly.** Researched on 16 October 2026 from
secondary sources, with every figure marked as needing confirmation against the
provider's own terms - free tiers move, and three of them moved in the last two
years.

The frontend is a good fit for a free tier: it serves static files and holds
nothing. Cloudflare Pages or Netlify; **Vercel's Hobby plan rules itself out**,
because its terms exclude commercial use and a company's own HRMS is commercial
use.

The backend is not, for three specific reasons rather than a general unease. A
free instance that sleeps makes the first punch of the day wait about a minute,
and attendance is the one feature where latency is a product problem. The nine
scheduled jobs need something awake. And uploaded documents need a disk that
survives a restart - ephemeral storage loses them while the database still
references them, which is the worst shape of failure because the records look
intact.

The database is where a free tier becomes indefensible: **Render deletes a free
Postgres 44 days after creation**, which is incompatible with statutory
retention of employment records measured in years, and the alternatives pause
or suspend on idle.

On the brief's explicit question - whether the terms permit confidential payroll
data - the honest answer is recorded as such: **the research does not answer
it.** Data-processing terms, sub-processor lists, residency and breach
commitments live in documents no comparison article covers, and this project has
not been given a legal position on where Indian employees' salary and bank data
may sit. `hosting.md` ends with a ten-point checklist for whoever decides,
rather than a recommendation dressed up as a finding.

The recommendation: free tiers for a demonstration with invented data; a small
paid instance with a persistent disk and managed Postgres for real payroll. The
difference is a few pounds a month and it removes every objection above.

**Verified** -- 1197 backend tests (4 new, end to end), 32 frontend tests,
`alembic check` clean, all eight migrations round-tripped from base and back,
frontend typecheck, lint, format and build clean, the backup restore verified by
round trip, and 18 live checks over a freshly seeded stack: each role signing
in, an employee refused the settings, the audit log, the reports and payroll,
payroll reporting itself not ready with its blockers named, 42 of 60 settings
still awaiting confirmation, leave types and holidays and salary components and
statutory rules all starting empty, and two punches alternating direction.

**One mistake worth recording.** The migration round-trip was run against the
**development** database rather than a scratch one, which emptied it - and
`backup.py verify` had already deleted its own dump on success, so there was
nothing to restore from. The data was invented development fixtures, and the
stack was re-seeded through the public API rather than by writing rows, so what
it now holds has passed the same validation an operator's input would. The
lesson is in `operations.md`: prove a downgrade on a database nobody needs.

## Interface overhaul -- COMPLETE

Not a planned milestone. Raised after the ten were finished: the application
worked and read as an administrative tool nobody had designed.

**Two real defects found while looking.**

- `rounded-[--radius-card]` compiled to `border-radius:--radius-card`, which is
  invalid CSS. Tailwind v4 dropped the v3 shorthand for a bare custom property.
  Every card, panel, stat tile, alert and skeleton in the application had been
  rendering with square corners. Twelve occurrences, now `rounded-[var(...)]`.
- `dark:` was keyed only to `prefers-color-scheme`. Choosing dark mode in the
  interface redefined the surface variables but left every `dark:` utility in
  light mode, so badges, stat tiles and alerts stayed pale on a dark page.
  Fixed with a `@custom-variant dark` that matches `data-mode` as well.

**Theming.** Nothing in the application names a colour any more. The brand,
accent and cool ramps are generated from five control variables and surfaces
take their tint from the brand hue, so a colour is five numbers and switching
one attribute on `<html>` restyles everything with no rebuild and no re-render.
Status colours are deliberately exempt: red must mean red in every theme.

The first attempt put six settings -- colour, mode, corner radius, panel
finish, background wash -- behind a panel in the top bar. It was rejected as
too complex, and rightly: they were five more decisions for somebody who only
wanted a different blue, and a panel is a place to go rather than a thing to
click. It is now two rows of the account menu, Display mode and Theme colour,
each opening a short list with a tick against the current choice. Eight
colours, applied on click.

**Controls.** Fields moved to a `.field-control` class -- a sunken surface that
lifts and lights up on focus with a coloured halo an outline cannot express.
Selects, checkboxes and switches are drawn rather than inherited from the
operating system, while keeping the real input underneath for keyboard and
assistive technology. The remaining fifteen `<input type="date">` were migrated
to the existing custom picker, which gained month and year jump controls and a
single-control layout; `ControlledDate` bridges it to react-hook-form.

**New.** An employee directory with three views (cards, table, organisation
chart), an interactive reporting chart built client-side, a command palette on
Ctrl+K, a collapsible sidebar, and a dashboard rebuilt on new stat primitives.
There are no sparklines: the API returns today's figures, not a series, and a
trend line drawn from a single number would be an invention.

**One mojibake sweep.** Ten source files carried double-encoded characters from
earlier shell heredocs: a middle dot on the sign-in page showing as two
characters, and em dashes showing as three through the attendance and shifts
pages. Repaired, and the whole tree scanned to confirm nothing else remains.

**Verified** -- 56 frontend tests (24 new: appearance validation and DOM
attribute behaviour, and the organisation tree's handling of several roots, a
two-person cycle, a longer cycle, self-management and a manager outside the
visible set), typecheck clean, lint clean (0 errors, 3 pre-existing fast-refresh
warnings), prettier clean, production build clean at 98 kB of CSS and 526 kB of
JS, and the compiled stylesheet checked directly to confirm zero invalid
`border-radius` declarations, all eight palettes present, and the `dark:`
variant bound to `data-mode`.

**Not verified.** How it looks. Every check above is mechanical; nobody has
confirmed in a browser that the eight palettes are pleasant, that the contrast
holds in each one, or that the chart is legible on a real hierarchy. That needs
eyes on a screen.

## Reproducible setup, and a bug it uncovered -- COMPLETE

Not a planned milestone. Asked for so that a push to GitHub and a pull
elsewhere installs everything with one command.

**Delivered.** `backend/requirements.txt` (47 runtime packages) and
`backend/requirements-dev.txt` (the same plus 12 for tests and lint), both
pinned to exact versions and generated by `backend/scripts/generate_requirements.py`
rather than typed; `scripts/setup.ps1` and `scripts/setup.sh`, which build the
virtual environment, install both halves and write `backend/.env` with freshly
generated keys. The split between runtime and development is computed by
walking installed metadata, carrying extras along the walk -- the first attempt
dropped them and silently lost five packages, so the file would have installed
a server that cannot reload and a database driver with no binary behind it.

`pyproject.toml` keeps the acceptable ranges and stays the place to add a
dependency. The requirement files record one combination known to work.

**Verified by doing it.** A copy of the tree with no `.venv`, no `node_modules`
and no `.env`: setup ran, 59 packages installed, the app imported, the `iams`
command worked, the generated `.env` had no byte-order mark and its field
cipher round-tripped, a second run refused to replace the encryption key, and
`npm ci` followed by a build produced byte-identical assets to this checkout.

**The bug.** Running the backend suite afterwards failed three timezone tests.
Not flakes, and not caused by the packaging work: `get_engine` configured the
session timezone and `statement_timeout` with `SET` statements in a pool
`connect` handler. psycopg opens an implicit transaction on a connection's
first statement, so both ran inside one and SQLAlchemy discarded them when the
connection returned to the pool and was rolled back.

Two consequences, one latent since milestone 1:

- `statement_timeout` had never applied. `SHOW statement_timeout` returned `0`
  where the configuration said 30 seconds, so a runaway report query had
  nothing to stop it.
- Timestamps came back in whatever timezone PostgreSQL was installed with. The
  Docker database was UTC and the local PostgreSQL 18 is Asia/Kolkata, which is
  why moving off containers exposed it. The stored instants were always
  correct; the tzinfo was not, and anything reading `.hour`, calling `.date()`
  or formatting without converting would answer differently on a laptop than in
  production.

Both are now libpq startup options through `connect_args`, which are connection
properties rather than transactional statements. The regression test asserts
them **after an explicit rollback**, because the broken arrangement passed every
check that did not roll back first -- which is precisely how it survived ten
milestones of review.

**Verified** -- 1198 backend tests pass, including the new guard; ruff check and
format clean.
