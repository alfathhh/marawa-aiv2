"""Tests for the outbox worker and the answer formatter."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from scripts.answer_formatter import (
    Candidate, format_candidates, format_number, format_single_value,
    format_trend, system_counts_for,
)
from scripts.answer_gate import Evidence, GateContext, evaluate
from scripts.outbox_worker import (
    MAX_ATTEMPTS, Outcome, SendRecord, SendStatus, WorkerHealth, claimable,
    classify_result, idempotency_key, inbound_is_our_echo, is_duplicate_send,
    next_attempt_delay, resolve_unknown,
)

NOW = datetime(2026, 8, 17, 10, 0)


def _rec(**over) -> SendRecord:
    base = dict(outbox_id="o1", conversation_id="c1", body="halo",
                sender_type="bot", state_version_at_enqueue=3)
    base.update(over)
    return SendRecord(**base)


# --------------------------- outbox: idempotency ---------------------------

def test_retry_of_the_same_send_collapses_but_a_genuine_repeat_does_not():
    same = idempotency_key("c1", "halo", 3) == idempotency_key("c1", "halo", 3)
    assert same
    # Same text after the state moved on is a NEW message, not a duplicate.
    assert idempotency_key("c1", "halo", 3) != idempotency_key("c1", "halo", 4)


def test_already_sent_message_is_not_sent_again():
    already = _rec(outbox_id="o0", status=SendStatus.SENT)
    assert is_duplicate_send(_rec(), [already])
    assert not is_duplicate_send(_rec(), [_rec(outbox_id="o0", status=SendStatus.FAILED)])


def test_timeout_is_parked_not_retried_blindly():
    """The dangerous case: WhatsApp may have delivered it already."""
    result = classify_result(_rec(), Outcome.TIMEOUT, NOW)
    assert result.status is SendStatus.UNKNOWN
    assert result.next_attempt_at is None, "blind retry would double-send"


def test_unknown_resolves_from_the_echo():
    parked = _rec(status=SendStatus.UNKNOWN, wa_message_id="wa_7")
    assert resolve_unknown(parked, {"wa_7"}).status is SendStatus.DELIVERED
    assert resolve_unknown(parked, {"wa_9"}).status is SendStatus.UNKNOWN


def test_invalid_recipient_stops_immediately():
    result = classify_result(_rec(), Outcome.INVALID_RECIPIENT, NOW)
    assert result.status is SendStatus.FAILED
    assert result.attempts == 1


def test_transient_failure_backs_off_then_gives_up():
    record = _rec()
    for _ in range(MAX_ATTEMPTS - 1):
        record = classify_result(record, Outcome.RATE_LIMITED, NOW)
        assert record.status is SendStatus.PENDING
        assert record.next_attempt_at > NOW
    record = classify_result(record, Outcome.RATE_LIMITED, NOW)
    assert record.status is SendStatus.FAILED
    assert next_attempt_delay(MAX_ATTEMPTS) is None


def test_crashed_worker_does_not_wedge_the_queue_forever():
    stuck = _rec(status=SendStatus.CLAIMED, claimed_at=NOW - timedelta(minutes=5))
    assert claimable(stuck, NOW)
    fresh = _rec(status=SendStatus.CLAIMED, claimed_at=NOW - timedelta(seconds=5))
    assert not claimable(fresh, NOW)


def test_backoff_is_respected_before_the_next_attempt():
    waiting = _rec(next_attempt_at=NOW + timedelta(seconds=30))
    assert not claimable(waiting, NOW)
    assert claimable(waiting, NOW + timedelta(seconds=31))


def test_our_own_echo_is_not_mistaken_for_a_human():
    assert inbound_is_our_echo("wa_1", {"wa_1"})
    assert not inbound_is_our_echo("wa_typed_by_officer", {"wa_1"})


def test_connection_status_is_visible_and_actionable():
    assert WorkerHealth(connected=False).status() == "TERPUTUS"
    assert WorkerHealth(connected=True, consecutive_failures=3).status() == "BERMASALAH"
    assert WorkerHealth(connected=True, oldest_pending_age_seconds=600).status() == "TERTUNDA"
    assert WorkerHealth(connected=True).status() == "NORMAL"
    assert WorkerHealth(connected=False).should_alert()


# --------------------------- formatter ---------------------------

def _ev(**over) -> Evidence:
    base = dict(evidence_id="ev1", value=Decimal("462125"), unit="orang",
                unit_state="known", period="2025",
                geography="Kabupaten Padang Pariaman",
                source_label="BPS Kabupaten Padang Pariaman — SIMDASI")
    base.update(over)
    return Evidence(**base)


def test_indonesian_number_formatting():
    assert format_number(462125) == "462.125"
    assert format_number(Decimal("1234567.89"), 2) == "1.234.567,89"
    assert format_number(7) == "7"


def test_single_value_prints_period_and_unit():
    text = format_single_value(_ev(), "Jumlah Penduduk", updated_label="14 Agustus 2026")
    assert "462.125 orang" in text
    assert "2025" in text
    assert "Sumber:" in text


def test_formatter_refuses_to_print_a_guessed_unit():
    """HARD RULE reaches the formatter too, not just the gate."""
    text = format_single_value(_ev(unit_state="review_required", unit="miliar rupiah"), "PDRB")
    assert "462.125" not in text
    assert "satuannya belum dikonfirmasi" in text


def test_formatter_says_not_available_when_there_is_no_value():
    text = format_single_value(_ev(value=None), "Jumlah Sapi")
    assert "tidak tersedia" in text
    assert "tidak menebak" in text


def test_trend_skips_unpublishable_rows_and_says_so():
    rows = [
        _ev(evidence_id="a", period="2023", value=Decimal("450000")),
        _ev(evidence_id="b", period="2024", value=Decimal("457820")),
        _ev(evidence_id="c", period="2025", value=None),
    ]
    text = format_trend(rows, "Jumlah Penduduk")
    assert "2023" in text and "2024" in text
    assert "1 periode tidak ditampilkan" in text


def test_candidate_list_never_leaks_a_figure():
    candidates = [
        Candidate("D1", "dynamic", "Jumlah penduduk per kecamatan", "2002–2025"),
        Candidate("S1", "simdasi", "Penduduk dan kepadatan", "2018–2025"),
    ]
    text = format_candidates(candidates, recommended_ref="D1")
    assert "D1" in text and "*SIMDASI*" in text
    assert "462.125" not in text


def test_formatter_output_passes_the_gate_end_to_end():
    """The two halves must agree: what the formatter writes, the gate accepts."""
    evidence = _ev()
    text = format_single_value(evidence, "Jumlah Penduduk")
    verdict = evaluate(
        {"answer": text, "answer_type": "official_fact", "evidence_ids": ["ev1"], "citations": []},
        GateContext(evidence=[evidence], selection_source="candidate_set_ref", query_facts=True),
    )
    assert verdict.allowed, verdict.violations


def test_system_counts_let_the_agent_narrate_naturally():
    rows = [_ev(evidence_id="a", geography="Batang Anai"),
            _ev(evidence_id="b", geography="Lubuk Alung")]
    candidates = [Candidate("D1", "dynamic", "t", "2025"),
                  Candidate("D2", "dynamic", "t", "2025"),
                  Candidate("D3", "dynamic", "t", "2025")]
    counts = system_counts_for(candidates, rows)
    assert 3 in counts and 2 in counts

    verdict = evaluate(
        {"answer": "Ada 3 tabel yang cocok, dan datanya mencakup 2 kecamatan.",
         "answer_type": "service", "evidence_ids": [], "citations": []},
        GateContext(evidence=[], system_counts=counts, query_facts=False),
    )
    assert verdict.allowed, verdict.violations
