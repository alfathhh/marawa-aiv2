#!/usr/bin/env python3
"""Low-call BPS catalog sentinel; detects signals, never syncs bulk data.

Call budget: exactly three BPS requests per check:
  1. SIMDASI table catalogue (all 47 tables, includes latest_update + years)
  2. Dynamic variable catalogue page 1 (total-count + first-page metadata signal)
  3. Publication catalogue page 1 (total-count + newest/revised metadata signal)

This deliberately does NOT fetch table detail, dynamic facts, census data,
publication details, glossary, or PDFs. A non-empty report is a signal for a
human-approved targeted update; it never invokes the full crawler itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_client import BpsApiClient, load_api_config
from workers.ingestion.bps_crawler import extract_interop_data
from workers.ingestion.bps_storage import load_postgres_dsn

API_ENV = Path("/home/ubuntu/.config/marawa-ai/webapi.env")
POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
REPORT = ROOT / "data" / "reports" / "bps-update-sentinel-latest.json"
# Hard ceiling: three logical HTTP requests; a failed request does NOT retry.
SENTINEL_LOGICAL_REQUEST_BUDGET = 3
SENTINEL_MAX_ATTEMPTS = 1


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _years(row: dict[str, Any]) -> list[int]:
    value = row.get("ketersediaan_tahun")
    if not isinstance(value, list):
        return []
    years: set[int] = set()
    for item in value:
        try:
            years.add(int(float(str(item))))
        except (TypeError, ValueError):
            continue
    return sorted(years)


def compare_simdasi_catalog(
    remote: list[dict[str, Any]], local: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    new_tables: list[str] = []
    changed_tables: list[dict[str, Any]] = []
    for row in remote:
        table_id = _text(row.get("id_tabel"))
        if not table_id:
            continue
        old = local.get(table_id)
        if old is None:
            new_tables.append(table_id)
            continue
        latest_update = _text(row.get("latest_update"))
        old_years = {int(year) for year in old.get("years", [])}
        new_years = [year for year in _years(row) if year not in old_years]
        if latest_update != _text(old.get("latest_update")) or new_years:
            changed_tables.append(
                {
                    "table_id": table_id,
                    "latest_update_changed": latest_update != _text(old.get("latest_update")),
                    "new_years": new_years,
                }
            )
    return {"new_tables": sorted(new_tables), "changed_tables": sorted(changed_tables, key=lambda item: item["table_id"])}


def compare_publication_page(
    remote: list[dict[str, Any]],
    local: dict[str, dict[str, Any]],
    *,
    remote_total: int | None,
    local_total: int,
) -> dict[str, Any]:
    new_publications: list[str] = []
    revised_publications: list[str] = []
    for row in remote:
        pub_id = _text(row.get("pub_id"))
        if not pub_id:
            continue
        old = local.get(pub_id)
        if old is None:
            new_publications.append(pub_id)
        elif _text(row.get("updt_date")) != _text(old.get("updated_date")):
            revised_publications.append(pub_id)
    return {
        "remote_total": remote_total,
        "local_total": local_total,
        "total_changed": remote_total is not None and remote_total != local_total,
        "new_publications": sorted(new_publications),
        "revised_publications": sorted(revised_publications),
    }


def compare_dynamic_page(
    remote: list[dict[str, Any]],
    local: dict[str, dict[str, Any]],
    *,
    remote_total: int | None,
    local_total: int,
) -> dict[str, Any]:
    new_variables: list[str] = []
    changed_variables: list[str] = []
    for row in remote:
        var_id = _text(row.get("var_id"))
        if not var_id:
            continue
        old = local.get(var_id)
        if old is None:
            new_variables.append(var_id)
            continue
        comparable = {
            "title": _text(row.get("title")),
            "unit": _text(row.get("unit")),
            "subject_id": _text(row.get("sub_id")),
            "vertical_id": _text(row.get("vertical")),
        }
        if comparable != {key: _text(old.get(key)) for key in comparable}:
            changed_variables.append(var_id)
    return {
        "remote_total": remote_total,
        "local_total": local_total,
        "total_changed": remote_total is not None and remote_total != local_total,
        "new_variables": sorted(new_variables),
        "changed_variables": sorted(changed_variables),
    }


def has_changes(simdasi: dict[str, Any], dynamic: dict[str, Any], publication: dict[str, Any]) -> bool:
    """Return true only for a concrete catalogue signal (not false placeholders)."""
    return bool(
        simdasi["new_tables"]
        or simdasi["changed_tables"]
        or dynamic["new_variables"]
        or dynamic["changed_variables"]
        or dynamic["total_changed"]
        or publication["new_publications"]
        or publication["revised_publications"]
        or publication["total_changed"]
    )


def paginated_rows(payload: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("empty API payload")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], dict) or not isinstance(data[1], list):
        raise ValueError("unexpected paginated catalogue shape")
    rows = data[1]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("catalogue contains non-object row")
    return data[0], rows


def local_baseline(connection: psycopg.Connection[Any], domain: str, region: str) -> dict[str, Any]:
    simdasi = {
        row[0]: {"latest_update": row[1], "years": row[2] or []}
        for row in connection.execute(
            "SELECT table_id, raw->>'latest_update', available_years FROM bps_simdasi_tables WHERE region_code=%s",
            (region,),
        ).fetchall()
    }
    publications = {
        row[0]: {"updated_date": row[1]}
        for row in connection.execute(
            "SELECT publication_id, updated_date FROM bps_publications WHERE domain=%s", (domain,)
        ).fetchall()
    }
    dynamic = {
        row[0]: {"title": row[1], "unit": row[2], "subject_id": row[3], "vertical_id": row[4]}
        for row in connection.execute(
            "SELECT variable_id,title,unit,subject_id,vertical_id FROM bps_dynamic_variables WHERE domain=%s", (domain,)
        ).fetchall()
    }
    return {"simdasi": simdasi, "publications": publications, "dynamic": dynamic}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run exactly three low-cost BPS catalogue probes.")
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()

    config = load_api_config(str(API_ENV))
    domain = config["BPS_DOMAIN"]
    region = config["BPS_SIMDASI_REGION"]
    client = BpsApiClient(
        config["BPS_WEBAPI_KEY"],
        proxy_url=config.get("BPS_HTTP_PROXY"),
        timeout=45,
        max_attempts=SENTINEL_MAX_ATTEMPTS,
        min_delay=float(config.get("BPS_SENTINEL_MIN_DELAY", "2.0")),
        max_delay=float(config.get("BPS_SENTINEL_MAX_DELAY", "4.0")),
        backoff_base=3,
        max_backoff=20,
    )
    with psycopg.connect(load_postgres_dsn(POSTGRES_ENV)) as connection:
        baseline = local_baseline(connection, domain, region)
        simdasi_result = client.get_interop("simdasi", 23, [("wilayah", region)])
        _, simdasi_rows = extract_interop_data(simdasi_result.payload)
        dynamic_result = client.get_paginated("var", domain=domain, page=1)
        dynamic_meta, dynamic_rows = paginated_rows(dynamic_result.payload)
        publication_result = client.get_paginated("publication", domain=domain, page=1)
        publication_meta, publication_rows = paginated_rows(publication_result.payload)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "catalog-sentinel",
        "request_budget": SENTINEL_LOGICAL_REQUEST_BUDGET,
        "requests_made": SENTINEL_LOGICAL_REQUEST_BUDGET,
        "automatic_pull": False,
        "scope": {"domain": domain, "simdasi_region": region},
        "simdasi": compare_simdasi_catalog(simdasi_rows, baseline["simdasi"]),
        "dynamic": compare_dynamic_page(
            dynamic_rows, baseline["dynamic"],
            remote_total=int(dynamic_meta.get("total")) if dynamic_meta.get("total") is not None else None,
            local_total=len(baseline["dynamic"]),
        ),
        "publication": compare_publication_page(
            publication_rows, baseline["publications"],
            remote_total=int(publication_meta.get("total")) if publication_meta.get("total") is not None else None,
            local_total=len(baseline["publications"]),
        ),
        "limitations": [
            "Dynamic WebAPI catalogue has no update timestamp; silent revisions to old fact values cannot be detected without a human-approved targeted/full fetch.",
            "Publication sentinel inspects newest catalogue page only; total-count change signals additions/removals outside that page without identifying an ID.",
            "Census and glossary are excluded from periodic sentinel: census is event-release driven; glossary upstream currently returns HTTP 500.",
        ],
    }
    report["change_detected"] = has_changes(
        report["simdasi"], report["dynamic"], report["publication"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({
        "change_detected": report["change_detected"],
        "requests_made": report["requests_made"],
        "simdasi_new": len(report["simdasi"]["new_tables"]),
        "simdasi_changed": len(report["simdasi"]["changed_tables"]),
        "dynamic_new": len(report["dynamic"]["new_variables"]),
        "dynamic_changed": len(report["dynamic"]["changed_variables"]),
        "dynamic_total_changed": report["dynamic"]["total_changed"],
        "publication_new": len(report["publication"]["new_publications"]),
        "publication_revised": len(report["publication"]["revised_publications"]),
        "publication_total_changed": report["publication"]["total_changed"],
        "report": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
