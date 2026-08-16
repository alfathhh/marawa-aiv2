#!/usr/bin/env python3
"""Normalisasi nomor WhatsApp — satu bentuk kanonik untuk seluruh sistem.

KENAPA INI PANTAS JADI MODUL SENDIRI
------------------------------------
Nomor yang sama datang dalam banyak bentuk: `081234…`, `+62 812-34…`,
`6281234…@s.whatsapp.net`, `62 812 3456 789`. Kalau normalisasinya tidak
seragam, daftar blokir petugas **gagal diam-diam**: nomor tersimpan sebagai
`08123`, WhatsApp mengirim `628123`, keduanya tidak cocok, tidak ada error —
dan bot mulai menjawab petugas seolah warga.

Kegagalan senyap seperti ini persis pola yang berulang di seluruh audit proyek
ini: bukan logika yang salah, melainkan dua sisi yang memodelkan hal sama
dengan bentuk berbeda. Karena itu satu fungsi, dipakai di semua tempat.

Aturan Indonesia:
    08xxxxxxxxx      -> 628xxxxxxxxx    (0 di depan diganti 62)
    +62xxxxxxxxx     -> 62xxxxxxxxx
    62xxxxxxxxx      -> tetap
    ...@s.whatsapp.net / @g.us / @lid  -> akhiran dibuang
"""
from __future__ import annotations

import re

JID_SUFFIXES = ("@s.whatsapp.net", "@c.us", "@g.us", "@lid", "@broadcast")
_DIGITS = re.compile(r"\D+")
# Nomor Indonesia yang masuk akal: 62 + 9..13 digit. Batas ini menolak salah
# ketik yang jelas, bukan validasi operator — WhatsApp yang memutuskan nomor
# itu ada atau tidak.
_PLAUSIBLE = re.compile(r"^[1-9][0-9]{7,17}$")


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str, *, default_country: str = "62") -> str:
    """Kembalikan bentuk kanonik: hanya digit, berawalan kode negara.

    Melempar `InvalidPhone` untuk masukan yang tidak masuk akal. Sengaja keras:
    nomor yang diterima diam-diam lalu tidak pernah cocok adalah cara paling
    umum daftar blokir berhenti bekerja tanpa gejala.
    """
    if raw is None:
        raise InvalidPhone("nomor kosong")

    value = str(raw).strip()
    for suffix in JID_SUFFIXES:
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)]
            break

    # Buang penanda perangkat Baileys, mis. "628123:12@s.whatsapp.net"
    value = value.split(":", 1)[0]
    digits = _DIGITS.sub("", value)

    if not digits:
        raise InvalidPhone(f"tidak ada angka pada {raw!r}")

    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = default_country + digits.lstrip("0")

    if not _PLAUSIBLE.match(digits):
        raise InvalidPhone(
            f"{raw!r} tidak terlihat seperti nomor yang sah (hasil: {digits!r})"
        )
    return digits


def same_number(a: str, b: str) -> bool:
    """Bandingkan dua nomor apa pun bentuknya. False bila salah satu tidak sah."""
    try:
        return normalize_phone(a) == normalize_phone(b)
    except InvalidPhone:
        return False


def to_jid(phone: str) -> str:
    """Bentuk yang dipakai Baileys untuk mengirim."""
    return f"{normalize_phone(phone)}@s.whatsapp.net"


def display_phone(phone: str) -> str:
    """Bentuk yang enak dibaca petugas di dashboard: 62 812-3456-7890."""
    try:
        digits = normalize_phone(phone)
    except InvalidPhone:
        return str(phone)
    if not digits.startswith("62") or len(digits) < 10:
        return digits
    body = digits[2:]
    return f"62 {body[:3]}-{body[3:7]}-{body[7:]}" if len(body) > 7 else f"62 {body}"
