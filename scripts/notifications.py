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
    def send_to_officer_group(self, text: str) -> bool: ...
    def send_to_citizen(self, conversation_id: str, text: str) -> bool: ...


@dataclass
class InMemoryChannel:
    """Test/demo channel. Captures what WOULD have been sent."""

    officer_messages: list[str] = field(default_factory=list)
    citizen_messages: list[tuple[str, str]] = field(default_factory=list)

    def send_to_officer_group(self, text: str) -> bool:
        self.officer_messages.append(text)
        return True

    def send_to_citizen(self, conversation_id: str, text: str) -> bool:
        self.citizen_messages.append((conversation_id, text))
        return True


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
            notified = (
                channel.send_to_officer_group(
                    QUEUE_NOTICE_TEMPLATE.format(conversation_id=conversation.conversation_id)
                )
                is True
            )
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


# ---------------------------------------------------------------------------
# Channel produksi — mengirim sungguhan lewat outbox
# ---------------------------------------------------------------------------


@dataclass
class FanoutChannel:
    """Kirim notifikasi ke SETIAP nomor petugas yang aktif.

    Menggantikan satu JID grup. Alasan praktisnya: kantor kecil tidak selalu
    punya grup WA khusus, dan notifikasi ke nomor perorangan lebih sulit
    diabaikan daripada satu pesan lagi di grup yang sudah ramai.

    Satu percakapan yang mengantre menghasilkan satu baris outbox PER petugas.
    Kunci idempotensi memuat nomor tujuan, sehingga tiga petugas benar-benar
    menerima tiga pesan — bukan satu pesan yang menabrak dua duplikat.
    """

    store: object
    enabled: bool = True

    def _recipients(self) -> list[str]:
        getter = getattr(self.store, "admin_contacts", None)
        if getter is None:
            return []
        try:
            return [c["phone_e164"] for c in getter(only_notify=True)]
        except Exception:  # noqa: BLE001
            return []

    def send_to_officer_group(self, text: str) -> bool:
        if not self.enabled:
            return False
        recipients = self._recipients()
        if not recipients:
            _log_no_recipients()
            return False
        sent_any = False
        for phone in recipients:
            if _enqueue_via(self.store, f"{phone}@s.whatsapp.net", text, "system"):
                sent_any = True
        return sent_any

    def send_to_citizen(self, conversation_id: str, text: str) -> bool:
        return _enqueue_via(self.store, conversation_id, text, "bot")


_NO_RECIPIENT_LOGGED = False


def _log_no_recipients() -> None:
    global _NO_RECIPIENT_LOGGED
    if not _NO_RECIPIENT_LOGGED:
        import sys

        print(
            "[MARAWA] PERINGATAN: belum ada nomor petugas terdaftar. "
            "Notifikasi antrean tidak sampai ke siapa pun — petugas hanya akan "
            "tahu ada yang menunggu bila kebetulan membuka panel. "
            "Tambahkan di Dashboard > Setelan > Nomor petugas.",
            file=sys.stderr,
        )
        _NO_RECIPIENT_LOGGED = True


def _enqueue_via(store: object, conversation_id: str, body: str, sender_type: str) -> bool:
    from scripts.outbox_worker import SendRecord, idempotency_key

    minute_bucket = int(datetime.now().timestamp() // 60)
    key = idempotency_key(conversation_id, body, minute_bucket)

    # marawa_outbox.conversation_id has an FK to marawa_conversations. A
    # notification target (officer phone JID) is not a citizen conversation
    # yet, so create it and mark it hidden before enqueueing. Fake/test stores
    # may not expose these methods, hence guarded dispatch.
    get_conversation = getattr(store, "get_conversation", None)
    mark_staff = getattr(store, "mark_staff_channel", None)
    if sender_type == "system" and get_conversation is not None:
        get_conversation(conversation_id)
        if mark_staff is not None:
            mark_staff(conversation_id)

    record = SendRecord(
        outbox_id=f"ntf_{key[3:]}",
        conversation_id=conversation_id,
        body=body,
        sender_type=sender_type,
        state_version_at_enqueue=0,
        idempotency_key=key,
    )
    enqueue = getattr(store, "enqueue_outbox", None)
    if enqueue is None:
        return False
    return bool(enqueue(record))


@dataclass
class OutboxChannel:
    """Kirim notifikasi lewat outbox yang sama dengan pesan biasa.

    KENAPA LEWAT OUTBOX, BUKAN LANGSUNG KE BAILEYS
    ----------------------------------------------
    Alasannya sama persis dengan pesan warga: WhatsApp gagal dengan cara paling
    canggung — pengiriman berhasil di sisi mereka, responsnya tidak sampai ke
    kita. Notifikasi yang dikirim langsung dari handler akan hilang saat worker
    sedang putus, dan justru saat worker putus itulah petugas paling perlu tahu.

    Konsekuensi yang disengaja: notifikasi ikut antre bersama balasan warga,
    dan ikut mendapat retry, idempotency, serta pencatatan yang sama.

    KENAPA `conversation_id` GRUP PETUGAS DIPERLAKUKAN SEBAGAI PERCAKAPAN
    --------------------------------------------------------------------
    Supaya worker tidak perlu tahu apa pun tentang "notifikasi". Bagi worker itu
    hanya baris outbox lain dengan tujuan berbeda. Satu jalur pengiriman, satu
    tempat yang bisa salah.
    """

    store: object                      # Store atau PostgresStore
    officer_group_id: str | None       # JID grup WA petugas, mis. "1203...@g.us"
    enabled: bool = True

    def _enqueue(self, conversation_id: str, body: str, sender_type: str) -> bool:
        from scripts.outbox_worker import SendRecord, idempotency_key

        # Kunci idempotensi memuat menit, bukan detik: dua pemicu dalam satu
        # menit untuk percakapan yang sama adalah notifikasi ganda, bukan dua
        # kejadian berbeda. Debounce di should_notify_officers menangani jendela
        # yang lebih panjang; ini jaring pengaman terakhirnya.
        minute_bucket = int(datetime.now().timestamp() // 60)
        key = idempotency_key(conversation_id, body, minute_bucket)
        record = SendRecord(
            outbox_id=f"ntf_{key[3:]}",
            conversation_id=conversation_id,
            body=body,
            sender_type=sender_type,
            state_version_at_enqueue=0,
            idempotency_key=key,
        )
        enqueue = getattr(self.store, "enqueue_outbox", None)
        if enqueue is None:
            return False
        return bool(enqueue(record))

    def send_to_officer_group(self, text: str) -> bool:
        if not self.enabled or not self.officer_group_id:
            # Tidak dikonfigurasi bukan keadaan normal: panel yang tidak pernah
            # memberi tahu siapa pun adalah panel yang tidak dibuka. Dicatat
            # keras supaya ketahuan saat pemeriksaan, bukan ditelan diam-diam.
            _log_missing_officer_group()
            return False
        return self._enqueue(self.officer_group_id, text, "system")

    def send_to_citizen(self, conversation_id: str, text: str) -> bool:
        return self._enqueue(conversation_id, text, "bot")


_MISSING_LOGGED = False


def _log_missing_officer_group() -> None:
    global _MISSING_LOGGED
    if not _MISSING_LOGGED:
        import sys

        print(
            "[MARAWA] PERINGATAN: MARAWA_OFFICER_GROUP_JID belum di-set. "
            "Notifikasi antrean tidak akan sampai ke siapa pun, dan petugas "
            "hanya akan tahu ada yang menunggu bila kebetulan membuka panel.",
            file=sys.stderr,
        )
        _MISSING_LOGGED = True


def build_channel(store: object) -> NotificationChannel:
    """Pilih channel berdasarkan lingkungan.

    Produksi memakai outbox; pengembangan dan tes memakai penampung in-memory
    supaya tidak ada pesan nyata terkirim saat menjalankan tes.
    """
    import os

    if os.environ.get("MARAWA_ENV", "").lower() in ("production", "prod"):
        # Daftar nomor dikelola dari dashboard, bukan env — supaya petugas bisa
        # menambah/menghapus tanpa akses server dan tanpa restart.
        return FanoutChannel(store=store)
    return InMemoryChannel()
