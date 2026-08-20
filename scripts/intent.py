"""Klasifier niat pesan WhatsApp — membedakan MAKSUD user, bukan cuma "goal data
vs bukan". Berlapis: aturan deterministik dulu (cepat, dapat diaudit), fallback
ke sinyal leksikal untuk basa-basi yang beragam.

Tingkatan niat (urutan prioritas — yang pertama cocok menang):
  HANDOVER    — minta petugas/admin manusia
  END         — mengakhiri obrolan ("sudah", "cukup", "makasih udah")
  CONSULT     — minta konsultasi/penjelasan lebih dalam
  RECOMMEND   — minta rekomendasi data/sumber
  DATA        — minta angka statistik (goal data)
  SERVICE     — minta layanan/permintaan non-data (surat, antrean, dst)
  GREETING    — sapaan murni
  SMALLTALK   — basa-basi (tidak menanyakan apa pun, bukan sapaan/thanks)
  THANKS      — terima kasih / penutup sopan
  UNCLEAR     — tidak jelas / terlalu pendek

Kenapa bukan satu regex raksasa: basa-basi sangat beragam ("lagi apa", "udah
makan?", "oh gitu", "owalah"). Regex keras akan selalu ketinggalan. Maka:
  - pola KUAT (greeting/thanks/handover/end) -> regex deterministik;
  - basa-basi -> "tidak ada sinyal niat lain DAN ada penanda obrolan ringan"
    ATAU "tidak ada sinyal sama sekali tapi bukan permintaan".

Invariant: ini KLASIFIKASI niat untuk ROUTING, bukan untuk menjawab data.
Angka tetap hanya dari RAG query bergate; klasifier tidak pernah menghasilkan
angka.
"""
from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    ACTION = "action"            # aksi lanjutan (lanjut/next/urutkan/bandingkan)
    HANDOVER = "handover"        # minta petugas/admin
    END = "end"                  # akhiri obrolan
    CONSULT = "consult"          # konsultasi lebih dalam
    RECOMMEND = "recommend"      # rekomendasi data/sumber
    DATA = "data"                # permintaan angka statistik
    SERVICE = "service"          # permintaan layanan non-data
    GREETING = "greeting"        # sapaan
    THANKS = "thanks"            # terima kasih
    SMALLTALK = "smalltalk"      # basa-basi
    UNCLEAR = "unclear"          # tidak jelas


# ---------------------------------------------------------------------------
# Pola deterministik (sinyal kuat — diperiksa dulu)
# ---------------------------------------------------------------------------

_HANDOVER_RE = re.compile(
    r"\b(admin|petugas|operator|cs|customer service|manusia|orang (asli|beneran)|"
    r"staf|staff|pegawai|konsultasi dengan|ngomong (sama|dengan)|"
    r"hubungkan|sambungkan|call|telepon|hubungi)\b",
    re.IGNORECASE,
)
_END_RE = re.compile(
    r"^\s*(sudah (ya|saja|kok)?|udah (ya|saja|kok|cukup)|cukup|selesai|stop|berhenti|"
    r"keluar|exit|quit|bye|dadah|selamat tinggal|sampai jumpa|ga ?usah|ga ?perlu|"
    r"gak ?usah|gak ?perlu|tidak (perlu|usah)|makasih (udah|saja|dulu))\b",
    re.IGNORECASE,
)
_CONSULT_RE = re.compile(
    r"\b(konsultasi|konsul|penjelasan (lebih|detail)|jelaskan (lebih|lebih dalam)|"
    r"lebih (detail|dalam|lengkap)|kurang paham|gimana cara|bagaimana cara|"
    r"maksudnya apa|arti(nya|)|maksud (dari|)|bisa dijelasin|tolong jelasin)\b",
    re.IGNORECASE,
)
_RECOMMEND_RE = re.compile(
    r"\b(rekomendasi|rekomendasiin|saran|saranin|suggest|sebaiknya (pakai|pakai apa)|"
    r"yang mana (yang|paling)|bagusan mana|apa (yang|datanya) (ada|tersedia)|"
    r"data apa (aja|saja)|sumber apa)\b",
    re.IGNORECASE,
)
_SERVICE_RE = re.compile(
    r"\b(layanan|pelayanan|permohonan|surat|antrean|antrian|daftar (online|daring)|"
    r"bikin|buat|urus|pengurusan|legalisir|legalisasi|suket|keterangan|"
    r"buka (jam|pukul)|jam (buka|layanan)|alamat|lokasi kantor)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^\s*(halo+w*|ha+llo+|hai+|hei+|hello|hi+|pagi|siang|sore|malam|"
    r"assalamu['’]?alaikum|selamat (pagi|siang|sore|malam)|permisi|hola|yo|"
    r"hei kamu|halo bot|halo min)\b",
    re.IGNORECASE,
)
# THANKS murni = terima kasih / penutup sopan eksplisit. Pujian umum ("keren",
# "mantap", "bagus") BUKAN thanks — itu smalltalk. (Konflik ditemukan di test:
# "wah keren" salah jadi thanks.)
_THANKS_RE = re.compile(
    r"\b(makasih|terima kasih|thanks|thank you|tq|thx|ty|noted|siap|"
    r"oke deh|ok sip)\b",
    re.IGNORECASE,
)
# Penanda basa-basi: kata gaul/obrolan ringan, BUKAN permintaan.
_SMALLTALK_MARKERS = re.compile(
    r"(lagi apa|lg apa|udah makan|dah makan|udah makan belum|lagi sibuk|capek|cape|btw|ngomong2|"
    r"omong-omong|eh iya|oh iya|owalah|oh gitu|gitu ya|ternyata|wah|waduh|hmm|hmmm|"
    r"hehe|haha|wkwk|lol|anjir|buset|gila|parah|serius|beneran|masa sih|iya kan|"
    r"tau ga|tau gak|tau nggak|kepo|penasaran|iseng|coba-coba|nyoba|ngetes)\b",
    re.IGNORECASE,
)

# Stopword untuk deteksi topik data (kata panjang yang bukan topik).
_NON_TOPIC = {
    "mau", "minta", "mintak", "data", "dong", "gw", "aku", "saya", "ingin",
    "butuh", "perlu", "tolong", "coba", "cari", "carikan", "berapa", "brapa",
    "jumlah", "total", "banyaknya", "nilai", "angka", "yang", "ada", "bisa",
    "boleh", "kasih", "lihat", "tampilkan", "tunjukkan", "informasi", "info",
    "nanya", "nanya2", "tanya", "ttg", "tentang", "soal", "perihal", "mengenai",
    "kapan", "dimana", "gimana", "bagaimana", "kenapa", "mengapa", "apakah",
}

# Kata tanya/penanda permintaan data statistik (dipakai untuk DATA).
_DATA_SIGNAL = re.compile(
    r"(berapa|brapa|berapaan|berapa banyak|jumlah|jumla|total|nilai|persen|"
    r"tingkat|statistik|bandingkan|dibanding|urutkan|peringkat|"
    r"tertinggi|terendah|paling|mana yang|padat|kepadatan|pertumbuhan|"
    r"mau (nanya|tanya|minta)|mau minta|pengen (nanya|tanya)|ingin (tanya|nanya)|"
    r"nanya|tanya dong|minta data|butuh data|"
    # sumber data eksplisit (publikasi/sensus/dokumen = permintaan data)
    r"publikasi|sensus|dokumen|tabel statistik|data statistik)",
    re.IGNORECASE,
)


def _has_specific_topic(text: str) -> bool:
    """Ada kata kunci spesifik (topik) — kata panjang yang bukan stopword."""
    words = [w for w in re.findall(r"[A-Za-z]{4,}", (text or "").lower()) if w not in _NON_TOPIC]
    return bool(words)


# Aksi lanjutan pada daftar/hasil (bukan niat percakapan) — didahulukan.
# ACTION = paging pada daftar ("lanjut", "next") — BUKAN compare/analyze yang
# butuh data (itu DATA). "bandingkan 2023 vs 2025" adalah permintaan data.
_ACTION_RE = re.compile(
    r"^\s*(lanjut|next|berikutnya|lanjut publikasi|selanjutnya|sebelumnya|kembali)\b",
    re.IGNORECASE,
)


def classify(text: str) -> Intent:
    """Klasifikasikan niat pesan. Deterministik, dapat diaudit, tanpa LLM.

    Urutan prioritas PENTING: yang lebih kuat/spesifik didahulukan.
    """
    t = (text or "").strip()
    if not t:
        return Intent.UNCLEAR

    # 0. ACTION — perintah pada daftar/hasil ("lanjut", "urutkan") BUKAN niat
    #    percakapan. Mengalahkan semua supaya tidak salah jadi smalltalk.
    if _ACTION_RE.search(t):
        return Intent.ACTION

    # 1. HANDOVER — minta manusia (paling kuat; mengalahkan semua)
    if _HANDOVER_RE.search(t):
        return Intent.HANDOVER

    # 2. END — akhiri obrolan
    if _END_RE.search(t):
        return Intent.END

    # 3. CONSULT — minta penjelasan/konsultasi
    if _CONSULT_RE.search(t):
        return Intent.CONSULT

    # 4. RECOMMEND — minta rekomendasi
    if _RECOMMEND_RE.search(t):
        return Intent.RECOMMEND

    # 5. SERVICE — permintaan layanan non-data DIDAHULUKAN dari DATA bila ada
    #    kata layanan eksplisit ("jam buka kantor berapa?" mengandung "berapa"
    #    tapi itu pertanyaan LAYANAN, bukan angka statistik).
    if _SERVICE_RE.search(t):
        return Intent.SERVICE

    # 6. DATA — permintaan angka statistik.
    #    Sinyal data, ATAU topik spesifik + modifier temporal ("produksi padi
    #    terbaru" tanpa kata tanya tetap permintaan data).
    if _DATA_SIGNAL.search(t):
        return Intent.DATA
    # Modifier temporal + topik = DATA, TAPI hanya bila topiknya bukan kata
    # obrolan/perasaan. "capek banget hari ini" -> smalltalk, bukan data.
    _CHATTY = {"capek", "cape", "banget", "senang", "sedih", "seneng", "betah",
               "bosan", "betul", "bener", "pusing", "lelah", "capeknya"}
    if _has_specific_topic(t) and re.search(
        r"\b(terbaru|terkini|sekarang|tahun ini|terkini)\b", t, re.IGNORECASE
    ):
        words = {w for w in re.findall(r"[A-Za-z]{4,}", t.lower()) if w not in _NON_TOPIC}
        if words and not (words & _CHATTY):
            return Intent.DATA

    # 7. GREETING — sapaan murni (pesan PENDEK yang hanya sapaan)
    if _GREETING_RE.match(t) and len(t) < 40:
        return Intent.GREETING

    # 8. THANKS — terima kasih
    if _THANKS_RE.search(t):
        return Intent.THANKS

    # 9. SMALLTALK — basa-basi: penanda obrolan ringan. "lagi apa?", "udah makan?"
    if _SMALLTALK_MARKERS.search(t):
        return Intent.SMALLTALK

    # 10. UNCLEAR — tidak jelas / terlalu pendek / tidak ada sinyal
    words = [w for w in re.findall(r"[A-Za-z]{3,}", t.lower())]
    if not words:
        return Intent.UNCLEAR

    # Default: obrolan bebas -> SMALLTALK (serahkan LLM dengan konteks bersih)
    return Intent.SMALLTALK


def describe(intent: Intent) -> str:
    """Label manusiawi untuk logging/UI."""
    return {
        Intent.HANDOVER: "minta petugas",
        Intent.END: "akhiri obrolan",
        Intent.CONSULT: "konsultasi",
        Intent.RECOMMEND: "rekomendasi",
        Intent.DATA: "permintaan data",
        Intent.SERVICE: "permintaan layanan",
        Intent.GREETING: "sapaan",
        Intent.THANKS: "terima kasih",
        Intent.SMALLTALK: "basa-basi",
        Intent.UNCLEAR: "tidak jelas",
    }[intent]
