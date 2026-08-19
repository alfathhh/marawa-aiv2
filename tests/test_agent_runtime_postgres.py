"""E2E: agent runtime terhadap PostgreSQL asli (MARAWA_TEST_DSN).

Membuktikan rantai yang gagal di produksi:
pesan masuk → agent_run_active → AgentRuntime → outbox bot → transcript →
flag tuntas. Menjalankan hanya bila MARAWA_TEST_DSN tersedia.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MARAWA_TEST_DSN"), reason="butuh MARAWA_TEST_DSN (PostgreSQL asli)"
)

from scripts.agent_runtime import AgentRuntime, StaticLLM  # noqa: E402
from scripts.postgres_store import PostgresStore  # noqa: E402
from scripts.outbox_worker import SendStatus  # noqa: E402


@pytest.fixture()
def store() -> PostgresStore:
    s = PostgresStore(os.environ["MARAWA_TEST_DSN"], notification_channel=None)
    yield s


def _new_conv(store: PostgresStore) -> tuple[str, int]:
    cid = f"628{uuid.uuid4().int % 10**10:010d}@s.whatsapp.net"
    conv = store.get_conversation(cid)  # BOT_ACTIVE v0
    # simulasi webhook: simpan pesan + nyalakan run flag (satu transaksi nyata)
    store.persist_inbound_transition(
        conv, conv, direction="in", sender_type="user",
        body="berapa jumlah penduduk Padang Pariaman?",
        wa_message_id=f"w-{uuid.uuid4()}",
    )
    # naikkan flag run via SQL langsung (meniru efek run_agent state machine)
    import psycopg
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE marawa_conversations SET agent_run_active=true, state_version=state_version+1 "
            "WHERE conversation_id=%s RETURNING state_version",
            (cid,),
        )
        version = cur.fetchone()["state_version"]
    return cid, version


def test_agent_runtime_end_to_end_postgres(store: PostgresStore) -> None:
    _enable_bot(store)
    cid, version = _new_conv(store)
    runtime = AgentRuntime(store=store, llm=StaticLLM("Sebentar saya cekakan dulu datanya."))
    processed = runtime.process_pending(limit=5)
    assert processed == 1

    # outbox berisi balasan bot
    import psycopg
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sender_type, status, body FROM marawa_outbox "
            "WHERE conversation_id=%s ORDER BY created_at DESC LIMIT 1",
            (cid,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row["sender_type"] == "bot"
        assert row["status"] == SendStatus.PENDING.value
        assert row["body"] == "Sebentar saya cekakan dulu datanya."

        # flag tuntas
        cur.execute(
            "SELECT agent_run_active FROM marawa_conversations WHERE conversation_id=%s",
            (cid,),
        )
        assert cur.fetchone()["agent_run_active"] is False

        # transcript berisi balasan bot
        cur.execute(
            "SELECT count(*) FROM marawa_messages WHERE conversation_id=%s "
            "AND direction='out' AND sender_type='bot'",
            (cid,),
        )
        assert cur.fetchone()["count"] == 1
    _cleanup(store, cid)


def test_agent_runtime_idempotent_after_crash(store: PostgresStore) -> None:
    _enable_bot(store)
    cid, version = _new_conv(store)
    runtime = AgentRuntime(store=store, llm=StaticLLM("Jawaban pertama."))
    assert runtime.process_pending(limit=5) == 1
    # run kedua: flag sudah false → tidak diproses lagi
    assert runtime.process_pending(limit=5) == 0

    import psycopg
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM marawa_outbox WHERE conversation_id=%s AND sender_type='bot'",
            (cid,),
        )
        assert cur.fetchone()["count"] == 1
    _cleanup(store, cid)


def _enable_bot(store: PostgresStore) -> None:
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO marawa_settings (key, value) VALUES ('bot_global_switch', %s::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=%s::jsonb",
            ('{"enabled": true}', '{"enabled": true}')
        )


def _cleanup(store: PostgresStore, cid: str) -> None:
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM marawa_outbox WHERE conversation_id=%s", (cid,))
        cur.execute("DELETE FROM marawa_messages WHERE conversation_id=%s", (cid,))
        cur.execute("DELETE FROM marawa_conversations WHERE conversation_id=%s", (cid,))
