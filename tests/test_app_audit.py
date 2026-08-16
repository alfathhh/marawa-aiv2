"""SELF-AUDIT of scripts/app.py — tests written to break the wiring.

Every test here was written by asking "what would a hostile or unlucky caller
do?" rather than "does the happy path work?".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.app import Store, app, get_store

ADMIN = {"X-Admin-Id": "seed-super-1"}


@pytest.fixture()
def client():
    store = Store()
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), store
    app.dependency_overrides.clear()


def _inbound(cid="c1", wa="wa_1", from_me=False, body="halo", ts=None):
    return {
        "conversation_id": cid, "wa_message_id": wa, "from_me": from_me,
        "body": body, "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        "admin_id": None,
    }


# ---------------------------------------------------------------------------
# AUDIT F — the version guard is defeated by a non-atomic write
# ---------------------------------------------------------------------------

def test_store_write_is_compare_and_swap_not_last_write_wins():
    """AUDIT F: the version guard is inside apply(); the write is separate.

    NOTE ON METHOD: an earlier version of this test drove two threads through
    TestClient and passed — falsely. TestClient serialises requests through one
    portal, so the interleaving never happened and the green result meant
    nothing. Exercising Store directly is what actually reproduces it.
    """
    from scripts.conversation_state import Event, apply

    store = Store()
    store.get_conversation("c1")
    now = datetime.now(timezone.utc)

    snapshot_a = store.get_conversation("c1")
    snapshot_b = store.get_conversation("c1")
    version = snapshot_a.state_version

    transition_a = apply(snapshot_a, Event.HANDOVER_ON, now, admin_id="budi", expected_version=version)
    transition_b = apply(snapshot_b, Event.HANDOVER_ON, now, admin_id="sari", expected_version=version)

    assert store.compare_and_set(version, transition_a.state) is True
    assert store.compare_and_set(version, transition_b.state) is False, (
        "second writer overwrote the first; both officers would be told they hold it"
    )
    assert store.conversations["c1"].bot_paused_by == "budi"


def test_concurrent_toggle_requests_resolve_to_one_winner(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    store.admins["officer-b"] = {"name": "B", "role": "admin"}
    version = store.conversations["c1"].state_version

    results: list[int] = []
    for admin_id in ("seed-super-1", "officer-b"):
        response = http.post(
            "/conversations/c1/handover/on",
            json={"expected_version": version},
            headers={"X-Admin-Id": admin_id},
        )
        results.append(response.status_code)
    assert sorted(results) == [200, 409]


# ---------------------------------------------------------------------------
# AUDIT G — internal endpoints leak citizen data to anyone who can reach them
# ---------------------------------------------------------------------------

def test_internal_endpoints_require_authentication(client):
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound(body="pertanyaan warga"))

    assert http.post("/internal/sweep").status_code in (401, 403)
    leak = http.get("/internal/notifications")
    assert leak.status_code in (401, 403), (
        "unauthenticated caller can read conversation ids and message text"
    )


# ---------------------------------------------------------------------------
# AUDIT H — a legitimate repeated reply is rejected as a duplicate
# ---------------------------------------------------------------------------

def test_officer_can_send_the_same_short_message_twice(client):
    """"ok" typed twice in a row is normal human behaviour, not a duplicate.

    The idempotency key is body+conversation+state_version, and ADMIN_REPLY
    does not bump the version, so the second genuine send collides with the
    first and is refused.
    """
    http, store = client
    http.post("/webhook/whatsapp", json=_inbound())
    v = store.conversations["c1"].state_version
    http.post("/conversations/c1/handover/on", json={"expected_version": v}, headers=ADMIN)
    v = store.conversations["c1"].state_version

    first = http.post("/conversations/c1/messages",
                      json={"body": "ok", "expected_version": v}, headers=ADMIN)
    second = http.post("/conversations/c1/messages",
                       json={"body": "ok", "expected_version": v}, headers=ADMIN)
    assert first.status_code == 200
    assert second.status_code == 200, "a genuine repeated message must not be swallowed"
    assert first.json()["outbox_id"] != second.json()["outbox_id"]


# ---------------------------------------------------------------------------
# AUDIT I — naive vs aware datetime crashes the webhook
# ---------------------------------------------------------------------------

def test_timestamp_without_timezone_does_not_crash_the_webhook(client):
    """WhatsApp bridges are not guaranteed to send an offset. Comparing a naive
    timestamp against an aware pairing cutoff raises TypeError deep inside the
    handler and returns a 500 to the bridge, which will then retry forever.
    """
    http, store = client
    store.pairing_cutoff_ts = datetime.now(timezone.utc) - timedelta(days=1)
    naive = datetime.now().replace(microsecond=0).isoformat()  # no offset
    response = http.post("/webhook/whatsapp", json=_inbound(ts=None) | {"timestamp": naive})
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# AUDIT J — the signature header is accepted and never checked
# ---------------------------------------------------------------------------

def test_webhook_rejects_unsigned_requests_when_a_secret_is_configured(client):
    """`x_webhook_signature` is declared in the handler signature, which makes
    the endpoint LOOK authenticated in review while verifying nothing.
    """
    http, store = client
    store.webhook_secret = "s3cret"
    response = http.post("/webhook/whatsapp", json=_inbound())
    assert response.status_code in (401, 403), (
        "unsigned webhook accepted while a secret is configured"
    )
