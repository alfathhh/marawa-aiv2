#!/usr/bin/env python3
"""PBKDF2-HMAC-SHA256 password hashing — stdlib only.

MENGGANTI TOTP sebagai jalur login utama dashboard (permintaan operator,
16-Agt-2026: "login pakai auth username password biasa aja"). TOTP tetap
didukung bila admin ter-enroll; password jadi syarat utama.

Format hash: `pbkdf2_sha256$<iterasi>$<salt_b64>$<hash_b64>` — self-describing,
iterasi bisa dinaikkan lintas waktu tanpa migrasi data.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_ITERATIONS = 210_000
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
