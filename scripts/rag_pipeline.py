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
    describe_indicator,
    format_candidates,
    format_comparison,
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

# Goal = kata tanya / penanda statistik ringan. Topik divalidasi FTS terpisah.
# Sapaan/obrolan ("halo","makasih","terus gimana?") tidak mengandung penanda.
GOAL_RE = re.compile(
    r"(berapa|brapa|berapaan|berapa banyak|jumlah|jumla|total|nilai|persen|"
    r"tingkat|data\b|statistik|bandingkan|dibanding|urutkan|peringkat|"
    r"tertinggi|terendah|paling|mana yang|padat|kepadatan|pertumbuhan)",
    re.IGNORECASE,
)
QUESTION_MARK_RE = re.compile(r"[?？]")

# Sapaan murni — tanpa kata tanya data. Dibalas deterministik, BUKAN lewat LLM.
# AKAR BUG (2026-08-20): "halo" jatuh ke LLM yang membaca history penuh data
# (PDRB) lalu menjawab PDRB. Sapaan tidak boleh membawa konteks data lama.
GREETING_RE = re.compile(
    r"^\s*(halo+w*|ha+llo+|hai+|hei+|hello|hi|pagi|siang|sore|malam|assalamu['’]?alaikum|"
    r"selamat (pagi|siang|sore|malam)|permisi|tes|test|ping|hola|yo)(\b|\s|$|[!.]*)$",
    re.IGNORECASE,
)
THANKS_RE = re.compile(
    r"^\s*(makasih|terima kasih|thanks|thank you|tq|ok+|oke+|sip|mantap|baik|noted|ok sip|oke deh)(\b|\s|$|[!.]*)$",
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
# Follow-up tanya provenance pada seleksi aktif — bukan goal baru.
SOURCE_RE = re.compile(r"(dari sumber|sumber (apa|mana)|sumbernya|dari mana)", re.IGNORECASE)

# Dua tahun eksplisit untuk perbandingan head-to-head ("2024 vs 2025",
# "2023 dibanding 2025", "2022 dan 2024"). Diambil server-side, bukan LLM.
TWO_YEARS_RE = re.compile(r"\b(19\d{2}|20\d{2})\b\D{0,12}\b(19\d{2}|20\d{2})\b")


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
    candidates: list[dict[str, Any]], recommended_ref: str | None,
    csa_labeler=None,
) -> str:
    """Fase OFFER: daftar kandidat via deterministic formatter (docs/18 §3).

    csa_labeler (opsional): callable(title) -> label topik CSA. Bila tersedia,
    kandidat diberi konteks topik resmi — memperkaya FTS leksikal tanpa
    menggantikannya.
    """
    objs = [
        Candidate(
            ref=c.get("display_ref") or c.get("ref", "?"),
            family=c["family"],
            title=c.get("title", ""),
            period_range=c.get("period_range") or str(c.get("latest_year", "")),
        )
        for c in candidates
    ]
    text = format_candidates(objs, recommended_ref=recommended_ref)
    # CSA label: satu baris konteks topik di atas daftar (bila tersedia)
    if csa_labeler and objs:
        label = csa_labeler(objs[0].title)
        if label:
            text = f"Topik: {label}\n\n" + text
    return text


class RagPipeline:
    """Stateless-per-call pipeline; state percakapan hidup di store milik caller.

    Dependencies diinjeksikan agar dapat diuji tanpa DB/LLM nyata:
      store    — mengetahui kandidat terpilih terakhir per conversation.
      offer    — callable(index, text) -> offering dict (FTS registry).
      querier  — callable(family, text, selection) -> list[row] serving view.
      meta     — callable(family, indicator_name) -> metadata deskripsi (opsional).
    """

    def __init__(
        self,
        store: Any,
        llm: Any,
        querier: Callable[..., list[dict[str, Any]]] | None,
        offer: Callable[..., dict[str, Any]] | None = None,
        offering_index: dict[str, Any] | None = None,
        meta: Callable[..., dict[str, Any]] | None = None,
        csa_labeler: Callable[..., str | None] | None = None,
    ) -> None:
        self.store = store
        self.querier = querier
        self._offer = offer
        self._index = offering_index
        self._meta = meta
        # csa_labeler opsional: label topik CSA untuk kandidat (None = mati).
        self._csa_labeler = csa_labeler

    def _describe(self, selection: dict[str, Any], sample_row: dict[str, Any]) -> str:
        """Deskripsi indikator dari metadata — diletakkan SEBELUM angka agar
        jawaban tidak kering. Kosong bila meta fetcher tak diset (test)."""
        if self._meta is None:
            return ""
        try:
            m = self._meta(selection.get("family"), selection.get("indicator_name", "")) or {}
        except Exception:
            return ""
        desc = describe_indicator(
            selection.get("indicator_name", ""),
            topic_name=m.get("topic_name") or m.get("subject_name"),
            definition=m.get("definition"),
            unit=sample_row.get("unit") or m.get("unit_canonical"),
            period_min=m.get("period_min"),
            period_max=m.get("period_max"),
            period_granularity=m.get("period_granularity"),
            geography_label=sample_row.get("geography_name"),
        )
        return (desc + "\n\n") if desc else ""

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

        # (0) REF TAK DIKENAL saat ada daftar ditawarkan -> tampilkan daftar lagi
        #     (lebih berguna dari clarify kosong — user tinggal pilih yang ada).
        if offered and selection is None:
            ref = self._extract_ref(text)
            if ref and self._pick_from_offered(offered, ref) is None:
                candidates = offered.get("candidates") or []
                if candidates:
                    return RagOutcome(
                        kind="offer",
                        text=(f'Kode "{ref}" tidak ada di daftar. Ini yang tersedia:\n\n'
                              + render_candidates_reply(candidates, recommended_ref=None)),
                        candidate_refs=[c.get("display_ref") for c in candidates],
                    )
                return RagOutcome(
                    kind="clarify",
                    text=(f'Kode "{ref}" tidak ada. Sebutkan ulang topiknya.'),
                )

        # (1b) aksi lanjutan pada daftar yang ditawarkan (paging / rerank).
        if offered and selection is None:
            if PAGE_RE.match(text.strip()):
                return self._page_candidates(conversation_id, offered)
            if RERANK_RE.match(text.strip()):
                return self._rerank(conversation_id, text)

        # (1a) AKSI LANJUTAN pada seleksi aktif DIDAHULUKAN dari goal-baru.
        #      "bandingkan 2023 vs 2025" mengandung kata tanya tapi itu COMPARE,
        #      bukan topik baru. Begitu pula follow-up tahun ("tahun 2018 berapa?").
        if selection is not None:
            has_year = bool(re.search(r"\b(19\d{2}|20\d{2})\b", text or ""))
            if SOURCE_RE.search(text):
                return self._answer_source(conversation_id, selection)
            if COMPARE_RE.search(text):
                return self._compare_periods(conversation_id, text, selection)
            if ANALYZE_RE.search(text):
                return self._analyze_ranking(conversation_id, text, selection)
            if has_year:
                return self._answer_from_selection(conversation_id, text, selection)

        # (1) ref eksplisit + ada daftar yang ditawarkan -> user memilih.
        ref = self._extract_ref(text)
        if ref and offered and selection is None:
            chosen = self._pick_from_offered(offered, ref)
            if chosen is not None:
                # bawa tahun dari goal awal (yang disimpan di offered) agar
                # "jumlah penduduk 2023" -> pilih D1 menjawab 2023, bukan latest.
                chosen = {**chosen, "_goal_year": (offered or {}).get("goal_year")}
                sel = self._mark_selected(conversation_id, chosen)
                return self._answer_from_selection(
                    conversation_id, text, sel, selection_source="explicit_ref"
                )

        # (1c) GOAL BARU mengalahkan follow-up: kalau pesan adalah goal data baru
        #      DAN menyebut topik BERBEDA dari seleksi aktif -> OFFER ulang.
        #      FTS dinormalisasi: kata konsep (bukan seluruh kalimat) supaya
        #      "sekarang PDRB dong" tetap ketemu "PDRB".
        if selection is not None and self._looks_like_goal(text):
            active = (selection.get("indicator_name") or "").lower()
            first_word = active.split()[0] if active else ""
            if first_word and first_word not in text.lower():
                offering = self._do_offer(self._concept_query(text))
                if offering and offering.get("groups"):
                    candidates = self._flatten_candidates(offering["groups"])
                    self._persist_offered(conversation_id, candidates)
                    self._clear_selection(conversation_id)
                    rec = (offering.get("recommendation") or {}).get("ref")
                    return RagOutcome(
                        kind="offer",
                        text=render_candidates_reply(candidates, recommended_ref=rec, csa_labeler=self._csa_labeler),
                        candidate_refs=[c["display_ref"] for c in candidates],
                    )
                self._clear_selection(conversation_id)
                return RagOutcome(
                    kind="clarify",
                    text=render_candidates_reply([], recommended_ref=None),
                )

        # (2b) TANPA seleksi: pesan aksi (banding/urutkan) ADALAH goal baru —
        #      user minta perbandingan/ranking tapi belum pilih kandidat -> OFFER.
        #      (Di dalam seleksi, pesan yang sama = aksi lanjutan, bukan goal baru.)
        if selection is None and self._is_action_without_selection(text):
            offering = self._do_offer(text) or self._do_offer(self._concept_query(text))
            if offering and offering.get("groups"):
                candidates = self._prioritize_answerable(self._flatten_candidates(offering["groups"]), query_text=text)
                ym = re.search(r"\b(19\d{2}|20\d{2})\b", text)
                self._persist_offered(conversation_id, candidates, goal_year=ym.group(1) if ym else None)
                rec = candidates[0]["display_ref"] if candidates else None
                return RagOutcome(
                    kind="offer",
                    text=render_candidates_reply(candidates, recommended_ref=rec, csa_labeler=self._csa_labeler),
                    candidate_refs=[c["display_ref"] for c in candidates],
                )

        # (2) follow-up pada seleksi: pesan tanpa goal/aksi/tahun -> passthrough
        #     (jangan query ulang tanpa user bertanya).
        if selection is not None:
            if self._looks_like_goal(text):
                return self._answer_from_selection(conversation_id, text, selection)
            return RagOutcome(kind="passthrough", text="")

        # (3) goal data baru -> OFFER.
        if self._looks_like_goal(text):
            # offering pada teks APA ADANYA dan konsepnya; ambil yang menemukan
            # kandidat. "coba carikan kematian" -> konsep "kematian" ketemu.
            offering = self._do_offer(text)
            if not (offering and offering.get("groups")):
                offering = self._do_offer(self._concept_query(text))
            # Catatan: FTS leksikal tidak bisa membedakan "pendudk" (typo legit)
            # dari "unicorn" (tak ada) lewat skor — keduanya rendah. Maka TIDAK
            # ada threshold skor di sini; kandidat ditawarkan dan user memutuskan.
            # Anti-nebak dijaga oleh gate di hilir, bukan dengan menyembunyikan
            # kandidat yang mungkin relevan.
            if offering and offering.get("groups"):
                candidates = self._flatten_candidates(offering["groups"])
                # Rekomendasi & urutan: untuk goal ANGKA, family yang bisa menjawab
                # fakta (dynamic/simdasi) didahulukan dari publication/census yang
                # hanya metadata. FTS menaruh "tahun 2020" cocok judul publikasi
                # sensus 2020 di atas — itu menyesatkan user yang mau angka.
                candidates = self._prioritize_answerable(candidates, query_text=text)
                # simpan tahun yang user sebut di goal, supaya selection mewarisinya
                ym = re.search(r"\b(19\d{2}|20\d{2})\b", text)
                self._persist_offered(conversation_id, candidates, goal_year=ym.group(1) if ym else None)
                rec = candidates[0]["display_ref"] if candidates else None
                text_out = render_candidates_reply(candidates, recommended_ref=rec, csa_labeler=self._csa_labeler)
                return RagOutcome(
                    kind="offer",
                    text=text_out,
                    candidate_refs=[c["display_ref"] for c in candidates],
                )
            return RagOutcome(
                kind="clarify",
                text=render_candidates_reply([], recommended_ref=None),
            )

        # (3b) SAPAAN / TERIMA KASIH murni -> greeting deterministik, TANPA LLM.
        #      LLM membaca history data lama (PDRB) dan menjawab itu untuk
        #      "halo" — salah total. Sapaan dijawab tetap, tidak membawa konteks.
        if selection is None and GREETING_RE.match(text.strip()):
            return RagOutcome(
                kind="answer",
                text=(
                    "Halo! Saya MARAWA, asisten layanan statistik BPS Kabupaten "
                    "Padang Pariaman. Silakan tanyakan data yang Anda butuhkan, "
                    "misalnya: *berapa jumlah penduduk?*, *berapa PDRB 2024?*, "
                    "atau *berapa produksi padi?*."
                ),
            )
        if selection is None and THANKS_RE.match(text.strip()):
            return RagOutcome(
                kind="answer",
                text="Sama-sama! Silakan tanya lagi kalau butuh data statistik lain.",
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

    def _looks_like_goal(self, text: str) -> bool:
        """Goal data — bukan daftar topik hardcoded.

        Sinyal: (a) penanda kata tanya/statistik ringan, ATAU (b) tanda '?' +
        FTS ketemu, ATAU (c) FTS kuat pada teks ATAU konsepnya (stopword dibuang
        — 'coba carikan hotel' dan 'hotel' sama-sama ketemu).

        PESAN AKSI (banding/urutkan/lanjut/sumber) BUKAN goal baru — dideteksi
        di sini agar tak salah arah ke (1c).
        """
        if not text or not text.strip():
            return False
        # aksi lanjutan bukan goal baru
        if COMPARE_RE.search(text) or ANALYZE_RE.search(text) or SOURCE_RE.search(text) or PAGE_RE.match(text.strip()):
            return False
        if GOAL_RE.search(text):
            return True
        for variant in (text, self._concept_query(text)):
            offering = self._do_offer(variant)
            groups = (offering or {}).get("groups", [])
            if not groups:
                continue
            best = max((g.get("best_score", 0) for g in groups), default=0)
            if QUESTION_MARK_RE.search(text):
                return True
            if best >= self._GOAL_MIN_SCORE:
                return True
        return False

    _GOAL_MIN_SCORE = 2.0

    @staticmethod
    def _prioritize_answerable(
        candidates: list[dict[str, Any]], query_text: str = ""
    ) -> list[dict[str, Any]]:
        """Urutkan kandidat: FAMILY yang bisa jawab agregat dulu, lalu relevansi
        topik sebagai pengikat di dalam tier yang sama.

        Akar bug #1: dynamic membabi buta menaruh "Jumlah Penduduk" di atas utk
        "infrastruktur kesehatan". Akar bug #2: relevansi membabi buta menaruh
        simdasi (tak bisa agregat kabupaten) di atas dynamic utk "penduduk
        kecamatan X". Solusi: dua kunci — (family_answerable, -relevance).
        Family answerable = dynamic/simdasi (bisa query fakta); census/publication
        belakangan. Di dalam tier yang sama, yang paling relevan topiknya menang.
        """
        rank = {"dynamic": 0, "simdasi": 1, "census": 2, "publication": 3}
        stop = {
            "berapa", "jumlah", "total", "berapa banyak", "berapaan", "data",
            "tahun", "di", "ke", "dari", "yang", "dan", "atau", "padang",
            "pariaman", "kabupaten", "dong", "sih", "nih", "kan", "ya", "deh",
        }
        keywords = [
            w for w in re.findall(r"[a-zA-Z]{4,}", (query_text or "").lower())
            if w not in stop
        ]

        def relevance(c: dict[str, Any]) -> int:
            title = (c.get("title") or "").lower()
            return sum(1 for k in keywords if k in title)

        # family-tier dulu (dynamic/simdasi bisa jawab), lalu relevansi di dalamnya.
        # TAPI: kandidat answerable yang RELEVAN topiknya (mengandung kata kunci
        # spesifik user) harus mengalahkan kandidat answerable yang tidak relevan.
        # Jadi: kelompokkan answerable (dyn/simd) vs lainnya; dalam kelompok
        # answerable, urutkan berdasarkan relevansi; sisanya di bawah.
        def tier(c):
            return rank.get(c.get("family", ""), 9)

        answerable = [c for c in candidates if tier(c) <= 1]
        others = [c for c in candidates if tier(c) > 1]
        answerable.sort(key=lambda c: -relevance(c))
        others.sort(key=lambda c: (tier(c), -relevance(c)))
        return answerable + others

    @staticmethod
    def _is_action_without_selection(text: str) -> bool:
        """Pesan aksi (banding/urutkan/ranking) tanpa seleksi aktif = goal baru.
        Deteksi kata aksi saja — topik diserahkan ke FTS registry."""
        return bool(COMPARE_RE.search(text) or ANALYZE_RE.search(text))

    @staticmethod
    def _extract_ref(text: str) -> str | None:
        m = REF_RE.search(text or "")
        return f"{m.group(1).upper()}{m.group(2)}" if m else None

    @staticmethod
    def _concept_query(text: str) -> str:
        """Ekstrak kata konsep (kata benda kapital/panjang) untuk FTS saat ganti
        topik — buang kata sambung/gaul agar 'sekarang PDRB dong' -> 'PDRB'.

        Bukan daftar topik hardcoded: ini hanya membuang stopword umum; kata
        konsep yang tersisa diserahkan ke FTS registry apa adanya.
        """
        stop = {
            "sekarang", "dong", "dong,", "gimana", "gmn", "kalau", "kalo", "yang",
            "dan", "atau", "dengan", "untuk", "dari", "pada", "di", "ke", "ini",
            "itu", "saja", "aja", "juga", "kok", "sih", "nih", "kan", "deh", "lah",
            "berapa", "berapaan", "berapa banyak", "berapaan sih",
            # kata pembungkus permintaan — dibuang agar konsep bersih:
            "coba", "carikan", "carikan", "tolong", "minta", "mintak", "cari",
            "carikanlah", "mohon", "please", "cobain", "cariin", "tunjukkan",
            "tampilkan", "kasih", "kasih tau", "beri", "berikan", "dong",
            "data", "tahun", "berapa", "berapa banyak",
            # kata ukuran/kuantitas — bukan konsep:
            "total", "jumlah", "jumla", "banyak", "berapa", "nilai", "angka",
        }
        words = re.findall(r"[A-Za-z]{3,}", text)
        kept = [w for w in words if w.lower() not in stop]
        return " ".join(kept) if kept else text

    @staticmethod
    def _pick_from_offered(
        offered: dict[str, Any], ref: str
    ) -> dict[str, Any] | None:
        candidates = offered.get("candidates") or []
        for c in candidates:
            if c.get("display_ref") == ref:
                return c
        return None

    # -- state (store) -----------------------------------------------------

    def _current_offered(self, conversation_id: str) -> dict[str, Any] | None:
        getter = getattr(self.store, "get_offered", None)
        return getter(conversation_id) if callable(getter) else None

    def _persist_offered(
        self, conversation_id: str, candidates: list[dict[str, Any]],
        goal_year: str | None = None,
    ) -> None:
        setter = getattr(self.store, "set_offered", None)
        if callable(setter):
            # goal_year ikut tersimpan agar selection mewarisi tahun dari goal awal
            setter(conversation_id, {"candidates": candidates, "goal_year": goal_year})

    def _mark_selected(self, conversation_id: str, chosen: dict[str, Any]) -> dict[str, Any]:
        """Normalisasi candidate payload -> selection row, lalu persist."""
        selection = {
            "family": chosen.get("family"),
            "dataset_id": chosen.get("candidate_id"),
            "indicator_code": str(chosen.get("resource_id", "")),
            # title kandidat = indicator_name di serving view dynamic/simdasi
            "indicator_name": chosen.get("title", ""),
            # tahun dari goal awal (bila user menyebut) mengalahkan latest_year
            "period": str(chosen.get("_goal_year") or chosen.get("latest_year", "")),
        }
        setter = getattr(self.store, "set_selected", None)
        if callable(setter):
            setter(conversation_id, selection)
        return selection

    def _clear_selection(self, conversation_id: str) -> None:
        """Hapus seleksi aktif (saat user ganti topik)."""
        clearer = getattr(self.store, "clear_selection", None)
        if callable(clearer):
            clearer(conversation_id)

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

        # Tahun: dari teks user bila ada; kalau tidak, warisi selection.period
        # (tahun yang user sebut di goal awal). Ini membuat "jumlah penduduk
        # 2023" -> pilih D1 menjawab 2023, bukan tahun terbaru.
        eff_text = text
        if not re.search(r"\b(19\d{2}|20\d{2})\b", text or "") and selection.get("period"):
            eff_text = f"{text} {selection['period']}"
        rows = self.querier(family, eff_text, selection) or []
        if not rows:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.NOT_IN_CATALOGUE))

        evidence = [
            build_evidence_from_row(r, source_label=source_label_for(selection))
            for r in rows
        ]
        answer = self._describe(selection, rows[0]) + format_single_value(
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

    def _answer_source(
        self, conversation_id: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """Follow-up 'ini dari sumber apa?' — jawab provenance seleksi aktif."""
        src = source_label_for(selection)
        ind = selection.get("indicator_name", "indikator")
        period = selection.get("period") or "terbaru"
        text = (
            f"Data *{ind}* periode {period} ini bersumber dari *{src}*, "
            "basis data resmi BPS Kabupaten Padang Pariaman yang tercermin di "
            "sistem ini. Setiap angka yang saya sampaikan berasal dari sana, "
            "bukan perkiraan."
        )
        return RagOutcome(kind="answer", text=text)

    def _compare_periods(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """query_and_compare. HANYA family ber-nilai (dynamic/simdasi);
        census/publication tidak punya value agregat -> unavailable, JANGAN crash."""
        if selection.get("family") not in ("dynamic", "simdasi"):
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.PERIOD_UNAVAILABLE))
        pair = TWO_YEARS_RE.search(text or "")
        if pair:
            return self._compare_two_years(conversation_id, pair.group(1), pair.group(2), selection)
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
        answer = self._describe(selection, rows[0]) + format_trend(evidence, indicator_label=rows[0].get("indicator_name", "indikator"))
        return self._gate_or_abstain(conversation_id, answer, evidence, selection)

    def _compare_two_years(
        self, conversation_id: str, year_a: str, year_b: str, selection: dict[str, Any]
    ) -> RagOutcome:
        rows_a = self.querier(selection.get("family"), year_a, selection) or []
        rows_b = self.querier(selection.get("family"), year_b, selection) or []
        if not rows_a or not rows_b:
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.PERIOD_UNAVAILABLE))
        older, newer = (rows_a[0], rows_b[0]) if year_a <= year_b else (rows_b[0], rows_a[0])
        ev_old = build_evidence_from_row(older, source_label=source_label_for(selection))
        ev_new = build_evidence_from_row(newer, source_label=source_label_for(selection))
        answer = self._describe(selection, newer) + format_comparison(ev_old, ev_new, indicator_label=newer.get("indicator_name", "indikator"))
        return self._gate_or_abstain(conversation_id, answer, [ev_old, ev_new], selection)

    def _gate_or_abstain(
        self, conversation_id: str, answer: str, evidence: list, selection: dict[str, Any]
    ) -> RagOutcome:
        context = GateContext(
            evidence=evidence, query_facts=True,
            selection_source="active_dataset",
            system_counts=frozenset({len(evidence)}),
        )
        verdict = evaluate({"text": answer, "evidence_ids": [e.evidence_id for e in evidence]}, context)
        if verdict.blocked:
            log.warning("rag compare diblokir conv=%s: %s", conversation_id, verdict.violations)
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.GATE_BLOCKED), gate_violations=verdict.violations)
        return RagOutcome(kind="answer", text=answer, evidence_ids=[e.evidence_id for e in evidence])

    def _analyze_ranking(
        self, conversation_id: str, text: str, selection: dict[str, Any]
    ) -> RagOutcome:
        """analyze_existing_result: ranking per kecamatan. Hanya family ber-nilai."""
        if selection.get("family") not in ("dynamic", "simdasi"):
            return RagOutcome(kind="unavailable", text=abstention_text(NoDataReason.GEOGRAPHY_UNAVAILABLE))
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
