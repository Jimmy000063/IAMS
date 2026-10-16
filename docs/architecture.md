# Architecture

## Shape

Two deployables and one database.

```
browser --> Vite SPA (React 19, TypeScript)
                |  Authorization: Bearer <access token>
                v
            FastAPI (Python 3.12+)
                |  SQLAlchemy 2.1 / psycopg 3
                v
            PostgreSQL 17
```

In development the SPA calls `/api/v1/...` on its own origin and Vite proxies to
the backend, so the browser sees one origin and CORS is not exercised on the
common path. In a deployed setup the same arrangement is reproduced by a reverse
proxy, or `VITE_API_BASE_URL` points at the API host and `IAMS_CORS_ALLOW_ORIGINS`
is set to the SPA origin.

## Backend layering

Each module under `backend/app/modules/<name>/` follows the same shape, and the
dependency direction is strictly downward:

| File | Responsibility | May not do |
|---|---|---|
| `router.py` | HTTP surface: path, schema validation, permission dependency, status codes | Contain business rules |
| `service.py` | Orchestration and transaction boundaries; calls policy, writes models, records audit | Touch `Request`/`Response` |
| `policy.py` | Pure business rules -- accrual arithmetic, attendance classification, statutory computation | Perform I/O |
| `repository.py` | Non-trivial queries, where a service would otherwise carry a wall of SQL | Hold rules |
| `models.py` | SQLAlchemy mapped classes and database constraints | Hold rules |
| `schemas.py` | Pydantic request/response models | -- |

The `devices` module adds one more layer, `adapters/`, below `policy.py`. An
adapter's only job is to turn whatever a device speaks into a list of raw
payload dictionaries; everything after that is shared. It may not parse
timestamps, infer direction, deduplicate, resolve employees or write anything.

`policy.py` being I/O-free is what makes the parts that matter cheaply testable:
leave accrual, overnight-shift classification and statutory calculation are
functions over values, so a reference fixture can be asserted exactly without a
database.

## Cross-cutting decisions

### Time and timezone
Every stored instant is `TIMESTAMPTZ` in UTC. A *calendar date* -- `work_date`,
`leave_date` -- is derived in the employee's **work-location timezone**, never the
server's and never the browser's. The helpers are in `app/core/timeutil.py`, and
`utc_now()` is the only sanctioned source of "now". A punch timestamp is
generated server-side; the browser clock is never trusted.

### Money
All monetary columns are `NUMERIC(14,2)`, mapped to Python `Decimal`. No float
appears anywhere in payroll. Rate values are `NUMERIC(9,6)`. The rounding mode is
configuration (`payroll.rounding_mode`), not a literal.

### Configuration vs. code
Business policy lives in the database, in two forms, chosen by shape:

- **Scalar, company-wide** -> `system_settings` rows, seeded from
  `app/modules/settings/catalogue.py`. Editable by HR/Admin; every change writes
  to `setting_change_history` and `audit_logs`.
- **Rich, effective-dated, per-employee** -> their own tables with
  `effective_from` / `effective_to`: `shifts`, `shift_assignments`,
  `leave_policy_rules`, `employee_salaries`, `statutory_rule_sets`. A key/value
  row cannot express "12% of PF wages, capped at X, from 1 April, for employees
  hired before Y" -- and cannot reproduce a past payroll after the rule changes.

Seeding never overwrites a stored value. An HR decision always beats a code
default.

### Honest defaults
A setting carries `is_confirmed`. Only values stated in the project brief ship
confirmed; everything else is a working placeholder, visibly flagged in the UI
and reported by `iams config-status`. `settings.require_confirmed()` raises
`configuration_missing` rather than returning a placeholder, so payroll fails
loudly instead of producing a plausible wrong number.

### Authorization
Three independent layers:

1. **Permission** -- a code such as `leave.approve_hr`, declared in
   `app/core/permissions.py` and stored in `permissions`.
2. **Role** -- a bundle of permissions (`hr_admin`, `employee`), stored in `roles`.
   An administrator can add a role later without a code change.
3. **Relationship** -- whether the actor stands in the required relation to the
   record (owns it, or is the subject's reporting manager).

Layer 3 is why *reporting manager is not a role*. Managerial authority follows
`employees.reporting_manager_id`, so every employee holds
`leave.approve_as_manager` and the service additionally verifies the applicant
reports to them. A role grant alone can never approve an arbitrary employee's
leave.

Every protected route declares `Depends(require("..."))`. Unknown permission
codes raise at import time, so a typo stops startup instead of silently creating
a route nobody can reach. The frontend's `can()` controls navigation visibility
only -- it is never the control.

### Permissions are not in the token
Access tokens carry identity alone. Permissions are read from the database on
each request, so revoking a role takes effect on the next request rather than at
token expiry. The cost is one indexed read per request, which is the right trade.

### Sessions
Short-lived JWT access tokens plus opaque, rotating refresh tokens. Only the
SHA-256 digest of a refresh token is stored. Rotation records
`replaced_by_id`; replay of a *rotated* token revokes every session for that
user, because it indicates theft. A token revoked for any other reason (logout,
password change, deactivation) is simply rejected -- a stale browser tab retrying
after sign-out must not log the user out of every device.

### Durable security writes
Three security effects must survive the rejected request that produced them:
failed-login records, the per-account failure counter, and the mass revocation
on token reuse. Each failure path ends in an exception, which rolls the request
transaction back -- so these are committed in their own transaction
(`_record_failure_durably`, `_revoke_all_sessions_durably` in
`modules/auth/service.py`). Without that, rate limiting and lockout would be
silently inoperative. Both behaviours have regression tests.

### Idempotency
The recurring hazard in an HRMS is a retried job double-crediting leave or
duplicating an encashment. Three mechanisms:

- `job_runs.idempotency_key` -- unique. A job computes a deterministic key from
  its name and period (`leave_accrual:2026-04`) and inserts the row first, so a
  duplicate run fails at the database level.
- `leave_ledger_entries.idempotency_key` -- unique per logical movement, e.g.
  `accrual:42:CL:2026-04`. The key omits the run id and posting date, which
  would otherwise differ between runs and defeat it entirely.
- `attendance_events (source, external_event_id)` and
  `device_raw_events (device_id, external_event_id)` -- unique, so a re-delivered
  webhook or a re-imported CSV cannot create a second punch. A device that
  numbers nothing gets an id derived from the event's content, which deliberately
  excludes any *inferred* punch direction so reprocessing in a different order
  cannot defeat the check.
- `device_sync_runs.idempotency_key` -- unique over device, trigger and interval
  window. A scheduler firing twice in one window gets the original run's outcome
  back and fetches nothing.
- `payroll_runs.idempotency_key` -- unique over the period and run type, so two
  regular runs for one month are impossible. The second would look like a
  legitimate register and double the statutory filings.
- `payroll_adjustments.idempotency_key` -- unique, derived from the employee,
  type, amount and reason, so a retried import of arrears pays each person once.
- `notifications.dedupe_key` -- unique, so a retried job cannot notify twice.
  The key names the **fact**, not the attempt: `leave:41:hr_admin:approved`,
  `attendance:2026-03-02:needs_attention:missing_punch`,
  `announcement:7:user:12`. It never contains a run id or a clock reading, which
  would differ between runs and defeat it. Where a fact legitimately recurs the
  recurrence is in the key, so two dates or two approval stages remain two
  notifications. The keys are built by pure functions in
  `modules/notifications/policy.py`, which is what makes the property testable
  without a database.

### Append-only records
`audit_logs`, `leave_ledger_entries`, `leave_approvals`, the comp-off ledger,
`announcement_reads` and the identity of a `notification` are append-only. A
**finalized** `payroll_run` and its payslips are immutable by trigger, and the
parameters of a **verified** `statutory_rule_set` are frozen against update and
delete - a finalized run is the record of money that moved, and a verification
is a statement that somebody checked those particular figures. For `audit_logs`
this is enforced *in the database*: a `BEFORE UPDATE` trigger rejects any update,
and `UPDATE`/`DELETE` are revoked from the application role. `DELETE` remains
possible for the owner role so a documented retention purge is still available.

### JSONB and the meaning of null
Every JSONB column uses one shared type, `Jsonb` in `app/db/base.py`, declared
with `none_as_null=True`. SQLAlchemy's default for a JSON column stores Python
`None` as the JSON value `null`, which is **not** SQL NULL - so `IS NULL` reads
false for a column nobody set. That broke a check constraint written as "exactly
one of these is present" and would have made a query for "payslips with review
flags" return every payslip. The trap was found in Milestone 8, by which time
the development database already held 274 such rows across attendance, audit and
notifications.

### Error contract
One envelope for every failure:

```json
{ "error": { "code": "policy_violation", "message": "...", "details": [] },
  "request_id": "a1b2c3d4" }
```

`code` is stable and safe for the client to branch on; `message` is safe to
display. Internal exception text never reaches a response. `request_id` also
appears in the `X-Request-ID` header and in server logs, so a user-reported
problem is traceable.

### Logging
`app/core/logging.py` installs a redaction filter as a backstop: passwords,
bearer tokens and long digit runs (bank/Aadhaar numbers) are replaced before a
record is emitted. The audit writer redacts by key name
(`app/modules/audit/service.py`). Redaction is deliberately over-inclusive -- it
blanks any field whose *name* suggests a secret -- so audit payloads avoid such
names when the value is worth keeping.

### Transactions
One request, one transaction. `get_db_session` commits on a clean return and
rolls back on any exception, so a handler that raises mid-way cannot leave a
half-written ledger or payroll run behind.

Two consequences shape the device pipeline. A unique-constraint collision on one
incoming event is caught on a **savepoint** (`session.begin_nested`), so losing
a deduplication race discards that one event rather than the sync run and every
event already applied. And a sync that fails is **returned, not raised**: the
endpoint answers 200 with `status: "failed"` and the reason, because raising
would roll back the very record saying it failed. An integration whose failures
leave no trace is worse than one that fails loudly.

## Device integration

**One ingestion path.** An adapter's only job is to produce a list of raw
payloads. Parsing, direction inference, deduplication, employee mapping and
writing all happen downstream in `modules/devices/service.ingest`, so a new
adapter cannot get those parts subtly wrong in its own way.

**Honesty as a field, not a footnote.** `AdapterDescription.maturity` is
`tested`, `generic` or `development_only`, and the catalogue endpoint returns it
along with what it means and the adapter's caveats. No adapter is written
against a specific biometric or access-control product; the catalogue says so
explicitly rather than leaving an operator to discover it after configuring a
device. The simulator adapter refuses to run outside a local or test
environment, which is what makes it safe to ship at all.

**Nothing received is discarded.** Every payload becomes a `device_raw_event`,
including ones that could not be interpreted. That single decision is what makes
a wrong field mapping recoverable: fix the configuration and reprocess the
stored events, with no need for the device to re-send. Already-applied events
are refused for reprocessing, since re-applying would duplicate the punch.

**Interpretation is declared, never inferred.** Timestamp formats are chosen
from an explicit list because `03/04/2026` is a different day under a day-first
and a month-first reading, and the wrong choice stays invisible until a
month-end report. Naive timestamps are anchored to the device's declared zone --
never the server's -- and the shift date is then derived through the same
attendance policy a web punch uses, so an overnight shift lands on the date it
started.

**The webhook is the only unauthenticated write in the application**, and a test
asserts that remains true by reading the OpenAPI document. A device will not
perform a sign-in, so authentication is HMAC-SHA256 over the **raw request
body**, verified before any parsing -- verifying after would let an attacker
alter semantically-equivalent content without breaking the signature. A device
with no secret configured is refused rather than accepted unauthenticated, the
refusal does not disclose which check failed, and the rejection is audited in
its own committed transaction so the evidence survives the rollback the 401
causes.

**Outbound polling is treated as untrusted even though only HR/Admin configures
it.** `rest_poll` validates its endpoint on every request: http(s) only, no
credentials in the URL, redirects disabled, and cloud-metadata, link-local,
multicast and reserved addresses always refused. Private and loopback addresses
need an explicit opt-in, because a LAN device legitimately *is* a private
address. A failed poll never echoes the device's own error text, which can
contain the credential just sent to it.

**The failure that raises no error.** A device that stops reporting produces no
exception -- just a slowly growing hole in attendance that surfaces at month
end. `last_seen_at` plus a configurable quiet threshold turns that into
something the health screen can name while the gap is still small.

## Leave

**A balance is a `SUM` over an append-only ledger, never a stored column.** That
single decision drives most of the module: a mutable balance cannot be
reconciled or explained, and is where leave disputes come from. A mistake is
corrected by posting a reversal, so the error and the fix both remain visible,
and a trigger plus revoked privileges mean no amount of application-layer
mistake can rewrite one.

**Four balance figures rather than one.** `balance_days` is the ledger as of a
date; `pending_days` is held by undecided requests; `committed_days` is approved
leave dated later; `available_days` is what remains. The distinction is not
cosmetic - a deduction is dated on the leave's own start date so carry-forward
attributes it to the right leave year, which means leave approved today for next
month does not move today's total. Without counting it separately an employee
could book the same entitlement twice.

**One calendar resolver, shared with attendance.** Leave day counting calls
`shifts.policy.resolve_day_type` through the same `calendar_spec` loader
attendance uses. A second implementation would eventually disagree, and a
disagreement between leave and attendance about which days were working days is
exactly the payroll dispute this system exists to avoid. The preview endpoint
exists for the same reason: the form shows what a request will cost by asking
the backend, not by reimplementing the rules in the browser.

**Rounding is applied once.** Per accrual period, never to a running total -
twelve monthly accruals of 1.25 rounded each time to 1.5 would give 18 days
against a 15-day quota. Accrual periods are anchored to the leave year rather
than the calendar, so a company whose year starts in April gets quarters running
April to June.

**Relationship, not just permission.** `leave.approve_as_manager` is a
precondition; the service checks that the applicant actually reports to the
approver, and answers 404 rather than 403 when they do not, so the reporting
structure cannot be mapped by probing ids. Self-approval is refused regardless
of permissions held.

## Frontend structure

```
src/
  app/        router, guards, shell, navigation definition
  lib/        api client, auth context, toasts, formatting
  components/ui/  accessible primitives over the design tokens
  features/<module>/  api functions, pages, feature components
```

- **`lib/api.ts`** is the only place that knows about tokens, the error envelope
  and refresh. It coalesces concurrent refreshes into one rotation -- without
  that, a dashboard firing five queries at once would rotate five times, the
  backend would read the replays as theft, and the user would be signed out by
  their own page loading. It distinguishes *rejected* from *unreachable*, so a
  connectivity blip does not sign anyone out.
- **Design tokens** are declared once in `src/index.css` as Tailwind v4 theme
  variables, with surfaces named by role (`page`, `raised`, `sunken`, `ink`) and
  redefined for dark mode in one place rather than via `dark:` on every element.
- **Four states** -- loading, empty, failed, loaded -- are handled explicitly by
  every data view, using the shared components in `components/ui/Feedback.tsx`.
  A *refused rule* is not one of the failures: when the server answers
  `policy_violation`, `configuration_missing` or `not_found`, nothing has gone
  wrong and `ErrorPanel` says so calmly instead of in alarm red.

### Theming

No component in the application names a colour. The brand, accent and cool
ramps in `src/index.css` are generated from five control variables, and every
surface tint derives its hue from the brand:

| Variable         | Meaning                                              |
| ---------------- | ---------------------------------------------------- |
| `--brand-hue`    | the primary hue, in oklch degrees                    |
| `--brand-chroma` | multiplier on brand saturation (1 full, 0 grey)      |
| `--accent-hue`   | the secondary hue, for emphasis and pending states   |
| `--cool-hue`     | a third hue, for gradients and chart series only     |
| `--tint-chroma`  | multiplier on how much surfaces pick up the brand    |

A colour is therefore five numbers under `:root[data-palette='<id>']`, so
switching theme is one attribute write on the document element. The browser
recalculates every derived custom property itself; nothing re-renders, and no
component subscribes to a colour. One further attribute works the same way:

- `data-mode` -- `light` or `dark`; absent means follow the operating system.

There are two settings and no more. An earlier version also offered corner
radius, glass or solid panels and a background toggle, behind a settings panel
in the top bar. It was cut: they were three more decisions for somebody who
only wanted a different blue, and the panel was a place to go rather than a
thing to click. Both remaining settings now sit in the account menu as
submenus, applying the moment they are chosen. Adding a colour is a block in
`index.css` plus a row in `PALETTES`; nothing else changes.

Two consequences worth knowing:

- **`dark:` is redefined.** A `@custom-variant dark` in `index.css` keys the
  variant to `data-mode` *and* the media query. Without it, choosing dark in the
  interface would change the surface variables while every `dark:` utility
  stayed in light mode -- half a theme.
- **Status colours are not themed.** Success green, warning amber and danger red
  are fixed across all palettes. Red has to mean red in every theme, or a
  payroll error stops being legible.

Settings live in the browser (`localStorage`, key `iams.appearance`), not
against an account: which theme suits a screen is a property of where somebody
is sitting, not of who they are. `lib/appearance.ts` holds the data and the DOM
writes and has no React dependency, so the pre-paint call from `main.tsx` that
prevents a flash of the wrong theme does not drag a provider in with it;
`lib/theme.tsx` is only the provider that makes the settings changeable. Stored
values are validated field by field, because a bad `data-palette` would leave
`--brand-hue` undefined and render every brand colour as nothing at all.

### Controls

- **Fields** get their appearance from `.field-control` (and `.field-group`,
  for a field that contains its own buttons) rather than a string of utilities,
  because the focus halo needs a blurred, coloured box-shadow that an outline
  cannot express, and the states compose properly as pseudo-classes.
- **Dates** never use `<input type="date">`. `components/ui/DatePicker.tsx`
  replaces it: the native popover differs between browsers, cannot be styled,
  and -- the reason it matters here -- cannot shade the work calendar's
  non-working days, which is what makes picking a leave date intelligible.
  `ControlledDate` bridges it to react-hook-form, since `register()` drives
  inputs and a picker is not one.
- **The organisation chart** is built in the browser from the manager links
  already present in the employee list. The construction is in
  `features/employees/orgTree.ts`, under test, because a reporting cycle in the
  data would otherwise recurse forever and hang the tab. Several roots and
  cycles are both expected inputs, not error cases.

## Database conventions

- `BigInteger` identity primary keys.
- Constraint naming convention set on the metadata, so Alembic can alter and
  drop constraints it did not name itself.
- Check constraints for value domains (`job_runs.status`, non-negative counters,
  lowercase username/email) so a bug in a service or a manual SQL fix cannot
  persist a nonsense state.
- Partial indexes where the hot query is over a subset
  (`ix_refresh_tokens_active WHERE revoked_at IS NULL`).
- Every mapped class is imported in `app/db/registry.py`. Alembic's `env.py`
  imports only that module, so a model nobody imported cannot be missing from
  `Base.metadata` -- which would make autogenerate emit a `DROP TABLE`.
- Migrations are the only way the schema changes. `create_all` is used nowhere
  outside the test fixture.
- **Session parameters are libpq startup options, never `SET` statements.**
  `get_engine` passes `-c timezone=UTC` and `-c statement_timeout=...` through
  `connect_args`. A `SET` in a pool `connect` handler looks equivalent and is
  not: psycopg opens an implicit transaction on a connection's first statement,
  so the `SET` runs inside one, and SQLAlchemy rolls the connection back when it
  returns to the pool. The setting reads back correctly if checked immediately,
  which is exactly why this went unnoticed through ten milestones -- the
  statement timeout never once applied, and the session timezone silently
  followed whatever the server was installed with. Startup options are
  connection properties and survive a rollback.
- The transport timezone is UTC and nothing else. Work-location timezones are
  applied deliberately in the policy layer. Without the pin above, a server in
  Asia/Kolkata returns `09:32+05:30` where a UTC container returns
  `04:02+00:00` for the same row: the same instant, but any code reading
  `.hour`, calling `.date()` or formatting without converting gives a different
  answer on a laptop than in production. Attendance, overnight shifts and
  payroll cut-offs all do exactly that.

## Least privilege

`infra/postgres/init/01-least-privilege.sql` creates `iams_app` (DML only, no
DDL) and `iams_readonly`, with default privileges so future migrations' tables
are covered. Local development uses the owner role for convenience; staging and
production should point `IAMS_DATABASE_URL` at `iams_app` and run migrations
separately as the owner.
