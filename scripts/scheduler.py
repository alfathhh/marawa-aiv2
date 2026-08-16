#!/usr/bin/env python3
"""Sweep — decides which conversations need a time-based event applied.

WHY THIS EXISTS
---------------
Every timeout in this system (citizen idle, queue expiry, handover
auto-revert) only fires if SOMETHING periodically asks "has enough time passed
yet?". Without a sweep, `Event.IDLE_TIMEOUT` and `Event.AUTO_REVERT_CHECK` are
dead code — correct handlers that nothing ever calls, and every timeout
guarantee documented in docs/06 quietly stops being true.

Pure decision functions here; `scripts/app.py` wires them into a callable
endpoint (`POST /internal/sweep`) that a cron-like caller hits every minute.
No real scheduler is wired to a clock in this stage — see docs/14 Slice 1
Week 1 for where that lands (APScheduler or a simple `while True: sleep`
loop in the worker process).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from datetime import timezone

from scripts.conversation_state import ConversationState, Settings, State


def _aware(value: datetime | None) -> datetime | None:
    """AUDIT P: mixing naive and aware datetimes raises TypeError mid-sweep,
    which aborts the whole pass — so ONE conversation with a naive timestamp
    stops every other conversation's timeout from ever firing. Assume UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SweepItem:
    conversation_id: str
    event: str  # "idle_timeout" | "auto_revert_check"
    reason: str


def due_for_idle_timeout(
    conversation: ConversationState, now: datetime, settings: Settings
) -> bool:
    """BOT_ACTIVE conversations idle past the citizen timeout."""
    if conversation.state is not State.BOT_ACTIVE:
        return False
    last = _aware(conversation.last_activity_at)
    if last is None:
        return False
    return _aware(now) - last >= timedelta(minutes=settings.citizen_idle_minutes)


def due_for_queue_check(conversation: ConversationState) -> bool:
    """QUEUED conversations always get evaluated; the state machine itself
    decides (via its own queue_expiry_minutes math) whether that means closing
    or just continuing to wait. See `_on_idle_timeout` in conversation_state.py.
    """
    return conversation.state is State.QUEUED


def due_for_auto_revert(
    conversation: ConversationState, now: datetime, settings: Settings
) -> bool:
    """ADMIN_ACTIVE conversations where nobody has replied in a while."""
    if conversation.state is not State.ADMIN_ACTIVE:
        return False
    reference = _aware(conversation.last_admin_activity_at or conversation.bot_paused_at)
    if reference is None:
        return False
    return _aware(now) - reference >= timedelta(minutes=settings.handover_auto_revert_minutes)


def plan_sweep(
    conversations: list[ConversationState],
    now: datetime,
    settings: Settings = Settings(),
) -> list[SweepItem]:
    """One pass: which conversations need which event applied right now.

    Deliberately returns a PLAN rather than applying anything — the caller
    (app.py) owns calling `apply()` so that outbox cancellation and audit
    logging happen in the same place as every other transition, instead of
    this module quietly duplicating that wiring.
    """
    items: list[SweepItem] = []
    for conversation in conversations:
        # One bad row must never abort the sweep for everyone else.
        if due_for_idle_timeout(conversation, now, settings):
            items.append(SweepItem(conversation.conversation_id, "idle_timeout", "citizen_idle"))
        elif due_for_queue_check(conversation):
            items.append(SweepItem(conversation.conversation_id, "idle_timeout", "queue_check"))
        if due_for_auto_revert(conversation, now, settings):
            items.append(SweepItem(conversation.conversation_id, "auto_revert_check", "handover_forgotten"))
    return items
