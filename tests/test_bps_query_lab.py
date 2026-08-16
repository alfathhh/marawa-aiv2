from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_bps_query_prototypes import CASES, validate_read_only_sql


ROOT = Path(__file__).resolve().parents[1]


def test_query_lab_contains_all_planned_query_shapes() -> None:
    required = {
        "dynamic_exact_total",
        "dynamic_breakdown_coverage",
        "dynamic_trend_compare",
        "dynamic_composition",
        "dynamic_quarterly",
        "simdasi_exact_total",
        "simdasi_rounded_coverage",
        "simdasi_marker_preserved",
        "census_cross_tab",
        "publication_pagination",
        "candidate_answerability",
        "serving_gap_sentinels",
    }
    assert required <= {case.case_id for case in CASES}


def test_every_prototype_is_read_only() -> None:
    for case in CASES:
        validate_read_only_sql(case.sql)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE bps_dynamic_facts SET value_numeric=0",
        "WITH x AS (DELETE FROM bps_dynamic_facts RETURNING *) SELECT * FROM x",
        "SELECT 1; DROP TABLE bps_dynamic_facts",
        "INSERT INTO bps_dynamic_facts DEFAULT VALUES",
    ],
)
def test_read_only_validator_rejects_mutation(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_read_only_sql(sql)


def test_read_only_validator_accepts_select_and_cte() -> None:
    validate_read_only_sql("SELECT 1")
    validate_read_only_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x")


def test_query_lab_cli_imports_project_modules() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/validate_bps_query_prototypes.py", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--output" in completed.stdout
