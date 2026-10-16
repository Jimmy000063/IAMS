"""Password hashing, token issuance and password-policy checks.

Design notes
------------
* Passwords are hashed with **Argon2id** (``argon2-cffi``). Parameters live in
  settings so they can be raised as hardware improves; :func:`needs_rehash` lets
  us transparently upgrade a user's hash on their next successful sign-in.
* Access tokens are short-lived signed JWTs carrying **only identity**, never a
  permission list. Permissions are read from the database on every request, so
  revoking a role takes effect immediately instead of at token expiry.
* Refresh tokens are **opaque random strings**, not JWTs. Only their SHA-256
  digest is stored, so a database disclosure does not yield usable tokens, and
  they can be revoked individually or per user.
"""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.timeutil import utc_now

TOKEN_TYPE_ACCESS = "access"

# Characters for generated temporary passwords: unambiguous set, no 0/O/1/l/I.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
"""Upper bound guards against Argon2 denial-of-service via multi-megabyte inputs."""


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
    )


# --------------------------------------------------------------------- hashing


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id. Never log or store the input."""
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationFailed(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    return _hasher().hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time-ish verification that does not distinguish failure causes.

    Returns ``False`` for a missing hash or a malformed stored value rather than
    raising, so callers cannot use the exception type as an account oracle.
    """
    if not password_hash:
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than currently configured."""
    try:
        return _hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ------------------------------------------------------------- password policy


def validate_password_strength(password: str, *, username: str | None = None) -> None:
    """Enforce the baseline password policy.

    Deliberately simple and transparent: length plus character variety plus a
    check against the username. Raises :class:`ValidationFailed` listing every
    unmet rule at once so the user does not have to guess one rule at a time.
    """
    problems: list[str] = []

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"be at least {MIN_PASSWORD_LENGTH} characters long")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"be at most {MAX_PASSWORD_LENGTH} characters long")
    if not any(c.islower() for c in password):
        problems.append("contain a lowercase letter")
    if not any(c.isupper() for c in password):
        problems.append("contain an uppercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("contain a digit")
    if not any(c in string.punctuation for c in password):
        problems.append("contain a symbol")
    if username and len(username) >= 3 and username.lower() in password.lower():
        problems.append("not contain your username")

    if problems:
        raise ValidationFailed(
            "Password must " + ", ".join(problems) + ".",
            details=[{"field": "password", "message": f"Must {problem}."} for problem in problems],
        )


def generate_temporary_password(length: int = 14) -> str:
    """Generate a temporary password that satisfies :func:`validate_password_strength`.

    Used by HR when provisioning an account. The employee is forced to change it
    on first sign-in (``users.must_change_password``).
    """
    if length < MIN_PASSWORD_LENGTH:
        length = MIN_PASSWORD_LENGTH
    while True:
        candidate = "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length - 2))
        candidate += secrets.choice("23456789") + secrets.choice("!@#$%^&*?-_")
        try:
            validate_password_strength(candidate)
        except ValidationFailed:
            continue
        return candidate


# ---------------------------------------------------------------------- tokens


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    jti: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: int
    jti: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(user_id: int, *, ttl: timedelta | None = None) -> AccessToken:
    settings = get_settings()
    now = utc_now()
    lifetime = ttl or timedelta(minutes=settings.access_token_ttl_minutes)
    expires_at = now + lifetime
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "typ": TOKEN_TYPE_ACCESS,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return AccessToken(token=token, jti=jti, expires_at=expires_at)


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify and decode an access token.

    Raises :class:`app.core.errors.NotAuthenticated` for anything wrong with the
    token - expired, wrong type, bad signature, malformed - without revealing
    which, since the client cannot act on the distinction.
    """
    from app.core.errors import NotAuthenticated  # local import avoids a cycle

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise NotAuthenticated("Your session has expired. Please sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise NotAuthenticated("Invalid authentication token.") from exc

    if payload.get("typ") != TOKEN_TYPE_ACCESS:
        raise NotAuthenticated("Invalid authentication token.")

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise NotAuthenticated("Invalid authentication token.") from exc

    return AccessTokenClaims(
        user_id=user_id,
        jti=str(payload["jti"]),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )


def generate_refresh_token() -> str:
    """A 256-bit opaque refresh token. Returned to the client exactly once."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """SHA-256 digest stored in place of the refresh token itself.

    A fast digest is correct here (unlike for passwords): the token is
    high-entropy random, so brute-forcing the digest is infeasible and we avoid
    an Argon2 cost on every token refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
