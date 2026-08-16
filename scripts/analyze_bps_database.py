#!/usr/bin/env python3
"""Generate machine-readable and Markdown exploration reports from ingested BPS data."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
REPORT_DIR = ROOT / "data" / "reports"


def scalar(connection: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def rows(connection: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return connection.execute(sql, params).fetchall()


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, tuple):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    return value


def collect() -> dict[str, Any]:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        latest_run = connection.execute(
            "SELECT id, status, started_at, finished_at, summary FROM bps_ingestion_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        table_names = [row[0] for row in rows(connection, "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE %s ORDER BY table_name", ("bps_%",))]
        counts = {table: scalar(connection, f"SELECT count(*) FROM {table}") for table in table_names}
        report = {
            "generated_at": datetime.now(timezone.utc),
            "database_size": scalar(connection, "SELECT pg_size_pretty(pg_database_size(current_database()))"),
            "latest_run": None if latest_run is None else {
                "id": str(latest_run[0]), "status": latest_run[1], "started_at": latest_run[2],
                "finished_at": latest_run[3], "summary": latest_run[4],
            },
            "row_counts": counts,
            "dynamic": {
                "period_range": rows(connection, "SELECT min(NULLIF(period_label,'')), max(NULLIF(period_label,'')) FROM bps_dynamic_facts WHERE domain='1306'")[0],
                "variables_with_facts": scalar(connection, "SELECT count(DISTINCT variable_id) FROM bps_dynamic_facts WHERE domain='1306'"),
                "variables_without_facts": scalar(connection, "SELECT count(*) FROM bps_dynamic_variables v WHERE domain='1306' AND NOT EXISTS (SELECT 1 FROM bps_dynamic_facts f WHERE f.domain=v.domain AND f.variable_id=v.variable_id)"),
                "unit_distribution": rows(connection, "SELECT coalesce(unit,'(kosong)'), count(*) FROM bps_dynamic_variables WHERE domain='1306' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25"),
                "largest_variables": rows(connection, "SELECT v.variable_id, v.title, count(f.*) facts FROM bps_dynamic_variables v LEFT JOIN bps_dynamic_facts f ON f.domain=v.domain AND f.variable_id=v.variable_id WHERE v.domain='1306' GROUP BY v.variable_id,v.title ORDER BY facts DESC LIMIT 20"),
                "non_numeric_facts": scalar(connection, "SELECT count(*) FROM bps_dynamic_facts WHERE domain='1306' AND value_numeric IS NULL AND value_text IS NOT NULL"),
            },
            "simdasi": {
                "year_range": rows(connection, "SELECT min(year), max(year) FROM bps_simdasi_details WHERE region_code='1306000'")[0],
                "chapter_distribution": rows(connection, "SELECT coalesce(chapter,'(kosong)'), count(*) FROM bps_simdasi_tables WHERE region_code='1306000' GROUP BY 1 ORDER BY 2 DESC,1"),
                "tables_without_details": scalar(connection, "SELECT count(*) FROM bps_simdasi_tables t WHERE region_code='1306000' AND NOT EXISTS (SELECT 1 FROM bps_simdasi_details d WHERE d.region_code=t.region_code AND d.table_id=t.table_id)"),
                "table_year_coverage": rows(connection, "SELECT t.table_code,t.title,jsonb_array_length(t.available_years) expected,count(d.*) actual FROM bps_simdasi_tables t LEFT JOIN bps_simdasi_details d ON d.region_code=t.region_code AND d.table_id=t.table_id WHERE t.region_code='1306000' GROUP BY t.table_code,t.title,t.available_years ORDER BY (jsonb_array_length(t.available_years) - count(d.*)) DESC, t.table_code LIMIT 100"),
                "cells": scalar(connection, "SELECT count(*) FROM bps_simdasi_facts WHERE region_code='1306000'"),
                "numeric_cells": scalar(connection, "SELECT count(*) FROM bps_simdasi_facts WHERE region_code='1306000' AND value_numeric IS NOT NULL"),
                "marker_distribution": rows(connection, "SELECT value_code,count(*) FROM bps_simdasi_facts WHERE region_code='1306000' AND value_code IS NOT NULL GROUP BY value_code ORDER BY count(*) DESC,value_code"),
                "geographies": scalar(connection, "SELECT count(DISTINCT geography_code) FROM bps_simdasi_facts WHERE region_code='1306000' AND geography_code<>''"),
                "indicators": scalar(connection, "SELECT count(DISTINCT (table_id, column_id)) FROM bps_simdasi_facts WHERE region_code='1306000'"),
            },
            "census": {
                "events": rows(connection, "SELECT event_id,event_name,event_year FROM bps_census_events ORDER BY event_year DESC NULLS LAST"),
                "facts_by_event": rows(connection, "SELECT event_id,count(*) FROM bps_census_facts GROUP BY event_id ORDER BY event_id"),
                "indicators": scalar(connection, "SELECT count(DISTINCT indicator_id) FROM bps_census_facts"),
            },
            "publication": {
                "release_range": rows(connection, "SELECT min(release_date),max(release_date) FROM bps_publications WHERE domain='1306'")[0],
                "with_pdf_url": scalar(connection, "SELECT count(*) FROM bps_publications WHERE domain='1306' AND coalesce(pdf_url,'')<>''"),
                "declared_size_values": rows(connection, "SELECT declared_size,count(*) FROM bps_publications WHERE domain='1306' GROUP BY declared_size ORDER BY count(*) DESC LIMIT 25"),
                "latest": rows(connection, "SELECT publication_id,title,release_date,updated_date,declared_size FROM bps_publications WHERE domain='1306' ORDER BY release_date DESC NULLS LAST LIMIT 20"),
                "file_status": rows(connection, "SELECT download_status,count(*),coalesce(sum(bytes),0) FROM bps_publication_files WHERE domain='1306' GROUP BY download_status ORDER BY download_status"),
            },
            "glossary": {
                "with_definition": scalar(connection, "SELECT count(*) FROM bps_glossary WHERE coalesce(definition,'')<>''"),
                "without_definition": scalar(connection, "SELECT count(*) FROM bps_glossary WHERE coalesce(definition,'')=''"),
                "duplicate_concepts": rows(connection, "SELECT lower(concept),count(*) FROM bps_glossary WHERE coalesce(concept,'')<>'' GROUP BY lower(concept) HAVING count(*)>1 ORDER BY count(*) DESC,1 LIMIT 30"),
            },
            "quality": {
                "ingestion_errors": scalar(connection, "SELECT coalesce(sum(jsonb_array_length(coalesce(summary->'errors','[]'::jsonb))),0) FROM bps_ingestion_runs"),
                "raw_snapshots": scalar(connection, "SELECT count(*) FROM bps_raw_snapshots"),
                "distinct_requests": scalar(connection, "SELECT count(DISTINCT request_fingerprint) FROM bps_raw_snapshots"),
                "checkpoints": rows(connection, "SELECT checkpoint_key,state FROM bps_ingestion_checkpoints WHERE checkpoint_key LIKE %s ORDER BY checkpoint_key", ("bps:%",)),
            },
        }
    return clean(report)


def markdown(report: dict[str, Any]) -> str:
    counts = report["row_counts"]
    lines = [
        "# BPS WebAPI — Hasil Eksplorasi Database Aktual",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Database size: `{report['database_size']}`",
        f"Latest ingestion status: `{(report['latest_run'] or {}).get('status', 'none')}`",
        "",
        "## Inventory",
        "",
        "| Table | Rows |",
        "|---|---:|",
    ]
    for table, count in sorted(counts.items()):
        lines.append(f"| `{table}` | {count:,} |")
    lines.extend([
        "",
        "## Dynamic Data",
        "",
        f"- Variables with facts: **{report['dynamic']['variables_with_facts']}**",
        f"- Variables without facts: **{report['dynamic']['variables_without_facts']}**",
        f"- Period range: `{report['dynamic']['period_range']}`",
        f"- Non-numeric marker facts: **{report['dynamic']['non_numeric_facts']}**",
        "",
        "## SIMDASI",
        "",
        f"- Year range: `{report['simdasi']['year_range']}`",
        f"- Tables without downloaded detail: **{report['simdasi']['tables_without_details']}**",
        f"- Normalized cells: **{report['simdasi']['cells']}**",
        f"- Numeric cells: **{report['simdasi']['numeric_cells']}**",
        f"- Distinct geography codes: **{report['simdasi']['geographies']}**",
        f"- Distinct table-column indicators: **{report['simdasi']['indicators']}**",
        "",
        "## Census",
        "",
        f"- Events: **{len(report['census']['events'])}**",
        f"- Distinct indicators in local facts: **{report['census']['indicators']}**",
        "",
        "## Publication",
        "",
        f"- Release range: `{report['publication']['release_range']}`",
        f"- Publications with PDF URL: **{report['publication']['with_pdf_url']}**",
        "",
        "## Glosarium",
        "",
        f"- Concepts with definition: **{report['glossary']['with_definition']}**",
        f"- Concepts without definition: **{report['glossary']['without_definition']}**",
        "",
        "## Quality/Lineage",
        "",
        f"- Raw snapshots: **{report['quality']['raw_snapshots']}**",
        f"- Distinct canonical requests: **{report['quality']['distinct_requests']}**",
        f"- Recorded ingestion errors: **{report['quality']['ingestion_errors']}**",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = collect()
    (REPORT_DIR / "bps-exploration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "bps-exploration.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(REPORT_DIR / 'bps-exploration.json'), "markdown": str(REPORT_DIR / 'bps-exploration.md'), "latest_run": report['latest_run'], "row_counts": report['row_counts']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
