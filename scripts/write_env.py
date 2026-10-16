"""Fill in the generated secrets in a freshly copied backend/.env.

Called by setup.ps1. Kept as a script rather than inlined into the shell
because the file is written with an explicit encoding and no byte-order mark:
a BOM at the top of a .env file is invisible in an editor and turns the first
variable's name into something that does not match.

Refuses to touch a file that already holds a real key, so running setup again
cannot replace the field encryption key - which would make every bank account
and PAN already encrypted in the database unreadable.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

PLACEHOLDER_SECRET = "dev-only-insecure-secret-change-me"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: write_env.py <path to .env>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    current_field_key = re.search(r"^IAMS_FIELD_ENCRYPTION_KEY=(.*)$", text, re.MULTILINE)
    if current_field_key is not None and current_field_key.group(1).strip():
        print("  field encryption key already set - leaving this file alone", file=sys.stderr)
        return 0

    from cryptography.fernet import Fernet

    text = re.sub(
        r"^IAMS_SECRET_KEY=.*$",
        "IAMS_SECRET_KEY=" + secrets.token_urlsafe(48),
        text,
        count=1,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^IAMS_FIELD_ENCRYPTION_KEY=.*$",
        "IAMS_FIELD_ENCRYPTION_KEY=" + Fernet.generate_key().decode(),
        text,
        count=1,
        flags=re.MULTILINE,
    )

    if PLACEHOLDER_SECRET in text:
        print("  the placeholder secret is still present - check the file", file=sys.stderr)
        return 1

    path.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
