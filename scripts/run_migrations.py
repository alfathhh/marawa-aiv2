#!/usr/bin/env python3
"""Deterministic migration runner with a ledger in marawa_migrations.schema_migrations.

Usage:
  uv run python scripts/run_migrations.py status   # show applied vs pending
  uv run python scripts/run_migrations.py up       # apply pending, in order
  uv run python scripts/run_migrations.py down <count|id>  # rollback applied

Env: admin postgres env (default /home/ubuntu/.config/marawa-ai/postgres.env,
override with --env). Files: migrations/NNN_*.sql (forward), NNN_*.down.sql (rollback).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

MIGRATIONS_DIR = ROOT / "migrations"
DEFAULT_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def _catalog() -> dict[str, dict[str, Path]]:
    items: dict[str, dict[str, Path]] = {}
    for path in MIGRATIONS_DIR.glob("*.sql"):
        match = re.match(r"^(\d{3})_", path.name)
        if not match:
            continue
        migration_id = match.group(1)
        if path.name.endswith(".down.sql"):
            items.setdefault(migration_id, {})["down"] = path
        else:
            items.setdefault(migration_id, {})["up"] = path
    return dict(sorted(items.items()))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_ledger(connection: psycopg.Connection) -> None:
    # Infrastructure ledger lives OUTSIDE bps_registry so migration rollbacks
    # (002 drops bps_registry) never destroy the ledger itself.
    connection.execute(
        """
        CREATE SCHEMA IF NOT EXISTS marawa_migrations;
        CREATE TABLE IF NOT EXISTS marawa_migrations.schema_migrations (
            migration_id text PRIMARY KEY,
            checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def _applied(connection: psycopg.Connection) -> dict[str, str]:
    _ensure_ledger(connection)
    return dict(connection.execute("SELECT migration_id, checksum FROM marawa_migrations.schema_migrations").fetchall())


def _run_sql(connection: psycopg.Connection, path: Path) -> None:
    connection.execute(path.read_text(encoding="utf-8"))


def status(env: Path) -> dict[str, Any]:
    applied_map = _applied(psycopg.connect(load_postgres_dsn(env)))
    report: list[dict[str, str]] = []
    for migration_id, files in _catalog().items():
        state = "applied" if migration_id in applied_map else "pending"
        report.append({"migration_id": migration_id, "state": state, "up": files["up"].name})
    return {"applied": sorted(applied_map), "report": report}


def up(env: Path) -> dict[str, Any]:
    with psycopg.connect(load_postgres_dsn(env)) as connection:
        applied_map = _applied(connection)
        results = []
        for migration_id, files in _catalog().items():
            if migration_id in applied_map:
                results.append({"migration_id": migration_id, "action": "skip"})
                continue
            _run_sql(connection, files["up"])
            checksum = _sha256(files["up"])
            connection.execute(
                "INSERT INTO marawa_migrations.schema_migrations (migration_id, checksum) VALUES (%s, %s)",
                (migration_id, checksum),
            )
            connection.commit()
            results.append({"migration_id": migration_id, "action": "apply", "checksum": checksum})
        return {"results": results}


def down(env: Path, target: str) -> dict[str, Any]:
    with psycopg.connect(load_postgres_dsn(env)) as connection:
        applied_map = _applied(connection)
        catalog = _catalog()
        ordered = [migration_id for migration_id in catalog if migration_id in applied_map][::-1]
        results = []
        for migration_id in ordered:
            if target not in (migration_id, str(migration_id)):
                continue
            files = catalog[migration_id]
            if "down" not in files:
                results.append({"migration_id": migration_id, "action": "no_down_script"})
                continue
            _run_sql(connection, files["down"])
            connection.execute(
                "DELETE FROM marawa_migrations.schema_migrations WHERE migration_id=%s",
                (migration_id,),
            )
            connection.commit()
            results.append({"migration_id": migration_id, "action": "rollback"})
            break
        return {"results": results}


def backfill(env: Path) -> dict[str, Any]:
    """Register already-applied files in the ledger (used once, live DB)."""
    applied_map = _applied(psycopg.connect(load_postgres_dsn(env)))
    changed = []
    for migration_id, files in _catalog().items():
        if migration_id in applied_map:
            continue
        checksum = _sha256(files["up"])
        with psycopg.connect(load_postgres_dsn(env)) as connection:
            connection.execute(
                "INSERT INTO marawa_migrations.schema_migrations (migration_id, checksum) VALUES (%s, %s)",
                (migration_id, checksum),
            )
            connection.commit()
        changed.append(migration_id)
    return {"backfilled": changed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "up", "down", "backfill"])
    parser.add_argument("target", nargs="?", default=None)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()

    if args.action == "status":
        print(status(args.env))
    elif args.action == "up":
        print(up(args.env))
    elif args.action == "down":
        if not args.target:
            parser.error("down requires a migration id target")
        print(down(args.env, args.target))
    elif args.action == "backfill":
        print(backfill(args.env))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
