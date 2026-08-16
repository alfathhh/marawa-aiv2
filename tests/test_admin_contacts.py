"""Nomor petugas: notifikasi fanout + blokir dari bot."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.phone import InvalidPhone, display_phone, normalize_phone, same_number, to_jid

DSN = os.environ.get("MARAWA_TEST_DSN")


# ── Normalisasi: satu bentuk kanonik, atau daftar blokir gagal senyap ──

@pytest.mark.parametrize("raw", [
    "08123456789", "628123456789", "+628123456789", "+62 812-3456-789",
    "62 812 3456 789", "628123456789@s.whatsapp.net", "628123456789:12@s.whatsapp.net",
])
def test_every_common_form_normalizes_to_one_value(raw):
    assert normalize_phone(raw) == "628123456789"


def test_two_forms_of_the_same_number_compare_equal():
    assert same_number("08123456789", "628123456789@s.whatsapp.net")
    assert not same_number("08123456789", "08123456780")


@pytest.mark.parametrize("bad", ["", "   ", "abc", "0", "62", None])
def test_nonsense_is_rejected_loudly_not_stored_silently(bad):
    """Nomor yang diterima diam-diam lalu tidak pernah cocok adalah cara
    daftar blokir berhenti bekerja tanpa gejala."""
    with pytest.raises(InvalidPhone):
        normalize_phone(bad)


def test_jid_and_display_forms():
    assert to_jid("08123456789") == "628123456789@s.whatsapp.net"
    assert display_phone("628123456789") == "62 812-3456-789"


def test_double_zero_international_prefix_is_reachable():
    assert normalize_phone("00628123456789") == "628123456789"


# ── Fanout: satu antrean, satu pesan PER petugas ──

class FakeStore:
    def __init__(self, contacts):
        self._contacts = contacts
        self.enqueued = []
        self.created = []
        self.marked = []

    def admin_contacts(self, only_notify=False):
        return [c for c in self._contacts if not only_notify or c["notify"]]

    def get_conversation(self, cid):
        self.created.append(cid)
        return object()

    def mark_staff_channel(self, cid):
        self.marked.append(cid)

    def enqueue_outbox(self, record):
        self.enqueued.append(record)
        return True


def _c(phone, notify=True):
    return {"phone_e164": phone, "label": "x", "notify": notify}


def test_each_officer_gets_their_own_message():
    from scripts.notifications import FanoutChannel
    store = FakeStore([_c("628111111111"), _c("628222222222"), _c("628333333333")])
    FanoutChannel(store=store).send_to_officer_group("1 chat menunggu")
    assert len(store.enqueued) == 3
    targets = {r.conversation_id for r in store.enqueued}
    assert targets == {
        "628111111111@s.whatsapp.net", "628222222222@s.whatsapp.net",
        "628333333333@s.whatsapp.net",
    }
    assert set(store.created) == targets
    assert set(store.marked) == targets


def test_idempotency_keys_differ_per_recipient():
    """Kalau kuncinya sama, database menolak dua dari tiga dan hanya satu
    petugas yang diberi tahu."""
    from scripts.notifications import FanoutChannel
    store = FakeStore([_c("628111111111"), _c("628222222222")])
    FanoutChannel(store=store).send_to_officer_group("menunggu")
    keys = {r.idempotency_key for r in store.enqueued}
    assert len(keys) == 2


def test_officer_with_notify_off_is_skipped():
    from scripts.notifications import FanoutChannel
    store = FakeStore([_c("628111111111"), _c("628222222222", notify=False)])
    FanoutChannel(store=store).send_to_officer_group("menunggu")
    assert len(store.enqueued) == 1


def test_empty_recipient_list_is_loud(capsys):
    import scripts.notifications as notif
    from scripts.notifications import FanoutChannel
    notif._NO_RECIPIENT_LOGGED = False
    FanoutChannel(store=FakeStore([])).send_to_officer_group("menunggu")
    assert "nomor petugas" in capsys.readouterr().err


# ── Blokir: nomor petugas tidak dilayani bot ──

class BlockStore:
    def __init__(self, blocked):
        self._blocked = blocked
        self.marked = []

    def blocked_phones(self):
        return self._blocked

    def mark_staff_channel(self, cid):
        self.marked.append(cid)


def test_staff_number_is_detected_in_any_format():
    from scripts.app import _is_staff_number
    store = BlockStore({"628123456789"})
    for form in ("628123456789", "08123456789", "628123456789@s.whatsapp.net"):
        assert _is_staff_number(store, form) is True, form
    assert _is_staff_number(store, "628999999999") is False


def test_unparseable_sender_is_not_treated_as_staff():
    from scripts.app import _is_staff_number
    assert _is_staff_number(BlockStore({"628123456789"}), "status@broadcast") is False


def test_store_without_blocklist_support_does_not_crash():
    from scripts.app import _is_staff_number
    class Bare: pass
    assert _is_staff_number(Bare(), "628123456789") is False


def test_staff_policy_lookup_failure_fails_closed():
    """Store yang mengiklankan blocked_phones tapi error saat dipanggil harus
    menjalar (webhook → 503), BUKAN dianggap 'bukan petugas'."""

    from scripts.app import app, get_store
    from scripts.notifications import InMemoryChannel

    class BrokenStore:
        def __init__(self):
            self.notification_channel = InMemoryChannel()
            self.pairing_cutoff_ts = None
            self.webhook_secret = None

        def blocked_phones(self):
            raise RuntimeError("DB hilang")

    app.dependency_overrides[get_store] = lambda: BrokenStore()
    try:
        client = TestClient(app)
        res = client.post("/webhook/whatsapp", json={
            "conversation_id": "628123456789@s.whatsapp.net",
            "wa_message_id": "wa_failclosed",
            "from_me": False,
            "body": "halo",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "admin_id": None,
        })
        assert res.status_code == 503, res.text
    finally:
        app.dependency_overrides.clear()

# ── End-to-end lewat HTTP ──

@pytest.mark.skipif(not DSN, reason="MARAWA_TEST_DSN not set")
def test_officer_reply_to_notification_gets_no_bot_answer():
    """Petugas membalas pemberitahuan ("oke saya cek") adalah refleks wajar.
    Tanpa penjaga, bot menjawabnya sebagai pertanyaan statistik."""
    from scripts.app import app, get_store
    from scripts.notifications import InMemoryChannel
    from scripts.postgres_store import PostgresStore

    store = PostgresStore(DSN, InMemoryChannel())
    phone = "62811" + uuid.uuid4().hex[:7].translate(str.maketrans("abcdef", "123456"))
    store.add_admin_contact(phone, "Petugas Uji", "seed-super-1")
    app.dependency_overrides[get_store] = lambda: store
    try:
        client = TestClient(app)
        res = client.post("/webhook/whatsapp", json={
            "conversation_id": f"{phone}@s.whatsapp.net",
            "wa_message_id": "wa_" + uuid.uuid4().hex[:8],
            "from_me": False, "body": "oke saya cek",
            "timestamp": datetime.now(timezone.utc).isoformat(), "admin_id": None,
        })
        assert res.status_code == 200
        assert res.json()["status"] == "ignored_staff_number"
        assert res.json()["run_agent"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.skipif(not DSN, reason="MARAWA_TEST_DSN not set")
def test_removed_contact_can_be_readded():
    from scripts.notifications import InMemoryChannel
    from scripts.postgres_store import PostgresStore

    store = PostgresStore(DSN, InMemoryChannel())
    phone = "62812" + str(int(uuid.uuid4().hex[:7], 16))[:7]
    assert store.add_admin_contact(phone, "Petugas Lama", "seed-super-1")
    contact = next(c for c in store.admin_contacts() if c["phone_e164"] == phone)
    assert store.remove_admin_contact(contact["contact_id"])
    assert store.add_admin_contact(phone, "Petugas Aktif Lagi", "seed-super-1")
    contact = next(c for c in store.admin_contacts() if c["phone_e164"] == phone)
    assert contact["label"] == "Petugas Aktif Lagi"


@pytest.mark.skipif(not DSN, reason="MARAWA_TEST_DSN not set")
def test_staff_thread_never_appears_in_the_inbox():
    """Kalau ikut tampil, ia menenggelamkan warga yang benar-benar menunggu."""
    from scripts.notifications import InMemoryChannel
    from scripts.postgres_store import PostgresStore

    store = PostgresStore(DSN, InMemoryChannel())
    cid = f"staff_{uuid.uuid4().hex[:10]}"
    store.get_conversation(cid)
    store.mark_staff_channel(cid)
    ids = {c.conversation_id for c in store.list_conversations(limit=500)}
    assert cid not in ids
    ids_sweep = {c.conversation_id for c in store.conversations_needing_sweep()}
    assert cid not in ids_sweep
