"""Liveness and readiness endpoints.

Both are unauthenticated and deliberately terse. They report whether the service
can serve traffic, and nothing about its internals - no version of PostgreSQL, no
connection string, no migration SQL - because that detail is useful to an
attacker and not to a load balancer.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.core.deps import DbSession
from app.core.timeutil import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


class LivenessResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    server_time: str


class ReadinessResponse(BaseModel):
    status: str
    database: str
    migrations: str


@router.get("/health", response_model=LivenessResponse, summary="Liveness probe")
def liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        server_time=utc_now().isoformat(),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe including database connectivity",
    responses={503: {"description": "A dependency is unavailable"}},
)
def readiness(session: DbSession, response: Response) -> ReadinessResponse:
    """Checks that the database answers and that migrations have been applied.

    An empty ``alembic_version`` table means the schema was never migrated, which
    would surface later as confusing "relation does not exist" errors.
    """
    database = "ok"
    migrations = "ok"

    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check: database unreachable")
        database = "unavailable"
        migrations = "unknown"
    else:
        try:
            revision = session.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).scalar()
            migrations = "ok" if revision else "not_applied"
        except Exception:
            migrations = "not_applied"

    ready = database == "ok" and migrations == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        database=database,
        migrations=migrations,
    )
