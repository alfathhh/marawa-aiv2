"""Ingestion CSA (Classification of Statistical Activities) — taksonomi resmi BPS.

CSA memberi peta topik 3 tingkat yang menggantikan/menambah FTS leksikal di
offering RAG: kandidat dikelompokkan berdasarkan makna resmi, bukan kebetulan
kata di judul. Ini menambal kelemahan FTS yang kita temukan di 500 skenario
(kata umum "jumlah"/"total" mencocokkan kandidat salah).

Hierarki (WebAPI):
  subcatcsa      — kategori CSA (mis. "statistik demografi dan sosial")
  subjectcsa     — subjek CSA per kategori (mis. "Kependudukan")
  tablestatistic — tabel yang mengadopsi subjek CSA (dengan id subjek)

Sesuai invariant #21: ini INGESTION (background), bukan request-time chat.
Runtime tetap membaca serving views lokal; CSA hanya memperkaya metadata.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Parsing murni — tanpa jaringan, bisa diuji penuh.
# ---------------------------------------------------------------------------


def parse_csa_subcategories(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """list/model/subcatcsa -> baris kategori."""
    rows = _data_rows(payload)
    return [
        {"subcat_id": int(r["subcat_id"]), "title": str(r.get("title", "")).strip()}
        for r in rows
        if r.get("subcat_id") is not None
    ]


def parse_csa_subjects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """list/model/subjectcsa -> baris subjek dengan kategori induk."""
    rows = _data_rows(payload)
    return [
        {
            "sub_id": int(r["sub_id"]),
            "title": str(r.get("title", "")).strip(),
            "subcat_id": int(r["subcat_id"]) if r.get("subcat_id") is not None else None,
            "subcat": str(r.get("subcat", "")).strip(),
        }
        for r in rows
        if r.get("sub_id") is not None
    ]


def parse_csa_tables(payload: dict[str, Any], subject_id: int) -> list[dict[str, Any]]:
    """list/model/tablestatistic -> tabel yang mengadopsi subjek CSA.

    Menangkap `tablesource` (2=dynamic, 3=simdasi) — kunci mapping tabel CSA ke
    family RAG yang benar (pengetahuan operator: Tah, 2026-08-20).
    """
    rows = _data_rows(payload)
    out = []
    for r in rows:
        tid = r.get("id") or r.get("table_id") or r.get("tbl_id")
        if tid is None:
            continue
        out.append({
            "table_id": str(tid),
            "subject_csa_id": subject_id,
            "title": str(r.get("title") or r.get("nama") or "").strip(),
            "tablesource": r.get("tablesource"),
        })
    return out


# tablesource WebAPI -> family RAG (2=dinamis, 3=simdasi).
TABLESOURCE_TO_FAMILY = {2: "dynamic", 3: "simdasi"}


def csa_family(tablesource: int | None) -> str | None:
    if tablesource is None:
        return None
    return TABLESOURCE_TO_FAMILY.get(tablesource)


def _data_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """BPS WebAPI membungkus list di data[1]; data[0] = info halaman."""
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return []
    rows = data[1]
    return rows if isinstance(rows, list) else []


# ---------------------------------------------------------------------------
# Mapping CSA -> taksonomi yang dipakai RAG
# ---------------------------------------------------------------------------

def build_topic_map(subjects: list[dict[str, Any]]) -> dict[int, str]:
    """sub_id -> judul subjek (dipakai RAG untuk memberi label kandidat)."""
    return {s["sub_id"]: s["title"] for s in subjects}


# ---------------------------------------------------------------------------
# Storage — upsert ketiga tabel (idempotent, snapshot-tracked).
# ---------------------------------------------------------------------------

def store_csa(conn, subcats, subjects, tables, snapshot_id: int | None) -> dict[str, int]:
    """Upsert CSA ke public.bps_csa_*. Mengembalikan hitungan per tabel."""
    n = {"subcategories": 0, "subjects": 0, "tables": 0}
    for r in subcats:
        conn.execute(
            """
            INSERT INTO public.bps_csa_subcategories (subcat_id, title, snapshot_id, last_seen_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (subcat_id) DO UPDATE
              SET title=EXCLUDED.title, snapshot_id=EXCLUDED.snapshot_id, last_seen_at=now()
            """,
            (r["subcat_id"], r["title"], snapshot_id),
        )
        n["subcategories"] += 1
    for r in subjects:
        conn.execute(
            """
            INSERT INTO public.bps_csa_subjects (sub_id, title, subcat_id, snapshot_id, last_seen_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (sub_id) DO UPDATE
              SET title=EXCLUDED.title, subcat_id=EXCLUDED.subcat_id,
                  snapshot_id=EXCLUDED.snapshot_id, last_seen_at=now()
            """,
            (r["sub_id"], r["title"], r["subcat_id"], snapshot_id),
        )
        n["subjects"] += 1
    for r in tables:
        conn.execute(
            """
            INSERT INTO public.bps_csa_tables (table_id, subject_csa_id, title, tablesource, snapshot_id, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (table_id, subject_csa_id) DO UPDATE
              SET title=EXCLUDED.title, tablesource=EXCLUDED.tablesource,
                  snapshot_id=EXCLUDED.snapshot_id, last_seen_at=now()
            """,
            (r["table_id"], r["subject_csa_id"], r["title"], r.get("tablesource"), snapshot_id),
        )
        n["tables"] += 1
    return n


# ---------------------------------------------------------------------------
# Ingester — background, fail-safe (WAF/rate-limit di-handle BpsApiClient).
# ---------------------------------------------------------------------------

def ingest_csa(client, conn, snapshot_id: int | None = None) -> dict[str, int]:
    """Tarik CSA dari WebAPI dan simpan. Background-only (invariant #21).

    client: BpsApiClient (atau stub di test). conn: koneksi psycopg write.
    Gagal jaringan -> exception naik; caller (crawler/sentinel) menangani
    retry schedule. Tidak pernah mengubah serving data yang sudah ada.
    """
    subcats = parse_csa_subcategories(
        client.get_path("list", [("model", "subcatcsa"), ("domain", "0000")]).payload or {}
    )
    subjects: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for cat in subcats:
        subj_payload = client.get_path(
            "list", [("model", "subjectcsa"), ("domain", "0000"), ("subcat", cat["subcat_id"])]
        ).payload or {}
        subj_rows = parse_csa_subjects(subj_payload)
        subjects.extend(subj_rows)
        for subj in subj_rows:
            tbl_payload = client.get_path(
                "list",
                [("model", "tablestatistic"), ("domain", "0000"), ("subject", subj["sub_id"])],
            ).payload or {}
            tables.extend(parse_csa_tables(tbl_payload, subject_id=subj["sub_id"]))
    return store_csa(conn, subcats, subjects, tables, snapshot_id)


# ---------------------------------------------------------------------------
# CLI — tarik CSA dari WebAPI (butuh akses; WAF/rate-limit dihandle client).
# ---------------------------------------------------------------------------

def main() -> int:
    """uv run python -m workers.ingestion.bps_csa  — tarik CSA ke Postgres."""
    import logging
    import os
    import sys
    from pathlib import Path

    import psycopg

    from workers.ingestion.bps_client import BpsApiClient, load_api_config
    from workers.ingestion.bps_storage import load_postgres_dsn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("bps-csa")

    cfg = load_api_config(str(Path.home() / ".config/marawa-ai/webapi.env"))
    client = BpsApiClient(
        cfg["BPS_WEBAPI_KEY"],
        proxy_url=cfg.get("BPS_HTTP_PROXY") or None,
    )
    dsn = load_postgres_dsn(Path.home() / ".config/marawa-ai/postgres.env")
    try:
        with psycopg.connect(dsn) as conn:
            counts = ingest_csa(client, conn)
            conn.commit()
        log.info("CSA ter-ingest: %s", counts)
        return 0
    except Exception as exc:
        # WAF/rate-limit bukan crash — log dan keluar 0 agar scheduler retry nanti
        log.warning("CSA ingestion gagal (akan di-retry schedule berikut): %s", type(exc).__name__)
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
