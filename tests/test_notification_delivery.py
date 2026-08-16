"""Notifikasi antrean harus benar-benar terkirim, bukan hanya dicatat.

Panel yang tidak pernah memberi tahu siapa pun adalah panel yang tidak dibuka.
"""
from __future__ import annotations

import os
import uuid

import pytest

from scripts.notifications import (
    InMemoryChannel, OutboxChannel, build_channel, dispatch_effects,
)
from scripts.conversation_state import ConversationState, State
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, accept=True):
        self.enqueued = []
        self.accept = accept

    def enqueue_outbox(self, record):
        self.enqueued.append(record)
        return self.accept


def test_officer_notice_goes_through_the_outbox_not_straight_to_baileys():
    """Alasan sama seperti pesan warga: worker bisa sedang putus — dan justru
    saat itulah petugas paling perlu diberi tahu."""
    store = FakeStore()
    channel = OutboxChannel(store=store, officer_group_id="1203@g.us")
    channel.send_to_officer_group("1 chat menunggu")
    assert len(store.enqueued) == 1
    record = store.enqueued[0]
    assert record.conversation_id == "1203@g.us"
    assert record.sender_type == "system"
    assert record.idempotency_key


def test_missing_group_id_is_loud_not_silent(capsys):
    """Tidak dikonfigurasi bukan keadaan normal; harus terlihat saat diperiksa."""
    import scripts.notifications as notif
    notif._MISSING_LOGGED = False
    channel = OutboxChannel(store=FakeStore(), officer_group_id=None)
    channel.send_to_officer_group("halo")
    assert "MARAWA_OFFICER_GROUP_JID" in capsys.readouterr().err


def test_duplicate_notice_within_a_minute_is_collapsed():
    """Jaring pengaman terakhir setelah debounce state machine."""
    store = FakeStore()
    channel = OutboxChannel(store=store, officer_group_id="g@g.us")
    channel.send_to_officer_group("1 chat menunggu")
    first_key = store.enqueued[0].idempotency_key
    channel.send_to_officer_group("1 chat menunggu")
    assert store.enqueued[1].idempotency_key == first_key, (
        "dua pemicu dalam satu menit untuk teks sama harus punya kunci sama "
        "sehingga database menolak yang kedua"
    )


def test_citizen_notice_is_sent_as_bot():
    store = FakeStore()
    channel = OutboxChannel(store=store, officer_group_id="g@g.us")
    channel.send_to_citizen("c1", "Petugas akan membalas.")
    assert store.enqueued[0].sender_type == "bot"
    assert store.enqueued[0].conversation_id == "c1"


def test_dispatch_reaches_the_real_channel_end_to_end():
    store = FakeStore()
    channel = OutboxChannel(store=store, officer_group_id="g@g.us")
    conv = ConversationState("c1", state=State.QUEUED, handover_requested_at=NOW)
    notified = dispatch_effects(["notify_officers"], conv, NOW, channel)
    assert notified is True
    assert store.enqueued and "c1" in store.enqueued[0].body


def test_build_channel_uses_memory_outside_production(monkeypatch):
    monkeypatch.delenv("MARAWA_ENV", raising=False)
    assert isinstance(build_channel(FakeStore()), InMemoryChannel)


def test_build_channel_fans_out_to_officer_numbers_in_production(monkeypatch):
    """Diperbarui 16 Agt: notifikasi tidak lagi ke satu JID grup melainkan
    ke setiap nomor petugas terdaftar, dan daftarnya dikelola dari dashboard
    (bukan env) supaya bisa diubah tanpa akses server."""
    from scripts.notifications import FanoutChannel
    monkeypatch.setenv("MARAWA_ENV", "production")
    channel = build_channel(FakeStore())
    assert isinstance(channel, FanoutChannel)


def test_store_without_outbox_support_does_not_crash():
    """Channel tidak boleh menjatuhkan permintaan warga hanya karena
    penyimpanan yang dipakai belum mendukung outbox."""
    class Bare:
        pass
    channel = OutboxChannel(store=Bare(), officer_group_id="g@g.us")
    channel.send_to_officer_group("halo")  # tidak boleh melempar
