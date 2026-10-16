"""FastAPI application factory.

Run locally with::

    uvicorn app.main:app --reload --port 8000

Interactive API documentation is served at ``/api/docs`` (Swagger UI) and
``/api/redoc``, from the OpenAPI schema at ``/api/openapi.json``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.permissions import validate_catalogue as validate_permissions
from app.modules.attendance.router import router as attendance_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.compoff.router import router as compoff_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.devices.adapters.base import load_builtin_adapters
from app.modules.devices.router import router as devices_router
from app.modules.employees.router import router as employees_router
from app.modules.health.router import router as health_router
from app.modules.holidays.router import router as holidays_router
from app.modules.leave.router import router as leave_router
from app.modules.notifications.router import router as notifications_router
from app.modules.org.router import router as org_router
from app.modules.payroll.router import router as payroll_router
from app.modules.reports.router import router as reports_router
from app.modules.settings.catalogue import validate_catalogue as validate_settings_catalogue
from app.modules.settings.router import router as settings_router
from app.modules.shifts.router import router as shifts_router

logger = logging.getLogger(__name__)

API_DESCRIPTION = """
Human Resource Management System for **Ishvara Tech Consulting LLP**.

Covers employee management, attendance, shifts, leave, comp-off, holidays,
Indian payroll, announcements, notifications and reporting.

### Authentication

Call `POST /api/v1/auth/login` with a username, employee code or work email to
receive an access token and a refresh token. Send the access token as
`Authorization: Bearer <token>` on every subsequent request. Access tokens are
short-lived; use `POST /api/v1/auth/refresh` to rotate.

There is no public registration. HR/Admin creates accounts, and an employee must
replace their temporary password at first sign-in - until they do, protected
endpoints return `403 password_change_required`.

### Authorization

Every protected endpoint declares the permission codes it requires, listed in its
description. Authorization is enforced server-side; the response is
`403 permission_denied` when a required permission is absent.

### Errors

All failures share one envelope:

```json
{
  "error": {"code": "policy_violation", "message": "...", "details": []},
  "request_id": "a1b2c3d4"
}
```

`code` is stable and safe to branch on. `request_id` also appears in the
`X-Request-ID` response header and in server logs.
"""

#: Documentation pages, which need a looser Content-Security-Policy than the API.
DOC_PATHS = frozenset({"/api/docs", "/api/redoc", "/api/docs/oauth2-redirect"})

OPENAPI_TAGS = [
    {"name": "Health", "description": "Liveness and readiness probes."},
    {"name": "Authentication", "description": "Sign-in, sessions and account provisioning."},
    {
        "name": "Organisation",
        "description": (
            "Departments, designations, work locations and employee groups. A work "
            "location's timezone governs how attendance dates are derived for its "
            "employees."
        ),
    },
    {
        "name": "Employees",
        "description": (
            "Employee lifecycle and records. Statutory identifiers and bank details "
            "sit behind separate permissions and are never returned in full. "
            "Records outside the caller's access scope return 404, not 403."
        ),
    },
    {
        "name": "Shifts & Calendars",
        "description": (
            "Shifts, effective-dated shift assignments, weekly-off patterns and "
            "work calendars. The calendar is the single source of 'is this a "
            "working day' for both attendance and leave."
        ),
    },
    {
        "name": "Attendance",
        "description": (
            "Web punching and the attendance engine. Punch timestamps are "
            "server-generated; a punch is attributed to the shift date in the "
            "employee's work-location timezone, which for an overnight shift is "
            "the date the shift started.\n\n"
            "Raw events are immutable. `attendance_days` is derived and can be "
            "recomputed at any time, and every row carries the policy snapshot "
            "that produced it."
        ),
    },
    {
        "name": "Dashboards",
        "description": (
            "Aggregated overviews. Metrics awaiting a later milestone are listed "
            "under `not_yet_available` rather than returned as zero."
        ),
    },
    {
        "name": "Settings",
        "description": (
            "Company-wide business policy configuration. Settings marked "
            "`is_confirmed: false` are placeholder defaults, not company decisions."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration before accepting traffic."""
    settings: Settings = get_settings()

    # A typo in a permission or settings declaration must stop startup, not
    # produce a route that nobody can reach or a setting nobody can save.
    validate_permissions()
    validate_settings_catalogue()

    if settings.is_production:
        settings.assert_production_safe()

    logger.info(
        "%s %s starting in '%s' environment",
        settings.app_name,
        settings.app_version,
        settings.app_env,
    )
    yield
    logger.info("Shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(debug=settings.debug)

    # Device adapters register themselves on import. Done here rather than in
    # the lifespan so the registry is populated even when the app is built
    # without running startup, as tests do.
    load_builtin_adapters()

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        version=settings.app_version,
        openapi_tags=OPENAPI_TAGS,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url="/api/redoc" if settings.docs_enabled else None,
        lifespan=lifespan,
        contact={"name": "Ishvara Tech Consulting LLP"},
    )

    # CORS is restricted to the configured frontend origins and allows
    # credentials, so a wildcard origin is never acceptable here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Attach a request id and baseline security headers.

        The request id is accepted from the client when supplied so that a
        frontend trace can be correlated, but it is truncated and never trusted
        for anything beyond log correlation.
        """
        incoming = (request.headers.get("x-request-id") or "").strip()[:40]
        request_id = incoming or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"

        # The API itself serves JSON only, so it gets a deny-everything policy.
        # The Swagger UI and ReDoc pages are HTML that loads its bundle from a
        # CDN, so they need a policy that permits it - applying the API policy
        # there would leave a blank documentation page.
        if request.url.path in DOC_PATHS:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' https://fastapi.tiangolo.com data:; "
                "font-src 'self' https://cdn.jsdelivr.net; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    register_exception_handlers(app)

    prefix = settings.api_v1_prefix
    app.include_router(health_router)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(org_router, prefix=prefix)
    app.include_router(employees_router, prefix=prefix)
    app.include_router(shifts_router, prefix=prefix)
    app.include_router(attendance_router, prefix=prefix)
    app.include_router(devices_router, prefix=prefix)
    app.include_router(leave_router, prefix=prefix)
    app.include_router(compoff_router, prefix=prefix)
    app.include_router(holidays_router, prefix=prefix)
    app.include_router(notifications_router, prefix=prefix)
    app.include_router(payroll_router, prefix=prefix)
    app.include_router(reports_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(settings_router, prefix=prefix)

    return app


app = create_app()
