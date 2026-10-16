"""Request and response models for the audit viewer and retention report."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    entity_type: str | None
    entity_id: str | None
    actor_user_id: int | None
    actor_label: str | None = Field(
        description="Who acted, as recorded at the time - not resolved now, so a "
        "renamed or deleted account still reads correctly."
    )
    actor_ip: str | None
    request_id: str | None = Field(
        description="Ties an entry to the request that produced it, and to the "
        "error a user may have quoted."
    )
    before_value: Any | None
    after_value: Any | None
    context: Any | None
    succeeded: bool
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntryResponse]
    page: int
    page_size: int
    total: int
    pages: int


class JobRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_name: str
    idempotency_key: str | None = Field(
        description="What makes a re-run safe: the same key cannot be inserted twice."
    )
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    period_key: str | None
    records_processed: int
    records_skipped: int
    records_failed: int
    result: Any | None
    error_message: str | None


class RetentionReportResponse(BaseModel):
    as_of: datetime
    categories: list[dict[str, Any]]
    unconfirmed: list[str] = Field(
        description="Retention periods nobody has confirmed yet. They are company "
        "decisions with legal consequences in both directions."
    )
    note: str
