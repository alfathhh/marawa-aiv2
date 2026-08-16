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


def _llm_round(base_url: str, api_key: str, model: str, messages: list[dict]) -> tuple[str | None, float]:
    """OpenAI-compatible chat round with retry/backoff (flash-lite rate limits)."""
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request

    payload = _json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 400,
    }).encode("utf-8")
    for attempt in range(3):
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        started = _time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = _json.loads(response.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"], _time.monotonic() - started
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 503) and attempt < 2:
                _time.sleep(1.5 * (attempt + 1))
                continue
            return None, _time.monotonic() - started
        except Exception:
            if attempt < 2:
                _time.sleep(1.5 * (attempt + 1))
                continue
            return None, _time.monotonic() - started
    return None, 0.0


SYSTEM_PROMPT = """Kamu adalah MARAWA, asisten statistik BPS Kabupaten Padang Pariaman di WhatsApp.
SETIAP balasan WAJIB berupa SATU objek JSON tanpa teks lain, bentuk:
{"action": "<ACTION>", "ref": "<ref opsional>", "reason": "singkat"}

Daftar ACTION LENGKAP yang boleh dipakai (pilih paling tepat):
- show_service_menu          # awal sesi: sapaan + menu 5 layanan (orientasi, bukan gerbang)
- offer_candidates           # penemuan kandidat utk goal baru; JANGAN query sebelum user pilih
- offer_candidate_clusters   # banyak kandidat → kelompokkan
- clarify                    # pertanyaan ambigu/tidak jelas → minta klarifikasi
- inspect_dataset            # user minta lihat detail/isi satu kandidat
- query_stat_data            # query fakta, HANYA setelah user memilih kandidat (ref wajib)
- query_and_compare          # bandingkan data dua periode/wilayah (setelah pilih)
- resolve_or_clarify_candidates  # referensi tidak jelas → tawarkan daftar atau tanya
- candidate_page             # user minta halaman berikutnya dari daftar kandidat
- resolve_candidate          # user pilih kandidat → kunci ref utk query
- compare_sources            # bandingkan sumber/tabel yang mirip
- rerank_candidates          # user perjelas preferensi → susun ulang kandidat
- analyze_existing_result    # follow-up dari hasil yang sudah ada (banding/tertinggi)
- create_artifact            # buat grafik/tabel turunan dari hasil
- service_fallback_offer     # tidak bisa menjawab → tawarkan form/petugas
- request_admin_handover     # user minta petugas (antrean, SLA 3 menit)
- admin_handover_outcome     # hasil handover (diterima/admin sibuk)
- admin_busy_notice          # tidak ada admin mengangkat dalam 3 menit → info + opsi batal
- end_session                # user keluar / timeout 5 menit tanpa balasan

ATURAN KERAS:
1. DILARANG query_stat_data/query_and_compare TANPA kandidat yang SUDAH dipilih user di percakapan.
2. Angka tanpa evidence dilarang; kalau tidak yakin → abstain + tawarkan petugas.
3. "batal/cancel/keluar" dan cancel natural membatalkan antrean admin.
4. Admin yang mengambil alih = bot berhenti membalas.
5. Saat user minta "data X" di goal baru → pilih offer_candidates, BUKAN query_stat_data.
6. Bahasa Indonesia, ringkas, tanpa markdown."""


def run_llm() -> dict:
    """Live LLM evaluation of the golden episodes (OQ-05 resolved path).

    Plays each episode turn-by-turn against the configured model
    (MARAWA_LLM_BASE_URL / MARAWA_LLM_API_KEY / MARAWA_LLM_MODEL). The model
    only picks an action; numbers and queries stay out of scope of this round.
    Without configuration the mode stays blocked (CI/dev path).
    """
    import os

    base_url = os.environ.get("MARAWA_LLM_BASE_URL") or os.environ.get("PROBE_BASE_URL")
    api_key = os.environ.get("MARAWA_LLM_API_KEY") or os.environ.get("PROBE_API_KEY")
    model = os.environ.get("MARAWA_LLM_MODEL")

    if not (base_url and api_key and model):
        return {
            "mode": "llm",
            "status": "blocked",
            "reason": "OQ-05 unresolved: MARAWA_LLM_BASE_URL/API_KEY/MODEL not configured.",
            "episodes_total": 0,
            "exercised_passed": 0,
        }

    episodes = json.loads(EPISODES_PATH.read_text(encoding="utf-8"))["episodes"]
    case_rows = []
    passed_turns = 0
    total_turns = 0
    skipped_event_turns = 0

    import re as _re

    for episode in episodes:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        errors: list[str] = []
        model_turns = 0
        for index, turn in enumerate(episode["turns"]):
            total_turns += 1
            expect = turn["expect"]
            user_text = turn.get("user")
            if not user_text:
                # Event-only turns (idle timeout, handover SLA) are driven by
                # the session-policy ENGINE, not by a model reading a citizen
                # message. Calling the LLM with no user input is meaningless
                # and the fixture classifies them separately in tools mode.
                skipped_event_turns += 1
                continue
            messages.append({"role": "user", "content": user_text})
            model_turns += 1

            reply, _secs = _llm_round(base_url, api_key, model, messages)
            if reply is None:
                errors.append(f"turn {index}: model call failed")
                continue

            match = _re.search(r"\{.*\}", reply, _re.S)
            if not match:
                errors.append(f"turn {index}: reply bukan JSON: {reply[:80]!r}")
                continue
            try:
                action = json.loads(match.group(0)).get("action")
            except json.JSONDecodeError:
                errors.append(f"turn {index}: JSON tidak bisa di-parse: {reply[:80]!r}")
                continue

            expected_action = expect.get("action")
            if action != expected_action:
                errors.append(
                    f"turn {index}: action {action!r}, diharapkan {expected_action!r}"
                )
            for forbidden in expect.get("forbidden_effects", []):
                if forbidden == "free_sql" and action == "query_stat_data":
                    errors.append(f"turn {index}: free_sql dibutuhkan pemilihan kandidat")
                if forbidden == "fact_query_before_candidate_selection" and action == "query_stat_data":
                    errors.append(f"turn {index}: query fakta sebelum kandidat dipilih")
                if forbidden == "bot_reply_during_admin_queue" and action not in (
                    "admin_busy_notice", "end_session", "show_service_menu",
                ):
                    errors.append(f"turn {index}: bot membalas saat antrean admin")

            if user_text:
                messages.append({"role": "assistant", "content": reply})

        if model_turns == 0:
            status = "not_evaluated"
        elif errors:
            status = "failed"
        else:
            status = "passed"
        if status == "passed":
            passed_turns += 1
        case_rows.append({
            "episode_id": episode["episode_id"],
            "status": status,
            "turns_total": len(episode["turns"]),
            "model_turns": model_turns,
            "errors": errors[:5],
        })

    failed = [c for c in case_rows if c["status"] == "failed"]
    not_evaluated = [c for c in case_rows if c["status"] == "not_evaluated"]

    return {
        "mode": "llm",
        "status": "ran" if not failed else "ran_with_failures",
        "model": model,
        "base_url": base_url,
        "episodes_total": len(episodes),
        "episodes_passed": passed_turns,
        "episodes_failed": len(failed),
        "episodes_not_evaluated": len(not_evaluated),
        "not_evaluated_ids": [c["episode_id"] for c in not_evaluated],
        "turns_total": total_turns,
        "skipped_event_turns": skipped_event_turns,
        "report_note": (
            "Model hanya memilih ACTION; angka/evidence/query dievaluasi di jalur "
            "tools. Event-only turns (timeout/handover SLA) di-skip: itu tugas "
            "session-policy ENGINE, bukan model. Episode pass bila SEMUA "
            "model-driven turn action-nya cocok."
        ),
        "cases": case_rows,
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
