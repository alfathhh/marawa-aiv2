#!/usr/bin/env python3
"""Conversation state machine + outbox send guard.

This is the module that stops the bot and a human officer from answering the
same citizen at the same time. It is pure logic: no DB, no network, no Baileys.
The caller persists `ConversationState` and replays events through `apply`.

Design rule that everything else follows: **every transition bumps
`state_version`, and nothing is ever sent without re-checking that version.**
An outbound message enqueued at version N is cancelled if the conversation has
moved on by the time the worker picks it up. That single rule closes the race
conditions in docs/06 §0.7.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any


class State(str, Enum):
    BOT_ACTIVE = "BOT_ACTIVE"
    QUEUED = "QUEUED"           # citizen asked for a human, nobody has opened it
    ADMIN_ACTIVE = "ADMIN_ACTIVE"
    IDLE_CLOSED = "IDLE_CLOSED"


class Event(str, Enum):
    INBOUND = "inbound"                  # citizen message
    REQUEST_HANDOVER = "request_handover"
    HANDOVER_ON = "handover_on"      # toggle dinyalakan petugas -> bot diam
    HANDOVER_OFF = "handover_off"    # toggle dimatikan -> bot hidup lagi
    ADMIN_REPLY = "admin_reply"
    FROM_ME_DETECTED = "from_me_detected"  # officer replied from the paired phone
    IDLE_TIMEOUT = "idle_timeout"
    AUTO_REVERT_CHECK = "auto_revert_check"


class Rejected(Exception):
    """Transition refused. Carries a machine-readable code for the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Settings:
    """Operator-tunable values. Bounds are enforced in validate_settings()."""

    citizen_idle_minutes: int = 5
    # BOT_COOLDOWN dihapus 15 Agt: state itu berperilaku identik dengan
    # BOT_ACTIVE (bot hanya bicara saat ada inbound), jadi ia satu state, satu
    # setelan, dan beberapa cabang tes yang tidak membeli apa pun.
    # AUDIT B: a queued citizen is waiting on US, not idling. Their session must
    # outlive a coffee break, so the queue uses its own, far longer expiry.
    queue_expiry_minutes: int = 240
    # Toggle yang lupa dimatikan adalah mode gagal khas model ini: bot mati,
    # petugas beralih ke pekerjaan lain, warga menunggu selamanya tanpa ada
    # yang menjawab. Auto-revert menutupnya.
    handover_auto_revert_minutes: int = 30
    queue_notify_repeat_minutes: int = 5
    office_open: time = time(7, 30)
    office_close: time = time(16, 0)
    # Bot runs 24/7 (decision 15 Aug), so handover wording must tell the truth
    # about when a human will actually reply.
    office_days: frozenset[int] = frozenset({0, 1, 2, 3, 4})  # Mon-Fri


@dataclass(frozen=True)
class ConversationState:
    conversation_id: str
    state: State = State.BOT_ACTIVE
    state_version: int = 0
    assigned_admin_id: str | None = None
    last_activity_at: datetime | None = None
    handover_requested_at: datetime | None = None
    # AUDIT E: the version guard cannot stop two concurrent agent runs, because
    # inbound during BOT_ACTIVE deliberately does not change the state. Without
    # this flag, two messages arriving together produce two answers.
    agent_run_active: bool = False
    # Siapa yang menyalakan toggle. Wajib ada supaya audit bisa menjawab
    # "siapa yang mematikan bot di percakapan ini".
    bot_paused_by: str | None = None
    bot_paused_at: datetime | None = None
    last_admin_activity_at: datetime | None = None
    # Pesan sebelum titik ini tidak dijawab bot saat toggle dimatikan. Tanpa ini
    # bot menyembur menjawab seluruh antrean pesan lama sekaligus.
    resume_watermark_at: datetime | None = None
    last_notified_at: datetime | None = None


@dataclass(frozen=True)
class OutboxEntry:
    outbox_id: str
    conversation_id: str
    body: str
    sender_type: str                 # "bot" | "admin" | "system"
    state_version_at_enqueue: int
    sender_admin_id: str | None = None


@dataclass
class Transition:
    state: ConversationState
    effects: list[str] = field(default_factory=list)
    cancel_pending_bot_outbox: bool = False
    notify_officers: bool = False


# ---------------------------------------------------------------------------
# Settings validation (docs/06 §3C) — server-side, never trust the form
# ---------------------------------------------------------------------------

SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "citizen_idle_minutes": (2, 30),
    "queue_expiry_minutes": (30, 1440),
    "handover_auto_revert_minutes": (5, 480),
    "queue_notify_repeat_minutes": (1, 30),
}


def validate_settings(raw: dict[str, Any]) -> list[str]:
    """Return human-readable errors. Empty list means the settings are safe."""
    errors: list[str] = []
    for key, value in raw.items():
        if key not in SETTING_BOUNDS:
            errors.append(f"{key}: bukan setelan yang dapat diubah dari dashboard")
            continue
        low, high = SETTING_BOUNDS[key]
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{key}: harus bilangan bulat")
            continue
        if not low <= value <= high:
            errors.append(f"{key}: {value} di luar rentang {low}–{high}")
    return errors


# ---------------------------------------------------------------------------
# Inbound filtering
# ---------------------------------------------------------------------------

def should_ignore_inbound(
    message_timestamp: datetime,
    pairing_cutoff_ts: datetime | None,
) -> bool:
    """Drop anything that predates pairing.

    Baileys replays history when a number is linked. Without this, pairing an
    existing MARAWA number makes the bot answer months-old conversations as if
    they had just arrived.
    """
    if pairing_cutoff_ts is None:
        return False
    return message_timestamp < pairing_cutoff_ts


def classify_from_me(wa_message_id: str, known_outbox_wa_ids: set[str]) -> str:
    """Is this `fromMe` message ours, or did a human type it on the phone?

    Everything the system sends is recorded in the outbox with its returned
    wa_message_id. Anything else with fromMe=true was typed by a person.
    """
    return "echo_of_our_own_send" if wa_message_id in known_outbox_wa_ids else "human_typed"


# ---------------------------------------------------------------------------
# Office hours
# ---------------------------------------------------------------------------

def is_within_office_hours(now: datetime, settings: Settings) -> bool:
    if now.weekday() not in settings.office_days:
        return False
    return settings.office_open <= now.time() < settings.office_close


def handover_notice(now: datetime, settings: Settings, form_url: str | None = None) -> str:
    """Never imply an immediate reply outside office hours.

    AUDIT D: this used to return a string containing a literal "{form_url}"
    that the caller had to remember to format. Forgetting showed the raw
    placeholder to a citizen. The URL is a parameter now, so it cannot be
    forgotten silently.
    """
    if is_within_office_hours(now, settings):
        return (
            "Baik, saya hubungkan dengan petugas PST. Mohon tunggu sebentar di "
            "chat ini."
        )
    notice = (
        "Baik, permintaan Anda sudah saya teruskan ke petugas PST. Saat ini di "
        "luar jam layanan, jadi petugas akan membalas pada jam kerja berikutnya "
        "(Senin–Jumat, 07.30–16.00 WIB)."
    )
    if form_url:
        notice += f"\n\nKalau ingin lebih cepat, Anda juga bisa mengisi formulir: {form_url}"
    return notice


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------

def apply(
    current: ConversationState,
    event: Event,
    now: datetime,
    settings: Settings = Settings(),
    admin_id: str | None = None,
    expected_version: int | None = None,
) -> Transition:
    """Apply an event. Raises Rejected when the transition is not allowed.

    `expected_version` implements optimistic concurrency: the dashboard sends
    the version it rendered, and a stale version loses. This is what makes two
    officers clicking "Ambil Alih" at the same moment safe.
    """
    if expected_version is not None and expected_version != current.state_version:
        raise Rejected(
            "CONVERSATION_STATE_CONFLICT",
            f"versi percakapan sudah berubah ({expected_version} != {current.state_version})",
        )

    handler = _HANDLERS.get(event)
    if handler is None:
        raise Rejected("UNKNOWN_EVENT", f"event tidak dikenal: {event}")
    return handler(current, now, settings, admin_id)


def _advance(
    current: ConversationState,
    state: State,
    now: datetime,
    **changes: Any,
) -> ConversationState:
    return replace(
        current,
        state=state,
        state_version=current.state_version + 1,
        last_activity_at=now,
        **changes,
    )


def _on_inbound(current, now, settings, admin_id) -> Transition:
    if current.state is State.ADMIN_ACTIVE:
        # Store and stream to the dashboard; the bot stays silent.
        return Transition(
            state=replace(current, last_activity_at=now),
            effects=["store_message", "stream_to_dashboard"],
        )
    if current.state is State.QUEUED:
        return Transition(
            state=replace(current, last_activity_at=now),
            effects=["store_message", "stream_to_dashboard"],
            notify_officers=True,
        )
    if current.state is State.IDLE_CLOSED:
        return Transition(
            state=_advance(
                current, State.BOT_ACTIVE, now,
                assigned_admin_id=None, agent_run_active=True,
            ),
            effects=["store_message", "run_agent"],
        )
    if current.agent_run_active:
        # AUDIT E: an agent run is already in flight for this conversation.
        # Queue the message so the running turn can pick it up instead of
        # starting a second run that would answer the same person twice.
        return Transition(
            state=replace(current, last_activity_at=now),
            effects=["store_message", "queue_followup"],
        )
    return Transition(
        state=replace(current, last_activity_at=now, agent_run_active=True),
        effects=["store_message", "run_agent"],
    )


def _on_request_handover(current, now, settings, admin_id) -> Transition:
    if current.state is State.ADMIN_ACTIVE:
        raise Rejected("ALREADY_ADMIN_ACTIVE", "petugas sudah menangani percakapan ini")
    if current.state is State.QUEUED:
        # Asking twice is not an error; just re-notify.
        return Transition(state=current, effects=["send_queue_ack"], notify_officers=True)
    return Transition(
        state=_advance(
            current, State.QUEUED, now,
            handover_requested_at=now, agent_run_active=False,
        ),
        effects=["send_handover_notice", "cancel_pending_bot_outbox"],
        cancel_pending_bot_outbox=True,
        notify_officers=True,
    )


def _on_handover_on(current, now, settings, admin_id) -> Transition:
    """Toggle ON: bot diam untuk percakapan ini."""
    if admin_id is None:
        raise Rejected("ADMIN_ID_REQUIRED", "toggle harus menyertakan identitas petugas")
    if current.state is State.ADMIN_ACTIVE:
        if current.bot_paused_by == admin_id:
            return Transition(state=current, effects=[])
        # Petugas lain sudah memegang. Bukan error keras: cukup beri tahu siapa.
        raise Rejected(
            "ALREADY_HELD",
            f"percakapan ini sedang dipegang {current.bot_paused_by}",
        )
    return Transition(
        state=_advance(
            current, State.ADMIN_ACTIVE, now,
            assigned_admin_id=admin_id, agent_run_active=False,
            bot_paused_by=admin_id, bot_paused_at=now,
            last_admin_activity_at=now,
        ),
        effects=["cancel_pending_bot_outbox", "notify_citizen_handover", "audit_handover_on"],
        cancel_pending_bot_outbox=True,
    )


def _on_handover_off(current, now, settings, admin_id) -> Transition:
    """Toggle OFF: bot hidup lagi, tapi tidak mengejar pesan lama."""
    if current.state is not State.ADMIN_ACTIVE:
        raise Rejected("NOT_HELD", "toggle handover tidak sedang aktif")
    return Transition(
        state=_advance(
            current, State.BOT_ACTIVE, now,
            assigned_admin_id=None, agent_run_active=False,
            bot_paused_by=None, bot_paused_at=None,
            # Semua pesan sebelum detik ini sudah menjadi urusan petugas.
            resume_watermark_at=now,
        ),
        effects=["notify_citizen_bot_resumed", "audit_handover_off"],
    )


def _on_auto_revert(current, now, settings, admin_id) -> Transition:
    """Toggle ditinggal menyala. Kembalikan bot supaya warga tidak terlantar."""
    if current.state is not State.ADMIN_ACTIVE:
        return Transition(state=current, effects=[])
    reference = current.last_admin_activity_at or current.bot_paused_at
    if reference is None:
        return Transition(state=current, effects=[])
    if now - reference < timedelta(minutes=settings.handover_auto_revert_minutes):
        return Transition(state=current, effects=[])
    return Transition(
        state=_advance(
            current, State.BOT_ACTIVE, now,
            assigned_admin_id=None, agent_run_active=False,
            bot_paused_by=None, bot_paused_at=None,
            resume_watermark_at=now,
        ),
        effects=["notify_citizen_bot_resumed", "notify_officers_auto_revert", "audit_auto_revert"],
    )


def _on_admin_reply(current, now, settings, admin_id) -> Transition:
    """Petugas mengirim balasan. Toggle harus sudah menyala."""
    if current.state is not State.ADMIN_ACTIVE:
        raise Rejected("HANDOVER_NOT_ON", "nyalakan toggle handover dulu")
    if current.assigned_admin_id is not None and admin_id != current.assigned_admin_id:
        raise Rejected("NOT_ASSIGNED", "percakapan ini dipegang petugas lain")
    return Transition(
        # Setiap balasan memperpanjang jendela auto-revert, sehingga percakapan
        # yang sedang aktif ditangani tidak pernah direbut kembali oleh bot.
        state=replace(
            current,
            last_activity_at=now,
            last_admin_activity_at=now,
            assigned_admin_id=admin_id,
            bot_paused_by=current.bot_paused_by or admin_id,
        ),
        effects=["enqueue_admin_outbound", "audit_reply"],
    )


def _on_from_me(current, now, settings, admin_id) -> Transition:
    """A human typed on the paired phone. Bot must go quiet immediately."""
    if current.state is State.ADMIN_ACTIVE:
        return Transition(
            state=replace(current, last_activity_at=now, last_admin_activity_at=now),
            effects=["store_admin_message", "stream_to_dashboard"],
        )
    return Transition(
        state=_advance(
            current, State.ADMIN_ACTIVE, now,
            assigned_admin_id=admin_id, agent_run_active=False,
            bot_paused_by=admin_id, bot_paused_at=now, last_admin_activity_at=now,
        ),
        effects=[
            "store_admin_message",
            "cancel_pending_bot_outbox",
            "audit_phone_takeover",
        ],
        cancel_pending_bot_outbox=True,
    )


def _on_idle_timeout(current, now, settings, admin_id) -> Transition:
    if current.state is State.ADMIN_ACTIVE:
        raise Rejected(
            "IDLE_NOT_APPLICABLE",
            "percakapan sedang ditangani petugas; idle timeout tidak berlaku",
        )
    if current.state is State.IDLE_CLOSED:
        return Transition(state=current, effects=[])
    if current.last_activity_at is None and current.state is State.BOT_ACTIVE:
        # AUDIT O: a conversation that has never spoken has nothing to time out.
        # Closing it would send an "sesi berakhir" notice to someone who never
        # started a session.
        return Transition(state=current, effects=[])

    effects = ["send_idle_notice"]
    if current.state is State.QUEUED:
        # AUDIT B: the citizen is waiting on us, not idling. Hold the queue open
        # for queue_expiry_minutes so an officer returning from a break finds a
        # live conversation instead of a closed one.
        requested_at = current.handover_requested_at or current.last_activity_at
        if requested_at is not None:
            waited = now - requested_at
            if waited < timedelta(minutes=settings.queue_expiry_minutes):
                return Transition(state=current, effects=["queue_still_waiting"])
        effects = ["mark_queue_abandoned"]

    return Transition(
        state=_advance(
            current, State.IDLE_CLOSED, now,
            assigned_admin_id=None, agent_run_active=False,
        ),
        effects=effects,
        cancel_pending_bot_outbox=True,
    )


_HANDLERS = {
    Event.INBOUND: _on_inbound,
    Event.REQUEST_HANDOVER: _on_request_handover,
    Event.HANDOVER_ON: _on_handover_on,
    Event.HANDOVER_OFF: _on_handover_off,
    Event.ADMIN_REPLY: _on_admin_reply,
    Event.AUTO_REVERT_CHECK: _on_auto_revert,
    Event.FROM_ME_DETECTED: _on_from_me,
    Event.IDLE_TIMEOUT: _on_idle_timeout,
}


# ---------------------------------------------------------------------------
# Outbox send guard — the final gate before Baileys
# ---------------------------------------------------------------------------

def authorize_send(entry: OutboxEntry, current: ConversationState) -> tuple[bool, str | None]:
    """Called by the worker immediately before sending. Never skip this.

    The entry was enqueued at some version; if the conversation has moved on,
    sending it would put a stale message in front of the citizen — the classic
    "bot talks over the officer" failure.
    """
    if entry.state_version_at_enqueue != current.state_version:
        return False, "state_changed_since_enqueue"
    if entry.sender_type == "bot" and current.state is not State.BOT_ACTIVE:
        return False, "handover_preempted"
    if entry.sender_type == "admin":
        if current.state is not State.ADMIN_ACTIVE:
            return False, "no_longer_admin_active"
        if entry.sender_admin_id != current.assigned_admin_id:
            return False, "reassigned_to_other_admin"
    if current.state is State.IDLE_CLOSED and entry.sender_type != "system":
        # AUDIT C: this used to block everything, including the very notice that
        # tells the citizen the session ended — a message that by definition is
        # enqueued after the close. System notices are exempt.
        return False, "session_closed"
    return True, None


# ---------------------------------------------------------------------------
# Concurrency: many citizens at once
# ---------------------------------------------------------------------------
#
# Baileys holds ONE connection. Every outbound for every citizen goes through
# it, so "banyak yang chat barengan" is not a theoretical concern — it is the
# normal case the moment the number is published.

def should_run_agent(
    conversation: ConversationState,
    message_timestamp: datetime,
    bot_globally_enabled: bool = True,
) -> tuple[bool, str | None]:
    """Single decision point for "does the bot answer this message?".

    Consolidating it here matters: the same question was previously answered in
    three places (state handler, outbox guard, worker) and any one of them
    drifting reintroduces the bot talking over a human.
    """
    if not bot_globally_enabled:
        return False, "bot_globally_disabled"
    if conversation.state is State.ADMIN_ACTIVE:
        return False, "handover_toggle_on"
    if conversation.state is State.QUEUED:
        return False, "waiting_for_officer"
    if conversation.agent_run_active:
        return False, "agent_already_running"
    if (
        conversation.resume_watermark_at is not None
        and message_timestamp <= conversation.resume_watermark_at
    ):
        # Messages the citizen sent while a human was handling the conversation
        # already have an owner. Answering them after the toggle flips off would
        # dump a burst of stale replies on someone who has moved on.
        return False, "before_resume_watermark"
    return True, None


def should_notify_officers(
    conversation: ConversationState,
    now: datetime,
    settings: Settings = Settings(),
) -> bool:
    """Debounce queue notifications.

    Five citizens asking for a human within a minute must not produce a burst
    that trains officers to mute the group. One notice per conversation per
    repeat window.
    """
    if conversation.state is not State.QUEUED:
        return False
    if conversation.last_notified_at is None:
        return True
    elapsed = now - conversation.last_notified_at
    return elapsed >= timedelta(minutes=settings.queue_notify_repeat_minutes)


def order_send_queue(pending: list[OutboxEntry]) -> list[OutboxEntry]:
    """Fair ordering across conversations, strict ordering within one.

    A naive FIFO lets one long conversation with a dozen queued lines starve
    everyone else on the single Baileys connection. Round-robin one message per
    conversation per pass keeps latency roughly even while never reordering two
    messages destined for the same person.
    """
    by_conversation: dict[str, list[OutboxEntry]] = {}
    for entry in pending:
        by_conversation.setdefault(entry.conversation_id, []).append(entry)

    ordered: list[OutboxEntry] = []
    while by_conversation:
        for conversation_id in list(by_conversation):
            queue = by_conversation[conversation_id]
            ordered.append(queue.pop(0))
            if not queue:
                del by_conversation[conversation_id]
    return ordered


@dataclass(frozen=True)
class GlobalBotSwitch:
    """Superadmin kill switch (docs/06 §3B).

    Deliberately NOT a per-conversation setting and deliberately not something
    the agent can flip. Use when the bot is misbehaving and the office needs it
    quiet immediately without redeploying.
    """

    enabled: bool = True
    disabled_by: str | None = None
    disabled_at: datetime | None = None
    reason: str | None = None

    def notice_for_citizen(self) -> str | None:
        if self.enabled:
            return None
        return (
            "Layanan otomatis sedang dimatikan sementara. Pesan Anda tetap kami "
            "terima dan akan dibalas petugas pada jam kerja."
        )
