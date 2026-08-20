"""Contract tests untuk klasifier niat — terutama basa-basi yang beragam.

Setiap kasus = (pesan, intent yang diharapkan). Basa-basi sengaja banyak variasi
karena itu yang paling sering muncul di WA nyata.
"""
from __future__ import annotations

import pytest

from scripts.intent import Intent, classify


# (pesan, intent) — dikelompokkan per kategori
CASES: list[tuple[str, Intent]] = [
    # --- GREETING (sapaan murni, pendek) ---
    ("halo", Intent.GREETING),
    ("halooo", Intent.GREETING),
    ("hai", Intent.GREETING),
    ("pagi", Intent.GREETING),
    ("selamat malam", Intent.GREETING),
    ("assalamualaikum", Intent.GREETING),
    ("hei bot", Intent.GREETING),

    # --- THANKS ---
    ("makasih", Intent.THANKS),
    ("terima kasih banyak", Intent.THANKS),
    ("ok sip mantap", Intent.THANKS),
    ("oke deh", Intent.THANKS),

    # --- HANDOVER (minta petugas) ---
    ("mau chat admin", Intent.HANDOVER),
    ("hubungkan ke petugas dong", Intent.HANDOVER),
    ("bisa ngomong sama manusia?", Intent.HANDOVER),
    ("ada customer service?", Intent.HANDOVER),
    ("panggil operator", Intent.HANDOVER),

    # --- END (akhiri obrolan) ---
    ("udah cukup", Intent.END),
    ("stop", Intent.END),
    ("makasih udah", Intent.END),
    ("ga usah", Intent.END),
    ("selesai", Intent.END),

    # --- CONSULT (konsultasi/penjelasan) ---
    ("bisa konsul tentang data ini?", Intent.CONSULT),
    ("jelaskan lebih detail dong", Intent.CONSULT),
    ("gimana cara baca tabel ini?", Intent.CONSULT),
    ("maksudnya apa tuh?", Intent.CONSULT),
    ("kurang paham, tolong jelasin", Intent.CONSULT),

    # --- RECOMMEND (rekomendasi) ---
    ("rekomendasi data untuk pertanian", Intent.RECOMMEND),
    ("saran sumber untuk kemiskinan", Intent.RECOMMEND),
    ("data apa aja yang ada soal pendidikan?", Intent.RECOMMEND),
    ("bagusan mana sensus atau dinamis?", Intent.RECOMMEND),

    # --- DATA (permintaan angka) ---
    ("berapa jumlah penduduk?", Intent.DATA),
    ("berapa PDRB 2024?", Intent.DATA),
    ("produksi padi terbaru", Intent.DATA),
    ("jumlah sekolah per kecamatan", Intent.DATA),
    ("mau minta data penduduk", Intent.DATA),
    ("bandingkan 2023 vs 2025", Intent.DATA),

    # --- SERVICE (layanan non-data) ---
    ("jam buka kantor berapa?", Intent.SERVICE),
    ("mau bikin surat keterangan", Intent.SERVICE),
    ("cara daftar online gimana?", Intent.SERVICE),
    ("alamat kantor BPS dimana?", Intent.SERVICE),

    # --- SMALLTALK (basa-basi — PALING BERAGAM) ---
    ("lagi apa?", Intent.SMALLTALK),
    ("udah makan belum?", Intent.SMALLTALK),
    ("oh gitu ya", Intent.SMALLTALK),
    ("owalah", Intent.SMALLTALK),
    ("wah keren", Intent.SMALLTALK),
    ("hmm menarik", Intent.SMALLTALK),
    ("hehe iya", Intent.SMALLTALK),
    ("wkwk lucu", Intent.SMALLTALK),
    ("masa sih?", Intent.SMALLTALK),
    ("tau ga sih", Intent.SMALLTALK),
    ("kepo nih", Intent.SMALLTALK),
    ("lagi sibuk ga?", Intent.SMALLTALK),
    ("capek banget hari ini", Intent.SMALLTALK),
    ("eh iya ngomong-ngomong", Intent.SMALLTALK),
    ("ternyata gitu", Intent.SMALLTALK),
    ("beneran?", Intent.SMALLTALK),

    # --- UNCLEAR (tidak jelas) ---
    ("p", Intent.UNCLEAR),
    ("?", Intent.UNCLEAR),
    ("asdf", Intent.SMALLTALK),  # ada kata >=3 huruf -> obrolan bebas
]


@pytest.mark.parametrize("text,want", CASES)
def test_intent(text: str, want: Intent) -> None:
    got = classify(text)
    assert got == want, f"{text!r}: got {got}, want {want}"


def test_priority_handover_beats_data() -> None:
    # "berapa" (data) + "admin" (handover) -> handover menang
    assert classify("berapa data, tapi aku mau ngomong admin") == Intent.HANDOVER


def test_greeting_requires_short() -> None:
    # sapaan panjang yang berisi permintaan data -> bukan greeting
    assert classify("halo, berapa jumlah penduduk padang pariaman?") == Intent.DATA


def test_empty_and_symbols() -> None:
    assert classify("") == Intent.UNCLEAR
    assert classify("   ") == Intent.UNCLEAR
    assert classify("...") == Intent.UNCLEAR
