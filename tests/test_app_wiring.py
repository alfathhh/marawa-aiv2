"""End-to-end wiring tests using FastAPI's TestClient.

No network, no DB, no LLM: exercises the real HTTP routes against the
in-memory Store, so the seams between modules (state machine <-> outbox
<-> gate) are checked together instead of only in isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.app import Store, _build_store, app, get_store


def test_build_store_switches_on_runtime_dsn(monkeypatch) -> None:
    """MARAWA_RUNTIME_DSN selects PostgresStore; without it, in-memory.

    Construction only — PostgresStore opens connections lazily, so no DB is
    needed for this check."""
    from scripts.postgres_store import PostgresStore

    monkeypatch.delenv("MARAWA_RUNTIME_DSN", raising=False)
    assert type(_build_store()) is Store
    monkeypatch.setenv("MARAWA_RUNTIME_DSN", "dbname=unused_construction_only")
    assert type(_build_store()) is PostgresStore


@pytest.fixture()
def client():
    store = Store()
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store
    app.dependency_overrides.clear()


ADMIN = {"X-Admin-Id": "seed-super-1"}


def _inbound(conversation_id="c1", body="halo", from_me=False, wa_id="wa_1", admin_id=None):
    return {
        "conversation_id": conversation_id,
        "wa_message_id": wa_id,
        "from_me": from_me,
        "body": body,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "admin_id": admin_id,
    }


def test_inbound_message_is_stored_and_runs_agent(client):
    http, store = client
    response = http.post("/webhook/whatsapp", json=_inbound())
    assert response.status_code == 200
    assert response.json()["run_agent"] is True
    assert store.conversations["c1"].state.value == "BOT_ACTIVE"


def test_unauthenticated_dashboard_request_is_rejected(client):
    http, _store = client
    response = http.get("/conversations")
    assert response.status_code == 401


def test_full_handover_cycle_via_http(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    conv = store.conversations["c1"]

    on = http.post(
        f"/conversations/c1/handover/on",
        json={"expected_version": conv.state_version},
        headers=ADMIN,
    )
    assert on.status_code == 200
    assert on.json()["state"] == "ADMIN_ACTIVE"

    reply = http.post(
        "/conversations/c1/messages",
        json={"body": "Halo, ini petugas PST.", "expected_version": on.json()["state_version"]},
        headers=ADMIN,
    )
    assert reply.status_code == 200
    assert reply.json()["status"] == "pending"

    conv_after_reply = http.get("/conversations/c1", headers=ADMIN).json()["conversation"]
    off = http.post(
        "/conversations/c1/handover/off",
        json={"expected_version": conv_after_reply["state_version"]},
        headers=ADMIN,
    )
    assert off.status_code == 200
    assert off.json()["state"] == "BOT_ACTIVE"


def test_stale_version_conflict_returns_409_not_500(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    response = http.post(
        "/conversations/c1/handover/on", json={"expected_version": 999}, headers=ADMIN,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONVERSATION_STATE_CONFLICT"


def test_two_admins_racing_to_toggle_only_one_wins(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    conv = store.conversations["c1"]
    store.admins["officer-b"] = {"name": "Officer B", "role": "admin"}

    first = http.post(
        "/conversations/c1/handover/on",
        json={"expected_version": conv.state_version}, headers=ADMIN,
    )
    second = http.post(
        "/conversations/c1/handover/on",
        json={"expected_version": conv.state_version},
        headers={"X-Admin-Id": "officer-b"},
    )
    assert first.status_code == 200
    assert second.status_code == 409


def test_admin_reply_is_blocked_before_toggling_on(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    conv = store.conversations["c1"]
    response = http.post(
        "/conversations/c1/messages",
        json={"body": "halo", "expected_version": conv.state_version},
        headers=ADMIN,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "HANDOVER_NOT_ON"


def test_phone_takeover_via_webhook_pauses_the_bot(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    response = http.post("/webhook/whatsapp", json=_inbound(from_me=True, wa_id="wa_officer"))
    assert response.json()["status"] == "human_takeover_recorded"
    assert store.conversations["c1"].state.value == "ADMIN_ACTIVE"


def test_our_own_outbound_echo_is_not_treated_as_takeover(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    store.sent_wa_ids.add("wa_ours")
    response = http.post("/webhook/whatsapp", json=_inbound(from_me=True, wa_id="wa_ours"))
    assert response.json()["status"] == "ignored_own_echo"
    assert store.conversations["c1"].state.value == "BOT_ACTIVE"


def test_settings_out_of_bounds_are_rejected_at_the_http_boundary(client):
    http, _store = client
    response = http.put(
        "/settings/timeouts", json={"values": {"citizen_idle_minutes": 999}}, headers=ADMIN,
    )
    assert response.status_code == 422


def test_regular_admin_cannot_reach_superadmin_settings(client):
    http, store = client
    store.admins["officer-b"] = {"name": "Officer B", "role": "admin"}
    response = http.get("/settings/timeouts", headers={"X-Admin-Id": "officer-b"})
    assert response.status_code == 403


def test_agent_settings_endpoint_never_offers_a_gate_bypass(client):
    http, _store = client
    response = http.get("/settings/agent", headers=ADMIN)
    body = response.json()
    assert "answer_gate.enabled" not in body["editable"]
    assert "answer_gate.enabled" in body["never_editable"]


def test_global_kill_switch_reaches_the_status_endpoint(client):
    http, _store = client
    http.post("/settings/bot-global-switch?enabled=false&reason=maintenance", headers=ADMIN)
    status = http.get("/status/whatsapp", headers=ADMIN)
    assert status.json()["bot_globally_enabled"] is False


def test_whatsapp_status_visible_to_regular_admin_too(client):
    http, store = client
    store.admins["officer-b"] = {"name": "Officer B", "role": "admin"}
    response = http.get("/status/whatsapp", headers={"X-Admin-Id": "officer-b"})
    assert response.status_code == 200


def test_audit_log_hidden_from_regular_admin(client):
    http, store = client
    store.admins["officer-b"] = {"name": "Officer B", "role": "admin"}
    response = http.get("/audit-log", headers={"X-Admin-Id": "officer-b"})
    assert response.status_code == 403


def test_retry_of_the_same_send_action_is_deduplicated(client):
    """Dedup keys on the client's request id — "the same click" — not on the
    message text. See AUDIT H: keying on text meant an officer typing "ok"
    twice had the second one silently swallowed.
    """
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    conv = store.conversations["c1"]
    http.post("/conversations/c1/handover/on",
              json={"expected_version": conv.state_version}, headers=ADMIN)
    v = store.conversations["c1"].state_version

    payload = {"body": "baik pak", "expected_version": v, "client_request_id": "req-1"}
    first = http.post("/conversations/c1/messages", json=payload, headers=ADMIN)
    retry = http.post("/conversations/c1/messages", json=payload, headers=ADMIN)
    assert first.status_code == 200
    assert retry.status_code == 409, "a retry of the same click must not double-send"


def test_two_genuine_sends_of_identical_text_both_go_through(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    conv = store.conversations["c1"]
    http.post("/conversations/c1/handover/on",
              json={"expected_version": conv.state_version}, headers=ADMIN)
    v = store.conversations["c1"].state_version

    a = http.post("/conversations/c1/messages",
                  json={"body": "ok", "expected_version": v, "client_request_id": "req-a"},
                  headers=ADMIN)
    b = http.post("/conversations/c1/messages",
                  json={"body": "ok", "expected_version": v, "client_request_id": "req-b"},
                  headers=ADMIN)
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["outbox_id"] != b.json()["outbox_id"]
