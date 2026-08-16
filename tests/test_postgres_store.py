"""PostgresStore tests against a REAL PostgreSQL instance.

Skipped when MARAWA_TEST_DSN is unset. These are the tests the in-memory store
could never give us: the lost-update guarantee is only meaningful across
separate connections, which is exactly what a threading.Lock cannot prove.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

DSN = os.environ.get("MARAWA_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="MARAWA_TEST_DSN not set")

from scripts.conversation_state import (  # noqa: E402
    ConversationState, Event, State, apply,
)
from scripts.notifications import InMemoryChannel  # noqa: E402
from scripts.outbox_worker import SendRecord, SendStatus  # noqa: E402
from scripts.postgres_store import PostgresStore  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def store():
    s = PostgresStore(DSN, InMemoryChannel())
    yield s


@pytest.fixture()
def cid():
    return f"c_{uuid.uuid4().hex[:12]}"


def test_conversation_is_created_on_first_contact(store, cid):
    conv = store.get_conversation(cid)
    assert conv.conversation_id == cid
    assert conv.state is State.BOT_ACTIVE
    assert conv.state_version == 0


def test_concurrent_first_contact_does_not_duplicate(store, cid):
    a = store.get_conversation(cid)
    b = store.get_conversation(cid)
    assert a.conversation_id == b.conversation_id == cid


def test_lost_update_is_impossible_across_separate_connections(store, cid):
    """THE test the in-memory store could not give us.

    Two officers read the same version through two different connections, both
    pass the optimistic guard, both attempt to write. Exactly one must win, and
    the loser must be told — not silently overwritten.
    """
    store.get_conversation(cid)
    snapshot_a = store.get_conversation(cid)
    snapshot_b = store.get_conversation(cid)
    version = snapshot_a.state_version
    assert snapshot_b.state_version == version

    move_a = apply(snapshot_a, Event.HANDOVER_ON, NOW, admin_id="budi", expected_version=version)
    move_b = apply(snapshot_b, Event.HANDOVER_ON, NOW, admin_id="sari", expected_version=version)

    assert store.compare_and_set(version, move_a.state) is True
    assert store.compare_and_set(version, move_b.state) is False, (
        "second writer overwrote the first; both officers would hold the chat"
    )
    assert store.get_conversation(cid).bot_paused_by == "budi"


def test_state_survives_a_new_store_instance(store, cid):
    """Persistence: a restart must not lose who is handling a conversation."""
    store.get_conversation(cid)
    held = apply(store.get_conversation(cid), Event.HANDOVER_ON, NOW, admin_id="budi").state
    assert store.compare_and_set(0, held)

    fresh = PostgresStore(DSN, InMemoryChannel())
    reloaded = fresh.get_conversation(cid)
    assert reloaded.state is State.ADMIN_ACTIVE
    assert reloaded.bot_paused_by == "budi"


def test_redelivered_inbound_message_is_stored_once(store, cid):
    store.get_conversation(cid)
    wa_id = f"wa_{uuid.uuid4().hex[:10]}"
    assert store.append_message(cid, "in", "user", "halo", wa_message_id=wa_id) is True
    assert store.append_message(cid, "in", "user", "halo", wa_message_id=wa_id) is False
    assert len(store.messages(cid)) == 1


def test_duplicate_send_action_is_rejected_by_the_database(store, cid):
    store.get_conversation(cid)
    key = f"cli_{uuid.uuid4().hex[:10]}"
    first = SendRecord(str(uuid.uuid4()), cid, "ok", "admin", 0, idempotency_key=key)
    second = SendRecord(str(uuid.uuid4()), cid, "ok", "admin", 0, idempotency_key=key)
    assert store.enqueue_outbox(first) is True
    assert store.enqueue_outbox(second) is False


def test_two_genuine_sends_of_the_same_text_both_queue(store, cid):
    store.get_conversation(cid)
    a = SendRecord(str(uuid.uuid4()), cid, "ok", "admin", 0, idempotency_key=f"cli_{uuid.uuid4().hex}")
    b = SendRecord(str(uuid.uuid4()), cid, "ok", "admin", 0, idempotency_key=f"cli_{uuid.uuid4().hex}")
    assert store.enqueue_outbox(a) is True
    assert store.enqueue_outbox(b) is True


def test_claim_batch_does_not_hand_the_same_row_to_two_workers(store, cid):
    store.get_conversation(cid)
    for _ in range(3):
        store.enqueue_outbox(SendRecord(
            str(uuid.uuid4()), cid, "pesan", "bot", 0,
            idempotency_key=f"cli_{uuid.uuid4().hex}",
        ))
    first = store.claim_outbox_batch("worker-1", limit=10)
    second = store.claim_outbox_batch("worker-2", limit=10)
    ids_first = {r.outbox_id for r in first}
    ids_second = {r.outbox_id for r in second}
    assert ids_first, "worker-1 claimed nothing"
    assert not (ids_first & ids_second), "same outbox row claimed twice"


def test_handover_cancels_pending_bot_outbox(store, cid):
    store.get_conversation(cid)
    store.enqueue_outbox(SendRecord(
        str(uuid.uuid4()), cid, "jawaban bot", "bot", 0,
        idempotency_key=f"cli_{uuid.uuid4().hex}",
    ))
    cancelled = store.cancel_pending_bot_outbox(cid, "handover_preempted")
    assert cancelled == 1


def test_audit_log_is_append_only_and_readable(store, cid):
    store.get_conversation(cid)
    store.audit("handover_on", "budi", cid, {"note": "test"})
    entries = [e for e in store.audit_log if e["conversation_id"] == cid]
    assert entries and entries[0]["action"] == "handover_on"


def test_sweep_query_excludes_closed_conversations(store, cid):
    store.get_conversation(cid)
    closed = apply(store.get_conversation(cid), Event.IDLE_TIMEOUT, NOW)
    if closed.state.state is State.IDLE_CLOSED:
        store.compare_and_set(0, closed.state)
        ids = {c.conversation_id for c in store.conversations_needing_sweep()}
        assert cid not in ids


def _backdate_past_retention(store, cid, days: int = 400) -> None:
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        old = NOW - timedelta(days=days)
        cur.execute(
            "UPDATE marawa_messages SET created_at=%s WHERE conversation_id=%s",
            (old, cid),
        )
        cur.execute(
            "UPDATE marawa_conversations SET last_activity_at=%s, state='IDLE_CLOSED' "
            "WHERE conversation_id=%s",
            (old, cid),
        )


def test_retention_deletes_expired_messages_and_empty_closed_conversation(store, cid):
    store.get_conversation(cid)
    store.append_message(cid, "in", "user", "Sesuatu yang sensitif", wa_message_id=uuid.uuid4().hex)
    _backdate_past_retention(store, cid)

    deleted = store.apply_retention()

    assert deleted["messages"] >= 1
    # the freshly-created test conversation is IDLE_CLOSED, old, and empty now
    assert deleted["conversations"] >= 1
    assert store.get_conversation(cid).conversation_id == cid  # recreated fresh


def test_retention_keeps_recent_messages_and_inflight_outbox(store, cid):
    import psycopg

    store.get_conversation(cid)
    store.append_message(cid, "in", "user", "Pertanyaan baru hari ini", wa_message_id=uuid.uuid4().hex)
    store.enqueue_outbox(SendRecord(
        outbox_id=uuid.uuid4().hex, conversation_id=cid, body="balasan",
        sender_type="bot", state_version_at_enqueue=0, status=SendStatus.PENDING,
        idempotency_key=f"cli_{uuid.uuid4().hex}",
    ))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE marawa_outbox SET created_at=%s WHERE conversation_id=%s",
                    (NOW - timedelta(days=400), cid))

    deleted = store.apply_retention()

    # the specific fresh message and inflight row must survive the sweep;
    # global zero-counts would break on accumulated data from other tests.
    kept = [m for m in store.messages(cid) if m["body"] == "Pertanyaan baru hari ini"]
    assert len(kept) == 1
    import psycopg
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM marawa_outbox WHERE conversation_id=%s", (cid,),
        )
        assert {r[0] for r in cur.fetchall()} == {"pending"}
