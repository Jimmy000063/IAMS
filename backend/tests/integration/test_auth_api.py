"""Authentication and session endpoint tests against a real database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import LoginAttempt, RefreshToken, User
from tests.conftest import TEST_PASSWORD, auth_header, login

pytestmark = pytest.mark.integration


class TestLogin:
    def test_login_with_username_succeeds(self, client: TestClient, admin_user: User) -> None:
        tokens = login(client, "hradmin")
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["token_type"] == "bearer"
        assert tokens["must_change_password"] is False

    def test_login_with_email_succeeds(self, client: TestClient, admin_user: User) -> None:
        tokens = login(client, "hradmin@example.com")
        assert tokens["access_token"]

    def test_username_is_case_insensitive(self, client: TestClient, admin_user: User) -> None:
        assert login(client, "HRAdmin")["access_token"]

    def test_wrong_password_is_rejected(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_failed"

    def test_unknown_account_is_indistinguishable_from_a_wrong_password(
        self, client: TestClient, admin_user: User
    ) -> None:
        """The response must not reveal whether an account exists."""
        wrong_password = client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        no_such_user = client.post(
            "/api/v1/auth/login", json={"identifier": "ghost", "password": "Wr0ng!Password"}
        )
        assert wrong_password.status_code == no_such_user.status_code == 401
        assert wrong_password.json()["error"] == no_such_user.json()["error"]

    def test_inactive_account_cannot_sign_in(
        self, client: TestClient, session: Session, admin_user: User, employee_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        response = client.patch(
            f"/api/v1/auth/users/{employee_user.id}/active",
            json={"is_active": False, "reason": "Left the company"},
            headers=headers,
        )
        assert response.status_code == 200

        denied = client.post(
            "/api/v1/auth/login",
            json={"identifier": "e.employee", "password": TEST_PASSWORD},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "account_inactive"

    def test_response_carries_a_request_id(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        assert response.headers["X-Request-ID"]
        assert response.json()["request_id"] == response.headers["X-Request-ID"]


class TestLoginThrottling:
    def test_failed_attempts_are_recorded_despite_the_request_rolling_back(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """Regression test for rate limiting.

        A failed sign-in raises, so the request's transaction is rolled back. If
        the attempt row shared that transaction it would disappear and throttling
        would never trigger.
        """
        client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        recorded = session.scalar(
            select(func.count()).select_from(LoginAttempt).where(LoginAttempt.succeeded.is_(False))
        )
        assert recorded == 1

    def test_repeated_failures_lock_the_account(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        for _ in range(5):
            response = client.post(
                "/api/v1/auth/login",
                json={"identifier": "hradmin", "password": "Wr0ng!Password"},
            )
            assert response.status_code == 401

        throttled = client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        assert throttled.status_code == 429
        assert throttled.json()["error"]["code"] in {"account_locked", "rate_limited"}

    def test_the_correct_password_is_refused_while_locked(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """Otherwise the lockout would be trivially bypassable."""
        for _ in range(5):
            client.post(
                "/api/v1/auth/login",
                json={"identifier": "hradmin", "password": "Wr0ng!Password"},
            )
        response = client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": TEST_PASSWORD}
        )
        assert response.status_code == 429

    def test_failure_counter_persists_on_the_user_row(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        session.expire_all()
        assert session.get(User, admin_user.id).failed_login_count == 1

    def test_successful_login_clears_the_counter(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        login(client, "hradmin")
        session.expire_all()
        refreshed = session.get(User, admin_user.id)
        assert refreshed.failed_login_count == 0
        assert refreshed.last_login_at is not None


class TestTokenUsage:
    def test_protected_endpoint_requires_a_token(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"

    def test_garbage_token_is_rejected(self, client: TestClient, admin_user: User) -> None:
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    def test_me_returns_roles_and_permissions(self, client: TestClient, admin_user: User) -> None:
        response = client.get("/api/v1/auth/me", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "hradmin"
        assert [role["code"] for role in body["roles"]] == ["hr_admin"]
        assert "settings.manage" in body["permissions"]

    def test_employee_permissions_are_limited(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.get("/api/v1/auth/me", headers=auth_header(client, "e.employee"))
        permissions = response.json()["permissions"]
        assert "attendance.punch" in permissions
        assert "settings.manage" not in permissions
        assert "payroll.finalize" not in permissions


class TestRefreshAndLogout:
    def test_refresh_issues_a_new_pair(self, client: TestClient, admin_user: User) -> None:
        tokens = login(client, "hradmin")
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 200
        assert response.json()["refresh_token"] != tokens["refresh_token"]

    def test_rotated_token_cannot_be_reused(self, client: TestClient, admin_user: User) -> None:
        tokens = login(client, "hradmin")
        client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401

    def test_token_reuse_revokes_every_session(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """Replay means the token may be stolen, so all sessions end."""
        first = login(client, "hradmin")
        second = login(client, "hradmin")
        rotated = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        ).json()

        client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})

        for token in (second["refresh_token"], rotated["refresh_token"]):
            assert (
                client.post("/api/v1/auth/refresh", json={"refresh_token": token}).status_code
                == 401
            )

        session.expire_all()
        live = session.scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == admin_user.id, RefreshToken.revoked_at.is_(None))
        )
        assert live == 0

    def test_unknown_refresh_token_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "nonexistent-token-value"}
        )
        assert response.status_code == 401

    def test_logout_revokes_the_supplied_session_only(
        self, client: TestClient, admin_user: User
    ) -> None:
        keep = login(client, "hradmin")
        drop = login(client, "hradmin")

        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": drop["refresh_token"]},
            headers={"Authorization": f"Bearer {drop['access_token']}"},
        )
        assert response.status_code == 204

        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": drop["refresh_token"]}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": keep["refresh_token"]}
            ).status_code
            == 200
        )

    def test_retrying_a_logged_out_token_does_not_end_other_sessions(
        self, client: TestClient, admin_user: User
    ) -> None:
        """A stale tab retrying after sign-out is not token theft.

        Only replay of a *rotated* token is treated as theft. Treating an
        explicitly revoked token the same way would sign the user out of every
        other device whenever a background tab retried.
        """
        keep = login(client, "hradmin")
        drop = login(client, "hradmin")
        client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": drop["refresh_token"]},
            headers={"Authorization": f"Bearer {drop['access_token']}"},
        )

        # Retry the logged-out token twice, as a stale client would.
        for _ in range(2):
            assert (
                client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": drop["refresh_token"]}
                ).status_code
                == 401
            )

        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": keep["refresh_token"]}
            ).status_code
            == 200
        )

    def test_logout_all_revokes_every_session(self, client: TestClient, admin_user: User) -> None:
        first = login(client, "hradmin")
        second = login(client, "hradmin")
        client.post(
            "/api/v1/auth/logout",
            json={"all_sessions": True},
            headers={"Authorization": f"Bearer {first['access_token']}"},
        )
        for token in (first["refresh_token"], second["refresh_token"]):
            assert (
                client.post("/api/v1/auth/refresh", json={"refresh_token": token}).status_code
                == 401
            )


class TestForcedPasswordChange:
    def test_temporary_password_blocks_protected_endpoints(
        self, client: TestClient, pending_user: User
    ) -> None:
        tokens = login(client, "newjoiner")
        assert tokens["must_change_password"] is True

        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        blocked = client.get("/api/v1/settings", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "password_change_required"

    def test_me_stays_reachable_so_the_client_can_redirect(
        self, client: TestClient, pending_user: User
    ) -> None:
        tokens = login(client, "newjoiner")
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert response.status_code == 200
        assert response.json()["must_change_password"] is True

    def test_changing_the_password_clears_the_block(
        self, client: TestClient, session: Session, pending_user: User
    ) -> None:
        tokens = login(client, "newjoiner")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        response = client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "BrandNew!Pass99"},
            headers=headers,
        )
        assert response.status_code == 204

        fresh = login(client, "newjoiner", "BrandNew!Pass99")
        assert fresh["must_change_password"] is False

    def test_old_sessions_end_when_the_password_changes(
        self, client: TestClient, pending_user: User
    ) -> None:
        tokens = login(client, "newjoiner")
        client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "BrandNew!Pass99"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert (
            client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            ).status_code
            == 401
        )

    def test_wrong_current_password_is_refused(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": "Not!TheOne99", "new_password": "BrandNew!Pass99"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 401

    def test_weak_new_password_is_refused(self, client: TestClient, employee_user: User) -> None:
        response = client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": "allsmallnosymbols"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] in {"validation_error", "policy_violation"}

    def test_reusing_the_same_password_is_refused(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.post(
            "/api/v1/auth/me/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422


class TestAccountProvisioning:
    def test_there_is_no_public_registration_endpoint(self, client: TestClient) -> None:
        """The brief requires HR-created accounts only."""
        for path in ("/api/v1/auth/register", "/api/v1/auth/signup", "/api/v1/register"):
            assert client.post(path, json={}).status_code == 404

    def test_creating_a_user_requires_permission(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.post(
            "/api/v1/auth/users",
            json={"username": "intruder", "role_codes": ["hr_admin"]},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"

    def test_hr_can_create_an_account_and_receives_a_temporary_password(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.post(
            "/api/v1/auth/users",
            json={
                "username": "p.nair",
                "email": "p.nair@example.com",
                "role_codes": ["employee"],
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["username"] == "p.nair"
        assert len(body["temporary_password"]) >= 12

        issued = login(client, "p.nair", body["temporary_password"])
        assert issued["must_change_password"] is True

    def test_duplicate_username_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        payload = {"username": "dupe.user", "role_codes": ["employee"]}
        assert client.post("/api/v1/auth/users", json=payload, headers=headers).status_code == 201
        second = client.post("/api/v1/auth/users", json=payload, headers=headers)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "conflict"

    def test_duplicate_username_differing_only_in_case_is_rejected(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.post(
            "/api/v1/auth/users",
            json={"username": "case.test", "role_codes": ["employee"]},
            headers=headers,
        )
        clash = client.post(
            "/api/v1/auth/users",
            json={"username": "Case.Test", "role_codes": ["employee"]},
            headers=headers,
        )
        assert clash.status_code == 409

    def test_unknown_role_is_rejected(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/auth/users",
            json={"username": "x.yz", "role_codes": ["superuser"]},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_hr_reset_forces_a_change_and_ends_sessions(
        self, client: TestClient, employee_user: User, admin_user: User
    ) -> None:
        employee_tokens = login(client, "e.employee")

        response = client.post(
            f"/api/v1/auth/users/{employee_user.id}/reset-password",
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200
        temporary = response.json()["temporary_password"]

        assert (
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": employee_tokens["refresh_token"]},
            ).status_code
            == 401
        )
        assert login(client, "e.employee", temporary)["must_change_password"] is True

    def test_employee_cannot_reset_another_users_password(
        self, client: TestClient, employee_user: User, admin_user: User
    ) -> None:
        response = client.post(
            f"/api/v1/auth/users/{admin_user.id}/reset-password",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_deactivation_revokes_live_sessions(
        self, client: TestClient, employee_user: User, admin_user: User
    ) -> None:
        employee_tokens = login(client, "e.employee")
        client.patch(
            f"/api/v1/auth/users/{employee_user.id}/active",
            json={"is_active": False, "reason": "Offboarded"},
            headers=auth_header(client, "hradmin"),
        )
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {employee_tokens['access_token']}"},
            ).status_code
            == 403
        )


class TestRoleAndPermissionListing:
    def test_hr_can_list_roles_with_their_grants(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/api/v1/auth/roles", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        roles = {role["code"]: role for role in response.json()}
        assert set(roles) == {"hr_admin", "employee"}
        assert roles["hr_admin"]["is_system"] is True
        assert len(roles["hr_admin"]["permissions"]) > len(roles["employee"]["permissions"])

    def test_employee_cannot_list_roles(self, client: TestClient, employee_user: User) -> None:
        response = client.get("/api/v1/auth/roles", headers=auth_header(client, "e.employee"))
        assert response.status_code == 403

    def test_permission_catalogue_is_persisted(self, client: TestClient, admin_user: User) -> None:
        from app.core.permissions import PERMISSIONS

        response = client.get("/api/v1/auth/permissions", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        assert len(response.json()) == len(PERMISSIONS)
