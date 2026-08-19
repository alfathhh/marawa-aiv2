"""Contract tests untuk bps_csa — parsing CSA tanpa jaringan (transport palsu).

Membuktikan: parser menangani bentuk respons BPS (data[0]=info, data[1]=rows),
dan ingester mengisi ketiga tabel CSA dengan benar.
"""
from __future__ import annotations

import pytest

from workers.ingestion.bps_csa import (
    build_topic_map,
    parse_csa_subcategories,
    parse_csa_subjects,
    parse_csa_tables,
)

# Bentuk respons nyata WebAPI: data[0]=info halaman, data[1]=list baris.
SUBCAT_RESP = {
    "status": "OK",
    "data": [
        {"page": 1, "pages": 1, "per_page": 10, "count": 3, "total": 3},
        [
            {"subcat_id": 514, "title": "statistik demografi dan sosial"},
            {"subcat_id": 515, "title": "statistik ekonomi\n"},
            {"subcat_id": 516, "title": "statistik lingkungan hidup dan multi-domain"},
        ],
    ],
}

SUBJECT_RESP = {
    "status": "OK",
    "data": [
        {"page": 1, "pages": 2, "per_page": 10, "count": 10, "total": 11},
        [
            {"sub_id": 528, "title": "Aktivitas Politik dan Komunitas Lainnya",
             "subcat_id": 514, "subcat": "statistik demografi dan sosial"},
            {"sub_id": 529, "title": "Kependudukan", "subcat_id": 514,
             "subcat": "statistik demografi dan sosial"},
        ],
    ],
}

TABLE_RESP = {
    "status": "OK",
    "data": [
        {"page": 1, "pages": 1, "per_page": 10, "count": 2, "total": 2},
        [
            {"id": 1001, "title": "Jumlah Penduduk menurut Kecamatan"},
            {"id": 1002, "title": "Laju Pertumbuhan Penduduk"},
        ],
    ],
}


def test_parse_subcategories() -> None:
    rows = parse_csa_subcategories(SUBCAT_RESP)
    assert len(rows) == 3
    assert rows[0] == {"subcat_id": 514, "title": "statistik demografi dan sosial"}
    # whitespace dibersihkan
    assert rows[1]["title"] == "statistik ekonomi"


def test_parse_subjects() -> None:
    rows = parse_csa_subjects(SUBJECT_RESP)
    assert len(rows) == 2
    assert rows[1]["sub_id"] == 529
    assert rows[1]["subcat_id"] == 514


def test_parse_tables_links_subject() -> None:
    rows = parse_csa_tables(TABLE_RESP, subject_id=529)
    assert len(rows) == 2
    assert all(r["subject_csa_id"] == 529 for r in rows)
    assert rows[0]["table_id"] == "1001"


def test_parse_empty_data_safe() -> None:
    assert parse_csa_subcategories({"data": []}) == []
    assert parse_csa_subjects({"data": [{"page": 1}]}) == []
    assert parse_csa_tables({"status": "OK"}, subject_id=1) == []


def test_topic_map() -> None:
    subjects = parse_csa_subjects(SUBJECT_RESP)
    m = build_topic_map(subjects)
    assert m[529] == "Kependudukan"
    assert m[528].startswith("Aktivitas Politik")


# ---------------------------------------------------------------------------
# Ingester end-to-end (client stub + Postgres nyata bila MARAWA_TEST_DSN)
# ---------------------------------------------------------------------------

class _StubResult:
    def __init__(self, payload):
        self.payload = payload


class _StubClient:
    """Meniru BpsApiClient tanpa jaringan."""
    def get_path(self, endpoint, params):
        model = dict(params).get("model")
        if model == "subcatcsa":
            return _StubResult(SUBCAT_RESP)
        if model == "subjectcsa":
            return _StubResult(SUBJECT_RESP)
        if model == "tablestatistic":
            return _StubResult(TABLE_RESP)
        return _StubResult({"data": []})


def test_ingest_csa_stub_client_fills_tables() -> None:
    import os
    if not os.environ.get("MARAWA_TEST_DSN"):
        pytest.skip("MARAWA_TEST_DSN tidak diset")
    import psycopg
    from scripts.rag_store_pg import _dsn
    from workers.ingestion.bps_csa import ingest_csa
    conn = psycopg.connect(_dsn(), autocommit=False)
    try:
        counts = ingest_csa(_StubClient(), conn, snapshot_id=None)
        assert counts["subcategories"] == 3
        # stub mengembalikan SUBJECT_RESP per kategori (3x2=6 subjek) dan
        # TABLE_RESP per subjek (6x2=12 tabel).
        assert counts["subjects"] == 6
        assert counts["tables"] == 12
        # verifikasi isi (upsert idempotent — sub_id 529 unik)
        row = conn.execute(
            "SELECT title FROM public.bps_csa_subjects WHERE sub_id=529"
        ).fetchone()
        assert row and row[0] == "Kependudukan"
        tbl = conn.execute(
            "SELECT title FROM public.bps_csa_tables WHERE subject_csa_id=529"
        ).fetchone()
        assert tbl and "Penduduk" in tbl[0]
    finally:
        conn.rollback()  # isolated: jangan commit ke DB test
        conn.close()


def test_parse_tables_captures_tablesource() -> None:
    """tablesource (2=dynamic, 3=simdasi) tertangkap untuk mapping family."""
    resp = {"data": [{"page": 1}, [
        {"id": 1001, "title": "Jumlah Penduduk", "tablesource": 2},
        {"id": 1002, "title": "Laju Pertumbuhan", "tablesource": 3},
    ]]}
    rows = parse_csa_tables(resp, subject_id=529)
    assert rows[0]["tablesource"] == 2
    assert rows[1]["tablesource"] == 3


def test_csa_family_mapping() -> None:
    from workers.ingestion.bps_csa import csa_family
    assert csa_family(2) == "dynamic"
    assert csa_family(3) == "simdasi"
    assert csa_family(None) is None
    assert csa_family(9) is None  # tak dikenal
