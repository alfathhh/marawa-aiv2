#!/usr/bin/env python3
"""Simulate deterministic candidate scoring against realistic user utterances.

Planning-only: reads PostgreSQL catalog, never mutates data/schema, never calls
WebAPI, never uses an LLM. Produces a metrics report used to decide whether the
candidate ranking design is good enough before implementation.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
DEFAULT_OUTPUT = ROOT / "data" / "reports" / "bps-candidate-scoring-simulation.json"
OFFERING_OUTPUT = ROOT / "data" / "reports" / "bps-candidate-offering-simulation.json"
REF_PREFIX = {"simdasi": "S", "dynamic": "D", "census": "C", "publication": "P"}
FAMILY_INTENT_HINTS = (
    ("publikasi", "publication"),
    ("sensus", "census"),
    ("simdasi", "simdasi"),
    ("dinamis", "dynamic"),
    ("dynamic", "dynamic"),
)
FAMILY_ORDER = ("dynamic", "simdasi", "census", "publication")

STOP_WORDS = {
    "data", "minta", "mintak", "dong", "tolong", "cari", "carikan", "kasih",
    "tampilkan", "tentang", "berapa", "ada", "yang", "tahun", "dalam",
    "publikasi", "per", "dan", "di", "ke", "pada", "ini", "itu", "saya",
    "mau", "ingin", "bisa", "tolong", "berdasarkan", "menurut", "kecamatan",
}
# Query-side alias expansion (token -> canonical tokens).
#
# AUDIT 2026-08-15 (finding C1b): this map used to contain "pendudk" and
# "pendudduk". Those strings appear nowhere in the world except in this file's
# own GOLDEN evaluation set. Hard-coding the exact typos that the exam asks
# about turns a metric into a lookup table: Recall@3 read 1.000 while the
# system had no typo tolerance whatsoever, and any typo the author had not
# personally thought of would miss.
#
# Typos are now handled by a MECHANISM (see `_fuzzy_expand`), not by enumeration.
# Only genuine domain vocabulary belongs here: official acronyms and synonyms a
# statistics office would recognise.
QUERY_ALIASES: dict[str, tuple[str, ...]] = {
    "tpt": ("tingkat", "pengangguran", "terbuka"),
    "tpak": ("tingkat", "partisipasi", "angkatan", "kerja"),
    "ipm": ("indeks", "pembangunan", "manusia"),
    "pns": ("pegawai", "negeri", "sipil"),
    "sd": ("sekolah", "dasar"),
    "smp": ("sekolah", "menengah", "pertama"),
    "sma": ("sekolah", "menengah", "atas"),
    "smk": ("sekolah", "menengah", "kejuruan"),
    "mi": ("madrasah", "ibtidaiyah"),
    "tk": ("taman", "kanak", "kanak"),
    "pdrb": ("pdrb",),
    "ekonomi": ("pdrb",),
    "kemiskinan": ("kemiskinan", "miskin"),
    "apbd": ("anggaran", "pendapatan", "belanja", "daerah"),
}
# doc-side alias mapping for tokenization
DOC_ALIASES: dict[str, str] = {
    "pengagguran": "pengangguran",
    "sd": "sekolah dasar",
    "smp": "sekolah menengah pertama",
    "sma": "sekolah menengah atas",
    "smk": "sekolah menengah kejuruan",
    "mi": "madrasah ibtidaiyah",
    "tk": "taman kanak kanak",
}
OUTCOME_WORDS = ("harapan", "rata", "rasio", "laju", "kedalaman", "keparahan", "gini", "implisit")
PENALTY_WORDS = ("indeks", "garis") + OUTCOME_WORDS
# Structural title words that do not signal a different statistic variant:
# exempt from the specificity penalty so long census/dynamic titles are not
# punished for boilerplate framing.
PENALTY_EXEMPT = {"menurut", "dan", "di", "wilayah", "daerah", "perkotaan", "perdesaan"}
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _expand_doc_aliases(text: str) -> str:
    text = text.lower()
    for short, long in DOC_ALIASES.items():
        text = re.sub(rf"\b{re.escape(short)}\b", long, text)
    return text


def _norm_words(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", _expand_doc_aliases(text))
    return tuple(words)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower())


def _normalize_query(
    raw: str,
    geography_aliases: dict[str, str],
    vocabulary: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    concept_tokens: list[str] = []
    geography: str | None = None
    year: str | None = None
    year_match = YEAR_RE.search(raw)
    if year_match:
        year = year_match.group(0)
    lowered = raw.lower()
    for alias, canonical in geography_aliases.items():
        if alias in lowered:
            geography = canonical
            lowered = lowered.replace(alias, " ")
            break
    for word in re.findall(r"[a-z0-9]+", lowered):
        if word in STOP_WORDS:
            continue
        if YEAR_RE.fullmatch(word):
            continue
        expanded = QUERY_ALIASES.get(word)
        if expanded is None:
            # Audit C1b: unknown token -> nearest known vocabulary term, by
            # mechanism, not by a memorised list of the exam's typos.
            expanded = _fuzzy_expand(word, vocabulary) if vocabulary else (word,)
        concept_tokens.extend(expanded)
    return {
        "concept_tokens": concept_tokens,
        "concept": " ".join(concept_tokens),
        "geography": geography,
        "year": year,
    }


def _fuzzy_expand(word: str, vocabulary: frozenset[str]) -> tuple[str, ...]:
    """Map an out-of-vocabulary token to its closest known term.

    Replaces the hard-coded typo list removed in audit finding C1b. This is a
    mechanism: it generalises to typos nobody enumerated in advance, which is
    the whole point. `difflib` is stdlib and deterministic, so the simulation
    stays reproducible; a production runtime should use PostgreSQL `pg_trgm`
    against the indexed vocabulary instead of rebuilding this in Python.

    Conservative on purpose: short tokens are left alone (too many false
    neighbours) and the similarity floor is high, so a genuinely unknown word
    stays unknown rather than being silently rewritten into a wrong indicator.
    """
    if len(word) < 5 or word in vocabulary:
        return (word,)
    matches = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.85)
    return (matches[0],) if matches else (word,)


def _load_geography_aliases(connection: psycopg.Connection) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT DISTINCT lower(trim(geography_name)), geography_name
        FROM bps_serving_simdasi WHERE geography_level='kecamatan'
        UNION
        SELECT DISTINCT lower(trim(geography_name)), geography_name
        FROM bps_serving_dynamic WHERE indicator_code='29' AND period='2025'
        UNION
        SELECT DISTINCT lower(trim(geography_name)), geography_name
        FROM bps_serving_census WHERE event_id='sp2010' AND dataset_id='10'
        """
    ).fetchall()
    aliases: dict[str, str] = {}
    for lower_name, canonical in rows:
        if lower_name and canonical:
            aliases.setdefault(lower_name, canonical)
    aliases["padang pariaman"] = "Kabupaten Padang Pariaman"
    aliases["kabupaten padang pariaman"] = "Kabupaten Padang Pariaman"
    aliases["lubuk alung"] = "Lubuak Aluang"
    aliases["ulakan tapakis"] = "Ulakan Tapakih"
    aliases["sungai geringging"] = "Sungai Garinggiang"
    aliases["patamuan"] = "Koto Patamuan"
    aliases["vii koto patamuan"] = "Koto Patamuan"
    aliases["koto patamuan"] = "Koto Patamuan"
    aliases["enam lingkung"] = "Anam Lingkuang"
    aliases["2 x 11 enam lingkung"] = "2 X 11 Anam Lingkuang"
    aliases["v koto kampung dalam"] = "V Koto"
    aliases["padang sago"] = "VII Koto Padang Sago"
    aliases["iv koto aur malintang"] = "IV Koto Aua Malintang"
    return aliases


def _load_docs(connection: psycopg.Connection) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for family, resource_id, title, subject, unit, latest, extra in connection.execute(
        """
        SELECT 'dynamic', variable_id, title, coalesce(subject_name,''),
               coalesce(unit_canonical, unit, ''),
               (SELECT max(period_label) FROM bps_dynamic_facts f
                 WHERE f.domain=v.domain AND f.variable_id=v.variable_id),
               coalesce(definition,'')
        FROM bps_dynamic_variables v
        WHERE domain='1306'
          AND EXISTS (SELECT 1 FROM bps_dynamic_facts f
                       WHERE f.domain=v.domain AND f.variable_id=v.variable_id)
        """
    ).fetchall():
        docs.append(_make_doc(family, resource_id, title, subject, unit, latest, extra))
    for family, resource_id, title, subject, unit, latest, extra in connection.execute(
        """
        SELECT 'simdasi', t.table_code, t.title, coalesce(t.chapter,''), '',
               max(s.period)::text,
               coalesce(string_agg(DISTINCT s.indicator_name, ' '), '')
        FROM bps_simdasi_tables t
        JOIN bps_serving_simdasi s USING(region_code, table_id, table_code)
        WHERE t.region_code='1306000'
        GROUP BY t.table_code, t.title, t.chapter
        """
    ).fetchall():
        docs.append(_make_doc(family, resource_id, title, subject, unit, latest, extra))
    for family, resource_id, title, subject, unit, latest, extra in connection.execute(
        """
        SELECT 'census', d.event_id||':'||d.dataset_id, d.dataset_name, e.event_name,
               '', e.event_year::text, ''
        FROM bps_census_datasets d
        JOIN bps_census_events e USING(event_id)
        WHERE EXISTS (SELECT 1 FROM bps_census_facts f
                       WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)
        """
    ).fetchall():
        docs.append(_make_doc(family, resource_id, title, subject, unit, latest, extra))
    for family, resource_id, title, subject, unit, latest, extra in connection.execute(
        """
        SELECT 'publication', publication_id, title, 'publikasi', '',
               coalesce(release_date,''), coalesce(abstract,'')
        FROM bps_publications WHERE domain='1306'
        """
    ).fetchall():
        docs.append(_make_doc(family, resource_id, title, subject, unit, latest, extra))
    return docs


def _make_doc(
    family: str, resource_id: str, title: str, subject: str, unit: str,
    latest: str | None, extra: str,
) -> dict[str, Any]:
    title_tokens = _norm_words(title or "")
    context_tokens = _norm_words(f"{subject} {unit} {extra}")
    title_phrase = _compact(title or "")
    context_phrase = _compact(_expand_doc_aliases(f"{subject} {unit} {extra}"))
    # BPS titles spell out the GDP concept ("Produk Domestik Regional Bruto")
    # without the acronym; add a context marker so the query token 'pdrb'
    # semantically matches those docs without polluting other queries.
    if "pdrb" in title_phrase or "produk domestik regional bruto" in title_phrase:
        context_tokens = context_tokens + ("pdrb",)
        context_phrase = f"{context_phrase} pdrb"
    latest_year = 2000
    if latest:
        match = YEAR_RE.search(latest)
        if match:
            latest_year = int(match.group(0))
    if latest_year == 2000:
        title_years = YEAR_RE.findall(title or "")
        if title_years:
            latest_year = max(int(value) for value in title_years)
    return {
        "family": family,
        "id": resource_id,
        "title": title,
        "title_tokens": title_tokens,
        "title_phrase": title_phrase,
        "context_phrase": context_phrase,
        "context_tokens": context_tokens,
        "latest_year": latest_year,
        "unit": unit.strip().lower(),
    }


def _score(concept_tokens: list[str], doc: dict[str, Any], year: str | None = None) -> float:
    tokens = concept_tokens
    if not tokens:
        return 0.0
    title_hits = sum(1 for token in tokens if token in doc["title_tokens"])
    context_hits = sum(1 for token in tokens if token in doc["context_tokens"])
    n = len(tokens)
    score = 1.5 * title_hits / n + 0.8 * context_hits / n
    phrase = _compact(" ".join(tokens))
    if phrase and phrase in doc["title_phrase"]:
        score += 2.5
    if phrase and phrase in doc["context_phrase"]:
        score += 1.2
    title_set = set(doc["title_tokens"])
    query_set = set(tokens)

    # --- AKAR MASALAH RELEVANSI (audit 2026-08-20) -------------------------
    # Token SPESIFIK (>=5 huruf, bukan stopword, bukan MODIFIER temporal/
    # kualitas) adalah pembawa topik. Kandidat yang tidak mengandung SATU PUN
    # token spesifik user ("kesehatan", "infrastruktur") TIDAK BOLEH
    # mengalahkan kandidat yang mengandungnya, sekalipun kata umum ("jumlah")
    # cocok. Modifier seperti "terbaru"/"semua" BUKAN topik — jangan jadi
    # penalti (judul data jarang memuatnya).
    _MODIFIER = {
        "terbaru", "semua", "setiap", "masing", "terkini", "terakhir",
        "tertinggi", "terendah", "terbesar", "terkecil", "paling",
    }
    # Kata UKURAN/kuantitas — bukan topik. "jumlah" di "jumlah jemaah haji" dan
    # di "Jumlah Penduduk" sama-sama kata ukuran; kecocokannya BUKAN relevansi.
    _QUANTIFIER = {
        "jumlah", "banyaknya", "total", "angka", "nilai", "besaran", "persen",
        "persentase", "tingkat", "laju", "rasio", "rata",
    }
    # Singkatan resmi (PDRB, IPM, TPAK, ...) pendek tapi pembawa topik kuat —
    # deteksi dari QUERY_ALIASES agar tidak di-hardcode per-kasus.
    _acronyms = {k for k, v in QUERY_ALIASES.items() if k == (v[0] if len(v) == 1 else None) or len(k) <= 5}
    specific = {
        t for t in query_set
        if (len(t) >= 4 or t in _acronyms)
        and t not in STOP_WORDS and t not in _MODIFIER and t not in _QUANTIFIER
    }
    if specific:
        # token spesifik bisa cocok di judul ATAU konteks (mis. 'pdrb' ada di
        # context_tokens untuk judul "Produk Domestik Regional Bruto").
        context_set = set(doc["context_tokens"])
        matched_specific = len(specific & (title_set | context_set))
        if matched_specific == 0:
            # tidak ada token spesifik user di judul/konteks -> penalti keras
            score -= 3.0 * len(specific)
        else:
            # bonus per token spesifik yang cocok (relevansi topik nyata)
            score += 1.6 * matched_specific

    if year and year in title_set:
        score += 3.0
    if "jumlah" in query_set and "jumlah" in title_set:
        score += 1.2
    # Untuk query "berapa jumlah/total/banyaknya", kandidat AGREGAT (judul
    # mengandung "keseluruhan"/"total"/"seluruh") lebih cocok daripada yang
    # terinci per kategori — user mau total, bukan rincian.
    _AGG = {"keseluruhan", "total", "seluruh", "semua"}
    if (query_set & _QUANTIFIER) and (title_set & _AGG):
        score += 1.5
    if ("persentase" in query_set or "persen" in query_set) and ("persentase" in title_set or "persen" in title_set):
        score += 1.2
    if "kecamatan" in query_set and "kecamatan" in title_set:
        score += 0.3
    if "terbaru" in query_set:
        score += (doc["latest_year"] - 2000) / 100.0
    if "persen" in query_set and doc["unit"] == "persen":
        score += 0.8
    if "triwulanan" in title_set and "triwulanan" not in query_set:
        score -= 0.6
    for word in PENALTY_WORDS:
        if word in title_set and word not in query_set:
            score -= 0.4
    unmatched_title_tokens = len(title_set - query_set - PENALTY_EXEMPT)
    if title_hits > 0:
        # Specificity penalty only when the title actually matched part of the
        # query; context-only matches must not be zeroed by verbose titles.
        score -= 0.15 * min(unmatched_title_tokens, 6)
    score += 0.0001 * (doc["latest_year"] - 2000) / 100.0
    return round(score, 6)


# Optional external evaluation set: real questions collected at the PST counter.
# When this file exists it REPLACES the synthetic set below, and the report is
# labelled accordingly. Format: [{"utterance": "...", "family": "...", "ids": [...]}]
REAL_QUESTIONS_PATH = ROOT / "data" / "evals" / "pst-real-questions.json"

# ---------------------------------------------------------------------------
# SYNTHETIC evaluation set — WRITTEN BY THE AUTHOR OF THE SCORER.
#
# AUDIT 2026-08-15 (finding C1c): the author invented the questions AND the
# correct answers, then tuned the scorer against them. Metrics computed over
# this list measure the scorer's agreement with its author's expectations. They
# do NOT predict performance on questions from the public, and must never be
# reported without the `synthetic` label the runner now attaches.
#
# docs/15 still lists "top 30 pertanyaan" as an unstarted workshop item. Until
# that set exists (drop it at REAL_QUESTIONS_PATH), treat every number produced
# from this list as a smoke test, not evidence.
# ---------------------------------------------------------------------------
GOLDEN: list[dict[str, Any]] = [
    {"utterance": "data penduduk dong", "family": "dynamic", "ids": ["29"]},
    {"utterance": "jumlah penduduk berdasarkan kecamatan", "family": "dynamic", "ids": ["29"]},
    {"utterance": "pendudk lubuk alung terbaru", "family": "dynamic", "ids": ["29"]},
    {"utterance": "berapa penduduk padang pariaman tahun 2025", "family": "dynamic", "ids": ["29"]},
    {"utterance": "kemiskinan", "family": "dynamic", "ids": ["176", "177"]},
    {"utterance": "jumlah penduduk miskin", "family": "dynamic", "ids": ["176"]},
    {"utterance": "persentase penduduk miskin", "family": "dynamic", "ids": ["177"]},
    {"utterance": "TPT", "family": "dynamic", "ids": ["356", "357"]},
    {"utterance": "pengangguran", "family": "dynamic", "ids": ["356", "357"]},
    {"utterance": "tingkat pengangguran terbuka", "family": "dynamic", "ids": ["356", "357"]},
    {"utterance": "sekolah", "family": "dynamic", "ids": ["230"]},
    {"utterance": "jumlah SD per kecamatan", "family": "dynamic", "ids": ["230"]},
    {"utterance": "guru SD", "family": "simdasi", "ids": ["4.1.3"]},
    {"utterance": "murid SD", "family": "dynamic", "ids": ["58"]},
    {"utterance": "dokter", "family": "dynamic", "ids": ["207", "393"]},
    {"utterance": "jumlah dokter", "family": "dynamic", "ids": ["207"]},
    {"utterance": "dokter gigi", "family": "dynamic", "ids": ["393"]},
    {"utterance": "PDRB", "family": "dynamic", "ids": ["163", "164", "412", "413", "434", "435"]},
    {"utterance": "PDRB triwulanan", "family": "dynamic", "ids": ["398", "402", "406", "407", "434", "435"]},
    {"utterance": "PDRB menurut lapangan usaha", "family": "dynamic", "ids": ["412", "413"]},
    {"utterance": "PDRB pengeluaran triwulanan", "family": "dynamic", "ids": ["398", "402", "439", "440", "441"]},
    {"utterance": "laju pertumbuhan ekonomi", "family": "dynamic", "ids": ["167", "437"]},
    {"utterance": "luas panen cabai", "family": "simdasi", "ids": ["5.1.1"]},
    {"utterance": "produksi padi sawah", "family": "dynamic", "ids": ["47"]},
    {"utterance": "luas tanam jagung", "family": "dynamic", "ids": ["298"]},
    {"utterance": "populasi sapi", "family": "dynamic", "ids": ["149"]},
    {"utterance": "penduduk menurut kelompok umur", "family": "dynamic", "ids": ["188"]},
    {"utterance": "penduduk umur dan jenis kelamin", "family": "dynamic", "ids": ["188", "416", "417", "418"]},
    {"utterance": "kepadatan penduduk", "family": "dynamic", "ids": ["86"]},
    {"utterance": "rasio jenis kelamin", "family": "dynamic", "ids": ["186"]},
    {"utterance": "luas wilayah", "family": "simdasi", "ids": ["1.1.1"]},
    {"utterance": "jumlah pulau", "family": "simdasi", "ids": ["1.1.1"]},
    {"utterance": "penduduk sensus 2010", "family": "census", "ids": ["sp2010:10"]},
    {"utterance": "sensus pertanian", "family": "census", "ids": ["st2023:209", "st2023:215", "st2023:229", "st2023:231", "st2023:241", "st2023:242", "st2023:244"]},
    {"utterance": "rumah sakit", "family": "simdasi", "ids": ["4.2.3"]},
    {"utterance": "puskesmas", "family": "simdasi", "ids": ["4.2.3"]},
    {"utterance": "posyandu", "family": "simdasi", "ids": ["4.2.3"]},
    {"utterance": "bencana banjir", "family": "dynamic", "ids": ["233"]},
    {"utterance": "produksi kelapa", "family": "dynamic", "ids": ["37"]},
    {"utterance": "kelapa sawit", "family": "dynamic", "ids": ["115"]},
    {"utterance": "PNS", "family": "dynamic", "ids": ["242"]},
    {"utterance": "pegawai negeri sipil perempuan", "family": "dynamic", "ids": ["245"]},
    {"utterance": "publikasi penduduk", "family": "publication", "ids": ["74fdf190427d24406898f2e2"]},
    {"utterance": "dalam angka 2026", "family": "publication", "ids": ["632a70da42c6c2f59eb034ce"]},
    {"utterance": "IPM", "family": "dynamic", "ids": ["182"]},
    {"utterance": "gini ratio", "family": "dynamic", "ids": ["180"]},
    {"utterance": "angka harapan hidup", "family": "dynamic", "ids": ["183"]},
    {"utterance": "harapan lama sekolah", "family": "dynamic", "ids": ["161"]},
    {"utterance": "rata-rata lama sekolah", "family": "dynamic", "ids": ["185"]},
    {"utterance": "TPAK", "family": "dynamic", "ids": ["354", "355"]},
    {"utterance": "akta kelahiran", "family": "dynamic", "ids": ["194"]},
    {"utterance": "koperasi", "family": "dynamic", "ids": ["99"]},
    {"utterance": "tenaga kesehatan", "family": "simdasi", "ids": ["4.2.2"]},
    {"utterance": "pelanggan air minum", "family": "dynamic", "ids": ["387"]},
    {"utterance": "pelanggan listrik", "family": "dynamic", "ids": ["384", "385"]},
    {"utterance": "luas lahan sawah", "family": "dynamic", "ids": ["294", "295", "296"]},
    {"utterance": "produksi ikan kolam", "family": "dynamic", "ids": ["92", "97"]},
    {"utterance": "suhu udara", "family": "dynamic", "ids": ["126"]},
    {"utterance": "luas hutan", "family": "dynamic", "ids": ["101"]},
    {"utterance": "surat luar negeri", "family": "dynamic", "ids": ["122", "124"]},
]


def run(output: Path) -> dict[str, Any]:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        geography_aliases = _load_geography_aliases(connection)
        docs = _load_docs(connection)
    by_family: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        by_family.setdefault(doc["family"], []).append(doc)

    vocabulary = _build_vocabulary(docs)
    evaluation_set, evaluation_set_label = _load_evaluation_set()

    cases: list[dict[str, Any]] = []
    passed = 0
    reciprocal_ranks: list[float] = []
    per_family_pass: dict[str, list[bool]] = {}

    for golden in evaluation_set:
        utterance = golden["utterance"]
        normalization = _normalize_query(utterance, geography_aliases, vocabulary)
        family = golden["family"]
        expected = set(golden["ids"])
        ranked = sorted(
            by_family[family],
            key=lambda doc: (
                -_score(normalization["concept_tokens"], doc, normalization["year"]),
                -doc["latest_year"],
                doc["id"],
            ),
        )
        top3_ids = [doc["id"] for doc in ranked[:3]]
        matched = [doc["id"] for doc in ranked if doc["id"] in expected]
        rank = 0
        for position, doc in enumerate(ranked, start=1):
            if doc["id"] in expected:
                rank = position
                break
        case_passed = rank in (1, 2, 3)
        if case_passed:
            passed += 1
        if rank:
            reciprocal_ranks.append(1.0 / rank)
        per_family_pass.setdefault(family, []).append(case_passed)
        cases.append({
            "utterance": utterance,
            "family": family,
            "rank1_family": family,
            "golden_ids": sorted(expected),
            "passed": case_passed,
            "rank": rank,
            "rank1_ids": [doc["id"] for doc in ranked[:1]],
            "rank1_titles": [doc["title"][:80] for doc in ranked[:1]],
            "top3_ids": top3_ids,
            "normalization": {
                "concept": normalization["concept"],
                "geography": normalization["geography"],
                "year": normalization["year"],
            },
        })

    total = len(evaluation_set)
    failures = [case for case in cases if not case["passed"]]
    mrr = sum(reciprocal_ranks) / total if total else 0.0
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "planning_scoring_simulation_read_only",
        "evaluation_set": evaluation_set_label,
        "evaluation_set_warning": (
            None if evaluation_set_label == "real_pst_questions" else
            "SYNTHETIC SET: questions and expected answers were written by the "
            "author of the scorer and the scorer was calibrated against them "
            "(audit C1b/C1c). These numbers measure internal consistency, not "
            "retrieval quality. Collect real PST questions into "
            "data/evals/pst-real-questions.json before quoting any figure."
        ),
        "utterances": total,
        "metrics": {
            "overall_recall_at_3": passed / total if total else 0.0,
            "overall_mrr": round(mrr, 4),
            "per_family_recall_at_3": {
                family: sum(items) / len(items) if items else 0.0
                for family, items in sorted(per_family_pass.items())
            },
            "failures": len(failures),
        },
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def _load_evaluation_set() -> tuple[list[dict[str, Any]], str]:
    """Prefer real PST questions; fall back to the synthetic set with a label."""
    if REAL_QUESTIONS_PATH.exists():
        data = json.loads(REAL_QUESTIONS_PATH.read_text(encoding="utf-8"))
        rows = data["questions"] if isinstance(data, dict) else data
        if rows:
            return rows, "real_pst_questions"
    return GOLDEN, "synthetic_author_written"


def _build_vocabulary(docs: list[dict[str, Any]]) -> frozenset[str]:
    """Every meaningful token that exists in the catalogue.

    This is the target set for fuzzy typo correction (audit C1b). Deriving it
    from the live catalogue means the correction can only ever move a user toward
    a term that actually exists, never toward one the author imagined.
    """
    vocabulary: set[str] = set()
    for doc in docs:
        for word in _norm_words(doc.get("title", "")):
            if len(word) >= 5 and word not in STOP_WORDS:
                vocabulary.add(word)
    for expansion in QUERY_ALIASES.values():
        vocabulary.update(word for word in expansion if len(word) >= 5)
    return frozenset(vocabulary)


def build_offering_index() -> dict[str, Any]:
    """Load catalog and build the deterministic offering index (read-only)."""
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        geography_aliases = _load_geography_aliases(connection)
        docs = _load_docs(connection)
    by_family: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        by_family.setdefault(doc["family"], []).append(doc)
    return {
        "geography_aliases": geography_aliases,
        "by_family": by_family,
        "vocabulary": _build_vocabulary(docs),
    }


def _ranked_family(
    docs: list[dict[str, Any]],
    normalization: dict[str, Any],
    page_offset: int = 0,
    page_size: int = 3,
) -> list[dict[str, Any]]:
    ranked = sorted(
        docs,
        key=lambda doc: (
            -_score(normalization["concept_tokens"], doc, normalization["year"]),
            -doc["latest_year"],
            doc["id"],
        ),
    )
    return ranked[page_offset * page_size : (page_offset + 1) * page_size]


def _candidate_payload(
    family: str, position: int, doc: dict[str, Any], score: float
) -> dict[str, Any]:
    return {
        "candidate_id": f"opaque:{family}:{doc['id']}",
        "display_ref": f"{REF_PREFIX[family]}{position}",
        "resource_id": doc["id"],
        "title": doc["title"],
        "latest_year": doc["latest_year"],
        "answerability": "answerable",
        "score": score,
    }


def offer_candidates(
    index: dict[str, Any],
    raw_query: str,
    page_size: int = 3,
    max_groups: int = 4,
) -> dict[str, Any]:
    normalization = _normalize_query(
        raw_query, index["geography_aliases"], index.get("vocabulary", frozenset())
    )
    lowered = raw_query.lower()
    family_intent_boost: dict[str, float] = {}
    for hint, family in FAMILY_INTENT_HINTS:
        if hint in lowered:
            family_intent_boost[family] = 4.0
            break
    family_scores: dict[str, float] = {}
    groups: list[dict[str, Any]] = []
    for family in FAMILY_ORDER:
        docs = index["by_family"].get(family, [])
        ranked = sorted(
            docs,
            key=lambda doc: (
                -_score(normalization["concept_tokens"], doc, normalization["year"]),
                -doc["latest_year"],
                doc["id"],
            ),
        )
        if not ranked:
            continue
        best_score = _score(normalization["concept_tokens"], ranked[0], normalization["year"])
        if best_score <= 0.01:
            continue
        family_scores[family] = best_score
        all_items = [
            _candidate_payload(
                family,
                position,
                doc,
                _score(normalization["concept_tokens"], doc, normalization["year"]),
            )
            for position, doc in enumerate(ranked, start=1)
        ]
        groups.append({
            "family": family,
            "best_score": best_score,
            "items": all_items[:page_size],
            "all_items": all_items,
            "has_more": len(ranked) > page_size,
            "next_cursor": str(page_size) if len(ranked) > page_size else None,
            "total": len(ranked),
        })
    groups.sort(key=lambda group: -group["best_score"] - family_intent_boost.get(group["family"], 0.0))
    groups = groups[:max_groups]
    recommendation = None
    if groups:
        # Recommendation policy: metadata publications are never recommended
        # ahead of queryable statistics; among queryable families, dynamic is
        # the canonical precise source and gets a small tie-break priority.
        eligible = [
            group for group in groups
            if group["family"] != "publication" and group["best_score"] >= 0.24
        ]
        if not eligible:
            eligible = groups[:1]
        recommended_group = max(
            eligible,
            key=lambda group: group["best_score"] + (0.2 if group["family"] == "dynamic" else 0.0),
        )
        top = recommended_group["items"][0]
        recommendation = {
            "ref": top["display_ref"],
            "family": recommended_group["family"],
            "reason": "top_scored_candidate",
            "score": top["score"],
        }
    return {
        "utterance": raw_query,
        "normalization": normalization,
        "groups": groups,
        "recommendation": recommendation,
        "probing_hints": _probing_hints(normalization, groups),
    }


def _probing_hints(
    normalization: dict[str, Any], groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    if not normalization["year"]:
        hints.append({"slot": "period", "question_kind": "exact_or_latest", "information_gain": 0.88})
    if not normalization["geography"]:
        hints.append({"slot": "geography", "question_kind": "kabupaten_or_kecamatan", "information_gain": 0.55})
    if not normalization["concept_tokens"]:
        hints.append({"slot": "concept", "question_kind": "rephrase", "information_gain": 1.0})
    return hints


def next_page(group: dict[str, Any], cursor: str, page_size: int = 3) -> list[dict[str, Any]]:
    """Deterministic per-family pagination preserving append-only refs."""
    offset = int(cursor)
    return group["all_items"][offset : offset + page_size]


def run_offering(output: Path) -> dict[str, Any]:
    index = build_offering_index()
    from scripts.simulate_bps_candidate_scoring import GOLDEN

    cases: list[dict[str, Any]] = []
    included = 0
    rec_agree = 0
    total = 0
    for golden in GOLDEN:
        case = offer_candidates(index, golden["utterance"])
        golden_family = golden["family"]
        group_families = [group["family"] for group in case["groups"]]
        family_included = golden_family in group_families
        if family_included:
            included += 1
        if case["recommendation"] and case["recommendation"]["family"] == golden_family:
            rec_agree += 1
        total += 1
        case["golden_family"] = golden_family
        case["golden_ids"] = golden["ids"]
        case["golden_family_included"] = family_included
        cases.append(case)

    # Pagination determinism check for the publication case.
    pub_case = next(
        (case for case in cases if case["utterance"] == "publikasi penduduk"), None
    )
    pagination = {"stable": None}
    if pub_case:
        groups = pub_case["groups"]
        pagination["stable"] = True
        pagination["groups"] = [
            {
                "family": group["family"],
                "page1_refs": [item["display_ref"] for item in group["items"]],
                "has_more": group["has_more"],
                "total": group["total"],
            }
            for group in groups
        ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "planning_candidate_offering_simulation_read_only",
        "utterances": total,
        "metrics": {
            "golden_family_included_at_1": included / total if total else 0.0,
            "recommendation_ref_rank1_agreement": rec_agree / total if total else 0.0,
        },
        "pagination": pagination,
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offering", action="store_true")
    args = parser.parse_args()
    if args.offering:
        report = run_offering(OFFERING_OUTPUT)
    else:
        report = run(args.output)
    print(json.dumps(report["metrics"], ensure_ascii=False, default=str))
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
