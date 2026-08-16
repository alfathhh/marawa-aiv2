#!/usr/bin/env python3
"""Fail-closed integrity checks for the local BPS WebAPI mirror."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")

# Families that must be checkpoint-complete for a healthy mirror.
CORE_FAMILIES = ("simdasi", "dynamic", "census", "publication")
# Families retried opportunistically and reported as warnings when upstream is down.
OPTIONAL_FAMILIES = ("glossary",)


def scalar(connection: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def main() -> int:
    warnings: list[str] = []
    errors: list[str] = []
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        latest = connection.execute(
            "SELECT id,status,summary FROM bps_ingestion_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if latest is None or latest[1] not in ("completed", "partial"):
            errors.append(f"latest ingestion run is not completed: {None if latest is None else latest[1]}")

        expected_simdasi = scalar(
            connection,
            "SELECT coalesce(sum(jsonb_array_length(available_years)),0) FROM bps_simdasi_tables WHERE region_code='1306000'",
        )
        actual_simdasi = scalar(
            connection,
            "SELECT count(*) FROM bps_simdasi_details WHERE region_code='1306000'",
        )
        if expected_simdasi != actual_simdasi:
            errors.append(f"SIMDASI detail coverage {actual_simdasi}/{expected_simdasi}")

        orphan_details = scalar(
            connection,
            """
            SELECT count(*) FROM bps_simdasi_details d
            WHERE NOT EXISTS (
              SELECT 1 FROM bps_simdasi_tables t
              WHERE t.region_code=d.region_code AND t.table_id=d.table_id
            )
            """,
        )
        if orphan_details:
            errors.append(f"orphan SIMDASI details: {orphan_details}")

        for table, column in [
            ("bps_dynamic_variables", "variable_id"),
            ("bps_census_events", "event_id"),
            ("bps_publications", "publication_id"),
            ("bps_glossary", "glossary_id"),
            ("bps_simdasi_tables", "table_id"),
        ]:
            invalid = scalar(
                connection,
                f"SELECT count(*) FROM {table} WHERE {column} IS NULL OR {column} IN ('', 'None', 'null')",
            )
            if invalid:
                errors.append(f"invalid external IDs in {table}: {invalid}")

        broken_snapshots = 0
        for table in [
            "bps_dynamic_variables", "bps_dynamic_dimensions", "bps_dynamic_facts",
            "bps_census_events", "bps_census_topics", "bps_census_areas",
            "bps_census_datasets", "bps_census_facts", "bps_simdasi_tables",
            "bps_simdasi_details", "bps_simdasi_columns", "bps_simdasi_facts",
            "bps_publications", "bps_glossary",
        ]:
            broken_snapshots += scalar(
                connection,
                f"SELECT count(*) FROM {table} t WHERE snapshot_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM bps_raw_snapshots s WHERE s.id=t.snapshot_id)",
            )
        if broken_snapshots:
            errors.append(f"broken snapshot references: {broken_snapshots}")

        checkpoint_rows = connection.execute(
            "SELECT checkpoint_key,state FROM bps_ingestion_checkpoints WHERE checkpoint_key LIKE %s ORDER BY checkpoint_key",
            ("bps:%",),
        ).fetchall()
        checkpoint_state = {key: state for key, state in checkpoint_rows}
        for family in CORE_FAMILIES:
            key = f"bps:{family}:1306:1306000"
            state = checkpoint_state.get(key)
            if not state or state.get("done") is not True:
                errors.append(f"checkpoint not done: {key}")
        for family in OPTIONAL_FAMILIES:
            key = f"bps:{family}:1306:1306000"
            state = checkpoint_state.get(key)
            if not state or state.get("done") is not True:
                warnings.append(f"checkpoint pending (upstream may be unavailable): {key}")

        summary = {
            "latest_run": None if latest is None else str(latest[0]),
            "status": None if latest is None else latest[1],
            "simdasi_table_years": {"expected": expected_simdasi, "actual": actual_simdasi},
            "dynamic_variables": scalar(connection, "SELECT count(*) FROM bps_dynamic_variables WHERE domain='1306'"),
            "dynamic_facts": scalar(connection, "SELECT count(*) FROM bps_dynamic_facts WHERE domain='1306'"),
            "census_facts": scalar(connection, "SELECT count(*) FROM bps_census_facts"),
            "publications": scalar(connection, "SELECT count(*) FROM bps_publications WHERE domain='1306'"),
            "glossary": scalar(connection, "SELECT count(*) FROM bps_glossary"),
            "warnings": warnings,
            "errors": errors,
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
