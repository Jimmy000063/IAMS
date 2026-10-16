"""Request and response models for business policy settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SettingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    category: str
    label: str
    description: str
    value: Any = Field(description="Current effective value")
    default_value: Any = Field(description="Value the application ships with")
    value_type: str
    constraints: dict[str, Any] | None
    is_editable: bool
    is_confirmed: bool = Field(
        description="False means this is still a placeholder default, not a company decision."
    )
    requires_legal_review: bool = Field(
        description="True when the value has statutory consequences and must be verified."
    )
    source_reference: str | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> SettingResponse:
        return cls(
            key=row.key,
            category=row.category,
            label=row.label,
            description=row.description,
            value=row.unwrapped_value,
            default_value=(
                row.default_value.get("value")
                if isinstance(row.default_value, dict)
                else row.default_value
            ),
            value_type=row.value_type,
            constraints=row.constraints,
            is_editable=row.is_editable,
            is_confirmed=row.is_confirmed,
            requires_legal_review=row.requires_legal_review,
            source_reference=row.source_reference,
            updated_at=row.updated_at,
        )


class UpdateSettingRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "value": 20,
                    "reason": "Approved in the HR policy review of 10 October 2026",
                    "confirm": True,
                }
            ]
        }
    )

    value: Any = Field(description="New value, matching the setting's declared type")
    reason: str | None = Field(
        default=None, max_length=1000, description="Why the value is changing"
    )
    confirm: bool | None = Field(
        default=None,
        description="Mark the value as a confirmed company decision rather than a placeholder.",
    )
    source_reference: str | None = Field(
        default=None,
        max_length=1000,
        description=(
            "Authoritative source for the value. Required when confirming a "
            "setting that has statutory consequences."
        ),
    )


class SettingHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    setting_key: str
    old_value: Any
    new_value: Any
    changed_at: datetime
    changed_by_user_id: int | None
    change_reason: str | None


class ConfigurationHealthResponse(BaseModel):
    """Outstanding configuration decisions, surfaced rather than hidden."""

    total: int
    confirmed: int
    unconfirmed: int
    awaiting_legal_verification: int
    unconfirmed_keys: list[str]
    legal_review_keys: list[str]
