from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workers.ingestion.bps_webapi import (
    ApiPayloadError,
    canonical_request,
    decode_dynamic_facts,
    extract_paginated,
    normalize_census_rows,
    normalize_glossary_hit,
    normalize_publication,
    normalize_simdasi_detail,
    normalize_simdasi_facts,
    parse_json_payload,
    response_sha256,
)


def test_canonical_request_never_persists_api_key() -> None:
    request = canonical_request(
        "https://webapi.bps.go.id/v1/api/list",
        {"model": "var", "domain": "1306", "key": "super-secret", "page": 1},
    )

    serialized = json.dumps(request, sort_keys=True)
    assert "super-secret" not in serialized
    assert request["params"] == {"domain": "1306", "model": "var", "page": "1"}
    assert request["request_fingerprint"].startswith("sha256:")


def test_extract_paginated_rejects_html_waf_even_when_http_was_200() -> None:
    with pytest.raises(ApiPayloadError, match="HTML/WAF"):
        extract_paginated(b"<!doctype html><title>LTM WAF Block</title>")


def test_extract_paginated_returns_meta_and_rows() -> None:
    payload = {
        "status": "OK",
        "data-availability": "available",
        "data": [
            {"page": 1, "pages": 2, "per_page": 10, "count": 1, "total": 11},
            [{"var_id": 122, "title": "Jumlah Penduduk"}],
        ],
    }

    meta, rows = extract_paginated(json.dumps(payload).encode())

    assert meta["pages"] == 2
    assert rows == [{"var_id": 122, "title": "Jumlah Penduduk"}]


def test_response_hash_is_stable_for_equivalent_json() -> None:
    left = response_sha256(b'{"b":2,"a":1}')
    right = response_sha256(b'{ "a": 1, "b": 2 }')

    assert left == right
    assert left == "sha256:" + hashlib.sha256(b'{"a":1,"b":2}').hexdigest()


def test_decode_dynamic_facts_uses_exact_dimension_combinations() -> None:
    payload = {
        "var": [{"val": 145, "label": "Persentase Rumah Tangga", "unit": "Persen"}],
        "turvar": [{"val": 289, "label": "Listrik PLN"}],
        "vervar": [{"val": 1306, "label": "Kabupaten Padang Pariaman"}],
        "tahun": [{"val": 100, "label": "2020"}],
        "turtahun": [{"val": 0, "label": "Tahun"}],
        "datacontent": {"13061452891000": 83.68},
    }

    facts = decode_dynamic_facts("1306", payload)

    assert facts == [
        {
            "domain": "1306",
            "var_id": "145",
            "var_label": "Persentase Rumah Tangga",
            "unit": "Persen",
            "vervar_id": "1306",
            "vervar_label": "Kabupaten Padang Pariaman",
            "turvar_id": "289",
            "turvar_label": "Listrik PLN",
            "period_id": "100",
            "period_label": "2020",
            "derived_period_id": "0",
            "derived_period_label": "Tahun",
            "content_key": "13061452891000",
            "value_numeric": 83.68,
            "value_text": None,
        }
    ]


def test_normalize_dynamic_dimensions_extracts_all_dimension_types() -> None:
    from workers.ingestion.bps_webapi import normalize_dynamic_dimensions

    payload = {
        "vervar": [{"val": 1306, "label": "Padang Pariaman"}],
        "turvar": [{"val": 1, "label": "Laki-laki", "group": 10, "group_label": "Sex"}],
        "tahun": [{"val": 123, "label": "2023"}],
        "turtahun": [{"val": 1, "label": "Januari"}],
    }

    rows = normalize_dynamic_dimensions("1306", "122", payload)

    assert {(row["dimension_type"], row["item_id"]) for row in rows} == {
        ("vervar", "1306"), ("turvar", "1"), ("tahun", "123"), ("turtahun", "1")
    }
    assert next(row for row in rows if row["dimension_type"] == "turvar")["group_id"] == "10"


def test_parse_json_payload_rejects_waf_html() -> None:
    with pytest.raises(ApiPayloadError, match="HTML/WAF"):
        parse_json_payload(b"<!doctype html><title>LTM WAF Block</title>")


def test_parse_json_payload_raises_empty_for_null_body() -> None:
    from workers.ingestion.bps_webapi import ApiEmptyData

    with pytest.raises(ApiEmptyData):
        parse_json_payload(b"null")


def test_require_text_rejects_missing_identifier() -> None:
    from workers.ingestion.bps_webapi import require_text

    with pytest.raises(ApiPayloadError):
        require_text({"title": "x"}, "var_id")
    assert require_text({"var_id": 156}, "var_id") == "156"


def test_decode_dynamic_facts_rejects_mismatched_var() -> None:
    payload = {"var": [{"val": 999, "label": "X"}], "datacontent": {}}

    with pytest.raises(ApiPayloadError, match="!= requested"):
        decode_dynamic_facts("1306", payload, expected_var_id="156")


def test_merge_publication_rows_preserves_list_fields() -> None:
    from workers.ingestion.bps_webapi import merge_publication_rows

    list_row = {
        "pub_id": "1", "pdf": "http://x/1.pdf", "size": "1 MB",
        "cover": "http://x/c.jpg", "rl_date": "2026-02-27",
    }
    detail = {"pub_id": "1", "abstract": "abstrak", "pdf": None}

    merged = merge_publication_rows(list_row, detail)

    assert merged["pdf"] == "http://x/1.pdf"
    assert merged["abstract"] == "abstrak"
    assert merged["size"] == "1 MB"


def test_decode_dynamic_facts_preserves_unmatched_content_key() -> None:
    payload = {
        "var": [{"val": 1, "label": "X", "unit": "Orang"}],
        "turvar": [],
        "vervar": [],
        "tahun": [],
        "datacontent": {"unexpected": "-"},
    }

    facts = decode_dynamic_facts("1306", payload)

    assert facts[0]["content_key"] == "unexpected"
    assert facts[0]["value_text"] == "-"
    assert facts[0]["vervar_id"] is None


def test_normalize_census_rows_keeps_four_category_slots() -> None:
    payload = {
        "timestamp": "2022-07-11T10:35:03.125",
        "data": [
            {
                "id_wilayah": "area-1",
                "kode_wilayah": "1306",
                "nama_wilayah": "PADANG PARIAMAN",
                "level_wilayah": 2,
                "id_indikator": "indicator-1",
                "nama_indikator": "Jumlah Penduduk",
                "id_kategori_1": "sex",
                "nama_kategori_1": "Jenis Kelamin",
                "id_item_kategori_1": "male",
                "kode_item_kategori_1": "L",
                "nama_item_kategori_1": "Laki-laki",
                "period": "2020",
                "nilai": "215000.0",
            }
        ],
    }

    rows = normalize_census_rows("sp2020", "dataset-1", payload)

    assert rows[0]["geography_code"] == "1306"
    assert rows[0]["value_numeric"] == 215000.0
    assert rows[0]["categories"] == [
        {
            "category_id": "sex",
            "category_name": "Jenis Kelamin",
            "item_id": "male",
            "item_code": "L",
            "item_name": "Laki-laki",
        }
    ]
    assert rows[0]["raw"]["level_wilayah"] == 2


def test_normalize_census_rows_preserves_fifth_category_slot() -> None:
    payload = {
        "timestamp": "2026-08-14 11:59:02",
        "data": [{
            "id_wilayah": "2991", "kode_wilayah": "1306010",
            "id_indikator": "3873918", "period": "2010", "nilai": 1,
            "id_kategori_5": "1050", "nama_kategori_5": "Kategori Kelima",
            "id_item_kategori_5": "2050", "kode_item_kategori_5": "X",
            "nama_item__kategori_5": "Item Kelima",
        }],
    }

    facts = normalize_census_rows("sp2010", "dataset", payload)

    assert facts[0]["categories"] == [{
        "category_id": "1050", "category_name": "Kategori Kelima",
        "item_id": "2050", "item_code": "X", "item_name": "Item Kelima",
    }]


def test_census_fact_identity_excludes_revisable_value_and_raw_noise() -> None:
    from workers.ingestion.bps_webapi import census_fact_identity

    base = {
        "event_id": "sp2010", "dataset_id": "6", "geography_id": "2991",
        "geography_code": "1306010", "indicator_id": "3873918", "period": "2010",
        "categories": [{"category_id": "1026", "item_id": "1355"}],
        "value_numeric": 21992, "raw": {"nilai": 21992, "irrelevant": "a"},
    }
    revised = {**base, "value_numeric": 22000, "raw": {"nilai": 22000, "irrelevant": "b"}}

    assert census_fact_identity(base) == census_fact_identity(revised)


def test_normalize_simdasi_detail_handles_headers_and_rows_without_schema_loss() -> None:
    payload = {
        "status": 200,
        "condition": "OK",
        "created": "2026-08-14 17:30:32",
        "data": {
            "judul": "Penduduk Menurut Kecamatan",
            "satuan": "orang",
            "kolom": ["Kecamatan", "2023"],
            "baris": [["Batang Anai", "53210"]],
        },
    }

    normalized = normalize_simdasi_detail(
        region_code="1306000",
        table_id="table-1",
        year=2023,
        payload=payload,
    )

    assert normalized["region_code"] == "1306000"
    assert normalized["table_id"] == "table-1"
    assert normalized["year"] == 2023
    assert normalized["title"] == "Penduduk Menurut Kecamatan"
    assert normalized["unit"] == "orang"
    assert normalized["raw"] == payload


def test_localized_numeric_parser_handles_indonesian_grouping() -> None:
    from workers.ingestion.bps_webapi import as_numeric

    assert as_numeric("1.234,56") == 1234.56
    assert as_numeric("12 345,00") == 12345
    assert as_numeric("180,39") == 180.39
    assert as_numeric("1.234", decimal_places=0) == 1234
    assert as_numeric("1.234") == 1.234
    assert as_numeric("...") is None


def test_normalize_simdasi_facts_flattens_columns_rows_and_markers() -> None:
    payload = {
        "judul_tabel": "Luas Daerah Menurut Kecamatan, 2017",
        "tahun_data": 2017,
        "kolom": {
            "area": {"nama_variabel": "Luas Wilayah", "tipe": "Numerik", "satuan": "km<sup>2</sup>", "angka_desimal_dibelakang_koma": 2},
            "island": {"nama_variabel": "Jumlah Pulau", "tipe": "Numerik", "angka_desimal_dibelakang_koma": 0},
        },
        "data": [
            {
                "label": "Batang Anai",
                "kode_wilayah": 1306010,
                "variables": {
                    "area": {"value": "180,39", "value_raw": "180,39", "value_code": None},
                    "island": {"value": "...", "value_raw": None, "value_code": "..."},
                },
            }
        ],
        "keterangan_data": {"...": "Data tidak tersedia"},
    }

    columns, facts = normalize_simdasi_facts("1306000", "table-1", 2017, payload)

    assert columns[0]["column_id"] == "area"
    assert columns[0]["unit"] == "km²"
    assert facts[0]["geography_code"] == "1306010"
    assert facts[0]["value_numeric"] == 180.39
    assert facts[1]["value_numeric"] is None
    assert facts[1]["value_code"] == "..."
    assert facts[1]["value_note"] == "Data tidak tersedia"


def test_normalize_publication_preserves_pdf_lineage() -> None:
    row = normalize_publication(
        "1306",
        {
            "pub_id": "pub-1",
            "title": "Kabupaten Padang Pariaman Dalam Angka 2026",
            "rl_date": "2026-02-27",
            "updt_date": None,
            "pdf": "https://example.bps.go.id/pub-1.pdf",
            "size": "12.3 MB",
        },
    )

    assert row["domain"] == "1306"
    assert row["publication_id"] == "pub-1"
    assert row["pdf_url"].endswith("pub-1.pdf")
    assert row["raw"]["size"] == "12.3 MB"


def test_normalize_glossary_hit_unwraps_elasticsearch_source() -> None:
    hit = {
        "_id": "glosarium_4406_web",
        "_source": {
            "id": "4406",
            "konsep": "Agama",
            "konsep_en": "Religion",
            "definisi": "Agama merupakan keyakinan...",
            "satuan": "",
            "endpoint": "web",
        },
    }

    row = normalize_glossary_hit(hit)

    assert row["glossary_id"] == "4406"
    assert row["concept"] == "Agama"
    assert row["definition"].startswith("Agama merupakan")
    assert row["raw"] == hit


def test_publication_binary_is_not_expected_inside_database_fixture(tmp_path: Path) -> None:
    target = tmp_path / "publication.pdf"
    target.write_bytes(b"%PDF-1.7 fixture")

    assert target.stat().st_size == 16
    assert target.read_bytes().startswith(b"%PDF")
