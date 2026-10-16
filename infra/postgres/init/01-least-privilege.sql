-- Database initialisation, run once when the data volume is first created.
--
-- Purpose: provide a least-privilege role for the application at runtime,
-- separate from the owner role that Alembic uses to change the schema. The
-- application can read and write rows but cannot drop a table, which limits the
-- damage an injected statement or a compromised application process can do.
--
-- Local development uses the owner role for convenience (one less thing to
-- configure). Staging and production should point IAMS_DATABASE_URL at
-- `iams_app` and run migrations separately as the owner.

-- Application runtime role. No CREATEDB, no CREATEROLE, no superuser.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_app') THEN
        CREATE ROLE iams_app LOGIN PASSWORD 'iams_app_local_dev';
    END IF;
END
$$;

-- Read-only role for reporting and ad-hoc analysis.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'iams_readonly') THEN
        CREATE ROLE iams_readonly LOGIN PASSWORD 'iams_readonly_local_dev';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE iams TO iams_app, iams_readonly;
GRANT USAGE ON SCHEMA public TO iams_app, iams_readonly;

-- Rights on tables that already exist...
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO iams_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO iams_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO iams_readonly;

-- ...and on tables migrations create later. Without these defaults, every new
-- Alembic revision would silently leave its table unreadable by the app role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO iams_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO iams_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO iams_readonly;

-- Nobody but the owner may create objects in `public`.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
