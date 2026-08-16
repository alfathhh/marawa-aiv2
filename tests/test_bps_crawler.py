from __future__ import annotations

from dataclasses import dataclass

import pytest

from workers.ingestion.bps_crawler import (
    extract_interop_data,
    iter_available_years,
    publication_declared_bytes,
)
from workers.ingestion.bps_webapi import ApiPayloadError


def test_extract_interop_data_handles_nested_service_envelope() -> None:
    payload = {
        "status": "OK",
        "data": [
            {"page": 1, "pages": 1},
            {"status": 200, "condition": "OK", "created": "2026-08-14", "data": [{"id": 1}]},
        ],
    }

    envelope, rows = extract_interop_data(payload)

    assert envelope["created"] == "2026-08-14"
    assert rows == [{"id": 1}]


def test_extract_interop_data_accepts_direct_list_payload() -> None:
    payload = {"status": "OK", "data": [{"page": 1}, [{"id": "sp2020"}]]}

    envelope, rows = extract_interop_data(payload)

    assert envelope == {}
    assert rows == [{"id": "sp2020"}]


def test_extract_interop_data_rejects_invalid_nested_shape() -> None:
    with pytest.raises(ApiPayloadError):
        extract_interop_data({"status": "OK", "data": [{"page": 1}, "broken"]})


def test_extract_interop_data_accepts_explicit_null_catalogue_as_empty() -> None:
    payload = {
        "status": "OK",
        "data-availability": "available",
        "data": [{"page": 1, "pages": 1}, None],
    }

    envelope, rows = extract_interop_data(payload)

    assert envelope == {}
    assert rows == []


def test_extract_interop_data_rejects_nested_error_status() -> None:
    with pytest.raises(ApiPayloadError, match="service"):
        extract_interop_data({
            "status": "OK",
            "data": [{"page": 1}, {"status": 500, "condition": "ERROR", "data": []}],
        })


def test_extract_interop_data_rejects_malformed_row() -> None:
    with pytest.raises(ApiPayloadError, match="not an object"):
        extract_interop_data({
            "status": "OK",
            "data": [{"page": 1}, [{"id": 1}, "broken"]],
        })


def test_iter_available_years_normalizes_and_sorts_unique_values() -> None:
    table = {"ketersediaan_tahun": [2024, "2023", 2024, "", None, "2022.0"]}

    assert iter_available_years(table) == [2022, 2023, 2024]


def test_publication_declared_bytes_parses_common_units() -> None:
    assert publication_declared_bytes("12.3 MB") == 12_897_484
    assert publication_declared_bytes("1 GB") == 1_073_741_824
    assert publication_declared_bytes("850 KB") == 870_400
    assert publication_declared_bytes(None) is None
    assert publication_declared_bytes("-") is None


def test_simdasi_detail_parameters_use_live_lowercase_tahun() -> None:
    from workers.ingestion.bps_crawler import simdasi_detail_params

    assert simdasi_detail_params("1306000", 2025, "opaque-table") == [
        ("wilayah", "1306000"),
        ("tahun", 2025),
        ("id_tabel", "opaque-table"),
    ]


def test_dynamic_period_chunks_respect_live_two_year_limit() -> None:
    from workers.ingestion.bps_crawler import dynamic_period_chunks

    rows = [
        {"th_id": 123, "th": "2023"},
        {"th_id": 122, "th": "2022"},
        {"th_id": 121, "th": "2021"},
        {"th_id": 120, "th": "2020"},
        {"th_id": 122, "th": "duplicate"},
    ]

    assert dynamic_period_chunks(rows) == [["123", "122"], ["121", "120"]]


def test_dynamic_period_chunks_reject_missing_period_ids() -> None:
    from workers.ingestion.bps_crawler import dynamic_period_chunks

    assert dynamic_period_chunks([{"th": "2025"}, {"th_id": None}]) == []


def test_select_census_target_areas_never_falls_back_to_province() -> None:
    from workers.ingestion.bps_crawler import select_census_target_areas

    areas = [
        {"id": 1, "kode_mfd": "13", "nama": "SUMATERA BARAT"},
        {"id": 2, "kode_mfd": "1306", "nama": "PADANG PARIAMAN"},
        {"id": 3, "kode_mfd": "1306010", "nama": "BATANG ANAI"},
    ]

    assert select_census_target_areas(areas) == [areas[1]]
    assert select_census_target_areas([areas[0], areas[2]]) == []
