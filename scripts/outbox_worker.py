#!/usr/bin/env python3
"""Outbox worker decision logic — the layer between the database and Baileys.

Pure functions. No network, no DB. The worker shell calls these; all the
reasoning that is easy to get wrong lives here where it can be tested.

WHY AN OUTBOX AT ALL
--------------------
Because WhatsApp fails in the most awkward way possible: the send succeeds on
their side and the response never reaches us. Any design that calls Baileys
directly from a request handler will, on that day, either drop the reply or send
it twice. Both are visible to a citizen, and the second one is worse — a bot
that repeats itself looks broken in a way that people screenshot.

The rule that carries the whole design: **a message is written to the database
first, sent second, and marked sent third.** Every failure lands somewhere the
worker can reason about.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class SendStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"        # a worker holds a lease on it
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"        # sent, but we never learned the outcome


class Outcome(str, Enum):
    """What Baileys told us, normalised."""

    ACK = "ack"
    RATE_LIMITED = "rate_limited"
    DISCONNECTED = "disconnected"
    INVALID_RECIPIENT = "invalid_recipient"
    TIMEOUT = "timeout"
    UNKNOWN_ERROR = "unknown_error"


# Retry policy. Deliberately short and finite: a statistics answer that arrives
# forty minutes late is not useful, and retrying forever hides a real outage.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (5, 20, 60)
# A claimed entry whose worker died must return to the pool, but not so fast
# that a slow send gets picked up twice.
LEASE_SECONDS = 120


@dataclass
class SendRecord:
    outbox_id: str
    conversation_id: str
    body: str
    sender_type: str
    state_version_at_enqueue: int
    status: SendStatus = SendStatus.PENDING
    attempts: int = 0
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    next_attempt_at: datetime | None = None
    wa_message_id: str | None = None
    idempotency_key: str | None = None
    last_error: str | None = None


def idempotency_key(conversation_id: str, body: str, state_version: int) -> str:
    """Stable key for one logical send.

    Includes state_version so that the same text sent legitimately twice (a
    citizen asks the same thing again after a handover) is NOT collapsed into
    one message, while a retry of the same logical send is.
    """
    digest = hashlib.sha256(
        f"{conversation_id}\x00{state_version}\x00{body}".encode("utf-8")
    ).hexdigest()
    return f"ob_{digest[:32]}"


def is_duplicate_send(candidate: SendRecord, recent: list[SendRecord]) -> bool:
    """Has this exact logical message already left the building?"""
    key = candidate.idempotency_key or idempotency_key(
        candidate.conversation_id, candidate.body, candidate.state_version_at_enqueue
    )
    for record in recent:
        if record.outbox_id == candidate.outbox_id:
            continue
        existing = record.idempotency_key or idempotency_key(
            record.conversation_id, record.body, record.state_version_at_enqueue
        )
        if existing == key and record.status in (
            SendStatus.SENT, SendStatus.DELIVERED, SendStatus.CLAIMED, SendStatus.UNKNOWN
        ):
            return True
    return False


def claimable(record: SendRecord, now: datetime) -> bool:
    """Can this worker take the entry?

    Covers the case everyone forgets: a worker that crashed mid-send left the
    row CLAIMED forever. The lease expiry is what unsticks it.
    """
    if record.status is SendStatus.PENDING:
        return record.next_attempt_at is None or record.next_attempt_at <= now
    if record.status is SendStatus.CLAIMED:
        if record.claimed_at is None:
            return True
        return now - record.claimed_at >= timedelta(seconds=LEASE_SECONDS)
    return False


def next_attempt_delay(attempts: int) -> timedelta | None:
    """None means: stop trying."""
    if attempts >= MAX_ATTEMPTS:
        return None
    index = min(attempts - 1, len(BACKOFF_SECONDS) - 1)
    return timedelta(seconds=BACKOFF_SECONDS[max(index, 0)])


def classify_result(
    record: SendRecord,
    outcome: Outcome,
    now: datetime,
    wa_message_id: str | None = None,
) -> SendRecord:
    """Fold a send result back into the record."""
    # AUDIT N: a terminal record must be inert. Without this, a stray late
    # callback on an already-FAILED entry incremented attempts and could flip it
    # back to PENDING, resurrecting a message the system had given up on.
    if record.status in (SendStatus.FAILED, SendStatus.CANCELLED, SendStatus.DELIVERED):
        return record
    attempts = record.attempts + 1

    if outcome is Outcome.ACK:
        return _replace(record, status=SendStatus.SENT, attempts=attempts,
                        wa_message_id=wa_message_id, claimed_at=None, claimed_by=None,
                        next_attempt_at=None, last_error=None)

    if outcome is Outcome.INVALID_RECIPIENT:
        # Retrying cannot help and each attempt is a wasted call.
        return _replace(record, status=SendStatus.FAILED, attempts=attempts,
                        claimed_at=None, claimed_by=None, next_attempt_at=None,
                        last_error="invalid_recipient")

    if outcome is Outcome.TIMEOUT:
        # The dangerous one: WhatsApp may well have delivered it. Retrying blind
        # is how a citizen receives the same answer twice. Park it as UNKNOWN and
        # let reconciliation against inbound `fromMe` echoes settle it.
        return _replace(record, status=SendStatus.UNKNOWN, attempts=attempts,
                        claimed_at=None, claimed_by=None, next_attempt_at=None,
                        last_error="timeout_outcome_unknown")

    delay = next_attempt_delay(attempts)
    if delay is None:
        return _replace(record, status=SendStatus.FAILED, attempts=attempts,
                        claimed_at=None, claimed_by=None, next_attempt_at=None,
                        last_error=outcome.value)
    return _replace(record, status=SendStatus.PENDING, attempts=attempts,
                    claimed_at=None, claimed_by=None,
                    next_attempt_at=now + delay, last_error=outcome.value)


def resolve_unknown(
    record: SendRecord,
    echoed_wa_ids: set[str],
    echoed_bodies: set[str] | None = None,
) -> SendRecord:
    """Settle an UNKNOWN using the `fromMe` echo WhatsApp sends back.

    AUDIT M: matching on wa_message_id alone was unreachable in the exact case
    it exists for. A TIMEOUT means the response never arrived, so there IS no
    wa_message_id — those records could never be resolved and accumulated
    forever as a backlog nobody would ever decide about.

    Body matching covers that gap: seeing our own text echoed back is proof of
    delivery even when we never learned the id. Exact match only, so a citizen
    quoting us does not accidentally resolve anything.

    Still never auto-resends: an unresolved UNKNOWN stays UNKNOWN for a human
    or the conversation layer to decide, because a silent resend is the
    duplicate this whole design exists to prevent.
    """
    if record.wa_message_id and record.wa_message_id in echoed_wa_ids:
        return _replace(record, status=SendStatus.DELIVERED, last_error=None)
    if echoed_bodies and record.body in echoed_bodies:
        return _replace(record, status=SendStatus.DELIVERED, last_error=None)
    return record


def inbound_is_our_echo(wa_message_id: str, sent_wa_ids: set[str]) -> bool:
    """Distinguishes our own outbound echo from an officer typing on the phone.

    Getting this backwards has two failure modes and both are bad: treat our own
    echo as a human and the bot goes silent for no reason; treat a human as our
    echo and the bot talks over the officer.
    """
    return wa_message_id in sent_wa_ids


@dataclass
class WorkerHealth:
    connected: bool
    last_successful_send_at: datetime | None = None
    consecutive_failures: int = 0
    pending_count: int = 0
    oldest_pending_age_seconds: int = 0
    notes: list[str] = field(default_factory=list)

    def status(self) -> str:
        """What the dashboard shows both roles (docs/06 §3.0a)."""
        if not self.connected:
            return "TERPUTUS"
        if self.consecutive_failures >= 3:
            return "BERMASALAH"
        if self.oldest_pending_age_seconds > 300:
            return "TERTUNDA"
        return "NORMAL"

    def should_alert(self) -> bool:
        return self.status() in ("TERPUTUS", "BERMASALAH")


def _replace(record: SendRecord, **changes) -> SendRecord:
    data = record.__dict__.copy()
    data.update(changes)
    return SendRecord(**data)
