#!/usr/bin/env python3
"""Validate the MARAWA AI documentation package without external dependencies."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "AGENT.md",
    "AGENTS.md",
    ".env.example",
    *[f"docs/{n:02d}-{name}.md" for n, name in [
        (0, "INDEX"),
        (1, "PRD"),
        (2, "AGENT-RUNTIME"),
        (2, "SCOPE-AND-CONVERSATION"),
        (3, "ARCHITECTURE"),
        (4, "RAG-AND-DATA"),
        (5, "DATABASE"),
        (6, "DASHBOARD-AND-HANDOVER"),
        (7, "WHATSAPP-WEBHOOK"),
        (8, "API-CONTRACT"),
        (9, "SECURITY-PRIVACY"),
        (10, "TEST-EVALUATION"),
        (11, "DEPLOYMENT"),
        (12, "OBSERVABILITY-RUNBOOK"),
        (13, "ADR"),
        (14, "IMPLEMENTATION-PLAN"),
        (15, "OPEN-QUESTIONS"),
        (16, "GLOSSARY"),
    ]],
    "docs/SOURCES.md",
    "docs/09A-ANTI-JAILBREAK.md",
    "docs/09B-ANTI-JAILBREAK-REDTEAM.md",
    "docs/09C-ANTI-JAILBREAK-RESEARCH.md",
    "docs/17-BPS-WEBAPI-DATA.md",
    "docs/18-WHATSAPP-DATA-ANSWER-FORMATS.md",
]

errors: list[str] = []
warnings: list[str] = []

for rel in REQUIRED:
    if not (ROOT / rel).is_file():
        if rel == ".env.example":
            warnings.append("missing .env.example (expected in the full repo, not in a docs-only bundle)")
            continue
        errors.append(f"missing required file: {rel}")

# Historical snapshots (audit reports, changelogs) are excluded from the
# consistency gates below: they deliberately quote numbers that were true (or
# wrong) AT THE TIME, as part of the finding itself, and are not living status
# claims. `docs/` stays the only place status is asserted; see docs/25.
markdown_files = sorted(
    p for p in ROOT.rglob("*.md") if "reports" not in p.relative_to(ROOT).parts
)
report_files = sorted((ROOT / "reports").glob("*.md")) if (ROOT / "reports").is_dir() else []
link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
citation_re = re.compile(r"\[(\d+)\]")

for path in markdown_files:
    rel = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")

    if text.count("```") % 2:
        errors.append(f"unbalanced fenced code block: {rel}")

    if "SAPA Statistik" in text:
        errors.append(f"stale branding 'SAPA Statistik': {rel}")

    if "files2.hatafisme" in text:
        errors.append(f"unrelated/stale URL: {rel}")

    citations = {int(value) for value in citation_re.findall(text)}
    # Ignore numbered list markers because the citation pattern only matches brackets.
    if citations:
        if "## Sources" not in text:
            errors.append(f"citations without Sources block: {rel}")
        for source_id in citations:
            if not re.search(rf"^\[{source_id}\] ", text, flags=re.MULTILINE):
                errors.append(f"citation [{source_id}] missing from Sources block: {rel}")

    for target in link_re.findall(text):
        target = target.strip().split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            errors.append(f"local link escapes repo in {rel}: {target}")
            continue
        if not resolved.exists():
            errors.append(f"broken local link in {rel}: {target}")

# ---------------------------------------------------------------------------
# Consistency gates (audit 2026-08-15, finding C1d).
#
# The validator used to check only that files exist, links resolve, and certain
# magic strings appear somewhere. It reported PASS while the pack carried three
# different test counts (74/126/150) and two different episode counts
# (11/28 and 19/47) for the same artifacts, and while the PRD promised a
# scheduled BPS sync that the cron policy forbids. Keyword presence is not
# consistency. These gates derive the truth from the artifacts themselves.
# ---------------------------------------------------------------------------

EPISODES_PATH = ROOT / "packages" / "evals" / "bps-agent-query-episodes.json"
if EPISODES_PATH.is_file():
    import json as _json

    _episodes = _json.loads(EPISODES_PATH.read_text(encoding="utf-8"))["episodes"]
    actual_episodes = len(_episodes)
    actual_turns = sum(len(episode["turns"]) for episode in _episodes)
    episode_claim_re = re.compile(r"(\d+)\s*episode\s*/\s*(\d+)\s*turn")
    for path in markdown_files:
        rel = path.relative_to(ROOT)
        for claimed_episodes, claimed_turns in episode_claim_re.findall(
            path.read_text(encoding="utf-8")
        ):
            if (int(claimed_episodes), int(claimed_turns)) != (actual_episodes, actual_turns):
                errors.append(
                    f"episode count drift in {rel}: claims "
                    f"{claimed_episodes} episode / {claimed_turns} turn, "
                    f"fixture has {actual_episodes} episode / {actual_turns} turn"
                )

# A number that appears in more than one document drifts. Test totals live in
# exactly one canonical place; everything else must point at it.
test_count_re = re.compile(r"\b(\d{2,4})\s*(?:tests?|passed|PASS)\b")
test_count_sources: dict[str, set[str]] = {}
for path in markdown_files:
    rel = str(path.relative_to(ROOT))
    for count in test_count_re.findall(path.read_text(encoding="utf-8")):
        test_count_sources.setdefault(count, set()).add(rel)
canonical_test_doc = "docs/25-PLANNING-AUDIT-STATUS.md"
for count, sources in test_count_sources.items():
    outside = sorted(source for source in sources if source != canonical_test_doc)
    if outside:
        warnings.append(
            f"test count '{count}' quoted outside {canonical_test_doc}: {', '.join(outside)} "
            "(point at the canonical status document instead of restating the number)"
        )

# Mutually exclusive policy statements must not coexist.
#
# Negation-aware: a sentence that FORBIDS the thing is not a promise of it.
# Without this, the gate fires on the very sentence that states the policy.
NEGATION_RE = re.compile(
    r"\b(no|not|never|tanpa|tidak|bukan|dilarang|forbid\w*|prohibit\w*|"
    r"reappear\w*|fails the build|deferred|dicabut|cancelled)\b",
    re.IGNORECASE,
)


def _asserting_lines(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Lines that make the claim, excluding lines that deny or forbid it."""
    hits = []
    for line in text.splitlines():
        if pattern.search(line) and not NEGATION_RE.search(line):
            hits.append(line.strip())
    return hits


CONTRADICTIONS = [
    (
        re.compile(r"scheduled\s+sync", re.IGNORECASE),
        "a scheduled BPS sync is promised while the locked policy is manual-only updates",
    ),
]
for pattern, message in CONTRADICTIONS:
    offenders = []
    for path in markdown_files:
        rel = str(path.relative_to(ROOT))
        if _asserting_lines(path.read_text(encoding="utf-8"), pattern):
            offenders.append(rel)
    if offenders:
        errors.append(f"policy contradiction: {message} ({', '.join(offenders)})")

# Closed-loop metrics may not be presented as evidence without their label.
# See audit findings C1b/C1c: the scorer was calibrated on the set it is scored
# against, so a bare "Recall@3 1.000" overstates what is known.
for path in markdown_files:
    rel = str(path.relative_to(ROOT))
    text = path.read_text(encoding="utf-8")
    if re.search(r"Recall@3[^\n]*1[.,]000", text) and "sintetis" not in text and "synthetic" not in text:
        errors.append(
            f"unlabelled closed-loop metric in {rel}: Recall@3 1.000 comes from an "
            "author-written set the scorer was tuned on; label it synthetic"
        )

all_text = "\n".join(path.read_text(encoding="utf-8") for path in markdown_files)
env_text = (ROOT / ".env.example").read_text(encoding="utf-8") if (ROOT / ".env.example").exists() else ""

for expected in [
    "https://padangpariamankab.bps.go.id",
    "https://ppid.bps.go.id/?mfd=1306",
    "MARAWA AI — Asisten Statistik Padang Pariaman",
    "ADMIN_ACTIVE",
    "deepseek-v4-flash",
    "domain-bounded conversational AI agent",
    "MAX_AGENT_STEPS",
    "working memory",
    "analysis sandbox",
    "scoped capability",
    "Effect-ASR",
    "Agents Rule of Two",
    "BPS WebAPI mirror",
    "SIMDASI",
    "bps_raw_snapshots",
]:
    if expected not in all_text and expected not in env_text:
        errors.append(f"required decision/reference missing: {expected}")

if not env_text:
    warnings.append("skipping .env.example checks: file not present in this bundle")
else:
    if "PRIMARY_MODEL=" not in env_text:
        errors.append("PRIMARY_MODEL must be environment-configurable")
    if re.search(r"^PRIMARY_MODEL=\S+", env_text, flags=re.MULTILINE):
        errors.append("PRIMARY_MODEL must not be hard-coded in .env.example")
    if "FALLBACK_MODEL=deepseek-v4-flash" not in env_text:
        errors.append("fallback model default missing from .env.example")

# Production blockers are expected, but should be centralized and explicit.
for path in markdown_files:
    rel = str(path.relative_to(ROOT))
    text = path.read_text(encoding="utf-8")
    if "TBD" in text and rel not in {
        "README.md",
        "docs/00-INDEX.md",
        "docs/01-PRD.md",
        "docs/11-DEPLOYMENT.md",
        "docs/15-OPEN-QUESTIONS.md",
    }:
        warnings.append(f"TBD outside blocker documents: {rel}")

print(f"checked {len(markdown_files)} markdown files and {len(REQUIRED)} required artifacts")
for warning in sorted(set(warnings)):
    print(f"WARN: {warning}")
if errors:
    for error in sorted(set(errors)):
        print(f"ERROR: {error}")
    sys.exit(1)
print("documentation validation: PASS")
