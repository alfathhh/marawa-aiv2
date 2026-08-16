#!/usr/bin/env python3
"""Notification sink — turns state-machine effect strings into actual sends.

Without this, "notify_officers" and "notify_citizen_*" appearing in a
Transition.effects list are just names nobody acts on. This module is the
seam where those names become an outbound WhatsApp message (to the officer
group, per docs/06 §0.5) or an outbox entry (to the citizen).

Kept separate from conversation_state.py on purpose: the state machine decides
WHETHER to notify (that is domain logic worth unit-testing in isolation); this
module decides HOW and WHERE (that is wiring, and changes independently — a
future channel like email doesn't touch conversation_state.py at all).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from scripts.conversation_state import ConversationState, Settings, should_notify_officers


class NotificationChannel(Protocol):
    def send_to_officer_group(self, text: str) -> None: ...
    def send_to_citizen(self, conversation_id: str, text: str) -> None: ...


@dataclass
class InMemoryChannel:
    """Test/demo channel. Captures what WOULD have been sent."""

    officer_messages: list[str] = field(default_factory=list)
    citizen_messages: list[tuple[str, str]] = field(default_factory=list)

    def send_to_officer_group(self, text: str) -> None:
        self.officer_messages.append(text)

    def send_to_citizen(self, conversation_id: str, text: str) -> None:
        self.citizen_messages.append((conversation_id, text))


QUEUE_NOTICE_TEMPLATE = "🔔 1 chat menunggu petugas — buka dashboard untuk membalas.\n{conversation_id}"
AUTO_REVERT_NOTICE_TEMPLATE = (
    "⚠️ Toggle handover pada percakapan {conversation_id} otomatis dimatikan "
    "setelah {minutes} menit tanpa aktivitas. Bot sudah aktif kembali."
)


def dispatch_effects(
    effects: list[str],
    conversation: ConversationState,
    now: datetime,
    channel: NotificationChannel,
    settings: Settings = Settings(),
    citizen_text_by_effect: dict[str, str] | None = None,
) -> bool:
    """Turn effect names into sends. Returns whether an officer notice fired.

    `citizen_text_by_effect` lets the caller supply the exact wording (office
    hours vs after-hours, docs/06 §0) without this module hard-coding copy.
    """
    citizen_text_by_effect = citizen_text_by_effect or {}
    notified = False

    if "notify_officers" in effects:
        if should_notify_officers(conversation, now, settings):
            channel.send_to_officer_group(
                QUEUE_NOTICE_TEMPLATE.format(conversation_id=conversation.conversation_id)
            )
            notified = True
        # Debounce means "no send this time" — this is not a failure, so no
        # violation/log is raised here. The caller updates last_notified_at
        # only when `notified` is True (see app.py wiring).

    if "notify_officers_auto_revert" in effects:
        channel.send_to_officer_group(
            AUTO_REVERT_NOTICE_TEMPLATE.format(
                conversation_id=conversation.conversation_id,
                minutes=settings.handover_auto_revert_minutes,
            )
        )

    for effect_name in ("notify_citizen_handover", "notify_citizen_bot_resumed", "send_handover_notice"):
        if effect_name in effects and effect_name in citizen_text_by_effect:
            channel.send_to_citizen(conversation.conversation_id, citizen_text_by_effect[effect_name])

    return notified
