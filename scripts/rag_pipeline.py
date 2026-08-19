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
    format_trend,
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

# Intent aksi lanjutan (setelah seleksi / hasil ada).
COMPARE_RE = re.compile(
    r"(bandingkan|dibanding|selisih|selisihnya|lebih (besar|tinggi|banyak)|"
    r"naik|turun|pertumbuhan|tren|trend|perkembangan|dari tahun)",
    re.IGNORECASE,
)
ANALYZE_RE = re.compile(
    r"(urutkan|peringkat|tertinggi|terendah|paling (tinggi|besar|banyak|rendah)|"
    r"terbesar|terkecil|mana yang)",
    re.IGNORECASE,
)
PAGE_RE = re.compile(r"^(lanjut|next|berikutnya|lanjut publikasi)$", re.IGNORECASE)
RERANK_RE = re.compile(r"^bukan\s+\w+", re.IGNORECASE)  # "bukan pendidikan, jumlah SD"


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

        # (1b) aksi lanjutan pada daftar yang ditawarkan (paging / rerank).
        if offered and selection is None:
            if PAGE_RE.match(text.strip()):
                return self._page_candidates(conversation_id, offered)
            if RERANK_RE.match(text.strip()):
                return self._rerank(conversation_id, text)

        # (1) ref eksplisit + ada daftar yang ditawarkan -> user memilih.
        ref = self._extract_ref(text)
        if ref and offered and selection is None:
            chosen = self._pick_from_offered(offered, ref)
            if chosen is not None:
                sel = self._mark_selected(conversation_id, chosen)
                return self._answer_from_selection(
                    conversation_id, text, sel, selection_source="explicit_ref"
                )

        # (2) follow-up pada seleksi yang sudah ada: aksi turunan dulu
        #     (banding/analisis), baru query fakta biasa.
        if selection is not None:
            if COMPARE_RE.search(text):
                return self._compare_periods(conversation_id, text, selection)
            if ANALYZE_RE.search(text):
                return self._analyze_ranking(conversation_id, text, selection)
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
        family = selection.get("family")
        # Census: metadata inspect saja (multi-dimensi + pemekaran kecamatan
        # membuat agregat naif salah 2x). Publication: metadata saja (angka
        # butuh render PDF). Keduanya TIDAK lewat format_single_value.
        if family == "census":
            return self._inspect_census(conversation_id, text, selection)
        if family == "publication":
            return self._show_publications(conversation_id, text, selection)

        rows = self.querier(family, text, selection) or []
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

    def _inspect_census(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """Census = inspect_dataset. Jelaskan cakupan + periode, minta user
        memperjelas wilayah/dimensi. Tidak pernah menjawab agregat (invariant #3)."""
        rows = self.querier("census", text, selection) or []
        if not rows:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.NOT_IN_CATALOGUE))
        lines = [f"*Data Sensus — {selection.get('indicator_name','topik')}*", ""]
        for r in rows:
            lines.append(
                f"• {r.get('indicator_name')} — periode {r.get('period')}, "
                f"{r.get('wilayah_count')} kecamatan, {r.get('row_count')} baris rincian."
            )
        lines.append("")
        lines.append(
            "Data sensus ini sangat rinci (per kecamatan, jenis kelamin, agama, "
            "pendidikan, pekerjaan, dsb.). Sebutkan yang Anda butuhkan lebih "
            "spesifik — misalnya kecamatan dan perinciannya — atau balas *ADMIN* "
            "untuk dibantu petugas PST."
        )
        return RagOutcome(kind="answer", text="\n".join(lines))

    def _show_publications(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """Publication = metadata saja (judul, katalog, rilis, tautan PDF)."""
        rows = self.querier("publication", text, selection) or []
        if not rows:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.NOT_IN_CATALOGUE))
        lines = ["Berikut publikasi yang relevan:", ""]
        for i, p in enumerate(rows, 1):
            rilis = str(p.get("release_date") or "-")
            lines.append(f"{i}. *{p.get('title', 'Publikasi')}*")
            lines.append(f"   Rilis: {rilis} · Katalog: {p.get('catalog_number') or '-'}")
            if p.get("pdf_url"):
                lines.append(f"   PDF: {p['pdf_url']}")
            lines.append("")
        lines.append("Angka rinci di dalam publikasi bisa dibantu petugas. Balas *ADMIN*.")
        return RagOutcome(kind="answer", text="\n".join(lines))

    # -- aksi lanjutan ---------------------------------------------------

    def _compare_periods(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """query_and_compare: trend multi-periode (banding tahun)."""
        rows = self.querier(
            selection.get("family"), text,
            {**selection, "_mode": "trend"},
        ) or []
        if len(rows) < 2:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.PERIOD_UNAVAILABLE))
        evidence = [
            build_evidence_from_row(r, source_label=source_label_for(selection))
            for r in rows
        ]
        answer = format_trend(evidence, indicator_label=rows[0].get("indicator_name", "indikator"))
        context = GateContext(
            evidence=evidence, query_facts=True,
            selection_source="active_dataset",
            system_counts=frozenset({len(rows)}),
        )
        verdict = evaluate({"text": answer, "evidence_ids": [e.evidence_id for e in evidence]}, context)
        if verdict.blocked:
            log.warning("rag compare diblokir conv=%s: %s", conversation_id, verdict.violations)
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.GATE_BLOCKED), gate_violations=verdict.violations)
        return RagOutcome(kind="answer", text=answer, evidence_ids=[e.evidence_id for e in evidence])

    def _analyze_ranking(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """analyze_existing_result: ranking per kecamatan (tertinggi/terendah)."""
        rows = self.querier(
            selection.get("family"), text,
            {**selection, "_mode": "by_geography"},
        ) or []
        if not rows:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.GEOGRAPHY_UNAVAILABLE))
        evidence = [
            build_evidence_from_row(r, source_label=source_label_for(selection))
            for r in rows
        ]
        ind = rows[0].get("indicator_name", "indikator")
        period = rows[0].get("period", "")
        unit = rows[0].get("unit") or ""
        lines = [f"*{ind}* per kecamatan — {period}", ""]
        for i, r in enumerate(rows[:10], 1):
            val = r.get("value")
            val_s = f"{val:,.0f}".replace(",", ".") if isinstance(val, (int, float, Decimal)) else str(val)
            lines.append(f"{i}. {r.get('geography_name')}: {val_s} {unit}".rstrip())
        lines.append("")
        lines.append(f"Sumber: {source_label_for(selection)}")
        answer = "\n".join(lines)
        context = GateContext(
            evidence=evidence, query_facts=True,
            selection_source="active_dataset",
            system_counts=frozenset({len(rows)}),
        )
        verdict = evaluate({"text": answer, "evidence_ids": [e.evidence_id for e in evidence]}, context)
        if verdict.blocked:
            log.warning("rag analyze diblokir conv=%s: %s", conversation_id, verdict.violations)
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.GATE_BLOCKED), gate_violations=verdict.violations)
        return RagOutcome(kind="answer", text=answer, evidence_ids=[e.evidence_id for e in evidence])

    def _page_candidates(
        self, conversation_id: str, offered: dict[str, Any]
    ) -> RagOutcome:
        """candidate_page: halaman berikutnya dari daftar yang sedang tampil."""
        candidates = offered.get("candidates", [])
        if not candidates:
            return RagOutcome(kind="clarify", text=render_candidates_reply([], recommended_ref=None))
        # Halaman berikut = kandidat di luar 3 pertama yang belum tampil.
        rest = candidates[3:]
        if not rest:
            return RagOutcome(
                kind="clarify",
                text="Tidak ada kandidat tambahan. Silakan pilih dari daftar yang sudah tampil, atau balas *ADMIN*.",
            )
        text_out = render_candidates_reply(rest, recommended_ref=None)
        return RagOutcome(kind="offer", text=text_out, candidate_refs=[c["display_ref"] for c in rest])

    def _rerank(self, conversation_id: str, text: str) -> RagOutcome:
        """rerank_candidates: koreksi topik dengan negasi ("bukan X, Y")."""
        m = re.search(r"bukan\s+.+?,\s*(.+)$", text.strip(), re.IGNORECASE)
        new_query = m.group(1) if m else text
        offering = self._do_offer(new_query)
        if offering and offering.get("groups"):
            candidates = self._flatten_candidates(offering["groups"])
            self._persist_offered(conversation_id, candidates)
            rec = (offering.get("recommendation") or {}).get("ref")
            text_out = "Baik, saya carikan yang dimaksud.\n\n" + render_candidates_reply(candidates, recommended_ref=rec)
            return RagOutcome(kind="offer", text=text_out, candidate_refs=[c["display_ref"] for c in candidates])
        return RagOutcome(kind="clarify", text=render_candidates_reply([], recommended_ref=None))
