"""Shared pytest fixtures.

Database strategy
-----------------
The suite runs against a **real PostgreSQL instance** - by default the
``iams_test`` database on the locally installed server - not SQLite. The
application relies on PostgreSQL-specific behaviour: JSONB columns, partial
indexes, ``TIMESTAMPTZ`` semantics, ``NULLS NOT DISTINCT`` unique constraints
and plpgsql triggers. A SQLite stand-in would pass tests that production would
fail.

The schema is created by running **the Alembic migrations**, not
``Base.metadata.create_all``. That makes every test run a check that the
migrations themselves work and stay in step with the models.

Isolation is by truncation between tests rather than by wrapping each test in a
rollback. Some production code paths deliberately commit in their own
transaction - failed sign-ins must survive the request rollback to make rate
limiting work - and a rollback-based fixture would silently discard exactly the
behaviour those tests need to verify.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

# Environment must be set before any application module reads settings.
os.environ["IAMS_APP_ENV"] = "test"
os.environ["IAMS_DEBUG"] = "false"
# The **test** database, which is never the development one: the suite truncates
# tables between tests and creates a database per parallel worker, so pointing
# it at `iams` would empty it on every run. Override with
# IAMS_TEST_DATABASE_URL to run against a different server.
_BASE_TEST_URL = os.environ.get(
    "IAMS_TEST_DATABASE_URL",
    "postgresql+psycopg://iams_owner:iams_local_dev@localhost:5432/iams_test",
)

# Under pytest-xdist each worker gets its **own database**, because the suite
# isolates tests by truncating tables - two workers sharing one database would
# truncate each other's rows mid-test. The worker name is in the environment
# before this module is imported, which is the only point early enough to change
# the URL before the settings cache is populated.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
os.environ["IAMS_DATABASE_URL"] = f"{_BASE_TEST_URL}_{_WORKER}" if _WORKER else _BASE_TEST_URL
os.environ["IAMS_SECRET_KEY"] = "test-only-secret-key-at-least-32-characters-long"
# A small pool per worker. The production default (5 + 10 overflow) multiplied
# by eight xdist workers exceeds PostgreSQL's default max_connections of 100,
# and the failure surfaces as an unrelated-looking error in whichever statement
# happens to need a connection next. Tests are sequential within a worker, so
# two connections plus a little headroom is ample.
os.environ["IAMS_DB_POOL_SIZE"] = "2"
os.environ["IAMS_DB_MAX_OVERFLOW"] = "3"
# Argon2 at production cost would dominate the suite's runtime. These values are
# used only here; tests that care about hashing assert on behaviour, not cost.
os.environ["IAMS_ARGON2_TIME_COST"] = "1"
os.environ["IAMS_ARGON2_MEMORY_COST_KIB"] = "8192"
os.environ["IAMS_ARGON2_PARALLELISM"] = "1"
os.environ["IAMS_LOGIN_MAX_ATTEMPTS"] = "5"
os.environ["IAMS_LOGIN_LOCKOUT_MINUTES"] = "15"
os.environ["IAMS_LOGIN_ATTEMPT_WINDOW_MINUTES"] = "15"
# Field encryption must be configured or bank/PAN tests cannot run at all.
os.environ["IAMS_FIELD_ENCRYPTION_KEY"] = "jaCdBscJoc7ZnmehRK2l-k5u90WiHhTxhr9JtyRvpWs="
# Uploads go to a throwaway directory, never the development storage folder.
os.environ["IAMS_STORAGE_DIR"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_tmp_storage"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.permissions import ROLE_EMPLOYEE, ROLE_HR_ADMIN  # noqa: E402
from app.db.registry import Base  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.audit.service import AuditContext  # noqa: E402
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.auth.models import User  # noqa: E402
from app.modules.settings import service as settings_service  # noqa: E402

TEST_PASSWORD = "TestPassw0rd!2026"


def _ensure_worker_database() -> None:
    """Create this xdist worker's own database if it does not exist yet.

    Connects to the server's default database to issue ``CREATE DATABASE``,
    which cannot run inside a transaction - hence autocommit. A worker database
    left over from an earlier run is reused; its schema is dropped and rebuilt
    either way.
    """
    from sqlalchemy import create_engine

    settings = get_settings()
    target = settings.sqlalchemy_url.rsplit("/", 1)[-1]
    admin_url = settings.sqlalchemy_url.rsplit("/", 1)[0] + "/postgres"

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        # Workers start together and PostgreSQL serialises CREATE DATABASE
        # against its template, so simultaneous attempts fail with "source
        # database is being accessed by other users". Retried with a short
        # backoff rather than left to flake, and treated as success if another
        # worker got there first.
        for attempt in range(12):
            with admin.connect() as connection:
                exists = connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": target},
                ).scalar()
                if exists:
                    return
                try:
                    connection.execute(text(f'CREATE DATABASE "{target}"'))
                    return
                except Exception:
                    if attempt == 11:
                        raise
            time.sleep(0.5 + attempt * 0.25)
    finally:
        admin.dispose()


def _run_migrations() -> None:
    """Apply migrations to the test database from a clean slate."""
    from alembic.config import Config

    from alembic import command

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(backend_dir, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url)
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    """Build the test schema once per session.

    **Not** autouse, deliberately. The unit tests are pure functions over values
    and never touch a database; making every one of them wait for a full
    migration run, and then truncate fifty tables afterwards, made the suite
    several times slower than the work it was doing. Only the fixtures that
    actually need a database depend on this, so `pytest tests/unit` now needs no
    database at all.
    """
    settings = get_settings()
    if "5434" not in settings.sqlalchemy_url and "iams_test" not in settings.sqlalchemy_url:
        pytest.exit(
            "Refusing to run: the test database URL does not look like a test "
            f"database ({settings.sqlalchemy_url.split('@')[-1]}). "
            "Set IAMS_TEST_DATABASE_URL.",
            returncode=1,
        )

    if _WORKER:
        _ensure_worker_database()

    engine = get_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.exit(
            "Cannot reach the test database. Start it with:\n"
            "  docker compose --profile test up -d postgres-test\n"
            f"({type(exc).__name__})",
            returncode=1,
        )

    # Drop and rebuild so a schema left over from an older revision cannot make
    # the suite pass or fail for the wrong reason.
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    _run_migrations()
    yield
    engine.dispose()


#: Tables holding seeded reference data rather than test state.
#:
#: The permission catalogue - 80 permissions, 2 roles and 102 grants - is fixed,
#: and no test rewrites it. Keeping it out of truncation lets it be seeded once
#: per session instead of ~184 inserts before every single test, which was the
#: largest single cost in the integration suite.
#:
#: ``system_settings`` is deliberately **not** here. Several tests exercise
#: seeding itself or change a setting and read the effect, so settings must be
#: rebuilt per test - 57 rows, which is the cheaper half of the work.
REFERENCE_TABLES = frozenset({"permissions", "roles", "role_permissions"})

#: Fixtures that put rows in the database. A test using any of them - directly
#: or transitively - needs truncation afterwards; one that uses none of them has
#: left nothing behind to clean.
DATABASE_FIXTURES = frozenset(
    {
        "database",
        "session",
        "seeded",
        "client",
        "admin_user",
        "employee_user",
        "pending_user",
        "org_fixtures",
        "employee_record",
    }
)


@pytest.fixture(autouse=True)
def clean_tables(request: pytest.FixtureRequest) -> Iterator[None]:
    """Truncate every application table after a test that used the database.

    ``alembic_version`` is excluded so the schema is not re-migrated, and
    ``RESTART IDENTITY`` resets sequences so ids are predictable per test.

    Skipped entirely for a test that requested no database fixture. Truncating
    fifty-one tables after a pure arithmetic test is pure waste, and it was the
    single largest cost in the suite.
    """
    yield

    _TOKEN_CACHE.clear()

    if DATABASE_FIXTURES.isdisjoint(request.fixturenames):
        return

    engine = get_engine()
    with engine.begin() as connection:
        tables = [
            table.name
            for table in reversed(Base.metadata.sorted_tables)
            if table.name not in REFERENCE_TABLES
        ]
        connection.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(f'"{name}"' for name in tables)
                + " RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def session(database: None) -> Iterator[Session]:
    """A plain session for service-level tests."""
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="session")
def _permission_catalogue(database: None) -> None:
    """Seed the permission catalogue once for the whole session.

    It is reference data, not test state: 80 permissions, 2 roles and 102 grants
    that no test rewrites. Seeding it per test was the largest single cost in the
    integration suite.
    """
    db = get_session_factory()()
    try:
        auth_service.seed_permissions(db)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seeded(session: Session, _permission_catalogue: None) -> None:
    """Permissions, roles and the settings catalogue, as a fresh install would have.

    The permission catalogue comes from the session fixture; settings are seeded
    per test because several tests exercise seeding itself or change a value and
    read the effect.
    """
    settings_service.seed_settings(session)
    session.commit()


@pytest.fixture
def cli_context() -> AuditContext:
    return AuditContext(actor_label="test", ip_address="127.0.0.1", user_agent="pytest")


@pytest.fixture
def admin_user(session: Session, seeded: None, cli_context: AuditContext) -> User:
    result = auth_service.create_user(
        session,
        username="hradmin",
        email="hradmin@example.com",
        role_codes=[ROLE_HR_ADMIN],
        password=TEST_PASSWORD,
        must_change_password=False,
        context=cli_context,
    )
    session.commit()
    return result.user


@pytest.fixture
def employee_user(session: Session, seeded: None, cli_context: AuditContext) -> User:
    result = auth_service.create_user(
        session,
        username="e.employee",
        email="employee@example.com",
        role_codes=[ROLE_EMPLOYEE],
        password=TEST_PASSWORD,
        must_change_password=False,
        context=cli_context,
    )
    session.commit()
    return result.user


@pytest.fixture
def pending_user(session: Session, seeded: None, cli_context: AuditContext) -> User:
    """An employee who has not yet replaced their temporary password."""
    result = auth_service.create_user(
        session,
        username="newjoiner",
        email="newjoiner@example.com",
        role_codes=[ROLE_EMPLOYEE],
        password=TEST_PASSWORD,
        must_change_password=True,
        context=cli_context,
    )
    session.commit()
    return result.user


@pytest.fixture
def client(database: None) -> Iterator[TestClient]:
    """HTTP client against the real application, with real dependencies.

    Nothing is stubbed: requests go through the actual middleware, dependency
    chain and database, so a test that passes here exercises the same
    authorization path a browser would.
    """
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def org_fixtures(session: Session, cli_context: AuditContext) -> dict[str, int]:
    """A minimal organisation: one department, designation and work location."""
    from app.modules.org import service as org_service

    department = org_service.create_department(
        session, data={"code": "ENG", "name": "Engineering"}, context=cli_context
    )
    designation = org_service.create_designation(
        session,
        data={"code": "SE2", "name": "Software Engineer II", "level": 2},
        context=cli_context,
    )
    location = org_service.create_work_location(
        session,
        data={
            "code": "BLR",
            "name": "Bengaluru Office",
            "timezone": "Asia/Kolkata",
            "state": "Karnataka",
        },
        context=cli_context,
    )
    session.commit()
    return {
        "department_id": department.id,
        "designation_id": designation.id,
        "work_location_id": location.id,
    }


@pytest.fixture
def employee_record(session: Session, org_fixtures: dict[str, int], cli_context: AuditContext):
    """An employee linked to the ``employee_user`` account.

    Linked on purpose: most self-service and team-scoping behaviour only exists
    once an account has an employee record behind it.
    """
    from datetime import date

    from app.modules.employees import service as employee_service

    employee = employee_service.create_employee(
        session,
        data={
            "employee_code": "ITC0001",
            "first_name": "Ravi",
            "last_name": "Sharma",
            "work_email": "ravi.sharma@example.com",
            "employment_type": "full_time",
            "employment_status": "active",
            "date_of_joining": date(2026, 1, 5),
            **org_fixtures,
        },
        context=cli_context,
    )
    session.commit()
    return employee


def login(client: TestClient, identifier: str, password: str = TEST_PASSWORD) -> dict:
    """Sign in and return the token payload. Fails loudly on an unexpected error."""
    response = client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


#: Access tokens already obtained during the current test, keyed by identity.
#:
#: Cleared between tests by the ``clean_tables`` fixture, because the accounts
#: themselves are truncated away. Within a test the token stays valid, and a
#: sign-in costs an Argon2 hash plus a round trip - tests that call
#: ``auth_header`` a dozen times were paying that each time for nothing.
_TOKEN_CACHE: dict[tuple[str, str], str] = {}


def auth_header(client: TestClient, identifier: str, password: str = TEST_PASSWORD) -> dict:
    """An Authorization header for this identity, signing in once per test."""
    key = (identifier, password)
    token = _TOKEN_CACHE.get(key)
    if token is None:
        token = login(client, identifier, password)["access_token"]
        _TOKEN_CACHE[key] = token
    return {"Authorization": f"Bearer {token}"}


def forget_tokens() -> None:
    """Drop cached tokens. Called between tests, and by a test that re-creates a user."""
    _TOKEN_CACHE.clear()
