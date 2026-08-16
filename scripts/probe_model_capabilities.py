#!/usr/bin/env python3
"""Probe an OpenAI-compatible endpoint for the three capabilities MARAWA needs.

Resolves OQ-05. Half a day of work that removes the largest unknown from Slice 1,
because each answer changes the runtime design:

  1. STRUCTURED OUTPUT (json_schema / json_object)
       yes -> envelope is guaranteed parseable
       no  -> prompt + strict parser + one repair attempt (AGENT.md §0C)

  2. ASSISTANT PREFILL (trailing assistant message continued by the model)
       Many OpenAI-compatible proxies silently ignore it or error. If absent,
       drop prefill from the design entirely rather than assuming it works.

  3. TOOL / FUNCTION CALLING
       yes -> Layer 0 stands as designed: the model emits {template_id, params}
              and never SQL
       no  -> Layer 0 needs a hand-written parser, which is a materially
              different (and weaker) design. Know this BEFORE building.

Also measures latency and reports whether the model volunteers a number it was
never given — a cheap early read on hallucination pressure for this domain.

Usage:
    export PROBE_BASE_URL="https://.../v1"
    export PROBE_API_KEY="..."
    uv run python scripts/probe_model_capabilities.py --model gemini-3.1-flash
    uv run python scripts/probe_model_capabilities.py --model deepseek-v4-flash

Writes: data/reports/model-capability-probe.json (merged per model)
No database access. Costs a handful of tokens per run.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "reports" / "model-capability-probe.json"

ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "enum": ["in_scope", "out_of_scope"]},
        "answer_type": {"type": "string"},
        "answer": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scope", "answer_type", "answer", "evidence_ids"],
    "additionalProperties": False,
}

QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_stat_data",
        "description": "Ambil angka statistik dari tabel yang sudah dipilih pengguna.",
        "parameters": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string"},
                "indicator_code": {"type": "string"},
                "period": {"type": "string"},
                "geography_code": {"type": "string"},
            },
            "required": ["template_id", "indicator_code"],
        },
    },
}


def _post(base_url: str, api_key: str, payload: dict[str, Any], timeout: int = 60) -> tuple[int, Any, float]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body, time.monotonic() - started
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return exc.code, {"error": detail}, time.monotonic() - started
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": repr(exc)}, time.monotonic() - started


def _content(body: Any) -> str:
    try:
        return body["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

def probe_structured_output(base_url, api_key, model) -> dict[str, Any]:
    """Try json_schema, then fall back to json_object, then to prompt-only."""
    prompt = [
        {"role": "system", "content": "Balas HANYA JSON sesuai schema. Tanpa teks lain."},
        {"role": "user", "content": "Sapa pengguna dan tawarkan bantuan statistik BPS."},
    ]
    attempts: dict[str, Any] = {}

    status, body, _ = _post(base_url, api_key, {
        "model": model,
        "messages": prompt,
        "max_tokens": 300,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "envelope", "strict": True, "schema": ENVELOPE_SCHEMA},
        },
    })
    attempts["json_schema"] = _judge_json(status, body)

    status, body, _ = _post(base_url, api_key, {
        "model": model,
        "messages": prompt,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    })
    attempts["json_object"] = _judge_json(status, body)

    status, body, _ = _post(base_url, api_key, {
        "model": model, "messages": prompt, "max_tokens": 300,
    })
    attempts["prompt_only"] = _judge_json(status, body)

    if attempts["json_schema"]["parsed"]:
        mode, note = "json_schema", "Terbaik. Envelope dijamin sesuai schema."
    elif attempts["json_object"]["parsed"]:
        mode, note = "json_object", "JSON dijamin, schema tidak. Validasi envelope tetap wajib."
    elif attempts["prompt_only"]["parsed"]:
        mode, note = "prompt_only", "Tidak ada jaminan. Parser ketat + satu kali repair wajib."
    else:
        mode, note = "none", "Model gagal menghasilkan JSON sama sekali. Jangan dipakai."
    return {"supported_mode": mode, "note": note, "attempts": attempts}


def _judge_json(status: int, body: Any) -> dict[str, Any]:
    if status != 200:
        return {"http_status": status, "parsed": False, "error": str(body.get("error"))[:200]}
    text = _content(body).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        json.loads(text)
        return {"http_status": 200, "parsed": True}
    except json.JSONDecodeError as exc:
        return {"http_status": 200, "parsed": False, "error": f"{exc}", "sample": text[:160]}


def probe_prefill(base_url, api_key, model) -> dict[str, Any]:
    """Does the endpoint continue a trailing assistant message?

    Detection: send a prefill that commits to a JSON opening. If the reply
    RE-EMITS the opening brace, the prefill was ignored and treated as history.
    """
    status, body, _ = _post(base_url, api_key, {
        "model": model,
        "messages": [
            {"role": "system", "content": "Balas HANYA JSON."},
            {"role": "user", "content": "Sapa pengguna."},
            {"role": "assistant", "content": '{"scope":'},
        ],
        "max_tokens": 200,
    })
    if status != 200:
        return {
            "supported": False,
            "http_status": status,
            "note": "Endpoint menolak trailing assistant message. HAPUS prefill dari desain.",
            "error": str(body.get("error"))[:200],
        }
    text = _content(body).strip()
    continued = not text.startswith("{")
    return {
        "supported": continued,
        "http_status": 200,
        "sample": text[:120],
        "note": (
            "Prefill dilanjutkan. Pakai sebagai optimasi format (AGENT.md §0A)."
            if continued else
            "Balasan mengulang dari awal — prefill DIABAIKAN. Jangan andalkan."
        ),
    }


def probe_tool_calling(base_url, api_key, model) -> dict[str, Any]:
    status, body, _ = _post(base_url, api_key, {
        "model": model,
        "messages": [
            {"role": "system", "content": "Gunakan tool bila perlu data. Jangan mengarang angka."},
            {"role": "user", "content": "Berapa jumlah penduduk Kecamatan Batang Anai tahun 2025? Tabel D1 sudah dipilih."},
        ],
        "tools": [QUERY_TOOL],
        "max_tokens": 300,
    })
    if status != 200:
        return {
            "supported": False, "http_status": status,
            "note": "Tanpa tool calling, Lapis 0 harus diganti parser manual — desain berbeda dan lebih lemah.",
            "error": str(body.get("error"))[:200],
        }
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError):
        return {"supported": False, "http_status": 200, "note": "Bentuk respons tidak dikenali."}
    calls = message.get("tool_calls") or []
    return {
        "supported": bool(calls),
        "http_status": 200,
        "called": [c.get("function", {}).get("name") for c in calls],
        "note": (
            "Lapis 0 berdiri seperti dirancang: model mengeluarkan tool call, bukan SQL."
            if calls else
            "Model menjawab langsung tanpa memanggil tool. Periksa apakah ia MENGARANG angka."
        ),
        "content_if_no_call": _content(body)[:200],
    }


def probe_hallucination_pressure(base_url, api_key, model) -> dict[str, Any]:
    """Ask for a figure the model was never given. Does it volunteer one?

    Not a benchmark — a smoke signal. The gate blocks fabrication regardless,
    but a model that invents figures unprompted will trip the gate constantly
    and make MARAWA feel unhelpful.
    """
    status, body, _ = _post(base_url, api_key, {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "Kamu asisten statistik BPS. ATURAN KERAS: jangan pernah menyebut angka "
                "yang tidak ada di hasil tool. Kalau tidak ada datanya, katakan tidak ada."
            )},
            {"role": "user", "content": "Berapa jumlah penduduk Kecamatan Batang Anai tahun 2025?"},
        ],
        "max_tokens": 300,
    })
    if status != 200:
        return {"http_status": status, "error": str(body.get("error"))[:200]}
    text = _content(body)
    import re
    numbers = re.findall(r"\d{1,3}(?:[.,]\d{3})+", text)
    return {
        "http_status": 200,
        "volunteered_numbers": numbers,
        "refused_cleanly": not numbers,
        "sample": text[:240],
        "note": (
            "Menolak tanpa mengarang. Sinyal bagus." if not numbers else
            "Model menyebut angka tanpa tool. Gate akan memblokirnya, tetapi "
            "abstention rate akan tinggi — pertimbangkan prompt lebih tegas atau model lain."
        ),
    }


def probe_latency(base_url, api_key, model, rounds: int = 3) -> dict[str, Any]:
    timings: list[float] = []
    for _ in range(rounds):
        status, _body, elapsed = _post(base_url, api_key, {
            "model": model,
            "messages": [{"role": "user", "content": "Jawab satu kata: siap"}],
            "max_tokens": 10,
        })
        if status == 200:
            timings.append(elapsed)
    if not timings:
        return {"measured": False}
    return {
        "measured": True,
        "rounds": len(timings),
        "median_seconds": round(statistics.median(timings), 2),
        "max_seconds": round(max(timings), 2),
        "note": "WhatsApp terasa lambat di atas ~8 detik untuk balasan pertama.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default=os.environ.get("PROBE_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("PROBE_API_KEY"))
    args = parser.parse_args()

    if not args.base_url or not args.api_key:
        print("PROBE_BASE_URL dan PROBE_API_KEY wajib diisi.", file=sys.stderr)
        return 2

    print(f"Probing {args.model} ...", file=sys.stderr)
    result = {
        "model": args.model,
        "base_url": args.base_url,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "structured_output": probe_structured_output(args.base_url, args.api_key, args.model),
        "prefill": probe_prefill(args.base_url, args.api_key, args.model),
        "tool_calling": probe_tool_calling(args.base_url, args.api_key, args.model),
        "hallucination_pressure": probe_hallucination_pressure(args.base_url, args.api_key, args.model),
        "latency": probe_latency(args.base_url, args.api_key, args.model),
    }

    blockers = []
    if result["structured_output"]["supported_mode"] == "none":
        blockers.append("tidak bisa menghasilkan JSON — model tidak layak dipakai")
    if not result["tool_calling"]["supported"]:
        blockers.append("tanpa tool calling, Lapis 0 harus dirancang ulang")
    result["verdict"] = {
        "usable": not blockers,
        "blockers": blockers,
        "design_impact": [
            f"envelope mode = {result['structured_output']['supported_mode']}",
            f"prefill = {'pakai' if result['prefill']['supported'] else 'HAPUS dari desain'}",
            f"tool calling = {'ada' if result['tool_calling']['supported'] else 'TIDAK ADA'}",
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if REPORT_PATH.exists():
        try:
            existing = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing[args.model] = result
    REPORT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result["verdict"], ensure_ascii=False, indent=2))
    print(f"\nLaporan lengkap: {REPORT_PATH.relative_to(ROOT)}", file=sys.stderr)
    return 0 if result["verdict"]["usable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
