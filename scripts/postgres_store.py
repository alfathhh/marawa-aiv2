#!/usr/bin/env python3
"""PostgreSQL-backed conversation store.

Drop-in replacement for the in-memory `Store` in scripts/app.py. The route
handlers do not change; only the object passed to `get_store()` does.

WHY THIS EXISTS (and it is not mainly persistence)
--------------------------------------------------
The in-memory store guarded `compare_and_set()` with a `threading.Lock`. That
closes the lost-update race (audit F) for ONE process and no further. Run two
uvicorn workers and each holds its own lock, so both can read state_version 3,
both pass the guard, and both write version 4 — two officers each told they
hold the conversation, and the loser never learns otherwise.

Here the guarantee comes from the database:

    UPDATE marawa_conversations
       SET ... , state_version = state_version + 1
     WHERE conversation_id = %s AND state_version = %s

`cur.rowcount == 0` means someone else moved first. That holds across processes,
across machines, and across restarts. Same guarantee as the lock, different
mechanism — and the existing tests do not change, which is the point.
"""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from scripts.conversation_state import ConversationState, GlobalBotSwitch, State
from scripts.outbox_worker import SendRecord, SendStatus, WorkerHealth

CONVERSATION_COLUMNS = (
    "conversation_id", "state", "state_version", "assigned_admin_id",
    "bot_paused_by", "bot_paused_at", "last_admin_activity_at",
    "last_activity_at", "handover_requested_at", "resume_watermark_at",
    "last_notified_at", "agent_run_active",
)

_STATE_FIELDS = {f.name for f in fields(ConversationState)}


def _row_to_state(row: dict[str, Any]) -> ConversationState:
    data = {k: v for k, v in row.items() if k in _STATE_FIELDS}
    data["state"] = State(row["state"])
    return ConversationState(**data)


class PostgresStore:
    """Same surface as scripts.app.Store, backed by PostgreSQL."""

    def __init__(self, dsn: str, notification_channel: Any) -> None:
        self.dsn = dsn
        self.notification_channel = notification_channel
        self.webhook_secret: str | None = None
        self.pairing_cutoff_ts: datetime | None = None
        self.worker_health = WorkerHealth(connected=True)
        self._global_switch = GlobalBotSwitch()

    # -- connection ------------------------------------------------------

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    # -- conversations ---------------------------------------------------

    def get_conversation(self, conversation_id: str) -> ConversationState:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CONVERSATION_COLUMNS)} "
                "FROM marawa_conversations WHERE conversation_id = %s",
                (conversation_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return _row_to_state(row)
            # First contact. ON CONFLICT DO NOTHING keeps two simultaneous
            # inbound messages from racing each other into a duplicate insert.
            cur.execute(
                "INSERT INTO marawa_conversations (conversation_id, wa_contact_hash) "
                "VALUES (%s, %s) ON CONFLICT (conversation_id) DO NOTHING",
                (conversation_id, conversation_id),
            )
            cur.execute(
                f"SELECT {', '.join(CONVERSATION_COLUMNS)} "
                "FROM marawa_conversations WHERE conversation_id = %s",
                (conversation_id,),
            )
            return _row_to_state(cur.fetchone())

    def compare_and_set(self, expected_version: int, new_state: ConversationState) -> bool:
        """Atomic guarded write. False means someone else moved first.

        The version in the WHERE clause is what makes this safe; do not be
        tempted to read-then-write in two statements, which is exactly the bug
        this replaces.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE marawa_conversations SET
                    state = %s,
                    state_version = %s,
                    assigned_admin_id = %s,
                    bot_paused_by = %s,
                    bot_paused_at = %s,
                    last_admin_activity_at = %s,
                    last_activity_at = %s,
                    handover_requested_at = %s,
                    resume_watermark_at = %s,
                    last_notified_at = %s,
                    agent_run_active = %s
                WHERE conversation_id = %s AND state_version = %s
                """,
                (
                    new_state.state.value, new_state.state_version,
                    new_state.assigned_admin_id, new_state.bot_paused_by,
                    new_state.bot_paused_at, new_state.last_admin_activity_at,
                    new_state.last_activity_at, new_state.handover_requested_at,
                    new_state.resume_watermark_at, new_state.last_notified_at,
                    new_state.agent_run_active,
                    new_state.conversation_id, expected_version,
                ),
            )
            return cur.rowcount == 1

    def list_conversations(self, limit: int = 100) -> list[ConversationState]:
        """Kotak masuk petugas.

        `is_staff_channel` dikecualikan: thread notifikasi ke nomor petugas
        bukan warga yang menunggu, dan kalau ikut tampil ia menenggelamkan
        orang yang benar-benar butuh dibantu — persis kebalikan dari tugas
        papan triase ini.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CONVERSATION_COLUMNS)} FROM marawa_conversations "
                "WHERE NOT is_staff_channel "
                "ORDER BY last_activity_at DESC NULLS LAST LIMIT %s",
                (limit,),
            )
            return [_row_to_state(r) for r in cur.fetchall()]

    def conversations_needing_sweep(self) -> list[ConversationState]:
        """Only rows a timeout could possibly apply to.

        Sweeping every conversation ever created would grow linearly forever;
        an idle-closed row from six months ago can never need another event.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(CONVERSATION_COLUMNS)} FROM marawa_conversations "
                "WHERE state <> 'IDLE_CLOSED' AND NOT is_staff_channel"
            )
            return [_row_to_state(r) for r in cur.fetchall()]

    # -- messages --------------------------------------------------------

    def append_message(
        self, conversation_id: str, direction: str, sender_type: str, body: str,
        wa_message_id: str | None = None, sender_admin_id: str | None = None,
    ) -> bool:
        """Returns False when the message was already stored (redelivery)."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO marawa_messages "
                "(conversation_id, direction, sender_type, sender_admin_id, body, wa_message_id) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (wa_message_id) WHERE wa_message_id IS NOT NULL DO NOTHING",
                (conversation_id, direction, sender_type, sender_admin_id, body, wa_message_id),
            )
            return cur.rowcount == 1

    def messages(self, conversation_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT direction, sender_type, sender_admin_id, body, created_at "
                "FROM marawa_messages WHERE conversation_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (conversation_id, limit),
            )
            return list(reversed(cur.fetchall()))

    # -- outbox ----------------------------------------------------------

    def enqueue_outbox(self, record: SendRecord) -> bool:
        """False means this exact send action was already queued (audit H)."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO marawa_outbox (outbox_id, conversation_id, body, sender_type, "
                "sender_admin_id, state_version_at_enqueue, status, idempotency_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING",
                (record.outbox_id, record.conversation_id, record.body, record.sender_type,
                 record.sender_admin_id, record.state_version_at_enqueue,
                 record.status.value, record.idempotency_key),
            )
            return cur.rowcount == 1

    def claim_outbox_batch(self, worker_id: str, limit: int = 10) -> list[SendRecord]:
        """Lease pending rows.

        `FOR UPDATE SKIP LOCKED` is what lets several workers drain the same
        queue without stepping on each other or serialising behind one slow row.
        """
        now = datetime.now(timezone.utc)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH claimable AS (
                    SELECT outbox_id FROM marawa_outbox
                    WHERE (status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= %s))
                       OR (status = 'claimed' AND claimed_at < %s - interval '120 seconds')
                    ORDER BY created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE marawa_outbox o
                   SET status = 'claimed', claimed_at = %s, claimed_by = %s
                  FROM claimable c
                 WHERE o.outbox_id = c.outbox_id
             RETURNING o.outbox_id, o.conversation_id, o.body, o.sender_type,
                       o.sender_admin_id, o.state_version_at_enqueue, o.status,
                       o.attempts, o.wa_message_id, o.idempotency_key, o.last_error
                """,
                (now, now, limit, now, worker_id),
            )
            return [
                SendRecord(
                    outbox_id=r["outbox_id"], conversation_id=r["conversation_id"],
                    body=r["body"], sender_type=r["sender_type"],
                    sender_admin_id=r["sender_admin_id"],
                    state_version_at_enqueue=r["state_version_at_enqueue"],
                    status=SendStatus(r["status"]), attempts=r["attempts"],
                    wa_message_id=r["wa_message_id"],
                    idempotency_key=r["idempotency_key"], last_error=r["last_error"],
                )
                for r in cur.fetchall()
            ]

    def update_outbox(self, record: SendRecord) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE marawa_outbox SET status=%s, attempts=%s, next_attempt_at=%s, "
                "wa_message_id=%s, last_error=%s, claimed_at=NULL, claimed_by=NULL "
                "WHERE outbox_id=%s",
                (record.status.value, record.attempts, record.next_attempt_at,
                 record.wa_message_id, record.last_error, record.outbox_id),
            )

    def cancel_pending_bot_outbox(self, conversation_id: str, reason: str) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE marawa_outbox SET status='cancelled', last_error=%s "
                "WHERE conversation_id=%s AND sender_type='bot' AND status='pending'",
                (reason, conversation_id),
            )
            return cur.rowcount

    def sent_wa_ids(self) -> set[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT wa_message_id FROM marawa_outbox WHERE wa_message_id IS NOT NULL"
            )
            return {r["wa_message_id"] for r in cur.fetchall()}

    # -- admins, settings, audit ----------------------------------------

    def get_admin(self, admin_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT admin_id, name, role FROM marawa_admins "
                "WHERE admin_id=%s AND active",
                (admin_id,),
            )
            return cur.fetchone()

    @property
    def admins(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT admin_id, name, role FROM marawa_admins WHERE active")
            return {r["admin_id"]: r for r in cur.fetchall()}

    @property
    def settings(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT key, value FROM marawa_settings")
            return {r["key"]: r["value"] for r in cur.fetchall()}

    def update_settings(self, values: dict[str, int], admin_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            for key, value in values.items():
                cur.execute(
                    "INSERT INTO marawa_settings (key, value, updated_by) VALUES (%s,%s,%s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, "
                    "updated_by=EXCLUDED.updated_by, updated_at=now()",
                    (key, Jsonb(value), admin_id),
                )

    @property
    def global_switch(self) -> GlobalBotSwitch:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM marawa_settings WHERE key='bot_global_switch'")
            row = cur.fetchone()
            if row is None:
                return GlobalBotSwitch()
            data = row["value"]
            return GlobalBotSwitch(
                enabled=data.get("enabled", True),
                disabled_by=data.get("disabled_by"),
                reason=data.get("reason"),
            )

    def set_global_switch(self, enabled: bool, admin_id: str, reason: str | None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO marawa_settings (key, value, updated_by) VALUES "
                "('bot_global_switch', %s, %s) ON CONFLICT (key) DO UPDATE SET "
                "value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()",
                (Jsonb({"enabled": enabled, "disabled_by": admin_id, "reason": reason}), admin_id),
            )

    def audit(
        self, action: str, admin_id: str | None,
        conversation_id: str | None, detail: dict[str, Any],
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO marawa_audit_log (action, admin_id, conversation_id, detail) "
                "VALUES (%s,%s,%s,%s)",
                (action, admin_id, conversation_id, Jsonb(detail)),
            )

    # -- TOTP secrets ----------------------------------------------------------

    def totp_secret_for(self, admin_id: str) -> str | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT totp_secret FROM marawa_admins WHERE admin_id=%s AND active",
                (admin_id,),
            )
            row = cur.fetchone()
            return row["totp_secret"] if row else None

    def set_totp_secret(self, admin_id: str, secret: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE marawa_admins SET totp_secret=%s WHERE admin_id=%s",
                (secret, admin_id),
            )

    # -- retention (365-day policy, 16 Aug 2026 decision; docs/15 note) --------

    def apply_retention(self, retention_days: int = 365) -> dict[str, int]:
        """Delete raw chat data older than the retention window.

        Returns deleted-row counts per table. Deliberately conservative:
        - messages: raw transcripts (PII) past the window;
        - outbox: terminal rows past the window (pending/claimed never deleted —
          they may still be in flight);
        - conversations: IDLE_CLOSED conversations past the window that have no
          remaining messages (the contact hash lives here; erase only when the
          conversation is done AND empty);
        - audit log: NOT touched. Append-only by grant stays true; operational
          audit volume is small and its retention is a separate policy question.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted: dict[str, int] = {}
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM marawa_messages WHERE created_at < %s",
                (cutoff,),
            )
            deleted["messages"] = cur.rowcount
            cur.execute(
                "DELETE FROM marawa_outbox WHERE created_at < %s "
                "AND status IN ('sent','delivered','failed','cancelled')",
                (cutoff,),
            )
            deleted["outbox"] = cur.rowcount
            cur.execute(
                "DELETE FROM marawa_conversations c WHERE state = 'IDLE_CLOSED' "
                "AND last_activity_at < %s "
                "AND NOT EXISTS (SELECT 1 FROM marawa_messages m "
                "                 WHERE m.conversation_id = c.conversation_id) "
                "AND NOT EXISTS (SELECT 1 FROM marawa_outbox o "
                "                 WHERE o.conversation_id = c.conversation_id)",
                (cutoff,),
            )
            deleted["conversations"] = cur.rowcount
        return deleted

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT audit_id, at, action, admin_id, conversation_id, detail "
                "FROM marawa_audit_log ORDER BY at DESC LIMIT 500"
            )
            return cur.fetchall()

    # -- kontak petugas (migrasi 009) ------------------------------------

    def admin_contacts(self, only_notify: bool = False) -> list[dict[str, Any]]:
        clause = "WHERE active" + (" AND notify" if only_notify else "")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT contact_id, phone_e164, label, admin_id, notify, "
                "blocked_from_bot, active, created_at "
                f"FROM marawa_admin_contacts {clause} ORDER BY label"
            )
            return cur.fetchall()

    def add_admin_contact(
        self, phone_e164: str, label: str, created_by: str,
        admin_id: str | None = None, notify: bool = True,
    ) -> bool:
        """False bila nomor sudah terdaftar."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO marawa_admin_contacts "
                "(phone_e164, label, admin_id, notify, created_by) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (phone_e164) DO UPDATE SET "
                "label=EXCLUDED.label, admin_id=EXCLUDED.admin_id, "
                "notify=EXCLUDED.notify, active=true, created_by=EXCLUDED.created_by "
                "WHERE NOT marawa_admin_contacts.active",
                (phone_e164, label, admin_id, notify, created_by),
            )
            return cur.rowcount == 1

    def remove_admin_contact(self, contact_id: int) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE marawa_admin_contacts SET active = false WHERE contact_id = %s",
                (contact_id,),
            )
            return cur.rowcount == 1

    def blocked_phones(self) -> set[str]:
        """Nomor yang tidak boleh dilayani bot. Dibaca tiap pesan masuk, jadi
        di-index; kalau nanti jadi hambatan, cache dengan TTL pendek — jangan
        cache tanpa kedaluwarsa, karena nomor yang baru dihapus harus segera
        bisa memakai bot lagi."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT phone_e164 FROM marawa_admin_contacts "
                "WHERE active AND blocked_from_bot"
            )
            return {r["phone_e164"] for r in cur.fetchall()}

    def mark_staff_channel(self, conversation_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE marawa_conversations SET is_staff_channel = true "
                "WHERE conversation_id = %s",
                (conversation_id,),
            )
