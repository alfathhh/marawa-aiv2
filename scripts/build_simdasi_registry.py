#!/usr/bin/env python3
"""Build the SIMDASI unit registry and marker legend from ingested data.

General: processes every ingested region (or one chosen with ``--region``) and
derives units from column metadata, indicator names, table titles, same-family
tables, and per-row unit signals. No per-region or per-table hardcoding.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn
from workers.ingestion.bps_units import marker_legend, match_unit, resolve_unit, table_family

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def regions_in_db(connection: psycopg.Connection[Any]) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT region_code FROM bps_simdasi_tables ORDER BY region_code"
        ).fetchall()
    ]


def build(connection: psycopg.Connection[Any], region: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    columns = [
        {"table_code": row[0], "title": row[1], "column_name": row[2], "unit": row[3], "data_type": row[4]}
        for row in connection.execute(
            """
            SELECT t.table_code, t.title, c.name, max(c.unit) AS unit, max(c.data_type) AS data_type
            FROM bps_simdasi_columns c
            JOIN bps_simdasi_tables t
              ON t.region_code=c.region_code AND t.table_id=c.table_id
            WHERE c.region_code=%s
            GROUP BY t.table_code, t.title, c.name
            ORDER BY t.table_code, c.name
            """,
            (region,),
        ).fetchall()
    ]
    raw_details = [
        {"raw": row[0]}
        for row in connection.execute(
            "SELECT raw FROM bps_simdasi_details WHERE region_code=%s", (region,)
        ).fetchall()
    ]
    table_titles = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT table_code, title FROM bps_simdasi_tables WHERE region_code=%s", (region,)
        ).fetchall()
    }

    resolved: dict[tuple[str, str], tuple[str | None, str]] = {}
    for column in columns:
        key = (column["table_code"], column["column_name"])
        resolved[key] = resolve_unit(
            column["column_name"], column["unit"], column["title"], column["data_type"]
        )

    # Same column name in another table (sibling tables).
    by_name: dict[str, tuple[str, str]] = {}
    for (table_code, column_name), (unit, source) in resolved.items():
        if unit and column_name not in by_name:
            by_name[column_name] = (unit, source)
    for key, (unit, source) in list(resolved.items()):
        if unit is None and source == "unresolved" and key[1] in by_name:
            resolved[key] = (by_name[key[1]][0], "sibling_table")

    # Same table-code family: when the family has exactly one distinct title unit,
    # inherit it (e.g. family 3.1 carries "ribu jiwa" via table 3.1.2).
    family_units: dict[str, set[str]] = {}
    for table_code, title in table_titles.items():
        unit = match_unit(title)
        if unit:
            family_units.setdefault(table_family(table_code), set()).add(unit)
    unambiguous_family = {
        family: next(iter(units))
        for family, units in family_units.items()
        if len(units) == 1
    }
    for key, (unit, source) in list(resolved.items()):
        if unit is None and source in {"unresolved", "count"}:
            family_unit = unambiguous_family.get(table_family(key[0]))
            if family_unit:
                resolved[key] = (family_unit, "family_title")

    # Tables with per-row units (show_satuan): leave registry unit empty so the
    # serving view falls back to the fact's row_unit.
    row_unit_tables = {
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT t.table_code
            FROM bps_simdasi_facts f
            JOIN bps_simdasi_tables t ON t.table_id=f.table_id AND t.region_code=f.region_code
            WHERE f.region_code=%s AND f.row_unit IS NOT NULL
            """,
            (region,),
        ).fetchall()
    }
    for key, (unit, source) in list(resolved.items()):
        if unit is None and source == "unresolved" and key[0] in row_unit_tables:
            resolved[key] = (None, "row_varied")

    rows = [
        {
            "region_code": region, "table_code": table_code,
            "column_name": column_name, "unit": unit, "unit_source": source,
        }
        for (table_code, column_name), (unit, source) in sorted(resolved.items())
    ]
    return rows, marker_legend(raw_details)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", help="process one region (default: all ingested regions)")
    args = parser.parse_args()
    dsn = load_postgres_dsn(POSTGRES_ENV)
    store = BpsStore(dsn)
    store.ensure_schema()
    summary: dict[str, Any] = {}
    with psycopg.connect(dsn) as connection:
        regions = [args.region] if args.region else regions_in_db(connection)
        for region in regions:
            rows, legend = build(connection, region)
            with connection.cursor() as cursor:
                for row in rows:
                    cursor.execute(
                        """
                        INSERT INTO bps_simdasi_units (region_code, table_code, column_name, unit, unit_source)
                        VALUES (%(region_code)s, %(table_code)s, %(column_name)s, %(unit)s, %(unit_source)s)
                        ON CONFLICT (region_code, table_code, column_name) DO UPDATE SET
                            unit=excluded.unit, unit_source=excluded.unit_source, last_seen_at=now()
                        """,
                        row,
                    )
                for marker, description in legend.items():
                    cursor.execute(
                        """
                        INSERT INTO bps_simdasi_marker_legend (marker, description)
                        VALUES (%s, %s)
                        ON CONFLICT (marker) DO UPDATE SET
                            description=excluded.description, last_seen_at=now()
                        """,
                        (marker, description),
                    )
            unresolved = [row for row in rows if row["unit_source"] == "unresolved"]
            summary[region] = {
                "units": len(rows),
                "resolved_with_unit": sum(1 for row in rows if row["unit"] is not None),
                "bare_counts": sum(1 for row in rows if row["unit_source"] == "count"),
                "row_varied": sum(1 for row in rows if row["unit_source"] == "row_varied"),
                "unresolved": len(unresolved),
                "markers": len(legend),
                "unresolved_samples": [
                    f"{row['table_code']}:{row['column_name']}" for row in unresolved[:30]
                ],
            }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
