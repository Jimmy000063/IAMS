"""Domain exceptions and a single consistent error response envelope.

Every error the API returns has the shape::

    {
      "error": {
        "code": "leave_balance_insufficient",
        "message": "Casual Leave balance is insufficient for this request.",
        "details": [{"field": "from_date", "message": "..."}]
      },
      "request_id": "0f1c..."
    }

``code`` is a stable machine-readable identifier the frontend can branch on;
``message`` is safe to display. Internal exception text is never exposed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for all expected, client-facing failures."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"
    message: str = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or []
        self.status_code = status_code or self.status_code
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationFailed(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The submitted data is not valid."


class AuthenticationFailed(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"
    message = "Invalid credentials."


class NotAuthenticated(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "not_authenticated"
    message = "Authentication is required."


class PermissionDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class PasswordChangeRequired(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "password_change_required"
    message = "You must change your temporary password before continuing."


class AccountInactive(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_inactive"
    message = "This account is not active. Contact HR."


class AccountLocked(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "account_locked"
    message = "Too many failed sign-in attempts. Try again later."


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested record was not found."


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state of the record."


class PolicyViolation(AppError):
    """A business rule forbids this operation (balance, window, approval order)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "policy_violation"
    message = "This action is not permitted by the configured policy."


class ConfigurationMissing(AppError):
    """A required policy or statutory value has not been configured or verified.

    Raised instead of guessing. Payroll and accrual code paths depend on this to
    surface gaps rather than produce a plausible-looking wrong number.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "configuration_missing"
    message = "A required configuration value is missing or unverified."


def _envelope(request: Request, payload: dict[str, Any], status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(
            {"error": payload, "request_id": getattr(request.state, "request_id", None)}
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Unhandled application error: %s", exc.code, exc_info=exc)
        return _envelope(request, exc.to_payload(), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or None,
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return _envelope(
            request,
            {
                "code": "validation_error",
                "message": "The submitted data is not valid.",
                "details": details,
            },
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        generic = {
            401: "not_authenticated",
            403: "permission_denied",
            404: "not_found",
            405: "method_not_allowed",
            429: "rate_limited",
        }
        return _envelope(
            request,
            {
                "code": generic.get(exc.status_code, "http_error"),
                "message": str(exc.detail),
                "details": [],
            },
            exc.status_code,
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # A unique/foreign-key violation that reached this far is a race or a
        # missing pre-check. Log the detail, return a generic conflict: database
        # constraint text can disclose schema and column values.
        logger.warning("Database integrity error", exc_info=exc)
        return _envelope(
            request,
            {
                "code": "conflict",
                "message": "The request conflicts with an existing record.",
                "details": [],
            },
            status.HTTP_409_CONFLICT,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return _envelope(
            request,
            {
                "code": "internal_error",
                "message": "An unexpected error occurred. The incident has been logged.",
                "details": [],
            },
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
