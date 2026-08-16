from __future__ import annotations

import pytest

from scripts.action_masking import (
    allowed_actions,
    apply_action,
    mask_prompt_line,
)


def test_query_actions_locked_until_selection() -> None:
    before = allowed_actions("BOT_ACTIVE", has_selected=False)
    assert "query_stat_data" not in before
    after = allowed_actions("BOT_ACTIVE", has_selected=True)
    assert "query_stat_data" in after
    assert "query_and_compare" in after


def test_queue_state_limits_bot_replies() -> None:
    queued = set(allowed_actions("QUEUED"))
    assert "offer_candidates" in queued  # cancel → arah baru
    assert "show_service_menu" in queued
    assert "end_session" in queued
    assert "query_stat_data" not in queued
    assert "admin_busy_notice" not in queued  # engine-only, never model option


def test_admin_active_mutes_bot() -> None:
    admin = set(allowed_actions("ADMIN_ACTIVE"))
    assert admin == {"end_session", "show_service_menu"}


def test_engine_actions_never_exposed() -> None:
    for state in ("BOT_ACTIVE", "QUEUED", "ADMIN_ACTIVE", "IDLE_CLOSED"):
        assert "admin_busy_notice" not in allowed_actions(state)
        assert "admin_handover_outcome" not in allowed_actions(state)


def test_transitions() -> None:
    assert apply_action("BOT_ACTIVE", "request_admin_handover") == ("QUEUED", False)
    assert apply_action("BOT_ACTIVE", "resolve_candidate") == ("BOT_ACTIVE", True)
    assert apply_action("BOT_ACTIVE", "query_stat_data", has_selected=True) == ("BOT_ACTIVE", True)
    assert apply_action("BOT_ACTIVE", "end_session") == ("IDLE_CLOSED", False)
    # natural cancel dari antrean → kembali ke agent
    assert apply_action("QUEUED", "offer_candidates") == ("BOT_ACTIVE", False)
    assert apply_action("ADMIN_ACTIVE", "show_service_menu") == ("BOT_ACTIVE", False)


def test_mask_prompt_line_mentions_valid_actions() -> None:
    line = mask_prompt_line("QUEUED", False)
    assert "ANTREAN" in line
    assert "end_session" in line
    assert "ACTION VALID" in line
