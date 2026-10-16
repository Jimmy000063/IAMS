-- One-time setup for a locally installed PostgreSQL, in place of the
-- docker-compose containers.
--
-- Run it as a superuser, connected to any database:
--
--   psql -U postgres -h localhost -p 5432 -f infra/postgres/local-setup.sql
--
-- It creates four things:
--
--   * `iams_owner`  - owns the schema. Alembic connects as this role, because
--                     changing the schema is what it does.
--   * `iams_app`    - the application at runtime. It can read and write rows
--                     but cannot drop a table, which limits what an injected
--                     statement or a compromised process can do. The migrations
--                     additionally revoke DELETE on the tables whose rows must
--                     never disappear - audit entries, ledger rows, salary
--                     revisions, finalized payroll.
--   * `iams_readonly` - reporting and inspection. SELECT only.
--   * the `iams` and `iams_test` databases.
--
-- `iams_test` is separate on purpose. The test suite truncates tables between
-- tests and creates one database per parallel worker; pointing it at the
-- development database would empty that database every time the suite ran.
--
-- The passwords below are development placeholders and are safe to change -
-- change them in `backend/.env` at the same time. They are not secrets worth
-- protecting on a developer's own machine, and this file is for local use only;
-- a deployment should set them from its own secret store and never from a file
-- in the repository.

\set ON_ERROR_STOP on

-- Roles ---------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_owner') THEN
        -- CREATEDB so this role can create its own test databases, which the
        -- parallel test runner needs.
        CREATE ROLE iams_owner LOGIN CREATEDB PASSWORD 'iams_local_dev';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_app') THEN
        CREATE ROLE iams_app LOGIN PASSWORD 'iams_app_local_dev';
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_readonly') THEN
        CREATE ROLE iams_readonly LOGIN PASSWORD 'iams_readonly_local_dev';
    END IF;
END
$$;

-- Databases -----------------------------------------------------------------
--
-- CREATE DATABASE cannot run inside a transaction or a DO block, so these use
-- \gexec: the SELECT produces the statement only when the database is absent,
-- and \gexec runs whatever the query returned.

SELECT 'CREATE DATABASE iams OWNER iams_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'iams')
\gexec

SELECT 'CREATE DATABASE iams_test OWNER iams_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'iams_test')
\gexec

-- Per-database grants -------------------------------------------------------

GRANT CONNECT ON DATABASE iams TO iams_app, iams_readonly;
GRANT CONNECT ON DATABASE iams_test TO iams_app, iams_readonly;

\echo ''
\echo 'Roles and databases are in place. Now run the per-database grants:'
\echo '  psql -U postgres -h localhost -p 5432 -d iams      -f infra/postgres/local-grants.sql'
\echo '  psql -U postgres -h localhost -p 5432 -d iams_test -f infra/postgres/local-grants.sql'
\echo ''
