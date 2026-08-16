#!/usr/bin/env python3
"""Context caching untuk panggilan model — hemat biaya input token.

APA YANG SEBENARNYA DIHEMAT
---------------------------
Setiap giliran percakapan mengirim ulang bagian yang tidak berubah: system
prompt, definisi tool, dan konteks katalog. Pada Gemini, input yang kena cache
dibayar sekitar 10% dari harga normal. Untuk `gemini-3.5-flash-lite`
($0.30 input / $2.50 output per 1 juta token), prefix stabil ~2.000 token yang
dikirim 5.000 kali sebulan:

    tanpa cache : 2.000 x 5.000 = 10 juta token x $0.30  = $3.00
    dengan cache: ~90% kena cache                        ≈ $0.57

Penghematannya sederhana dan nyata, tetapi TIDAK gratis secara desain: caching
hanya bekerja bila prefix-nya BYTE-IDENTIK antar panggilan. Satu timestamp,
satu nama warga, atau satu penanda sesi yang menyelip ke bagian awal prompt
membatalkan seluruh cache tanpa error apa pun — tagihan tetap normal dan tidak
ada yang memberi tahu.

Karena itu modul ini memisahkan prompt menjadi dua bagian secara eksplisit dan
MENOLAK prefix yang mengandung penanda volatil, alih-alih membiarkannya gagal
diam-diam.

DUA JENIS CACHING
-----------------
- Implisit: penyedia mendeteksi prefix berulang sendiri. Tidak perlu konfigurasi;
  cukup jaga prefix tetap stabil dan taruh di depan. Ini yang dipakai default.
- Eksplisit: prefix didaftarkan lebih dulu dan dirujuk lewat id. Jaminannya
  lebih kuat tetapi butuh dukungan API dan pengelolaan TTL.

Modul ini murni: menyusun dan memvalidasi, tidak memanggil jaringan.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

# Minimum token agar caching berarti. Di bawah ini, overhead pengelolaan cache
# lebih besar daripada penghematannya.
MIN_CACHEABLE_TOKENS = 1024
# Perkiraan kasar: 1 token ~ 4 karakter untuk teks Indonesia/Inggris campuran.
CHARS_PER_TOKEN = 4

# Pola yang membuat prefix berubah setiap panggilan. Menemukan salah satunya
# di prefix berarti cache TIDAK AKAN PERNAH kena.
VOLATILE_PATTERNS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"), "timestamp ISO"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "jam:menit:detik"),
    (re.compile(r"\bconversation_id\s*[:=]\s*\S+"), "id percakapan"),
    (re.compile(r"\b(?:62|08)\d{8,}\b"), "nomor telepon"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", re.I), "UUID"),
    (re.compile(r"\bsession[_-]?id\b", re.I), "penanda sesi"),
)


class CachePrefixError(ValueError):
    """Prefix tidak layak di-cache. Sengaja keras: gagal senyap di sini berarti
    tagihan naik diam-diam tanpa gejala."""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def find_volatile(text: str) -> list[str]:
    """Kembalikan daftar alasan kenapa teks ini tidak stabil antar panggilan."""
    return [label for pattern, label in VOLATILE_PATTERNS if pattern.search(text)]


def prefix_fingerprint(parts: list[str]) -> str:
    """Sidik jari byte-exact dari prefix.

    Dipakai untuk mendeteksi drift: kalau sidik jari berubah padahal tidak ada
    yang sengaja mengubah prompt, cache berhenti bekerja dan biaya naik tanpa
    ada error di mana pun.
    """
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
    return f"cp_{digest[:24]}"


@dataclass
class CachedPrompt:
    """Prompt yang sudah dipisah antara bagian stabil dan bagian per-giliran."""

    stable_parts: list[str]
    volatile_parts: list[str]
    fingerprint: str
    stable_tokens: int
    cacheable: bool
    reasons: list[str] = field(default_factory=list)

    def to_messages(self, user_message: str) -> list[dict[str, Any]]:
        """Susun messages dengan bagian stabil SELALU di depan.

        Urutan ini bukan kosmetik: caching bekerja pada PREFIX. Menaruh apa pun
        yang berubah sebelum bagian stabil membatalkan seluruh penghematan.
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(self.stable_parts)}
        ]
        if self.volatile_parts:
            messages.append({"role": "system", "content": "\n\n".join(self.volatile_parts)})
        messages.append({"role": "user", "content": user_message})
        return messages


def build_cached_prompt(
    stable_parts: list[str],
    volatile_parts: list[str] | None = None,
    *,
    strict: bool = True,
) -> CachedPrompt:
    """Susun prompt cache-aware.

    `stable_parts` harus identik pada setiap panggilan: system prompt, aturan
    keras, definisi tool, ringkasan katalog.
    `volatile_parts` boleh berubah: state percakapan, hasil tool, waktu.

    Dengan `strict=True` (default), penanda volatil di bagian stabil menaikkan
    `CachePrefixError`. Itu disengaja — kalau dibiarkan lewat, satu-satunya
    gejalanya adalah tagihan yang lebih besar dari perkiraan, dan tidak ada yang
    akan menghubungkannya dengan perubahan prompt berbulan-bulan sebelumnya.
    """
    volatile_parts = list(volatile_parts or [])
    joined = "\n\n".join(stable_parts)

    reasons: list[str] = []
    volatile_found = find_volatile(joined)
    if volatile_found:
        message = (
            "Bagian stabil mengandung nilai yang berubah tiap panggilan "
            f"({', '.join(volatile_found)}). Cache tidak akan pernah kena. "
            "Pindahkan ke volatile_parts."
        )
        if strict:
            raise CachePrefixError(message)
        reasons.append(message)

    stable_tokens = estimate_tokens(joined)
    if stable_tokens < MIN_CACHEABLE_TOKENS:
        reasons.append(
            f"prefix hanya ~{stable_tokens} token; di bawah {MIN_CACHEABLE_TOKENS} "
            "penghematannya tidak sepadan dengan overhead"
        )

    return CachedPrompt(
        stable_parts=list(stable_parts),
        volatile_parts=volatile_parts,
        fingerprint=prefix_fingerprint(stable_parts),
        stable_tokens=stable_tokens,
        cacheable=not reasons,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Pelacakan efektivitas — caching yang berhenti bekerja tidak menimbulkan error
# ---------------------------------------------------------------------------

@dataclass
class CacheStats:
    """Ringkasan hit/miss beserta estimasi biaya.

    Ada karena kegagalan caching itu SENYAP. Tidak ada exception, tidak ada
    respons yang salah — hanya tagihan yang perlahan naik. Satu-satunya cara
    mengetahuinya adalah mengukur.
    """

    calls: int = 0
    cached_input_tokens: int = 0
    uncached_input_tokens: int = 0
    output_tokens: int = 0
    fingerprints_seen: set[str] = field(default_factory=set)

    def record(
        self, *, fingerprint: str, cached_tokens: int,
        uncached_tokens: int, output_tokens: int,
    ) -> None:
        self.calls += 1
        self.cached_input_tokens += cached_tokens
        self.uncached_input_tokens += uncached_tokens
        self.output_tokens += output_tokens
        self.fingerprints_seen.add(fingerprint)

    @property
    def hit_rate(self) -> float:
        total = self.cached_input_tokens + self.uncached_input_tokens
        return 0.0 if total == 0 else self.cached_input_tokens / total

    def prefix_drifted(self) -> bool:
        """Lebih dari satu sidik jari berarti prefix berubah di tengah jalan —
        penyebab paling umum cache berhenti bekerja."""
        return len(self.fingerprints_seen) > 1

    def estimated_cost_usd(
        self, input_per_mtok: float = 0.30,
        output_per_mtok: float = 2.50,
        cached_discount: float = 0.10,
    ) -> dict[str, float]:
        """Perkiraan biaya. Default = tarif gemini-3.5-flash-lite (Agt 2026).

        Angka ini ESTIMASI dari hitungan token kita sendiri, bukan tagihan
        resmi. Pakai untuk mendeteksi perubahan tren, bukan untuk rekonsiliasi.
        """
        cached = self.cached_input_tokens / 1_000_000 * input_per_mtok * cached_discount
        uncached = self.uncached_input_tokens / 1_000_000 * input_per_mtok
        output = self.output_tokens / 1_000_000 * output_per_mtok
        without_cache = (
            (self.cached_input_tokens + self.uncached_input_tokens)
            / 1_000_000 * input_per_mtok
        ) + output
        return {
            "input_cached_usd": round(cached, 6),
            "input_uncached_usd": round(uncached, 6),
            "output_usd": round(output, 6),
            "total_usd": round(cached + uncached + output, 6),
            "without_cache_usd": round(without_cache, 6),
            "saved_usd": round(without_cache - (cached + uncached + output), 6),
        }

    def health(self) -> dict[str, Any]:
        warnings: list[str] = []
        if self.prefix_drifted():
            warnings.append(
                f"prefix berubah ({len(self.fingerprints_seen)} sidik jari berbeda) — "
                "cache kemungkinan besar berhenti bekerja"
            )
        if self.calls >= 20 and self.hit_rate < 0.30:
            warnings.append(
                f"hit rate hanya {self.hit_rate:.0%} setelah {self.calls} panggilan — "
                "periksa apakah ada nilai volatil menyelip ke prefix"
            )
        return {
            "calls": self.calls,
            "hit_rate": round(self.hit_rate, 4),
            "distinct_prefixes": len(self.fingerprints_seen),
            "warnings": warnings,
            "healthy": not warnings,
        }


def read_usage(response: dict[str, Any]) -> dict[str, int]:
    """Ambil angka token dari respons OpenAI-compatible.

    Nama field cached token berbeda antar penyedia dan antar versi; beberapa
    tidak melaporkannya sama sekali. Dicoba beberapa nama, dan 0 dianggap
    "tidak dilaporkan" — bukan "tidak ada cache".
    """
    usage = response.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = (
        details.get("cached_tokens")
        or usage.get("cached_content_token_count")
        or usage.get("cache_read_input_tokens")
        or 0
    )
    return {
        "cached_tokens": cached,
        "uncached_tokens": max(0, prompt_tokens - cached),
        "output_tokens": usage.get("completion_tokens", 0),
        "reported": bool(details or "cached_content_token_count" in usage),
    }
