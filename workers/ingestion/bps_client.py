"""Rate-limited, resumable-friendly HTTP client for BPS WebAPI."""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import quote

from workers.ingestion.bps_webapi import (
    ApiEmptyData,
    ApiPayloadError,
    canonical_request,
    parse_json_payload,
)

BASE_URL = "https://webapi.bps.go.id/v1/api"
Transport = Callable[[str, float], tuple[int, str, bytes]]


@dataclass(frozen=True)
class ApiResult:
    request: dict[str, Any]
    payload: dict[str, Any] | None
    bytes_received: int
    attempts: int


class BpsApiError(RuntimeError):
    def __init__(self, message: str, request: dict[str, Any]) -> None:
        super().__init__(message)
        self.request = request


def build_path_url(base: str, params: Iterable[tuple[str, Any]], api_key: str) -> str:
    parts: list[str] = [base.rstrip("/")]
    for key, value in params:
        if value is None or value == "":
            continue
        parts.extend((quote(str(key), safe=""), quote(str(value), safe="")))
    parts.extend(("key", quote(api_key, safe="")))
    return "/".join(parts) + "/"


def classify_retry(status: int, content_type: str, body: bytes) -> str:
    lower_type = (content_type or "").lower()
    prefix = body.lstrip()[:256].lower()
    if status == 429 or status >= 500:
        return "retry"
    if 200 <= status < 300 and (
        "text/html" in lower_type
        or prefix.startswith((b"<!doctype html", b"<html", b"<head"))
        or b"ltm waf block" in prefix
    ):
        return "retry"
    if 200 <= status < 300:
        return "accept"
    return "fatal"


def make_transport(proxy_url: str | None = None) -> Transport:
    handlers: list[Any] = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)

    def transport(url: str, timeout: float) -> tuple[int, str, bytes]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "User-Agent": "MARAWA-BPS-Ingestor/0.4 (+https://padangpariamankab.bps.go.id)",
            },
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.headers.get("Content-Type", ""), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type", ""), error.read()

    return transport


class BpsApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        proxy_url: str | None = None,
        timeout: float = 120,
        max_attempts: int = 6,
        min_delay: float = 1.25,
        max_delay: float = 3.0,
        backoff_base: float = 2.0,
        max_backoff: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.transport = transport or make_transport(proxy_url)
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff_base = backoff_base
        self.max_backoff = max_backoff
        self._last_request_at = 0.0

    def retry_delay(self, attempt: int, *, jitter: float | None = None) -> float:
        jitter_value = random.uniform(0, 1) if jitter is None else jitter
        return min(self.max_backoff, self.backoff_base ** (attempt - 1) + jitter_value)

    def _pace(self) -> None:
        target = random.uniform(self.min_delay, self.max_delay)
        wait = target - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    def get_path(self, endpoint: str, params: list[tuple[str, Any]]) -> ApiResult:
        base = f"{BASE_URL}/{endpoint.lstrip('/')}"
        safe_params = {key: value for key, value in params}
        request_meta = canonical_request(base, safe_params)
        url = build_path_url(base, params, self.api_key)
        last_problem = "unknown failure"
        null_attempts = 0
        null_body_len = 0
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            try:
                status, content_type, body = self.transport(url, self.timeout)
            except Exception as error:
                last_problem = f"transport error: {type(error).__name__}"
                action = "retry"
            else:
                self._last_request_at = time.monotonic()
                action = classify_retry(status, content_type, body)
                if action == "accept":
                    try:
                        payload = parse_json_payload(body)
                    except ApiEmptyData:
                        null_attempts += 1
                        null_body_len = len(body)
                        last_problem = "API returned JSON null (no data)"
                        action = "retry"
                    except ApiPayloadError as error:
                        last_problem = str(error)
                        action = "retry"
                    else:
                        return ApiResult(request_meta, payload, len(body), attempt)
                elif action == "retry":
                    last_problem = f"retryable HTTP {status} or HTML/WAF payload"
                else:
                    last_problem = f"fatal HTTP {status}"
            if action == "fatal" or attempt >= self.max_attempts:
                break
            time.sleep(self.retry_delay(attempt))
        if null_attempts == self.max_attempts:
            return ApiResult(request_meta, None, null_body_len, self.max_attempts)
        raise BpsApiError(f"BPS WebAPI request failed after {self.max_attempts} attempt(s): {last_problem}", request_meta)

    def get_interop(self, datasource: str, service_id: int, params: list[tuple[str, Any]]) -> ApiResult:
        endpoint = f"interoperabilitas/datasource/{datasource}/id/{service_id}"
        return self.get_path(endpoint, params)

    def get_paginated(
        self,
        model: str,
        *,
        domain: str | None = None,
        page: int = 1,
        perpage: int | None = None,
        extra: list[tuple[str, Any]] | None = None,
    ) -> ApiResult:
        params: list[tuple[str, Any]] = [("model", model)]
        if perpage is not None:
            params.append(("perpage", perpage))
        params.append(("lang", "ind"))
        if domain is not None:
            params.append(("domain", domain))
        params.append(("page", page))
        if extra:
            params.extend(extra)
        return self.get_path("list", params)

    def get_view(self, model: str, *, domain: str | None, item_id: str, lang: str = "ind") -> ApiResult:
        params: list[tuple[str, Any]] = [("model", model), ("lang", lang)]
        if domain is not None:
            params.append(("domain", domain))
        params.append(("id", item_id))
        return self.get_path("view", params)


def load_api_config(path: str) -> dict[str, str]:
    lines = secure_read_secret_file(path)
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def secure_read_secret_file(path: str) -> list[str]:
    """Read a secret file only when it is a regular, non-symlink, user-only file."""
    import os
    import stat

    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise OSError(f"secret file must not be a symlink: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"secret file must be a regular file: {path}")
    if info.st_uid != os.geteuid():
        raise OSError(f"secret file must be owned by the current user: {path}")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise OSError(f"secret file must not be group/world accessible: {path}")
    with open(path, encoding="utf-8") as handle:
        return handle.readlines()
