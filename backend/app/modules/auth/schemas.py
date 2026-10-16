"""Request and response models for the authentication module."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"identifier": "admin", "password": "ChangeMe!2026Now"}]}
    )

    identifier: str = Field(
        min_length=1,
        max_length=255,
        description="Username, employee code or work email address.",
    )
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime = Field(description="UTC expiry of the access token")
    refresh_expires_at: datetime = Field(description="UTC expiry of the refresh token")
    must_change_password: bool = Field(
        description="When true, the client must send the user to the change-password screen."
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None,
        max_length=512,
        description="Session to revoke. Omit to revoke every session for this user.",
    )
    all_sessions: bool = False


class RoleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class CurrentUserResponse(BaseModel):
    """Everything the frontend needs to render navigation for the signed-in user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    employee_id: int | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    roles: list[RoleSummary]
    permissions: list[str] = Field(
        description="Permission codes held via all active roles, for UI affordances only."
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(
        min_length=12,
        max_length=128,
        description="Must contain upper and lower case, a digit and a symbol.",
    )


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr | None = None
    role_codes: list[str] = Field(min_length=1)
    employee_id: int | None = None


class CreatedUserResponse(BaseModel):
    id: int
    username: str
    email: str | None
    roles: list[str]
    temporary_password: str = Field(
        description=(
            "Shown once and never stored in plaintext. Deliver it to the employee "
            "out of band; they must change it at first sign-in."
        )
    )


class ResetPasswordResponse(BaseModel):
    user_id: int
    username: str
    temporary_password: str = Field(description="Shown once. Deliver out of band.")


class SetActiveRequest(BaseModel):
    is_active: bool
    reason: str | None = Field(default=None, max_length=500)


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    module: str
    description: str


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str
    is_system: bool
    is_active: bool
    permissions: list[str]
