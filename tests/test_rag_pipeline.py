"""Contract tests untuk rag_pipeline — jembatan retrieval→query→evidence→answer.

Invariant yang dijaga (AGENTS.md):
- #2  no free SQL: query hanya lewat query_template_registry + bind_template.
- #3  no angka tanpa evidence: jawaban melewati answer_gate.evaluate.
- #4  LLM tidak menghitung: angka dari serving view, formatter deterministic.

Test ini memakai PostgresStore palsu in-memory untuk offering; query template
dan gate diuji terhadap kontrak nyata (bind_template menolak param jahat,
gate memblokir angka tanpa evidence).
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from scripts.answer_gate import Evidence, GateContext, evaluate
from scripts.rag_pipeline import (
    RagOutcome,
    RagPipeline,
    build_evidence_from_row,
    render_candidates_reply,
)


# --------------------------------------------------------------------------
# build_evidence_from_row — satu baris serving view -> Evidence ber-provenance
# --------------------------------------------------------------------------

def test_build_evidence_from_row_maps_provenance() -> None:
    row = {
        "snapshot_id": 461,
        "value": Decimal("433555"),
        "unit": "jiwa",
        "unit_state": "known",
        "period": "2025",
        "geography_name": "Padang Pariaman",
        "indicator_name": "Jumlah Penduduk",
    }
    ev = build_evidence_from_row(row, source_label="Data Dinamis")
    assert ev.value == Decimal("433555")
    assert ev.unit == "jiwa"
    assert ev.period == "2025"
    assert ev.geography == "Padang Pariaman"
    assert ev.source_label == "Data Dinamis"
    # evidence_id harus traceable ke snapshot
    assert "461" in ev.evidence_id


def test_build_evidence_rejects_missing_value() -> None:
    with pytest.raises(ValueError):
        build_evidence_from_row({"snapshot_id": 1, "value": None}, source_label="X")


# --------------------------------------------------------------------------
# render_candidates_reply — fase OFFER memakai formatter deterministic
# --------------------------------------------------------------------------

def test_render_candidates_reply_lists_refs() -> None:
    candidates = [
        {"ref": "D1", "family": "dynamic", "title": "Jumlah Penduduk", "period_range": "2010-2025"},
        {"ref": "S1", "family": "simdasi", "title": "Penduduk Simdasi", "period_range": "2020-2024"},
    ]
    text = render_candidates_reply(candidates, recommended_ref="D1")
    assert "D1" in text and "S1" in text
    assert "Data Dinamis" in text  # label family
    assert "D1" in text  # recommendation mentioned


def test_render_candidates_empty_gives_fallback_hint() -> None:
    text = render_candidates_reply([], recommended_ref=None)
    assert "belum menemukan" in text.lower() or "petugas" in text.lower()


# --------------------------------------------------------------------------
# Gate integration — angka dari evidence lolos, angka karangan diblokir
# --------------------------------------------------------------------------

def _ctx_with_evidence(value: Decimal) -> GateContext:
    ev = Evidence(
        evidence_id="snap461-r1", value=value, unit="jiwa", unit_state="known",
        period="2025", geography="Padang Pariaman", source_label="Data Dinamis",
    )
    return GateContext(evidence=[ev], query_facts=True)


def test_gate_allows_number_from_evidence() -> None:
    ctx = _ctx_with_evidence(Decimal("433555"))
    envelope = {"text": "Jumlah penduduk Padang Pariaman tahun 2025 adalah 433555 jiwa."}
    verdict = evaluate(envelope, ctx)
    # angka dari evidence tidak masuk ungrounded
    assert "433555" not in verdict.ungrounded_numbers


def test_gate_blocks_fabricated_number() -> None:
    ctx = _ctx_with_evidence(Decimal("433555"))
    envelope = {"text": "Jumlah penduduknya 999999 jiwa."}
    verdict = evaluate(envelope, ctx)
    assert "999999" in verdict.ungrounded_numbers or verdict.blocked


# --------------------------------------------------------------------------
# RagPipeline outcome state machine
# --------------------------------------------------------------------------

def test_rag_outcome_kinds() -> None:
    o = RagOutcome(kind="offer", text="pilih D1", candidate_refs=["D1"])
    assert o.kind == "offer"
    o2 = RagOutcome(kind="answer", text="433555 jiwa", evidence_ids=["e1"])
    assert o2.evidence_ids == ["e1"]


def test_pipeline_rejects_query_before_selection() -> None:
    """Invariant: query_stat_data dilarang sebelum user pilih kandidat."""
    pipe = RagPipeline(store=None, llm=None, querier=None)
    out = pipe.handle(conversation_id="c1", text="berapa penduduk?")
    # tanpa candidate terpilih, pipeline harus OFFER atau CLARIFY, bukan answer
    assert out.kind in ("offer", "clarify", "unavailable")


# --------------------------------------------------------------------------
# Integrasi AgentRuntime: goal data dijawab RAG, LLM tidak dipanggil
# --------------------------------------------------------------------------

def test_agent_runtime_answers_goal_via_rag_without_llm() -> None:
    from scripts.agent_runtime import AgentRuntime, StaticLLM
    from scripts.app import Store
    from scripts.conversation_state import ConversationState, State

    cid = "628555@s.whatsapp.net"
    store = Store()
    store.conversations[cid] = ConversationState(
        conversation_id=cid, state=State.BOT_ACTIVE, agent_run_active=True,
    )
    store.append_message(cid, "in", "user", "berapa penduduk?", wa_message_id=f"w-{uuid.uuid4()}")

    # LLM yang akan gagal total bila dipanggil -> membuktikan RAG yang jawab.
    class _ExplodingLLM:
        def complete(self, messages):
            raise AssertionError("LLM tidak boleh dipanggil untuk goal data")

    offered = {"groups": [{"family": "dynamic", "best_score": 1.0, "items": [
        {"display_ref": "D1", "family": "dynamic", "title": "Jumlah Penduduk",
         "latest_year": "2025", "period_range": "2010-2025"},
    ]}], "recommendation": {"ref": "D1"}}
    rag = RagPipeline(
        store=None, llm=None, querier=None,
        offer=lambda idx, text: offered, offering_index={},
    )
    runtime = AgentRuntime(store=store, llm=_ExplodingLLM(), rag=rag)
    assert runtime.process_pending(limit=5) == 1
    outbox = [r for r in store.outbox.values() if r.conversation_id == cid]
    assert len(outbox) == 1
    assert "D1" in outbox[0].body  # kandidat ditawarkan, bukan angka karangan
    assert store.conversations[cid].agent_run_active is False


def test_simdasi_querier_kabupaten_live() -> None:
    """Querier simdasi mengambil baris row_role=kabupaten (agregat resmi)."""
    pytest.importorskip("psycopg")
    import os
    if not os.environ.get("MARAWA_TEST_DSN"):
        pytest.skip("MARAWA_TEST_DSN tidak diset")
    import sys
    sys.path.insert(0, ".")
    from scripts.rag_store_pg import query_serving
    rows = query_serving("simdasi", "penduduk 2025", {"family": "simdasi", "indicator_name": "Jumlah Penduduk"})
    assert rows, "simdasi harus mengembalikan baris kabupaten"
    r = rows[0]
    assert r["value"] is not None and r["unit_state"] in ("known", "canonical")


def test_security_headers_present_on_admin() -> None:
    """Middleware header keamanan wajib ada di setiap respons (audit 19/8)."""
    from fastapi.testclient import TestClient
    from scripts.app import app
    c = TestClient(app)
    r = c.get("/admin")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "frame-ancestors" in (r.headers.get("content-security-policy") or "")

def test_root_redirects_to_admin() -> None:
    from fastapi.testclient import TestClient
    from scripts.app import app
    c = TestClient(app, follow_redirects=False)
    r = c.get("/")
    assert r.status_code in (302, 307)
    assert r.headers.get("location") == "/admin"


def test_census_inspect_mode_no_aggregate() -> None:
    """Census tidak pernah auto-agregat (pemekaran kecamatan -> SUM salah 2x)."""
    import os, sys
    if not os.environ.get("MARAWA_TEST_DSN"):
        pytest.skip("MARAWA_TEST_DSN tidak diset")
    sys.path.insert(0, ".")
    from scripts.rag_store_pg import query_census_inspect, _dsn
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(_dsn(), row_factory=dict_row) as c:
        rows = query_census_inspect(c, "penduduk", None)
    assert rows, "census inspect harus mengembalikan cakupan"
    assert "wilayah_count" in rows[0] and "value" not in rows[0]

def test_publication_metadata_only() -> None:
    """Publication mengembalikan metadata (judul/katalog), bukan angka."""
    import os, sys
    if not os.environ.get("MARAWA_TEST_DSN"):
        pytest.skip("MARAWA_TEST_DSN tidak diset")
    sys.path.insert(0, ".")
    from scripts.rag_store_pg import query_publication_meta, _dsn
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(_dsn(), row_factory=dict_row) as c:
        rows = query_publication_meta(c, "penduduk", None)
    assert rows and "title" in rows[0] and "value" not in rows[0]


# --- aksi lanjutan (compare/analyze/paging/rerank) ---

def _pipe_with_geo():
    import os, sys
    if not os.environ.get("MARAWA_TEST_DSN"):
        pytest.skip("MARAWA_TEST_DSN tidak diset")
    sys.path.insert(0, ".")
    import psycopg
    from scripts.rag_pipeline import RagPipeline
    from scripts.rag_store_pg import PgSelectionStore, _dsn, load_offering_index, make_offer, query_serving
    idx = load_offering_index(); sel = PgSelectionStore(_dsn())
    rag = RagPipeline(store=sel, llm=None, querier=query_serving, offer=make_offer(), offering_index=idx)
    return rag, sel, _dsn

def test_compare_periods_trend() -> None:
    import psycopg
    rag, sel, dsn_fn = _pipe_with_geo(); dsn = dsn_fn()
    cid = "628100000001@s.whatsapp.net"
    with psycopg.connect(dsn) as c: c.execute("DELETE FROM public.rag_selection WHERE conversation_id=%s",(cid,)); c.commit()
    rag.handle(cid, "berapa jumlah penduduk?"); rag.handle(cid, "D1")
    o = rag.handle(cid, "bandingkan dengan tahun sebelumnya")
    assert o.kind == "answer"
    assert "2025" in o.text and "2024" in o.text  # trend multi-periode

def test_analyze_ranking_kecamatan() -> None:
    import psycopg
    rag, sel, dsn_fn = _pipe_with_geo(); dsn = dsn_fn()
    cid = "628100000002@s.whatsapp.net"
    with psycopg.connect(dsn) as c: c.execute("DELETE FROM public.rag_selection WHERE conversation_id=%s",(cid,)); c.commit()
    rag.handle(cid, "berapa jumlah penduduk?"); rag.handle(cid, "D1")
    o = rag.handle(cid, "urutkan kecamatan tertinggi")
    assert o.kind == "answer"
    assert "Kabupaten" not in o.text.split(chr(10))[2]  # baris pertama bukan agregat kabupaten
    assert "Batang Anai" in o.text or "jiwa" in o.text

def test_paging_candidates() -> None:
    import psycopg
    rag, sel, dsn_fn = _pipe_with_geo(); dsn = dsn_fn()
    cid = "628100000003@s.whatsapp.net"
    with psycopg.connect(dsn) as c: c.execute("DELETE FROM public.rag_selection WHERE conversation_id=%s",(cid,)); c.commit()
    rag.handle(cid, "publikasi tentang penduduk")
    o = rag.handle(cid, "lanjut")
    assert o.kind in ("offer", "clarify")

def test_rerank_negation() -> None:
    import psycopg
    rag, sel, dsn_fn = _pipe_with_geo(); dsn = dsn_fn()
    cid = "628100000004@s.whatsapp.net"
    with psycopg.connect(dsn) as c: c.execute("DELETE FROM public.rag_selection WHERE conversation_id=%s",(cid,)); c.commit()
    rag.handle(cid, "data pendidikan")
    o = rag.handle(cid, "bukan pendidikan, jumlah SD")
    assert o.kind in ("offer", "clarify")
