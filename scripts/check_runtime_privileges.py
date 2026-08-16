#!/usr/bin/env python3
"""Assert that marawa_runtime_ro has exactly the privileges it should (audit H3).

Why this exists
---------------
`DROP VIEW` removes object-level grants. Migration 001 dropped and recreated the
serving views, and `ensure_schema` does the same on bootstrap, so read-only
enforcement previously depended on a human remembering to re-apply migration 004
afterwards. A forgotten re-apply fails open in the worst way: the runtime keeps
working (it reads through a superuser DSN or loses access silently) and nobody
notices until an audit.

Run this after ANY migration, registry rebuild, or view bootstrap. Non-zero exit
means the read-only boundary is not what the documents claim.

Usage:
    uv run python scripts/check_runtime_privileges.py
    uv run python scripts/check_runtime_privileges.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn  # noqa: E402

RUNTIME_ROLE = "marawa_runtime_ro"

# Objects the runtime MUST be able to read.
EXPECTED_SELECT = [
    ("public", "bps_serving_dynamic"),
    ("public", "bps_serving_simdasi"),
    ("public", "bps_serving_census"),
    ("public", "bps_publications"),
    ("bps_registry", "registry_versions"),
    ("bps_registry", "dataset_registry"),
    ("bps_registry", "measure_registry"),
    ("bps_registry", "dimension_registry"),
    ("bps_registry", "dimension_item_registry"),
    ("bps_registry", "geography_registry"),
    ("bps_registry", "geography_aliases"),
    ("bps_registry", "query_template_registry"),
]

# Raw/fact tables the runtime must NEVER reach. Reading these would bypass the
# serving-view semantics (units, row_role, subperiod) that make answers correct.
FORBIDDEN_SELECT = [
    ("public", "bps_raw_snapshots"),
    ("public", "bps_dynamic_facts"),
    ("public", "bps_dynamic_variables"),
    ("public", "bps_simdasi_facts"),
    ("public", "bps_simdasi_tables"),
    ("public", "bps_census_facts"),
]

# Role attributes that must hold regardless of grants.
EXPECTED_ROLE_ATTRS = {
    "rolsuper": False,
    "rolcreatedb": False,
    "rolcreaterole": False,
    "rolreplication": False,
    "rolbypassrls": False,
}

EXPECTED_SETTINGS = {
    "default_transaction_read_only": "on",
    "statement_timeout": "5s",
    "lock_timeout": "1s",
    "idle_in_transaction_session_timeout": "10s",
}


def check(connection: psycopg.Connection) -> list[str]:
    failures: list[str] = []

    row = connection.execute(
        """
        SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
               coalesce(rolconfig, '{}')
        FROM pg_roles WHERE rolname = %s
        """,
        (RUNTIME_ROLE,),
    ).fetchone()
    if row is None:
        return [f"role {RUNTIME_ROLE} does not exist"]

    attrs = dict(zip(
        ["rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"],
        row[:5],
    ))
    for name, expected in EXPECTED_ROLE_ATTRS.items():
        if attrs[name] != expected:
            failures.append(f"role attribute {name}={attrs[name]}, expected {expected}")

    config = dict(
        entry.split("=", 1) for entry in row[5] if "=" in entry
    )
    for setting, expected in EXPECTED_SETTINGS.items():
        actual = config.get(setting)
        if actual != expected:
            failures.append(f"role setting {setting}={actual!r}, expected {expected!r}")

    for schema, name in EXPECTED_SELECT:
        granted = connection.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (RUNTIME_ROLE, f"{schema}.{name}"),
        ).fetchone()[0]
        if not granted:
            failures.append(
                f"MISSING GRANT: {RUNTIME_ROLE} cannot SELECT {schema}.{name} "
                "(a DROP VIEW / rebuild most likely removed it — re-apply "
                "migrations/004_runtime_readonly_role.sql)"
            )

    for schema, name in FORBIDDEN_SELECT:
        exists = connection.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{name}",)
        ).fetchone()[0]
        if not exists:
            continue
        granted = connection.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (RUNTIME_ROLE, f"{schema}.{name}"),
        ).fetchone()[0]
        if granted:
            failures.append(
                f"EXCESS GRANT: {RUNTIME_ROLE} can SELECT raw table {schema}.{name}; "
                "runtime must read serving views only"
            )

    # No write privilege anywhere, on anything.
    excess_writes = connection.execute(
        """
        SELECT table_schema, table_name, privilege_type
        FROM information_schema.table_privileges
        WHERE grantee = %s
          AND privilege_type <> 'SELECT'
        ORDER BY 1, 2, 3
        """,
        (RUNTIME_ROLE,),
    ).fetchall()
    for schema, name, privilege in excess_writes:
        failures.append(f"EXCESS GRANT: {privilege} on {schema}.{name}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with psycopg.connect(load_postgres_dsn((Path(__file__).resolve().parent.parent / ".env").resolve())) as connection:
        failures = check(connection)

    if args.json:
        print(json.dumps({"passed": not failures, "failures": failures}, indent=2))
    else:
        if failures:
            print(f"runtime privilege check: FAIL ({len(failures)} problems)")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print("runtime privilege check: PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
