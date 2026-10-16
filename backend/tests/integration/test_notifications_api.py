"""Announcements and the notification centre, against a real database.

Two things are worth proving here and cannot be proved without the database:

* **The audience filter is server-side.** An announcement aimed at one office
  must not be in the other office's response at all - not merely hidden by the
  browser.
* **Deduplication is enforced by the unique constraint.** Every job in this
  project can be re-run, and the brief requires that re-running one does not
  repeat itself. These tests run each job twice and assert the second run
  writes nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.modules.auth.models import User
from app.modules.notifications.models import Announcement, Notification
from tests.conftest import auth_header

pytestmark = pytest.mark.integration


@pytest.fixture
def hr(client: TestClient, admin_user: User) -> dict:
    return auth_header(client, "hradmin")


@pytest.fixture
def staff(client: TestClient, hr: dict, employee_user: User, org_fixtures: dict[str, int]) -> dict:
    """An employee account with a record behind it, and its auth header."""
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_code": "ITC0001",
            "first_name": "Ravi",
            "last_name": "Sharma",
            "employment_type": "full_time",
            "date_of_joining": "2026-01-05",
            **org_fixtures,
        },
        headers=hr,
    )
    assert created.status_code == 201, created.text
    record = created.json()
    link = client.post(
        f"/api/v1/employees/{record['id']}/link-account",
        json={"user_id": employee_user.id},
        headers=hr,
    )
    assert link.status_code == 200, link.text
    return {
        "employee": record,
        "headers": auth_header(client, "e.employee"),
        "user_id": employee_user.id,
    }


def draft(client: TestClient, hr: dict, **overrides) -> dict:
    body = {
        "title": "Office closed on Thursday",
        "body": "The office will be closed for maintenance. Work from home applies.",
        **overrides,
    }
    response = client.post("/api/v1/admin/announcements", json=body, headers=hr)
    assert response.status_code == 201, response.text
    return response.json()


class TestAnnouncementLifecycle:
    def test_a_new_announcement_is_a_draft_and_reaches_nobody(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        """Drafting is what lets a notice be checked before everyone sees it."""
        created = draft(client, hr)
        assert created["status"] == "draft"

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"])
        assert feed.status_code == 200
        assert feed.json() == []

    def test_publishing_makes_it_visible_and_notifies(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert published.status_code == 200, published.text
        result = published.json()
        assert result["status"] == "published"
        assert result["recipients"] == 1
        assert result["notifications_created"] == 1

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"])
        assert [item["title"] for item in feed.json()] == ["Office closed on Thursday"]

        centre = client.get("/api/v1/notifications", headers=staff["headers"])
        assert [item["category"] for item in centre.json()] == ["announcement"]

    def test_publishing_twice_is_refused(self, client: TestClient, hr: dict, staff: dict) -> None:
        created = draft(client, hr)
        first = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert first.status_code == 200
        again = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert again.status_code == 409, again.text

    def test_a_future_publish_time_schedules_it(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        when = utc_now() + timedelta(days=2)
        created = draft(client, hr, publish_at=when.isoformat())
        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert published.json()["status"] == "scheduled"
        assert published.json()["notifications_created"] == 0

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"])
        assert feed.json() == []

    def test_an_announcement_with_no_audience_cannot_be_published(
        self, client: TestClient, hr: dict, session: Session
    ) -> None:
        created = draft(client, hr)
        session.execute(
            select(Announcement).where(Announcement.id == created["id"])
        )  # keep the session warm; the delete below is what matters
        session.execute(
            Announcement.__table__.update()
            .where(Announcement.id == created["id"])
            .values(status="draft")
        )
        from app.modules.notifications.models import AnnouncementTarget

        session.execute(
            AnnouncementTarget.__table__.delete().where(
                AnnouncementTarget.announcement_id == created["id"]
            )
        )
        session.commit()

        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert published.status_code == 422, published.text
        assert "nobody" in published.text

    def test_withdrawing_needs_a_reason_and_keeps_the_row(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        blank = client.post(
            f"/api/v1/admin/announcements/{created['id']}/withdraw",
            json={"reason": ""},
            headers=hr,
        )
        assert blank.status_code == 422

        withdrawn = client.post(
            f"/api/v1/admin/announcements/{created['id']}/withdraw",
            json={"reason": "The date was wrong."},
            headers=hr,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["status"] == "withdrawn"
        assert withdrawn.json()["withdrawal_reason"] == "The date was wrong."

        # Gone from the feed, still on the record.
        assert client.get("/api/v1/announcements/feed", headers=staff["headers"]).json() == []
        assert (
            client.get(f"/api/v1/admin/announcements/{created['id']}", headers=hr).status_code
            == 200
        )

    def test_a_withdrawn_announcement_is_not_republished(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        """Re-publishing would erase the withdrawal from the record."""
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        client.post(
            f"/api/v1/admin/announcements/{created['id']}/withdraw",
            json={"reason": "Wrong date."},
            headers=hr,
        )
        again = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert again.status_code == 409
        assert "withdrawn" in again.text


class TestAudience:
    def test_an_announcement_for_another_location_is_not_in_the_response(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        """Server-side, not hidden by the browser: the brief requires backend
        enforcement, and a feed filtered in the client is not that."""
        other = client.post(
            "/api/v1/org/work-locations",
            json={
                "code": "PNQ",
                "name": "Pune Office",
                "timezone": "Asia/Kolkata",
                "state": "Maharashtra",
            },
            headers=hr,
        )
        assert other.status_code == 201, other.text

        created = draft(
            client,
            hr,
            title="Pune only",
            targets=[{"target_type": "work_location", "target_id": other.json()["id"]}],
        )
        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert published.json()["recipients"] == 0

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"])
        assert feed.json() == []
        assert "Pune only" not in feed.text

    def test_a_department_target_reaches_its_members(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        department_id = staff["employee"]["department_id"]
        created = draft(
            client,
            hr,
            title="Engineering stand-up moves",
            targets=[{"target_type": "department", "target_id": department_id}],
        )
        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        )
        assert published.json()["recipients"] == 1

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"])
        assert [item["title"] for item in feed.json()] == ["Engineering stand-up moves"]

    def test_an_unknown_target_id_is_refused(self, client: TestClient, hr: dict) -> None:
        """``target_id`` cannot be a foreign key - it points at one of four
        tables - so the service has to check it."""
        response = client.post(
            "/api/v1/admin/announcements",
            json={
                "title": "To nowhere",
                "body": "Body text.",
                "targets": [{"target_type": "department", "target_id": 999_999}],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "department" in response.text

    def test_an_all_target_with_an_id_is_refused(self, client: TestClient, hr: dict) -> None:
        response = client.post(
            "/api/v1/admin/announcements",
            json={
                "title": "Contradictory",
                "body": "Body text.",
                "targets": [{"target_type": "all", "target_id": 3}],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text

    def test_pinned_announcements_come_first(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        ordinary = draft(client, hr, title="Ordinary notice")
        client.post(f"/api/v1/admin/announcements/{ordinary['id']}/publish", json={}, headers=hr)

        pinned = draft(
            client,
            hr,
            title="Pinned notice",
            pin_until=(date.today() + timedelta(days=5)).isoformat(),
        )
        client.post(f"/api/v1/admin/announcements/{pinned['id']}/publish", json={}, headers=hr)

        titles = [
            item["title"]
            for item in client.get("/api/v1/announcements/feed", headers=staff["headers"]).json()
        ]
        assert titles[0] == "Pinned notice"


class TestReadState:
    def test_reading_is_recorded_and_acknowledgement_is_separate(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr, priority="critical")
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        read = client.post(
            f"/api/v1/announcements/{created['id']}/read",
            json={"acknowledge": False},
            headers=staff["headers"],
        )
        assert read.status_code == 204

        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"]).json()
        assert feed[0]["is_read"] is True
        assert feed[0]["is_acknowledged"] is False

        report = client.get(
            f"/api/v1/admin/announcements/{created['id']}/read-report", headers=hr
        ).json()
        assert report["audience_size"] == 1
        assert report["read_count"] == 1
        assert report["acknowledged_count"] == 0
        assert report["unread"] == []

        client.post(
            f"/api/v1/announcements/{created['id']}/read",
            json={"acknowledge": True},
            headers=staff["headers"],
        )
        report = client.get(
            f"/api/v1/admin/announcements/{created['id']}/read-report", headers=hr
        ).json()
        assert report["acknowledged_count"] == 1

    def test_reading_the_same_announcement_twice_does_not_duplicate(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        for _ in range(3):
            client.post(
                f"/api/v1/announcements/{created['id']}/read", json={}, headers=staff["headers"]
            )

        report = client.get(
            f"/api/v1/admin/announcements/{created['id']}/read-report", headers=hr
        ).json()
        assert report["read_count"] == 1

    def test_an_unread_report_names_who_has_not_read_it(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        report = client.get(
            f"/api/v1/admin/announcements/{created['id']}/read-report", headers=hr
        ).json()
        assert report["read_count"] == 0
        assert [row["employee_code"] for row in report["unread"]] == ["ITC0001"]


class TestNotificationCentre:
    def test_the_unread_count_breaks_down_by_category(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        summary = client.get("/api/v1/notifications/unread-count", headers=staff["headers"]).json()
        assert summary["unread_total"] == 1
        assert summary["unread_by_category"] == {"announcement": 1}
        assert summary["has_critical"] is False

    def test_marking_read_clears_the_count(self, client: TestClient, hr: dict, staff: dict) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        marked = client.post("/api/v1/notifications/mark-read", json={}, headers=staff["headers"])
        assert marked.status_code == 200
        assert marked.json()["marked"] == 1

        summary = client.get("/api/v1/notifications/unread-count", headers=staff["headers"]).json()
        assert summary["unread_total"] == 0

        # Marking again changes nothing; it is not an error.
        assert (
            client.post(
                "/api/v1/notifications/mark-read", json={}, headers=staff["headers"]
            ).json()["marked"]
            == 0
        )

    def test_archiving_keeps_the_row(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        """The record of having been told survives the recipient dismissing it."""
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        listed = client.get("/api/v1/notifications", headers=staff["headers"]).json()
        archived = client.post(
            f"/api/v1/notifications/{listed[0]['id']}/archive", headers=staff["headers"]
        )
        assert archived.status_code == 200
        assert archived.json()["archived_at"] is not None

        assert client.get("/api/v1/notifications", headers=staff["headers"]).json() == []
        with_archived = client.get(
            "/api/v1/notifications", params={"include_archived": True}, headers=staff["headers"]
        ).json()
        assert len(with_archived) == 1
        assert session.scalar(select(func.count()).select_from(Notification)) == 1

    def test_one_employee_cannot_archive_anothers_notification(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        listed = client.get("/api/v1/notifications", headers=staff["headers"]).json()

        # The HR account is a different user; the row is not theirs to touch.
        response = client.post(f"/api/v1/notifications/{listed[0]['id']}/archive", headers=hr)
        assert response.status_code == 404, response.text

    def test_muting_a_category_stops_new_notifications(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        muted = client.put(
            "/api/v1/notifications/preferences",
            json={"category": "announcement", "muted": True},
            headers=staff["headers"],
        )
        assert muted.status_code == 200
        assert muted.json() == {"category": "announcement", "muted": True}

        created = draft(client, hr)
        published = client.post(
            f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr
        ).json()
        # Still in the audience; simply not told.
        assert published["recipients"] == 1
        assert published["notifications_created"] == 0
        assert client.get("/api/v1/notifications", headers=staff["headers"]).json() == []

        # The announcement itself is still readable - muting a notification is
        # not the same as being removed from the audience.
        feed = client.get("/api/v1/announcements/feed", headers=staff["headers"]).json()
        assert len(feed) == 1

    def test_preferences_default_to_notifying(self, client: TestClient, staff: dict) -> None:
        rows = client.get("/api/v1/notifications/preferences", headers=staff["headers"]).json()
        assert rows, "every category should be listed"
        assert all(row["muted"] is False for row in rows)

    def test_an_unknown_category_is_refused(self, client: TestClient, staff: dict) -> None:
        response = client.put(
            "/api/v1/notifications/preferences",
            json={"category": "gossip", "muted": True},
            headers=staff["headers"],
        )
        assert response.status_code == 422, response.text


class TestDeduplication:
    """The brief's requirement: a retried job must not repeat itself."""

    def test_rerunning_the_announcement_schedule_notifies_nobody_twice(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        when = utc_now() - timedelta(minutes=5)
        created = draft(client, hr, publish_at=when.isoformat())
        # Schedule it in the past so the job finds it due.
        session.execute(
            Announcement.__table__.update()
            .where(Announcement.id == created["id"])
            .values(status="scheduled", publish_at=when)
        )
        session.commit()

        first = client.post("/api/v1/admin/announcements/run-schedule", headers=hr).json()
        assert first["published"] == 1
        assert first["notifications_created"] == 1

        second = client.post("/api/v1/admin/announcements/run-schedule", headers=hr).json()
        assert second["published"] == 0
        assert second["notifications_created"] == 0

        assert session.scalar(select(func.count()).select_from(Notification)) == 1

    def test_a_second_fan_out_of_one_announcement_creates_nothing(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        """Simulates a publish that failed partway and was retried."""
        from app.modules.audit.service import AuditContext
        from app.modules.notifications import service as notifications

        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        assert session.scalar(select(func.count()).select_from(Notification)) == 1

        announcement = notifications.get_announcement(session, created["id"])
        again = notifications._fan_out(
            session, announcement=announcement, context=AuditContext(actor_label="test")
        )
        session.commit()
        assert again == 0
        assert session.scalar(select(func.count()).select_from(Notification)) == 1

    def test_rerunning_the_unsettled_attendance_job_notifies_once(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        """A nightly job over the same data must write nothing the second time."""
        from app.modules.attendance import service as attendance
        from app.modules.attendance.models import AttendanceDay
        from app.modules.audit.service import AuditContext

        yesterday = date.today() - timedelta(days=1)
        session.add(
            AttendanceDay(
                employee_id=staff["employee"]["id"],
                work_date=yesterday,
                day_type="working",
                status="missing_punch",
                worked_minutes=0,
                break_minutes=0,
                net_minutes=0,
                late_minutes=0,
                early_exit_minutes=0,
                overtime_minutes=0,
                has_missing_punch=True,
                computed_at=utc_now(),
            )
        )
        session.commit()

        context = AuditContext(actor_label="test")
        first = attendance.notify_unsettled_days(
            session, start=yesterday, end=yesterday, context=context
        )
        session.commit()
        assert first["days_examined"] == 1
        assert first["notifications_created"] == 1

        second = attendance.notify_unsettled_days(
            session, start=yesterday, end=yesterday, context=context
        )
        session.commit()
        assert second["days_examined"] == 1
        assert second["notifications_created"] == 0
        assert second["already_notified"] == 1

        rows = session.scalars(
            select(Notification).where(Notification.category == "attendance")
        ).all()
        assert len(rows) == 1
        assert "not been treated as an absence" in (rows[0].body or "")

    def test_today_is_never_reported_as_unsettled(
        self, client: TestClient, hr: dict, staff: dict, session: Session
    ) -> None:
        """An open shift is not a missing punch."""
        from app.modules.attendance import service as attendance
        from app.modules.attendance.models import AttendanceDay
        from app.modules.audit.service import AuditContext

        today = date.today()
        session.add(
            AttendanceDay(
                employee_id=staff["employee"]["id"],
                work_date=today,
                day_type="working",
                status="incomplete",
                worked_minutes=0,
                break_minutes=0,
                net_minutes=0,
                late_minutes=0,
                early_exit_minutes=0,
                overtime_minutes=0,
                has_missing_punch=True,
                computed_at=utc_now(),
            )
        )
        session.commit()

        result = attendance.notify_unsettled_days(
            session, start=today, end=today, context=AuditContext(actor_label="test")
        )
        session.commit()
        assert result["days_examined"] == 0
        assert result["notifications_created"] == 0


# A one-pixel PNG and a minimal PDF, given as hex so the magic-byte check
# has something real to pass.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
PDF = bytes.fromhex(
    "255044462d312e340a312030206f626a0a3c3c3e3e0a656e646f626a0a"
    "747261696c65720a3c3c3e3e0a2525454f460a"
)


class TestAttachments:
    """Images and attachments, which the milestone asks for by name."""

    def test_an_image_and_an_attachment_can_both_be_set(self, client: TestClient, hr: dict) -> None:
        created = draft(client, hr)

        image = client.post(
            f"/api/v1/admin/announcements/{created['id']}/files?as_image=true",
            files={"file": ("banner.png", PNG, "image/png")},
            headers=hr,
        )
        assert image.status_code == 200, image.text
        assert image.json()["image_file_id"] is not None

        attachment = client.post(
            f"/api/v1/admin/announcements/{created['id']}/files",
            files={"file": ("policy.pdf", PDF, "application/pdf")},
            headers=hr,
        )
        assert attachment.status_code == 200, attachment.text
        body = attachment.json()
        assert body["attachment_file_id"] is not None
        # The two are separate slots, so setting one does not clear the other.
        assert body["image_file_id"] is not None

    def test_a_pdf_cannot_be_the_banner_image(self, client: TestClient, hr: dict) -> None:
        created = draft(client, hr)
        response = client.post(
            f"/api/v1/admin/announcements/{created['id']}/files?as_image=true",
            files={"file": ("policy.pdf", PDF, "application/pdf")},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "image" in response.text

    def test_a_renamed_file_is_refused(self, client: TestClient, hr: dict) -> None:
        """The declared type is checked against the magic bytes."""
        created = draft(client, hr)
        response = client.post(
            f"/api/v1/admin/announcements/{created['id']}/files",
            files={"file": ("not-really.pdf", b"this is plain text", "application/pdf")},
            headers=hr,
        )
        assert response.status_code in (415, 422), response.text

    def test_a_recipient_can_download_it(self, client: TestClient, hr: dict, staff: dict) -> None:
        created = draft(client, hr)
        client.post(
            f"/api/v1/admin/announcements/{created['id']}/files",
            files={"file": ("policy.pdf", PDF, "application/pdf")},
            headers=hr,
        )
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        response = client.get(
            f"/api/v1/announcements/{created['id']}/file", headers=staff["headers"]
        )
        assert response.status_code == 200, response.text
        assert response.content == PDF
        # Never rendered in the application's origin.
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in response.headers["content-disposition"]

    def test_somebody_outside_the_audience_cannot_download_it(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        """404, not 403: a guessed id must not reveal that the notice exists."""
        other = client.post(
            "/api/v1/org/work-locations",
            json={
                "code": "PNQ",
                "name": "Pune Office",
                "timezone": "Asia/Kolkata",
                "state": "Maharashtra",
            },
            headers=hr,
        )
        created = draft(
            client,
            hr,
            title="Pune only",
            targets=[{"target_type": "work_location", "target_id": other.json()["id"]}],
        )
        client.post(
            f"/api/v1/admin/announcements/{created['id']}/files",
            files={"file": ("policy.pdf", PDF, "application/pdf")},
            headers=hr,
        )
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)

        response = client.get(
            f"/api/v1/announcements/{created['id']}/file", headers=staff["headers"]
        )
        assert response.status_code == 404, response.text

    def test_an_announcement_with_no_attachment_is_404(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        client.post(f"/api/v1/admin/announcements/{created['id']}/publish", json={}, headers=hr)
        response = client.get(
            f"/api/v1/announcements/{created['id']}/file", headers=staff["headers"]
        )
        assert response.status_code == 404, response.text


class TestAuthorization:
    def test_an_employee_cannot_author_an_announcement(
        self, client: TestClient, staff: dict
    ) -> None:
        response = client.post(
            "/api/v1/admin/announcements",
            json={"title": "Unauthorised", "body": "Body text."},
            headers=staff["headers"],
        )
        assert response.status_code == 403, response.text

    def test_an_employee_cannot_read_the_read_report(
        self, client: TestClient, hr: dict, staff: dict
    ) -> None:
        created = draft(client, hr)
        response = client.get(
            f"/api/v1/admin/announcements/{created['id']}/read-report", headers=staff["headers"]
        )
        assert response.status_code == 403, response.text

    def test_the_feed_takes_no_employee_id(self, client: TestClient, staff: dict) -> None:
        """There is nothing to tamper with: the audience comes from the token."""
        response = client.get(
            "/api/v1/announcements/feed",
            params={"employee_id": 999},
            headers=staff["headers"],
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_unauthenticated_access_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/notifications").status_code == 401
        assert client.get("/api/v1/announcements/feed").status_code == 401
