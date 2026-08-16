from __future__ import annotations

import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from workers.ingestion.bps_client import (
    BpsApiClient,
    BpsApiError,
    build_path_url,
    classify_retry,
)


def test_build_path_url_encodes_values_and_places_key_last() -> None:
    url = build_path_url(
        "https://webapi.bps.go.id/v1/api/list",
        [("model", "publication"), ("domain", "1306"), ("keyword", "dalam angka")],
        "secret/key",
    )

    assert url.endswith("/model/publication/domain/1306/keyword/dalam%20angka/key/secret%2Fkey/")


def test_classify_retry_handles_rate_limit_waf_and_server_errors() -> None:
    assert classify_retry(429, "application/json", b"{}") == "retry"
    assert classify_retry(503, "application/json", b"{}") == "retry"
    assert classify_retry(200, "text/html", b"<!doctype html><title>LTM WAF Block</title>") == "retry"
    assert classify_retry(404, "application/json", b"{}") == "fatal"
    assert classify_retry(200, "application/json", b'{"status":"OK"}') == "accept"


def test_client_never_exposes_key_in_exception_or_request_metadata() -> None:
    def transport(url: str, timeout: float) -> tuple[int, str, bytes]:
        headers = Message()
        headers["Content-Type"] = "application/json"
        raise HTTPError(url, 404, "Not Found", headers, None)

    client = BpsApiClient("very-secret-key", transport=transport, max_attempts=1, min_delay=0)

    with pytest.raises(BpsApiError) as error:
        client.get_path("list", [("model", "missing")])

    assert "very-secret-key" not in str(error.value)
    assert "very-secret-key" not in json.dumps(error.value.request)
    assert error.value.request["params"]["model"] == "missing"


def test_client_retries_html_waf_then_returns_json() -> None:
    calls = 0

    def transport(url: str, timeout: float) -> tuple[int, str, bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 200, "text/html", b"<!doctype html><title>LTM WAF Block</title>"
        return 200, "application/json", b'{"status":"OK","data":[{"page":1},[]]}'

    client = BpsApiClient(
        "secret",
        transport=transport,
        max_attempts=2,
        min_delay=0,
        backoff_base=0,
    )

    result = client.get_path("list", [("model", "var"), ("domain", "1306")])

    assert calls == 2
    assert result.payload["status"] == "OK"
    assert result.request["params"] == {"domain": "1306", "model": "var"}
    assert "secret" not in json.dumps(result.request)


def test_retry_backoff_is_capped() -> None:
    client = BpsApiClient("secret", min_delay=0, backoff_base=2, max_backoff=5)

    assert client.retry_delay(1, jitter=0) == 1
    assert client.retry_delay(4, jitter=0) == 5
    assert client.retry_delay(20, jitter=0) == 5


def test_proxy_secret_is_not_in_request_metadata() -> None:
    def transport(url: str, timeout: float) -> tuple[int, str, bytes]:
        return 200, "application/json", b'{"status":"OK","data":[{"page":1},[]]}'

    proxy = "http://proxy-user:proxy-password@proxy.example:1234"
    client = BpsApiClient("api-secret", proxy_url=proxy, transport=transport, min_delay=0)

    result = client.get_path("list", [("model", "var")])

    serialized = json.dumps(result.request)
    assert "proxy-user" not in serialized
    assert "proxy-password" not in serialized
    assert "proxy.example" not in serialized


def test_client_returns_none_payload_for_persistent_null_after_retries() -> None:
    calls: list[str] = []

    def transport(url: str, timeout: float) -> tuple[int, str, bytes]:
        calls.append(url)
        return 200, "application/json", b"null"

    client = BpsApiClient(
        "api-secret", transport=transport, min_delay=0, max_delay=0,
        max_attempts=3, backoff_base=1, max_backoff=0,
    )

    result = client.get_path("list", [("model", "data"), ("var", "151"), ("th", "121;120")])

    assert result.payload is None
    assert result.attempts == 3
    assert len(calls) == 3
