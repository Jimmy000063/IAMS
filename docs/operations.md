# Operator guide

For whoever runs this system: deployment, the scheduled jobs, backups, and what
to do when something is wrong. Written to be followed by somebody who did not
build it.

Companion documents: [setup.md](setup.md) for a development machine,
[security.md](security.md) for the controls and their gaps,
[hosting.md](hosting.md) for where to run it, [open-items.md](open-items.md) for
the decisions the system still needs.

---

## 1. Before the first real employee

Do these in order. Each one is refused by the system if the previous is missing,
which is deliberate - but knowing the order saves an afternoon.

### 1.1 Environment

```
IAMS_DATABASE_URL         postgresql+psycopg://iams_app:...@host:5432/iams
IAMS_SECRET_KEY           long random string; signs access tokens
IAMS_FIELD_ENCRYPTION_KEY from `iams generate-encryption-key`; encrypts bank
                          details, PAN and Aadhaar
IAMS_ENVIRONMENT          production
IAMS_CORS_ORIGINS         the frontend's origin, exactly
```

Point the URL at the **`iams_app`** role, not the owner. The per-table
revocations - no deleting audit entries, ledger rows, salary revisions or
finalized payroll - only take effect under that role. Running as the owner
works and quietly removes every one of those protections.

**If `IAMS_FIELD_ENCRYPTION_KEY` is lost, encrypted columns are unrecoverable.**
Store it where you store the database password, and not in the same place as the
backups.

### 1.2 Schema and the first account

```
alembic upgrade head
iams seed-permissions          # the RBAC catalogue; idempotent
iams seed-settings             # the settings catalogue; never overwrites a value
iams bootstrap-admin --username admin --email hr@ishvaratech.com
```

`bootstrap-admin` prints a temporary password once. The account must change it
at first sign-in; it cannot be used for anything else until it does.

### 1.3 Confirm the settings that gate real work

```
iams config-status             # non-zero exit while anything is unconfirmed
```

Two of these are not optional, because the system refuses to proceed without
them rather than guessing:

| Setting | Why it blocks |
|---|---|
| `payroll.payable_days_basis` | The denominator of every loss-of-pay and proration amount. One unpaid day costs 1/30 of a month on one basis and 1/26 on another. **Payroll will not calculate until this is confirmed.** |
| `retention.*` | Nothing is purged while they are unconfirmed, which is the safe direction, but `iams purge-expired-data` will say so every time. |

The rest are working defaults the company should review:
`attendance.grace_late_minutes`, `leave.year_start_month`,
`compoff.expiry_days` and the others listed in
[open-items.md](open-items.md).

### 1.4 Company data, in this order

1. **Organisation** - departments, designations, work locations with their
   timezones. Attendance dates are derived in the employee's work-location
   timezone, so the timezone matters.
2. **Shifts and a work calendar** - including the weekly-off pattern. An
   employee with no shift assignment has no attendance obligation, and the
   engine says so rather than marking them absent.
3. **Holiday calendar** for the year, then **publish** it. A draft affects
   nothing; publishing marks those dates non-working for everyone in scope.
4. **Leave types and policies**, then assign them and grant opening balances.
   None is seeded: statutory entitlements have not been verified here.
5. **Employees**, then link each to an account.
6. **Salary components**, then a salary revision per employee. The components
   must add up to the agreed gross.
7. **Statutory rule sets** - the rates, ceilings and slabs the company is
   registered for - then **verify** each against its published source. Payroll
   can be calculated without this and **cannot be finalized** with it
   outstanding.

## 2. Scheduled jobs

Every job is idempotent: re-running it writes nothing the second time, because
each entry is keyed by the fact rather than the run. A missed run is caught up
by running it later. Nothing here needs a distributed scheduler - the host's own
is enough.

| Job | When | What it does |
|---|---|---|
| `iams accrue-leave` | monthly, after the accrual date | Credits one accrual period. `--as-of` catches up a missed one. |
| `iams grant-compoff` | nightly | Creates comp-off credits from qualifying work. |
| `iams expire-compoff` | nightly | Closes credits past their expiry date. |
| `iams expire-carried-leave` | nightly | Lapses carried-forward leave that reached its date. |
| `iams notify-unsettled-attendance` | nightly | Asks employees to correct days the punches did not settle. Never includes today. |
| `iams run-announcement-schedule` | hourly | Sends notifications for announcements now due. |
| `iams sync-devices` | every 15 minutes | Polls pollable devices. Only if devices are configured. |
| `iams purge-expired-sessions --older-than-days 30` | weekly | Removes long-expired refresh tokens. |
| `python infra/backup/backup.py dump` | nightly | A database dump. See section 3. |

**Not scheduled, on purpose:**

* **Payroll.** A run is opened, calculated, reviewed, approved and finalized by
  people. Automating any of those steps would defeat the separation of duties
  they exist to create.
* **`iams close-leave-year`.** It moves everybody's entitlement at once. Run the
  preview, read the rows, then apply.
* **`iams purge-expired-data`.** A purge destroys evidence as well as data.

Check what the jobs did in the application's **Audit and retention > Scheduled
jobs** view, or:

```
iams check-db                  # connectivity and the current migration revision
```

## 3. Backups

```
python infra/backup/backup.py dump      # a custom-format dump into backups/
python infra/backup/backup.py verify    # dump, restore into a scratch DB, compare
python infra/backup/backup.py restore --file backups/iams-....dump --into iams_check
```

`verify` is the one that matters. It restores a fresh dump into a scratch
database beside the original and compares the schema version and the row count
of every table. **Run it monthly.** A backup procedure that has never been
restored is a guess.

Three things the script deliberately does not do, because they are decisions
rather than defaults:

* **No encryption.** The dump contains salaries, bank account numbers and PAN
  fragments in the clear. Encrypt it before it leaves the machine, or store it
  somewhere that acceptability has been established for.
* **No upload.** A backup on the same host as the database is not a backup.
* **No deletion.** Old dumps accumulate until somebody chooses a retention
  period - the same decision the data itself needs.

To restore for real:

```
python infra/backup/backup.py restore --file <dump> --into iams_restore_check
# inspect it, then
python infra/backup/backup.py restore --file <dump> --force     # over the live DB
```

`--force` is required to restore over the live database, and it is deliberately
inconvenient.

## 4. Routine tasks

**Somebody has left.** Record the exit on their employee record - this is a
status and a date, never a deletion, because attendance, ledger, payroll and
audit rows must keep resolving to a real person. Then calculate their full and
final settlement. If gratuity applies, its rule set must be verified first, or
the figure stays zero with the reason recorded.

**Somebody forgot to punch out.** The day shows as `incomplete` or
`missing_punch` and is **paid in full and flagged** - never docked. The employee
raises a correction, which goes to their manager and then to HR. Running
`iams notify-unsettled-attendance` nightly means they are asked rather than
discovering it on a payslip.

**A punch was recorded wrongly.** HR can add a manual event, supersede one, or
override the day's status with a reason. All three are audited, and an
overridden day is preserved against recomputation.

**Payroll month.** Open a run, calculate, read the review flags, review (not as
the person who calculated), approve (not as the reviewer), finalize, then
release the payslips. A finalized run is immutable; a correction is an
adjustment in a later run.

**A statutory rate has changed.** Create a **new** effective-dated rule set and
verify it against the notification that changed it. Do not edit the old one -
the database will refuse, because past payslips cite it.

**Somebody needs their password reset.** HR issues a temporary password through
the application, which forces a change at first use and revokes existing
sessions. The temporary password is shown once, for out-of-band delivery; no
email provider is involved.

## 5. When something is wrong

**Start with the request id.** Every error response carries one, and every audit
entry records it. A user quoting `a1b2c3d4` lets you find the exact request in
the logs and the trail.

| Symptom | Where to look |
|---|---|
| "Payroll will not calculate" | `GET /payroll/readiness`, or the Statutory tab. It lists the blockers plainly. |
| "The run will not finalize" | The run's `unverified_domains`. Verify the rule set, send the run back, recalculate. |
| "My leave balance is wrong" | The ledger for that employee and leave type. The balance is a sum of entries; each has a reason and an idempotency key. |
| "The attendance day is wrong" | The day's `policy_snapshot` records the settings used and `anomalies` what the engine noticed. Recompute if the inputs have since changed. |
| "A device stopped sending" | The device's sync runs and failure counter; raw events are kept separately from computed days, so reprocessing is safe. |
| "An employee says they never got a notification" | Their notification centre includes archived items. A notification is never deleted. |
| "Did somebody export the salary report?" | The audit log, action `report.exported`. Every export records who, what and which filters. |
| Migration refuses to run | `iams check-db` shows the current revision. `alembic check` reports divergence between models and schema. |

**What not to do:** do not edit the database directly to fix an application
problem. The triggers will refuse most of it, and the parts they allow will
leave the audit trail describing a system that no longer matches.

## 6. Upgrading

```
python infra/backup/backup.py dump     # first, always
alembic upgrade head
iams seed-permissions                  # new permissions, if any
iams seed-settings                     # new settings, never overwriting values
iams config-status                     # anything new awaiting confirmation
```

Migrations are the only way the schema changes; `alembic check` fails the build
if a model and the schema have diverged. Every migration in this project has a
tested downgrade, but **a downgrade that drops a table destroys its data** -
restore from the dump instead unless the downgrade is known to be safe.

**Never run `alembic downgrade base` against a database you care about.** It is
the right way to prove every migration round-trips, and the only safe place to
prove it is a scratch or test database. Doing it on a populated one empties it,
and `backup.py verify` deletes its own dump when it succeeds, so there may be
nothing to restore from. (This happened once during development, on the
development database.)

## 7. What this system does not do

Worth knowing before somebody expects it:

* **It does not move money.** Payroll produces a register; the transfer is made
  by a person from that data.
* **It does not file statutory returns.** The per-domain totals are available;
  the filing is not.
* **It does not send email, SMS or push notifications.** Delivery is in-app, so
  a notification is seen when somebody signs in.
* **It does not integrate any specific biometric device.** The framework and
  generic adapters are built and tested against their own contracts; no physical
  unit has been exercised against this code. Web punching never depends on one.
* **It does not verify statutory rates.** It refuses to compute a statutory
  domain without rules the company entered and verified against a source.
* **It does not pay overtime.** The minutes are recorded; the multiplier is a
  legal rate that has not been verified here.
