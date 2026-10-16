"""Announcement and notification-centre endpoints.

Two audiences, two permissions. Everybody with ``announcement.read`` sees their
own feed, filtered server-side to the announcements aimed at them - the feed
endpoint never accepts an employee id, so there is nothing to tamper with.
Authoring, publishing and the read report need ``announcement.manage``.

The notification centre is strictly self-service: every query is scoped to the
signed-in user's own rows, and ``notification.read_self`` is the only permission
involved. There is no endpoint that reads somebody else's notifications.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.core.deps import AuditCtx, CurrentUser, DbSession, require
from app.core.errors import NotFound, PolicyViolation
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees import storage
from app.modules.employees.models import StoredFile
from app.modules.notifications import service
from app.modules.notifications.models import NOTIFICATION_CATEGORIES
from app.modules.notifications.schemas import (
    AnnouncementCreate,
    AnnouncementResponse,
    AnnouncementUpdate,
    FeedItem,
    MarkReadRequest,
    MarkReadResponse,
    NotificationResponse,
    PreferenceItem,
    PreferenceUpdate,
    PublishRequest,
    PublishResponse,
    ReadReportResponse,
    ReadRequest,
    ScheduleRunResponse,
    UnreadSummaryResponse,
    WithdrawRequest,
)

router = APIRouter(tags=["Announcements and notifications"])


# ------------------------------------------------------------------ my feed


@router.get(
    "/announcements/feed",
    response_model=list[FeedItem],
    summary="Your announcement feed",
)
def my_feed(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("announcement.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[FeedItem]:
    """Published announcements aimed at you, pinned ones first.

    The audience filter runs here, not in the browser: an announcement meant for
    one office is not merely hidden from the others, it is never sent to them.
    """
    return [FeedItem(**row) for row in service.feed_for(session, user=user, limit=limit)]


@router.post(
    "/announcements/{announcement_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark an announcement read",
)
def mark_announcement_read(
    announcement_id: int,
    payload: ReadRequest,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("announcement.read"))],
) -> None:
    """Record that you opened it, and optionally that you acknowledge it.

    Only announcements you can actually see count: posting an id you were not
    sent records nothing, so the read report cannot be padded.
    """
    if user.employee_id is None:
        raise PolicyViolation(
            "This account is not linked to an employee record, so a read "
            "receipt cannot be recorded against it."
        )

    visible = {
        item.id for item in service.visible_announcements(session, employee_id=user.employee_id)
    }
    if announcement_id not in visible:
        # Silent for an announcement that was never sent to this employee: there
        # is nothing to record and nothing to tell them about it.
        return None

    service.mark_announcement_read(
        session,
        announcement_id=announcement_id,
        employee_id=user.employee_id,
        acknowledge=payload.acknowledge,
    )
    return None


# ------------------------------------------------------------- authoring


@router.get(
    "/admin/announcements",
    response_model=list[AnnouncementResponse],
    summary="List announcements, including drafts",
)
def list_announcements(
    session: DbSession,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
    statuses: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AnnouncementResponse]:
    return [
        AnnouncementResponse.model_validate(item)
        for item in service.list_announcements(session, statuses=statuses, limit=limit)
    ]


@router.post(
    "/admin/announcements",
    response_model=AnnouncementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft an announcement",
)
def create_announcement(
    payload: AnnouncementCreate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> AnnouncementResponse:
    """Create it as a draft. Publishing is a separate, deliberate call."""
    announcement = service.create_announcement(
        session,
        data=payload.model_dump(exclude_unset=False),
        context=context,
    )
    return AnnouncementResponse.model_validate(announcement)


@router.get(
    "/admin/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="One announcement",
)
def get_announcement(
    announcement_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> AnnouncementResponse:
    return AnnouncementResponse.model_validate(service.get_announcement(session, announcement_id))


@router.patch(
    "/admin/announcements/{announcement_id}",
    response_model=AnnouncementResponse,
    summary="Edit an announcement",
)
def update_announcement(
    announcement_id: int,
    payload: AnnouncementUpdate,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> AnnouncementResponse:
    """Correct an announcement.

    A published one can still be edited - a wrong date in a closure notice has
    to be fixable - and the audit entry notes that people may have read the
    earlier text.
    """
    announcement = service.update_announcement(
        session,
        announcement_id,
        data=payload.model_dump(exclude_unset=True),
        context=context,
    )
    return AnnouncementResponse.model_validate(announcement)


@router.post(
    "/admin/announcements/{announcement_id}/publish",
    response_model=PublishResponse,
    summary="Publish or schedule an announcement",
)
def publish_announcement(
    announcement_id: int,
    payload: PublishRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> PublishResponse:
    """Publish now, or schedule it for a future time.

    Safe to call again after a partial failure: each recipient's notification is
    keyed on the announcement and the user, so nobody is notified twice.
    """
    result = service.publish_announcement(
        session, announcement_id, context=context, publish_at=payload.publish_at
    )
    return PublishResponse(**result)


@router.post(
    "/admin/announcements/{announcement_id}/withdraw",
    response_model=AnnouncementResponse,
    summary="Withdraw a published announcement",
)
def withdraw_announcement(
    announcement_id: int,
    payload: WithdrawRequest,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> AnnouncementResponse:
    """Take it down, keeping the row and the reason.

    Nothing is deleted: people have read it, and a notice that vanishes without
    trace is worse than one marked withdrawn.
    """
    announcement = service.withdraw_announcement(
        session, announcement_id, reason=payload.reason, context=context
    )
    return AnnouncementResponse.model_validate(announcement)


@router.get(
    "/admin/announcements/{announcement_id}/read-report",
    response_model=ReadReportResponse,
    summary="Who has read an announcement",
)
def read_report(
    announcement_id: int,
    session: DbSession,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> ReadReportResponse:
    """Read and acknowledgement counts, with the names still outstanding."""
    return ReadReportResponse(**service.read_report(session, announcement_id))


@router.post(
    "/admin/announcements/run-schedule",
    response_model=ScheduleRunResponse,
    summary="Send notifications for announcements now due",
)
def run_schedule(
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
) -> ScheduleRunResponse:
    """Publish what is due and expire what has run out.

    Idempotent: the announcements are already visible by time, and each
    notification is keyed per announcement and user, so a second run posts
    nothing.
    """
    return ScheduleRunResponse(**service.publish_due_announcements(session, context=context))


@router.post(
    "/admin/announcements/{announcement_id}/files",
    response_model=AnnouncementResponse,
    summary="Attach a file or set the banner image",
)
def upload_announcement_file(
    announcement_id: int,
    session: DbSession,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.manage"))],
    file: Annotated[UploadFile, File(description="PDF, JPEG, PNG, DOCX or XLSX.")],
    as_image: Annotated[
        bool, Query(description="Set it as the banner image rather than an attachment.")
    ] = False,
) -> AnnouncementResponse:
    """Upload an attachment, or the banner image.

    The declared content type is checked against the file's magic bytes and the
    stored name is server-generated, so a renamed file is refused and an
    uploaded name can never influence the storage path. One attachment and one
    image per announcement: re-uploading replaces.
    """
    saved = storage.save_upload(
        stream=file.file,
        filename=file.filename or "upload",
        declared_content_type=file.content_type,
    )
    stored = StoredFile(
        storage_key=saved.storage_key,
        original_filename=saved.original_filename,
        content_type=saved.content_type,
        size_bytes=saved.size_bytes,
        sha256=saved.sha256,
        uploaded_by_user_id=context.actor_user_id,
    )
    session.add(stored)
    session.flush()

    announcement = service.attach_file(
        session,
        announcement_id,
        stored_file_id=stored.id,
        content_type=saved.content_type,
        as_image=as_image,
        context=context,
    )
    return AnnouncementResponse.model_validate(announcement)


@router.get(
    "/announcements/{announcement_id}/file",
    response_class=FileResponse,
    summary="Download an announcement's attachment or image",
)
def download_announcement_file(
    announcement_id: int,
    session: DbSession,
    user: CurrentUser,
    context: AuditCtx,
    _dep: Annotated[User, Depends(require("announcement.read"))],
    as_image: Annotated[
        bool, Query(description="Fetch the banner image rather than the attachment.")
    ] = False,
) -> Response:
    """Served only to someone the announcement was actually sent to.

    An announcement aimed elsewhere is 404 here as well as in the feed, so a
    guessed id cannot be used to read a file somebody was never sent. Always
    served as an attachment with sniffing disabled: a stored file must not
    execute in the application's origin.
    """
    announcement, file_id = service.file_for_recipient(
        session, announcement_id=announcement_id, user=user, as_image=as_image
    )
    stored = session.get(StoredFile, file_id)
    if stored is None:  # pragma: no cover - FK prevents this
        raise NotFound("The file is no longer available.")

    audit.record(
        session,
        action="announcement.file_downloaded",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        extra={"as_image": as_image, "file_id": file_id},
    )

    return FileResponse(
        path=storage.resolve_path(stored.storage_key),
        media_type=stored.content_type,
        filename=stored.original_filename,
        headers={
            "Content-Disposition": f'attachment; filename="{stored.original_filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# -------------------------------------------------------- notification centre


@router.get(
    "/notifications",
    response_model=list[NotificationResponse],
    summary="Your notifications",
)
def list_notifications(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
    unread_only: bool = False,
    category: Annotated[str | None, Query()] = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NotificationResponse]:
    """Your own notifications, newest first. Never anybody else's."""
    return [
        NotificationResponse.model_validate(item)
        for item in service.list_notifications(
            session,
            user_id=user.id,
            unread_only=unread_only,
            category=category,
            include_archived=include_archived,
            limit=limit,
        )
    ]


@router.get(
    "/notifications/unread-count",
    response_model=UnreadSummaryResponse,
    summary="Your unread count",
)
def unread_count(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> UnreadSummaryResponse:
    """A total, a per-category breakdown and whether anything is critical.

    Per category as well as a total because "3 unread" does not tell you whether
    it is worth opening, while "1 leave, 2 announcements" does.
    """
    return UnreadSummaryResponse(**service.unread_summary(session, user_id=user.id))


@router.post(
    "/notifications/mark-read",
    response_model=MarkReadResponse,
    summary="Mark notifications read",
)
def mark_read(
    payload: MarkReadRequest,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> MarkReadResponse:
    """Mark some, or all, of your unread notifications read."""
    marked = service.mark_read(session, user_id=user.id, notification_ids=payload.notification_ids)
    return MarkReadResponse(marked=marked)


@router.post(
    "/notifications/{notification_id}/archive",
    response_model=NotificationResponse,
    summary="Dismiss a notification",
)
def archive_notification(
    notification_id: int,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> NotificationResponse:
    """Clear it from the centre. The row is kept, so the record of having been
    told survives."""
    return NotificationResponse.model_validate(
        service.archive(session, user_id=user.id, notification_id=notification_id)
    )


@router.get(
    "/notifications/preferences",
    response_model=list[PreferenceItem],
    summary="Your notification preferences",
)
def list_preferences(
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> list[PreferenceItem]:
    """Every category, and whether you have muted it.

    The default is to notify: a preference row exists only when you have asked
    for silence.
    """
    return [PreferenceItem(**row) for row in service.preferences_for(session, user_id=user.id)]


@router.put(
    "/notifications/preferences",
    response_model=PreferenceItem,
    summary="Mute or unmute a category",
)
def set_preference(
    payload: PreferenceUpdate,
    session: DbSession,
    user: CurrentUser,
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> PreferenceItem:
    """Silence a category, or turn it back on.

    Account and payroll notices can be muted like anything else; nothing here
    is a substitute for the audit trail, which is not optional.
    """
    preference = service.set_preference(
        session, user_id=user.id, category=payload.category, muted=payload.muted
    )
    return PreferenceItem(category=preference.category, muted=preference.muted)


@router.get(
    "/notifications/categories",
    response_model=list[str],
    summary="The notification categories",
)
def categories(
    _dep: Annotated[User, Depends(require("notification.read_self"))],
) -> list[str]:
    return list(NOTIFICATION_CATEGORIES)
