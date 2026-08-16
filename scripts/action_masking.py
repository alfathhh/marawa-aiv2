#!/usr/bin/env python3
"""Session-policy action masking (docs/27 — conversational control states).

TEMUAN dari eval LLM live 16-Agu: model memilih action bebas dari 19 opsi tanpa
papan kandidat/session state → 4/19 action-only. Solusinya bukan prompt yang
lebih panjang — solusinya server membatasi action yang VALID untuk state saat
ini (AGENTS.md #17: trusted control plane). Modul ini adalah reference
implementasi masking + state transition; runtime engine akan memanggil fungsi
yang sama, dan eval `--mode llm` memakai mask ini per turn.
"""
from __future__ import annotations

from typing import Any

# State dipakai dari conversation_state.State (BOT_ACTIVE/QUEUED/ADMIN_ACTIVE/
# IDLE_CLOSED). Flag tambahan `has_selected` melacak kandidat yang sudah
# dikunci user — tanpa itu, query sebelum pilih tidak bisa dicegah server-side.

# Action yang boleh dipilih model di tiap state.
# query_actions hanya muncul SETELAH user mengunci kandidat (has_selected).
QUERY_ACTIONS = {
    "query_stat_data",
    "query_and_compare",
    "analyze_existing_result",
    "create_artifact",
}

DISCOVERY_ACTIONS = {
    "show_service_menu",
    "offer_candidates",
    "offer_candidate_clusters",
    "clarify",
    "inspect_dataset",
    "resolve_or_clarify_candidates",
    "candidate_page",
    "compare_sources",
    "rerank_candidates",
    "resolve_candidate",
    "service_fallback_offer",
    "request_admin_handover",
    "end_session",
}

# Engine/event-only actions — model tidak pernah memilihnya sendiri.
ENGINE_ACTIONS = {"admin_busy_notice", "admin_handover_outcome"}


def allowed_actions(state: str, has_selected: bool = False) -> list[str]:
    """Action yang boleh dipilih model pada state+flag ini (urutan stabil)."""
    base = {"BOT_ACTIVE": DISCOVERY_ACTIONS,
            "QUEUED": {
                "offer_candidates", "clarify", "show_service_menu",
                "request_admin_handover", "end_session",
            },
            "ADMIN_ACTIVE": {"show_service_menu", "end_session"},
            "IDLE_CLOSED": {"show_service_menu", "offer_candidates"}}
    allowed = set(base.get(state, DISCOVERY_ACTIONS))
    if state == "BOT_ACTIVE" and has_selected:
        allowed |= QUERY_ACTIONS
    # engine actions tidak pernah jadi opsi model
    allowed -= ENGINE_ACTIONS
    return sorted(allowed)


def apply_action(state: str, action: str, has_selected: bool = False) -> tuple[str, bool]:
    """(new_state, new_has_selected) setelah action; mask harus dicek dulu."""
    if action == "end_session":
        return "IDLE_CLOSED", False
    if action == "request_admin_handover":
        return "QUEUED", has_selected
    if action == "resolve_candidate":
        return "BOT_ACTIVE", True
    if action in QUERY_ACTIONS:
        return "BOT_ACTIVE", True
    if state in ("QUEUED", "ADMIN_ACTIVE", "IDLE_CLOSED") and action in (
        "show_service_menu", "offer_candidates", "clarify",
    ):
        # cancel natural / sesi baru → kembali ke agent aktif
        return "BOT_ACTIVE", False
    return "BOT_ACTIVE", has_selected


def describe(state: str, has_selected: bool) -> str:
    """Instruksi state untuk prompt (bukan daftar mentah)."""
    if state == "QUEUED":
        return (
            "User sedang dalam ANTREAN petugas. Bot tidak boleh membalas "
            "layanan; yang boleh: cancel (keluar/menu/arah baru) atau tetap "
            "di antrean."
        )
    if state == "ADMIN_ACTIVE":
        return "Petugas sedang menangani percakapan. Bot DIAM total; hanya cancel/keluar."
    if state == "IDLE_CLOSED":
        return "Sesi lama sudah ditutup (timeout). Pesan ini = sesi baru."
    if has_selected:
        return "Kandidat SUDAH dipilih user — query fakta boleh dilakukan."
    return "Belum ada kandidat dipilih — DILARANG query fakta."


def mask_prompt_line(state: str, has_selected: bool) -> str:
    """Baris prompt per turn: daftar action valid + larangan kontekstual."""
    actions = ", ".join(allowed_actions(state, has_selected))
    return (
        f"STATE SAAT INI: {describe(state, has_selected)}\n"
        f"ACTION VALID SAAT INI (pilih salah satu): {actions}"
    )
