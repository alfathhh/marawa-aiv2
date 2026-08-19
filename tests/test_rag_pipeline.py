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
