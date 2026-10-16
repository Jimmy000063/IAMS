#!/usr/bin/env bash
#
# Install everything a fresh checkout needs, on macOS or Linux.
#
# Creates the backend virtual environment, installs the pinned Python and Node
# dependencies, and writes the environment file if it is not already there -
# generating real secrets rather than leaving the placeholders in place.
#
# Safe to run again. It never overwrites an existing .env, because that file
# holds the field encryption key, and replacing that key makes every encrypted
# bank account and PAN already in the database unreadable.
#
# Usage:
#   ./scripts/setup.sh                     # use python3 from PATH
#   PYTHON=/usr/bin/python3.12 ./scripts/setup.sh
#   SKIP_FRONTEND=1 ./scripts/setup.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

PYTHON="${PYTHON:-python3}"
SKIP_FRONTEND="${SKIP_FRONTEND:-}"

step() { printf '\n==> %s\n' "$1"; }
note() { printf '    %s\n' "$1"; }

# -----------------------------------------------------------------------------
# Python interpreter
# -----------------------------------------------------------------------------

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "No '$PYTHON' found. Install Python 3.12 or newer, or set PYTHON to a full path." >&2
  exit 1
fi

VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Python $VERSION found, but this project needs 3.12 or newer." >&2
  exit 1
fi

step "Python $VERSION"
note "$(command -v "$PYTHON")"

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

VENV="$BACKEND/.venv"
VENV_PYTHON="$VENV/bin/python"

if [ -x "$VENV_PYTHON" ]; then
  VENV_VERSION="$("$VENV_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [ "$VENV_VERSION" != "$VERSION" ]; then
    echo "The virtual environment at $VENV was built with Python $VENV_VERSION," >&2
    echo "but you are running setup with $VERSION. Mixing them produces import" >&2
    echo "errors that look like corrupt packages. Delete the folder and retry." >&2
    exit 1
  fi
  step "Virtual environment already present"
else
  step "Creating the virtual environment"
  "$PYTHON" -m venv "$VENV"
fi

step "Installing Python dependencies"
note "Pinned versions from backend/requirements-dev.txt"
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
(
  cd "$BACKEND"
  "$VENV_PYTHON" -m pip install --quiet -r requirements-dev.txt
  # The project itself, editable, without letting pip re-resolve anything: the
  # pins above are the versions we want.
  "$VENV_PYTHON" -m pip install --quiet -e . --no-deps
)

# -----------------------------------------------------------------------------
# Backend environment file
# -----------------------------------------------------------------------------

ENV_FILE="$BACKEND/.env"
if [ -f "$ENV_FILE" ]; then
  step "backend/.env already exists - left untouched"
  note "Delete it yourself if you want a fresh one. Keep a copy of"
  note "IAMS_FIELD_ENCRYPTION_KEY first: without it, encrypted bank details"
  note "and PANs already in the database cannot be read back."
else
  step "Writing backend/.env with freshly generated keys"
  cp "$BACKEND/.env.example" "$ENV_FILE"

  SECRET="$("$VENV_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  FIELD_KEY="$("$VENV_PYTHON" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

  "$VENV_PYTHON" - "$ENV_FILE" "$SECRET" "$FIELD_KEY" <<'PY'
import re
import sys

path, secret, field_key = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    text = handle.read()
text = re.sub(r"IAMS_SECRET_KEY=.*", "IAMS_SECRET_KEY=" + secret, text)
text = re.sub(r"IAMS_FIELD_ENCRYPTION_KEY=.*", "IAMS_FIELD_ENCRYPTION_KEY=" + field_key, text)
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
PY

  note "Both keys are new and belong to this machine only."
fi

# -----------------------------------------------------------------------------
# Frontend
# -----------------------------------------------------------------------------

if [ -z "$SKIP_FRONTEND" ]; then
  if ! command -v npm >/dev/null 2>&1; then
    step "npm not found - skipping the frontend"
    note "Install Node 20 or newer, then run: cd frontend && npm ci"
  else
    step "Installing frontend dependencies (Node $(node --version))"
    note "Exact versions from frontend/package-lock.json"
    # `npm ci` rather than `npm install`: it installs exactly what the lock
    # file says and fails if the lock and package.json disagree, which is what
    # makes two machines match.
    (cd "$FRONTEND" && npm ci --silent)
  fi
fi

# -----------------------------------------------------------------------------
# What is left to do
# -----------------------------------------------------------------------------

cat <<'DONE'

Dependencies installed.

Still to do, because these need a database rather than a package manager:

  1. Create the roles and databases, once per machine:
       psql -U postgres -f infra/postgres/local-setup.sql
       psql -U postgres -d iams -f infra/postgres/local-grants.sql
       psql -U postgres -d iams_test -f infra/postgres/local-grants.sql

  2. Apply the migrations and seed the catalogues:
       cd backend
       source .venv/bin/activate
       alembic upgrade head
       iams seed-permissions
       iams seed-settings
       iams create-admin

  3. Run it, in two terminals:
       cd backend  && source .venv/bin/activate && uvicorn app.main:app --reload --port 8001
       cd frontend && npm run dev

  The API is on http://localhost:8001 and the interface on http://localhost:5173.
  Full detail, including what to do when something fails, is in docs/setup.md.
DONE
