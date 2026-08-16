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


def _llm_round(
    base_url: str, api_key: str, model: str, messages: list[dict],
    tools: list[dict] | None = None,
) -> tuple[str | None, list[dict] | None, float]:
    """OpenAI-compatible chat round with retry/backoff + tool calling.

    Returns (text, tool_calls, seconds); tool_calls is a list of
    {name, arguments} when the model requested function calls.
    """
    import json as _json
    import time as _time
    import urllib.error
    import urllib.request

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": 400,
    }
    if tools:
        payload["tools"] = tools
    body = _json.dumps(payload).encode("utf-8")
    for attempt in range(3):
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        started = _time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                parsed = _json.loads(response.read().decode("utf-8"))
                message = parsed["choices"][0]["message"]
                raw_calls = message.get("tool_calls") or []
                calls = []
                for call in raw_calls:
                    fn = call.get("function", {})
                    calls.append({
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments", "{}"),
                        # Replay butuh objek MENTAH: id asli + extra_content
                        # (thought_signature) + nama dengan prefix provider,
                        # kalau tidak Gemini membalas 400.
                        "raw": call,
                        "tool_call_id": call.get("id"),
                    })
                return message.get("content"), (calls or None), _time.monotonic() - started
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 503) and attempt < 2:
                _time.sleep(1.5 * (attempt + 1))
                continue
            return None, None, _time.monotonic() - started
        except Exception:
            if attempt < 2:
                _time.sleep(1.5 * (attempt + 1))
                continue
            return None, None, _time.monotonic() - started
    return None, None, 0.0


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


def _offer_tool_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "offer_candidates",
            "description": (
                "Cari kandidat dataset statistik BPS utk utterance ini. "
                "Panggil ini SEBELUM memutuskan kandidat mana yang relevan."
            ),
            "parameters": {
                "type": "object",
                "properties": {"utterance": {"type": "string"}},
                "required": ["utterance"],
            },
        },
    }


def _board_text(offering: dict | None) -> str:
    """Papan kandidat NYATA dari offering engine utk prompt (observation)."""
    if not offering:
        return ""
    lines = ["KANDIDAT DISKOVERI (dari registry BPS):"]
    for group in offering.get("groups", [])[:4]:
        for item in group.get("items", [])[:3]:
            lines.append(
                f"- {item['display_ref']} ({group['family']}) — "
                f"{item['title'][:70]} … periode {item.get('latest_year', '?')}"
            )
    rec = offering.get("recommendation") or {}
    if rec.get("ref"):
        lines.append(f"Rekomendasi sistem: {rec['ref']} ({rec.get('family', '?')}).")
    return "\n".join(lines)


def run_llm() -> dict:
    """Hybrid live LLM evaluation (observation + tool call + masking).

    1. Offering engine jalan dulu (server-side) utk turn discovery → papan
       kandidat NYATA masuk prompt (model tidak menebak-cari dari kosong).
    2. Tool `offer_candidates` tersedia — model boleh pangil; hasil eksekusi
       ditambahkan sebagai tool result sebelum keputusan final.
    3. Scoring HARD vs SOFT (anti-overfit):
       hard_fail  = parse/call gagal, masking violation, forbidden effect
       soft       = action valid tapi beda dari fixture (dilaporkan, bukan gagal)
    Tanpa konfigurasi → status blocked (CI/dev path).
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

    from scripts.action_masking import (
        allowed_actions as allowed_actions_check,
        apply_action,
        mask_prompt_line,
    )
    from scripts.simulate_bps_candidate_scoring import (
        build_offering_index,
        offer_candidates,
    )

    offering_index = build_offering_index()
    episodes = json.loads(EPISODES_PATH.read_text(encoding="utf-8"))["episodes"]
    tools = [_offer_tool_schema()]
    case_rows = []
    total_turns = 0
    skipped_event_turns = 0
    aggregate = {"hard_fail_turns": 0, "soft_mismatch_turns": 0}

    import re as _re

    def _call(messages: list[dict]) -> tuple[str | None, list[dict] | None, float]:
        return _llm_round(base_url, api_key, model, messages, tools=tools)

    for episode in episodes:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        hard_errors: list[str] = []
        soft_mismatches: list[str] = []
        model_turns = 0
        state = "BOT_ACTIVE"
        has_selected = False
        for index, turn in enumerate(episode["turns"]):
            total_turns += 1
            expect = turn["expect"]
            user_text = turn.get("user")
            if not user_text:
                # Event-only turns (idle timeout, handover SLA) are driven by
                # the session-policy ENGINE, not by a model reading a citizen
                # message. Calling the LLM with no user input is meaningless.
                skipped_event_turns += 1
                continue
            model_turns += 1
            masked = mask_prompt_line(state, has_selected)
            # Selection detection: user MENYEBUT ref (D1/S1/C1/P1) = pilih
            # kandidat, terlepas dari apa yang model "putuskan". Ini semantics
            # fixture (selection_source: explicit_ref/candidate_set_ref).
            if _re.search(r"\b[DCSP]\d+\b", user_text or ""):
                has_selected = True
            # Observation-first: offering engine sudah dijalankan server-side.
            board = ""
            if expect.get("new_goal") or expect.get("action") in (
                "offer_candidates", "resolve_or_clarify_candidates",
            ):
                offering = offer_candidates(offering_index, user_text)
                board = _board_text(offering)
            messages.append({
                "role": "user",
                "content": f"{user_text}\n\n{board}\n{masked}".strip(),
            })

            reply, _calls, _secs = _call(messages)
            # Tool loop: model boleh pangil offer_candidates -> hasilnya masuk
            # sebagai tool message, lalu model memutuskan final.
            if _calls:
                for call in _calls[:1]:
                    if call["name"] != "offer_candidates":
                        hard_errors.append(f"turn {index}: model memanggil tool asing {call['name']!r}")
                        continue
                    # Replay PERSIS objek yang dikirim model (id asli,
                    # function dengan prefix provider, extra_content).
                    tool_call_id = call.get("tool_call_id") or f"call_{index}"
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call.get("raw") or {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }],
                    })
                    try:
                        tool_args = json.loads(call["arguments"] or "{}")
                        tool_offering = offer_candidates(offering_index, tool_args.get("utterance", user_text))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": _board_text(tool_offering),
                        })
                    except json.JSONDecodeError:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": "ERROR: arguments tidak valid.",
                        })
                reply, _calls2, _secs2 = _call(messages)
                if _calls2:
                    hard_errors.append(f"turn {index}: tool loop tidak konvergen")
                    continue
            if reply is None:
                hard_errors.append(f"turn {index}: model call failed")
                continue

            match = _re.search(r"\{.*\}", reply, _re.S)
            if not match:
                hard_errors.append(f"turn {index}: reply bukan JSON: {reply[:80]!r}")
                continue
            try:
                action = json.loads(match.group(0)).get("action")
            except json.JSONDecodeError:
                hard_errors.append(f"turn {index}: JSON tidak bisa di-parse: {reply[:80]!r}")
                continue

            expected_action = expect.get("action")
            valid = action in allowed_actions_check(state, has_selected) if action else False
            if not valid:
                hard_errors.append(
                    f"turn {index}: action {action!r} TIDAK diizinkan masking pada "
                    f"state {state!r} (has_selected={has_selected})"
                )
                aggregate["hard_fail_turns"] += 1
            if action != expected_action:
                soft_mismatches.append(
                    f"turn {index}: action {action!r}, fixture ingin {expected_action!r}"
                )
                aggregate["soft_mismatch_turns"] += 1
            for forbidden in expect.get("forbidden_effects", []):
                if forbidden == "free_sql" and action == "query_stat_data" and not has_selected:
                    hard_errors.append(f"turn {index}: free_sql tanpa pemilihan kandidat")
                    aggregate["hard_fail_turns"] += 1
                if forbidden == "fact_query_before_candidate_selection" and action == "query_stat_data" and not has_selected:
                    hard_errors.append(f"turn {index}: query fakta sebelum kandidat dipilih")
                    aggregate["hard_fail_turns"] += 1
                # Berlaku saat SUDAH dalam antrean (state QUEUED sebelum
                # action ini), bukan pada turn yang meminta handover.
                if forbidden == "bot_reply_during_admin_queue" and state == "QUEUED":
                    if action not in ("admin_busy_notice", "end_session", "show_service_menu", "offer_candidates"):
                        hard_errors.append(f"turn {index}: bot membalas saat antrean admin")
                        aggregate["hard_fail_turns"] += 1
            state, has_selected = apply_action(state, action, has_selected)
            messages.append({"role": "assistant", "content": reply})

        if model_turns == 0:
            status = "not_evaluated"
        elif hard_errors:
            status = "failed"
        elif soft_mismatches:
            status = "passed_with_soft_mismatch"
        else:
            status = "passed"
        case_rows.append({
            "episode_id": episode["episode_id"],
            "status": status,
            "turns_total": len(episode["turns"]),
            "model_turns": model_turns,
            "hard_errors": hard_errors[:5],
            "soft_mismatches": soft_mismatches[:5],
        })

    failed = [c for c in case_rows if c["status"] == "failed"]
    passed_full = [c for c in case_rows if c["status"] == "passed"]
    passed_soft = [c for c in case_rows if c["status"] == "passed_with_soft_mismatch"]
    not_evaluated = [c for c in case_rows if c["status"] == "not_evaluated"]

    return {
        "mode": "llm",
        "harness": "hybrid_observation_tool_masking",
        "status": "ran" if not failed else "ran_with_failures",
        "model": model,
        "base_url": base_url,
        "episodes_total": len(episodes),
        "episodes_passed": len(passed_full),
        "episodes_passed_soft": len(passed_soft),
        "episodes_failed_hard": len(failed),
        "episodes_not_evaluated": len(not_evaluated),
        "not_evaluated_ids": [c["episode_id"] for c in not_evaluated],
        "turns_total": total_turns,
        "skipped_event_turns": skipped_event_turns,
        "hard_fail_turns": aggregate["hard_fail_turns"],
        "soft_mismatch_turns": aggregate["soft_mismatch_turns"],
        "anti_overfit_note": (
            "Soft mismatch (action valid tapi beda selera dari fixture) TIDAK "
            "dihitung gagal. Hard = forbidden/masking/parse. Skor yang "
            "dikejar: hard_clean. Ambigu ditinjau manusia, bukan dilarikan."
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
