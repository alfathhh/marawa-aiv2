from __future__ import annotations

from workers.ingestion.bps_analysis import classify_table_health, format_whatsapp_statistic


def test_classify_table_health_distinguishes_empty_partial_and_ready() -> None:
    assert classify_table_health(row_count=0, failed_resources=0) == "empty"
    assert classify_table_health(row_count=10, failed_resources=2) == "partial"
    assert classify_table_health(row_count=10, failed_resources=0) == "ready"


def test_whatsapp_single_value_format_has_required_grounding_fields() -> None:
    text = format_whatsapp_statistic(
        indicator="Jumlah Penduduk",
        geography="Kabupaten Padang Pariaman",
        period="2025",
        value="462.125",
        unit="orang",
        source="BPS WebAPI — SIMDASI",
        updated_at="14 Agustus 2026",
        note=None,
    )

    assert "*Jumlah Penduduk*" in text
    assert "Kabupaten Padang Pariaman" in text
    assert "2025" in text
    assert "462.125 orang" in text
    assert "Sumber: BPS WebAPI — SIMDASI" in text
    assert "Diperbarui: 14 Agustus 2026" in text


def test_whatsapp_format_includes_short_caveat() -> None:
    text = format_whatsapp_statistic(
        indicator="Kemiskinan",
        geography="Kabupaten Padang Pariaman",
        period="2024",
        value="6,78",
        unit="persen",
        source="BPS WebAPI — Dynamic Data",
        updated_at="14 Agustus 2026",
        note="Angka masih berstatus sementara.",
    )

    assert "Catatan: Angka masih berstatus sementara." in text
