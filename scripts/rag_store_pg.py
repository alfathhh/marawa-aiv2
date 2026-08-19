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


def query_dynamic_trend(
    conn, indicator_name: str, limit: int = 8
) -> list[dict[str, Any]]:
    """Trend multi-periode (query_and_compare / trend): agregat kabupaten per
    tahun, terbaru dulu. Dipakai untuk banding periode dan 'urutkan'."""
    rows = conn.execute(
        """
        SELECT %s AS indicator_name, 'Padang Pariaman'::text AS geography_name,
               period::text AS period, sum(value) AS value,
               min(unit) AS unit, min(unit_state) AS unit_state,
               max(snapshot_id) AS snapshot_id
        FROM public.bps_serving_dynamic
        WHERE indicator_name = %s
          AND unit_state IN ('canonical', 'known')
          AND (category_label IN ('Total', 'Tidak ada', 'Semua') OR category_label IS NULL)
        GROUP BY period
        ORDER BY period DESC
        LIMIT %s
        """,
        (indicator_name, indicator_name, limit),
    ).fetchall()
    return [dict(r) for r in rows if r.get("value") is not None]


def query_dynamic_by_geography(
    conn, indicator_name: str, year: str | None, limit: int = 20
) -> list[dict[str, Any]]:
    """Rincian per kecamatan untuk analyze_existing_result (ranking)."""
    period = year or _latest_period(conn, "bps_serving_dynamic", indicator_name)
    if period is None:
        return []
    rows = conn.execute(
        """
        SELECT indicator_name, geography_name, period, value, unit, unit_state, snapshot_id
        FROM public.bps_serving_dynamic
        WHERE indicator_name = %s AND period = %s
          AND unit_state IN ('canonical', 'known')
          AND (category_label IN ('Total', 'Tidak ada', 'Semua') OR category_label IS NULL)
          -- exclude baris agregat kabupaten agar ranking murni per kecamatan
          AND geography_name NOT ILIKE '%%kabupaten%%'
        ORDER BY value DESC NULLS LAST
        LIMIT %s
        """,
        (indicator_name, period, limit),
    ).fetchall()
    return [dict(r) for r in rows if r.get("value") is not None]


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


def query_simdasi_kabupaten(
    conn, indicator_name: str, year: str | None
) -> list[dict[str, Any]]:
    """Simdasi: baris row_role='kabupaten' adalah agregat resmi — ambil langsung,
    JANGAN dijumlahkan dari kecamatan (risiko double-count rincian).

    indicator_name di simdasi sering gabungan multi-indikator dalam satu judul
    ("Jumlah Penduduk, Laju Pertumbuhan, ...") — cocokkan longgar (ILIKE).
    """
    period = year or _latest_period(conn, "bps_serving_simdasi", indicator_name)
    if period is None:
        return []
    rows = conn.execute(
        """
        SELECT indicator_name, geography_name, period, value, unit,
               unit_state, snapshot_id
        FROM public.bps_serving_simdasi
        WHERE indicator_name ILIKE %s
          AND period = %s
          AND row_role = 'kabupaten'
          AND unit_state IN ('known', 'canonical')
        ORDER BY value DESC NULLS LAST
        LIMIT 1
        """,
        (f"%{indicator_name}%", period),
    ).fetchall()
    return [dict(r) for r in rows if r.get("value") is not None]


def _census_topic(text: str) -> str:
    """Kata kunci utama untuk FTS census dari teks bebas user.

    Ambil token konsep pertama yang bermakna (bukan kata tanya/sambung), supaya
    'penduduk sensus berdasarkan jenis kelamin' -> 'penduduk'.
    """
    stop = {
        "berapa", "data", "sensus", "yang", "dan", "di", "ke", "dari", "untuk",
        "berdasarkan", "menurut", "tahun", "terbaru", "jumlah", "padang",
        "pariaman", "kabupaten", "the", "dong", "ya", "mohon", "tolong",
    }
    for tok in re.findall(r"[a-zA-Z]{4,}", (text or "").lower()):
        if tok not in stop:
            return tok
    return "penduduk"


def query_census_inspect(
    conn, topic: str, year: str | None
) -> list[dict[str, Any]]:
    """Census inspect: cakupan dataset sensus yang cocok dengan topik (FTS),
    BUKAN agregat angka. Census multi-dimensi + pemekaran kecamatan membuat
    SUM naif salah — yang user butuh pertama adalah tahu data apa yang ada,
    lalu memperjelas wilayah/dimensi (inspect_dataset, docs/18)."""
    rows = conn.execute(
        """
        SELECT indicator_name, period,
               count(DISTINCT geography_name) AS wilayah_count,
               count(*) AS row_count,
               max(snapshot_id) AS snapshot_id
        FROM public.bps_serving_census
        WHERE indicator_name ILIKE %s
        GROUP BY indicator_name, period
        ORDER BY period DESC NULLS LAST, row_count DESC
        LIMIT 3
        """,
        (f"%{topic}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def query_publication_meta(
    conn, topic: str, year: str | None
) -> list[dict[str, Any]]:
    """Publication: metadata saja (judul, katalog, tanggal rilis, tautan) —
    BUKAN angka. Angka publikasi butuh render PDF (tahap berikut)."""
    rows = conn.execute(
        """
        SELECT title, catalog_number, publication_number, issn,
               release_date, abstract, pdf_url, cover_url, snapshot_id
        FROM public.bps_publications
        WHERE title ILIKE %s
        ORDER BY release_date DESC NULLS LAST
        LIMIT 3
        """,
        (f"%{topic}%",),
    ).fetchall()
    return [dict(r) for r in rows]


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
    mode = selection.get("_mode")  # trend | by_geography | None
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if family == "dynamic":
            if mode == "trend":
                return query_dynamic_trend(conn, indicator)
            if mode == "by_geography":
                return query_dynamic_by_geography(conn, indicator, year)
            return query_dynamic_kabupaten(conn, indicator, year)
        if family == "simdasi":
            return query_simdasi_kabupaten(conn, indicator, year)
        if family == "census":
            # census: topik dari teks user (indicator_name registry bisa
            # multi-dimensi dan tak cocok persis dengan serving.census).
            return query_census_inspect(conn, _census_topic(text), year)
        if family == "publication":
            return query_publication_meta(conn, indicator, year)
        return []


def selection_source_label(selection: dict[str, Any]) -> str:
    return FAMILY_SOURCE_LABEL.get(selection.get("family") or "", "BPS")
