"""Crawl orchestration helpers for BPS WebAPI resource families."""
from __future__ import annotations

import re
from typing import Any

from workers.ingestion.bps_webapi import ApiPayloadError


def _strict_rows(rows: Any, context: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ApiPayloadError(f"{context} rows must be a list")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ApiPayloadError(f"{context} row {index} is not an object")
        result.append(row)
    return result


def extract_interop_data(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2:
        raise ApiPayloadError("interoperability payload must contain metadata and data")
    service = data[1]
    if service is None:
        return {}, []
    if isinstance(service, list):
        return {}, _strict_rows(service, "interoperability")
    if isinstance(service, dict):
        status = service.get("status")
        condition = service.get("condition")
        if isinstance(status, int) and status >= 400:
            raise ApiPayloadError(f"interoperability service status is {status}")
        if isinstance(status, str) and status not in ("OK", "200", "success", ""):
            raise ApiPayloadError(f"interoperability service status is {status!r}")
        if condition is not None and str(condition) not in ("OK", "200", ""):
            raise ApiPayloadError(f"interoperability service condition is {condition!r}")
        if isinstance(service.get("data"), list):
            return service, _strict_rows(service["data"], "interoperability service")
    raise ApiPayloadError("invalid interoperability nested data shape")


def iter_available_years(table: dict[str, Any]) -> list[int]:
    years: set[int] = set()
    values = table.get("ketersediaan_tahun") or []
    if not isinstance(values, list):
        values = [values]
    for value in values:
        if value is None or value == "":
            continue
        try:
            years.add(int(float(str(value))))
        except ValueError:
            continue
    return sorted(years)


def publication_declared_bytes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(KB|MB|GB|B)\b", value.upper())
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    multiplier = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[match.group(2)]
    return int(number * multiplier)


def simdasi_detail_params(region_code: str, year: int, table_id: str) -> list[tuple[str, Any]]:
    """Return live service-25 parameter casing (docs show stale `Tahun`)."""
    return [("wilayah", region_code), ("tahun", year), ("id_tabel", table_id)]


def dynamic_period_chunks(rows: Iterable[dict[str, Any]], size: int = 2) -> list[list[str]]:
    """Return unique period IDs in live-API-safe chunks (maximum two `th` IDs)."""
    if size < 1 or size > 2:
        raise ValueError("BPS Dynamic Data permits one or two period IDs per request")
    period_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = row.get("th_id")
        if value is None:
            continue
        period_id = str(value)
        if period_id not in seen:
            seen.add(period_id)
            period_ids.append(period_id)
    return [period_ids[index:index + size] for index in range(0, len(period_ids), size)]


def select_census_target_areas(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select only the exact Padang Pariaman regency area, never province fallback."""
    return [row for row in rows if str(row.get("kode_mfd", "")) in {"1306", "1306000"}][:1]
