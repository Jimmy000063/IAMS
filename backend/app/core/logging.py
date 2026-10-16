"""Logging configuration.

A filter strips values that must never reach a log file. The brief forbids
logging passwords, tokens, bank details and unnecessary personal information;
services are written not to log them, and this is the backstop for a mistake in
a message template or a third-party library.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Final

#: Patterns rewritten before a record is emitted. Each captures the key or prefix
#: so the redacted line still shows *what* was suppressed.
_REDACTION_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Bearer must be handled before the key/value rule below. Otherwise that
    # rule treats the literal word "Bearer" as the value of `Authorization:` and
    # replaces only it, leaving the actual token in the log line.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer [redacted]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)"
            r"(\"?\s*[:=]\s*\"?)([^\s,\"'}&]+)"
        ),
        r"\1\2[redacted]",
    ),
    # Long digit runs are most likely bank account numbers or Aadhaar numbers.
    (re.compile(r"\b\d{9,18}\b"), "[redacted-number]"),
)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a broken format string
            return True

        redacted = message
        for pattern, replacement in _REDACTION_PATTERNS:
            redacted = pattern.sub(replacement, redacted)

        if redacted != message:
            # Replace the message and drop args, which have been folded in.
            record.msg = redacted
            record.args = ()
        return True


_CONFIGURED = False


def configure_logging(*, debug: bool = False) -> None:
    """Configure root logging once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # SQLAlchemy echo is controlled by the engine's `echo` setting; keep its own
    # logger quiet so enabling DEBUG elsewhere does not dump every statement
    # (which would also defeat the point of the redaction filter).
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _CONFIGURED = True
