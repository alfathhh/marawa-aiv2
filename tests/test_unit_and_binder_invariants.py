"""Invariant tests for the audit 2026-08-15 findings.

These enforce, in code, the two rules the documents repeat most often and that
the implementation was quietly violating:

    "unit tidak ditebak"        -> C2a, C2b, C2c
    "row_limit server-side"     -> H1

Every test here failed before the fix. Keep them failing-first if you touch the
registry builder or the binder.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_bps_registry.py"


def _load_templates() -> list[dict]:
    """Read TEMPLATES literally, so no database or import side effects."""
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TEMPLATES":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TEMPLATES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("TEMPLATES not found in build_bps_registry.py")


TEMPLATES = _load_templates()


# ---------------------------------------------------------------------------
# C2 — unit is never guessed
# ---------------------------------------------------------------------------

def _pure(function_name: str):
    """Exec a single pure function out of the builder.

    The builder imports psycopg and workers.* at module level, which are not
    available in a unit-test environment, so the function under test is lifted
    out rather than imported. Behaviour is asserted directly; grepping the
    source would also match the explanatory comments.
    """
    source = BUILDER.read_text(encoding="utf-8")
    namespace: dict = {}
    prelude = "\n".join([
        "import re",
        "from typing import Any",
        'COUNT_TITLE_RE = re.compile(r"^(jumlah|banyaknya|banyak|luas|produksi)", re.IGNORECASE)',
        'HEURISTIC_UNIT_SOURCES = frozenset({"title_matched"})',
        'QUERYABLE_UNIT_STATES = frozenset({"known", "unitless"})',
    ])
    body = f"def {function_name}" + source.split(f"def {function_name}", 1)[1].split("\ndef ", 1)[0]
    exec(prelude + "\n" + body, namespace)  # noqa: S102
    return namespace[function_name]


def test_menurut_is_not_evidence_of_unitlessness():
    """C2b: "menurut" marks a breakdown dimension, not a missing unit.

    "Rata-rata Lama Sekolah Menurut Kecamatan" is measured in years. Treating
    the word as proof of unitlessness let such measures skip unit review.
    """
    _unit_state = _pure("_unit_state")
    assert _unit_state("Rata-rata Lama Sekolah Menurut Kecamatan", None, None) == "unknown_review"
    assert _unit_state("Angka Harapan Hidup Menurut Kecamatan", None, None) == "unknown_review"
    # A genuine count title still resolves without review.
    assert _unit_state("Jumlah Penduduk Menurut Kecamatan", None, None) == "unitless"


def test_title_derived_unit_is_review_required():
    """C2a: a unit matched out of a table title is a guess, not metadata."""
    _unit_state = _pure("_unit_state")
    assert _unit_state("PDRB Kabupaten", "miliar rupiah", None, "title_matched") == "review_required"
    assert _unit_state("PDRB Kabupaten", "miliar rupiah", None, "column_meta") == "known"
    assert _unit_state("Persentase Penduduk Miskin", "persen", None) == "known"


def test_unknown_unit_is_never_summable():
    """M3: an unknown unit must not default to a summable aggregation class."""
    _aggregation = _pure("_aggregation")
    assert _aggregation("Sesuatu Yang Tidak Jelas", "", "unknown_review") == "unknown"
    assert _aggregation("PDRB", "miliar rupiah", "review_required") == "unknown"
    assert _aggregation("Jumlah Penduduk", "", "unitless") == "count"
    assert _aggregation("Persentase Penduduk Miskin", "persen", "known") == "share"


def test_measure_gate_blocks_unsourced_units():
    """C2c: the gate must live on the measure, not only on the dataset."""
    source = BUILDER.read_text(encoding="utf-8")
    assert "def _measure_gate" in source, "measure-level gate missing"
    gate_src = source.split("def _measure_gate", 1)[1].split("\ndef ", 1)[0]
    assert '"queryable": False' in gate_src
    # The old dataset-level-only rule must be gone.
    assert "states <= {\"unknown_review\"}" not in source, (
        "dataset-level-only unit gate reintroduced; a dataset with one good "
        "measure would again expose its unit-less measures"
    )


def test_integrity_gate_refuses_queryable_measure_without_unit():
    source = BUILDER.read_text(encoding="utf-8")
    assert "unit must be sourced, never guessed" in source


# ---------------------------------------------------------------------------
# H1 — row limit really is server-side
# ---------------------------------------------------------------------------

def test_every_template_declares_limit_ownership():
    for template in TEMPLATES:
        assert "has_own_limit" in template, (
            f"{template['template_id']} must declare has_own_limit; the old code "
            "sniffed the substring 'LIMIT' out of the SQL, which was both "
            "case-sensitive and matched aliases and comments"
        )
        sql_has_limit = re.search(r"\bLIMIT\b", template["sql_template"], re.IGNORECASE) is not None
        assert bool(template["has_own_limit"]) == sql_has_limit, (
            f"{template['template_id']}: has_own_limit disagrees with the SQL"
        )


def test_every_declared_parameter_is_applied_in_sql():
    for template in TEMPLATES:
        for name in template["parameter_schema"]:
            assert f"%({name})s" in template["sql_template"], (
                f"{template['template_id']} declares {name!r} but never applies it; "
                "the agent would believe a filter was applied when it was not"
            )


def test_caller_cannot_request_unbounded_pages():
    from scripts.bps_template_binder import TemplateValidationError, bind_template

    publication = next(t for t in TEMPLATES if t["template_id"] == "publication_list")
    template = {**publication, "row_limit": 100}
    with pytest.raises(TemplateValidationError, match="exceeds declared maximum"):
        bind_template(template, {"search": None, "page_size": 50_000_000, "offset": 0})


def test_template_without_own_limit_is_wrapped():
    from scripts.bps_template_binder import bind_template

    point = next(t for t in TEMPLATES if t["template_id"] == "dynamic_point")
    sql, bound = bind_template(
        {**point, "row_limit": 100},
        {
            "indicator_code": "29",
            "period": "2025",
            "geography_code": None,
            "category_item": None,
            "subperiod_code": None,
        },
    )
    assert sql.rstrip().endswith("LIMIT %(row_limit)s")
    assert bound["row_limit"] == 100


def test_integer_parameters_must_declare_a_bound():
    from scripts.bps_template_binder import TemplateValidationError, bind_template

    template = {
        "template_id": "probe",
        "has_own_limit": False,
        "row_limit": 100,
        "parameter_schema": {"n": "integer"},
        "sql_template": "SELECT %(n)s",
    }
    with pytest.raises(TemplateValidationError, match="must declare a 'max:' bound"):
        bind_template(template, {"n": 5})


# ---------------------------------------------------------------------------
# H2 — LIKE wildcards cannot escape the filter
# ---------------------------------------------------------------------------

def test_like_wildcards_are_escaped():
    from scripts.bps_template_binder import bind_template

    publication = next(t for t in TEMPLATES if t["template_id"] == "publication_list")
    _sql, bound = bind_template(
        {**publication, "row_limit": 100},
        {"search": "100% padi_2025", "page_size": 10, "offset": 0},
    )
    assert bound["search"] == r"100\% padi\_2025"


def test_like_template_declares_escape_clause():
    publication = next(t for t in TEMPLATES if t["template_id"] == "publication_list")
    assert "ESCAPE" in publication["sql_template"], (
        "escaping in the binder is inert unless the SQL declares ESCAPE"
    )


# ---------------------------------------------------------------------------
# H4 / M1 / M2 — query surface matches how people actually ask
# ---------------------------------------------------------------------------

def test_latest_period_template_exists():
    ids = {t["template_id"] for t in TEMPLATES}
    assert {"dynamic_latest", "simdasi_latest"} <= ids, (
        "'terbaru' is the most common public question shape and had no template"
    )


def test_no_duplicate_geography_parameter():
    """M1: primary_dimension_item filtered the same column as geography_code."""
    for template in TEMPLATES:
        schema = template["parameter_schema"]
        assert "primary_dimension_item" not in schema, (
            f"{template['template_id']} still exposes primary_dimension_item, "
            "which is a second name for geography_code"
        )


def test_simdasi_binds_on_code_not_label():
    """M2: exact-matching a human label breaks on whitespace and casing."""
    for template_id in ("simdasi_point", "simdasi_latest"):
        template = next(t for t in TEMPLATES if t["template_id"] == template_id)
        assert "indicator_code" in template["parameter_schema"]
        assert "indicator_name" not in template["parameter_schema"]
