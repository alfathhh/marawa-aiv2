"""RAG pipeline — jembatan retrieval → query → evidence → jawaban.

Ini implementasi invariant #2/#3/#4 dari AGENTS.md di jalur agent:

  #2  NO FREE SQL      — query hanya lewat query_template_registry +
                         bind_template (param tervalidasi, LIMIT server-side).
  #3  NO ANGKA TANPA EVIDENCE — jawaban melewati answer_gate.evaluate; angka
                         yang tak tertelusur ke Evidence diblokir.
  #4  LLM TIDAK MENGHITUNG — angka dibaca dari bps_serving_* views, diformat
                         answer_formatter secara deterministik. LLM hanya
                         menarasikan bila pipeline menyerahkan teks ke model.

Siklus (per pesan user):
  OFFER   — offer_candidates() FTS atas bps_registry -> daftar kandidat.
  SELECT  — user menyebut ref (D1/S1/...) atau kata kunci -> kandidat terpilih.
  QUERY   — bind_template(template aktif utk family) -> baca serving view ->
            baris mentah -> build_evidence_from_row (provenance lengkap).
  ANSWER  — format_single_value() deterministic -> answer_gate.evaluate ->
            lolos: kirim; diblokir: abstention_text (bukan angka karangan).

Modul ini TIDAK memanggil LLM sendiri. Ia mengembalikan teks final (deterministik)
atau menyerahkan evidence terstruktur ke AgentRuntime untuk dinarasikan.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from scripts.answer_formatter import (
    Candidate,
    format_candidates,
    format_single_value,
)
from scripts.answer_gate import (
    Evidence,
    GateContext,
    NoDataReason,
    abstention_text,
    evaluate,
)

log = logging.getLogger("marawa-rag")

# Ref eksplisit yang dapat diketik user untuk memilih kandidat.
REF_RE = re.compile(r"\b([DCSP])(\d+)\b", re.IGNORECASE)

FAMILY_SOURCE_LABEL = {
    "dynamic": "Data Dinamis BPS",
    "simdasi": "SIMDASI BPS",
    "census": "Sensus BPS",
    "publication": "Publikasi BPS",
}


def source_label_for(selection: dict[str, Any]) -> str:
    return FAMILY_SOURCE_LABEL.get(selection.get("family") or "", "BPS")

# Pertanyaan yang mengandung tanda permintaan angka statistik.
GOAL_RE = re.compile(
    r"(berapa|jumlah|total|nilai|persen|tingkat|berapa banyak|data |pdrb|"
    r"inflasi|penduduk|kemiskinan|ipm|produksi|luas|hasil)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RagOutcome:
    """Hasil satu langkah pipeline. kind menentukan siapa yang lanjut."""

    kind: str  # offer | answer | clarify | unavailable | passthrough
    text: str
    candidate_refs: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    gate_violations: list[str] = field(default_factory=list)


def build_evidence_from_row(row: dict[str, Any], source_label: str) -> Evidence:
    """Satu baris serving view -> Evidence ber-provenance lengkap.

    evidence_id menyertakan snapshot_id agar setiap angka tertelusur ke
    snapshot ingestion yang melahirkannya (auditability, invariant #3).
    """
    value = row.get("value")
    if value is None:
        raise ValueError("row tanpa value tidak bisa menjadi evidence")
    snapshot = row.get("snapshot_id", "?")
    period = row.get("period") or row.get("period_latest")
    return Evidence(
        evidence_id=f"snap{snapshot}-{period}",
        value=Decimal(str(value)),
        unit=row.get("unit") or row.get("unit_raw") or None,
        unit_state=row.get("unit_state") or "unknown",
        period=str(period) if period is not None else None,
        geography=row.get("geography_name"),
        source_label=source_label,
    )


def render_candidates_reply(
    candidates: list[dict[str, Any]], recommended_ref: str | None
) -> str:
    """Fase OFFER: daftar kandidat via deterministic formatter (docs/18 §3)."""
    objs = [
        Candidate(
            ref=c.get("display_ref") or c.get("ref", "?"),
            family=c["family"],
            title=c.get("title", ""),
            period_range=c.get("period_range") or str(c.get("latest_year", "")),
        )
        for c in candidates
    ]
    return format_candidates(objs, recommended_ref=recommended_ref)


class RagPipeline:
    """Stateless-per-call pipeline; state percakapan hidup di store milik caller.

    Dependencies diinjeksikan agar dapat diuji tanpa DB/LLM nyata:
      store    — mengetahui kandidat terpilih terakhir per conversation.
      offer    — callable(index, text) -> offering dict (FTS registry).
      querier  — callable(family, text, selection) -> list[row] serving view.
    """

    def __init__(
        self,
        store: Any,
        llm: Any,
        querier: Callable[..., list[dict[str, Any]]] | None,
        offer: Callable[..., dict[str, Any]] | None = None,
        offering_index: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.querier = querier
        self._offer = offer
        self._index = offering_index

    def _do_offer(self, text: str) -> dict[str, Any] | None:
        if self._offer is None or self._index is None:
            return None
        return self._offer(self._index, text)

    def handle(self, conversation_id: str, text: str) -> RagOutcome:
        """Satu pesan user masuk -> satu outcome.

        Siklus (invariant #3 / docs/18 §3 — tidak ada query fakta sebelum
        user memilih kandidat):
          1. User menyebut REF dan ada daftar 'offered'  -> SELECT -> QUERY.
          2. Sudah ada seleksi aktif                      -> QUERY (follow-up).
          3. Pesan = goal data tanpa seleksi              -> OFFER (persist).
          4. Bukan goal data                              -> passthrough (LLM).
        """
        offered = self._current_offered(conversation_id)
        selection = self._current_selection(conversation_id)

        # (1) ref eksplisit + ada daftar yang ditawarkan -> user memilih.
        ref = self._extract_ref(text)
        if ref and offered and selection is None:
            chosen = self._pick_from_offered(offered, ref)
            if chosen is not None:
                sel = self._mark_selected(conversation_id, chosen)
                return self._answer_from_selection(
                    conversation_id, text, sel, selection_source="explicit_ref"
                )

        # (2) follow-up pada seleksi yang sudah ada.
        if selection is not None:
            return self._answer_from_selection(conversation_id, text, selection)

        # (3) goal data baru -> OFFER.
        if self._looks_like_goal(text):
            offering = self._do_offer(text)
            if offering and offering.get("groups"):
                candidates = self._flatten_candidates(offering["groups"])
                self._persist_offered(conversation_id, candidates)
                rec = (offering.get("recommendation") or {}).get("ref")
                text_out = render_candidates_reply(candidates, recommended_ref=rec)
                return RagOutcome(
                    kind="offer",
                    text=text_out,
                    candidate_refs=[c["display_ref"] for c in candidates],
                )
            return RagOutcome(
                kind="clarify",
                text=render_candidates_reply([], recommended_ref=None),
            )

        # (4) bukan goal data -> biarkan AgentRuntime meneruskan ke LLM.
        return RagOutcome(kind="passthrough", text="")

    # -- deteksi -----------------------------------------------------------

    @staticmethod
    def _flatten_candidates(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group items tidak membawa 'family' — inject dari level group."""
        out: list[dict[str, Any]] = []
        for g in groups:
            fam = g.get("family", "")
            for item in g.get("items", []):
                out.append({**item, "family": fam})
        return out

    @staticmethod
    def _looks_like_goal(text: str) -> bool:
        return bool(text) and bool(GOAL_RE.search(text))

    @staticmethod
    def _extract_ref(text: str) -> str | None:
        m = REF_RE.search(text or "")
        return f"{m.group(1).upper()}{m.group(2)}" if m else None

    @staticmethod
    def _pick_from_offered(
        offered: dict[str, Any], ref: str
    ) -> dict[str, Any] | None:
        for c in offered.get("candidates", []):
            if c.get("display_ref") == ref:
                return c
        return None

    # -- state (store) -----------------------------------------------------

    def _current_offered(self, conversation_id: str) -> dict[str, Any] | None:
        getter = getattr(self.store, "get_offered", None)
        return getter(conversation_id) if callable(getter) else None

    def _persist_offered(
        self, conversation_id: str, candidates: list[dict[str, Any]]
    ) -> None:
        setter = getattr(self.store, "set_offered", None)
        if callable(setter):
            setter(conversation_id, candidates)

    def _mark_selected(self, conversation_id: str, chosen: dict[str, Any]) -> dict[str, Any]:
        """Normalisasi candidate payload -> selection row, lalu persist."""
        selection = {
            "family": chosen.get("family"),
            "dataset_id": chosen.get("candidate_id"),
            "indicator_code": str(chosen.get("resource_id", "")),
            # title kandidat = indicator_name di serving view dynamic/simdasi
            "indicator_name": chosen.get("title", ""),
            "period": str(chosen.get("latest_year", "")),
        }
        setter = getattr(self.store, "set_selected", None)
        if callable(setter):
            setter(conversation_id, selection)
        return selection

    def _current_selection(self, conversation_id: str) -> dict[str, Any] | None:
        """Kandidat terpilih untuk percakapan ini (None bila belum memilih)."""
        getter = getattr(self.store, "get_selection", None)
        if not callable(getter):
            return None
        return getter(conversation_id)

    def _answer_from_selection(
        self, conversation_id: str, text: str, selection: dict[str, Any],
        selection_source: str = "active_dataset",
    ) -> RagOutcome:
        rows = self.querier(selection.get("family"), text, selection) or []
        if not rows:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.NOT_IN_CATALOGUE))

        evidence = [
            build_evidence_from_row(r, source_label=source_label_for(selection))
            for r in rows
        ]
        answer = format_single_value(
            evidence[0],
            indicator_label=rows[0].get("indicator_name", "indikator"),
        )
        context = GateContext(
            evidence=evidence,
            query_facts=True,
            # gate menolak query fakta tanpa bukti user memilih tabel.
            selection_source=selection_source,
            system_counts=frozenset({len(rows)}),
        )
        verdict = evaluate({"text": answer, "evidence_ids": [e.evidence_id for e in evidence]}, context)
        if verdict.blocked:
            log.warning(
                "rag gate memblokir jawaban conv=%s violations=%s",
                conversation_id, verdict.violations,
            )
            return RagOutcome(
                kind="unavailable",
                text=abstention_text(NoDataReason.GATE_BLOCKED),
                gate_violations=verdict.violations,
            )
        return RagOutcome(
            kind="answer",
            text=answer,
            evidence_ids=[e.evidence_id for e in evidence],
        )
