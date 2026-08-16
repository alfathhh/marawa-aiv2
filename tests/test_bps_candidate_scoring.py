from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "reports" / "bps-candidate-scoring-simulation.json"


def load_report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_simulation_report_exists_and_records_metrics() -> None:
    report = load_report()
    assert report["mode"] == "planning_scoring_simulation_read_only"
    assert report["utterances"] >= 50
    metrics = report["metrics"]
    for key in ("overall_recall_at_3", "overall_mrr", "per_family_recall_at_3", "failures"):
        assert key in metrics


def test_simulation_target_metrics_reached() -> None:
    report = load_report()
    assert report["metrics"]["overall_recall_at_3"] >= 0.95, report["metrics"]
    assert report["metrics"]["overall_mrr"] >= 0.60, report["metrics"]


def test_known_ambiguous_cases_rank_correct_concepts() -> None:
    report = load_report()
    per_case = {item["utterance"]: item for item in report["cases"]}
    kemiskinan = per_case["kemiskinan"]
    assert kemiskinan["passed"] is True
    assert kemiskinan["rank1_family"] == "dynamic"
    assert any(rid in {"176", "177"} for rid in kemiskinan["rank1_ids"]), kemiskinan

    tpt = per_case["TPT"]
    assert tpt["passed"] is True
    assert any(rid in {"356", "357"} for rid in tpt["rank1_ids"]), tpt

    sekolah = per_case["jumlah SD per kecamatan"]
    assert sekolah["passed"] is True
    assert "230" in sekolah["top3_ids"], sekolah


def test_typo_and_alias_utterances_are_normalized() -> None:
    report = load_report()
    per_case = {item["utterance"]: item for item in report["cases"]}
    typo = per_case["pendudk lubuk alung terbaru"]
    assert typo["passed"] is True
    assert "29" in typo["rank1_ids"], typo
    assert "penduduk" in typo["normalization"]["concept"], typo
    assert typo["normalization"]["geography"] == "Lubuak Aluang", typo
