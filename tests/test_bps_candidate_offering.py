from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.simulate_bps_candidate_scoring import (  # noqa: E402
    build_offering_index,
    offer_candidates,
    next_page,
)

REPORT_PATH = ROOT / "data" / "reports" / "bps-candidate-scoring-simulation.json"
OFFERING_PATH = ROOT / "data" / "reports" / "bps-candidate-offering-simulation.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_offering_report_exists_with_metrics() -> None:
    report = load_json(OFFERING_PATH)
    assert report["mode"] == "planning_candidate_offering_simulation_read_only"
    assert report["metrics"]["golden_family_included_at_1"] >= 0.95
    assert report["metrics"]["recommendation_ref_rank1_agreement"] >= 0.60


def test_population_breakdown_offers_d29_as_d1() -> None:
    report = load_json(OFFERING_PATH)
    per_case = {item["utterance"]: item for item in report["cases"]}
    case = per_case["jumlah penduduk berdasarkan kecamatan"]
    groups = {group["family"]: group for group in case["groups"]}
    assert groups["dynamic"]["items"][0]["display_ref"] == "D1"
    assert groups["dynamic"]["items"][0]["resource_id"] == "29"
    assert case["recommendation"]["ref"] == "D1"


def test_offering_limits_three_items_per_family() -> None:
    for item in load_json(OFFERING_PATH)["cases"]:
        for group in item["groups"]:
            assert len(group["items"]) <= 3
            refs = [candidate["display_ref"] for candidate in group["items"]]
            assert len(refs) == len(set(refs))


def test_publication_pagination_keeps_stable_refs() -> None:
    report = load_json(OFFERING_PATH)
    per_case = {item["utterance"]: item for item in report["cases"]}
    case = per_case["publikasi penduduk"]
    group = next(group for group in case["groups"] if group["family"] == "publication")
    page1 = [candidate["display_ref"] for candidate in group["items"]]
    assert page1 == ["P1", "P2", "P3"]
    assert group["has_more"] is True
    cursor = group["next_cursor"]
    page2 = [candidate["display_ref"] for candidate in next_page(group, cursor)]
    assert page2 == ["P4", "P5", "P6"]
    assert not set(page1) & set(page2)


def test_all_offered_candidates_are_answerable() -> None:
    for item in load_json(OFFERING_PATH)["cases"]:
        for group in item["groups"]:
            for candidate in group["items"]:
                assert candidate["answerability"] == "answerable", (
                    item["utterance"],
                    candidate["display_ref"],
                )


def test_offering_engine_is_pure_and_deterministic() -> None:
    index = build_offering_index()
    first = offer_candidates(index, "data penduduk dong")
    second = offer_candidates(index, "data penduduk dong")
    assert first == second
