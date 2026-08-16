#!/usr/bin/env python3
"""Answer gate — the layer that actually makes the agent obey.

WHY THIS FILE EXISTS
--------------------
A system prompt cannot enforce anything. It biases a probability distribution.
Every rule in AGENT.md that matters ("jangan sebut angka tanpa evidence", "unit
tidak ditebak", "jangan query sebelum user pilih tabel") is enforced HERE, on
the server, after the model has spoken and before the user sees anything.

The model proposes. This module disposes.

Design consequence worth internalising: a run where the model drafts a
hallucinated number and this gate blocks it is a SUCCESS, not a failure. The
metric that matters is what gets SENT, not what gets drafted. Track both
separately (draft violation rate vs delivered violation rate) — if you only
track delivered, you cannot tell whether the model got better or the gate got
lucky.

WHAT BELONGS HERE, AND WHAT DOES NOT
------------------------------------
Every proposed gate must pass one question:

    Does this catch a wrong FACT, or merely an unusual STYLE?

Facts belong here. Style belongs in the prompt. A gate that blocks an answer for
asking two questions, or for choosing an unexpected wording, does not make
MARAWA safer — it makes it sound like a form validator, and it silently converts
an agent into a rigid bot one rule at a time.

Relaxed on 15 Aug for exactly that reason: question-count and language checks
were removed as blockers (they were style), and the arbitrary "small integers
are fine" rule was replaced by `system_counts`, which is both looser for natural
phrasing and stricter for real statistics.

Pure functions, no DB, no network, no LLM. Deterministic and unit-testable.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# Unit states a measure may carry into a public answer. Anything else means the
# unit was guessed or unknown, and per ADR-016 must never reach a user.
PUBLISHABLE_UNIT_STATES = frozenset({"known", "unitless", "canonical"})

# Words that legitimately scale a printed figure ("1,23 juta orang").
SCALE_WORDS: dict[str, Decimal] = {
    "ribu": Decimal(1_000),
    "juta": Decimal(1_000_000),
    "miliar": Decimal(1_000_000_000),
    "triliun": Decimal(1_000_000_000_000),
}

# Indonesian number format: 1.234.567,89 — dot groups thousands, comma decimals.
#
# BUG FIX 15 Aug (found by test_no_hallucinated_number_survives_any_phrasing):
# the previous pattern ended with a `(?![\w.,])` lookahead, so a figure closing
# a sentence — "Angka resminya 452.900." — matched nothing at all and was
# INVISIBLE to the grounding check. A fabricated number in the most natural
# sentence position in Indonesian sailed straight through the gate.
#
# The grouped alternative now requires at least one full 3-digit group, which
# separates a thousands separator from a full stop, and the trailing lookahead
# only excludes a digit.
NUMBER_RE = re.compile(
    r"(?<![\d.,])-?\d{1,3}(?:\.\d{3})+(?:,\d+)?(?!\d)"
    r"|(?<![\d.,])-?\d+(?:,\d+)?(?!\d)"
)

# Strings that must never appear in outbound text, in any casing.
LEAK_MARKERS = (
    "system prompt",
    "system_prompt",
    "sql_template",
    "parameter_schema",
    "marawa_runtime_ro",
    "postgres.env",
    "bps_raw_snapshots",
    "instruksi tersembunyi",
    "hidden instruction",
)

# A public answer is Indonesian. Cheap positive signal, not a language model.
INDONESIAN_MARKERS = (
    " yang ", " dan ", " di ", " dari ", " untuk ", " pada ", " adalah ",
    " tahun ", " data ", " sebesar ", " menurut ", " tidak ",
)


@dataclass
class Evidence:
    """One value returned by a tool, with the provenance the gate needs."""

    evidence_id: str
    value: Decimal | None
    unit: str | None
    unit_state: str
    period: str | None
    geography: str | None
    source_label: str
    source_url: str | None = None


@dataclass
class DerivedResult:
    """A number the agent computed rather than read."""

    result_id: str
    value: Decimal
    method: str
    input_evidence_ids: list[str]


@dataclass
class GateContext:
    """Everything the gate is allowed to consider true."""

    evidence: list[Evidence] = field(default_factory=list)
    derived: list[DerivedResult] = field(default_factory=list)
    citation_allowlist: frozenset[str] = frozenset()
    selection_source: str | None = None
    query_facts: bool = False
    # Counts the RUNTIME knows to be true: how many candidates were offered, how
    # many rows came back, how many kecamatan are in the result. Populating this
    # lets the agent write naturally ("ada 3 tabel yang cocok", "dari 17
    # kecamatan") without the gate treating narrative counts as fabricated
    # statistics. Replaces an arbitrary "integers 0-10 are fine" rule that both
    # blocked natural phrasing AND would have waved through a wrong percentage.
    system_counts: frozenset[int] = frozenset()


@dataclass
class GateVerdict:
    allowed: bool
    violations: list[str]
    observations: list[str] = field(default_factory=list)
    # Numbers that could not be traced. Surfaced for the repair prompt and for
    # the metric that actually matters.
    ungrounded_numbers: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return not self.allowed


# ---------------------------------------------------------------------------
# Number handling
# ---------------------------------------------------------------------------

def parse_id_number(token: str) -> Decimal | None:
    """Parse an Indonesian-formatted numeric token."""
    cleaned = token.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _scaled_variants(value: Decimal, answer: str) -> set[Decimal]:
    """A figure may legitimately be printed scaled: 1_230_000 -> "1,23 juta".

    A scale variant is admitted ONLY when its scale word actually appears in the
    answer. Without that condition the gate would silently admit 451 as a valid
    rendering of 451.234 (via the "ribu" divisor), which is exactly the kind of
    quiet looseness that lets a wrong number through.
    """
    variants = {value}
    lowered = answer.lower()
    for word, factor in SCALE_WORDS.items():
        if word in lowered:
            variants.add(value / factor)
    return variants


def _rounding_variants(value: Decimal) -> set[Decimal]:
    """Allow the formatter to round to 0-2 decimals."""
    variants: set[Decimal] = set()
    for places in (0, 1, 2):
        quant = Decimal(1).scaleb(-places)
        try:
            variants.add(value.quantize(quant))
        except InvalidOperation:
            continue
    return variants


def _allowed_numbers(context: GateContext, answer: str) -> set[Decimal]:
    """Every numeric value the answer is permitted to contain."""
    allowed: set[Decimal] = set()

    def admit(value: Decimal) -> None:
        for scaled in _scaled_variants(value, answer):
            allowed.update(_rounding_variants(scaled))
            allowed.add(scaled)

    for item in context.evidence:
        if item.value is not None:
            admit(item.value)
        # Periods are facts about the evidence and may be printed as-is. This is
        # also what makes "terbaru" answerable honestly: the year must be shown.
        if item.period:
            for match in NUMBER_RE.findall(item.period):
                parsed = parse_id_number(match)
                if parsed is not None:
                    allowed.add(parsed)
    for result in context.derived:
        admit(result.value)
    for count in context.system_counts:
        allowed.add(Decimal(count))
    return allowed


def check_numeric_grounding(answer: str, context: GateContext) -> tuple[list[str], list[str]]:
    """Every number in the answer must trace to evidence or a derived result.

    This is the single most valuable gate in the system. The dominant risk for a
    statistics office is not jailbreak; it is a plausible wrong number leaving
    with BPS's name attached.
    """
    allowed = _allowed_numbers(context, answer)
    violations: list[str] = []
    ungrounded: list[str] = []

    for token in NUMBER_RE.findall(answer):
        parsed = parse_id_number(token)
        if parsed is None:
            continue
        if parsed in allowed:
            continue
        ungrounded.append(token)

    if ungrounded:
        violations.append(
            "numeric_not_grounded: "
            + ", ".join(sorted(set(ungrounded)))
            + " — tidak ada evidence/derived result yang memuat angka ini"
        )
    return violations, ungrounded


# ---------------------------------------------------------------------------
# The remaining gates
# ---------------------------------------------------------------------------

def check_unit_publishable(context: GateContext) -> list[str]:
    """ADR-016: a guessed unit is not a unit and never reaches a user."""
    violations = []
    for item in context.evidence:
        if item.unit_state not in PUBLISHABLE_UNIT_STATES:
            violations.append(
                f"unit_not_publishable: evidence {item.evidence_id} has "
                f"unit_state={item.unit_state!r}; measure needs data-owner review "
                "before it can be quoted (docs/26)"
            )
    return violations


def check_selection_envelope(context: GateContext) -> list[str]:
    """No fact query without an explicit user selection of the table."""
    if not context.query_facts:
        return []
    if context.selection_source in {"candidate_set_ref", "explicit_ref", "active_dataset"}:
        return []
    return [
        "selection_envelope_missing: fact query ran without a recorded user "
        f"selection (selection_source={context.selection_source!r})"
    ]


def check_citations(citations: Iterable[dict[str, Any]], context: GateContext) -> list[str]:
    violations = []
    for citation in citations:
        url = (citation or {}).get("url")
        if not url:
            continue
        host = re.sub(r"^https?://", "", url).split("/", 1)[0].lower()
        if host not in context.citation_allowlist:
            violations.append(f"citation_not_allowlisted: {host}")
    return violations


def check_evidence_declared(answer: str, envelope: dict[str, Any], context: GateContext) -> list[str]:
    """A factual answer must declare the evidence it used."""
    declared = set(envelope.get("evidence_ids") or [])
    known = {item.evidence_id for item in context.evidence}
    unknown = declared - known
    violations = []
    if unknown:
        violations.append(
            "evidence_id_fabricated: " + ", ".join(sorted(unknown))
            + " — id tidak ada di tool result run ini"
        )
    if envelope.get("answer_type") == "official_fact" and not declared:
        violations.append("official_fact_without_evidence: answer_type klaim fakta resmi tanpa evidence_ids")
    for result in context.derived:
        if not result.input_evidence_ids:
            violations.append(f"derived_without_lineage: {result.result_id} tidak menunjuk input evidence")
    return violations


def check_period_disclosed(answer: str, context: GateContext) -> list[str]:
    """"Terbaru" is not a period. The year must be printed."""
    lowered = answer.lower()
    if not any(word in lowered for word in ("terbaru", "terakhir", "saat ini")):
        return []
    periods = {item.period for item in context.evidence if item.period}
    if not periods:
        return []
    if any(period and period in answer for period in periods):
        return []
    return [
        "period_not_disclosed: jawaban memakai kata 'terbaru' tanpa menyebut "
        f"periode sebenarnya ({', '.join(sorted(p for p in periods if p))})"
    ]


def check_no_leakage(answer: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", answer).lower()
    return [
        f"internal_leak: {marker!r} muncul di teks publik"
        for marker in LEAK_MARKERS
        if marker in normalized
    ]


def observe_language(answer: str) -> list[str]:
    """Signal only — never blocks.

    RELAXED 15 Aug. This used to block any answer that failed a marker
    heuristic. That is wrong twice over: the heuristic is crude, and replying in
    the language the person actually wrote in is GOOD agent behaviour, not a
    violation. If someone writes in Minang or English, matching them is the
    right call. Logged so drift is visible; never blocks.
    """
    if len(answer) < 40:
        return []
    padded = f" {answer.lower()} "
    if any(marker in padded for marker in INDONESIAN_MARKERS):
        return []
    return ["observation: jawaban tampaknya bukan Bahasa Indonesia"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate(envelope: dict[str, Any], context: GateContext) -> GateVerdict:
    """Run every gate. Order is deliberate: cheapest and most severe first."""
    answer = envelope.get("answer") or ""
    violations: list[str] = []

    violations += check_no_leakage(answer)
    violations += check_selection_envelope(context)
    violations += check_unit_publishable(context)
    violations += check_evidence_declared(answer, envelope, context)

    numeric_violations, ungrounded = check_numeric_grounding(answer, context)
    violations += numeric_violations

    violations += check_period_disclosed(answer, context)
    violations += check_citations(envelope.get("citations") or [], context)

    # Observations are logged, not enforced. Keep this separation strict: the
    # moment a style preference is added to `violations`, the agent starts
    # sounding like a form validator.
    observations = observe_language(answer)

    return GateVerdict(
        allowed=not violations,
        violations=violations,
        ungrounded_numbers=ungrounded,
        observations=observations,
    )


# ---------------------------------------------------------------------------
# Saying "tidak ada" — the hard rule
# ---------------------------------------------------------------------------
#
# HARD RULE: MARAWA NEVER INVENTS A NUMBER. When the data is not there, it says
# so plainly. There is no situation in which guessing is preferable to "tidak
# ada" — a wrong figure carrying the BPS name is worse than no figure.
#
# But "tidak ada" must itself be accurate. Saying "data tidak tersedia" when the
# data exists for a different year is a smaller lie in service of a bigger
# truth, and this system does not trade in either. So the refusal states WHICH
# kind of unavailable it is, and where the data does exist, it says so.


class NoDataReason(str, Enum):
    NOT_IN_CATALOGUE = "not_in_catalogue"     # BPS mirror has no such indicator
    PERIOD_UNAVAILABLE = "period_unavailable"  # exists, other period(s) only
    GEOGRAPHY_UNAVAILABLE = "geography_unavailable"
    UNIT_UNDER_REVIEW = "unit_under_review"    # exists, unit not yet approved
    GATE_BLOCKED = "gate_blocked"              # internal check failed
    UNCLEAR_QUESTION = "unclear_question"


_ADMIN_TAIL = "\n\nBalas *ADMIN* kalau ingin dibantu petugas PST."


def abstention_text(
    reason: NoDataReason,
    *,
    available_periods: list[str] | None = None,
    available_geographies: list[str] | None = None,
    indicator_label: str | None = None,
) -> str:
    """Say plainly that the number is not available, and be specific about why.

    Never softened into a maybe. Never padded until it sounds like an answer.
    """
    subject = f"Data {indicator_label}" if indicator_label else "Data yang Anda tanyakan"

    if reason is NoDataReason.NOT_IN_CATALOGUE:
        return (
            f"{subject} tidak tersedia di data BPS Kabupaten Padang Pariaman "
            "yang saya miliki. Saya tidak menebak angka." + _ADMIN_TAIL
        )

    if reason is NoDataReason.PERIOD_UNAVAILABLE:
        body = f"{subject} untuk periode itu tidak tersedia."
        if available_periods:
            listed = ", ".join(available_periods[:5])
            body += f" Yang tersedia: {listed}."
        body += " Saya tidak menebak angka untuk periode yang datanya belum ada."
        return body + _ADMIN_TAIL

    if reason is NoDataReason.GEOGRAPHY_UNAVAILABLE:
        body = f"{subject} tidak tersedia untuk wilayah itu."
        if available_geographies:
            listed = ", ".join(available_geographies[:5])
            body += f" Tersedia untuk: {listed}."
        return body + _ADMIN_TAIL

    if reason is NoDataReason.UNIT_UNDER_REVIEW:
        return (
            f"{subject} ada di basis data, tetapi satuannya belum dikonfirmasi "
            "sehingga belum bisa saya sampaikan. Menyampaikan angka tanpa satuan "
            "yang pasti berisiko menyesatkan." + _ADMIN_TAIL
        )

    if reason is NoDataReason.UNCLEAR_QUESTION:
        return "Boleh diperjelas sedikit maksud pertanyaannya?"

    # GATE_BLOCKED: internal failure. Never explain internals to a citizen, and
    # never let the blocked draft leak into the replacement.
    return (
        "Maaf, saya belum bisa memastikan angkanya, jadi tidak saya sampaikan. "
        "Saya tidak menebak." + _ADMIN_TAIL
    )


def safe_response(verdict: GateVerdict) -> dict[str, Any]:
    """What is sent when the gate blocks.

    Fixed text on purpose. A model-written apology would be another chance to
    leak or hallucinate, and varying the refusal gives an attacker an oracle for
    which probe tripped which detector.
    """
    return {
        "scope": "in_scope",
        "run_status": "abstained",
        "answer_type": "service",
        "answer": abstention_text(NoDataReason.GATE_BLOCKED),
        "evidence_ids": [],
        "blocked_by_gate": True,
        "internal_violations": verdict.violations,
    }
