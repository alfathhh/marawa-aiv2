from __future__ import annotations

import pytest

from scripts.password_auth import hash_password, verify_password


def test_roundtrip_correct_password():
    stored = hash_password("rahasia")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("rahasia", stored) is True


def test_wrong_password_fails():
    stored = hash_password("rahasia")
    assert verify_password("salah", stored) is False


def test_malformed_stored_hash_fails_closed():
    assert verify_password("x", "") is False
    assert verify_password("x", "plain-text-not-a-hash") is False
    assert verify_password("x", "bcrypt$10$abcdef") is False
    assert verify_password("x", "pbkdf2_sha256$a$b$c$d") is False
    assert verify_password("x", "pbkdf2_sha256$abc$$") is False


def test_salting_produces_distinct_hashes():
    assert hash_password("sama") != hash_password("sama")
