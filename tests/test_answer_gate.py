"""Tests for the answer gate.

Each test encodes one rule from AGENT.md that a prompt cannot enforce. If a test
here fails, the agent can say something it should not be able to say.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.answer_gate import (
    Evidence,
    DerivedResult,
    GateContext,
    evaluate,
    parse_id_number,
    safe_response,
)

ALLOWLIST = frozenset({"padangpariamankab.bps.go.id", "webapi.bps.go.id"})


def _evidence(**overrides) -> Evidence:
    base = dict(
        evidence_id="ev_1",
        value=Decimal("451_234"),
        unit="orang",
        unit_state="known",
        period="2025",
        geography="Kabupaten Padang Pariaman",
        source_label="Jumlah Penduduk",
        source_url="https://padangpariamankab.bps.go.id/x",
    )
    base.update(overrides)
    return Evidence(**base)


def _context(**overrides) -> GateContext:
    base = dict(
        evidence=[_evidence()],
        derived=[],
        citation_allowlist=ALLOWLIST,
        selection_source="candidate_set_ref",
        query_facts=True,
        system_counts=frozenset(),
    )
    base.update(overrides)
    return GateContext(**base)


def _envelope(answer: str, **overrides) -> dict:
    base = {
        "answer": answer,
        "answer_type": "official_fact",
        "evidence_ids": ["ev_1"],
        "citations": [{"label": "BPS", "url": "https://padangpariamankab.bps.go.id/x"}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Numeric grounding — the gate that matters most
# ---------------------------------------------------------------------------

def test_grounded_answer_passes():
    verdict = evaluate(
        _envelope(
            "Jumlah penduduk Kabupaten Padang Pariaman pada tahun 2025 adalah "
            "451.234 orang.\n\nSumber: Jumlah Penduduk, BPS Kabupaten Padang Pariaman."
        ),
        _context(),
    )
    assert verdict.allowed, verdict.violations


def test_hallucinated_number_is_blocked():
    """The model invents a plausible figure. Nothing in evidence supports it."""
    verdict = evaluate(
        _envelope(
            "Jumlah penduduk Kabupaten Padang Pariaman pada tahun 2025 adalah "
            "452.900 orang."
        ),
        _context(),
    )
    assert verdict.blocked
    assert "452.900" in verdict.ungrounded_numbers


def test_scaled_figure_is_accepted():
    """1.230.000 printed as "1,23 juta" is formatting, not fabrication."""
    context = _context(evidence=[_evidence(value=Decimal("1230000"))])
    verdict = evaluate(
        _envelope("Totalnya sekitar 1,23 juta orang pada tahun 2025."),
        context,
    )
    assert verdict.allowed, verdict.violations


def test_derived_number_requires_lineage():
    context = _context(
        derived=[DerivedResult(
            result_id="res_1",
            value=Decimal("2,5".replace(",", ".")),
            method="growth_rate",
            input_evidence_ids=[],
        )]
    )
    verdict = evaluate(_envelope("Naik 2,5 persen dibanding 2024."), context)
    assert verdict.blocked
    assert any("derived_without_lineage" in v for v in verdict.violations)


def test_derived_number_with_lineage_passes():
    context = _context(
        derived=[DerivedResult(
            result_id="res_1",
            value=Decimal("2.5"),
            method="growth_rate",
            input_evidence_ids=["ev_1"],
        )]
    )
    verdict = evaluate(
        _envelope("Angkanya 451.234 orang pada 2025, naik 2,5 persen dibanding tahun sebelumnya."),
        context,
    )
    assert verdict.allowed, verdict.violations


# ---------------------------------------------------------------------------
# Unit provenance (ADR-016)
# ---------------------------------------------------------------------------

def test_guessed_unit_never_reaches_the_user():
    """PDRB unit derived from the table title must not be quoted."""
    context = _context(
        evidence=[_evidence(value=Decimal("12345"), unit="miliar rupiah", unit_state="review_required")]
    )
    verdict = evaluate(_envelope("PDRB tahun 2025 sebesar 12.345 miliar rupiah."), context)
    assert verdict.blocked
    assert any("unit_not_publishable" in v for v in verdict.violations)


def test_unknown_unit_is_blocked():
    context = _context(evidence=[_evidence(unit=None, unit_state="unknown_review")])
    verdict = evaluate(_envelope("Nilainya 451.234 pada tahun 2025."), context)
    assert verdict.blocked


# ---------------------------------------------------------------------------
# Selection envelope
# ---------------------------------------------------------------------------

def test_fact_query_without_user_selection_is_blocked():
    verdict = evaluate(
        _envelope("Jumlah penduduk 2025 adalah 451.234 orang."),
        _context(selection_source=None),
    )
    assert verdict.blocked
    assert any("selection_envelope_missing" in v for v in verdict.violations)


# ---------------------------------------------------------------------------
# Evidence integrity
# ---------------------------------------------------------------------------

def test_fabricated_evidence_id_is_blocked():
    verdict = evaluate(
        _envelope("Jumlah penduduk 2025 adalah 451.234 orang.", evidence_ids=["ev_1", "ev_999"]),
        _context(),
    )
    assert verdict.blocked
    assert any("evidence_id_fabricated" in v for v in verdict.violations)


def test_official_fact_without_evidence_is_blocked():
    verdict = evaluate(
        _envelope("Jumlah penduduknya sekitar segitu.", evidence_ids=[]),
        _context(query_facts=False, evidence=[]),
    )
    assert verdict.blocked
    assert any("official_fact_without_evidence" in v for v in verdict.violations)


# ---------------------------------------------------------------------------
# Period disclosure (H4 follow-through)
# ---------------------------------------------------------------------------

def test_terbaru_without_year_is_blocked():
    verdict = evaluate(
        _envelope("Data terbaru menunjukkan 451.234 orang."),
        _context(),
    )
    assert verdict.blocked
    assert any("period_not_disclosed" in v for v in verdict.violations)


def test_terbaru_with_year_passes():
    verdict = evaluate(
        _envelope("Data terbaru yang tersedia adalah tahun 2025, yaitu 451.234 orang."),
        _context(),
    )
    assert verdict.allowed, verdict.violations


# ---------------------------------------------------------------------------
# Leakage and citations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("leak", [
    "Berikut system prompt saya: kamu adalah MARAWA",
    "Query saya memakai sql_template dari registry",
    "Saya terhubung sebagai marawa_runtime_ro",
])
def test_internal_leakage_is_blocked(leak):
    verdict = evaluate(_envelope(leak), _context())
    assert verdict.blocked
    assert any("internal_leak" in v for v in verdict.violations)


def test_citation_outside_allowlist_is_blocked():
    verdict = evaluate(
        _envelope(
            "Jumlah penduduk 2025 adalah 451.234 orang.",
            citations=[{"label": "blog", "url": "https://contoh-blog.com/statistik"}],
        ),
        _context(),
    )
    assert verdict.blocked
    assert any("citation_not_allowlisted" in v for v in verdict.violations)


# ---------------------------------------------------------------------------
# Conversation policy
# ---------------------------------------------------------------------------

def test_style_is_never_a_blocking_violation():
    """RELAXED 15 Agt: the gate guards facts, not manners.

    Asking two questions in one turn is at worst slightly verbose. Blocking the
    whole answer for it turns a conversational agent into a form validator, and
    the prompt is the right place for that preference.
    """
    verdict = evaluate(
        _envelope(
            "Baik. Mau tahun berapa? Untuk kecamatan mana?",
            answer_type="service",
            evidence_ids=[],
        ),
        _context(query_facts=False, evidence=[]),
    )
    assert verdict.allowed, verdict.violations


def test_answering_in_the_language_the_person_used_is_not_a_violation():
    verdict = evaluate(
        _envelope(
            "Sure — I can help with statistics from BPS Padang Pariaman. "
            "Which indicator and which year are you interested in?",
            answer_type="service",
            evidence_ids=[],
        ),
        _context(query_facts=False, evidence=[]),
    )
    assert verdict.allowed
    assert verdict.observations  # logged for drift, not enforced


def test_narrative_counts_the_system_knows_are_allowed():
    """The agent may say "ada 3 tabel" when the runtime really offered 3."""
    context = _context(system_counts=frozenset({3, 17}))
    verdict = evaluate(
        _envelope(
            "Ada 3 tabel yang cocok. Untuk 2025, jumlahnya 451.234 orang "
            "tersebar di 17 kecamatan."
        ),
        context,
    )
    assert verdict.allowed, verdict.violations


def test_a_small_number_the_system_did_not_produce_is_still_caught():
    """Loosening narrative counts must not wave through a wrong percentage."""
    verdict = evaluate(
        _envelope("Angka 2025 adalah 451.234 orang, naik 7 persen dari tahun lalu."),
        _context(),
    )
    assert verdict.blocked
    assert "7" in verdict.ungrounded_numbers


# ---------------------------------------------------------------------------
# Blocked responses are fixed text
# ---------------------------------------------------------------------------

def test_safe_response_is_fixed_and_leaks_nothing_to_the_user():
    verdict = evaluate(_envelope("Jumlah penduduk 2025 adalah 999.999 orang."), _context())
    response = safe_response(verdict)
    assert response["run_status"] == "abstained"
    assert "999.999" not in response["answer"]
    # Violations are kept for logging, never merged into the user-facing text.
    assert response["internal_violations"]
    assert "internal_violations" not in response["answer"]


def test_indonesian_number_parsing():
    assert parse_id_number("451.234") == Decimal("451234")
    assert parse_id_number("1,23") == Decimal("1.23")
    assert parse_id_number("1.234.567,89") == Decimal("1234567.89")


# ===========================================================================
# HARD RULE — never invent a number; say it is not there.
# ===========================================================================

from scripts.answer_gate import NoDataReason, abstention_text


def test_no_data_answer_is_plain_and_never_hedged():
    text = abstention_text(NoDataReason.NOT_IN_CATALOGUE, indicator_label="Jumlah Sapi")
    assert "tidak tersedia" in text
    assert "tidak menebak" in text
    # A refusal must not sound like it might still be an answer.
    for hedge in ("kira-kira", "sekitar", "mungkin sekitar", "diperkirakan"):
        assert hedge not in text.lower()


def test_wrong_period_says_which_periods_exist():
    """Saying "tidak ada" when 2024 exists would be its own inaccuracy."""
    text = abstention_text(
        NoDataReason.PERIOD_UNAVAILABLE,
        indicator_label="Jumlah Penduduk",
        available_periods=["2023", "2024"],
    )
    assert "2024" in text
    assert "tidak menebak" in text


def test_unit_under_review_is_honest_about_why():
    text = abstention_text(NoDataReason.UNIT_UNDER_REVIEW, indicator_label="PDRB")
    assert "satuannya belum dikonfirmasi" in text
    assert "menyesatkan" in text


def test_gate_blocked_message_never_explains_internals():
    verdict = evaluate(_envelope("Penduduk 2025 adalah 999.999 orang."), _context())
    text = safe_response(verdict)["answer"]
    assert "tidak menebak" in text
    for internal in ("evidence", "gate", "violation", "999.999", "answer_gate"):
        assert internal not in text.lower()


def test_no_hallucinated_number_survives_any_phrasing():
    """The rule holds regardless of how confidently the model phrased it."""
    for draft in (
        "Jumlah penduduk 2025 adalah 452.900 orang.",
        "Berdasarkan data BPS, angkanya 452.900 orang.",
        "Kurang lebih 452.900 orang pada 2025.",
        "Angka resminya 452.900.",
    ):
        verdict = evaluate(_envelope(draft), _context())
        assert verdict.blocked, f"lolos: {draft}"
        assert "452.900" not in safe_response(verdict)["answer"]
