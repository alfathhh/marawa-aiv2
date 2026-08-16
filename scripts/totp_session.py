#!/usr/bin/env python3
"""TOTP (RFC 6238) dan sesi admin — stdlib only, tanpa dependensi baru.

Kenapa stdlib: TOTP hanyalah HMAC-SHA1 + truncation + 30-detik step, dan
sesi hanya HMAC-signed token. Mengimpor pyotp/itsdangerous untuk 40 baris
algoritma menambah permukaan dependensi tanpa menambah jaminan.

Keamanan operasional:
- MARAWA_SESSION_KEY di-set di produksi; tanpa itu, kunci sesi dibangkitkan
  acak tiap boot (semua sesi invalid setelah restart — aman secara default).
- verifikasi TOTP memakai window ±1 step (clock skew sampai 30 detik).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time


def _b32decode_pad(secret_b32: str) -> bytes:
    padding = "=" * ((8 - len(secret_b32) % 8) % 8)
    return base64.b32decode(secret_b32.upper() + padding)


def totp_at(secret_b32: str, counter: int, digits: int = 6, algorithm: str = "sha1") -> str:
    """RFC 6238 HOTP/TOTP value."""
    key = _b32decode_pad(secret_b32)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, algorithm).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def verify_totp(secret_b32: str, code: str, *, window: int = 1, now: int | None = None) -> bool:
    """True bila `code` valid dalam ±window step dari waktu sekarang."""
    if not code or not code.isdigit():
        return False
    now = int(time.time()) if now is None else now
    counter = now // 30
    for shift in range(-window, window + 1):
        if secrets.compare_digest(totp_at(secret_b32, counter + shift), code):
            return True
    return False


def new_totp_secret() -> str:
    """Base32 secret 20-byte (160-bit, standar RFC 6238 contoh)."""
    return base64.b32encode(secrets.token_bytes(20)).decode()


def otpauth_uri(secret_b32: str, issuer: str, account: str) -> str:
    return (
        f"otpauth://totp/{issuer}:{account}?secret={secret_b32}&issuer={issuer}"
        "&algorithm=SHA1&digits=6&period=30"
    )


# -- sesi admin ---------------------------------------------------------------

_SESSION_KEY = os.environ.get("MARAWA_SESSION_KEY") or secrets.token_hex(32)
_SESSION_TTL_SECONDS = 12 * 3600  # 12 jam


def session_key_stable() -> bool:
    return "MARAWA_SESSION_KEY" in os.environ


def issue_session(admin_id: str, *, now: int | None = None) -> str:
    """Token sesi HMAC-SHA256: payload base64url(admin_id.exp) + signature."""
    now = int(time.time()) if now is None else now
    payload = base64.urlsafe_b64encode(f"{admin_id}.{now + _SESSION_TTL_SECONDS}".encode()).rstrip(b"=")
    sig = hmac.new(_SESSION_KEY.encode(), payload, hashlib.sha256).digest()
    return payload.decode() + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def verify_session(token: str | None, *, now: int | None = None) -> str | None:
    """Kembalikan admin_id bila token valid; None bila invalid/expired."""
    if not token:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = base64.urlsafe_b64decode(payload_b64.encode() + b"=" * (-len(payload_b64) % 4))
        expected = hmac.new(_SESSION_KEY.encode(), payload_b64.encode(), hashlib.sha256).digest()
        if not secrets.compare_digest(expected, base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))):
            return None
        admin_id, exp = payload.decode().rsplit(".", 1)
        now = int(time.time()) if now is None else now
        if int(exp) < now:
            return None
        return admin_id
    except Exception:
        return None
