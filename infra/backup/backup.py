"""Backup, restore and verify the IAMS database.

Why a script rather than a paragraph in the documentation
---------------------------------------------------------
A backup procedure that has never been run is a guess, and a restore procedure
that has never been tested is worse than none: it produces confidence without
capability. This script does three things, and the third is the one that
matters.

    python infra/backup/backup.py dump
    python infra/backup/backup.py restore --file backups/iams-20261015-1200.dump
    python infra/backup/backup.py verify

``verify`` takes a fresh dump, restores it into a scratch database beside the
original, and compares the schema version and the row count of every table. It
leaves the scratch database behind only if it fails, so there is something to
look at.

What it deliberately does not do
--------------------------------
* No encryption, and no upload anywhere. A payroll backup is the most sensitive
  file this system produces; where it is stored and who holds the key are
  decisions for whoever operates it, and a default would be the wrong one.
* No schedule. Running this nightly belongs in the host's scheduler, where the
  operator can see it, not inside the application.
* Nothing is deleted. Old dumps accumulate until somebody decides on a
  retention period, which is the same decision the data itself needs.

Both ``pg_dump`` and ``pg_restore`` run **inside the container**, so the
versions always match the server and no client install is needed on the host.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CONTAINER = os.environ.get("IAMS_PG_CONTAINER", "iams-postgres")
DATABASE = os.environ.get("POSTGRES_DB", "iams")
USER = os.environ.get("POSTGRES_USER", "iams")
BACKUP_DIR = Path(os.environ.get("IAMS_BACKUP_DIR", "backups"))
SCRATCH = "iams_restore_check"


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=capture, text=True)


def docker(*args: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", *args], capture=capture)


def psql(sql: str, *, database: str | None = None) -> str:
    result = docker(
        "exec",
        CONTAINER,
        "psql",
        "-U",
        USER,
        "-d",
        database or DATABASE,
        "-t",
        "-A",
        "-c",
        sql,
    )
    if result.returncode != 0:
        raise SystemExit(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def require_container() -> None:
    result = docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise SystemExit(
            f"The container {CONTAINER!r} is not running. Start it with "
            "'docker compose up -d postgres'."
        )


def table_counts(database: str) -> dict[str, int]:
    """Every public table and its row count.

    Counted rather than estimated: a restore that produced a schema but no rows
    would pass an estimate-based check, which is exactly the failure this is
    looking for.
    """
    tables = [
        name
        for name in psql(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
            database=database,
        ).splitlines()
        if name.strip()
    ]
    if not tables:
        return {}
    union = " UNION ALL ".join(
        f"SELECT '{name}' AS t, count(*) AS c FROM \"{name}\"" for name in tables
    )
    counts: dict[str, int] = {}
    rows = psql(f"SELECT t || '=' || c FROM ({union}) AS x ORDER BY t", database=database)
    for line in rows.splitlines():
        if "=" in line:
            name, _, count = line.partition("=")
            counts[name.strip()] = int(count)
    return counts


def schema_version(database: str) -> str:
    return psql("SELECT version_num FROM alembic_version", database=database) or "(none)"


def do_dump(args: argparse.Namespace) -> int:
    """A custom-format dump, which is what pg_restore needs for parallel work."""
    require_container()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = Path(args.file) if args.file else BACKUP_DIR / f"iams-{stamp}.dump"

    print(f"Dumping {DATABASE} from {CONTAINER} to {target}")
    # Captured as **bytes**, never text: a custom-format dump is a binary
    # archive, and decoding it would corrupt the file in a way that only shows
    # up when somebody tries to restore it.
    command = [
        "docker",
        "exec",
        CONTAINER,
        "pg_dump",
        "-U",
        USER,
        "-d",
        DATABASE,
        "--format=custom",
        "--no-owner",
    ]
    if args.portable:
        command.append("--no-privileges")

    binary = subprocess.run(command, capture_output=True)
    if binary.returncode != 0:
        print(binary.stderr.decode(errors="replace"), file=sys.stderr)
        return 1

    target.write_bytes(binary.stdout)
    size = target.stat().st_size
    print(f"Wrote {size:,} bytes. Schema version {schema_version(DATABASE)}.")
    print(
        "This file contains payroll and personal data. It is not encrypted: "
        "store it where that is acceptable, or encrypt it before moving it."
    )
    return 0


def _restore_into(database: str, dump: Path, *, create: bool) -> int:
    if create:
        psql(f'DROP DATABASE IF EXISTS "{database}"', database="postgres")
        psql(f'CREATE DATABASE "{database}"', database="postgres")

    process = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "pg_restore",
            "-U",
            USER,
            "-d",
            database,
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
        ],
        input=dump.read_bytes(),
        capture_output=True,
    )
    if process.returncode != 0:
        print(process.stderr.decode(errors="replace"), file=sys.stderr)
    return process.returncode


def do_restore(args: argparse.Namespace) -> int:
    require_container()
    dump = Path(args.file)
    if not dump.exists():
        raise SystemExit(f"No such dump: {dump}")

    target = args.into or DATABASE
    if target == DATABASE and not args.force:
        raise SystemExit(
            f"Restoring over {DATABASE!r} would replace live data. Pass --force if "
            "that is what you mean, or --into a scratch database."
        )

    print(f"Restoring {dump} into {target}")
    code = _restore_into(target, dump, create=True)
    if code != 0:
        return code
    print(f"Restored. Schema version {schema_version(target)}.")
    return 0


def do_verify(args: argparse.Namespace) -> int:
    """Dump, restore into a scratch database, and compare.

    The only honest way to say a backup can be restored. A dump that cannot be
    restored is indistinguishable from a good one until the day it matters.
    """
    require_container()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dump = BACKUP_DIR / f"verify-{stamp}.dump"

    print(f"1. Dumping {DATABASE}")
    binary = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "pg_dump",
            "-U",
            USER,
            "-d",
            DATABASE,
            "--format=custom",
            "--no-owner",
        ],
        capture_output=True,
    )
    if binary.returncode != 0:
        print(binary.stderr.decode(errors="replace"), file=sys.stderr)
        return 1
    dump.write_bytes(binary.stdout)
    print(f"   {dump.stat().st_size:,} bytes")

    original_counts = table_counts(DATABASE)
    original_version = schema_version(DATABASE)
    print(f"   {len(original_counts)} tables, schema version {original_version}")

    print(f"2. Restoring into {SCRATCH}")
    code = _restore_into(SCRATCH, dump, create=True)
    if code != 0:
        print("   restore FAILED; the scratch database has been left for inspection")
        return 1

    print("3. Comparing")
    restored_counts = table_counts(SCRATCH)
    restored_version = schema_version(SCRATCH)

    problems: list[str] = []
    if restored_version != original_version:
        problems.append(f"schema version differs: {original_version} -> {restored_version}")
    missing = sorted(set(original_counts) - set(restored_counts))
    if missing:
        problems.append(f"missing table(s): {', '.join(missing)}")
    for name, count in sorted(original_counts.items()):
        other = restored_counts.get(name)
        if other is not None and other != count:
            problems.append(f"{name}: {count} row(s) -> {other}")

    total = sum(original_counts.values())
    print(f"   {len(original_counts)} tables, {total:,} rows compared")

    if problems:
        print("\nRESTORE VERIFICATION FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        print(f"The scratch database {SCRATCH!r} has been left for inspection.")
        return 1

    if not args.keep:
        psql(f'DROP DATABASE IF EXISTS "{SCRATCH}"', database="postgres")
        if not args.keep_dump:
            dump.unlink(missing_ok=True)

    print(
        f"\nRestore verified: every table and row count matches, at schema "
        f"version {original_version}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    dump_parser = sub.add_parser("dump", help="Write a custom-format dump.")
    dump_parser.add_argument("--file", help="Where to write it.")
    dump_parser.add_argument(
        "--portable",
        action="store_true",
        help="Omit privileges, for restoring onto a differently configured server.",
    )
    dump_parser.set_defaults(func=do_dump)

    restore_parser = sub.add_parser("restore", help="Restore a dump.")
    restore_parser.add_argument("--file", required=True)
    restore_parser.add_argument("--into", help="A database other than the live one.")
    restore_parser.add_argument(
        "--force", action="store_true", help="Allow restoring over the live database."
    )
    restore_parser.set_defaults(func=do_restore)

    verify_parser = sub.add_parser(
        "verify", help="Dump, restore into a scratch database, and compare."
    )
    verify_parser.add_argument(
        "--keep", action="store_true", help="Keep the scratch database afterwards."
    )
    verify_parser.add_argument("--keep-dump", action="store_true", help="Keep the dump afterwards.")
    verify_parser.set_defaults(func=do_verify)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
