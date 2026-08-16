#!/usr/bin/env python3
"""Verify numbered HTTP(S) sources in MARAWA anti-jailbreak research.

Uses bounded GET requests so PDF/HTML bodies are not downloaded in full. HTTP 429 is
reported as rate-limited but does not fail the run; 404/410 and network failures do.
"""
from __future__ import annotations

import concurrent.futures
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "09C-ANTI-JAILBREAK-RESEARCH.md"
SOURCE_RE = re.compile(r"^\[(\d+)]\s+(https?://\S+)", re.MULTILINE)
MAX_WORKERS = 5
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Result:
    source_id: int
    url: str
    status: int | None
    final_url: str | None
    outcome: str
    detail: str = ""


def extract_sources() -> list[tuple[int, str]]:
    text = DOCUMENT.read_text(encoding="utf-8")
    sources = [(int(source_id), url.rstrip(".,;")) for source_id, url in SOURCE_RE.findall(text)]
    if not sources:
        raise RuntimeError(f"no numbered sources found in {DOCUMENT.relative_to(ROOT)}")
    ids = [source_id for source_id, _ in sources]
    expected = list(range(1, max(ids) + 1))
    if ids != expected:
        raise RuntimeError(f"source IDs must be contiguous and ordered: got {ids}, expected {expected}")
    return sources


def fetch(source: tuple[int, str]) -> Result:
    source_id, url = source
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0 MARAWA-research-source-verifier/1.0",
            "Range": "bytes=0-2047",
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            response.read(2048)
            outcome = "PASS" if 200 <= status < 400 else "FAIL"
            return Result(source_id, url, status, response.geturl(), outcome)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            return Result(source_id, url, error.code, error.geturl(), "RATE_LIMITED", "authoritative endpoint throttled verification")
        return Result(source_id, url, error.code, error.geturl(), "FAIL", str(error))
    except Exception as error:  # network failures need the concrete type in the report
        return Result(source_id, url, None, None, "FAIL", f"{type(error).__name__}: {error}")


def main() -> int:
    try:
        sources = extract_sources()
    except Exception as error:
        print(f"ERROR: {error}")
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(executor.map(fetch, sources))

    failures = [result for result in results if result.outcome == "FAIL"]
    limited = [result for result in results if result.outcome == "RATE_LIMITED"]
    for result in results:
        suffix = f" — {result.detail}" if result.detail else ""
        final = f" -> {result.final_url}" if result.final_url and result.final_url != result.url else ""
        print(f"[{result.source_id:02d}] {result.outcome:<12} HTTP {result.status!s:<4} {result.url}{final}{suffix}")

    print(
        f"research source verification: total={len(results)} "
        f"pass={len(results) - len(failures) - len(limited)} "
        f"rate_limited={len(limited)} fail={len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
