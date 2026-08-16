#!/usr/bin/env python3
"""FastAPI wiring — ties conversation_state, outbox_worker, answer_gate, and
answer_formatter into one process with real endpoints.

WHAT THIS IS NOT
-----------------
No Baileys, no PostgreSQL, no LLM call. Storage is an in-memory repository
behind a small interface (`Store`), so this can be tested and demonstrated end
to end today, and swapped for real Postgres + Baileys without touching the
route handlers. That swap is deliberately the next piece of work, not this one.

Endpoints match docs/06 §0.4 for the dashboard and docs/07 for the webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from scripts.answer_gate import (
    Evidence,
    GateContext,
    NoDataReason,
    abstention_text,
    evaluate,
    safe_response,
)
from scripts.conversation_state import (
    ConversationState,
    Event,
    GlobalBotSwitch,
    OutboxEntry,
    Rejected,
    State,
    apply,
    authorize_send,
    classify_from_me,
    handover_notice,
    is_within_office_hours,
    order_send_queue,
    should_notify_officers,
    should_run_agent,
    should_ignore_inbound,
    validate_settings,
)
from scripts.conversation_state import Settings as TimeoutSettings
from scripts.outbox_worker import (
    SendRecord,
    SendStatus,
    WorkerHealth,
    idempotency_key,
    is_duplicate_send,
)
from scripts.scheduler import plan_sweep
from scripts.notifications import InMemoryChannel, build_channel, dispatch_effects
from scripts.postgres_store import PostgresStore
from scripts.phone import InvalidPhone, normalize_phone, display_phone
from scripts.password_auth import hash_password, verify_password
from scripts.rate_limit import LOGIN_LIMITER
from scripts.totp_session import issue_session, new_totp_secret, otpauth_uri, session_key_stable, verify_session, verify_totp

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Storage interface — swap this, not the routes, for real Postgres
# ---------------------------------------------------------------------------


class Store:
    """In-memory repository. Single-process only; replace with Postgres before
    running more than one worker or surviving a restart."""

    def __init__(self) -> None:
        self.conversations: dict[str, ConversationState] = {}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.outbox: dict[str, SendRecord] = {}
        self.sent_wa_ids: set[str] = set()
        self.admins: dict[str, dict[str, Any]] = {
            "seed-super-1": {"name": "Superadmin Satu", "role": "superadmin", "password_hash": None},
            "seed-super-2": {"name": "Superadmin Dua", "role": "superadmin", "password_hash": None},
        }
        self.settings: dict[str, int] = {
            "citizen_idle_minutes": 5,
            "queue_expiry_minutes": 240,
            "handover_auto_revert_minutes": 30,
            "queue_notify_repeat_minutes": 5,
        }
        self.runtime_settings: dict[str, Any] = {}
        self.global_switch = GlobalBotSwitch()
        self.pairing_cutoff_ts: datetime | None = None
        self.audit_log: list[dict[str, Any]] = []
        self.totp_secrets: dict[str, str] = {}
        self.worker_health = WorkerHealth(connected=True)
        # Swap for a real WhatsApp-sending channel before production; see
        # scripts/notifications.py. Kept in-memory here so the sweep and
        # notification wiring can be demonstrated and tested without Baileys.
        # Produksi memakai OutboxChannel (kirim sungguhan lewat antrean yang
        # sama dengan pesan warga); dev/tes memakai penampung in-memory.
        self.notification_channel = build_channel(self)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # AUDIT V: this used to be hardcoded None and never populated from the
        # environment, so `_verify_webhook_signature` took its "no secret
        # configured" early-return on EVERY request and the endpoint accepted
        # anything that could reach the port. Loading it here is what makes the
        # AUDIT J fix actually reachable in production.
        self.webhook_secret: str | None = os.environ.get("MARAWA_WEBHOOK_SECRET") or None

    def get_conversation(self, conversation_id: str) -> ConversationState:
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = ConversationState(conversation_id=conversation_id)
        return self.conversations[conversation_id]

    def lock_for(self, conversation_id: str) -> threading.Lock:
        """AUDIT F: the optimistic version check lives inside apply(), but the
        write back to the dict is a SEPARATE statement. Two requests could both
        read version 3, both pass the guard, and both write version 4 — a lost
        update where two officers each believe they hold the conversation, and
        the loser is never told.

        Read-check-write must be one critical section. Postgres will do this
        with `UPDATE ... WHERE state_version = %s` and a rowcount check; this
        in-memory store needs an explicit lock to match that guarantee.
        """
        with self._locks_guard:
            return self._locks.setdefault(conversation_id, threading.Lock())

    def compare_and_set(self, expected_version: int, new_state: ConversationState) -> bool:
        current = self.conversations.get(new_state.conversation_id)
        if current is not None and current.state_version != expected_version:
            return False
        self.conversations[new_state.conversation_id] = new_state
        return True

    def append_message(
        self, conversation_id: str, direction: str, sender_type: str, body: str,
        wa_message_id: str | None = None, sender_admin_id: str | None = None,
    ) -> bool:
        if wa_message_id and self.has_message_id(wa_message_id):
            return False
        self.messages.setdefault(conversation_id, []).append({
            "direction": direction, "sender_type": sender_type,
            "sender_admin_id": sender_admin_id, "body": body,
            "wa_message_id": wa_message_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return True

    def has_message_id(self, wa_message_id: str) -> bool:
        return any(
            message.get("wa_message_id") == wa_message_id
            for messages in self.messages.values() for message in messages
        )

    def persist_inbound_transition(
        self, expected: ConversationState, new_state: ConversationState,
        *, direction: str, sender_type: str, body: str,
        wa_message_id: str, sender_admin_id: str | None = None,
        cancel_bot_outbox: bool = False,
    ) -> str:
        with self.lock_for(expected.conversation_id):
            if self.has_message_id(wa_message_id):
                return "duplicate"
            current = self.conversations.get(expected.conversation_id)
            if (
                current is None
                or current.state_version != expected.state_version
                or current.agent_run_active != expected.agent_run_active
            ):
                return "conflict"
            self.conversations[expected.conversation_id] = new_state
            self.messages.setdefault(expected.conversation_id, []).append({
                "direction": direction, "sender_type": sender_type,
                "sender_admin_id": sender_admin_id, "body": body,
                "wa_message_id": wa_message_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            if cancel_bot_outbox:
                self.cancel_pending_bot_outbox(
                    expected.conversation_id, "handover_preempted"
                )
            return "persisted"

    def enqueue_outbox(self, record: SendRecord) -> bool:
        if record.idempotency_key and any(
            item.idempotency_key == record.idempotency_key
            for item in self.outbox.values()
        ):
            return False
        self.outbox[record.outbox_id] = record
        return True

    def outbox_has_idempotency_key(self, key: str) -> bool:
        return any(item.idempotency_key == key for item in self.outbox.values())

    def persist_admin_reply(
        self, expected_version: int, new_state: ConversationState,
        record: SendRecord,
    ) -> str:
        if self.outbox_has_idempotency_key(record.idempotency_key or ""):
            return "duplicate"
        if not self.compare_and_set(expected_version, new_state):
            return "conflict"
        if not self.enqueue_outbox(record):
            return "duplicate"
        self.append_message(
            record.conversation_id, "out", "admin", record.body,
            sender_admin_id=record.sender_admin_id,
        )
        return "enqueued"

    def cancel_pending_bot_outbox(self, conversation_id: str, reason: str) -> int:
        count = 0
        for record in self.outbox.values():
            if (
                record.conversation_id == conversation_id
                and record.sender_type == "bot"
                and record.status in (SendStatus.PENDING, SendStatus.CLAIMED)
            ):
                record.status = SendStatus.CANCELLED
                record.last_error = reason
                count += 1
        return count

    def mark_notified(
        self, conversation_id: str, expected_version: int, at: datetime,
    ) -> bool:
        current = self.conversations.get(conversation_id)
        if current is None or current.state_version != expected_version:
            return False
        self.conversations[conversation_id] = ConversationState(
            **{**current.__dict__, "last_notified_at": at}
        )
        return True

    def pending_outbox_count(self) -> int:
        return sum(
            1 for record in self.outbox.values()
            if record.status in (SendStatus.PENDING, SendStatus.CLAIMED)
        )

    def conversations_needing_sweep(self) -> list[ConversationState]:
        return [
            state for state in self.conversations.values()
            if state.state is not State.IDLE_CLOSED
        ]

    def update_settings(self, values: dict[str, int], admin_id: str) -> None:
        self.settings.update(values)

    def set_global_switch(
        self, enabled: bool, admin_id: str, reason: str | None,
    ) -> None:
        self.global_switch = GlobalBotSwitch(
            enabled=enabled, disabled_by=None if enabled else admin_id,
            disabled_at=None if enabled else datetime.now(timezone.utc),
            reason=reason,
        )

    def audit(self, action: str, admin_id: str | None, conversation_id: str | None, detail: dict) -> None:
        # Append-only. No route in this app ever deletes from this list —
        # see docs/06 §3.0b. A real deployment enforces this with a DB grant.
        self.audit_log.append({
            "id": str(uuid.uuid4()),
            "at": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "admin_id": admin_id,
            "conversation_id": conversation_id,
            "detail": detail,
        })


    def totp_secret_for(self, admin_id: str) -> str | None:
        return self.totp_secrets.get(admin_id)

    def set_totp_secret(self, admin_id: str, secret: str) -> None:
        self.totp_secrets[admin_id] = secret

    def set_admin_password(self, admin_id: str, password_hash: str) -> bool:
        admin = self.admins.get(admin_id)
        if admin is None:
            return False
        admin["password_hash"] = password_hash
        return True

    def list_admin_accounts(self) -> list[dict[str, Any]]:
        return [
            {
                "admin_id": admin_id, "name": row["name"],
                "role": row["role"], "active": True,
            }
            for admin_id, row in sorted(self.admins.items())
        ]

    def create_admin_account(
        self, admin_id: str, name: str, role: str, password_hash: str,
    ) -> bool:
        if admin_id in self.admins:
            return False
        self.admins[admin_id] = {
            "name": name, "role": role, "password_hash": password_hash,
        }
        return True

    def set_setting(self, key: str, value: Any) -> None:
        self.runtime_settings[key] = value

    def get_setting(self, key: str) -> Any:
        return self.runtime_settings.get(key)


def _build_store() -> Store:
    """PostgresStore when MARAWA_RUNTIME_DSN is set; in-memory otherwise.

    The PostgreSQL store gives compare-and-swap that holds across processes and
    survives restart (audit F); the in-memory store stays the default for
    development and the existing wiring tests. Neither branch opens a
    connection here — PostgresStore connects lazily per operation.
    """
    dsn = os.environ.get("MARAWA_RUNTIME_DSN")
    if dsn:
        store = PostgresStore(dsn, notification_channel=InMemoryChannel())
        # build_channel needs the store itself. Construct first, then wire the
        # production fanout adapter; otherwise deployed Postgres remains on
        # InMemoryChannel even though Store() selects FanoutChannel.
        store.notification_channel = build_channel(store)
        store.webhook_secret = os.environ.get("MARAWA_WEBHOOK_SECRET") or None
        return store
    return Store()


STORE = _build_store()


def get_store() -> Store:
    return STORE


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="MARAWA AI — Runtime Wiring")


@app.on_event("startup")
def fail_closed_production_startup() -> None:
    """A production service with missing auth/webhook secrets must not boot."""
    problems = assert_production_config()
    if problems:
        raise RuntimeError("Konfigurasi produksi tidak aman: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Auth — TOTP + sesi (docs/06 §4); header X-Admin-Id hanya mode dev
# ---------------------------------------------------------------------------


class AdminIdentity(BaseModel):
    admin_id: str
    role: str


def is_production() -> bool:
    """Mode produksi ditentukan EKSPLISIT, bukan disimpulkan dari ada/tidaknya
    sebuah env var.

    AUDIT W: sebelumnya mode dev aktif kapan pun `MARAWA_SESSION_KEY` tidak ada.
    Artinya kalau env file systemd gagal dimuat di produksi, aplikasi DIAM-DIAM
    turun ke menerima header `X-Admin-Id` — yaitu tanpa autentikasi sama sekali.
    Salah konfigurasi harus gagal-tertutup dan berisik, bukan gagal-terbuka dan
    senyap.
    """
    return os.environ.get("MARAWA_ENV", "").lower() in ("production", "prod")


def assert_production_config() -> list[str]:
    """Prasyarat yang wajib ada sebelum melayani lalu lintas nyata."""
    problems: list[str] = []
    if not is_production():
        return problems
    if not session_key_stable():
        problems.append("MARAWA_SESSION_KEY wajib di-set di produksi (sesi tidak boleh acak per boot)")
    if not os.environ.get("MARAWA_WEBHOOK_SECRET"):
        problems.append("MARAWA_WEBHOOK_SECRET wajib di-set di produksi (webhook tanpa verifikasi)")
    return problems


def _dev_header_mode() -> bool:
    """Header `X-Admin-Id` hanya boleh hidup di luar produksi."""
    return not is_production() and not session_key_stable()


def current_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_admin_id: str | None = Header(default=None, alias="X-Admin-Id"),
    store: Store = Depends(get_store),
) -> AdminIdentity:
    # Auth failures — missing OR invalid — return 401 uniformly.
    if authorization and authorization.startswith("Bearer "):
        admin_id = verify_session(authorization.removeprefix("Bearer ").strip())
        if admin_id is None:
            raise HTTPException(401, "Sesi tidak valid atau kedaluwarsa")
        return AdminIdentity(admin_id=admin_id, role=_role_of(admin_id, store))
    if _dev_header_mode() and x_admin_id is not None:
        admin = store.admins.get(x_admin_id)
        if admin is None:
            raise HTTPException(401, "Admin tidak dikenal")
        return AdminIdentity(admin_id=x_admin_id, role=admin["role"])
    raise HTTPException(401, "Autentikasi diperlukan")


def _role_of(admin_id: str, store: Store) -> str:
    admin = store.admins.get(admin_id)
    if admin is None:
        raise HTTPException(401, "Admin tidak dikenal")
    return admin["role"]


def require_superadmin(admin: AdminIdentity = Depends(current_admin)) -> AdminIdentity:
    if admin.role != "superadmin":
        raise HTTPException(403, "Hanya superadmin")
    return admin


class LoginRequest(BaseModel):
    admin_id: str
    password: str
    totp_code: str | None = None  # opsional bila admin ter-enroll TOTP


@app.post("/admin/login")
def admin_login(
    body: LoginRequest,
    request: Request,
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    if not session_key_stable():
        raise HTTPException(503, "Sesi dinonaktifkan: set MARAWA_SESSION_KEY")

    # AUDIT X: dibatasi per akun DAN per IP. Per akun saja bisa disapu dari
    # banyak IP; per IP saja membiarkan satu penyerang menggilir banyak akun.
    client_ip = request.client.host if request.client else "unknown"
    keys = (f"admin:{body.admin_id}", f"ip:{client_ip}")
    for key in keys:
        allowed, retry_after = LOGIN_LIMITER.check(key)
        if not allowed:
            store.audit("admin_login_ratelimited", body.admin_id, None, {"key": key})
            raise HTTPException(
                429, f"Terlalu banyak percobaan. Coba lagi dalam {retry_after} detik.",
                headers={"Retry-After": str(retry_after)},
            )

    def _fail(status: int, detail: str) -> HTTPException:
        for key in keys:
            LOGIN_LIMITER.record_failure(key)
        # Pesan gagal sengaja tidak membedakan "admin tidak ada" dari "password
        # salah": membedakannya memberi penyerang cara mengetahui admin_id mana
        # yang nyata sebelum mulai menebak password.
        return HTTPException(status, detail)

    admin = store.admins.get(body.admin_id)
    if admin is None:
        raise _fail(401, "Kredensial tidak valid")

    # Jalur utama: password (permintaan operator 16-Agt-2026). Admin yang
    # belum punya hash -> login ditolak dengan pesan seragam.
    stored = admin.get("password_hash")
    if not stored:
        raise _fail(401, "Kredensial tidak valid")
    if not verify_password(body.password, stored):
        raise _fail(401, "Kredensial tidak valid")

    # Lintasan kedua (opsional): TOTP tetap didukung bila ter-enroll DAN kode
    # diberikan. Tanpa kode, password cukup — bukan multi-faktor wajib.
    secret = store.totp_secret_for(body.admin_id)
    if secret is not None and body.totp_code is not None:
        if not verify_totp(secret, body.totp_code):
            raise _fail(401, "Kredensial tidak valid")

    for key in keys:
        LOGIN_LIMITER.record_success(key)
    store.audit("admin_login", body.admin_id, None, {"via": "totp", "ip": client_ip})
    return {"token": issue_session(body.admin_id), "admin_id": body.admin_id, "role": admin["role"]}


class EnrollRequest(BaseModel):
    admin_id: str


@app.post("/admin/enroll-totp")
def enroll_totp(
    body: EnrollRequest,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    if store.admins.get(body.admin_id) is None:
        raise HTTPException(404, "Admin tidak dikenal")
    secret = new_totp_secret()
    store.set_totp_secret(body.admin_id, secret)
    store.audit("admin_totp_enroll", admin.admin_id, None, {"for_admin": body.admin_id})
    return {"otpauth_uri": otpauth_uri(secret, "MARAWA-BPS", body.admin_id)}


@app.get("/admin/session")
def admin_session(admin: AdminIdentity = Depends(current_admin)) -> dict[str, Any]:
    return {"admin_id": admin.admin_id, "role": admin.role}


@app.get("/admin")
def admin_dashboard() -> FileResponse:
    """Panel admin satu-file (apps/dashboard/index.html); auth via Bearer di API."""
    return FileResponse(ROOT / "apps" / "dashboard" / "index.html")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@app.exception_handler(Rejected)
async def rejected_handler(_request: Request, exc: Rejected) -> JSONResponse:
    return JSONResponse(status_code=409, content={"code": exc.code, "message": str(exc)})


# ------------------------------- Webhook (docs/07) --------------------------


class InboundMessage(BaseModel):
    conversation_id: str
    wa_message_id: str
    from_me: bool
    body: str
    timestamp: datetime
    admin_id: str | None = None  # only meaningful when from_me


def _is_staff_number(store: Store, conversation_id: str) -> bool:
    """Apakah pengirim ini nomor petugas?

    Perbandingan SELALU lewat normalize_phone di kedua sisi. Membandingkan
    string mentah adalah cara daftar ini berhenti bekerja tanpa gejala: nomor
    tersimpan `08123`, WhatsApp mengirim `628123`, tidak cocok, tidak ada error.

    FAIL-CLOSED (audit STAFF-005): store yang MENGIKLANKAN dukungan blokir
    (`blocked_phones` ada) tapi gagal saat dipanggil → exception menjalar ke
    webhook dan menjadi 503. Petugas yang tidak sengaja dilayani karena lookup
    error lebih berbahaya daripada webhook sebentar 503. Store dev tanpa
    method (kembalian None dari getattr) → False, itu bukan kegagalan operasi.
    """
    getter = getattr(store, "blocked_phones", None)
    if getter is None:
        return False
    blocked = getter()  # biarkan exception propagate -> webhook 503
    if not blocked:
        return False
    try:
        return normalize_phone(conversation_id) in blocked
    except InvalidPhone:
        return False


def _mark_staff(store: Store, conversation_id: str) -> None:
    marker = getattr(store, "mark_staff_channel", None)
    if marker is not None:
        try:
            marker(conversation_id)
        except Exception:  # noqa: BLE001
            pass


def _as_aware(value: datetime) -> datetime:
    """AUDIT I: a WhatsApp bridge is not guaranteed to send an offset.

    Comparing a naive timestamp against an aware pairing cutoff raises
    TypeError deep inside the handler, returns 500 to the bridge, and the
    bridge then retries the same message forever. Assume UTC when the offset
    is missing rather than crashing.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _verify_webhook_signature(store: Store, body: bytes, signature: str) -> None:
    """AUDIT J: the signature header used to be declared and never checked.

    A handler that accepts `x_webhook_signature` LOOKS authenticated in review
    while verifying nothing — worse than having no parameter at all, because it
    stops the reader from asking the question.
    """
    if not store.webhook_secret:
        return  # no secret configured (local/dev); nothing to verify against
    expected = hmac.new(
        store.webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "Signature webhook tidak valid")


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(
    request: Request,
    message: InboundMessage,
    # AUDIT V: the worker signs and sends `X-Marawa-Signature`; this handler read
    # `x-webhook-signature`. Two names for one thing, so the values could never
    # match. Both names are accepted now — the worker's name is canonical, the
    # old one kept so an in-flight deployment does not break mid-rollout.
    x_marawa_signature: str = Header(default="", alias="X-Marawa-Signature"),
    x_webhook_signature: str = Header(default="", alias="X-Webhook-Signature"),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    _verify_webhook_signature(
        store, await request.body(), x_marawa_signature or x_webhook_signature
    )

    message_ts = _as_aware(message.timestamp)
    cutoff = _as_aware(store.pairing_cutoff_ts) if store.pairing_cutoff_ts else None
    if should_ignore_inbound(message_ts, cutoff):
        return {"status": "ignored_pre_pairing_history"}

    # Nomor petugas tidak dilayani bot. Filter berjalan SEBELUM percakapan
    # dibuat, bukan sesudah — kalau ditaruh belakangan, thread petugas tetap
    # lahir dan mengotori kotak masuk meski botnya diam.
    #
    # Tanpa penjaga ini: petugas membalas notifikasi ("oke saya cek") dan bot
    # menjawabnya sebagai pertanyaan statistik; nomor petugas muncul di papan
    # triase sebagai warga; dan petugas bisa mengetik ADMIN lalu memicu
    # notifikasi ke dirinya sendiri.
    #
    # Fail-closed (STAFF-005): lookup blokir yang error menjadi 503 — bukan
    # "bukan petugas". Melayani petugas karena lookup rusak lebih berbahaya
    # daripada menolak webhook sebentar.
    try:
        staff = _is_staff_number(store, message.conversation_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "Kebijakan petugas tidak bisa diverifikasi") from None
    if staff:
        _mark_staff(store, message.conversation_id)
        return {"status": "ignored_staff_number", "run_agent": False}

    if message.from_me:
        sent_ids = store.sent_wa_ids() if callable(getattr(store, "sent_wa_ids", None)) else store.sent_wa_ids
        if message.wa_message_id in sent_ids:
            return {"status": "ignored_own_echo"}
        for _attempt in range(3):
            conversation = store.get_conversation(message.conversation_id)
            transition = apply(conversation, Event.FROM_ME_DETECTED, message_ts)
            persisted = store.persist_inbound_transition(
                conversation, transition.state,
                direction="out", sender_type="admin", body=message.body,
                wa_message_id=message.wa_message_id,
                sender_admin_id=message.admin_id,
                cancel_bot_outbox=transition.cancel_pending_bot_outbox,
            )
            if persisted == "duplicate":
                return {"status": "ignored_duplicate"}
            if persisted == "conflict":
                continue
            store.audit("phone_takeover_detected", message.admin_id, message.conversation_id, {})
            return {"status": "human_takeover_recorded", "effects": transition.effects}
        raise Rejected("CONVERSATION_STATE_CONFLICT", "percakapan terlalu sibuk; coba lagi")

    # Dedupe + message insert + state mutation are ONE repository transaction.
    # A different message that loses the agent_run_active guard is retried from a
    # fresh snapshot and becomes queue_followup instead of starting agent #2.
    for _attempt in range(3):
        conversation = store.get_conversation(message.conversation_id)
        if not store.global_switch.enabled:
            run, reason = False, "bot_globally_disabled"
        else:
            run, reason = should_run_agent(conversation, message_ts)
        transition = apply(conversation, Event.INBOUND, message_ts)
        persisted = store.persist_inbound_transition(
            conversation, transition.state,
            direction="in", sender_type="user", body=message.body,
            wa_message_id=message.wa_message_id,
        )
        if persisted == "duplicate":
            return {"status": "ignored_duplicate"}
        if persisted == "conflict":
            continue
        _dispatch_and_record(store, transition, message_ts)
        return {
            "status": "stored", "run_agent": run,
            "skip_reason": reason, "effects": transition.effects,
        }
    raise Rejected("CONVERSATION_STATE_CONFLICT", "percakapan terlalu sibuk; coba lagi")


# ------------------------------- Dashboard (docs/06 §0.4) -------------------


@app.get("/conversations")
def list_conversations(
    _admin: AdminIdentity = Depends(current_admin),
    store: Store = Depends(get_store),
) -> list[dict[str, Any]]:
    if callable(getattr(store, "list_conversations", None)):
        rows = store.list_conversations(limit=100)
    else:
        rows = sorted(store.conversations.values(), key=lambda c: c.last_activity_at or datetime.min, reverse=True)
    return [
        {
            "conversation_id": c.conversation_id,
            "state": c.state.value if hasattr(c.state, "value") else str(c.state),
            "state_version": c.state_version,
            "assigned_admin_id": c.assigned_admin_id,
            "bot_paused_by": c.bot_paused_by,
            "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
            "handover_requested_at": (
                c.handover_requested_at.isoformat() if c.handover_requested_at else None
            ),
            "last_notified_at": c.last_notified_at.isoformat() if c.last_notified_at else None,
        }
        for c in rows
    ]


@app.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    _admin: AdminIdentity = Depends(current_admin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    conversation = store.get_conversation(conversation_id)
    messages = (
        store.messages(conversation_id) if callable(getattr(store, "messages", None))
        else store.messages.get(conversation_id, [])
    )
    return {
        "conversation": conversation.__dict__,
        "messages": messages,
    }


class HandoverToggle(BaseModel):
    expected_version: int


@app.post("/conversations/{conversation_id}/handover/on")
def handover_on(
    conversation_id: str,
    payload: HandoverToggle,
    admin: AdminIdentity = Depends(current_admin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with store.lock_for(conversation_id):
        conversation = store.get_conversation(conversation_id)
        transition = apply(
            conversation, Event.HANDOVER_ON, now,
            admin_id=admin.admin_id, expected_version=payload.expected_version,
        )
        if not store.compare_and_set(payload.expected_version, transition.state):
            raise Rejected("CONVERSATION_STATE_CONFLICT", "versi percakapan berubah saat diproses")
    store.audit("handover_on", admin.admin_id, conversation_id, {})

    if transition.cancel_pending_bot_outbox:
        _cancel_pending_bot_outbox(store, conversation_id)
    _dispatch_and_record(
        store, transition, now,
        citizen_text={"send_handover_notice": handover_notice(now, TimeoutSettings())},
    )

    return {"state": transition.state.state.value, "state_version": transition.state.state_version}


@app.post("/conversations/{conversation_id}/handover/off")
def handover_off(
    conversation_id: str,
    payload: HandoverToggle,
    admin: AdminIdentity = Depends(current_admin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with store.lock_for(conversation_id):
        conversation = store.get_conversation(conversation_id)
        transition = apply(
            conversation, Event.HANDOVER_OFF, now,
            admin_id=admin.admin_id, expected_version=payload.expected_version,
        )
        if not store.compare_and_set(payload.expected_version, transition.state):
            raise Rejected("CONVERSATION_STATE_CONFLICT", "versi percakapan berubah saat diproses")
    store.audit("handover_off", admin.admin_id, conversation_id, {})
    return {"state": transition.state.state.value, "state_version": transition.state.state_version}


class AdminReply(BaseModel):
    body: str
    expected_version: int
    # AUDIT H: deduplication must key on "the same click", not "the same text".
    # Keying on body+version meant an officer typing "ok" twice — ordinary human
    # behaviour — had the second message silently swallowed. The dashboard sends
    # a fresh uuid per send action; a retry of that action reuses it.
    client_request_id: str | None = None


@app.post("/conversations/{conversation_id}/messages")
def admin_reply(
    conversation_id: str,
    payload: AdminReply,
    admin: AdminIdentity = Depends(current_admin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    conversation = store.get_conversation(conversation_id)
    now = datetime.now(timezone.utc)
    transition = apply(
        conversation, Event.ADMIN_REPLY, now,
        admin_id=admin.admin_id, expected_version=payload.expected_version,
    )
    outbox_id = str(uuid.uuid4())
    key = (
        f"cli_{payload.client_request_id}" if payload.client_request_id
        else f"ob_{outbox_id}"
    )
    record = SendRecord(
        outbox_id=outbox_id, conversation_id=conversation_id, body=payload.body,
        sender_type="admin", sender_admin_id=admin.admin_id,
        state_version_at_enqueue=transition.state.state_version,
        idempotency_key=key,
    )
    result = store.persist_admin_reply(
        payload.expected_version, transition.state, record,
    )
    if result == "duplicate":
        raise HTTPException(409, "Permintaan kirim ini sudah diproses")
    if result == "conflict":
        raise Rejected("CONVERSATION_STATE_CONFLICT", "versi percakapan berubah saat diproses")
    store.audit("admin_reply_enqueued", admin.admin_id, conversation_id, {"outbox_id": outbox_id})
    return {"outbox_id": outbox_id, "status": record.status.value}


# ------------------------------- Superadmin settings (docs/06 §3B/3C) -------


class TimeoutSettingsPayload(BaseModel):
    values: dict[str, int]


@app.get("/settings/timeouts")
def get_timeout_settings(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, int]:
    defaults = TimeoutSettings()
    effective = {
        key: getattr(defaults, key)
        for key in (
            "citizen_idle_minutes", "queue_expiry_minutes",
            "handover_auto_revert_minutes", "queue_notify_repeat_minutes",
        )
    }
    effective.update(store.settings)
    return effective


@app.put("/settings/timeouts")
def put_timeout_settings(
    payload: TimeoutSettingsPayload,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    errors = validate_settings(payload.values)
    if errors:
        raise HTTPException(422, {"errors": errors})
    before = dict(store.settings)
    store.update_settings(payload.values, admin.admin_id)
    after = dict(store.settings)
    store.audit("settings_changed", admin.admin_id, None, {"before": before, "after": after})
    return after


# ── Nomor petugas (migrasi 009) ──


class AdminContactIn(BaseModel):
    phone: str
    label: str
    notify: bool = True


@app.get("/settings/admin-contacts")
def list_admin_contacts(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> list[dict[str, Any]]:
    getter = getattr(store, "admin_contacts", None)
    if getter is None:
        return []
    rows = getter()
    return [
        {**r, "display": display_phone(r["phone_e164"]),
         "created_at": r["created_at"].isoformat() if r.get("created_at") else None}
        for r in rows
    ]


@app.post("/settings/admin-contacts")
def add_admin_contact(
    body: AdminContactIn,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    try:
        phone = normalize_phone(body.phone)
    except InvalidPhone as exc:
        raise HTTPException(422, str(exc)) from exc
    label = body.label.strip()
    if not label:
        raise HTTPException(422, "Beri nama supaya nomornya bisa dikenali nanti")

    adder = getattr(store, "add_admin_contact", None)
    if adder is None:
        raise HTTPException(501, "Penyimpanan ini belum mendukung kontak petugas")
    if not adder(phone, label, admin.admin_id, notify=body.notify):
        raise HTTPException(409, "Nomor ini sudah terdaftar")
    store.audit("admin_contact_added", admin.admin_id, None,
                {"phone": phone, "label": label})
    return {"phone_e164": phone, "display": display_phone(phone), "label": label}


@app.delete("/settings/admin-contacts/{contact_id}")
def remove_admin_contact(
    contact_id: int,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    remover = getattr(store, "remove_admin_contact", None)
    if remover is None or not remover(contact_id):
        raise HTTPException(404, "Nomor tidak ditemukan")
    store.audit("admin_contact_removed", admin.admin_id, None, {"contact_id": contact_id})
    return {"removed": contact_id}


class AdminAccountIn(BaseModel):
    admin_id: str
    name: str
    role: str
    password: str


_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")


def _validate_admin_account(body: AdminAccountIn) -> tuple[str, str]:
    admin_id = body.admin_id.strip().lower()
    name = body.name.strip()
    if not _ACCOUNT_ID_RE.fullmatch(admin_id):
        raise HTTPException(
            422,
            "Username 3–40 karakter: huruf kecil, angka, titik, strip, atau underscore",
        )
    if not (2 <= len(name) <= 80):
        raise HTTPException(422, "Nama tampilan harus 2–80 karakter")
    if body.role not in ("admin", "superadmin"):
        raise HTTPException(422, "Role harus admin atau superadmin")
    if not (8 <= len(body.password) <= 128):
        raise HTTPException(422, "Password harus 8–128 karakter")
    return admin_id, name


@app.get("/admin/accounts")
def list_admin_accounts(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> list[dict[str, Any]]:
    getter = getattr(store, "list_admin_accounts", None)
    if getter is None:
        raise HTTPException(501, "Penyimpanan ini belum mendukung akun admin")
    return [
        {
            "admin_id": row["admin_id"], "name": row["name"],
            "role": row["role"], "active": bool(row.get("active", True)),
            "created_at": (
                row["created_at"].isoformat()
                if row.get("created_at") and hasattr(row["created_at"], "isoformat")
                else row.get("created_at")
            ),
        }
        for row in getter()
    ]


@app.post("/admin/accounts", status_code=201)
def create_admin_account(
    body: AdminAccountIn,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    admin_id, name = _validate_admin_account(body)
    creator = getattr(store, "create_admin_account", None)
    if creator is None:
        raise HTTPException(501, "Penyimpanan ini belum mendukung akun admin")
    if not creator(admin_id, name, body.role, hash_password(body.password)):
        raise HTTPException(409, "Username sudah digunakan")
    store.audit(
        "admin_account_created", admin.admin_id, None,
        {"target_admin_id": admin_id, "name": name, "role": body.role},
    )
    return {"admin_id": admin_id, "name": name, "role": body.role, "active": True}


class PasswordSetIn(BaseModel):
    admin_id: str
    password: str


@app.post("/admin/set-password")
def set_password(
    body: PasswordSetIn,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Set/reset password admin (superadmin only). Dipakai waktu seed awal."""
    if len(body.password) < 8:
        raise HTTPException(422, "Password minimal 8 karakter")
    setter = getattr(store, "set_admin_password", None)
    if setter is None:
        raise HTTPException(501, "Penyimpanan ini belum mendukung password")
    if not setter(body.admin_id, hash_password(body.password)):
        raise HTTPException(404, "Admin tidak ditemukan")
    store.audit("admin_password_set", admin.admin_id, None, {"target": body.admin_id})
    return {"ok": True}


# ------------------------------- Internal key auth (docs/07) -----------------


def require_internal_key(
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
) -> None:
    """Worker↔API channel auth (docs/07). Fail-closed: tanpa MARAWA_INTERNAL_KEY
    di environment, semua panggilan internal ditolak (503) — bukan dibiarkan
    terbuka seperti path segment 'internal' yang menyesatkan."""
    key = os.environ.get("MARAWA_INTERNAL_KEY")
    if not key:
        raise HTTPException(503, "Internal channel belum dikonfigurasi")
    if not x_internal_key or not hmac.compare_digest(key, x_internal_key):
        raise HTTPException(401, "Internal key tidak valid")


class QrPushIn(BaseModel):
    qr: str
    expires_at: datetime


class ConnPushIn(BaseModel):
    state: str


@app.post("/internal/whatsapp-qr")
def internal_whatsapp_qr(
    body: QrPushIn,
    _key: None = Depends(require_internal_key),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    setter = getattr(store, "set_setting", None)
    if setter is None:
        return {"ok": False}
    setter("wa_qr", {
        "qr": body.qr,
        "expires_at": body.expires_at.isoformat(),
        "pushed_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@app.post("/internal/whatsapp-connection")
def internal_whatsapp_connection(
    body: ConnPushIn,
    _key: None = Depends(require_internal_key),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    setter = getattr(store, "set_setting", None)
    if setter is None:
        return {"ok": False}
    if body.state not in ("open", "closed", "connecting"):
        raise HTTPException(422, "state harus open/closed/connecting")
    setter("wa_connection", {
        "state": body.state,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return {"ok": True}


@app.get("/settings/whatsapp")
def get_whatsapp_status(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Status koneksi + QR pairing terbaru — ditampilkan dashboard."""
    qr = getattr(store, "get_setting", lambda k: None)("wa_qr")
    conn = getattr(store, "get_setting", lambda k: None)("wa_connection")
    return {
        "qr": (qr or {}).get("qr"),
        "qr_expires_at": (qr or {}).get("expires_at"),
        "connected": bool(conn and conn.get("state") == "open"),
        "connection_state": (conn or {}).get("state", "unknown"),
    }


@app.get("/settings/whatsapp-qr.png")
def get_whatsapp_qr_png(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> Any:
    """QR sebagai PNG supaya dashboard bisa render tanpa lib QR client-side."""
    qr = getattr(store, "get_setting", lambda k: None)("wa_qr")
    payload = (qr or {}).get("qr")
    if not payload:
        raise HTTPException(404, "QR belum tersedia")
    try:
        import qrcode  # type: ignore
        from io import BytesIO
        img = qrcode.make(payload)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png")
    except ImportError:
        raise HTTPException(503, "qrcode tidak terpasang") from None


@app.get("/settings/agent")
def get_agent_settings(_admin: AdminIdentity = Depends(require_superadmin)) -> dict[str, Any]:
    # AGENT.md §3B: no toggle here ever disables the answer_gate. This endpoint
    # exists to demonstrate the boundary, not to widen it.
    return {
        "note": (
            "Pengaturan yang boleh diubah dari sini terbatas pada model, "
            "temperature (0.0-0.3), dan teks operasional. Gate kebenaran "
            "(answer_gate) tidak dapat dimatikan dari endpoint ini, selamanya."
        ),
        "editable": ["model", "temperature", "greeting_text", "fallback_text"],
        "never_editable": [
            "answer_gate.enabled", "unit_review_bypass", "selection_envelope_bypass",
        ],
    }


@app.post("/settings/bot-global-switch")
def set_global_switch(
    enabled: bool,
    reason: str | None = None,
    admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    store.set_global_switch(enabled, admin.admin_id, reason)
    store.audit("global_switch", admin.admin_id, None, {"enabled": enabled, "reason": reason})
    return {"enabled": enabled}


@app.get("/status/whatsapp")
def whatsapp_status(
    _admin: AdminIdentity = Depends(current_admin),  # both roles, docs/06 §3.0a
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    pending_counter = getattr(store, "pending_outbox_count", None)
    pending_count = pending_counter() if callable(pending_counter) else store.worker_health.pending_count
    return {
        "status": store.worker_health.status(),
        "connected": store.worker_health.connected,
        "pending_count": pending_count,
        "bot_globally_enabled": store.global_switch.enabled,
    }


@app.get("/audit-log")
def get_audit_log(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> list[dict[str, Any]]:
    return store.audit_log


# ------------------------------- Sweep (docs/06 §0.9/0.10) -------------------


@app.post("/internal/outbox/claim")
def internal_outbox_claim(
    worker_id: str,
    limit: int = 10,
    _key: None = Depends(require_internal_key),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Ambil batch outbox untuk dikirim (worker WhatsApp poll tiap N detik)."""
    claim = getattr(store, "claim_outbox_batch", None)
    if claim is None:
        raise HTTPException(501, "Store aktif tidak mendukung claim outbox")
    records = claim(worker_id, min(max(limit, 1), 50))
    return {
        "records": [
            {
                "outbox_id": r.outbox_id,
                "conversation_id": r.conversation_id,
                "body": r.body,
                "sender_type": r.sender_type,
                "sender_admin_id": r.sender_admin_id,
                "state_version_at_enqueue": r.state_version_at_enqueue,
                "idempotency_key": r.idempotency_key,
                "wa_message_id": r.wa_message_id,
            }
            for r in records
        ]
    }


class OutboxAuthorizeRequest(BaseModel):
    outbox_id: str


@app.post("/internal/outbox/authorize")
def internal_outbox_authorize(
    body: OutboxAuthorizeRequest,
    _key: None = Depends(require_internal_key),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    authorize = getattr(store, "authorize_claimed_outbox", None)
    if authorize is None:
        raise HTTPException(501, "Store aktif tidak mendukung final send gate")
    allowed, reason = authorize(body.outbox_id)
    return {"outbox_id": body.outbox_id, "allowed": allowed, "reason": reason}


class OutboxUpdateRequest(BaseModel):
    outbox_id: str
    status: str  # sent | delivered | failed | cancelled | unknown
    wa_message_id: str | None = None
    error: str | None = None


@app.post("/internal/outbox/update")
def internal_outbox_update(
    body: OutboxUpdateRequest,
    _key: None = Depends(require_internal_key),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    from scripts.outbox_worker import SendRecord, SendStatus

    record = SendRecord(
        outbox_id=body.outbox_id,
        conversation_id="",
        body="",
        sender_type="bot",
        state_version_at_enqueue=0,
        status=SendStatus(body.status),
        wa_message_id=body.wa_message_id,
        last_error=body.error,
    )
    update = getattr(store, "update_outbox", None)
    if update is None:
        raise HTTPException(501, "Store aktif tidak mendukung update outbox")
    update(record)
    return {"outbox_id": body.outbox_id, "status": body.status}


@app.post("/internal/sweep")
def run_sweep(
    # AUDIT G: this was unauthenticated. A sweep mutates conversation state, and
    # anyone able to reach the port could force-close sessions or revert
    # handovers. "Internal" in a path segment is not an access control.
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Applies IDLE_TIMEOUT / AUTO_REVERT_CHECK to every conversation that is
    due. Call this every minute (cron, APScheduler, or a worker loop) —
    without a caller hitting this, every timeout guarantee in docs/06 is dead
    code that nothing ever triggers. Not itself scheduled in this stage.
    """
    now = datetime.now(timezone.utc)
    timeout_values = {
        key: value for key, value in store.settings.items()
        if key in ("citizen_idle_minutes", "queue_expiry_minutes",
                   "handover_auto_revert_minutes", "queue_notify_repeat_minutes")
    }
    candidates = store.conversations_needing_sweep()
    plan = plan_sweep(candidates, now, TimeoutSettings(**timeout_values))
    applied = []
    for item in plan:
        conversation = store.get_conversation(item.conversation_id)
        event = Event.IDLE_TIMEOUT if item.event == "idle_timeout" else Event.AUTO_REVERT_CHECK
        try:
            transition = apply(conversation, event, now, TimeoutSettings(**timeout_values))
        except Rejected:
            continue
        if not store.compare_and_set(conversation.state_version, transition.state):
            continue  # another worker/admin moved first
        if transition.cancel_pending_bot_outbox:
            _cancel_pending_bot_outbox(store, item.conversation_id)
        _dispatch_and_record(store, transition, now)
        applied.append({"conversation_id": item.conversation_id, "reason": item.reason,
                        "new_state": transition.state.state.value})
    return {"planned": len(plan), "applied": applied}


@app.get("/internal/notifications")
def get_notifications(
    # AUDIT G: this returned conversation ids and message text to any caller.
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    """Inspection endpoint for the in-memory channel — testing/demo only."""
    channel = store.notification_channel
    return {
        "officer_messages": getattr(channel, "officer_messages", []),
        "citizen_messages": getattr(channel, "citizen_messages", []),
    }


# ------------------------------- helpers -------------------------------


def _dispatch_and_record(
    store: Store, transition, now: datetime, citizen_text: dict[str, str] | None = None,
) -> None:
    conversation = store.get_conversation(transition.state.conversation_id)
    # BUG FIX (found by test_queued_conversation_notifies_officers_via_webhook):
    # `Transition.notify_officers` is a separate boolean field, NOT a string in
    # `Transition.effects` — but dispatch_effects only reads the effects list.
    # `_on_inbound` and `_on_request_handover` set the boolean; `_on_auto_revert`
    # instead puts a literal "notify_officers_auto_revert" string in effects.
    # Two notification paths through the same state machine, inconsistent by
    # accident. Unify here rather than in conversation_state.py, since that
    # module should stay free of any notion of "how a notification is sent".
    effects = list(transition.effects)
    if getattr(transition, "notify_officers", False) and "notify_officers" not in effects:
        effects.append("notify_officers")
    notified = dispatch_effects(
        effects, conversation, now, store.notification_channel,
        citizen_text_by_effect=citizen_text,
    )
    if notified:
        store.mark_notified(conversation.conversation_id, conversation.state_version, now)


def _cancel_pending_bot_outbox(store: Store, conversation_id: str) -> None:
    store.cancel_pending_bot_outbox(conversation_id, "handover_preempted")
