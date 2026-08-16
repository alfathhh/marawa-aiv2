#!/usr/bin/env python3
"""Backfill bps_dynamic_variables.unit_canonical from existing raw units.

General: processes every ingested domain. Idempotent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn
from workers.ingestion.bps_units import canonical_unit

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def main() -> int:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    store = BpsStore(dsn)
    store.ensure_schema()
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(
            "SELECT domain, variable_id, unit FROM bps_dynamic_variables"
        ).fetchall()
        with connection.cursor() as cursor:
            for domain, variable_id, unit in rows:
                cursor.execute(
                    "UPDATE bps_dynamic_variables SET unit_canonical=%s "
                    "WHERE domain=%s AND variable_id=%s",
                    (canonical_unit(unit), domain, variable_id),
                )
    print(json.dumps({"variables": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
