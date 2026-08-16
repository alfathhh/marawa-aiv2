"""Tests for the sweep and notification wiring.

Without a sweep, IDLE_TIMEOUT and AUTO_REVERT_CHECK are dead code. Without
notification wiring, "notify_officers" in an effects list is just a string
nobody acts on. This file proves both actually fire.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from scripts.app import Store, app, get_store
from scripts.conversation_state import (
    ConversationState, Event, Settings, State, apply, should_notify_officers,
)
from scripts.notifications import InMemoryChannel, dispatch_effects
from scripts.scheduler import (
    due_for_auto_revert, due_for_idle_timeout, plan_sweep,
)

NOW = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
SUPER = {"X-Admin-Id": "seed-super-1"}


def test_idle_timeout_is_not_due_before_the_threshold():
    conv = ConversationState("c1", state=State.BOT_ACTIVE, last_activity_at=NOW)
    assert not due_for_idle_timeout(conv, NOW + timedelta(minutes=4), Settings())
    assert due_for_idle_timeout(conv, NOW + timedelta(minutes=6), Settings())


def test_auto_revert_is_not_due_while_admin_recently_active():
    conv = ConversationState(
        "c1", state=State.ADMIN_ACTIVE, assigned_admin_id="budi",
        bot_paused_by="budi", last_admin_activity_at=NOW,
    )
    assert not due_for_auto_revert(conv, NOW + timedelta(minutes=29), Settings())
    assert due_for_auto_revert(conv, NOW + timedelta(minutes=31), Settings())


def test_plan_sweep_skips_conversations_that_need_nothing():
    fresh = ConversationState("fresh", state=State.BOT_ACTIVE, last_activity_at=NOW)
    plan = plan_sweep([fresh], NOW + timedelta(minutes=1), Settings())
    assert plan == []


def test_plan_sweep_finds_both_kinds_of_due_conversation():
    idle = ConversationState("idle1", state=State.BOT_ACTIVE, last_activity_at=NOW)
    stuck_handover = ConversationState(
        "stuck1", state=State.ADMIN_ACTIVE, assigned_admin_id="budi",
        bot_paused_by="budi", last_admin_activity_at=NOW,
    )
    plan = plan_sweep([idle, stuck_handover], NOW + timedelta(hours=1), Settings())
    reasons = {(item.conversation_id, item.event) for item in plan}
    assert ("idle1", "idle_timeout") in reasons
    assert ("stuck1", "auto_revert_check") in reasons


# --------------------------- notification dispatch ---------------------------

def test_queue_notice_reaches_the_officer_channel():
    channel = InMemoryChannel()
    conv = ConversationState("c1", state=State.QUEUED, handover_requested_at=NOW)
    notified = dispatch_effects(["notify_officers"], conv, NOW, channel)
    assert notified
    assert "c1" in channel.officer_messages[0]


def test_debounced_notice_does_not_spam_the_channel():
    channel = InMemoryChannel()
    conv = ConversationState("c1", state=State.QUEUED, last_notified_at=NOW)
    notified = dispatch_effects(["notify_officers"], conv, NOW + timedelta(seconds=30), channel)
    assert not notified
    assert channel.officer_messages == []


def test_auto_revert_notice_is_distinct_from_queue_notice():
    channel = InMemoryChannel()
    conv = ConversationState("c1", state=State.BOT_ACTIVE)
    dispatch_effects(["notify_officers_auto_revert"], conv, NOW, channel)
    assert "otomatis dimatikan" in channel.officer_messages[0]


def test_citizen_gets_the_handover_notice_text_supplied_by_caller():
    channel = InMemoryChannel()
    conv = ConversationState("c1", state=State.QUEUED)
    dispatch_effects(
        ["send_handover_notice"], conv, NOW, channel,
        citizen_text_by_effect={"send_handover_notice": "Baik, saya hubungkan dengan petugas."},
    )
    assert channel.citizen_messages == [("c1", "Baik, saya hubungkan dengan petugas.")]


# --------------------------- end-to-end via HTTP ---------------------------

def test_sweep_endpoint_reverts_a_forgotten_toggle():
    store = Store()
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)

    conv = store.get_conversation("c1")
    stuck = apply(conv, Event.HANDOVER_ON, NOW, admin_id="seed-super-1").state
    store.conversations["c1"] = stuck

    # Force the clock forward by mutating the stored timestamp directly,
    # since the sweep endpoint uses wall-clock `now` internally.
    store.conversations["c1"] = ConversationState(
        **{**stuck.__dict__, "last_admin_activity_at": datetime.now(timezone.utc) - timedelta(minutes=40)}
    )

    response = client.post("/internal/sweep", headers=SUPER)
    assert response.status_code == 200
    body = response.json()
    assert any(a["conversation_id"] == "c1" and a["new_state"] == "BOT_ACTIVE" for a in body["applied"])

    notes = client.get("/internal/notifications", headers=SUPER).json()
    assert any("otomatis dimatikan" in m for m in notes["officer_messages"])
    app.dependency_overrides.clear()


def test_sweep_never_closes_an_actively_handled_conversation():
    store = Store()
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)

    conv = store.get_conversation("c1")
    active = apply(conv, Event.HANDOVER_ON, datetime.now(timezone.utc), admin_id="seed-super-1").state
    store.conversations["c1"] = active  # last_admin_activity_at is "now" — not due

    response = client.post("/internal/sweep", headers=SUPER)
    assert store.conversations["c1"].state.value == "ADMIN_ACTIVE"
    assert response.status_code == 200
    app.dependency_overrides.clear()


def test_queued_conversation_notifies_officers_via_webhook():
    store = Store()
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app)

    now = datetime.now(timezone.utc)
    inbound = {
        "conversation_id": "c1", "wa_message_id": "wa_1", "from_me": False,
        "body": "ADMIN", "timestamp": now.isoformat(), "admin_id": None,
    }
    client.post("/webhook/whatsapp", json=inbound)  # BOT_ACTIVE, agent would classify intent

    # Simulate the state machine already having moved to QUEUED (as it would
    # once the agent recognises a handover request) and a follow-up message
    # arriving while queued — this is the path that must notify officers.
    queued = apply(
        store.conversations["c1"], Event.REQUEST_HANDOVER, now,
    ).state
    store.conversations["c1"] = queued

    followup = {**inbound, "wa_message_id": "wa_2", "body": "masih ada?",
                "timestamp": (now + timedelta(seconds=5)).isoformat()}
    client.post("/webhook/whatsapp", json=followup)

    notes = client.get("/internal/notifications", headers=SUPER).json()
    assert any("c1" in m for m in notes["officer_messages"])
    app.dependency_overrides.clear()
