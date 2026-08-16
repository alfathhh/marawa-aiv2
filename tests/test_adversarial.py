"""FULL ADVERSARIAL AUDIT — every module, hostile inputs and edge conditions.

Written to break things, not to confirm them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scripts.answer_formatter import (
    Candidate, format_candidates, format_number, format_single_value, format_trend,
)
from scripts.answer_gate import (
    Evidence, GateContext, evaluate, parse_id_number,
)
from scripts.conversation_state import (
    ConversationState, Event, Settings, State, apply, order_send_queue,
    should_notify_officers,
)
from scripts.outbox_worker import (
    Outcome, SendRecord, SendStatus, classify_result, resolve_unknown,
)
from scripts.scheduler import plan_sweep

NOW = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)


def _ev(**over) -> Evidence:
    base = dict(evidence_id="ev1", value=Decimal("451234"), unit="orang",
                unit_state="known", period="2025", geography="Padang Pariaman",
                source_label="BPS")
    base.update(over)
    return Evidence(**base)


def _ctx(**over) -> GateContext:
    base = dict(evidence=[_ev()], selection_source="candidate_set_ref", query_facts=True)
    base.update(over)
    return GateContext(**base)


def _env(answer: str, **over) -> dict:
    base = {"answer": answer, "answer_type": "official_fact",
            "evidence_ids": ["ev1"], "citations": []}
    base.update(over)
    return base


# ===========================================================================
# GATE — bypass attempts
# ===========================================================================

def test_fabricated_number_in_non_ascii_digits_is_still_caught():
    """A number written in fullwidth or Arabic-Indic digits must not slip past.

    `\\d` in Python matches Unicode digits, so the regex FINDS the token, but if
    Decimal cannot parse it the checker `continue`s — silently skipping a number
    it just found. That is a bypass, not a safe default.
    """
    verdict = evaluate(_env("Jumlah penduduk 2025 adalah ４５２９００ orang."), _ctx())
    assert verdict.blocked, "fullwidth digits bypassed the grounding check"


def test_arabic_indic_digits_are_caught():
    verdict = evaluate(_env("Jumlahnya ٤٥٢٩٠٠ orang pada 2025."), _ctx())
    assert verdict.blocked


def test_negative_figure_not_in_evidence_is_caught():
    verdict = evaluate(_env("Selisihnya -452.900 orang pada 2025."), _ctx())
    assert verdict.blocked


def test_zero_is_a_real_statistic_and_must_be_grounded():
    verdict = evaluate(_env("Nilainya 0 pada 2025."), _ctx())
    assert verdict.blocked, "0 is a claim about the world like any other"

    grounded = evaluate(_env("Nilainya 0 pada 2025."), _ctx(evidence=[_ev(value=Decimal("0"))]))
    assert grounded.allowed, grounded.violations


def test_gate_survives_absurdly_long_input():
    verdict = evaluate(_env("angka " * 20000 + "452.900"), _ctx())
    assert verdict.blocked


def test_evidence_with_non_finite_value_does_not_poison_the_allowed_set():
    """A NaN or Infinity slipping in from a bad cast must not make everything
    pass. It should fail closed."""
    verdict = evaluate(_env("Jumlahnya 452.900 orang pada 2025."),
                       _ctx(evidence=[_ev(value=Decimal("NaN"))]))
    assert verdict.blocked


# ===========================================================================
# FORMATTER
# ===========================================================================

def test_empty_candidate_list_does_not_tell_the_user_to_answer_D1():
    """With zero candidates the hint fell back to a hard-coded "D1" — telling
    a citizen to pick an option that was never shown."""
    text = format_candidates([])
    assert "D1" not in text


def test_candidate_titles_cannot_break_whatsapp_formatting():
    """BPS titles are upstream data; a stray asterisk or newline corrupts the
    rendered message for every candidate after it."""
    nasty = Candidate("D1", "dynamic", "Judul *aneh*\ndengan baris baru", "2025")
    text = format_candidates([nasty])
    lines = [line for line in text.splitlines() if line.startswith("D1.")]
    assert len(lines) == 1, "title newline split the entry across lines"
    assert "*aneh*" not in text, "unescaped asterisks corrupt bold formatting"


def test_zero_value_is_rendered_not_treated_as_missing():
    text = format_single_value(_ev(value=Decimal("0")), "Jumlah Kasus")
    assert "*0 orang*" in text
    assert "tidak tersedia" not in text


def test_negative_number_formats_correctly():
    assert format_number(Decimal("-451234")) == "-451.234"


def test_trend_with_all_rows_unusable_says_not_available():
    rows = [_ev(evidence_id="a", value=None), _ev(evidence_id="b", unit_state="review_required")]
    text = format_trend(rows, "Jumlah Penduduk")
    assert "tidak tersedia" in text or "tidak menebak" in text


# ===========================================================================
# OUTBOX
# ===========================================================================

def test_timeout_without_a_message_id_can_still_be_resolved():
    """A timeout usually means we never received a wa_message_id at all.

    resolve_unknown() keys on record.wa_message_id, so an entry parked by a
    timeout has nothing to match on and stays UNKNOWN forever — the queue grows
    a permanent backlog of messages nobody will ever decide about.
    """
    parked = classify_result(
        SendRecord("o1", "c1", "halo apa kabar", "bot", 3), Outcome.TIMEOUT, NOW
    )
    assert parked.status is SendStatus.UNKNOWN
    assert parked.wa_message_id is None

    resolved = resolve_unknown(parked, echoed_wa_ids=set(), echoed_bodies={"halo apa kabar"})
    assert resolved.status is SendStatus.DELIVERED, (
        "an echo of our own text is proof of delivery even without an id"
    )


def test_already_failed_record_is_not_retried_into_more_attempts():
    failed = SendRecord("o1", "c1", "x", "bot", 3, status=SendStatus.FAILED, attempts=4)
    result = classify_result(failed, Outcome.RATE_LIMITED, NOW)
    assert result.attempts == 4, "a terminal record must not accrue more attempts"


# ===========================================================================
# STATE MACHINE / SCHEDULER
# ===========================================================================

def test_brand_new_conversation_is_not_closed_by_an_idle_sweep():
    """last_activity_at is None on a conversation that has never spoken."""
    fresh = ConversationState("c1", state=State.BOT_ACTIVE, last_activity_at=None)
    result = apply(fresh, Event.IDLE_TIMEOUT, NOW)
    assert result.state.state is State.BOT_ACTIVE


def test_sweep_does_not_crash_on_naive_timestamps():
    naive = ConversationState("c1", state=State.BOT_ACTIVE,
                              last_activity_at=datetime(2026, 8, 17, 9, 0))
    plan = plan_sweep([naive], NOW, Settings())
    assert isinstance(plan, list)


def test_notification_clock_skew_does_not_permanently_mute_officers():
    future = ConversationState("c1", state=State.QUEUED,
                               last_notified_at=NOW + timedelta(hours=5))
    assert should_notify_officers(future, NOW) is False
    assert should_notify_officers(future, NOW + timedelta(hours=6)) is True


def test_order_send_queue_handles_empty_and_single():
    assert order_send_queue([]) == []
