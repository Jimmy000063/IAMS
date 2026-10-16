<#
.SYNOPSIS
    Install everything a fresh checkout needs, on Windows.

.DESCRIPTION
    Creates the backend virtual environment, installs the pinned Python and
    Node dependencies, and writes the two environment files if they are not
    already there - generating real secrets rather than leaving the
    placeholders in place.

    Safe to run again. It never overwrites an existing .env, because that file
    holds the field encryption key, and replacing that key makes every
    encrypted bank account and PAN already in the database unreadable.

.PARAMETER Python
    Full path to the Python interpreter to build the virtual environment with.
    Give this explicitly if `python` on your PATH is an Anaconda base
    environment or an older version: a virtual environment built by the wrong
    interpreter fails later in ways that look like corrupt packages.

.PARAMETER SkipFrontend
    Install the backend only.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -Python "C:\Program Files\Python314\python.exe"
#>

[CmdletBinding()]
param(
    [string] $Python = "",
    [switch] $SkipFrontend
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

function Write-Step($text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}

function Write-Note($text) {
    Write-Host "    $text" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# Python interpreter
# -----------------------------------------------------------------------------

if ($Python -eq "") {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        throw "No 'python' on PATH. Install Python 3.12 or newer, or pass -Python with a full path."
    }
    $Python = $found.Source
}

if (-not (Test-Path $Python)) {
    throw "No interpreter at '$Python'."
}

$versionText = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
$version = [version] $versionText
if ($version -lt [version] "3.12") {
    throw "Python $versionText found at '$Python', but this project needs 3.12 or newer."
}

Write-Step "Python $versionText"
Write-Note $Python
if ($Python -match "conda|anaconda|miniconda") {
    Write-Note "This looks like a conda interpreter. If the install misbehaves,"
    Write-Note "re-run with -Python pointing at a standalone CPython."
}

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

$venv = Join-Path $backend ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (Test-Path $venvPython) {
    $venvVersion = & $venvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($venvVersion -ne $versionText) {
        throw @"
The existing virtual environment at
  $venv
was built with Python $venvVersion, but you are running setup with $versionText.
Mixing them produces import errors that look like corrupt packages. Delete the
folder and run this again.
"@
    }
    Write-Step "Virtual environment already present"
} else {
    Write-Step "Creating the virtual environment"
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

Write-Step "Installing Python dependencies"
Write-Note "Pinned versions from backend/requirements-dev.txt"
& $venvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }

Push-Location $backend
try {
    & $venvPython -m pip install --quiet -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "Installing requirements-dev.txt failed." }

    # The project itself, editable, without letting pip re-resolve anything:
    # the pins above are the versions we want.
    & $venvPython -m pip install --quiet -e . --no-deps
    if ($LASTEXITCODE -ne 0) { throw "Installing the project failed." }
} finally {
    Pop-Location
}

# -----------------------------------------------------------------------------
# Backend environment file
# -----------------------------------------------------------------------------

$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) {
    Write-Step "backend\.env already exists - left untouched"
    Write-Note "Delete it yourself if you want a fresh one. Keep a copy of"
    Write-Note "IAMS_FIELD_ENCRYPTION_KEY first: without it, encrypted bank"
    Write-Note "details and PANs already in the database cannot be read back."
} else {
    Write-Step "Writing backend\.env with freshly generated keys"
    Copy-Item (Join-Path $backend ".env.example") $envFile

    # The substitution runs in Python rather than PowerShell on purpose.
    # Windows PowerShell's Set-Content writes a UTF-8 byte-order mark, and a
    # BOM at the top of a .env file has caused the first variable to be read
    # with an invisible character in its name before now.
    & $venvPython (Join-Path $PSScriptRoot "write_env.py") $envFile
    if ($LASTEXITCODE -ne 0) { throw "Could not write backend\.env." }

    Write-Note "Both keys are new and belong to this machine only."
}

# -----------------------------------------------------------------------------
# Frontend
# -----------------------------------------------------------------------------

if (-not $SkipFrontend) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        Write-Step "npm not found - skipping the frontend"
        Write-Note "Install Node 20 or newer, then run: cd frontend; npm ci"
    } else {
        $nodeVersion = (& node --version).TrimStart("v")
        Write-Step "Installing frontend dependencies (Node $nodeVersion)"
        Write-Note "Exact versions from frontend/package-lock.json"
        Push-Location $frontend
        try {
            # `npm ci` rather than `npm install`: it installs exactly what the
            # lock file says and fails if the lock and package.json disagree,
            # which is what makes two machines match.
            & npm ci --silent
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
        } finally {
            Pop-Location
        }
    }
}

# -----------------------------------------------------------------------------
# What is left to do
# -----------------------------------------------------------------------------

Write-Host ""
Write-Host "Dependencies installed." -ForegroundColor Green
Write-Host @"

Still to do, because these need a database rather than a package manager:

  1. Create the roles and databases, once per machine:
       psql -U postgres -f infra/postgres/local-setup.sql
       psql -U postgres -d iams -f infra/postgres/local-grants.sql
       psql -U postgres -d iams_test -f infra/postgres/local-grants.sql

  2. Apply the migrations and seed the catalogues:
       cd backend
       .\.venv\Scripts\Activate.ps1
       alembic upgrade head
       iams seed-permissions
       iams seed-settings
       iams create-admin

  3. Run it, in two terminals:
       cd backend;  .\.venv\Scripts\Activate.ps1;  uvicorn app.main:app --reload --port 8001
       cd frontend; npm run dev

  The API is on http://localhost:8001 and the interface on http://localhost:5173.
  Full detail, including what to do when something fails, is in docs/setup.md.
"@
