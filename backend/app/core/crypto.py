"""Field-level encryption for the few columns that need it.

Scope: bank account numbers and PAN. These are needed *in full* for payroll
output, so they cannot be reduced to a last-four fragment the way Aadhaar is -
and a database dump should not hand them over in plaintext.

This complements, not replaces, encryption at the storage layer. Disk
encryption protects a stolen disk; this protects a leaked dump, a misconfigured
read replica, or a backup copied somewhere it should not be.

Key management
--------------
``IAMS_FIELD_ENCRYPTION_KEY`` holds a urlsafe-base64 32-byte key, generated with
``iams generate-encryption-key``. If it is not set, the encrypt/decrypt helpers
raise :class:`ConfigurationMissing` and the affected fields simply cannot be
stored - which is the safe failure, rather than silently writing plaintext.

**Losing the key means losing the data.** It is not derived from
``IAMS_SECRET_KEY``, deliberately: rotating the token-signing key is a routine
operation, and it must not render every stored bank account unreadable.

Rotation is supported by ``IAMS_FIELD_ENCRYPTION_KEYS_RETIRED``: a
comma-separated list of previous keys, tried on decrypt only. Re-encrypt with
``iams rotate-encryption-key``, then remove the retired entry.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.errors import AppError, ConfigurationMissing

logger = logging.getLogger(__name__)


class DecryptionFailed(AppError):
    """A stored value could not be decrypted with any configured key.

    Almost always a key-management mistake: the key was rotated without
    re-encrypting, or a database was restored alongside the wrong key.
    """

    status_code = 500
    code = "decryption_failed"
    message = (
        "A stored encrypted value could not be read. The field encryption key "
        "may have changed. Contact your administrator."
    )


def generate_key() -> str:
    """A fresh urlsafe-base64 key, suitable for IAMS_FIELD_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")


@lru_cache(maxsize=1)
def _cipher() -> MultiFernet:
    settings = get_settings()
    primary = settings.field_encryption_key

    if not primary:
        raise ConfigurationMissing(
            "Field encryption is not configured, so statutory and bank details "
            "cannot be stored. Generate a key with 'iams generate-encryption-key' "
            "and set IAMS_FIELD_ENCRYPTION_KEY.",
            details=[{"field": "IAMS_FIELD_ENCRYPTION_KEY", "message": "Not set."}],
        )

    keys: list[Fernet] = []
    for raw in [primary.get_secret_value(), *settings.field_encryption_keys_retired]:
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            keys.append(Fernet(candidate.encode("ascii")))
        except (ValueError, TypeError) as exc:
            raise ConfigurationMissing(
                "A field encryption key is not a valid Fernet key. Generate one "
                "with 'iams generate-encryption-key'."
            ) from exc

    # MultiFernet encrypts with the first key and decrypts with any of them,
    # which is what makes rotation possible without downtime.
    return MultiFernet(keys)


def is_configured() -> bool:
    """Whether encryption is available, without raising. Used by health checks."""
    return bool(get_settings().field_encryption_key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt a value for storage. Never log the input."""
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty value.")
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes | None) -> str | None:
    """Decrypt a stored value, or return None when there is nothing stored."""
    if ciphertext is None:
        return None
    try:
        return _cipher().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as exc:
        # Deliberately does not include the ciphertext or the key in the log.
        logger.error("Field decryption failed; check IAMS_FIELD_ENCRYPTION_KEY")
        raise DecryptionFailed() from exc


def reset_cipher_cache() -> None:
    """Drop the cached cipher. Used by tests that change key configuration."""
    _cipher.cache_clear()


def last4(value: str) -> str:
    """The last four characters, for display alongside an encrypted value."""
    cleaned = "".join(char for char in value if char.isalnum())
    return cleaned[-4:] if len(cleaned) >= 4 else cleaned


def mask(value: str, *, visible: int = 4) -> str:
    """Mask all but the trailing characters, e.g. ``XXXXXXXX1234``."""
    cleaned = value.strip()
    if len(cleaned) <= visible:
        return cleaned
    return "X" * (len(cleaned) - visible) + cleaned[-visible:]
