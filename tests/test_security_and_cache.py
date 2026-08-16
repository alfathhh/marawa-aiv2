"""Tes perbaikan keamanan (V, W, X) dan context caching."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.prompt_cache import (
    CachePrefixError, CacheStats, build_cached_prompt, estimate_tokens,
    find_volatile, prefix_fingerprint, read_usage,
)
from scripts.rate_limit import RateLimiter

# --------------------------------------------------------------------------
# AUDIT V — verifikasi HMAC webhook
# --------------------------------------------------------------------------

def _client_with_secret(secret: str):
    from scripts.app import Store, app, get_store
    store = Store()
    store.webhook_secret = secret
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app), store


def _payload():
    return {
        "conversation_id": "c1", "wa_message_id": "wa_1", "from_me": False,
        "body": "halo", "timestamp": datetime.now(timezone.utc).isoformat(),
        "admin_id": None,
    }


def test_unsigned_webhook_is_rejected_when_secret_configured():
    from scripts.app import app
    http, _ = _client_with_secret("s3cret")
    try:
        assert http.post("/webhook/whatsapp", json=_payload()).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_worker_header_name_is_accepted():
    """AUDIT V: the worker signs `X-Marawa-Signature`; the handler used to read
    only `x-webhook-signature`, so a correctly signed request was still rejected
    — and with the secret unset (the real production state) nothing was checked
    at all."""
    import hashlib, hmac, json
    from scripts.app import app
    http, _ = _client_with_secret("s3cret")
    try:
        body = json.dumps(_payload()).encode()
        sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        response = http.post(
            "/webhook/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Marawa-Signature": sig},
        )
        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.clear()


def test_wrong_signature_is_rejected():
    import json
    from scripts.app import app
    http, _ = _client_with_secret("s3cret")
    try:
        body = json.dumps(_payload()).encode()
        response = http.post(
            "/webhook/whatsapp", content=body,
            headers={"Content-Type": "application/json", "X-Marawa-Signature": "0" * 64},
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# AUDIT W — produksi harus gagal-tertutup
# --------------------------------------------------------------------------

def test_production_mode_rejects_dev_header(monkeypatch):
    import importlib
    import scripts.app as app_module
    monkeypatch.setenv("MARAWA_ENV", "production")
    importlib.reload(app_module)
    assert app_module.is_production() is True
    assert app_module._dev_header_mode() is False
    monkeypatch.delenv("MARAWA_ENV")
    importlib.reload(app_module)


def test_production_config_check_names_missing_secrets(monkeypatch):
    import importlib
    import scripts.app as app_module
    monkeypatch.setenv("MARAWA_ENV", "production")
    monkeypatch.delenv("MARAWA_SESSION_KEY", raising=False)
    monkeypatch.delenv("MARAWA_WEBHOOK_SECRET", raising=False)
    importlib.reload(app_module)
    problems = app_module.assert_production_config()
    assert any("MARAWA_SESSION_KEY" in p for p in problems)
    assert any("MARAWA_WEBHOOK_SECRET" in p for p in problems)
    monkeypatch.delenv("MARAWA_ENV")
    importlib.reload(app_module)


# --------------------------------------------------------------------------
# AUDIT X — rate limit login
# --------------------------------------------------------------------------

def test_limiter_locks_after_repeated_failures():
    limiter = RateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=300)
    for _ in range(3):
        assert limiter.check("admin:budi")[0] is True
        limiter.record_failure("admin:budi")
    allowed, retry_after = limiter.check("admin:budi")
    assert allowed is False
    assert retry_after > 0


def test_successful_login_clears_history():
    """A real user who mistypes twice then succeeds must not be locked out."""
    limiter = RateLimiter(max_attempts=3)
    limiter.record_failure("admin:budi")
    limiter.record_failure("admin:budi")
    limiter.record_success("admin:budi")
    for _ in range(2):
        limiter.record_failure("admin:budi")
    assert limiter.check("admin:budi")[0] is True


def test_lockout_expires():
    limiter = RateLimiter(max_attempts=2, lockout_seconds=100)
    now = 1000.0
    limiter.record_failure("k", now=now)
    limiter.record_failure("k", now=now)
    assert limiter.check("k", now=now + 50)[0] is False
    assert limiter.check("k", now=now + 101)[0] is True


def test_one_account_lockout_does_not_block_another():
    limiter = RateLimiter(max_attempts=2)
    for _ in range(2):
        limiter.record_failure("admin:budi")
    assert limiter.check("admin:budi")[0] is False
    assert limiter.check("admin:sari")[0] is True


# --------------------------------------------------------------------------
# Context caching
# --------------------------------------------------------------------------

STABLE = ["Kamu MARAWA AI, asisten statistik BPS." + " Aturan keras." * 300]


def test_volatile_value_in_prefix_is_refused_loudly():
    """Silent failure here means the bill quietly grows and nothing errors."""
    with pytest.raises(CachePrefixError, match="tidak akan pernah kena"):
        build_cached_prompt(STABLE + ["Waktu sekarang 2026-08-16T10:30"])


@pytest.mark.parametrize("volatile,label", [
    ("2026-08-16T10:30", "timestamp"),
    ("conversation_id: c_abc123", "id percakapan"),
    ("6281234567890", "nomor telepon"),
    ("session_id aktif", "penanda sesi"),
])
def test_each_volatile_pattern_is_detected(volatile, label):
    assert find_volatile(volatile), f"{label} tidak terdeteksi"


def test_stable_prefix_is_accepted_and_fingerprinted():
    prompt = build_cached_prompt(STABLE)
    assert prompt.cacheable is True
    assert prompt.fingerprint.startswith("cp_")
    assert prompt.stable_tokens >= 1024


def test_identical_prefix_yields_identical_fingerprint():
    assert prefix_fingerprint(["a", "b"]) == prefix_fingerprint(["a", "b"])
    assert prefix_fingerprint(["a", "b"]) != prefix_fingerprint(["a", "b "])


def test_stable_part_always_comes_first_in_messages():
    """Caching works on the PREFIX. Anything volatile placed before the stable
    block destroys the whole saving."""
    prompt = build_cached_prompt(STABLE, ["State: QUEUED"])
    messages = prompt.to_messages("berapa penduduk 2025?")
    assert messages[0]["content"].startswith("Kamu MARAWA AI")
    assert "QUEUED" in messages[1]["content"]
    assert messages[-1]["role"] == "user"


def test_short_prefix_is_flagged_as_not_worth_caching():
    prompt = build_cached_prompt(["pendek saja"])
    assert prompt.cacheable is False
    assert any("token" in r for r in prompt.reasons)


def test_stats_detect_prefix_drift():
    """The most common way caching silently stops working."""
    stats = CacheStats()
    stats.record(fingerprint="cp_a", cached_tokens=1800, uncached_tokens=200, output_tokens=100)
    assert stats.health()["healthy"] is True
    stats.record(fingerprint="cp_b", cached_tokens=0, uncached_tokens=2000, output_tokens=100)
    assert stats.prefix_drifted() is True
    assert any("prefix berubah" in w for w in stats.health()["warnings"])


def test_low_hit_rate_is_warned_after_enough_calls():
    stats = CacheStats()
    for _ in range(25):
        stats.record(fingerprint="cp_a", cached_tokens=0, uncached_tokens=2000, output_tokens=50)
    assert stats.health()["healthy"] is False


def test_cost_estimate_shows_the_saving():
    stats = CacheStats()
    for _ in range(1000):
        stats.record(fingerprint="cp_a", cached_tokens=1800, uncached_tokens=200, output_tokens=150)
    cost = stats.estimated_cost_usd()
    assert cost["saved_usd"] > 0
    assert cost["total_usd"] < cost["without_cache_usd"]


def test_usage_parsing_handles_missing_cache_fields():
    """Not every provider reports cached tokens; 0 must mean "not reported",
    never "definitely no cache"."""
    parsed = read_usage({"usage": {"prompt_tokens": 2000, "completion_tokens": 100}})
    assert parsed["cached_tokens"] == 0
    assert parsed["uncached_tokens"] == 2000
    assert parsed["reported"] is False

    parsed = read_usage({"usage": {
        "prompt_tokens": 2000, "completion_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 1800},
    }})
    assert parsed["cached_tokens"] == 1800
    assert parsed["uncached_tokens"] == 200
    assert parsed["reported"] is True
