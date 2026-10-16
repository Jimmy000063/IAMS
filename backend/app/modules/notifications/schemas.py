"""Request and response models for announcements and the notification centre."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Priority = Literal["normal", "important", "critical"]
TargetType = Literal["all", "department", "work_location", "employee_group", "employee"]
AnnouncementStatus = Literal["draft", "scheduled", "published", "expired", "withdrawn"]


class TargetSpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: TargetType
    target_id: int | None = Field(
        default=None,
        description=(
            "The department, work location, group or employee. Omitted for "
            "'all'. Not a foreign key in the schema because it points at one of "
            "four tables; the id is validated against the right one on write."
        ),
    )

    @model_validator(mode="after")
    def _id_matches_type(self) -> TargetSpecIn:
        if self.target_type == "all" and self.target_id is not None:
            raise ValueError("An 'all' target takes no id.")
        if self.target_type != "all" and self.target_id is None:
            raise ValueError(f"A {self.target_type} target needs an id.")
        return self


class TargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    target_type: str
    target_id: int | None


class AnnouncementCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "Office closed on 2 October",
                    "body": "The Pune office will be closed. Work from home applies.",
                    "priority": "important",
                    "targets": [{"target_type": "work_location", "target_id": 1}],
                }
            ]
        },
    )

    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1)
    summary: str | None = Field(
        default=None,
        max_length=300,
        description="Shown in the notification and the feed card. Falls back to the body.",
    )
    priority: Priority = "normal"

    targets: list[TargetSpecIn] = Field(
        default_factory=lambda: [TargetSpecIn(target_type="all")],
        max_length=50,
        description="The audience, unioned. Defaults to everyone.",
    )

    publish_at: datetime | None = Field(
        default=None,
        description=(
            "When it becomes visible. A future time schedules it. Visibility is "
            "a query on this field, so a missed scheduler run delays the "
            "notification but never hides the announcement."
        ),
    )
    expires_at: datetime | None = Field(
        default=None, description="When it drops out of the feed. Null means it stays."
    )
    pin_until: date | None = Field(
        default=None, description="Keep it at the top of the feed until this date."
    )
    notify_on_publish: bool = Field(
        default=True,
        description="Put it in each recipient's notification centre when published.",
    )

    attachment_file_id: int | None = None
    image_file_id: int | None = None

    @field_validator("title", "body")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Must not be blank.")
        return stripped

    @model_validator(mode="after")
    def _expiry_after_publish(self) -> AnnouncementCreate:
        if (
            self.expires_at is not None
            and self.publish_at is not None
            and self.expires_at <= self.publish_at
        ):
            raise ValueError("The expiry must be after the publication time.")
        return self


class AnnouncementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    summary: str | None = Field(default=None, max_length=300)
    priority: Priority | None = None
    targets: list[TargetSpecIn] | None = Field(default=None, max_length=50)
    publish_at: datetime | None = None
    expires_at: datetime | None = None
    pin_until: date | None = None
    notify_on_publish: bool | None = None


class AnnouncementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    summary: str | None
    priority: str
    status: str
    publish_at: datetime | None
    expires_at: datetime | None
    published_at: datetime | None
    withdrawn_at: datetime | None
    withdrawal_reason: str | None
    pin_until: date | None
    notify_on_publish: bool
    notifications_sent_at: datetime | None
    recipient_count: int | None
    attachment_file_id: int | None
    image_file_id: int | None
    targets: list[TargetResponse]
    created_at: datetime


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_at: datetime | None = Field(
        default=None,
        description="Omit to publish now. A future time schedules it instead.",
    )


class PublishResponse(BaseModel):
    status: Literal["published", "scheduled"]
    publish_at: str
    recipients: int
    notifications_created: int = Field(
        description=(
            "How many notification rows this call created. Lower than "
            "'recipients' when some recipients have no active account, have "
            "muted announcements, or were already notified."
        )
    )


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=3,
        max_length=1000,
        description=(
            "Required. People have already read it, so why it came down is part of the record."
        ),
    )


class ScheduleRunResponse(BaseModel):
    as_of: str
    published: int
    notifications_created: int
    expired: int


class FeedItem(BaseModel):
    id: int
    title: str
    body: str
    summary: str | None
    priority: str
    publish_at: datetime | None
    expires_at: datetime | None
    is_pinned: bool
    is_read: bool
    is_acknowledged: bool
    attachment_file_id: int | None
    image_file_id: int | None


class ReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge: bool = Field(
        default=False,
        description=(
            "Record an explicit acknowledgement as well as a read. Opening a "
            "critical notice is not the same as confirming it."
        ),
    )


class UnreadEmployee(BaseModel):
    employee_id: int
    employee_code: str | None
    employee_name: str | None


class ReadReportResponse(BaseModel):
    announcement_id: int
    title: str
    audience_size: int
    read_count: int
    acknowledged_count: int
    unread: list[UnreadEmployee] = Field(
        description="Capped at 200 names. Use the export for a full list."
    )


# --------------------------------------------------------------- notifications


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    priority: str
    title: str
    body: str | None
    link_path: str | None = Field(
        description="An application path such as '/leave/applications/12', not a URL."
    )
    entity_type: str | None
    entity_id: str | None
    read_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    payload: dict[str, Any] | None


class UnreadSummaryResponse(BaseModel):
    unread_total: int
    unread_by_category: dict[str, int]
    has_critical: bool


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification_ids: list[int] | None = Field(
        default=None,
        max_length=500,
        description="Omit to mark every unread notification read.",
    )


class MarkReadResponse(BaseModel):
    marked: int


class PreferenceItem(BaseModel):
    category: str
    muted: bool


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(max_length=30)
    muted: bool
