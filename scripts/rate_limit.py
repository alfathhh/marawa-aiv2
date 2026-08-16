#!/usr/bin/env python3
"""Rate limiting untuk endpoint autentikasi.

KENAPA INI ADA (audit X)
------------------------
TOTP 6 digit dengan window ±1 step berarti ada 3 kode valid dari 1.000.000
kemungkinan pada satu saat. Tanpa pembatas percobaan, penyerang yang mengirim
~1.000 request/detik menemukan kode valid rata-rata setiap ~5 menit. Algoritma
TOTP-nya sendiri benar; yang hilang justru pembatasnya.

Dua kunci dibatasi sekaligus, dan keduanya perlu:
  - per admin_id  → melindungi satu akun dari serangan terdistribusi
  - per IP        → mencegah satu penyerang menyapu banyak akun

Penyimpanan in-memory. Untuk beberapa proses, ganti dengan Redis atau tabel
Postgres — antarmukanya sengaja kecil supaya penggantian itu murah. Catatan
jujur: dengan dua worker uvicorn, batas efektifnya menjadi dua kali lipat dari
yang tertulis.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    attempts: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class RateLimiter:
    """Sliding window + penguncian sementara setelah terlalu banyak gagal."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,
        lockout_seconds: int = 900,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._buckets: dict[str, _Bucket] = {}
        self._guard = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """(diizinkan, detik_tersisa_bila_terkunci). Tidak mencatat percobaan."""
        now = time.time() if now is None else now
        with self._guard:
            bucket = self._buckets.get(key)
            if bucket is None:
                return True, 0
            if bucket.locked_until > now:
                return False, int(bucket.locked_until - now) + 1
            return True, 0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._guard:
            bucket = self._buckets.setdefault(key, _Bucket())
            cutoff = now - self.window_seconds
            bucket.attempts = [t for t in bucket.attempts if t > cutoff]
            bucket.attempts.append(now)
            if len(bucket.attempts) >= self.max_attempts:
                bucket.locked_until = now + self.lockout_seconds
                bucket.attempts.clear()

    def record_success(self, key: str) -> None:
        """Login berhasil membersihkan riwayat — pengguna sah yang salah ketik
        beberapa kali tidak boleh terkunci setelah akhirnya berhasil."""
        with self._guard:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        with self._guard:
            self._buckets.clear()


LOGIN_LIMITER = RateLimiter()
