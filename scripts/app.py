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
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
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
from scripts.notifications import InMemoryChannel, dispatch_effects
from scripts.postgres_store import PostgresStore
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
            "seed-super-1": {"name": "Superadmin Satu", "role": "superadmin"},
            "seed-super-2": {"name": "Superadmin Dua", "role": "superadmin"},
        }
        self.settings: dict[str, int] = {
            "citizen_idle_minutes": 5,
            "queue_expiry_minutes": 240,
            "handover_auto_revert_minutes": 30,
            "queue_notify_repeat_minutes": 5,
        }
        self.global_switch = GlobalBotSwitch()
        self.pairing_cutoff_ts: datetime | None = None
        self.audit_log: list[dict[str, Any]] = []
        self.totp_secrets: dict[str, str] = {}
        self.worker_health = WorkerHealth(connected=True)
        # Swap for a real WhatsApp-sending channel before production; see
        # scripts/notifications.py. Kept in-memory here so the sweep and
        # notification wiring can be demonstrated and tested without Baileys.
        self.notification_channel = InMemoryChannel()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # AUDIT J: when set, every webhook call must carry a valid signature.
        self.webhook_secret: str | None = None

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
        return store
    return Store()


STORE = _build_store()


def get_store() -> Store:
    return STORE


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="MARAWA AI — Runtime Wiring")


# ---------------------------------------------------------------------------
# Auth — TOTP + sesi (docs/06 §4); header X-Admin-Id hanya mode dev
# ---------------------------------------------------------------------------


class AdminIdentity(BaseModel):
    admin_id: str
    role: str


def _dev_header_mode() -> bool:
    """Sesuai docs/06 §4: header placeholder hanya untuk pengembangan.

    MARAWA_SESSION_KEY ada → mode produksi: WAJIB Bearer session.
    MARAWA_SESSION_KEY tidak ada → mode dev: X-Admin-Id tetap diterima
    sehingga wiring/dashboard test lama tetap hijau; sesi TOTP juga aktif.
    """
    return not session_key_stable()


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
    totp_code: str


@app.post("/admin/login")
def admin_login(body: LoginRequest, store: Store = Depends(get_store)) -> dict[str, Any]:
    if not session_key_stable():
        raise HTTPException(503, "Sesi dinonaktifkan: set MARAWA_SESSION_KEY")
    admin = store.admins.get(body.admin_id)
    if admin is None:
        raise HTTPException(401, "Admin tidak dikenal")
    secret = store.totp_secret_for(body.admin_id)
    if secret is None:
        raise HTTPException(403, "TOTP belum di-enroll untuk admin ini")
    if not verify_totp(secret, body.totp_code):
        raise HTTPException(401, "Kode TOTP salah")
    store.audit("admin_login", body.admin_id, None, {"via": "totp"})
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
    x_webhook_signature: str = Header(default=""),
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    _verify_webhook_signature(store, await request.body(), x_webhook_signature)

    message_ts = _as_aware(message.timestamp)
    cutoff = _as_aware(store.pairing_cutoff_ts) if store.pairing_cutoff_ts else None
    if should_ignore_inbound(message_ts, cutoff):
        return {"status": "ignored_pre_pairing_history"}

    conversation = store.get_conversation(message.conversation_id)

    if message.from_me:
        if message.wa_message_id in store.sent_wa_ids:
            return {"status": "ignored_own_echo"}
        transition = apply(conversation, Event.FROM_ME_DETECTED, message_ts)
        store.conversations[message.conversation_id] = transition.state
        store.audit("phone_takeover_detected", None, message.conversation_id, {})
        return {"status": "human_takeover_recorded", "effects": transition.effects}

    # BUG FIX (wiring test): should_run_agent must be asked BEFORE transitioning.
    # apply() flips agent_run_active=True as part of starting a run, so checking
    # should_run_agent on the POST-transition state always reads "already
    # running" and the bot would never answer anything. should_run_agent exists
    # for exactly this pre-check; the state machine's `effects` list is a
    # separate, internal record of what apply() itself decided to do.
    if not store.global_switch.enabled:
        run, reason = False, "bot_globally_disabled"
    else:
        run, reason = should_run_agent(conversation, message_ts)

    transition = apply(conversation, Event.INBOUND, message_ts)
    store.conversations[message.conversation_id] = transition.state
    store.messages.setdefault(message.conversation_id, []).append({
        "direction": "in", "body": message.body, "at": message_ts.isoformat(),
    })
    _dispatch_and_record(store, transition, message_ts)

    return {"status": "stored", "run_agent": run, "skip_reason": reason, "effects": transition.effects}


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
    store.conversations[conversation_id] = transition.state

    outbox_id = str(uuid.uuid4())
    key = (
        f"cli_{payload.client_request_id}" if payload.client_request_id
        else f"ob_{outbox_id}"
    )
    if payload.client_request_id and any(
        r.idempotency_key == key and r.status in (SendStatus.SENT, SendStatus.PENDING)
        for r in store.outbox.values()
    ):
        raise HTTPException(409, "Permintaan kirim ini sudah diproses")

    record = SendRecord(
        outbox_id=outbox_id, conversation_id=conversation_id, body=payload.body,
        sender_type="admin", state_version_at_enqueue=transition.state.state_version,
        idempotency_key=key,
    )
    store.outbox[outbox_id] = record
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
    return store.settings


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
    store.settings.update(payload.values)
    store.audit("settings_changed", admin.admin_id, None, {"before": before, "after": store.settings})
    return store.settings


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
    store.global_switch = GlobalBotSwitch(
        enabled=enabled, disabled_by=None if enabled else admin.admin_id,
        disabled_at=None if enabled else datetime.now(timezone.utc), reason=reason,
    )
    store.audit("global_switch", admin.admin_id, None, {"enabled": enabled, "reason": reason})
    return {"enabled": enabled}


@app.get("/status/whatsapp")
def whatsapp_status(
    _admin: AdminIdentity = Depends(current_admin),  # both roles, docs/06 §3.0a
    store: Store = Depends(get_store),
) -> dict[str, Any]:
    return {
        "status": store.worker_health.status(),
        "connected": store.worker_health.connected,
        "pending_count": store.worker_health.pending_count,
        "bot_globally_enabled": store.global_switch.enabled,
    }


@app.get("/audit-log")
def get_audit_log(
    _admin: AdminIdentity = Depends(require_superadmin),
    store: Store = Depends(get_store),
) -> list[dict[str, Any]]:
    return store.audit_log


# ------------------------------- Sweep (docs/06 §0.9/0.10) -------------------


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
    plan = plan_sweep(list(store.conversations.values()), now, TimeoutSettings(**{
        k: v for k, v in store.settings.items() if k in TimeoutSettings.__dataclass_fields__
    }))
    applied = []
    for item in plan:
        conversation = store.conversations[item.conversation_id]
        event = Event.IDLE_TIMEOUT if item.event == "idle_timeout" else Event.AUTO_REVERT_CHECK
        try:
            transition = apply(conversation, event, now)
        except Rejected:
            continue  # e.g. IDLE_TIMEOUT on ADMIN_ACTIVE — not applicable, skip
        store.conversations[item.conversation_id] = transition.state
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
    return {
        "officer_messages": store.notification_channel.officer_messages,
        "citizen_messages": store.notification_channel.citizen_messages,
    }


# ------------------------------- helpers -------------------------------


def _dispatch_and_record(
    store: Store, transition, now: datetime, citizen_text: dict[str, str] | None = None,
) -> None:
    conversation = store.conversations[transition.state.conversation_id]
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
        store.conversations[conversation.conversation_id] = ConversationState(
            **{**conversation.__dict__, "last_notified_at": now}
        )


def _cancel_pending_bot_outbox(store: Store, conversation_id: str) -> None:
    for record in store.outbox.values():
        if (
            record.conversation_id == conversation_id
            and record.sender_type == "bot"
            and record.status == SendStatus.PENDING
        ):
            record.status = SendStatus.CANCELLED
            record.last_error = "handover_preempted"
