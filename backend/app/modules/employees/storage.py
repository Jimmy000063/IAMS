"""Secure local file storage for employment documents.

Threat model this addresses
---------------------------
* **Path traversal** - the stored name is a random key the server generates; the
  uploaded filename is never used to build a path, only retained for display.
* **Content-type spoofing** - the declared type is checked against an allow-list
  *and* against the file's magic bytes, so renaming ``payload.html`` to
  ``offer.pdf`` does not get it served as a PDF.
* **Disk exhaustion** - the stream is read in bounded chunks and aborted the
  moment it exceeds the configured limit, rather than buffered whole and then
  measured.
* **Served as active content** - downloads always use
  ``Content-Disposition: attachment`` with ``X-Content-Type-Options: nosniff``,
  so a stored file cannot execute in the context of the application's origin.

The backend is deliberately a thin seam. Swapping local disk for object storage
means implementing ``save``/``open``/``delete`` elsewhere; nothing above this
module knows where bytes live.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ValidationFailed

#: Document formats HR realistically needs, and nothing that a browser would
#: execute. No SVG (it can carry script), no HTML, no archives.
ALLOWED_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (".docx",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (".xlsx",),
}

#: Leading bytes that must be present for the declared type. A declared type
#: with no signature here is rejected rather than trusted.
MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    # Both OOXML formats are ZIP containers.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
}

CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class SavedFile:
    storage_key: str
    size_bytes: int
    sha256: str
    content_type: str
    original_filename: str


def _storage_root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_display_name(filename: str) -> str:
    """Keep a readable name for display, stripped of anything path-like."""
    cleaned = (filename or "upload").replace("\\", "/").split("/")[-1]
    cleaned = "".join(char for char in cleaned if char.isalnum() or char in "._- ()").strip()
    return cleaned[:255] or "upload"


def validate_content_type(declared: str | None, filename: str) -> str:
    content_type = (declared or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationFailed(
            "That file type is not accepted. Upload a PDF, JPEG, PNG, DOCX or XLSX.",
            details=[
                {"field": "file", "message": f"Unsupported type: {content_type or 'unknown'}"}
            ],
        )

    suffixes = ALLOWED_CONTENT_TYPES[content_type]
    extension = Path(_safe_display_name(filename)).suffix.lower()
    if extension and extension not in suffixes:
        raise ValidationFailed(
            f"The file extension {extension} does not match the file type {content_type}.",
            details=[{"field": "file", "message": "Extension and type disagree."}],
        )
    return content_type


def _verify_magic(content_type: str, head: bytes) -> None:
    signatures = MAGIC_SIGNATURES.get(content_type)
    if not signatures:  # pragma: no cover - allow-list and signatures are kept in step
        raise ValidationFailed("That file type cannot be verified and was not stored.")
    if not any(head.startswith(signature) for signature in signatures):
        raise ValidationFailed(
            "The file's contents do not match its declared type.",
            details=[{"field": "file", "message": "Renaming a file does not change its type."}],
        )


def save_upload(*, stream, filename: str, declared_content_type: str | None) -> SavedFile:
    """Stream an upload to disk, validating size and real content type.

    ``stream`` is any object with a ``read(size)`` method - a Starlette
    ``UploadFile.file`` in practice.
    """
    settings = get_settings()
    content_type = validate_content_type(declared_content_type, filename)
    display_name = _safe_display_name(filename)

    # The key is random and date-partitioned. It never contains user input, so
    # no filename can escape the storage root.
    today = datetime.now(UTC).strftime("%Y/%m")
    key = f"{today}/{secrets.token_urlsafe(24)}"
    destination = _storage_root() / key
    destination.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    total = 0
    head = b""

    try:
        with destination.open("wb") as handle:
            while chunk := stream.read(CHUNK_SIZE):
                if not head:
                    head = chunk[:16]
                    _verify_magic(content_type, head)
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise ValidationFailed(
                        f"The file is larger than {settings.max_upload_bytes // 1024 // 1024} MB.",
                        details=[{"field": "file", "message": "File too large."}],
                    )
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        # Never leave a partial or rejected file behind.
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise ValidationFailed(
            "The file is empty.", details=[{"field": "file", "message": "No content."}]
        )

    return SavedFile(
        storage_key=key,
        size_bytes=total,
        sha256=digest.hexdigest(),
        content_type=content_type,
        original_filename=display_name,
    )


def resolve_path(storage_key: str) -> Path:
    """Map a storage key back to a path, refusing anything outside the root.

    The containment check is belt and braces - keys are server-generated - but
    it is the one assertion that makes a future bug in key handling harmless
    rather than an arbitrary file read.
    """
    root = _storage_root()
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root):
        raise ValidationFailed("Invalid file reference.")
    if not candidate.is_file():
        from app.core.errors import NotFound

        raise NotFound("The stored file is missing.")
    return candidate


def delete(storage_key: str) -> None:
    # Best effort: a file that is already gone, or a key that no longer
    # resolves, is not a reason to fail the caller's transaction.
    with contextlib.suppress(Exception):
        resolve_path(storage_key).unlink(missing_ok=True)
