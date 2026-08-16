#!/usr/bin/env python3
"""Rebuild normalized SIMDASI columns/cells from stored raw detail payloads."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn
from workers.ingestion.bps_webapi import normalize_simdasi_facts

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def main() -> int:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    store = BpsStore(dsn)
    with psycopg.connect(dsn) as connection:
        rows: list[tuple[str, str, int, dict[str, Any], int | None]] = connection.execute(
            """
            SELECT region_code, table_id, year, raw, snapshot_id
            FROM bps_simdasi_details
            WHERE region_code='1306000'
            ORDER BY table_id, year
            """
        ).fetchall()
    columns_total = 0
    facts_total = 0
    for index, (region_code, table_id, year, raw, snapshot_id) in enumerate(rows, 1):
        columns, facts = normalize_simdasi_facts(region_code, table_id, year, raw)
        columns_total += store.upsert_simdasi_columns(columns, snapshot_id)
        facts_total += store.upsert_simdasi_facts(facts, snapshot_id)
        if index % 50 == 0 or index == len(rows):
            print(f"SIMDASI backfill {index}/{len(rows)} columns={columns_total} facts={facts_total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
