#!/usr/bin/env python3
"""Export the unit-review packet for data owner sign-off.

Reads published blocked_quality datasets + measures, writes:
- data/reports/bps-unit-review-<date>.xlsx  (Excel with decision columns)
- docs/26-BPS-UNIT-REVIEW-PACKET.md          (markdown summary table)

Read-only against the registry. Deterministic; no WebAPI, no LLM.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
REPORTS = ROOT / "data" / "reports"
DOCS = ROOT / "docs"


def export(env: Path | None = None) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    env = env or POSTGRES_ENV
    with psycopg.connect(load_postgres_dsn(env)) as connection:
        rows = connection.execute(
            """
            SELECT d.source_family, d.source_resource_id, d.title,
                   m.source_measure_id, m.name, m.unit_state, m.unit_display
            FROM bps_registry.dataset_registry d
            JOIN bps_registry.registry_versions v USING (registry_version_id)
            JOIN bps_registry.measure_registry m USING (registry_version_id, dataset_id)
            WHERE v.status='published' AND d.answerability='blocked_quality'
            ORDER BY d.source_family, d.source_resource_id, m.source_measure_id
            """
        ).fetchall()

    stamp = datetime.now().strftime("%Y%m%d")
    xlsx_path = REPORTS / f"bps-unit-review-{stamp}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "unit_review"
    headers = [
        "source_family", "source_resource_id", "dataset_title",
        "source_measure_id", "measure_name", "unit_state", "unit_display_raw",
        "proposed_unit", "decision", "unit_approved", "reviewed_by", "notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(list(row) + [""] * 5)
    wb.save(xlsx_path)

    md_path = DOCS / "26-BPS-UNIT-REVIEW-PACKET.md"
    md_path.write_text(
        "\n".join(
            [
                "# BPS Unit Review Packet — blocked_quality datasets",
                "",
                f"> Generated: {datetime.now().isoformat(timespec='seconds')} (manual, no cron)",
                "> Sumber: `bps_registry` published. Review oleh data owner; JANGAN menebak unit.",
                f"> Excel: `data/reports/{xlsx_path.name}`",
                "",
                "| family | resource | title | measure | unit_state | raw unit |",
                "|---|---|---|---|---|---|",
            ]
            + [
                f"| {family} | {rid} | {title} | {name} | {state} | {unit or ''} |"
                for family, rid, title, _sid, name, state, unit in rows
            ]
            + [
                "",
                "Alur review:",
                "1. Data owner mengisi `proposed_unit` + `unit_approved` di Excel.",
                "2. Hasil approval dimasukkan ke registry builder sebagai override unit.",
                "3. Rebuild registry; dataset pindah ke status `answerable` bila unit known/unitless.",
            ]
        ),
        encoding="utf-8",
    )
    return {"measures": len(rows), "xlsx": str(xlsx_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=POSTGRES_ENV)
    args = parser.parse_args()
    print(export(args.env))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
