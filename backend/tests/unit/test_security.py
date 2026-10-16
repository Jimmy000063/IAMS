"""Password hashing, policy and token tests. No database required."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.errors import NotAuthenticated, ValidationFailed
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    generate_temporary_password,
    hash_password,
    hash_refresh_token,
    validate_password_strength,
    verify_password,
)

pytestmark = pytest.mark.unit


class TestPasswordHashing:
    def test_hash_is_argon2id_and_not_the_plaintext(self) -> None:
        digest = hash_password("CorrectHorse!99")
        assert digest.startswith("$argon2id$")
        assert "CorrectHorse!99" not in digest

    def test_same_password_hashes_differently(self) -> None:
        """A per-hash salt means identical passwords must not share a digest."""
        assert hash_password("CorrectHorse!99") != hash_password("CorrectHorse!99")

    def test_verify_accepts_correct_and_rejects_wrong(self) -> None:
        digest = hash_password("CorrectHorse!99")
        assert verify_password("CorrectHorse!99", digest) is True
        assert verify_password("correcthorse!99", digest) is False
        assert verify_password("", digest) is False

    @pytest.mark.parametrize("stored", [None, "", "not-a-hash", "$argon2id$garbage"])
    def test_verify_returns_false_for_unusable_stored_value(self, stored: str | None) -> None:
        """Must not raise: a raised exception type would be an account oracle."""
        assert verify_password("anything", stored) is False

    def test_oversized_password_is_rejected_not_hashed(self) -> None:
        """Guards against Argon2 CPU exhaustion from a huge submitted value."""
        oversized = "x" * (MAX_PASSWORD_LENGTH + 1)
        with pytest.raises(ValidationFailed):
            hash_password(oversized)
        assert verify_password(oversized, hash_password("Sh0rt!Abcdefg")) is False


class TestPasswordPolicy:
    @pytest.mark.parametrize(
        "password",
        [
            "short!A1",  # too short
            "alllowercase!1aaaa",  # no uppercase
            "ALLUPPERCASE!1AAAA",  # no lowercase
            "NoDigitsHere!abcde",  # no digit
            "NoSymbols1abcdefgh",  # no symbol
        ],
    )
    def test_rejects_weak_passwords(self, password: str) -> None:
        with pytest.raises(ValidationFailed):
            validate_password_strength(password)

    def test_rejects_password_containing_username(self) -> None:
        with pytest.raises(ValidationFailed):
            validate_password_strength("Rajesh!Kumar2026", username="rajesh")

    def test_accepts_a_compliant_password(self) -> None:
        validate_password_strength("Str0ng&Passphrase", username="someone")

    def test_error_lists_every_unmet_rule(self) -> None:
        """So the user is not made to fix one rule at a time."""
        with pytest.raises(ValidationFailed) as exc_info:
            validate_password_strength("short")
        assert len(exc_info.value.details) >= 3

    def test_generated_temporary_password_satisfies_the_policy(self) -> None:
        for _ in range(25):
            validate_password_strength(generate_temporary_password())

    def test_generated_passwords_are_unique(self) -> None:
        assert len({generate_temporary_password() for _ in range(50)}) == 50


class TestAccessTokens:
    def test_round_trip_preserves_identity(self) -> None:
        issued = create_access_token(4242)
        claims = decode_access_token(issued.token)
        assert claims.user_id == 4242
        assert claims.jti == issued.jti

    def test_each_token_has_a_distinct_id(self) -> None:
        """A unique jti is what makes individual token revocation possible later."""
        assert create_access_token(1).jti != create_access_token(1).jti

    def test_expired_token_is_rejected(self) -> None:
        issued = create_access_token(1, ttl=timedelta(seconds=-10))
        with pytest.raises(NotAuthenticated):
            decode_access_token(issued.token)

    @pytest.mark.parametrize(
        "token",
        ["", "garbage", "a.b.c", "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0."],
    )
    def test_malformed_or_unsigned_tokens_are_rejected(self, token: str) -> None:
        with pytest.raises(NotAuthenticated):
            decode_access_token(token)

    def test_tampered_signature_is_rejected(self) -> None:
        token = create_access_token(1).token
        header, payload, _ = token.split(".")
        with pytest.raises(NotAuthenticated):
            decode_access_token(f"{header}.{payload}.AAAAAAAAAAAAAAAAAAAAAA")

    def test_token_signed_with_another_key_is_rejected(self) -> None:
        import jwt

        forged = jwt.encode(
            {"sub": "1", "typ": "access", "jti": "x", "iat": 0, "exp": 9_999_999_999},
            "a-different-secret-key-entirely-and-long-enough-for-hs256",
            algorithm="HS256",
        )
        with pytest.raises(NotAuthenticated):
            decode_access_token(forged)


class TestRefreshTokens:
    def test_tokens_are_long_and_unique(self) -> None:
        tokens = {generate_refresh_token() for _ in range(100)}
        assert len(tokens) == 100
        assert all(len(token) >= 32 for token in tokens)

    def test_digest_is_stable_and_hides_the_token(self) -> None:
        token = generate_refresh_token()
        digest = hash_refresh_token(token)
        assert digest == hash_refresh_token(token)
        assert token not in digest
        assert len(digest) == 64
