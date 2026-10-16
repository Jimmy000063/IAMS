"""Database engine and session management.

Transaction boundary policy: one request == one transaction. The
:func:`get_db_session` dependency commits on a clean return and rolls back on any
exception, so a handler that raises mid-way can never leave a half-written leave
ledger or payroll run behind.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()

    #
    # Session parameters, sent to libpq as startup options rather than issued
    # as `SET` statements after connecting.
    #
    # This is not a style preference. psycopg opens an implicit transaction on
    # the first statement of a connection, so a `SET` in a pool "connect"
    # handler runs inside one - and SQLAlchemy rolls the connection back when
    # it is returned to the pool, discarding the setting. It survives just long
    # enough to look correct if you check it immediately, which is how the
    # statement timeout below sat here for ten milestones without ever taking
    # effect. Startup options are connection properties and are not transactional.
    #
    options = [
        # Pin the session timezone instead of inheriting the server's.
        #
        # PostgreSQL renders every TIMESTAMPTZ in the session's TimeZone, and
        # psycopg attaches that zone to the datetime it returns. The instant is
        # the same either way, but the tzinfo is not: a server installed in
        # Asia/Kolkata hands back 09:32+05:30 where one in a UTC container
        # hands back 04:02+00:00 for the very same row.
        #
        # That makes behaviour depend on where the database happens to run.
        # Anything reading `.hour`, calling `.date()`, or formatting a
        # timestamp without converting first would quietly give a different
        # answer in development than in production - and attendance, overnight
        # shifts and payroll cut-offs all do exactly that. Work-location
        # timezones are applied deliberately in the policy layer; what moves
        # over the wire stays UTC.
        "-c timezone=UTC",
    ]
    if settings.db_statement_timeout_ms > 0:
        options.append(f"-c statement_timeout={int(settings.db_statement_timeout_ms)}")

    return create_engine(
        settings.sqlalchemy_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=settings.db_pool_pre_ping,
        connect_args={"options": " ".join(options)},
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def get_db_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for CLI commands and scheduled jobs."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop cached engine/session factory. Used by tests after changing settings."""
    get_engine.cache_clear()
    get_session_factory.cache_clear()
