"""Adversarial audit: binder, registry builder, and candidate scorer."""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.bps_template_binder import (
    MAX_TEXT_LENGTH, TemplateValidationError, bind_template, escape_like,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_bps_registry.py"


def _templates() -> list[dict]:
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TEMPLATES":
            return ast.literal_eval(node.value)
    raise AssertionError("TEMPLATES not found")


TEMPLATES = {t["template_id"]: t for t in _templates()}


def _pure(name: str):
    source = BUILDER.read_text(encoding="utf-8")
    ns: dict = {}
    prelude = "\n".join([
        "import re",
        "from typing import Any",
        'COUNT_TITLE_RE = re.compile(r"^(jumlah|banyaknya|banyak|luas|produksi)", re.IGNORECASE)',
        'HEURISTIC_UNIT_SOURCES = frozenset({"title_matched"})',
        'QUERYABLE_UNIT_STATES = frozenset({"known", "unitless"})',
    ])
    body = f"def {name}" + source.split(f"def {name}", 1)[1].split("\ndef ", 1)[0]
    exec(prelude + "\n" + body, ns)  # noqa: S102
    return ns[name]


# ===========================================================================
# BINDER
# ===========================================================================

def test_template_without_row_limit_does_not_send_limit_null():
    """A missing/None row_limit must not become `LIMIT NULL`.

    In PostgreSQL `LIMIT NULL` means NO LIMIT — the exact opposite of the
    intent, and it fails silently: the query works, it just returns everything.
    """
    template = {
        "template_id": "probe", "has_own_limit": False, "row_limit": None,
        "parameter_schema": {"code": "text"},
        "sql_template": "SELECT * FROM bps_serving_dynamic WHERE indicator_code=%(code)s",
    }
    with pytest.raises(TemplateValidationError):
        bind_template(template, {"code": "29"})


def test_nan_and_infinity_are_rejected_as_numeric_parameters():
    """NaN defeats every comparison: `nan < 0` is False and `nan > max` is
    False, so both bounds checks pass and it reaches the database."""
    template = {
        "template_id": "probe", "has_own_limit": False, "row_limit": 100,
        "parameter_schema": {"n": "numeric|max:1000"},
        "sql_template": "SELECT %(n)s",
    }
    for bad in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(TemplateValidationError):
            bind_template(template, {"n": bad})


def test_text_length_cap_is_enforced():
    template = TEMPLATES["dynamic_latest"]
    with pytest.raises(TemplateValidationError, match="exceeds"):
        bind_template(
            {**template, "row_limit": 100},
            {"indicator_code": "x" * (MAX_TEXT_LENGTH + 1),
             "geography_code": None, "category_item": None},
        )


def test_jsonb_parameter_cannot_be_arbitrarily_deep():
    """A deeply nested payload is a cheap way to burn CPU in the JSON parser."""
    template = TEMPLATES["census_cross_tab"]
    nested: dict = {}
    cursor = nested
    for _ in range(500):
        cursor["a"] = {}
        cursor = cursor["a"]
    with pytest.raises(TemplateValidationError):
        bind_template(
            {**template, "row_limit": 100},
            {"event_id": "e1", "dataset_id": "d1", "geography_code": "1306000",
             "categories": nested},
        )


def test_escape_like_is_idempotent_safe_and_total():
    assert escape_like("100%") == r"100\%"
    assert escape_like(r"a\_b") == r"a\\\_b"
    assert escape_like("") == ""


# ===========================================================================
# REGISTRY BUILDER — pure functions against messy upstream data
# ===========================================================================

def test_unit_state_handles_missing_and_blank_titles():
    _unit_state = _pure("_unit_state")
    assert _unit_state(None, None, None) == "unknown_review"
    assert _unit_state("", "", None) == "unknown_review"
    assert _unit_state("   ", "  ", None) == "unknown_review"


def test_whitespace_only_unit_is_not_treated_as_a_real_unit():
    """A unit column containing "   " is missing data, not a unit."""
    _unit_state = _pure("_unit_state")
    assert _unit_state("Sesuatu", "   ", None) == "unknown_review"


def test_null_string_unit_from_upstream_is_not_a_unit():
    _unit_state = _pure("_unit_state")
    assert _unit_state("Sesuatu", "NULL", None) == "unknown_review"
    assert _unit_state("Sesuatu", "-", None) == "unknown_review"


def test_aggregation_never_returns_a_summable_class_for_unknown_units():
    _aggregation = _pure("_aggregation")
    for unit_state in ("unknown_review", "review_required"):
        assert _aggregation("apa saja", "", unit_state) == "unknown"
    assert _aggregation("Sesuatu Aneh", "", None) == "unknown"


def test_measure_gate_is_closed_by_default_for_unrecognised_states():
    _measure_gate = _pure("_measure_gate")
    assert _measure_gate("known")["queryable"] is True
    assert _measure_gate("unitless")["queryable"] is True
    assert _measure_gate("something_new_someone_added")["queryable"] is False


# ===========================================================================
# CANDIDATE SCORER — fuzzy matching must not rewrite valid words
# ===========================================================================

def _fuzzy_expand():
    """Lift the pure function out of the scorer.

    The module imports `workers.ingestion.*` at top level, which is not in this
    bundle, so a plain import fails. Skipping was the wrong answer: it left the
    only mechanism protecting against silent word-substitution untested.
    """
    import difflib

    source = (ROOT / "scripts" / "simulate_bps_candidate_scoring.py").read_text(encoding="utf-8")
    body = "def _fuzzy_expand" + source.split("def _fuzzy_expand", 1)[1].split("\ndef ", 1)[0]
    ns: dict = {"difflib": difflib}
    exec(body, ns)  # noqa: S102
    return ns["_fuzzy_expand"]


def test_fuzzy_match_does_not_silently_swap_a_different_indicator():
    """Typo tolerance that rewrites a REAL word into a DIFFERENT real word is
    worse than no tolerance: the citizen asks about one thing and is answered
    about another, confidently and with a source attached.
    """
    expand = _fuzzy_expand()
    vocabulary = frozenset({"kelahiran", "kematian", "penduduk", "pengangguran", "kemiskinan"})

    for word in ("kelahiran", "kematian", "kemiskinan", "penduduk"):
        assert expand(word, vocabulary) == (word,), f"{word} was rewritten"

    # Out-of-vocabulary but meaningful on its own — must NOT be forced onto a
    # near neighbour.
    assert expand("kelaparan", vocabulary) == ("kelaparan",)

    # A genuine typo still corrects.
    assert expand("pendudk", vocabulary) == ("penduduk",)


def test_fuzzy_match_leaves_short_words_alone():
    """Short tokens have too many plausible neighbours to correct safely."""
    expand = _fuzzy_expand()
    vocabulary = frozenset({"penduduk", "pendidikan"})
    assert expand("pdrb", vocabulary) == ("pdrb",)
    assert expand("tpt", vocabulary) == ("tpt",)


def test_fuzzy_match_is_a_noop_without_a_vocabulary():
    expand = _fuzzy_expand()
    assert expand("pendudk", frozenset()) == ("pendudk",)


def test_fuzzy_match_does_not_bridge_kelahiran_and_kematian():
    """These two differ by a handful of characters and mean opposite things.

    If the cutoff is ever loosened, this is the pair that will break first and
    the failure would be invisible in aggregate metrics.
    """
    expand = _fuzzy_expand()
    vocabulary = frozenset({"kelahiran"})
    assert expand("kematian", vocabulary) == ("kematian",), (
        "kematian was rewritten to kelahiran — opposite meaning, same confidence"
    )
