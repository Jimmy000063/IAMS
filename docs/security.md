# Security review

Written at Milestone 9, after the features it describes were built and tested.
It records what the system does, what it deliberately does not do, and what an
independent reviewer should look at before this handles real payroll. It is not
a certificate: nobody outside this project has audited the code.

Everything below was verified against the running system unless marked
otherwise.

---

## 1. Identity and sessions

| Control | How it works | Where |
|---|---|---|
| Password storage | Argon2id, with the cost parameters in settings and a CLI command to time them on the target hardware | `app/core/security.py`, `iams benchmark-hash` |
| Access tokens | Short-lived JWT carrying identity only - never permissions, so a grant revoked now takes effect on the next request rather than at token expiry | `app/modules/auth/service.py` |
| Refresh tokens | Opaque random strings, stored as SHA-256 digests, rotated on use, with reuse detection that revokes the whole family | `app/modules/auth/models.py` |
| Lockout | Failed attempts counted per account and committed in their own transaction, so the counter survives the rollback of the failed request | `app/modules/auth/service.py` |
| Temporary passwords | Expire after a configured number of days and force a change on first use | `security.temporary_password_validity_days` |
| Public registration | **Not implemented.** Accounts are created by an administrator | `security.public_registration_enabled`, not editable |

**What a reviewer should check.** There is no per-IP rate limit in the
application: lockout is per account, so a spray across many usernames from one
address is not slowed down. That belongs in the reverse proxy, and the
deployment notes say so, but it means this system alone does not stop it.

## 2. Authorization

Every protected endpoint declares its permission as a FastAPI dependency, and
the permission codes are validated against the catalogue **at import time** - a
typo in a permission name fails the application's start-up rather than silently
granting access.

Three rules are enforced in the service layer rather than left to the
interface, because the interface is not a security boundary:

1. **Relationship, not just permission.** `leave.approve_as_manager` says you
   may review a report's leave; the service additionally verifies that the
   applicant reports to you.
2. **Separation of duties.** Nobody decides their own leave, comp-off or
   regularization. Nobody reviews a payroll run they calculated, or approves one
   they reviewed.
3. **Scope, not visibility.** Team-scoped queries narrow at the database, so a
   manager's report and a manager's list return the same set of people. Reports
   in particular are never a wider view than the module they read.

**404 rather than 403** for records outside the caller's scope, so object ids
cannot be enumerated by comparing responses.

**What a reviewer should check.** The permission catalogue is large (80 codes).
A review should confirm the two seeded roles grant what their names imply -
particularly that the employee role has no `read_all` or `manage` grant.

## 3. Data protection

| Data | Treatment |
|---|---|
| Bank account numbers, PAN, Aadhaar | Encrypted at rest with Fernet (MultiFernet for rotation), under a key held separately from `secret_key`; only the last four digits are stored in clear, for display |
| Passwords | Argon2id hashes; never logged, never returned by any endpoint |
| Tokens | Refresh tokens stored as digests; access tokens never persisted |
| Audit values | Passed through a key-name redactor before storage, so a payload containing `password` or `account_number` is masked even if a caller sends it |
| Logs | Structured, with a request id; the redactor is deliberately over-inclusive |
| Uploads | Declared content type checked against magic bytes; stored filename server-generated; served only as an attachment with `X-Content-Type-Options: nosniff` |
| Reports | The employee register deliberately omits bank, PAN and Aadhaar fields: an exportable spreadsheet of them would be the easiest thing in the system to leak |

**Key handling is the weakest link and is not solved here.** The encryption key
lives in an environment variable. If it is lost, encrypted columns are
unrecoverable; if it leaks, they are readable. A production deployment needs a
key store and a documented rotation procedure - `iams rotate-encryption-key`
re-encrypts with a new primary key, but where the keys live is an operational
decision this project has not made.

## 4. Transport and headers

Set on every response by middleware (`app/main.py`):

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'
Strict-Transport-Security: max-age=31536000; includeSubDomains   (production only)
```

The API serves JSON, so it gets a deny-everything CSP. The documentation pages
get a narrower policy that permits their CDN bundle - applying the API policy
there would leave a blank page, and silently broken documentation is worse than
an explicit exception.

CORS is restricted to configured origins with credentials allowed; the default
development origin is the Vite dev server only.

**What a reviewer should check.** HSTS is only sent in production mode, and TLS
itself is the deployment's responsibility. There is no certificate pinning and
no mutual TLS.

## 5. Input handling

* Every request body is a Pydantic model with `extra="forbid"`, so an unexpected
  field is a 422 rather than being ignored - which is what stops a client
  setting a field the endpoint did not mean to accept.
* All database access goes through SQLAlchemy with bound parameters. The few
  raw SQL statements are in migrations and the `text()` calls in tests; none
  interpolates user input.
* File uploads are size-limited and type-checked by magic bytes.
* The CSV importer validates per row and reports problems without aborting the
  file; a dry run is the default.
* Monetary values are `Decimal` end to end. No payroll figure is ever a float.

**What a reviewer should check.** There is no global request body size limit in
the application; `uvicorn` and the proxy enforce one. The row caps on reports
(20,000) and the date-range cap on the daily register are the only query-cost
limits.

## 6. Auditability

* `audit_logs` is append-only, enforced by a `BEFORE UPDATE` trigger, with
  `UPDATE` and `DELETE` revoked from the application role.
* Every state-changing endpoint records an entry with before and after values,
  the actor, their IP and the request id.
* **Reading the audit log is itself audited**, with the filters used - somebody
  checking what a colleague did is exactly what an organisation may later need
  to account for.
* **Every report export is audited**, because a spreadsheet leaves the system's
  access controls behind and the entry is the only thing that makes the file
  traceable afterwards.
* Document downloads are audited when `security.document_access_logged` is on,
  which it is by default.
* Immutability is enforced in the database, not only in services: finalized
  payroll runs and their payslips, verified statutory rule parameters, leave and
  comp-off ledgers, approval rows, read receipts and the identity of a
  notification.

## 7. Least privilege in the database

`infra/postgres/init/01-least-privilege.sql` creates two roles beside the owner
and grants the broad defaults:

* `iams_app` - the application. `SELECT, INSERT, UPDATE, DELETE` on business
  tables, plus sequence usage.
* `iams_readonly` - reporting and inspection. `SELECT` only.

The **narrowing** is done per table by the migration that creates it, because
that is where it belongs: whoever adds a table decides whether its rows may ever
be deleted. `DELETE` is revoked from `iams_app` on audit logs, leave and
comp-off ledger entries, approval rows, comp-off credits and allocations, salary
revisions, payroll runs and periods, adjustments, published announcements and
notifications. `UPDATE` is revoked where a row is append-only.

Migrations run as the owner. The retention purge of `audit_logs` therefore
**requires the owner role** and fails loudly under the application's
credentials, which is intended: purging the trail should not be something the
application can do.

**What a reviewer should check.** Local development connects as `iams_owner`
for convenience - migrations and the application run from the same place. A
production deployment must point `IAMS_DATABASE_URL` at `iams_app` and run
`alembic upgrade` separately as the owner. **The revocations above only take
effect when it does**, so a deployment left on the owner role has none of them.

## 8. Retention and privacy

* Three retention periods exist as settings, all **unconfirmed** by default:
  audit logs, raw device events and notifications. None is applied
  automatically.
* `iams purge-expired-data` reports what it would remove and deletes only with
  `--apply`. It names any unconfirmed period, because a retention period
  destroys evidence as well as data.
* The retention report endpoint counts rows beyond each period without deleting
  anything, so the position can be reviewed before a purge.
* Employees are never deleted; exit is a status and a date, because attendance,
  ledger, payroll and audit rows must keep resolving to a real person.

**What a reviewer should check.** No subject-access export or erasure workflow
exists. Erasure conflicts with statutory retention of employment records, so it
needs a legal decision before it is built, not a technical one.

## 9. Backup and restore

`infra/backup/backup.py` dumps, restores and - the part that matters -
**verifies**: it takes a fresh dump, restores it into a scratch database beside
the original, and compares the schema version and the row count of every table.
Run at Milestone 9 against the development database: 71 tables, 915 rows, schema
version 0008, every count matching.

Deliberately not done by the script: encryption, upload to anywhere, and any
schedule. A payroll dump is the most sensitive file this system produces, and
where it is stored and who holds the key are decisions for whoever operates it.
The script says so when it finishes.

**What a reviewer should check.** There is no point-in-time recovery
configuration and no off-site copy. Both are deployment decisions; neither is
simulated here.

## 10. Known gaps

Stated plainly, because a security document that lists only strengths is
marketing.

1. **No independent review.** Nobody outside this project has read this code
   with an attacker's intent.
2. **No per-IP rate limiting** in the application; lockout is per account.
3. **Encryption key handling is environmental.** No key store, no automatic
   rotation schedule.
4. **No two-factor authentication.** Argon2id and lockout are the only barriers
   to a stolen password.
5. **No session listing or remote revocation for users** - an administrator can
   revoke all sessions for an account, but a user cannot see their own.
6. **No anomaly detection.** A compromised administrator account could change
   salaries and approve its own payroll if granted every permission; the
   separation-of-duties checks assume separate accounts exist and are used.
7. **TLS, proxy hardening and OS patching are outside this repository.**
8. **Statutory figures are unverified by design** - see `docs/open-items.md`.
   That is a correctness and compliance risk, not a security one, but it has the
   same shape: a plausible wrong number nobody checked.

## Verification record

| Check | Result |
|---|---|
| Permission codes validated at import | Application fails to start on an unknown code |
| Scoped reports | A manager's report excludes employees outside their team (integration test) |
| Export permission | An account with `report.attendance` but not `report.export` is refused the export |
| Export auditing | Every export writes a `report.exported` entry with its filters |
| Audit-read auditing | Searching the log writes an `audit.searched` entry with the filters, not the results |
| Audit log immutability | `UPDATE` refused by trigger |
| Finalized payroll immutability | `UPDATE` refused by trigger, on the run and its payslips |
| Verified rule parameters | `UPDATE` and `DELETE` refused by trigger |
| Unauthenticated access | 401 on every protected route tested |
| Employee access to admin routes | 403 on every admin route tested |
| Backup restore | Verified by round trip: 71 tables, 915 rows, schema 0008 |
