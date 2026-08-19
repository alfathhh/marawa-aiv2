"""Postgres-backed RAG querier + selection store.

Menyambung RagPipeline ke data BPS lokal (bps_serving_* views). Sesuai
invariant #21: runtime membaca LOCAL serving views, bukan WebAPI.

Boundary keamanan (invariant #2): querier hanya menjalankan SQL yang sudah
ditulis di sini dengan parameter ter-bind — TIDAK ADA string interpolation
dari teks user. Teks user hanya dipakai untuk normalisasi pencarian kandidat
(di simulate_bps_candidate_scoring) dan ekstraksi tahun/wilayah via regex
terbatas, bukan sebagai fragmen SQL.

Agregasi kabupaten: serving view dynamic menyimpan baris PER KECAMATAN.
Untuk pertanyaan tingkat kabupaten, querier menjumlahkan (SUM) baris-baris
kecamatan yang unit-nya sama dan period sama — agregasi deterministik di SQL,
bukan oleh LLM (invariant #4).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = logging.getLogger("marawa-rag-pg")

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")

# Tahun 4 digit yang wajar (bukan kode pos, bukan nomor).
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

FAMILY_SOURCE_LABEL = {
    "dynamic": "Data Dinamis BPS",
    "simdasi": "SIMDASI BPS",
    "census": "Sensus BPS",
    "publication": "Publikasi BPS",
}


def _dsn() -> str:
    from workers.ingestion.bps_storage import load_postgres_dsn

    return load_postgres_dsn(POSTGRES_ENV)


def load_offering_index() -> dict[str, Any]:
    """Offering index FTS atas bps_registry (read-only). Dipakai RagPipeline."""
    from scripts.simulate_bps_candidate_scoring import build_offering_index

    return build_offering_index()


def make_offer():
    from scripts.simulate_bps_candidate_scoring import offer_candidates

    return offer_candidates


class PgSelectionStore:
    """Menyimpan kandidat yang sedang ditawarkan / sudah dipilih per percakapan.

    State ini hidup di tabel rag_selection (migration). RagPipeline membaca
    lewat get_selection(); webhook handler/agent menulis lewat set_offered /
    set_selected. Terpisah dari conversation_state agar lifecycle kandidat
    tidak mengotori state machine handover.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def get_selection(self, conversation_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                "SELECT family, dataset_id, indicator_code, indicator_name, "
                "       period, status FROM public.rag_selection "
                "WHERE conversation_id=%s AND status='selected' "
                "ORDER BY updated_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_offered(self, conversation_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                "SELECT offered_payload FROM public.rag_selection "
                "WHERE conversation_id=%s AND status='offered' "
                "ORDER BY updated_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return {"candidates": row["offered_payload"]}

    def set_offered(
        self, conversation_id: str, candidates: list[dict[str, Any]]
    ) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                """
                INSERT INTO public.rag_selection
                    (conversation_id, status, offered_payload, updated_at)
                VALUES (%s, 'offered', %s::jsonb, now())
                ON CONFLICT (conversation_id, status)
                DO UPDATE SET offered_payload = EXCLUDED.offered_payload,
                              updated_at = now()
                """,
                (conversation_id, Jsonb(candidates)),
            )
            conn.commit()

    def set_selected(self, conversation_id: str, selection: dict[str, Any]) -> None:
        with psycopg.connect(self._dsn) as conn:
            # hapus offered lama + selected lama, lalu catat seleksi baru.
            conn.execute(
                "DELETE FROM public.rag_selection WHERE conversation_id=%s",
                (conversation_id,),
            )
            conn.execute(
                """
                INSERT INTO public.rag_selection
                    (conversation_id, status, family, dataset_id,
                     indicator_code, indicator_name, period, updated_at)
                VALUES (%s, 'selected', %s, %s, %s, %s, %s, now())
                """,
                (
                    conversation_id,
                    selection.get("family"),
                    selection.get("dataset_id"),
                    selection.get("indicator_code"),
                    selection.get("indicator_name"),
                    selection.get("period"),
                ),
            )
            conn.commit()


def _latest_period(conn, table: str, indicator_name: str) -> str | None:
    row = conn.execute(
        f"SELECT max(period) AS p FROM public.{table} WHERE indicator_name=%s",
        (indicator_name,),
    ).fetchone()
    return row["p"] if row else None


def query_dynamic_kabupaten(
    conn, indicator_name: str, year: str | None
) -> list[dict[str, Any]]:
    """Agregasi kabupaten: SUM atas semua kecamatan, period & unit konsisten.

    Hanya baris unit publishable (canonical/known) yang dijumlahkan; baris
    unknown_review dikecualikan agar total tidak tercampur satuan tak jelas.
    """
    period = year or _latest_period(conn, "bps_serving_dynamic", indicator_name)
    if period is None:
        return []
    rows = conn.execute(
        """
        SELECT %s AS indicator_name,
               'Padang Pariaman'::text AS geography_name,
               %s::text AS period,
               sum(value) AS value,
               min(unit) AS unit,
               min(unit_state) AS unit_state,
               max(snapshot_id) AS snapshot_id,
               count(*) AS kecamatan_count
        FROM public.bps_serving_dynamic
        WHERE indicator_name = %s
          AND period = %s
          AND unit_state IN ('canonical', 'known')
          -- Baris 'Total' adalah agregat resmi; menjumlahkan Laki+Perempuan+Total
          -- akan menghitung populasi tiga kali. Ambil Total saja. Bila tidak ada
          -- baris Total (indikator tanpa rincian), jatuh ke baris tanpa kategori.
          AND (category_label IN ('Total', 'Tidak ada', 'Semua') OR category_label IS NULL)
        GROUP BY unit
        ORDER BY kecamatan_count DESC
        LIMIT 1
        """,
        (indicator_name, period, indicator_name, period),
    ).fetchall()
    return [dict(r) for r in rows if r.get("value") is not None]


def query_serving(
    family: str | None, text: str, selection: dict[str, Any]
) -> list[dict[str, Any]]:
    """Titik masuk querier RagPipeline. family+indicator dari selection yang
    sudah divalidasi; teks user hanya untuk ekstraksi tahun (regex, no SQL)."""
    if not family or family not in FAMILY_SOURCE_LABEL:
        return []
    indicator = selection.get("indicator_name")
    if not indicator:
        return []
    m = YEAR_RE.search(text or "")
    year = m.group(1) if m else None
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if family == "dynamic":
            return query_dynamic_kabupaten(conn, indicator, year)
        # simdasi/census/publication: tahap berikut; saat ini fail-closed.
        log.info("family %s belum punya querier kabupaten", family)
        return []


def selection_source_label(selection: dict[str, Any]) -> str:
    return FAMILY_SOURCE_LABEL.get(selection.get("family") or "", "BPS")
