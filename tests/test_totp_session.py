from __future__ import annotations

import pytest

from scripts.totp_session import (
    issue_session,
    new_totp_secret,
    otpauth_uri,
    totp_at,
    verify_session,
    verify_totp,
)

# RFC 6238 Appendix B test vectors (SHA1, 8-byte secret, digit=8 in the RFC;
# we use 6 digits, so we re-derive from the same secret for our config).
RFC_SECRET = "12345678901234567890"  # ASCII secret in RFC 6238
RFC_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
# Known RFC 6238 SHA1 8-digit values at specific timestamps:
# T=59 -> 94287082 ; T=1111111109 -> 07081804 ; T=1111111111 -> 14050471
# The underlying code truncation is step-independent; we assert our 6-digit
# code at the same counters maps to the RFC's truncated 31-bit value.
# Instead of replicating the table, verify against pyotp semantic via direct
# RFC vectors for 8 digits by overriding digits.


def _rfc_counter(t: int) -> int:
    return t // 30


def test_totp_matches_rfc6238_reference_values() -> None:
    # RFC 6238 App. B (SHA1): at time 59 code is "94287082" (8 digits).
    # Our implementation is the same HOTP truncation; masking to 6 digits is a
    # display choice. Assert the exact 8-digit value using the same algorithm.
    from scripts.totp_session import _b32decode_pad as dec
    import base64, hashlib, hmac, struct

    def hotp8(counter: int) -> str:
        msg = struct.pack(">Q", counter)
        digest = hmac.new(dec(RFC_SECRET_B32), msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**8)
        return str(code).zfill(8)

    assert hotp8(_rfc_counter(59)) == "94287082"
    assert hotp8(_rfc_counter(1111111109)) == "07081804"
    assert hotp8(_rfc_counter(1111111111)) == "14050471"
    # and our public 6-digit function returns the last 6 of the same 31-bit code
    assert totp_at(RFC_SECRET_B32, _rfc_counter(59)) == hotp8(_rfc_counter(59))[-6:]


def test_verify_totp_window_accepts_adjacent_steps() -> None:
    secret = new_totp_secret()
    t = 1_700_000_000
    assert verify_totp(secret, totp_at(secret, t // 30), now=t)
    assert verify_totp(secret, totp_at(secret, t // 30 - 1), now=t)  # skew -30s
    assert verify_totp(secret, totp_at(secret, t // 30 + 1), now=t)  # skew +30s
    assert not verify_totp(secret, "000000", now=t)
    assert not verify_totp(secret, "abcdef", now=t)


def test_new_secret_roundtrip_and_uri() -> None:
    secret = new_totp_secret()
    assert len(secret) == 32  # 20 bytes -> base32
    uri = otpauth_uri(secret, "MARAWA-BPS", "budi")
    assert f"secret={secret}" in uri
    assert "issuer=MARAWA-BPS" in uri


def test_session_roundtrip_and_expiry() -> None:
    token = issue_session("budi", now=1_700_000_000)
    assert verify_session(token, now=1_700_000_000) == "budi"
    # expired 13 jam kemudian
    assert verify_session(token, now=1_700_000_000 + 13 * 3600) is None


def test_session_tamper_rejected() -> None:
    token = issue_session("budi", now=1_700_000_000)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify_session(tampered, now=1_700_000_000) is None
    assert verify_session(None) is None
    assert verify_session("garbage") is None
