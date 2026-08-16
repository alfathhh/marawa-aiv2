from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages" / "contracts" / "bps-query-contracts.schema.json"
EPISODES_PATH = ROOT / "packages" / "evals" / "bps-agent-query-episodes.json"
BENCHMARK_PATH = ROOT / "data" / "reports" / "bps-query-optimization-benchmark.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_planning_artifacts_exist_and_are_valid_json() -> None:
    for path in (SCHEMA_PATH, EPISODES_PATH, BENCHMARK_PATH):
        assert path.is_file(), f"missing planning artifact: {path}"
        assert isinstance(load_json(path), dict)


def test_contract_schema_defines_discovery_inspect_query_and_result() -> None:
    schema = load_json(SCHEMA_PATH)
    assert schema["$schema"].startswith("https://json-schema.org/")
    required_defs = {
        "DiscoveryRequest",
        "CandidateSetResponse",
        "InspectDatasetResponse",
        "QueryStatDataRequest",
        "NormalizedQueryResult",
        "ClarificationResponse",
    }
    assert required_defs <= set(schema["$defs"])
    for name in required_defs:
        assert schema["$defs"][name].get("additionalProperties") is False


def test_golden_episodes_cover_agentic_query_behaviors() -> None:
    payload = load_json(EPISODES_PATH)
    episodes = payload["episodes"]
    assert len(episodes) >= 10
    tags = {tag for episode in episodes for tag in episode["tags"]}
    assert {
        "probing",
        "candidate-offering",
        "pagination",
        "source-comparison",
        "quarterly",
        "census-cross-tab",
        "alias",
        "marker",
        "follow-up",
        "artifact",
    } <= tags


def test_population_by_subdistrict_episode_requires_user_dataset_selection_before_query() -> None:
    episodes = {item["episode_id"]: item for item in load_json(EPISODES_PATH)["episodes"]}
    episode = episodes["bps-dialog-001"]
    assert episode["turns"][0]["user"] == "jumlah penduduk berdasarkan kecamatan"
    first_expectation = episode["turns"][0]["expect"]
    assert first_expectation["action"] == "offer_candidates"
    assert first_expectation["query_facts"] is False
    assert first_expectation["requires_user_selection"] is True
    assert first_expectation["candidate_refs"][:2] == ["S1", "D1"]

    selected = episode["turns"][1]["expect"]
    assert selected["action"] == "clarify"
    assert selected["selected_ref"] == "D1"
    assert selected["missing_slots"] == ["period"]
    assert selected["must_not_ask"] == ["indicator", "geography_level", "gender"]

    queried = episode["turns"][2]["expect"]
    assert queried["query_spec"]["period"]["mode"] == "latest"
    assert queried["query_spec"]["dimension_filters"] == {"jenis_kelamin": ["total"]}
    assert queried["expected_result"]["coverage"] == {"returned": 17, "expected": 17}
    assert queried["expected_result"]["sum"] == 467038


def test_new_goal_never_queries_facts_before_candidate_selection() -> None:
    payload = load_json(EPISODES_PATH)
    for episode in payload["episodes"]:
        first = episode["turns"][0]["expect"]
        if first.get("new_goal", True):
            assert first["action"] == "offer_candidates", episode["episode_id"]
            assert first.get("query_facts") is False, episode["episode_id"]
            assert first.get("requires_user_selection") is True, episode["episode_id"]


def test_query_is_allowed_after_explicit_ref_or_active_dataset_selection() -> None:
    episodes = {item["episode_id"]: item for item in load_json(EPISODES_PATH)["episodes"]}
    explicit = episodes["bps-dialog-011"]
    assert explicit["turns"][0]["user"] == "D1 tahun 2025"
    assert explicit["turns"][0]["expect"]["action"] == "query_stat_data"
    assert explicit["turns"][0]["expect"]["selection_source"] == "explicit_ref"

    follow_up = episodes["bps-dialog-010"]
    assert follow_up["turns"][1]["expect"]["action"] == "query_stat_data"
    assert follow_up["turns"][1]["expect"]["selection_source"] == "candidate_set_ref"
    assert follow_up["turns"][2]["expect"]["action"] == "analyze_existing_result"
    assert follow_up["turns"][2]["expect"]["reuse_result"] is True


def test_every_fact_query_has_selection_proof() -> None:
    allowed = {"candidate_set_ref", "explicit_ref", "active_dataset"}
    for episode in load_json(EPISODES_PATH)["episodes"]:
        selected = False
        for turn in episode["turns"]:
            expect = turn["expect"]
            if expect.get("selection_source") in {"candidate_set_ref", "explicit_ref"}:
                selected = True
            if expect.get("query_facts"):
                selection_source = expect.get("selection_source")
                assert selection_source in allowed, (episode["episode_id"], turn["user"])
                if selection_source == "active_dataset":
                    assert selected, (episode["episode_id"], "active dataset before selection")


def test_query_contract_requires_server_validated_selection_envelope() -> None:
    query_contract = load_json(SCHEMA_PATH)["$defs"]["QueryStatDataRequest"]
    assert "selection" in query_contract["required"]
    selection = query_contract["properties"]["selection"]
    assert selection["additionalProperties"] is False
    assert set(selection["properties"]["mode"]["enum"]) == {
        "candidate_set_ref",
        "explicit_ref",
        "active_dataset",
    }
    candidate_response = load_json(SCHEMA_PATH)["$defs"]["CandidateSetResponse"]
    assert "selection_required" in candidate_response["required"]


def test_candidate_refs_are_grouped_and_pagination_is_stable() -> None:
    episodes = {item["episode_id"]: item for item in load_json(EPISODES_PATH)["episodes"]}
    episode = episodes["bps-dialog-003"]
    page1 = episode["turns"][0]["expect"]["candidate_refs"]
    page2 = episode["turns"][1]["expect"]["candidate_refs"]
    assert page1 == ["P1", "P2", "P3"]
    assert page2 == ["P4", "P5", "P6"]
    assert not set(page1) & set(page2)


def test_every_episode_has_effect_level_assertions() -> None:
    for episode in load_json(EPISODES_PATH)["episodes"]:
        assert episode["episode_id"].startswith("bps-dialog-")
        assert episode["turns"]
        for turn in episode["turns"]:
            expect = turn["expect"]
            assert "action" in expect
            assert "forbidden_effects" in expect
            assert "free_sql" in expect["forbidden_effects"]


def test_benchmark_recommends_context_reduction_not_fact_cube() -> None:
    benchmark = load_json(BENCHMARK_PATH)
    assert benchmark["catalog"]["documents"] == 1074
    assert benchmark["context_payload"]["full_catalog_approx_tokens"] == 51535
    assert benchmark["context_payload"]["candidate_approx_tokens"] == 306
    assert benchmark["context_payload"]["reduction_percent"] == pytest.approx(99.407)
    assert benchmark["decision"]["catalog_strategy"] == "versioned_in_process_snapshot"
    assert benchmark["decision"]["fact_strategy"] == "postgresql_direct_typed_queries"
    assert benchmark["decision"]["vector_catalog_search"] == "deferred_until_eval_gap"
    assert benchmark["decision"]["precomputed_fact_cube"] == "rejected_for_mvp"


def test_benchmark_contains_live_query_latency_evidence() -> None:
    benchmark = load_json(BENCHMARK_PATH)
    latency = benchmark["fact_query_execution_ms"]
    assert latency["dynamic_exact_total"] == pytest.approx(0.187)
    assert latency["dynamic_breakdown"] == pytest.approx(0.086)
    assert latency["dynamic_trend"] == pytest.approx(0.151)
    assert latency["dynamic_quarterly"] == pytest.approx(0.142)
    assert latency["census_cross_tab"] == pytest.approx(0.260)
    assert latency["simdasi_exact"] == pytest.approx(0.420)


def test_episode_fixture_contains_no_sql_or_internal_paths() -> None:
    text = EPISODES_PATH.read_text(encoding="utf-8").lower()
    assert "select " not in text
    assert "/home/ubuntu" not in text
    assert "postgres_password" not in text
    assert "api_key" not in text
