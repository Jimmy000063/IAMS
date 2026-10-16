# Ishvara Attendance Management System

Human Resource Management System for **Ishvara Tech Consulting LLP** - employee
management, attendance, shifts, leave, comp-off, holidays, Indian payroll,
announcements, notifications and reporting.

All code, branding, business logic and interface design is original to this
project.

## Status

**All ten milestones complete.** Working end to end: configuration, identity
and access control, auditing, the organisation structure, the full employee
lifecycle with effective-dated history, encrypted statutory and bank details,
validated CSV import, the attendance engine - web punching, configurable shifts
including overnight, work calendars, and the two-stage regularization chain -
the device integration framework with generic adapters, a
signature-authenticated webhook and a reconciliation queue - and leave
management: effective-dated policies, accrual with proration, two-stage
approval, and an append-only balance ledger - plus compensatory off, holiday
calendars, leave-year closing, targeted announcements, an in-app notification
centre, payroll - salary structures, versioned statutory configuration that
must be verified against a cited source, the monthly run from draft to released
payslips, registers and reconciliation - and ten reports with CSV and Excel
export, an audit log viewer, retention controls and a tested backup restore.

No specific biometric product is claimed as integrated, no statutory leave
entitlement has been entered, and **no statutory rate, ceiling or slab in this
system was provided by its authors**. See
[Device integration](#device-integration), [Leave management](#leave-management)
and [Payroll](#payroll).

See [docs/milestones.md](docs/milestones.md) for what each milestone delivered,
and [docs/open-items.md](docs/open-items.md) for the decisions this system still
needs.

| Document | For |
|---|---|
| [docs/operations.md](docs/operations.md) | Whoever runs it: first-run order, scheduled jobs, backups, troubleshooting |
| [docs/user-guide.md](docs/user-guide.md) | Employees, managers and HR |
| [docs/security.md](docs/security.md) | The security review, with ten gaps stated plainly |
| [docs/hosting.md](docs/hosting.md) | Where to run it, and why not on a free tier |
| [docs/architecture.md](docs/architecture.md) | How the code is arranged and why |
| [docs/data-model.md](docs/data-model.md) | Every table and the guarantee it carries |
| [docs/setup.md](docs/setup.md) | A development machine |

| | |
|---|---|
| Backend tests | 1197 passing (4 workers) |
| Frontend tests | 32 passing |
| API endpoints | 230 operations across 197 paths, all documented |
| Database tables | 70 application tables |
| Frontend bundle | 494 kB entry, pages loaded on demand |
| Backup restore | verified by round trip: 71 tables, every row count matching |
| Lint / typecheck | clean (ruff, eslint, tsc strict) |
| Migrations | `alembic check` clean; reversible to base and back |

## Stack

**Frontend** React 19 - Vite 8 - TypeScript 5.9 (strict) - Tailwind CSS 4 -
React Router 7 - TanStack Query 5 - React Hook Form - Zod

**Backend** Python 3.12+ - FastAPI - Pydantic 2 - SQLAlchemy 2.1 - Alembic -
psycopg 3 - Argon2id - Pytest

**Database** PostgreSQL 17

## Quick start

Requires PostgreSQL 15 or later installed locally (17 or 18 recommended),
Python 3.12+ and Node 20+.

### 1. Install the dependencies

One command, from the root of the checkout:

```powershell
.\scripts\setup.ps1
```

```bash
./scripts/setup.sh          # macOS and Linux
```

It builds the backend virtual environment, installs the pinned Python and Node
dependencies, and writes `backend\.env` with freshly generated keys. It is safe
to run again: an existing `.env` is never overwritten, because replacing
`IAMS_FIELD_ENCRYPTION_KEY` would make the bank details and PANs already
encrypted in the database unreadable.

If `python` on your PATH is an Anaconda base environment or an older version,
name the interpreter you want:

```powershell
.\scripts\setup.ps1 -Python "C:\Program Files\Python314\python.exe"
```

```bash
PYTHON=/usr/bin/python3.12 ./scripts/setup.sh
```

<details>
<summary>Or do it by hand</summary>

```powershell
cd backend
& "C:\Program Files\Python314\python.exe" -m venv .venv    # adjust the version
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m app.cli generate-encryption-key   # paste into backend\.env

cd ..\frontend
npm ci
```

`requirements-dev.txt` pins exact versions, recorded from a working
environment. `pip install -e ".[dev]"` reads the ranges in `pyproject.toml`
instead, which is what you want when deliberately picking up newer releases.

</details>

### 2. Set up the database

```powershell
# Roles, the two databases, and their grants. Prompts for your postgres
# superuser password three times.
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -U postgres -h localhost -p 5432 -f infra\postgres\local-setup.sql
& $psql -U postgres -h localhost -p 5432 -d iams      -f infra\postgres\local-grants.sql
& $psql -U postgres -h localhost -p 5432 -d iams_test -f infra\postgres\local-grants.sql
```

### 3. Migrate, seed and run

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
alembic upgrade head
python -m app.cli bootstrap-admin --username admin --email hr@ishvaratech.com
uvicorn app.main:app --reload --port 8001

# New terminal
cd frontend
npm run dev
```

Open <http://localhost:5173> and sign in with the temporary password that
`bootstrap-admin` printed. You will be required to change it immediately.

API documentation: <http://localhost:8001/api/docs>

Full instructions, test commands and troubleshooting:
[docs/setup.md](docs/setup.md).

## Repository layout

```
backend/                  FastAPI application
  app/
    core/                 config, security, crypto, errors, permissions,
                          deps, logging, time, pagination
    db/                   declarative base, session, model registry
    modules/              one package per domain, each with models,
                          schemas, policy, service, router:
                            auth        identity, sessions, RBAC
                            org         departments, designations, locations
                            employees   lifecycle, documents, CSV import
                            shifts      shifts, calendars, assignments
                            attendance  punching, the engine, corrections
                            devices     adapters, ingestion, reconciliation
                            leave       types, policies, accrual, ledger
                            compoff     credits, allocations, expiry
                            holidays    calendars, optional elections
                            notifications
                                        announcements, the notification
                                        centre, and the hooks other
                                        modules call
                            payroll     components, salary revisions,
                                        statutory rule sets, runs,
                                        payslips, settlements
                            reports     ten reports, CSV and Excel export
                            audit       append-only trail, the viewer,
                                        job history, retention
                            dashboard   aggregates
                            settings    business policy configuration
                            audit       append-only trail, job runs
    cli/                  administrative commands
  alembic/                migrations - the only way the schema changes
  tests/                  unit (pure rules) + integration (API and database)

frontend/                 Vite single-page application
  src/
    app/                  router, guards, shell, navigation
    lib/                  API client, auth context, toasts, date maths
    components/ui/        accessible primitives over the design tokens
    features/             one folder per domain

infra/postgres/           local database setup, least-privilege roles
infra/backup/             dump, restore, and a restore that verifies itself
docs/                     architecture, data model, setup, devices, security,
                          operations, user guide, hosting, milestones,
                          open items
docker-compose.yml        optional PostgreSQL in containers, for anyone who
                          would rather not install it locally
```

## Device integration

The framework is built; no particular machine is claimed to work with it.

**What is implemented and tested.** Three ways for punches to arrive, each
covered by automated tests against its own contract:

| Adapter | Direction | Maturity | For |
|---|---|---|---|
| `csv_upload` | You upload | **tested** | Any machine that exports a punch log. Needs no network access to the device. |
| `webhook` | Device pushes | **tested** | A device or middleware that can POST to a URL. |
| `rest_poll` | We poll | *generic* | A device exposing a JSON endpoint. Transport tested against a stub; never run against hardware. |
| `simulator` | We poll | *development only* | Exercising the pipeline before hardware arrives. Refuses to run outside a local or test environment. |

Setting one up, and what to do when events arrive that cannot be used:
[docs/devices.md](docs/devices.md).

**What is not implemented.** No adapter is written against any specific
biometric or access-control product. Vendor protocols differ in transport,
authentication, time handling and event semantics, and an adapter built from a
datasheet alone cannot be trusted with attendance data. Adding one needs the
protocol documentation *and* a unit to test against. The adapter catalogue
endpoint says all of this to the operator's face rather than leaving it to be
discovered after configuration.

**How it behaves.** Every adapter feeds one ingestion path, so a new adapter
cannot get the hard parts wrong in a novel way:

- **Nothing a device sends is discarded.** Every payload is stored, including
  ones that could not be interpreted. That is what makes a wrong field mapping
  fixable afterwards - correct it and reprocess, without the device re-sending.
- **Re-delivery is harmless.** Events are deduplicated on
  `(device, event id)`, and an id is derived from the content for devices that
  number nothing. A device re-sending its buffer after an outage is the normal
  case, not an edge case.
- **Timestamp formats are declared, never guessed.** `03/04/2026` is a different
  day under a day-first and a month-first reading, and the wrong choice stays
  invisible until a month-end report.
- **Naive timestamps are anchored to the device's declared zone**, never the
  server's. A punch is then attributed to a shift date by the same rule a web
  punch uses, so an overnight shift lands on the date it started.
- **A punch from the future is refused.** Device clocks drift and some reset
  after a power cut; crediting unworked time is worse than queuing the event.
- **Mappings are dated.** Enrolment slots get reused, so a punch from last March
  resolves to whoever held that slot in March. A second open mapping for one
  identifier is refused outright.
- **The webhook is authenticated by HMAC-SHA256 over the raw request body**,
  verified before the body is parsed. It is the only unauthenticated write path
  in the application, and a test asserts that remains true. A device with no
  secret configured is refused rather than accepted unauthenticated.
- **Outbound polling is validated against request forgery.** Cloud metadata and
  link-local addresses are always refused; a LAN address needs an explicit
  opt-in.
- **A device going silent is surfaced.** That failure raises no error of its own
  - it just leaves a growing hole in attendance. The health screen names quiet
  devices while the gap is still small.
- **A failed run is recorded rather than raised away.** The sync endpoint
  returns 200 with `status: "failed"` and the reason, because raising would roll
  back the very record saying it failed.

## Leave management

**No leave types are seeded.** The statutory ones have legally mandated
entitlements this project has not been given verified references for, and
creating them with invented quotas would be worse than leaving the list empty.
A company defines the types it uses; any whose entitlement comes from statute is
marked `requires_legal_review`, which is shown to employees and in the
configuration health report rather than being quietly trusted.

**A balance is a `SUM` over an append-only ledger, never a stored column.** A
mutable balance field cannot be reconciled or explained, and is the usual source
of disputes - an employee asks why they have 11.5 days and nobody can
reconstruct the arithmetic. Every change is a row with a type, a reason, an
actor and a link to what caused it, and a trigger refuses any `UPDATE` or
`DELETE`. A mistake is corrected by posting a reversal, so the error and the fix
both stay on the record.

**Four balance figures, not one**, because they answer different questions and
conflating them causes real errors:

| Figure | Meaning |
|---|---|
| `balance_days` | What the ledger sums to as of a date. Answers "what was my balance on 31 March". |
| `pending_days` | Held by requests nobody has decided yet. |
| `committed_days` | Approved, but dated later - leave booked for next month. |
| `available_days` | balance minus pending minus committed. The only figure a new request is checked against. |

A deduction is dated on the **leave's own start date**, not the approval date,
because that is what attributes it to the right leave year for carry-forward.
Leave approved today for next month therefore does not move today's ledger
total - and without counting it separately an employee could book the same
entitlement twice.

**Policies are effective-dated and superseded, not edited.** Each application
pins the policy version that governed it and stores a snapshot of the settings
in force, so raising a quota in July cannot change how a March application was
decided, and "why was this allowed?" stays answerable.

**Accrual is idempotent twice over.** Each credit is keyed
`accrual:<employee>:<type>:<period>` and the unique constraint rejects a
duplicate, so a retried run reports what was already posted rather than
crediting a balance twice - the most damaging mistake in leave management and
the hardest to notice afterwards. Periods are anchored to the **leave year**,
not the calendar: an April leave year has quarters running April to June.
Rounding is applied once per period, never to a running total, because twelve
monthly accruals of 1.25 rounded each time to 1.5 would give 18 days against a
15-day quota.

**Approval is manager then HR, and three rules are enforced on the server:**
nobody decides their own application whatever permissions they hold; a manager
stage requires the actual reporting relationship, not just the permission; and
stages run in order, so HR cannot approve something still awaiting its manager.
Only the final stage deducts, so a request a manager approved and HR refused
never touches the balance. A missing manager routes the request straight to HR
and records the skip as a stage decision, so a history with no manager row
explains itself instead of looking like a bypass.

**Day counting follows the shared calendar resolver**, the same one attendance
uses, so the two cannot disagree about which days were working days. A
non-working day inside a range is not consumed by default; the sandwich rule
charges only the ones *between* two leave days, and a leave type whose
entitlement is defined in calendar days charges all of them. Each rule has a
direct payroll consequence, so none is assumed.

**A request beyond the balance is split, not refused.** Someone needing five
days with three in hand takes all five, with two recorded as unpaid - forcing a
choice between a refusal and an unrecorded absence helps nobody. Payroll gets
the per-day split rather than having to recompute it.

Approving or cancelling leave **recomputes the affected attendance days**, so a
payslip is never built from attendance that still says the employee was absent.
Paid leave is a payable day; unpaid leave pays nothing; a half day pays its half
plus the worked half if the employee met the half-day threshold.

## Comp-off and holidays

**Comp-off is lots, not a balance.** Leave uses a ledger because accrued leave
is fungible - a day is a day. A comp-off credit is not: each one is earned on a
specific date and **expires on its own date**, so spending "a day" has to record
*which* credit it drew on, or expiry cannot be computed. Credits are therefore
modelled as lots with allocations, and spent **earliest-expiring first** so an
employee never loses one they could have used.

A credit cites the `attendance_day` that proves it - the day type and the
minutes actually worked, derived from punches. The brief requires comp-off to
come from verified qualifying work, and that row is the verification; a credit
with no attendance behind it is only possible as an explicit manual grant, which
is flagged as such.

`unique(employee_id, earned_date)` is the guarantee that matters: a day worked
earns credit **once**, however many times its attendance is recomputed or the
credit job is re-run. Credits are held at submission rather than approval, so one
about to expire is reserved for the request in front of it; a rejection or
withdrawal releases them. Work on an ordinary working day does **not** earn
comp-off unless configured to - otherwise every late evening would grant leave.

**Holiday calendars are scoped and unioned.** India has national, state and
company holidays, and an employee in Bengaluru does not get Kolkata's list. A
calendar may be scoped to a work location or an employee group, and the
applicable ones add together rather than replacing each other.

Publishing is deliberately separate from editing, because it marks those dates
non-working for everyone in scope at once; a draft affects nothing. Optional and
restricted holidays are **only** non-working for an employee who elected them,
which makes the holiday set genuinely per-employee - and they feed
`CalendarSpec.holiday_dates`, the field the day-type resolver has accepted since
Milestone 3, so attendance, leave and comp-off all see the same holidays through
one resolver with no second implementation.

**Leave-year closing balances.** The closing figure is split into carried,
encashed and lapsed, and the three always sum back to it - a reconciliation has
to balance, and any rule that quietly dropped days would make it impossible to
explain where entitlement went. A negative balance is carried in full rather
than written off, because an employee who overdrew still owes those days.

Carry-forward posts **two** entries - out of the closing year and into the next -
so each leave year's arithmetic is self-contained. Every entry is keyed by
employee, leave type and closing year, so re-running the job posts nothing, which
is what the brief requires of it. Carried days with a limited life expire on
their own schedule, not at the next year end.

## Payroll

**Not one statutory figure in this system was provided by its authors.** No
rate, no ceiling, no slab - and no salary component either, because "basic" and
"HRA" look universal but their proportions are a company decision with tax
consequences. A rate entered without a verified source produces a wrong
deduction that *looks right*: the employee loses money and nobody notices. So
the system is built to make that impossible to do quietly.

A statutory rule set starts as a **draft**. It computes, so a company can see
what its figures would do, but **verification** is a separate permissioned act
that records who checked them, when, and against which published document - and
a payroll run that used an unverified set cannot be finalized. Once verified,
the figures are frozen by database trigger: a change means a new effective-dated
version, because the verification is a statement that somebody checked *those*
numbers.

**A past payroll stays explainable.** Each run records the rule-set versions and
the settings it used, so a rate change next month cannot retrospectively alter
what was paid. Each payslip line stores its basis - not "PF 1800" but "10% of
20000, the configured ceiling (wage 25000)". A figure an employee cannot check
is a figure they cannot dispute.

**Payable days come from attendance**, summed from the exact decimal the
attendance engine already stored, so the two cannot disagree. A day with no
record, or one the engine left as a missing punch, is **paid in full and
flagged** - the brief forbids treating a missing punch as absence, and docking
pay on a gap in the records is that same mistake with money attached.

**The payable-days basis must be confirmed before payroll will run at all.** It
is the denominator of every loss-of-pay and proration amount: one unpaid day
costs 1/30 of a month on one basis and 1/26 on another. That is a company
decision, so the system refuses to guess.

**Separation of duties is enforced, not documented.** Calculating, reviewing,
approving and finalizing are four distinct permissions, and the same person may
not review what they calculated or approve what they reviewed. A reviewed run
cannot be recalculated - the numbers moving under a reviewer would make the
review meaningless - so it must be sent back explicitly, with a reason.

**A finalized run is immutable**, enforced by trigger on the run and its
payslips. It is the record of money that moved. A correction is an adjustment
applied in a later run, with the period the money belongs to recorded separately
from the run that pays it - which is what makes an arrear explainable as
"March's shortfall, paid in May".

**Reconciliation re-sums everything from the payslip lines** and compares it
with the stored totals, because a payroll that cannot prove it adds up should
not be paid. Employer contributions are shown but never subtracted from net pay:
that conflation is the classic payslip error.

**Gratuity is not invented.** Without a verified rule set the line is zero and
the reason is recorded, and a settlement resting on an unverified set cannot be
approved - an invented formula would understate or overstate a statutory payment
somebody is legally owed, and would look equally plausible either way.

Every amount is a `Decimal` in Python and `NUMERIC(14, 2)` in the database.
Nothing monetary is ever a float.

## Announcements and notifications

**A notification is keyed by the fact, never by the attempt.**
`notifications.dedupe_key` is unique, and every caller derives it from the thing
being reported: the application and the stage that decided it, the attendance
date, the announcement and the user. Never from a run id or a clock reading. So
a retried job, a re-published announcement or a re-processed attendance day
writes nothing the second time - and the database enforces it, rather than
trusting each caller to read before writing, which two concurrent runs would
defeat.

Where a fact genuinely recurs, the recurrence is in the key: a missing punch on
3 March and one on 4 March are two facts, and a manager's approval and HR's
approval of one request are two things the applicant should hear about.

**Announcements and notifications are separate things.** An announcement is one
company notice with an audience, a schedule and a read report. A notification is
one line in one person's centre. Targets are the union of all, department, work
location, employee group and named employee, resolved on the server - an
announcement aimed at another office is not in the response at all, and its
attachment is 404 rather than 403, so a guessed id cannot confirm the notice
exists.

**Publishing is deliberate and reversible only by withdrawal.** A draft reaches
nobody, which is what lets a notice be read back first. A published one can be
corrected, and the audit entry notes that people may have read the earlier text.
Withdrawal needs a reason and keeps the row: there is no delete, because people
have already read it. Visibility is a query on `publish_at`, not a flag a job
sets, so a missed scheduler run delays the notification and never hides the
announcement.

**Reading is not acknowledging.** Opening a closure notice is not the same as
confirming you understood it, so acknowledgement is a separate, explicit act
that a critical announcement asks for. A read receipt cannot be back-dated and
an acknowledgement cannot be withdrawn - both are enforced by trigger.

**Nothing requires a paid service.** Delivery is in-app. The `channel` column
admits email, sms and push so an adapter can be added later without a migration,
but none is needed for the system to work, and muting is per category with the
default being to notify.

**A notification never breaks what it reports.** Every hook swallows its own
failures: an approved leave application that could not be announced is still
approved. Rolling the approval back would be far worse than a missing line in
somebody's centre.

## Reports, audit and retention

**A report reads; it never recomputes.** Payable days in the attendance report
are the same stored decimal payroll used. A report that did its own arithmetic
would be a second implementation of the rule, and the two would drift until
nobody could say which was right.

**A report is never a wider view.** Holding a report permission says you may run
the report, not whose data it covers: each one is narrowed to the employees the
caller can already see, and the catalogue says whether the scope is the whole
company or one team.

**Export is its own permission, and every export is audited.** A spreadsheet
leaves the system's access controls behind; the entry recording who took what,
with which filters, is the only thing that makes the file traceable afterwards.
CSV carries a UTF-8 BOM so Excel does not mangle names, and the Excel export is
a real workbook rather than a renamed CSV.

**Reading the audit log is audited too**, with the filters rather than the
results. Somebody checking what a colleague did is exactly the thing an
organisation may later need to account for.

**Retention is a decision, not a default.** The three retention periods start
unconfirmed, nothing is purged on a schedule, and the purge command is a dry run
unless told otherwise. Purging the audit log needs the owner database role,
because the application should not be able to erase its own trail.

**The restore is tested.** `infra/backup/backup.py verify` dumps, restores into
a scratch database and compares every table's row count and the schema version.
A restore procedure nobody has run is worse than none: it produces confidence
without capability.

See [docs/security.md](docs/security.md) for the security review, including ten
gaps stated plainly.

## Design commitments

These shaped most of the decisions in the codebase.

**Policy is data, not code.** Working week, grace periods, leave year, approval
chain and statutory configuration live in the database. Changing one is an HR
action with a recorded reason, not a redeployment.

**Unconfirmed defaults are labelled as such.** Only values stated in the project
brief ship as confirmed decisions. The rest are visibly flagged as
placeholders, and code that needs a real decision raises
`configuration_missing` rather than quietly computing from a guess. No statutory
rate, ceiling or slab has been entered into this system - see
[docs/open-items.md](docs/open-items.md).

**Authorization is enforced on the server, on every route.** The frontend's
permission checks control navigation visibility only.

**Reporting-manager authority is a relationship, not a role.** Approval requires
both the permission and that the applicant actually reports to you.

**Money is `Decimal`; instants are UTC; calendar dates are derived in the
employee's work-location timezone.** No floats in payroll, and no trust in the
browser clock - a punch is timestamped by the server, and for an overnight shift
it is attributed to the date the shift started.

**Raw records are immutable; summaries are derived.** Attendance events are
append-only, enforced by a database trigger; `attendance_days` is recomputable
from them at any time and carries the policy snapshot that produced it. A
correction adds an event and recomputes - it never edits history.

**History is explicable.** Effective-dated rows instead of in-place updates,
append-only ledgers instead of mutable balances, and a payroll run that pins the
statutory rule version it used so it can be reproduced after the rules change.

**Idempotency is a database constraint.** Unique keys on job runs, ledger
entries, attendance events and notifications, plus an exclusion constraint on
shift assignments - so a retried job cannot double-credit leave, a redelivered
webhook cannot create a second punch, and two concurrent writes cannot leave an
employee on two shifts for one day.

**Sensitive fields are encrypted and never returned in full.** PAN and bank
account numbers are encrypted at rest under a key held separately from the
token-signing key, and the API returns only masked forms. Aadhaar is reduced to
its last four digits at the boundary - the full number is refused, not
truncated. Reading restricted data writes an audit entry.

**A record you may not see returns 404, not 403.** Confirming that an employee
id exists would leak the size and shape of the organisation to anyone
enumerating ids.

## Honest limits

Production readiness needs work beyond Milestone 3: an independent security
review, payroll calculations validated against verified statutory references,
hardware integration tested against real devices, a *tested* backup restore, and
a reviewed deployment environment.

Specifically **not** implemented or claimed:

- **No specific biometric or access-control product is integrated.** The
  framework is built and the generic adapters are tested against their own
  contracts, but no physical device has been exercised against this code. A
  vendor adapter needs that vendor's protocol documentation and a real unit to
  test against; until then an adapter is listed as `generic`, never `tested`.
  Web punching never depends on any of it.
- **No statutory leave entitlement has been entered.** Leave types and their
  quotas are created by the company; none are seeded, because the statutory ones
  have legally mandated entitlements this project has not been given verified
  references for. A type or policy whose entitlement comes from statute carries a
  `requires_legal_review` flag that is shown to employees rather than quietly
  trusted.
- **No holiday is seeded.** Dates vary by year, state and company, and an
  invented one would mark a working day as non-working for everyone on that
  calendar, in attendance and in payroll. HR publishes each year's list.
- Encashment at year end records the leave leaving the balance; **paying it is a
  payroll action** that Milestone 8 has not built yet.
- **No statutory rate, ceiling or slab has been entered or verified.** The
  engine applies whatever a company enters and refuses to compute a domain
  without it; the figures themselves, and the check that they match a published
  source, are the company's to supply. Nothing in this repository is a statutory
  reference.
- **Payroll produces a register, not a payment.** It does not transfer money,
  and no bank integration exists.
- **TDS is a projection.** The engine does banded arithmetic on an annualised
  income from the slabs a company enters. It has no exemptions, deductions,
  rebates, surcharge, cess, regime choice or previous-employer income, so a
  company enabling it is choosing a projection and should say so on its
  payslips.
- Statutory e-filing (EPF ECR, ESI, TDS returns) is out of scope. The
  per-domain totals a return is prepared from are available; the filing is not.
- **Notification delivery is in-app only.** Email, SMS and push are optional,
  and no adapter is implemented: the `channel` column admits them so one can be
  added later without a migration, but nothing in the system depends on a paid
  provider. A notification is therefore seen when somebody signs in, not pushed
  to their phone.
- **Payroll publication notifications are written but not yet wired.** The hook
  and its wording exist and are tested; payroll itself arrives in Milestone 8,
  which calls it when a payslip is released.
- Free hosting is evaluated in Milestone 10, including whether any provider's
  terms are acceptable for confidential payroll data.

## Licence

Proprietary - Ishvara Tech Consulting LLP. All dependencies are free and
open-source.
