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
        payload = row["offered_payload"]
        # payload bisa list lama (kandidat saja) atau dict baru (candidates+goal_year)
        if isinstance(payload, dict):
            return payload
        return {"candidates": payload, "goal_year": None}

    def set_offered(
        self, conversation_id: str, payload: Any
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
                (conversation_id, Jsonb(payload)),
            )
            conn.commit()

    def clear_selection(self, conversation_id: str) -> None:
        """Hapus seleksi + daftar offered (ganti topik / reset)."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "DELETE FROM public.rag_selection WHERE conversation_id=%s",
                (conversation_id,),
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


# ---------------------------------------------------------------------------
# Typed-template execution (invariant #2): SEMUA query data lewat
# query_template_registry + bind_template. Tidak ada SQL inline untuk fakta.
# ---------------------------------------------------------------------------

def _load_template(conn, template_id: str) -> dict[str, Any]:
    from scripts.bps_template_binder import bind_template  # noqa: F401 (re-export)
    row = conn.execute(
        "SELECT * FROM bps_registry.query_template_registry "
        "WHERE template_id=%s ORDER BY template_version DESC LIMIT 1",
        (template_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"query template {template_id} tidak terdaftar")
    return dict(row)


def _run_template(
    conn, template_id: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Bind + jalankan typed template. Row limit & validasi dari binder."""
    from scripts.bps_template_binder import bind_template

    tpl = _load_template(conn, template_id)
    sql, bound = bind_template(tpl, params)
    timeout_ms = int(tpl.get("timeout_ms") or 5000)
    conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
    rows = conn.execute(sql, bound).fetchall()
    return [dict(r) for r in rows]


def query_dynamic_trend(
    conn, indicator_name: str, limit: int = 8
) -> list[dict[str, Any]]:
    """Trend multi-periode — typed template dynamic_kabupaten_trend."""
    rows = _run_template(conn, "dynamic_kabupaten_trend", {"indicator_name": indicator_name})
    return [r for r in rows if r.get("value") is not None]


def query_dynamic_by_geography(
    conn, indicator_name: str, year: str | None, limit: int = 20
) -> list[dict[str, Any]]:
    """Ranking per kecamatan — typed template dynamic_by_kecamatan."""
    rows = _run_template(conn, "dynamic_by_kecamatan", {
        "indicator_name": indicator_name,
        "period": year,
    })
    return [r for r in rows if r.get("value") is not None]


def query_dynamic_kabupaten(
    conn, indicator_name: str, year: str | None
) -> list[dict[str, Any]]:
    """Agregat kabupaten — typed template dynamic_kabupaten_point.

    SUM per kecamatan, filter category 'Total', unit publishable. Baris
    unknown_review dikecualikan agar total tidak tercampur satuan tak jelas.
    """
    rows = _run_template(conn, "dynamic_kabupaten_point", {
        "indicator_name": indicator_name,
        "period": year,
    })
    return [r for r in rows if r.get("value") is not None]


def query_simdasi_kabupaten(
    conn, indicator_name: str, year: str | None
) -> list[dict[str, Any]]:
    """Simdasi kabupaten — typed template simdasi_kabupaten_point.

    row_role='kabupaten' adalah agregat resmi; JANGAN dijumlahkan dari
    kecamatan (risiko double-count rincian). period bertipe integer.
    """
    rows = _run_template(conn, "simdasi_kabupaten_point", {
        "indicator_name": indicator_name,
        "period": int(year) if year else None,
    })
    return [r for r in rows if r.get("value") is not None]


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


def csa_label_for_title(title: str) -> str | None:
    """Label topik CSA untuk judul kandidat — FTS atas bps_csa_subjects.

    Mengembalikan judul subjek CSA yang paling cocok, atau None bila CSA belum
    ter-ingest / tidak ada yang cocok. Dipakai RAG untuk memberi konteks topik
    resmi pada kandidat (bukan menggantikan FTS, hanya memperkaya label).
    """
    try:
        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                """
                SELECT title FROM public.bps_csa_subjects
                WHERE to_tsvector('indonesian', title) @@ plainto_tsquery('indonesian', %s)
                ORDER BY ts_rank(to_tsvector('indonesian', title), plainto_tsquery('indonesian', %s)) DESC
                LIMIT 1
                """,
                (title, title),
            ).fetchone()
        return row["title"] if row else None
    except Exception:
        return None  # tabel CSA belum ada -> None (fitur opsional)


def csa_family_for_title(title: str) -> str | None:
    """Family RAG (dynamic/simdasi) untuk judul — via tabel CSA yang cocok.

    Mengembalikan family dari tablesource tabel CSA yang judulnya paling mirip,
    atau None bila CSA belum ter-ingest. Dipakai RAG untuk memprioritaskan
    kandidat berdasarkan taksonomi resmi, bukan hanya skor FTS leksikal.
    """
    try:
        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute(
                """
                SELECT t.tablesource
                FROM public.bps_csa_tables t
                WHERE to_tsvector('indonesian', t.title) @@ plainto_tsquery('indonesian', %s)
                ORDER BY ts_rank(to_tsvector('indonesian', t.title), plainto_tsquery('indonesian', %s)) DESC
                LIMIT 1
                """,
                (title, title),
            ).fetchone()
        if not row:
            return None
        from workers.ingestion.bps_csa import csa_family
        return csa_family(row["tablesource"])
    except Exception:
        return None


def list_topics(limit: int = 12) -> list[str]:
    """Daftar kategori/topik nyata yang punya data aktif — untuk menu saat user
    minta data tapi belum spesifik. Data-driven dari registry, bukan hardcode."""
    try:
        with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            rows = conn.execute(
                """
                SELECT topic_name, count(*) AS n
                FROM bps_registry.dataset_registry
                WHERE active AND topic_name IS NOT NULL AND topic_name <> ''
                  AND topic_name NOT IN ('publication','census','dynamic','simdasi')
                  AND length(topic_name) > 3
                GROUP BY topic_name ORDER BY n DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [r["topic_name"] for r in rows]
    except Exception:
        return []


def fetch_indicator_meta(family: str, indicator_name: str) -> dict[str, Any]:
    """Metadata indikator dari dataset_registry (+ variable utk dynamic) untuk
    membangun deskripsi jujur. Read-only; kosong bila tak ketemu."""
    meta: dict[str, Any] = {}
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            """
            SELECT title, summary, topic_name, period_granularity,
                   period_min, period_max, answerability
            FROM bps_registry.dataset_registry
            WHERE source_family = %s AND active AND title = %s
            ORDER BY period_max DESC NULLS LAST
            LIMIT 1
            """,
            (family, indicator_name),
        ).fetchone()
        if row:
            meta.update(dict(row))
        if family == "dynamic":
            v = conn.execute(
                "SELECT definition, notes, subject_name, unit_canonical "
                "FROM public.bps_dynamic_variables WHERE title=%s LIMIT 1",
                (indicator_name,),
            ).fetchone()
            if v:
                meta.setdefault("definition", v.get("definition"))
                meta.setdefault("subject_name", v.get("subject_name"))
                meta.setdefault("unit_canonical", v.get("unit_canonical"))
    return meta


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
