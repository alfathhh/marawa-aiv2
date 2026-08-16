"""Dashboard/API paths exercised against a real PostgreSQL store.

These tests exist because the in-memory Store can accidentally expose dict
operations that the production PostgresStore does not support. Every frontend
mutation in this file must cross FastAPI -> PostgresStore -> PostgreSQL.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.app import app, get_store
from scripts.notifications import InMemoryChannel
from scripts.outbox_worker import SendRecord, SendStatus
from scripts.postgres_store import PostgresStore

DSN = os.environ.get("MARAWA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MARAWA_TEST_DSN not set")


ADMIN_ID = "e2e-superadmin"


@pytest.fixture()
def db_client(monkeypatch):
    import psycopg

    monkeypatch.delenv("MARAWA_ENV", raising=False)
    monkeypatch.setenv("MARAWA_INTERNAL_KEY", "e2e-internal-key")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO marawa_admins (admin_id, name, role, active) "
            "VALUES (%s,'E2E Superadmin','superadmin',true) "
            "ON CONFLICT (admin_id) DO NOTHING",
            (ADMIN_ID,),
        )
        defaults = {
            "citizen_idle_minutes": 5,
            "queue_expiry_minutes": 240,
            "handover_auto_revert_minutes": 30,
            "queue_notify_repeat_minutes": 5,
        }
        for key, value in defaults.items():
            cur.execute(
                "INSERT INTO marawa_settings (key,value,updated_by) "
                "VALUES (%s,%s::jsonb,'test') ON CONFLICT (key) DO NOTHING",
                (key, str(value)),
            )
    store = PostgresStore(DSN, InMemoryChannel())
    store.webhook_secret = None
    app.dependency_overrides[get_store] = lambda: store
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def headers():
    return {"X-Admin-Id": ADMIN_ID}


def test_dashboard_handover_reply_and_idempotency_use_postgres(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    store.get_conversation(cid)

    take = client.post(
        f"/conversations/{cid}/handover/on",
        json={"expected_version": 0}, headers=headers(),
    )
    assert take.status_code == 200, take.text
    version = take.json()["state_version"]

    request_id = uuid.uuid4().hex
    reply = client.post(
        f"/conversations/{cid}/messages",
        json={
            "body": "Baik, kami bantu cek.",
            "expected_version": version,
            "client_request_id": request_id,
        },
        headers=headers(),
    )
    assert reply.status_code == 200, reply.text
    assert store.get_conversation(cid).state_version == version
    assert store.get_conversation(cid).bot_paused_by == ADMIN_ID
    assert any(m["body"] == "Baik, kami bantu cek." for m in store.messages(cid))

    duplicate = client.post(
        f"/conversations/{cid}/messages",
        json={
            "body": "Baik, kami bantu cek.",
            "expected_version": version,
            "client_request_id": request_id,
        },
        headers=headers(),
    )
    assert duplicate.status_code == 409
    assert store.get_conversation(cid).state_version == version


def test_dashboard_settings_and_global_switch_persist_in_postgres(db_client):
    client, store = db_client
    current = client.get("/settings/timeouts", headers=headers())
    assert current.status_code == 200

    values = dict(current.json())
    values["queue_notify_repeat_minutes"] = 7
    saved = client.put(
        "/settings/timeouts", json={"values": values}, headers=headers(),
    )
    assert saved.status_code == 200, saved.text
    assert store.settings["queue_notify_repeat_minutes"] == 7

    disabled = client.post(
        "/settings/bot-global-switch?enabled=false&reason=e2e-test",
        headers=headers(),
    )
    assert disabled.status_code == 200, disabled.text
    assert store.global_switch.enabled is False

    enabled = client.post(
        "/settings/bot-global-switch?enabled=true&reason=e2e-cleanup",
        headers=headers(),
    )
    assert enabled.status_code == 200, enabled.text
    assert store.global_switch.enabled is True


def test_duplicate_inbound_does_not_advance_postgres_state_twice(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    wa_id = f"wa_{uuid.uuid4().hex}"
    payload = {
        "conversation_id": cid,
        "wa_message_id": wa_id,
        "from_me": False,
        "body": "Berapa jumlah penduduk?",
        "timestamp": "2026-08-16T12:00:00+00:00",
        "admin_id": None,
    }
    first = client.post("/webhook/whatsapp", json=payload)
    assert first.status_code == 200, first.text
    version = store.get_conversation(cid).state_version

    second = client.post("/webhook/whatsapp", json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "ignored_duplicate"
    assert store.get_conversation(cid).state_version == version
    assert len(store.messages(cid)) == 1


def _inbound(cid: str, wa_id: str, *, from_me: bool = False, body: str = "halo"):
    return {
        "conversation_id": cid, "wa_message_id": wa_id,
        "from_me": from_me, "body": body,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "admin_id": ADMIN_ID if from_me else None,
    }


def test_concurrent_inbound_starts_exactly_one_agent_run(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    store.get_conversation(cid)
    payloads = [_inbound(cid, f"wa_{uuid.uuid4().hex}", body=f"pesan {i}") for i in range(2)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda p: client.post("/webhook/whatsapp", json=p), payloads))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    decisions = [r.json()["run_agent"] for r in responses]
    assert decisions.count(True) == 1, [r.json() for r in responses]
    assert decisions.count(False) == 1
    assert len(store.messages(cid)) == 2
    assert store.get_conversation(cid).agent_run_active is True


def test_manual_phone_takeover_is_atomic_and_cancels_pending_and_claimed_bot(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    store.get_conversation(cid)
    for _ in range(2):
        store.enqueue_outbox(SendRecord(
            str(uuid.uuid4()), cid, "jawaban bot", "bot", 0,
            idempotency_key=f"bot_{uuid.uuid4().hex}",
        ))
    assert len(store.claim_outbox_batch("audit-worker", limit=1)) == 1

    payload = _inbound(cid, f"wa_{uuid.uuid4().hex}", from_me=True, body="Saya tangani manual")
    first = client.post("/webhook/whatsapp", json=payload)
    second = client.post("/webhook/whatsapp", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "human_takeover_recorded"
    assert second.json()["status"] == "ignored_duplicate"
    assert len(store.messages(cid)) == 1
    assert store.messages(cid)[0]["body"] == "Saya tangani manual"

    import psycopg
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM marawa_outbox WHERE conversation_id=%s", (cid,))
        assert {row[0] for row in cur.fetchall()} == {"cancelled"}
        cur.execute(
            "SELECT count(*) FROM marawa_audit_log "
            "WHERE conversation_id=%s AND action='phone_takeover_detected'",
            (cid,),
        )
        assert cur.fetchone()[0] == 1


def test_final_outbox_gate_cancels_stale_admin_reply(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    store.get_conversation(cid)
    take = client.post(
        f"/conversations/{cid}/handover/on",
        json={"expected_version": 0}, headers=headers(),
    )
    version = take.json()["state_version"]
    sent = client.post(
        f"/conversations/{cid}/messages",
        json={"body": "jawaban", "expected_version": version,
              "client_request_id": uuid.uuid4().hex},
        headers=headers(),
    )
    assert sent.status_code == 200, sent.text
    claimed = store.claim_outbox_batch("worker-gate", limit=10000)
    record = next(
        r for r in claimed
        if r.sender_type == "admin" and r.conversation_id == cid
    )

    released = client.post(
        f"/conversations/{cid}/handover/off",
        json={"expected_version": version}, headers=headers(),
    )
    assert released.status_code == 200, released.text
    allowed, reason = store.authorize_claimed_outbox(record.outbox_id)
    assert allowed is False
    assert reason == "state_changed_since_enqueue"
    gate = client.post(
        "/internal/outbox/authorize",
        json={"outbox_id": record.outbox_id},
        headers={"X-Internal-Key": "e2e-internal-key"},
    )
    assert gate.status_code == 200
    assert gate.json()["allowed"] is False


def test_sweep_endpoint_uses_postgres_store(db_client):
    client, store = db_client
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    store.get_conversation(cid)
    import psycopg
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE marawa_conversations SET last_activity_at=%s, "
            "agent_run_active=false WHERE conversation_id=%s",
            (old, cid),
        )
    swept = client.post("/internal/sweep", headers=headers())
    assert swept.status_code == 200, swept.text
    assert any(item["conversation_id"] == cid for item in swept.json()["applied"])
    assert store.get_conversation(cid).state.value == "IDLE_CLOSED"


def test_superadmin_creates_login_capable_admin_in_postgres(db_client, monkeypatch):
    client, _store = db_client
    import psycopg
    admin_id = f"petugas-{uuid.uuid4().hex[:10]}"
    password = "layanan-aman-2026"
    try:
        created = client.post(
            "/admin/accounts", headers=headers(),
            json={
                "admin_id": admin_id, "name": "Petugas Pelayanan E2E",
                "role": "admin", "password": password,
            },
        )
        assert created.status_code == 201, created.text
        listed = client.get("/admin/accounts", headers=headers())
        row = next(item for item in listed.json() if item["admin_id"] == admin_id)
        assert row["name"] == "Petugas Pelayanan E2E"
        assert "password_hash" not in row

        # Switch from dev header auth to the same signed-session gate production
        # uses, then prove the freshly stored hash is login-capable.
        monkeypatch.setenv("MARAWA_SESSION_KEY", "e2e-session-key-that-is-long-enough")
        login = client.post(
            "/admin/login",
            json={"admin_id": admin_id, "password": password, "totp_code": None},
        )
        assert login.status_code == 200, login.text
        assert login.json()["role"] == "admin"
        forbidden = client.get(
            "/admin/accounts",
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )
        assert forbidden.status_code == 403
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM marawa_audit_log WHERE admin_id=%s OR detail->>'target_admin_id'=%s", (admin_id, admin_id))
            cur.execute("DELETE FROM marawa_admins WHERE admin_id=%s", (admin_id,))
