"""Pure parsing and normalization primitives for BPS WebAPI ingestion."""
from __future__ import annotations

import hashlib
import html
import itertools
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class ApiPayloadError(ValueError):
    """Raised when the WebAPI response is not a usable API payload."""


class ApiEmptyData(ApiPayloadError):
    """Raised when the API returns valid JSON ``null`` (resource has no data)."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def canonical_request(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return a key-free request identity safe to persist and log."""
    split = urlsplit(url)
    clean_url = urlunsplit((split.scheme, split.netloc, split.path.rstrip("/"), "", ""))
    safe_params = {
        str(key): str(value)
        for key, value in sorted(params.items())
        if key.lower() not in {"key", "api_key", "token", "authorization"}
        and value is not None
    }
    identity = {"url": clean_url, "params": safe_params}
    digest = hashlib.sha256(_canonical_json(identity)).hexdigest()
    return {**identity, "request_fingerprint": f"sha256:{digest}"}


def require_text(row: dict[str, Any], key: str) -> str:
    """Return a non-empty external identifier or raise a typed schema error."""
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiPayloadError(f"missing required identifier {key!r} in row")
    return str(value)


def response_sha256(body: bytes) -> str:
    """Hash canonical JSON when possible, exact bytes otherwise."""
    try:
        canonical = _canonical_json(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        canonical = body
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def parse_json_payload(body: bytes) -> dict[str, Any]:
    prefix = body.lstrip()[:64].lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<head")):
        raise ApiPayloadError("WebAPI returned HTML/WAF instead of JSON")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApiPayloadError(f"invalid JSON payload: {error}") from error
    if payload is None:
        raise ApiEmptyData("WebAPI returned JSON null (no data for this resource)")
    if not isinstance(payload, dict):
        raise ApiPayloadError("top-level WebAPI payload must be an object")
    if payload.get("status") != "OK":
        detail = payload.get("message2") or payload.get("message") or payload.get("data-availability")
        raise ApiPayloadError(f"WebAPI status is not OK: {detail}")
    return payload


def extract_paginated(body: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = parse_json_payload(body)
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], dict):
        raise ApiPayloadError("paginated payload must contain [metadata, rows]")
    rows = data[1]
    if not isinstance(rows, list):
        raise ApiPayloadError("paginated rows must be a list")
    return data[0], rows


def _dimension(values: Any) -> list[tuple[str | None, str | None]]:
    if not isinstance(values, list) or not values:
        return [(None, None)]
    result: list[tuple[str | None, str | None]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        value = item.get("val")
        result.append((None if value is None else str(value), item.get("label")))
    return result or [(None, None)]


def as_numeric(value: Any, *, decimal_places: int | None = None) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip().replace("\u00a0", "").replace(" ", "")
        if not text or text in {"-", "–", "…", "...", "NA", "N/A"}:
            return None
        try:
            if "," in text and "." in text:
                normalized = text.replace(".", "").replace(",", ".")
            elif decimal_places == 0 and re.fullmatch(r"[-+]?\d{1,3}(?:\.\d{3})+", text):
                normalized = text.replace(".", "")
            else:
                normalized = text.replace(",", ".")
            number = float(normalized)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def normalize_dynamic_dimensions(
    domain: str, variable_id: str, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension_type in ("vervar", "turvar", "tahun", "turtahun"):
        values = payload.get(dimension_type) or []
        if not isinstance(values, list):
            raise ApiPayloadError(f"dynamic {dimension_type} must be a list")
        for raw in values:
            if not isinstance(raw, dict) or raw.get("val") is None:
                continue
            group_id = raw.get("group")
            rows.append({
                "domain": str(domain), "variable_id": str(variable_id),
                "dimension_type": dimension_type, "item_id": str(raw.get("val")),
                "item_label": raw.get("label"),
                "group_id": "" if group_id is None else str(group_id),
                "group_label": raw.get("group_label"), "raw": raw,
            })
    return rows


def decode_dynamic_facts(
    domain: str, payload: dict[str, Any], expected_var_id: str | None = None
) -> list[dict[str, Any]]:
    """Decode concatenated BPS dynamic-table keys using exact dimension products.

    Unmatched keys are retained as raw-value facts rather than silently discarded.
    When ``expected_var_id`` is provided, a response for a different variable is
    rejected as a schema error rather than mislabeled.
    """
    var_item = (payload.get("var") or [{}])[0]
    response_var_id = str(var_item.get("val")) if var_item.get("val") is not None else None
    if (
        expected_var_id is not None
        and response_var_id is not None
        and response_var_id != str(expected_var_id)
    ):
        raise ApiPayloadError(
            f"dynamic response var {response_var_id} != requested {expected_var_id}"
        )
    var_id = response_var_id
    var_label = var_item.get("label")
    unit = var_item.get("unit")
    dimensions = [
        _dimension(payload.get("vervar")),
        _dimension(payload.get("turvar")),
        _dimension(payload.get("tahun")),
        _dimension(payload.get("turtahun")),
    ]
    lookup: dict[str, tuple[tuple[str | None, str | None], ...]] = {}
    if var_id is not None:
        for combo in itertools.product(*dimensions):
            vervar, turvar, period, derived_period = combo
            key = "".join(
                value or ""
                for value in (vervar[0], var_id, turvar[0], period[0], derived_period[0])
            )
            lookup.setdefault(key, combo)

    facts: list[dict[str, Any]] = []
    datacontent = payload.get("datacontent") or {}
    if not isinstance(datacontent, dict):
        raise ApiPayloadError("dynamic datacontent must be an object")
    for content_key, value in datacontent.items():
        combo = lookup.get(str(content_key))
        if combo is None:
            vervar = turvar = period = derived_period = (None, None)
        else:
            vervar, turvar, period, derived_period = combo
        numeric = as_numeric(value)
        facts.append(
            {
                "domain": str(domain),
                "var_id": var_id,
                "var_label": var_label,
                "unit": unit,
                "vervar_id": vervar[0],
                "vervar_label": vervar[1],
                "turvar_id": turvar[0],
                "turvar_label": turvar[1],
                "period_id": period[0],
                "period_label": period[1],
                "derived_period_id": derived_period[0],
                "derived_period_label": derived_period[1],
                "content_key": str(content_key),
                "value_numeric": numeric,
                "value_text": None if numeric is not None else (None if value is None else str(value)),
            }
        )
    return facts


def normalize_census_rows(event_id: str, dataset_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise ApiPayloadError("census nested data must be a list")
    result: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        categories: list[dict[str, Any]] = []
        for number in range(1, 6):
            category_id = raw.get(f"id_kategori_{number}")
            item_id = raw.get(f"id_item_kategori_{number}")
            if not category_id and not item_id:
                continue
            item_name = raw.get(f"nama_item_kategori_{number}")
            if item_name is None:
                item_name = raw.get(f"nama_item__kategori_{number}")
            categories.append(
                {
                    "category_id": category_id,
                    "category_name": raw.get(f"nama_kategori_{number}") or raw.get(f"name_kategori_{number}"),
                    "item_id": item_id,
                    "item_code": raw.get(f"kode_item_kategori_{number}"),
                    "item_name": item_name,
                }
            )
        value = raw.get("nilai")
        numeric = as_numeric(value)
        result.append(
            {
                "event_id": str(event_id),
                "dataset_id": str(dataset_id),
                "source_timestamp": payload.get("timestamp"),
                "geography_id": raw.get("id_wilayah"),
                "geography_code": None if raw.get("kode_wilayah") is None else str(raw.get("kode_wilayah")),
                "geography_name": raw.get("nama_wilayah"),
                "geography_level": raw.get("level_wilayah"),
                "indicator_id": raw.get("id_indikator"),
                "indicator_name": raw.get("nama_indikator"),
                "period": None if raw.get("period") is None else str(raw.get("period")),
                "value_numeric": numeric,
                "value_text": None if numeric is not None else (None if value is None else str(value)),
                "categories": categories,
                "raw": raw,
            }
        )
    return result


def census_fact_identity(row: dict[str, Any]) -> str:
    """Stable identity for one census observation, excluding revisable values."""
    categories = [
        {
            "category_id": category.get("category_id"),
            "item_id": category.get("item_id"),
            "item_code": category.get("item_code"),
        }
        for category in row.get("categories", [])
        if isinstance(category, dict)
    ]
    identity = {
        "event_id": row.get("event_id"),
        "dataset_id": row.get("dataset_id"),
        "geography_id": row.get("geography_id"),
        "geography_code": row.get("geography_code"),
        "indicator_id": row.get("indicator_id"),
        "period": row.get("period"),
        "categories": categories,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(identity)).hexdigest()


def normalize_simdasi_detail(
    region_code: str,
    table_id: str,
    year: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    nested = payload.get("data")
    title = payload.get("judul_tabel") or payload.get("judul") or payload.get("title")
    unit = payload.get("satuan") or payload.get("unit")
    if title is None and isinstance(nested, dict):
        title = nested.get("judul_tabel") or nested.get("judul") or nested.get("title")
    if unit is None and isinstance(nested, dict):
        unit = nested.get("satuan") or nested.get("unit")
    return {
        "region_code": str(region_code),
        "table_id": str(table_id),
        "year": int(year),
        "title": title,
        "unit": unit,
        "source_created_at": payload.get("created"),
        "raw": payload,
    }


def _plain_unit(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"<sup>2</sup>", "²", text, flags=re.I)
    text = re.sub(r"<sup>3</sup>", "³", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip() or None


def normalize_simdasi_facts(
    region_code: str, table_id: str, year: int, payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    column_map = payload.get("kolom") or {}
    data_rows = payload.get("data") or []
    legend = payload.get("keterangan_data") or {}
    if not isinstance(column_map, dict) or not isinstance(data_rows, list):
        raise ApiPayloadError("SIMDASI columns/data have invalid shape")
    columns: list[dict[str, Any]] = []
    for position, (column_id, raw) in enumerate(column_map.items(), 1):
        if not isinstance(raw, dict):
            continue
        columns.append({
            "region_code": str(region_code), "table_id": str(table_id), "year": int(year),
            "column_id": str(column_id), "position": position,
            "name": raw.get("nama_variabel"), "name_en": raw.get("nama_variabel_en"),
            "data_type": raw.get("tipe"), "decimal_places": raw.get("angka_desimal_dibelakang_koma"),
            "unit": _plain_unit(raw.get("satuan")), "unit_en": _plain_unit(raw.get("satuan_en")),
            "raw": raw,
        })
    facts: list[dict[str, Any]] = []
    for row_position, row in enumerate(data_rows, 1):
        if not isinstance(row, dict):
            continue
        variables = row.get("variables") or {}
        if not isinstance(variables, dict):
            continue
        for column_id, value in variables.items():
            if not isinstance(value, dict):
                value = {"value": value}
            display = value.get("value")
            raw_value = value.get("value_raw")
            code = value.get("value_code")
            column_meta = column_map.get(column_id) if isinstance(column_map.get(column_id), dict) else {}
            decimal_places = column_meta.get("angka_desimal_dibelakang_koma")
            numeric = as_numeric(
                raw_value if raw_value is not None else display,
                decimal_places=decimal_places if isinstance(decimal_places, int) else None,
            )
            facts.append({
                "region_code": str(region_code), "table_id": str(table_id), "year": int(year),
                "row_position": row_position,
                "geography_code": None if row.get("kode_wilayah") is None else str(row.get("kode_wilayah")),
                "row_label": row.get("label"), "row_label_raw": row.get("label_raw"),
                "row_unit": _plain_unit(row.get("satuan")), "column_id": str(column_id),
                "value_numeric": numeric,
                "value_text": None if numeric is not None else (None if display is None else str(display)),
                "value_raw": None if raw_value is None else str(raw_value),
                "value_code": None if code is None else str(code),
                "value_note": legend.get(str(code)) if code is not None and isinstance(legend, dict) else None,
                "raw": value,
            })
    return columns, facts


def merge_publication_rows(list_row: dict[str, Any] | None, detail_row: dict[str, Any]) -> dict[str, Any]:
    """Merge list and detail representations; non-null detail fields win."""
    merged = {**(list_row or {})}
    for key, value in detail_row.items():
        if value is not None:
            merged[key] = value
    return merged


def normalize_publication(domain: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": str(domain),
        "publication_id": str(raw.get("pub_id")),
        "title": raw.get("title"),
        "issn": raw.get("issn"),
        "catalog_number": raw.get("kat_no"),
        "publication_number": raw.get("pub_no"),
        "abstract": raw.get("abstract"),
        "scheduled_date": raw.get("sch_date"),
        "release_date": raw.get("rl_date"),
        "updated_date": raw.get("updt_date"),
        "cover_url": raw.get("cover"),
        "pdf_url": raw.get("pdf"),
        "declared_size": raw.get("size"),
        "raw": raw,
    }


def normalize_glossary_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = hit.get("_source") if isinstance(hit.get("_source"), dict) else hit
    return {
        "glossary_id": str(source.get("id")),
        "external_id": hit.get("_id"),
        "concept": source.get("konsep"),
        "concept_en": source.get("konsep_en"),
        "definition": source.get("definisi"),
        "definition_en": source.get("definisi_en"),
        "indicator_title": source.get("judulIndikator"),
        "classification": source.get("namaKlasifikasi") or source.get("klasifikasi"),
        "measure": source.get("ukuran"),
        "unit": source.get("satuan"),
        "content_source": source.get("sumberKonten"),
        "data_source": source.get("sumberData"),
        "endpoint": source.get("endpoint"),
        "raw": hit,
    }
