"""Announcements and notification delivery.

The one rule everything else follows
------------------------------------
:func:`notify` is the only way a notification is created, and it is keyed. Every
caller supplies a ``dedupe_key`` derived from the *fact*, never from the time, so
a job that re-runs inserts nothing. A notification centre that repeats itself is
one people stop reading, which costs more than the missing notification it was
trying to avoid.

Notifying never fails the thing it reports
------------------------------------------
A leave approval that succeeded must not be rolled back because its notification
could not be written. Every hook here is wrapped so a failure is logged and the
business operation stands - the notification is a side effect, not part of the
transaction's purpose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, PolicyViolation, ValidationFailed
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.auth.models import User
from app.modules.employees.models import Employee
from app.modules.notifications.models import (
    NOTIFICATION_CATEGORIES,
    Announcement,
    AnnouncementRead,
    AnnouncementTarget,
    Notification,
    NotificationPreference,
)
from app.modules.org.models import (
    Department,
    EmployeeGroup,
    EmployeeGroupMember,
    WorkLocation,
)

logger = logging.getLogger(__name__)

MAX_TARGETS = 50


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def notify(
    session: Session,
    *,
    user_id: int,
    category: str,
    title: str,
    dedupe_key: str,
    body: str | None = None,
    link_path: str | None = None,
    priority: str = "normal",
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    employee_id: int | None = None,
    payload: dict[str, Any] | None = None,
    job_run_id: int | None = None,
) -> Notification | None:
    """Create one notification, or nothing if that key already exists.

    Returns None on a duplicate rather than raising: a bulk run wants to carry
    on, and "already told them" is a success, not a failure.

    The uniqueness is checked by the **database**, not by a prior read - two
    concurrent runs would both pass a read-then-write check.
    """
    if category not in NOTIFICATION_CATEGORIES:
        raise ValidationFailed(
            f"Unknown notification category {category!r}.",
            details=[{"field": "category", "message": "Unknown."}],
        )

    if _is_muted(session, user_id=user_id, category=category):
        return None

    notification = Notification(
        user_id=user_id,
        employee_id=employee_id,
        category=category,
        priority=priority,
        title=title[:200],
        body=body,
        link_path=link_path,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        dedupe_key=dedupe_key[:200],
        channel="in_app",
        delivery_status="sent",
        payload=payload,
        job_run_id=job_run_id,
        created_at=utc_now(),
    )
    try:
        with session.begin_nested():
            session.add(notification)
            session.flush()
    except IntegrityError:
        # Already told them. The savepoint discards only this insert, so a bulk
        # run continues with the rest.
        return None
    return notification


def _is_muted(session: Session, *, user_id: int, category: str) -> bool:
    """Whether this user has silenced the category.

    The absence of a row means "notify me": the default is to tell people
    things, and silence is the explicit choice.
    """
    return bool(
        session.scalar(
            select(func.count())
            .select_from(NotificationPreference)
            .where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.category == category,
                NotificationPreference.channel == "in_app",
                NotificationPreference.muted.is_(True),
            )
        )
    )


def safe_notify(session: Session, **kwargs: Any) -> Notification | None:
    """:func:`notify`, but a failure never breaks the caller.

    Used by every hook. A leave approval that succeeded must not be rolled back
    because its notification could not be written - the notification is a side
    effect, not part of what the transaction is for.
    """
    try:
        return notify(session, **kwargs)
    except Exception:  # pragma: no cover - defensive
        logger.exception(
            "Could not create a %s notification for user %s",
            kwargs.get("category"),
            kwargs.get("user_id"),
        )
        return None


def list_notifications(
    session: Session,
    *,
    user_id: int,
    unread_only: bool = False,
    category: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> list[Notification]:
    statement = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    if unread_only:
        statement = statement.where(Notification.read_at.is_(None))
    if category:
        statement = statement.where(Notification.category == category)
    if not include_archived:
        statement = statement.where(Notification.archived_at.is_(None))
    return list(session.scalars(statement).all())


def unread_summary(session: Session, *, user_id: int) -> dict[str, Any]:
    """The badge: a total and a per-category breakdown.

    Per category as well as a total because "3 unread" tells you nothing about
    whether it is worth opening, while "1 leave, 2 announcements" does.
    """
    rows = session.execute(
        select(Notification.category, func.count())
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.archived_at.is_(None),
        )
        .group_by(Notification.category)
    ).all()

    by_category = {row[0]: row[1] for row in rows}
    return {
        "unread_total": sum(by_category.values()),
        "unread_by_category": by_category,
        "has_critical": bool(
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                    Notification.archived_at.is_(None),
                    Notification.priority == "critical",
                )
            )
        ),
    }


def mark_read(session: Session, *, user_id: int, notification_ids: list[int] | None = None) -> int:
    """Mark some or all of a user's notifications read. Returns how many changed.

    Scoped to the caller's own rows, so an id from someone else's centre does
    nothing rather than erroring - there is no information to leak either way.
    """
    statement = select(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if notification_ids:
        statement = statement.where(Notification.id.in_(notification_ids))

    now = utc_now()
    changed = 0
    for notification in session.scalars(statement).all():
        notification.read_at = now
        changed += 1
    session.flush()
    return changed


def archive(session: Session, *, user_id: int, notification_id: int) -> Notification:
    """Dismiss a notification from the centre, keeping the row.

    Kept rather than deleted so the record of having been told survives - which
    matters when someone says they were never informed.
    """
    notification = session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
    )
    if notification is None:
        raise NotFound("Notification not found.")

    notification.archived_at = utc_now()
    if notification.read_at is None:
        notification.read_at = notification.archived_at
    session.flush()
    return notification


def set_preference(
    session: Session, *, user_id: int, category: str, muted: bool
) -> NotificationPreference:
    """Mute or unmute a category for one user."""
    if category not in NOTIFICATION_CATEGORIES:
        raise ValidationFailed(
            f"Unknown notification category {category!r}.",
            details=[{"field": "category", "message": "Unknown."}],
        )

    preference = session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.category == category,
            NotificationPreference.channel == "in_app",
        )
    )
    if preference is None:
        preference = NotificationPreference(
            user_id=user_id, category=category, channel="in_app", muted=muted
        )
        session.add(preference)
    else:
        preference.muted = muted
    session.flush()
    return preference


def preferences_for(session: Session, *, user_id: int) -> list[dict[str, Any]]:
    """Every category with whether this user has muted it."""
    muted = {
        row[0]
        for row in session.execute(
            select(NotificationPreference.category).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.channel == "in_app",
                NotificationPreference.muted.is_(True),
            )
        ).all()
    }
    return [
        {"category": category, "muted": category in muted} for category in NOTIFICATION_CATEGORIES
    ]


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TargetSpec:
    target_type: str
    target_id: int | None


def _validate_target(session: Session, spec: TargetSpec) -> None:
    """Check the id against the right table.

    ``target_id`` cannot be a foreign key - it points at one of four tables
    depending on ``target_type`` - so the check happens here, on write, rather
    than being left unverified.
    """
    if spec.target_type == "all":
        if spec.target_id is not None:
            raise ValidationFailed(
                "An 'all' target takes no id.",
                details=[{"field": "target_id", "message": "Must be empty."}],
            )
        return

    if spec.target_id is None:
        raise ValidationFailed(
            f"A {spec.target_type!r} target needs an id.",
            details=[{"field": "target_id", "message": "Required."}],
        )

    model = {
        "department": Department,
        "work_location": WorkLocation,
        "employee_group": EmployeeGroup,
        "employee": Employee,
    }[spec.target_type]

    if session.get(model, spec.target_id) is None:
        raise ValidationFailed(
            f"No {spec.target_type.replace('_', ' ')} with id {spec.target_id}.",
            details=[{"field": "target_id", "message": "Unknown."}],
        )


def resolve_audience(session: Session, announcement: Announcement) -> list[int]:
    """The employee ids an announcement reaches.

    The **union** of its targets, because an announcement legitimately goes to
    two offices, or a department plus a named individual. Exited employees are
    excluded: telling someone who has left about a policy change is noise, and
    they cannot sign in to read it anyway.
    """
    if not announcement.targets:
        return []

    conditions = []
    for target in announcement.targets:
        if target.target_type == "all":
            conditions.append(Employee.id.isnot(None))
        elif target.target_type == "department":
            conditions.append(Employee.department_id == target.target_id)
        elif target.target_type == "work_location":
            conditions.append(Employee.work_location_id == target.target_id)
        elif target.target_type == "employee":
            conditions.append(Employee.id == target.target_id)
        elif target.target_type == "employee_group":
            conditions.append(
                Employee.id.in_(
                    select(EmployeeGroupMember.employee_id).where(
                        EmployeeGroupMember.group_id == target.target_id
                    )
                )
            )

    if not conditions:
        return []

    return list(
        session.scalars(
            select(Employee.id).where(Employee.employment_status != "exited", or_(*conditions))
        ).all()
    )


def visible_announcements(
    session: Session, *, employee_id: int | None, as_of: datetime | None = None
) -> list[Announcement]:
    """Published, unexpired announcements aimed at this employee.

    An account with no employee record still sees the ``all`` announcements, so
    a new joiner whose record is not linked yet is not left out of a closure
    notice.
    """
    as_of = as_of or utc_now()

    live = session.scalars(
        select(Announcement)
        .options(selectinload(Announcement.targets))
        .where(
            Announcement.status == "published",
            Announcement.publish_at <= as_of,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > as_of),
        )
        .order_by(Announcement.publish_at.desc())
    ).all()

    if employee_id is None:
        return [
            item for item in live if any(target.target_type == "all" for target in item.targets)
        ]

    employee = session.get(Employee, employee_id)
    if employee is None:
        return []

    group_ids = set(
        session.scalars(
            select(EmployeeGroupMember.group_id).where(
                EmployeeGroupMember.employee_id == employee_id
            )
        ).all()
    )

    def applies(announcement: Announcement) -> bool:
        for target in announcement.targets:
            if target.target_type == "all":
                return True
            if target.target_type == "department" and target.target_id == employee.department_id:
                return True
            if (
                target.target_type == "work_location"
                and target.target_id == employee.work_location_id
            ):
                return True
            if target.target_type == "employee" and target.target_id == employee_id:
                return True
            if target.target_type == "employee_group" and target.target_id in group_ids:
                return True
        return False

    return [item for item in live if applies(item)]


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def create_announcement(
    session: Session, *, data: dict[str, Any], context: audit.AuditContext
) -> Announcement:
    """Create an announcement as a draft.

    A draft is invisible, which is what lets a notice be written and checked
    before it reaches everyone. Publishing is a separate, deliberate act.
    """
    targets_data = data.get("targets") or [{"target_type": "all", "target_id": None}]
    if len(targets_data) > MAX_TARGETS:
        raise ValidationFailed(
            f"At most {MAX_TARGETS} targets. Use a broader target instead of listing individuals."
        )

    specs = [
        TargetSpec(target_type=item["target_type"], target_id=item.get("target_id"))
        for item in targets_data
    ]
    for spec in specs:
        _validate_target(session, spec)

    publish_at = data.get("publish_at")
    expires_at = data.get("expires_at")
    if expires_at is not None and publish_at is not None and expires_at <= publish_at:
        raise ValidationFailed(
            "The expiry must be after the publication time.",
            details=[{"field": "expires_at", "message": "Must be after publish_at."}],
        )

    announcement = Announcement(
        title=data["title"].strip(),
        body=data["body"].strip(),
        summary=(data.get("summary") or "").strip() or None,
        priority=data.get("priority", "normal"),
        category="announcement",
        status="draft",
        publish_at=publish_at,
        expires_at=expires_at,
        pin_until=data.get("pin_until"),
        notify_on_publish=data.get("notify_on_publish", True),
        attachment_file_id=data.get("attachment_file_id"),
        image_file_id=data.get("image_file_id"),
        created_by_user_id=context.actor_user_id,
    )
    session.add(announcement)
    session.flush()

    for spec in specs:
        session.add(
            AnnouncementTarget(
                announcement_id=announcement.id,
                target_type=spec.target_type,
                target_id=spec.target_id,
            )
        )
    session.flush()
    session.refresh(announcement, attribute_names=["targets"])

    audit.record(
        session,
        action="announcement.created",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        after={
            "title": announcement.title,
            "priority": announcement.priority,
            "targets": [{"type": spec.target_type, "id": spec.target_id} for spec in specs],
            "status": "draft",
        },
    )
    return announcement


def update_announcement(
    session: Session, announcement_id: int, *, data: dict[str, Any], context: audit.AuditContext
) -> Announcement:
    """Edit an announcement.

    A published announcement can be corrected - a wrong date in a closure notice
    has to be fixable - but the edit is audited, because people have already read
    the old text.
    """
    announcement = get_announcement(session, announcement_id)
    before = {
        "title": announcement.title,
        "body": announcement.body,
        "priority": announcement.priority,
        "status": announcement.status,
    }

    for attribute in ("title", "body", "summary", "priority", "pin_until", "notify_on_publish"):
        if attribute in data:
            setattr(announcement, attribute, data[attribute])
    if "publish_at" in data and announcement.status in ("draft", "scheduled"):
        announcement.publish_at = data["publish_at"]
    if "expires_at" in data:
        announcement.expires_at = data["expires_at"]

    if "targets" in data and data["targets"] is not None:
        specs = [
            TargetSpec(target_type=item["target_type"], target_id=item.get("target_id"))
            for item in data["targets"]
        ]
        for spec in specs:
            _validate_target(session, spec)
        for existing in list(announcement.targets):
            session.delete(existing)
        session.flush()
        for spec in specs:
            session.add(
                AnnouncementTarget(
                    announcement_id=announcement.id,
                    target_type=spec.target_type,
                    target_id=spec.target_id,
                )
            )

    announcement.updated_by_user_id = context.actor_user_id
    session.flush()
    session.refresh(announcement, attribute_names=["targets"])

    audit.record(
        session,
        action="announcement.updated",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        before=before,
        after={
            "title": announcement.title,
            "priority": announcement.priority,
            "status": announcement.status,
        },
        extra=(
            {"note": "Edited after publication; people may have read the earlier text."}
            if announcement.status == "published"
            else None
        ),
    )
    return announcement


def get_announcement(session: Session, announcement_id: int) -> Announcement:
    announcement = session.scalar(
        select(Announcement)
        .where(Announcement.id == announcement_id)
        .options(selectinload(Announcement.targets))
    )
    if announcement is None:
        raise NotFound("Announcement not found.")
    return announcement


def list_announcements(
    session: Session, *, statuses: list[str] | None = None, limit: int = 100
) -> list[Announcement]:
    statement = (
        select(Announcement)
        .options(selectinload(Announcement.targets))
        .order_by(Announcement.created_at.desc())
        .limit(limit)
    )
    if statuses:
        statement = statement.where(Announcement.status.in_(statuses))
    return list(session.scalars(statement).all())


def publish_announcement(
    session: Session,
    announcement_id: int,
    *,
    context: audit.AuditContext,
    publish_at: datetime | None = None,
) -> dict[str, Any]:
    """Publish now, or schedule it.

    Scheduling stores the instant rather than relying on a job having run, so a
    missed job delays the *notification* but never hides the announcement
    itself - it becomes visible the moment its time passes, because visibility
    is a query, not a state transition somebody has to perform.
    """
    announcement = get_announcement(session, announcement_id)

    if announcement.status == "published":
        raise Conflict("This announcement is already published.")
    if announcement.status == "withdrawn":
        raise Conflict(
            "This announcement was withdrawn. Create a new one rather than "
            "re-publishing it, so the withdrawal stays on the record."
        )
    if not announcement.targets:
        raise PolicyViolation(
            "This announcement has no audience, so publishing it would reach nobody."
        )

    now = utc_now()
    when = publish_at or announcement.publish_at or now

    if when > now:
        announcement.status = "scheduled"
        announcement.publish_at = when
        session.flush()
        audit.record(
            session,
            action="announcement.scheduled",
            context=context,
            entity_type="announcement",
            entity_id=announcement.id,
            after={"publish_at": when.isoformat()},
        )
        return {
            "status": "scheduled",
            "publish_at": when.isoformat(),
            "recipients": 0,
            "notifications_created": 0,
        }

    announcement.status = "published"
    announcement.publish_at = when
    announcement.published_at = now
    session.flush()

    created = _fan_out(session, announcement=announcement, context=context)

    audit.record(
        session,
        action="announcement.published",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        after={
            "title": announcement.title,
            "priority": announcement.priority,
            "recipients": announcement.recipient_count,
            "notifications_created": created,
        },
    )
    return {
        "status": "published",
        "publish_at": when.isoformat(),
        "recipients": announcement.recipient_count or 0,
        "notifications_created": created,
    }


def _fan_out(session: Session, *, announcement: Announcement, context: audit.AuditContext) -> int:
    """Put a notification in each recipient's centre.

    Keyed on the announcement and the user, so re-publishing or a retried
    scheduler run notifies nobody twice.
    """
    employee_ids = resolve_audience(session, announcement)
    announcement.recipient_count = len(employee_ids)

    if not announcement.notify_on_publish or not employee_ids:
        announcement.notifications_sent_at = utc_now()
        session.flush()
        return 0

    rows = session.execute(
        select(User.id, User.employee_id).where(
            User.employee_id.in_(employee_ids), User.is_active.is_(True)
        )
    ).all()

    created = 0
    for user_id, employee_id in rows:
        notification = safe_notify(
            session,
            user_id=user_id,
            employee_id=employee_id,
            category="announcement",
            priority=announcement.priority,
            title=announcement.title,
            body=announcement.summary or announcement.body[:400],
            link_path="/announcements",
            entity_type="announcement",
            entity_id=announcement.id,
            dedupe_key=f"announcement:{announcement.id}:user:{user_id}",
        )
        if notification is not None:
            created += 1

    announcement.notifications_sent_at = utc_now()
    session.flush()
    return created


IMAGE_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})


def attach_file(
    session: Session,
    announcement_id: int,
    *,
    stored_file_id: int,
    content_type: str,
    as_image: bool,
    context: audit.AuditContext,
) -> Announcement:
    """Attach an uploaded file, or set the banner image.

    One of each: an announcement has a single attachment and a single image, so
    re-uploading replaces rather than accumulates. The previous file row is left
    in place - a stored file may be referenced elsewhere, and deleting it here
    would be the wrong place to decide that.

    An image must actually be an image. The upload path has already checked the
    magic bytes against the declared type, so this only has to refuse a PDF
    being set as the banner.
    """
    announcement = get_announcement(session, announcement_id)

    if as_image and content_type not in IMAGE_CONTENT_TYPES:
        raise ValidationFailed(
            "The banner image must be a JPEG or PNG.",
            details=[{"field": "file", "message": "Must be an image."}],
        )

    if as_image:
        announcement.image_file_id = stored_file_id
    else:
        announcement.attachment_file_id = stored_file_id
    announcement.updated_by_user_id = context.actor_user_id
    session.flush()

    audit.record(
        session,
        action="announcement.image_set" if as_image else "announcement.attachment_set",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        after={"file_id": stored_file_id, "content_type": content_type},
    )
    return announcement


def file_for_recipient(
    session: Session, *, announcement_id: int, user: User, as_image: bool
) -> tuple[Announcement, int]:
    """The attachment of an announcement **this user can see**.

    Scoped the same way the feed is: an announcement aimed elsewhere is 404 here
    too, so a guessed id cannot be used to read a file somebody was never sent.
    Someone who may manage announcements can read any of them, since they can
    read the announcement itself.
    """
    if user.has_permission("announcement.manage"):
        announcement = get_announcement(session, announcement_id)
    else:
        visible = visible_announcements(session, employee_id=user.employee_id)
        announcement = next((item for item in visible if item.id == announcement_id), None)
        if announcement is None:
            raise NotFound("Announcement not found.")

    file_id = announcement.image_file_id if as_image else announcement.attachment_file_id
    if file_id is None:
        raise NotFound("This announcement has no " + ("image." if as_image else "attachment."))
    return announcement, file_id


def withdraw_announcement(
    session: Session, announcement_id: int, *, reason: str, context: audit.AuditContext
) -> Announcement:
    """Take a published announcement down.

    Needs a reason, and the row is kept: people have already read it, and a
    notice that vanishes without trace is worse than one that is marked
    withdrawn.
    """
    announcement = get_announcement(session, announcement_id)
    if announcement.status not in ("published", "scheduled"):
        raise Conflict(f"This announcement is {announcement.status}; there is nothing to withdraw.")
    if not reason.strip():
        raise ValidationFailed(
            "A reason is required to withdraw an announcement.",
            details=[{"field": "reason", "message": "Required."}],
        )

    announcement.status = "withdrawn"
    announcement.withdrawn_at = utc_now()
    announcement.withdrawal_reason = reason.strip()
    session.flush()

    audit.record(
        session,
        action="announcement.withdrawn",
        context=context,
        entity_type="announcement",
        entity_id=announcement.id,
        after={"reason": reason.strip()},
    )
    return announcement


def publish_due_announcements(session: Session, *, context: audit.AuditContext) -> dict[str, Any]:
    """Send the notifications for announcements whose time has come.

    The announcements are already visible - visibility is a query on
    ``publish_at``, not a flag this job sets - so a missed run delays the
    notification and nothing else. Idempotent through the same per-user key.
    """
    now = utc_now()
    due = session.scalars(
        select(Announcement)
        .options(selectinload(Announcement.targets))
        .where(
            Announcement.status == "scheduled",
            Announcement.publish_at <= now,
        )
    ).all()

    published = 0
    created = 0
    for announcement in due:
        announcement.status = "published"
        announcement.published_at = now
        session.flush()
        created += _fan_out(session, announcement=announcement, context=context)
        published += 1

    expired = 0
    for announcement in session.scalars(
        select(Announcement).where(
            Announcement.status == "published",
            Announcement.expires_at.isnot(None),
            Announcement.expires_at <= now,
        )
    ).all():
        announcement.status = "expired"
        expired += 1

    session.flush()
    result = {
        "as_of": now.isoformat(),
        "published": published,
        "notifications_created": created,
        "expired": expired,
    }
    if published or expired:
        audit.record(
            session,
            action="announcement.schedule_run",
            context=context,
            entity_type="job",
            entity_id="announcement_schedule",
            extra=result,
        )
        session.flush()
    return result


def mark_announcement_read(
    session: Session, *, announcement_id: int, employee_id: int, acknowledge: bool = False
) -> AnnouncementRead:
    """Record that an employee opened an announcement.

    ``acknowledge`` is separate from reading: opening a closure notice is not
    the same as confirming you have understood it, and for a critical
    announcement the difference matters.
    """
    existing = session.scalar(
        select(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id,
            AnnouncementRead.employee_id == employee_id,
        )
    )
    now = utc_now()
    if existing is None:
        existing = AnnouncementRead(
            announcement_id=announcement_id,
            employee_id=employee_id,
            read_at=now,
            acknowledged_at=now if acknowledge else None,
        )
        session.add(existing)
    elif acknowledge and existing.acknowledged_at is None:
        existing.acknowledged_at = now
    session.flush()
    return existing


def read_report(session: Session, announcement_id: int) -> dict[str, Any]:
    """Who has read a published announcement, and who has not.

    Worth having for a critical notice: "did everyone see the closure" is a
    question an announcement system should be able to answer.
    """
    announcement = get_announcement(session, announcement_id)
    audience = resolve_audience(session, announcement)

    reads = {
        row[0]: (row[1], row[2])
        for row in session.execute(
            select(
                AnnouncementRead.employee_id,
                AnnouncementRead.read_at,
                AnnouncementRead.acknowledged_at,
            ).where(AnnouncementRead.announcement_id == announcement_id)
        ).all()
    }

    employees = (
        {
            row[0]: (row[1], " ".join(part for part in (row[2], row[3]) if part))
            for row in session.execute(
                select(
                    Employee.id,
                    Employee.employee_code,
                    Employee.first_name,
                    Employee.last_name,
                ).where(Employee.id.in_(audience))
            ).all()
        }
        if audience
        else {}
    )

    return {
        "announcement_id": announcement_id,
        "title": announcement.title,
        "audience_size": len(audience),
        "read_count": len([item for item in audience if item in reads]),
        "acknowledged_count": len(
            [item for item in audience if item in reads and reads[item][1] is not None]
        ),
        "unread": [
            {
                "employee_id": employee_id,
                "employee_code": employees.get(employee_id, (None, None))[0],
                "employee_name": employees.get(employee_id, (None, None))[1],
            }
            for employee_id in audience
            if employee_id not in reads
        ][:200],
    }


def feed_for(session: Session, *, user: User, limit: int = 50) -> list[dict[str, Any]]:
    """One employee's announcement feed, with their read state and pins first."""
    announcements = visible_announcements(session, employee_id=user.employee_id)

    read_ids: set[int] = set()
    acknowledged_ids: set[int] = set()
    if user.employee_id is not None and announcements:
        for row in session.execute(
            select(AnnouncementRead.announcement_id, AnnouncementRead.acknowledged_at).where(
                AnnouncementRead.employee_id == user.employee_id,
                AnnouncementRead.announcement_id.in_([item.id for item in announcements]),
            )
        ).all():
            read_ids.add(row[0])
            if row[1] is not None:
                acknowledged_ids.add(row[0])

    # The server's date, not the browser's: a pin that expires is a company fact.
    today = utc_now().date()
    priority_rank = {"critical": 0, "important": 1, "normal": 2}

    rows = [
        {
            "id": item.id,
            "title": item.title,
            "body": item.body,
            "summary": item.summary,
            "priority": item.priority,
            "publish_at": item.publish_at,
            "expires_at": item.expires_at,
            "is_pinned": item.pin_until is not None and item.pin_until >= today,
            "is_read": item.id in read_ids,
            "is_acknowledged": item.id in acknowledged_ids,
            "attachment_file_id": item.attachment_file_id,
            "image_file_id": item.image_file_id,
        }
        for item in announcements
    ]
    rows.sort(
        key=lambda row: (
            not row["is_pinned"],
            priority_rank.get(str(row["priority"]), 2),
            -(row["publish_at"].timestamp() if row["publish_at"] else 0),
        )
    )
    return rows[:limit]
