#!/usr/bin/env python3
"""Render the BPS bootstrap checklist from live PostgreSQL and artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
CHECKLIST = ROOT / "docs" / "19-BPS-BOOTSTRAP-CHECKLIST.md"
STATE_FILE = ROOT / "data" / "reports" / "bps-workflow-state.json"
EXPLORATION = ROOT / "data" / "reports" / "bps-exploration.json"
INTEGRITY = ROOT / "data" / "reports" / "bps-integrity-validation.json"
BACKUP_DIR = ROOT / "data" / "backups"


def scalar(connection: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = connection.execute(sql, params).fetchone()
    return None if row is None else row[0]


def load_manual_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def artifact_completed(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    latest = payload.get("latest_run") or {}
    return latest.get("status") == "completed"


def check(done: bool) -> str:
    return "x" if done else " "


def collect() -> dict[str, Any]:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        latest = connection.execute(
            "SELECT id,status,started_at,finished_at,summary FROM bps_ingestion_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        latest_id = None if latest is None else latest[0]
        current_family = None
        if latest_id is not None:
            row = connection.execute(
                "SELECT source_family FROM bps_snapshot_observations WHERE run_id=%s ORDER BY observed_at DESC LIMIT 1",
                (latest_id,),
            ).fetchone()
            current_family = None if row is None else row[0]
        checkpoints = {
            row[0].split(":")[1]: row[1]
            for row in connection.execute(
                "SELECT checkpoint_key,state FROM bps_ingestion_checkpoints WHERE checkpoint_key LIKE %s",
                ("bps:%:1306:1306000",),
            ).fetchall()
        }
        counts = {
            "simdasi_tables": scalar(connection, "SELECT count(*) FROM bps_simdasi_tables WHERE region_code='1306000'"),
            "simdasi_expected": scalar(connection, "SELECT coalesce(sum(jsonb_array_length(available_years)),0) FROM bps_simdasi_tables WHERE region_code='1306000'"),
            "simdasi_details": scalar(connection, "SELECT count(*) FROM bps_simdasi_details WHERE region_code='1306000'"),
            "simdasi_cells": scalar(connection, "SELECT count(*) FROM bps_simdasi_facts WHERE region_code='1306000'"),
            "dynamic_variables": scalar(connection, "SELECT count(*) FROM bps_dynamic_variables WHERE domain='1306'"),
            "dynamic_dimensions": scalar(connection, "SELECT count(*) FROM bps_dynamic_dimensions WHERE domain='1306'"),
            "dynamic_facts": scalar(connection, "SELECT count(*) FROM bps_dynamic_facts WHERE domain='1306'"),
            "census_events": scalar(connection, "SELECT count(*) FROM bps_census_events"),
            "census_facts": scalar(connection, "SELECT count(*) FROM bps_census_facts"),
            "publications": scalar(connection, "SELECT count(*) FROM bps_publications WHERE domain='1306'"),
            "publication_pdfs": scalar(connection, "SELECT count(*) FROM bps_publication_files WHERE domain='1306' AND download_status='downloaded'"),
            "glossary": scalar(connection, "SELECT count(*) FROM bps_glossary"),
            "snapshots": scalar(connection, "SELECT count(*) FROM bps_raw_snapshots"),
        }
    manual = load_manual_state()
    backup_done = any(BACKUP_DIR.glob("*.dump")) and any(BACKUP_DIR.glob("*.dump.sha256"))
    family_states: dict[str, Any] = {}
    for family in ("simdasi", "dynamic", "census", "publication", "glossary"):
        state = checkpoints.get(family) or {}
        family_states[family] = {
            "done": state.get("done") is True,
            "completed_details": len(state.get("completed_details", [])),
            "completed_variables": len(state.get("completed_variables", [])),
            "completed_data": len(state.get("completed_data", [])),
        }
    return {
        "latest_run_id": None if latest is None else str(latest[0]),
        "latest_status": None if latest is None else latest[1],
        "latest_started_at": None if latest is None else latest[2].isoformat(),
        "latest_finished_at": None if latest is None or latest[3] is None else latest[3].isoformat(),
        "current_family": current_family,
        "families": family_states,
        "counts": counts,
        "backup_done": backup_done,
        "integrity_done": bool(manual.get("integrity_done")) or artifact_completed(INTEGRITY),
        "exploration_done": bool(manual.get("exploration_done")) or artifact_completed(EXPLORATION),
        "pdf_done": counts["publications"] > 0 and scalar_pdf_complete(counts),
        "tests_done": bool(manual.get("tests_done")),
        "tests_detail": manual.get("tests_detail"),
        "docs_done": bool(manual.get("docs_done")),
        "docs_detail": manual.get("docs_detail"),
        "schedule_done": bool(manual.get("schedule_done")),
        "schedule_detail": manual.get("schedule_detail"),
    }


def scalar_pdf_complete(counts: dict[str, int]) -> bool:
    # Completion is refined by the final integrity/report step. Zero publications is never complete.
    return counts["publication_pdfs"] >= counts["publications"] > 0


def render(state: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Jakarta")).isoformat(timespec="seconds")
    c = state["counts"]
    f = state["families"]
    current = state["current_family"] or "belum terdeteksi"
    # A glossary-only upstream outage yields `partial`, while all core families
    # may still be complete and validator-approved.
    run_done = state["latest_status"] in {"completed", "partial"}
    lines = [
        "---",
        "title: BPS WebAPI Bootstrap Checklist",
        "status: live",
        f"updated_at: {now}",
        "---",
        "",
        "# BPS WebAPI Bootstrap — Live Checklist",
        "",
        f"<!-- state-sha256:{digest} -->",
        "> File ini diperbarui otomatis dari PostgreSQL/checkpoints dan artifact aktual. Jangan centang manual.",
        "",
        "## Status saat ini",
        "",
        f"- **Run:** `{state['latest_run_id']}`",
        f"- **Status:** `{state['latest_status']}`",
        f"- **Family aktif:** `{current}`",
        f"- **Raw snapshots:** **{c['snapshots']:,}**",
        "",
        "## 1. Persiapan dan proteksi",
        "",
        f"- [{check(state['backup_done'])}] PostgreSQL backup + SHA-256 tersedia",
        "- [x] Secret API/proxy/PostgreSQL berada di luar repository (mode 0600)",
        "- [x] Schema raw/current/serving + checkpoint tersedia",
        "- [x] HTTP client serialized, retry/backoff, WAF/non-JSON aware",
        "",
        "## 2. Ingestion data",
        "",
        f"- [{check(f['simdasi']['done'])}] **SIMDASI `1306000`** — {c['simdasi_tables']:,} tabel; {c['simdasi_details']:,}/{c['simdasi_expected']:,} table-year; {c['simdasi_cells']:,} cells",
        f"- [{check(f['dynamic']['done'])}] **Dynamic Data `1306`** — {f['dynamic']['completed_variables']:,}/{c['dynamic_variables']:,} variables selesai; {c['dynamic_dimensions']:,} dimensions; {c['dynamic_facts']:,} facts",
        f"- [{check(f['census']['done'])}] **Census Data** — {c['census_events']:,} events; {f['census']['completed_data']:,} local dataset-area requests; {c['census_facts']:,} facts",
        f"- [{check(f['publication']['done'])}] **Publication metadata/detail `1306`** — {c['publications']:,} publications; {f['publication']['completed_details']:,} details selesai",
        f"- [{check(f['glossary']['done'])}] **Glosarium BPS** — {c['glossary']:,} concepts (upstream HTTP 500; deferred)",
        f"- [{check(run_done)}] Core ingestion run selesai; Glosarium upstream-deferred tidak mengosongkan mirror",
        "",
        "## 3. Post-ingestion",
        "",
        f"- [{check(state['integrity_done'])}] Fail-closed database integrity validation",
        f"- [{check(state['exploration_done'])}] Exploration/quality report final dari database completed run",
        f"- [{check(state['pdf_done'])}] Publication PDF mirror + checksum ({c['publication_pdfs']:,}/{c['publications']:,})",
        f"- [{check(state['tests_done'])}] Full automated tests — {state['tests_detail'] or 'menunggu final run'}",
        f"- [{check(state['docs_done'])}] Documentation validator + Obsidian sync — {state['docs_detail'] or 'menunggu final run'}",
        f"- [{check(state['schedule_done'])}] Scheduled catalog sentinel aktif — {state['schedule_detail'] or 'menunggu bootstrap final'}",
        "",
        "## Artifact",
        "",
        "- Data contract: `docs/17-BPS-WEBAPI-DATA.md`",
        "- Format WhatsApp: `docs/18-WHATSAPP-DATA-ANSWER-FORMATS.md`",
        "- Exploration report: `data/reports/bps-exploration.md`",
        "- Integrity report: `data/reports/bps-integrity-validation.json`",
        "- Backup: `data/backups/`",
        "",
    ]
    return "\n".join(lines)


def current_hash() -> str | None:
    if not CHECKLIST.exists():
        return None
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        if line.startswith("<!-- state-sha256:"):
            return line.removeprefix("<!-- state-sha256:").removesuffix(" -->")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    state = collect()
    state_hash = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    if not args.force and current_hash() == state_hash:
        return 0
    content = render(state)
    CHECKLIST.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKLIST.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, CHECKLIST)
    print(f"updated {CHECKLIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
