"""Tests for the conversation state machine.

Every race condition in docs/06 §0.7 has a test here. If one of these fails, the
bot and a human officer can answer the same citizen at the same time.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from scripts.conversation_state import (
    ConversationState,
    Event,
    OutboxEntry,
    Rejected,
    Settings,
    State,
    GlobalBotSwitch,
    apply,
    authorize_send,
    order_send_queue,
    should_notify_officers,
    should_run_agent,
    classify_from_me,
    handover_notice,
    is_within_office_hours,
    should_ignore_inbound,
    validate_settings,
)

WORKDAY = datetime(2026, 8, 17, 10, 0)     # Monday 10:00
AFTER_HOURS = datetime(2026, 8, 17, 20, 0)  # Monday 20:00
SATURDAY = datetime(2026, 8, 15, 10, 0)


def _conv(**overrides) -> ConversationState:
    base = dict(conversation_id="c1", state=State.BOT_ACTIVE, state_version=3)
    base.update(overrides)
    return ConversationState(**base)


# ---------------------------------------------------------------------------
# Race condition 1 — two officers claim at once
# ---------------------------------------------------------------------------

def test_second_claim_loses_on_stale_version():
    conv = _conv()
    first = apply(conv, Event.HANDOVER_ON, WORKDAY, admin_id="budi", expected_version=3)
    assert first.state.state is State.ADMIN_ACTIVE
    assert first.state.state_version == 4

    with pytest.raises(Rejected) as exc:
        apply(first.state, Event.HANDOVER_ON, WORKDAY, admin_id="sari", expected_version=3)
    assert exc.value.code == "CONVERSATION_STATE_CONFLICT"


def test_toggle_by_other_admin_says_who_holds_it():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    with pytest.raises(Rejected) as exc:
        apply(conv, Event.HANDOVER_ON, WORKDAY, admin_id="sari", expected_version=3)
    assert exc.value.code == "ALREADY_HELD"
    assert "budi" in str(exc.value)


def test_toggle_on_twice_by_same_admin_is_idempotent():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    result = apply(conv, Event.HANDOVER_ON, WORKDAY, admin_id="budi", expected_version=3)
    assert result.state.state_version == 3  # no needless bump


# ---------------------------------------------------------------------------
# Race condition 2 — citizen asks for admin while the bot is drafting
# ---------------------------------------------------------------------------

def test_handover_cancels_pending_bot_outbox():
    result = apply(_conv(), Event.REQUEST_HANDOVER, WORKDAY)
    assert result.state.state is State.QUEUED
    assert result.cancel_pending_bot_outbox
    assert result.notify_officers


def test_bot_outbox_enqueued_before_handover_is_refused():
    conv = _conv()
    entry = OutboxEntry("o1", "c1", "jawaban bot", "bot", state_version_at_enqueue=3)
    after = apply(conv, Event.REQUEST_HANDOVER, WORKDAY).state
    allowed, reason = authorize_send(entry, after)
    assert not allowed
    assert reason == "state_changed_since_enqueue"


def test_bot_outbox_refused_when_admin_active_even_at_same_version():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi")
    entry = OutboxEntry("o1", "c1", "jawaban bot", "bot", state_version_at_enqueue=3)
    allowed, reason = authorize_send(entry, conv)
    assert not allowed
    assert reason == "handover_preempted"


# ---------------------------------------------------------------------------
# Race condition 3 — officer replies from the phone
# ---------------------------------------------------------------------------

def test_from_me_forces_admin_active_and_silences_bot():
    result = apply(_conv(), Event.FROM_ME_DETECTED, WORKDAY, admin_id="budi")
    assert result.state.state is State.ADMIN_ACTIVE
    assert result.cancel_pending_bot_outbox
    assert "audit_phone_takeover" in result.effects


def test_our_own_send_is_not_treated_as_human_takeover():
    assert classify_from_me("wa_1", {"wa_1", "wa_2"}) == "echo_of_our_own_send"
    assert classify_from_me("wa_9", {"wa_1", "wa_2"}) == "human_typed"


# ---------------------------------------------------------------------------
# Race condition 4 — admin outbound after return-to-bot
# ---------------------------------------------------------------------------

def test_admin_outbox_refused_after_return_to_bot():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    entry = OutboxEntry("o1", "c1", "balasan", "admin", 3, sender_admin_id="budi")
    after = apply(conv, Event.HANDOVER_OFF, WORKDAY, admin_id="budi").state
    allowed, reason = authorize_send(entry, after)
    assert not allowed


def test_admin_cannot_reply_to_conversation_owned_by_someone_else():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    with pytest.raises(Rejected) as exc:
        apply(conv, Event.ADMIN_REPLY, WORKDAY, admin_id="sari")
    assert exc.value.code == "NOT_ASSIGNED"


def test_admin_cannot_reply_before_toggling_handover_on():
    with pytest.raises(Rejected) as exc:
        apply(_conv(), Event.ADMIN_REPLY, WORKDAY, admin_id="budi")
    assert exc.value.code == "HANDOVER_NOT_ON"


# ---------------------------------------------------------------------------
# Bot silence invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", [State.ADMIN_ACTIVE, State.QUEUED])
def test_agent_never_runs_while_a_human_is_involved(state):
    conv = _conv(state=state, assigned_admin_id="budi" if state is State.ADMIN_ACTIVE else None)
    result = apply(conv, Event.INBOUND, WORKDAY)
    assert "run_agent" not in result.effects


def test_bot_resumes_after_admin_returns():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    returned = apply(conv, Event.HANDOVER_OFF, WORKDAY, admin_id="budi").state
    assert returned.state is State.BOT_ACTIVE
    assert returned.assigned_admin_id is None
    result = apply(returned, Event.INBOUND, WORKDAY)
    assert "run_agent" in result.effects


# ---------------------------------------------------------------------------
# Idle timeout
# ---------------------------------------------------------------------------

def test_idle_timeout_never_closes_a_conversation_a_human_is_handling():
    conv = _conv(state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi")
    with pytest.raises(Rejected) as exc:
        apply(conv, Event.IDLE_TIMEOUT, WORKDAY)
    assert exc.value.code == "IDLE_NOT_APPLICABLE"


def test_idle_timeout_on_queued_marks_abandoned():
    conv = _conv(state=State.QUEUED)
    result = apply(conv, Event.IDLE_TIMEOUT, WORKDAY)
    assert result.state.state is State.IDLE_CLOSED
    assert "mark_queue_abandoned" in result.effects


# ---------------------------------------------------------------------------
# Pairing cutoff
# ---------------------------------------------------------------------------

def test_history_replayed_at_pairing_is_ignored():
    cutoff = datetime(2026, 8, 17, 9, 0)
    old = datetime(2026, 5, 1, 12, 0)
    assert should_ignore_inbound(old, cutoff)
    assert not should_ignore_inbound(datetime(2026, 8, 17, 9, 1), cutoff)


def test_no_cutoff_means_accept_everything():
    assert not should_ignore_inbound(datetime(2020, 1, 1), None)


# ---------------------------------------------------------------------------
# Office hours honesty
# ---------------------------------------------------------------------------

def test_office_hours_detection():
    settings = Settings()
    assert is_within_office_hours(WORKDAY, settings)
    assert not is_within_office_hours(AFTER_HOURS, settings)
    assert not is_within_office_hours(SATURDAY, settings)


def test_after_hours_notice_does_not_promise_an_immediate_reply():
    notice = handover_notice(AFTER_HOURS, Settings())
    assert "jam kerja berikutnya" in notice
    assert "tunggu sebentar" not in notice


def test_office_hours_notice_is_the_short_one():
    assert "tunggu sebentar" in handover_notice(WORKDAY, Settings())


# ---------------------------------------------------------------------------
# Settings bounds (docs/06 §3C)
# ---------------------------------------------------------------------------

def test_settings_out_of_range_rejected():
    assert validate_settings({"citizen_idle_minutes": 999})
    assert validate_settings({"citizen_idle_minutes": 0})
    assert not validate_settings({"citizen_idle_minutes": 5})


def test_settings_not_exposed_to_dashboard_are_rejected():
    errors = validate_settings({"statement_timeout": 60000})
    assert errors and "bukan setelan" in errors[0]


def test_boolean_is_not_an_integer():
    assert validate_settings({"citizen_idle_minutes": True})


# ===========================================================================
# SELF-AUDIT 15 Aug — tests written to break the implementation above.
# All five failed on first run. See docs/06 §0.8 for the findings.
# ===========================================================================

def test_phone_takeover_does_not_strand_the_conversation():
    """AUDIT A: after a takeover from the phone, nobody owns the conversation.

    `fromMe` gives no admin identity, so assigned_admin_id becomes None while
    the state is ADMIN_ACTIVE. Claiming then compares None against the officer's
    id and rejects, and replying does the same — the conversation is stuck
    forever with a citizen waiting in it.
    """
    after_phone = apply(_conv(), Event.FROM_ME_DETECTED, WORKDAY, admin_id=None).state
    assert after_phone.state is State.ADMIN_ACTIVE
    assert after_phone.assigned_admin_id is None

    replied = apply(after_phone, Event.ADMIN_REPLY, WORKDAY, admin_id="budi")
    assert "enqueue_admin_outbound" in replied.effects
    assert replied.state.assigned_admin_id == "budi"


def test_queued_citizen_is_not_timed_out_for_our_slowness():
    """AUDIT B: the 5-minute idle timer punished the citizen for waiting on us.

    A citizen who asked for a human is not idle — they are waiting. Closing the
    session at 5 minutes and marking it abandoned means an officer returning
    from a short break finds a closed case and a citizen who was told nothing.
    """
    queued = _conv(state=State.QUEUED, handover_requested_at=WORKDAY)
    five_minutes_later = WORKDAY + timedelta(minutes=5)
    result = apply(queued, Event.IDLE_TIMEOUT, five_minutes_later)
    assert result.state.state is State.QUEUED, "queued conversations must not idle-close"

    much_later = WORKDAY + timedelta(hours=5)
    expired = apply(queued, Event.IDLE_TIMEOUT, much_later)
    assert expired.state.state is State.IDLE_CLOSED


def test_idle_notice_can_actually_be_sent():
    """AUDIT C: the closing notice was unsendable.

    _on_idle_timeout emits "send_idle_notice" and moves to IDLE_CLOSED, but
    authorize_send refused everything in IDLE_CLOSED — so the message telling
    the citizen the session ended could never leave. Circular by construction.
    """
    result = apply(_conv(), Event.IDLE_TIMEOUT, WORKDAY)
    notice = OutboxEntry(
        "o1", "c1", "sesi berakhir", "system",
        state_version_at_enqueue=result.state.state_version,
    )
    allowed, reason = authorize_send(notice, result.state)
    assert allowed, f"idle notice blocked: {reason}"


def test_handover_notice_never_shows_a_raw_placeholder():
    """AUDIT D: the after-hours notice returned an unformatted {form_url}."""
    notice = handover_notice(AFTER_HOURS, Settings(), form_url="https://s.id/form-pst")
    assert "{" not in notice and "}" not in notice
    assert "https://s.id/form-pst" in notice


def test_two_rapid_messages_cannot_produce_two_bot_answers():
    """AUDIT E: version guard alone does not prevent concurrent agent runs.

    Inbound during BOT_ACTIVE deliberately does not bump the version, so two
    messages arriving together produce two agent runs that both authorize at the
    same version and both send. The citizen gets two answers to one question.
    """
    conv = _conv()
    first = apply(conv, Event.INBOUND, WORKDAY)
    assert first.state.agent_run_active is True

    second = apply(first.state, Event.INBOUND, WORKDAY + timedelta(seconds=1))
    assert "run_agent" not in second.effects
    assert "queue_followup" in second.effects


# ===========================================================================
# TOGGLE MODEL — failure modes specific to a manual on/off switch
# ===========================================================================

def _held(**overrides) -> ConversationState:
    base = dict(
        state=State.ADMIN_ACTIVE, assigned_admin_id="budi", bot_paused_by="budi",
        bot_paused_at=WORKDAY, last_admin_activity_at=WORKDAY,
    )
    base.update(overrides)
    return _conv(**base)


def test_forgotten_toggle_auto_reverts_so_the_citizen_is_not_stranded():
    """The defining failure of a manual switch: someone flips it and forgets.

    Bot is off, the officer moves on to other work, and the citizen waits
    forever with nobody answering. Claim/return had the same hole; the toggle
    just makes it easier to hit.
    """
    held = _held()
    still_held = apply(held, Event.AUTO_REVERT_CHECK, WORKDAY + timedelta(minutes=10))
    assert still_held.state.state is State.ADMIN_ACTIVE

    reverted = apply(held, Event.AUTO_REVERT_CHECK, WORKDAY + timedelta(minutes=31))
    assert reverted.state.state is State.BOT_ACTIVE
    assert "notify_officers_auto_revert" in reverted.effects
    assert "notify_citizen_bot_resumed" in reverted.effects


def test_active_admin_is_never_interrupted_by_auto_revert():
    held = _held()
    replied = apply(held, Event.ADMIN_REPLY, WORKDAY + timedelta(minutes=25), admin_id="budi")
    checked = apply(replied.state, Event.AUTO_REVERT_CHECK, WORKDAY + timedelta(minutes=40))
    assert checked.state.state is State.ADMIN_ACTIVE, "replying must extend the window"


def test_bot_does_not_answer_the_backlog_when_the_toggle_flips_off():
    """Without a watermark the bot dumps a burst of stale replies.

    Citizen sends 3 messages while a human handles the chat. Officer flips the
    toggle off. The bot must pick up from NOW, not work through the backlog the
    officer already dealt with.
    """
    held = _held()
    resumed = apply(held, Event.HANDOVER_OFF, WORKDAY + timedelta(minutes=20), admin_id="budi").state
    assert resumed.resume_watermark_at is not None

    old_message = WORKDAY + timedelta(minutes=5)
    run, reason = should_run_agent(resumed, old_message)
    assert not run and reason == "before_resume_watermark"

    new_message = WORKDAY + timedelta(minutes=25)
    run, _ = should_run_agent(resumed, new_message)
    assert run


def test_toggle_records_who_switched_it():
    result = apply(_conv(), Event.HANDOVER_ON, WORKDAY, admin_id="sari")
    assert result.state.bot_paused_by == "sari"
    assert "audit_handover_on" in result.effects
    assert "notify_citizen_handover" in result.effects


def test_toggle_off_when_not_held_is_rejected():
    with pytest.raises(Rejected) as exc:
        apply(_conv(), Event.HANDOVER_OFF, WORKDAY, admin_id="budi")
    assert exc.value.code == "NOT_HELD"


# ---------------------------------------------------------------------------
# Many citizens at once
# ---------------------------------------------------------------------------

def test_global_kill_switch_silences_every_conversation():
    run, reason = should_run_agent(_conv(), WORKDAY, bot_globally_enabled=False)
    assert not run and reason == "bot_globally_disabled"
    assert GlobalBotSwitch(enabled=False).notice_for_citizen() is not None
    assert GlobalBotSwitch().notice_for_citizen() is None


def test_one_busy_conversation_does_not_starve_the_others():
    """Single Baileys connection: naive FIFO lets one chat monopolise it."""
    pending = [
        OutboxEntry(f"a{i}", "chat_a", f"a{i}", "bot", 1) for i in range(5)
    ] + [OutboxEntry("b1", "chat_b", "b1", "bot", 1), OutboxEntry("c1", "chat_c", "c1", "bot", 1)]

    ordered = order_send_queue(pending)
    positions = [e.conversation_id for e in ordered]
    assert positions.index("chat_b") < 3, "chat_b waited behind a backlog"
    assert positions.index("chat_c") < 3

    # Ordering WITHIN a conversation must never be shuffled.
    a_order = [e.outbox_id for e in ordered if e.conversation_id == "chat_a"]
    assert a_order == ["a0", "a1", "a2", "a3", "a4"]


def test_notification_storm_is_debounced():
    """Five citizens queueing at once must not produce a burst of pings."""
    just_notified = _conv(state=State.QUEUED, last_notified_at=WORKDAY)
    assert not should_notify_officers(just_notified, WORKDAY + timedelta(minutes=1))
    assert should_notify_officers(just_notified, WORKDAY + timedelta(minutes=6))
    never_notified = _conv(state=State.QUEUED, last_notified_at=None)
    assert should_notify_officers(never_notified, WORKDAY)


def test_bot_never_answers_while_a_human_holds_the_conversation():
    for state, expected in (
        (State.ADMIN_ACTIVE, "handover_toggle_on"),
        (State.QUEUED, "waiting_for_officer"),
    ):
        run, reason = should_run_agent(_conv(state=state), WORKDAY)
        assert not run and reason == expected


def test_second_concurrent_message_does_not_start_a_second_run():
    run, reason = should_run_agent(_conv(agent_run_active=True), WORKDAY)
    assert not run and reason == "agent_already_running"
