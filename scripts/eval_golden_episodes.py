#!/usr/bin/env python3
"""Golden-episode evaluation harness.

HONESTY CONTRACT (audit 2026-08-15, finding C1a)
------------------------------------------------
The previous version reported "19/19 PASS". That number was not what it looked
like. It executed the real offering engine only on the FIRST turn of an episode,
and only when that turn was a discovery turn. Every other turn was handed to
`_validation_errors()`, which inspects the expectation dictionary itself:

    if turn_expect.get("query_facts") is not True:
        errors.append("fact query must set query_facts=true")

That reads the fixture and checks the fixture says what the fixture says. For
episodes 012-019 — the entire service-menu, handover-SLA, busy-notice and
idle-timeout policy from docs/27 — nothing was executed at all, because no
implementation of that policy exists. They passed on the strength of their
action names being spelled correctly.

This harness therefore reports THREE numbers and never collapses them:

    exercised   turns actually run against real code (registry + offering engine)
    lint_only   turns where only the fixture's own schema was checked
    blocked     turns whose behaviour cannot be evaluated because the component
                that would implement them does not exist yet

`passed` is reported ONLY over `exercised`. A fixture that lints cleanly is not
a passing test, and this script will no longer say that it is.

Modes:
  tools (default) — execute what can be executed without an LLM.
  llm             — BLOCKED until OQ-05 (provider/model IDs) is resolved.

Report: data/reports/bps-golden-episode-eval.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EPISODES_PATH = ROOT / "packages" / "evals" / "bps-agent-query-episodes.json"
REPORT_PATH = ROOT / "data" / "reports" / "bps-golden-episode-eval.json"

FAMILY_PREFIX = {"S": "simdasi", "D": "dynamic", "C": "census", "P": "publication"}
QUERY_ACTIONS = {"query_stat_data", "query_and_compare"}
SELECTION_SOURCES = {"candidate_set_ref", "explicit_ref", "active_dataset"}

KNOWN_ACTIONS = {
    None, "offer_candidates", "offer_candidate_clusters", "clarify",
    "inspect_dataset", "query_stat_data", "query_and_compare",
    "resolve_or_clarify_candidates", "candidate_page", "resolve_candidate",
    "compare_sources", "rerank_candidates", "analyze_existing_result",
    "create_artifact", "show_service_menu", "service_fallback_offer",
    "request_admin_handover", "admin_handover_outcome",
    "admin_busy_notice", "end_session",
}

# Actions whose behaviour lives in the conversation/session policy engine.
# That engine is not built (docs/27 is planning), so these turns are reported as
# BLOCKED — never as passing.
SESSION_POLICY_ACTIONS = {
    "show_service_menu", "service_fallback_offer", "request_admin_handover",
    "admin_handover_outcome", "admin_busy_notice", "end_session",
}

# Actions requiring the query compiler / binder execution path, also not built.
RUNTIME_QUERY_ACTIONS = {
    "query_stat_data", "query_and_compare", "inspect_dataset",
    "candidate_page", "resolve_candidate", "compare_sources",
    "analyze_existing_result", "create_artifact",
}


def _fixture_errors(turn_expect: dict) -> list[str]:
    """Lint the fixture. NOT a behavioural test — see the module docstring."""
    errors: list[str] = []
    action = turn_expect.get("action")
    if action not in KNOWN_ACTIONS:
        errors.append(f"unknown action {action!r}")
    if action in QUERY_ACTIONS:
        if turn_expect.get("query_facts") is not True:
            errors.append("fact query must set query_facts=true")
        if turn_expect.get("selection_source") not in SELECTION_SOURCES:
            errors.append(
                f"fact query requires selection_source in {sorted(SELECTION_SOURCES)}"
            )
    return errors


def _classify(action: str | None) -> str:
    if action in SESSION_POLICY_ACTIONS:
        return "blocked_no_session_policy_engine"
    if action in RUNTIME_QUERY_ACTIONS:
        return "blocked_no_query_runtime"
    if action in ("offer_candidates", "offer_candidate_clusters"):
        return "exercised"
    return "lint_only"


def _exercise_discovery(offering_index, offer_candidates, turn: dict) -> list[str]:
    """Run the REAL offering engine for a discovery turn."""
    errors: list[str] = []
    expect = turn["expect"]
    utterance = turn.get("user")
    if not utterance:
        return ["discovery turn has no user utterance to exercise"]

    offering = offer_candidates(offering_index, utterance)
    groups = {group["family"] for group in offering["groups"]}

    for ref in expect.get("candidate_refs", []):
        if ref[0] in FAMILY_PREFIX and FAMILY_PREFIX[ref[0]] not in groups:
            errors.append(
                f"expected candidate {ref} family not offered; groups={sorted(groups)}"
            )
    recommended = expect.get("recommended_ref")
    if recommended and recommended[0] in FAMILY_PREFIX:
        rec_family = FAMILY_PREFIX[recommended[0]]
        if offering["recommendation"]["family"] != rec_family:
            errors.append(
                f"recommendation {offering['recommendation']['ref']} is family "
                f"{offering['recommendation']['family']}, expected {rec_family}"
            )
    if expect.get("query_facts") is not False:
        errors.append("discovery turn must not query facts")
    return errors


def run_tools(offering_index, offer_candidates) -> dict:
    episodes = json.loads(EPISODES_PATH.read_text(encoding="utf-8"))["episodes"]
    case_rows = []
    totals = {"exercised": 0, "lint_only": 0, "blocked": 0}
    exercised_passed = 0
    exercised_total = 0

    for episode in episodes:
        errors: list[str] = []
        turn_kinds: list[str] = []
        episode_exercised = 0
        episode_exercised_failed = 0

        for index, turn in enumerate(episode["turns"]):
            expect = turn["expect"]
            errors.extend(f"turn {index}: {e}" for e in _fixture_errors(expect))
            kind = _classify(expect.get("action"))
            turn_kinds.append(kind)

            if kind == "exercised" and expect.get("new_goal", False):
                turn_errors = _exercise_discovery(offering_index, offer_candidates, turn)
                episode_exercised += 1
                exercised_total += 1
                if turn_errors:
                    episode_exercised_failed += 1
                    errors.extend(f"turn {index}: {e}" for e in turn_errors)
                else:
                    exercised_passed += 1
                totals["exercised"] += 1
            elif kind.startswith("blocked"):
                totals["blocked"] += 1
            else:
                totals["lint_only"] += 1

        if episode_exercised == 0:
            status = "not_evaluated"
        elif episode_exercised_failed == 0:
            status = "passed"
        else:
            status = "failed"

        case_rows.append({
            "episode_id": episode["episode_id"],
            "status": status,
            "turns_total": len(episode["turns"]),
            "turns_exercised": episode_exercised,
            "turn_kinds": turn_kinds,
            "errors": errors[:5],
        })

    evaluated = [c for c in case_rows if c["status"] != "not_evaluated"]
    not_evaluated = [c for c in case_rows if c["status"] == "not_evaluated"]

    return {
        "mode": "tools",
        "episodes_total": len(episodes),
        "turns_total": sum(len(e["turns"]) for e in episodes),
        "turns_exercised": totals["exercised"],
        "turns_lint_only": totals["lint_only"],
        "turns_blocked": totals["blocked"],
        "episodes_evaluated": len(evaluated),
        "episodes_not_evaluated": len(not_evaluated),
        "not_evaluated_ids": [c["episode_id"] for c in not_evaluated],
        "exercised_assertions": exercised_total,
        "exercised_passed": exercised_passed,
        "headline": (
            f"{exercised_passed}/{exercised_total} exercised assertions passed; "
            f"{len(not_evaluated)} of {len(episodes)} episodes have no executable "
            f"assertion yet (no session-policy engine, no query runtime)"
        ),
        "warning": (
            "Do NOT quote this as 'N/N episodes PASS'. Episodes listed in "
            "not_evaluated_ids were never executed against any implementation; "
            "their fixtures were only schema-checked."
        ),
        "cases": case_rows,
    }


def run_llm() -> dict:
    return {
        "mode": "llm",
        "status": "blocked",
        "reason": "OQ-05 unresolved: exact provider/model credentials not configured; "
                  "live LLM evaluation waits for PRIMARY_MODEL/FALLBACK_MODEL IDs and quota.",
        "episodes_total": 0,
        "exercised_passed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tools", "llm"], default="tools")
    args = parser.parse_args()
    if args.mode == "llm":
        report = run_llm()
    else:
        from scripts.simulate_bps_candidate_scoring import (
            build_offering_index,
            offer_candidates,
        )

        index = build_offering_index()
        report = run_tools(index, offer_candidates)
    report.update({"generated_at": datetime.now(timezone.utc).isoformat()})
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
