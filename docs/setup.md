# Local setup

Verified on Windows 10 with PowerShell, Python 3.14.2, Node 22.21.1, Docker
29.1.3 (Linux containers).

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.14.2 | 3.12 or newer |
| Node.js | 22.21.1 | 22 or newer |
| Docker Desktop | 29.1.3 | Linux containers; must be running |
| Git | 2.49 | |

No local PostgreSQL client is needed -- the database runs in Docker, and
`docker exec iams-postgres psql ...` gives you a shell when you want one.

## 1. Database

A **locally installed PostgreSQL** on its default port 5432. Version 15 or
later; 17 or 18 recommended, since the schema uses `NULLS NOT DISTINCT` unique
constraints (PostgreSQL 15+) along with JSONB, partial indexes and plpgsql
triggers.

Run the setup scripts as a superuser. They prompt for its password.

```powershell
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"   # adjust the version
& $psql -U postgres -h localhost -p 5432 -f infra\postgres\local-setup.sql
& $psql -U postgres -h localhost -p 5432 -d iams      -f infra\postgres\local-grants.sql
& $psql -U postgres -h localhost -p 5432 -d iams_test -f infra\postgres\local-grants.sql
```

That creates:

| Thing | Purpose |
|---|---|
| `iams` | The development database. |
| `iams_test` | The test database. **Separate on purpose**: the suite truncates tables between tests and creates one database per parallel worker, so pointing it at `iams` would empty your development data on every run. |
| `iams_owner` | Owns the schema; Alembic connects as this role. Has `CREATEDB`, which the parallel test runner needs. |
| `iams_app` | The application at runtime. Can read and write rows but not drop a table. |
| `iams_readonly` | Reporting and inspection. `SELECT` only. |

The grants script is separate and runs **per database**, because
`GRANT ... ON SCHEMA` and `ALTER DEFAULT PRIVILEGES` act on the database you are
connected to rather than one you name - running them against `postgres` would
look as though it had worked.

Check it:

```powershell
$env:PGPASSWORD = "iams_local_dev"
& $psql -U iams_owner -h localhost -p 5432 -d iams -c "SELECT version();"
Remove-Item Env:PGPASSWORD
```

The passwords in `local-setup.sql` are development placeholders. Change them
there and in `backend/.env` together if you prefer; a deployment should set them
from its own secret store rather than from a file in the repository.

**Containers instead, if you would rather not install PostgreSQL:**
`docker-compose.yml` still provides both databases - but it publishes 5433 and
5434, so `backend/.env` and `IAMS_TEST_DATABASE_URL` have to be changed to
match. The local installation is the default path and the one the test suite
expects.

## 2. Backend

The scripted path, from the root of the checkout:

```powershell
.\scripts\setup.ps1 -Python "C:\Program Files\Python314\python.exe"
```

```bash
PYTHON=/usr/bin/python3.12 ./scripts/setup.sh
```

This does everything in this section and the frontend section below. It refuses
to continue if the Python version is wrong or if an existing `.venv` was built
by a different interpreter, and it will not overwrite an existing `.env`. Pass
`-SkipFrontend` (or `SKIP_FRONTEND=1`) to leave `node_modules` alone.

By hand, if you prefer:

```powershell
cd backend

# Name the interpreter explicitly rather than relying on `python`.
& "C:\Program Files\Python314\python.exe" -m venv .venv    # adjust the version
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps

# Only when backend\.env does not exist yet - see the warning below.
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### Which install command to use

| Command                             | Installs                                   |
| ----------------------------------- | ------------------------------------------ |
| `pip install -r requirements.txt`    | runtime dependencies, exact versions        |
| `pip install -r requirements-dev.txt`| the above plus pytest, coverage and ruff    |
| `pip install -e ".[dev]"`            | whatever the ranges in `pyproject.toml` allow |

The requirement files are pinned: they record one combination of versions that
is known to work, so a checkout on another machine installs what was tested.
`pyproject.toml` holds the acceptable ranges and remains the place to add or
remove a dependency. Use the editable install when you deliberately want newer
releases, then re-pin:

```powershell
cd backend
python scripts\generate_requirements.py
```

Review that diff before keeping it. An unexpected version jump is exactly the
kind of thing worth noticing.

Neither requirement file installs the `app` package itself, which is why
`pip install -e . --no-deps` follows. That is what puts the `iams` command on
PATH and makes `app` importable from outside `backend/`.

**Two ways this step goes wrong, both of which have happened here:**

A bare `python -m venv .venv` uses whatever `python` resolves to - an Anaconda
base environment, for instance. If `.venv` already exists, it rebuilds the
launcher scripts against that interpreter and leaves `site-packages` untouched,
so you end up with wheels compiled for one Python version running under
another. The symptom is an unhelpful `ImportError: cannot import name
'ArgsKwargs' from 'pydantic_core._pydantic_core' (unknown location)`: the
compiled extension no longer matches the interpreter's ABI tag, so Python falls
back to the type-stub directory beside it. Check with
`python -c "import sys; print(sys.version)"` and `cat .venv\pyvenv.cfg`; the fix
is to delete `.venv` and recreate it with the full path to the interpreter you
mean.

`Copy-Item .env.example .env` over an **existing** `.env` replaces the generated
`IAMS_SECRET_KEY` and `IAMS_FIELD_ENCRYPTION_KEY` with placeholders. Losing the
encryption key makes every stored bank account number and PAN permanently
unreadable, so guard the copy as above and keep that key wherever you keep the
database password.

Generate a real secret key before any shared deployment (the app refuses to
start in production with the development default):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
# put the result in backend/.env as IAMS_SECRET_KEY
```

Apply migrations, then create the first administrator:

```powershell
alembic upgrade head
python -m app.cli bootstrap-admin --username admin --email hr@ishvaratech.com
```

`bootstrap-admin` seeds the permission, role and settings catalogues, creates one
HR/Admin account, and **prints a temporary password once**. It is not stored in
plaintext anywhere and you must change it at first sign-in. The command refuses
to run if an active HR/Admin account already exists, so it cannot quietly add a
second administrator to a live system.

Run the API:

```powershell
uvicorn app.main:app --reload --port 8001
```

- Swagger UI -- <http://localhost:8001/api/docs>
- ReDoc -- <http://localhost:8001/api/redoc>
- OpenAPI schema -- <http://localhost:8001/api/openapi.json>
- Health -- <http://localhost:8001/health> and `/health/ready`

## 3. Frontend

```powershell
cd ..\frontend
npm ci
npm run dev
```

`npm ci` rather than `npm install`: it installs exactly what
`package-lock.json` says and fails if the lock file and `package.json`
disagree, which is what makes two machines match. Use `npm install` only when
deliberately adding or upgrading a package - it rewrites the lock file, and
that change belongs in the same commit.

Open <http://localhost:5173>. The dev server proxies `/api` and `/health` to
`localhost:8001`, so the browser sees a single origin. Override the target with
`VITE_API_PROXY_TARGET` if the backend runs elsewhere.

Sign in with the username and temporary password from `bootstrap-admin`. You will
be sent straight to the change-password screen; after changing it, all sessions
are revoked and you sign in again -- which is the intended behaviour.

## 4. Tests

### Backend

The suite runs against a **separate** test database so it never truncates data
you are working with:

```powershell
cd ..
docker compose --profile test up -d postgres-test   # host port 5434, in-memory
cd backend
pytest
```

The fixture drops and recreates the schema **by running the migrations**, so
every run also checks that migrations work and match the models. Isolation is by
truncation between tests, not a wrapping rollback -- some production code paths
deliberately commit in their own transaction (durable login-failure records), and
a rollback fixture would discard exactly the behaviour those tests verify.

Useful selections:

```powershell
pytest -m unit            # pure business rules, no database
pytest -m integration     # API and database
pytest --cov=app
```

### Frontend

```powershell
cd ..\frontend
npm test            # vitest
npm run typecheck   # tsc --noEmit, strict
npm run lint        # eslint
npm run build       # production build
```

## 5. Useful commands

```powershell
# Backend
python -m app.cli check-db              # connectivity + current migration revision
python -m app.cli config-status         # unconfirmed / unverified settings; non-zero exit if any
python -m app.cli seed-permissions      # reconcile the RBAC catalogue (idempotent)
python -m app.cli seed-settings         # reconcile the settings catalogue (never overwrites values)
python -m app.cli benchmark-hash        # time Argon2id with the configured parameters
python -m app.cli purge-expired-sessions --older-than-days 30
python -m app.cli accrue-leave           # credit one accrual period; safe to re-run
python -m app.cli accrue-leave --as-of 2026-11-30   # catch up a missed period
python -m app.cli grant-compoff          # credit qualifying work; safe to re-run
python -m app.cli expire-compoff         # close comp-off credits past their date
python -m app.cli close-leave-year --leave-year 2026        # dry run by default
python -m app.cli close-leave-year --leave-year 2026 --apply
python -m app.cli expire-carried-leave   # lapse carried leave that reached its date
python -m app.cli notify-unsettled-attendance              # ask for corrections on
python -m app.cli notify-unsettled-attendance --days-back 14   # unsettled days
python -m app.cli run-announcement-schedule   # notify about announcements now due
# Payroll has no scheduled job on purpose: a run is opened, calculated,
# reviewed, approved and finalized by people, and automating any of those
# steps would defeat the separation of duties they exist to create.
python -m app.cli purge-expired-data     # dry run; --apply to delete
python -m app.cli purge-expired-data --category audit-logs --apply
python -m app.cli sync-devices           # poll pollable devices once; safe to re-run
python -m app.cli sync-devices --force   # poll even while devices.auto_sync_enabled is off

alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
alembic check                           # fails if models and schema have diverged
alembic history --verbose

ruff check app tests && ruff format app tests

# Tests. Four xdist workers, each with its own database; `-n 0` runs serially
# when you want a readable traceback. Unit tests need no database at all.
pytest                      # everything, ~2m45s
pytest tests/unit           # pure rules only, under a second
pytest -n 0 -k some_test    # one test, serially
```

## 6. Resetting

```powershell
# Wipe the development database entirely and start over.
docker compose down -v
docker compose up -d postgres
cd backend
alembic upgrade head
python -m app.cli bootstrap-admin --username admin --email hr@ishvaratech.com
```

## Troubleshooting

**`Connection failed` from `check-db`** -- the container is not running, or
something else holds port 5433. `docker compose ps`, then
`docker compose logs postgres`.

**`alembic upgrade` reports a missing relation** -- the database was created by an
older revision. `docker compose down -v` and start from step 1.

**Pytest exits with "does not look like a test database"** -- the guard in
`tests/conftest.py` fired. It refuses to run against anything that is not
port 5434 or named `iams_test`, so the suite cannot truncate your development
data. Start `postgres-test`, or set `IAMS_TEST_DATABASE_URL`.

**403 `password_change_required` on every endpoint** -- expected: the account
still has an HR-issued temporary password. Call
`POST /api/v1/auth/me/change-password`, or use the UI, which redirects there.

**Swagger UI loads blank** -- the API sends a deny-all Content-Security-Policy and
the docs pages get a looser one. If you add a route that serves HTML, add it to
`DOC_PATHS` in `app/main.py`.

**npm reports `EBADENGINE`** for `jsdom`'s transitive dependencies -- they ask for
Node >= 22.22.2. The tests pass on 22.21.1; upgrade Node to silence it.
