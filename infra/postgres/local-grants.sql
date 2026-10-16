-- Schema grants, run once per database, connected **to that database**.
--
--   psql -U postgres -h localhost -p 5432 -d iams      -f infra/postgres/local-grants.sql
--   psql -U postgres -h localhost -p 5432 -d iams_test -f infra/postgres/local-grants.sql
--
-- Separate from `local-setup.sql` because `GRANT ... ON SCHEMA` and
-- `ALTER DEFAULT PRIVILEGES` act on the database you are connected to, not on
-- the one you name. Running them against `postgres` would grant rights on the
-- wrong database and look as though it had worked.

\set ON_ERROR_STOP on

-- The schema belongs to the owner role, so Alembic can create and alter tables
-- in it.
ALTER SCHEMA public OWNER TO iams_owner;

GRANT USAGE ON SCHEMA public TO iams_app, iams_readonly;

-- Rights on tables that already exist...
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO iams_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO iams_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO iams_readonly;

-- ...and on tables a later migration creates. Without these defaults, every new
-- Alembic revision would silently leave its table unreadable by the app role.
--
-- FOR ROLE iams_owner is the important part: default privileges apply to
-- objects created *by the role named here*, and migrations run as the owner. A
-- plain ALTER DEFAULT PRIVILEGES would apply to objects created by whoever ran
-- this script instead, which is nobody.
ALTER DEFAULT PRIVILEGES FOR ROLE iams_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO iams_app;
ALTER DEFAULT PRIVILEGES FOR ROLE iams_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO iams_app;
ALTER DEFAULT PRIVILEGES FOR ROLE iams_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO iams_readonly;

-- Nobody but the owner may create objects in `public`.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

\echo 'Grants applied to the current database.'
