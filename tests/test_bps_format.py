from __future__ import annotations

from workers.ingestion.bps_format import format_id_number


def test_integers_use_dot_thousands() -> None:
    assert format_id_number(467000) == "467.000"
    assert format_id_number(207131) == "207.131"
    assert format_id_number(597) == "597"
    assert format_id_number(0) == "0"


def test_decimals_use_comma() -> None:
    assert format_id_number(59.7) == "59,7"
    assert format_id_number(1234.56) == "1.234,56"
    assert format_id_number(0.5) == "0,5"


def test_negative_numbers() -> None:
    assert format_id_number(-10500.5) == "-10.500,5"
    assert format_id_number(-42) == "-42"


def test_none_and_integer_float() -> None:
    assert format_id_number(None) == ""
    assert format_id_number(500.0) == "500"


def test_trailing_zeros_stripped() -> None:
    assert format_id_number(1.50) == "1,5"
    assert format_id_number(2.0) == "2"
