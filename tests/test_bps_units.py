from __future__ import annotations

from workers.ingestion.bps_units import canonical_unit, resolve_unit, table_family


# --- resolve_unit (SIMDASI) ---


def test_column_metadata_unit_wins() -> None:
    unit, source = resolve_unit("Kepadatan Penduduk per km persegi", "km persegi", "Judul")

    assert unit == "km persegi"
    assert source == "column_meta"


def test_unit_extracted_from_column_name() -> None:
    unit, source = resolve_unit("Distribusi Persentase (persen)", None, "Judul Tabel")

    assert unit == "persen"
    assert source == "column_name"


def test_indicator_name_pattern() -> None:
    assert resolve_unit("Rasio Jenis Kelamin Penduduk", None, "Judul") == ("rasio", "column_name")
    assert resolve_unit("Laju Pertumbuhan Penduduk per Tahun", None, "Judul") == (
        "persen/tahun",
        "column_name",
    )


def test_unit_from_table_title() -> None:
    unit, source = resolve_unit(
        "Penduduk (Laki-Laki)", None, "Jumlah Penduduk Menurut Jenis Kelamin (ribu jiwa)"
    )

    assert unit == "ribu jiwa"
    assert source == "table_title"


def test_text_column() -> None:
    unit, source = resolve_unit("Ibu Kota Wilayah", None, "Judul", data_type="Teks")

    assert unit is None
    assert source == "text_column"


def test_count_fallback() -> None:
    unit, source = resolve_unit("Jumlah Desa", None, "Jumlah Desa Menurut Kecamatan")

    assert unit is None
    assert source == "count"


def test_unresolved_returns_none() -> None:
    unit, source = resolve_unit(
        "Indeks Pembangunan Manusia", None, "Indeks Pembangunan Manusia Menurut Kecamatan"
    )

    assert unit is None
    assert source == "unresolved"


def test_indicator_pattern_does_not_leak_into_table_title() -> None:
    title = "Jumlah Penduduk, Laju Pertumbuhan Penduduk, Kepadatan Penduduk Menurut Kecamatan"

    unit, source = resolve_unit("Jumlah Penduduk", None, title)

    assert unit is None
    assert source == "count"


def test_persentase_in_title_does_not_leak_to_other_columns() -> None:
    title = "Jumlah Penduduk, Distribusi Persentase Penduduk Menurut Kecamatan"

    assert resolve_unit("Jumlah Penduduk", None, title) == (None, "count")
    assert resolve_unit("Persentase Penduduk", None, title) == ("persen", "column_name")


def test_table_family_two_segments() -> None:
    assert table_family("3.1.1") == "3.1"
    assert table_family("12.4") == "12.4"


# --- canonical_unit (Dynamic) ---


def test_normalizes_common_abbreviations() -> None:
    assert canonical_unit("M2") == "m²"
    assert canonical_unit("M3") == "m³"
    assert canonical_unit("KG") == "kg"


def test_fixes_spelling_and_casing() -> None:
    assert canonical_unit("Milyar Rupiah") == "miliar rupiah"
    assert canonical_unit("Persen") == "persen"
    assert canonical_unit("Rumah Tangga ") == "rumah tangga"


def test_hektar_becomes_ha_for_consistency_with_simdasi() -> None:
    assert canonical_unit("Hektar") == "ha"


def test_bare_count_markers_become_none() -> None:
    assert canonical_unit("Tidak Ada Satuan") is None
    assert canonical_unit(None) is None
    assert canonical_unit("") is None
    assert canonical_unit("-") is None


def test_unknown_units_lowercased_and_trimmed() -> None:
    assert canonical_unit("  Ton/Ha ") == "ton/ha"
    assert canonical_unit("Ekor") == "ekor"
    assert canonical_unit("Bencana") == "bencana"
