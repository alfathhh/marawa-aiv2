"""Deterministic BPS data quality and WhatsApp presentation helpers."""
from __future__ import annotations


def classify_table_health(*, row_count: int, failed_resources: int) -> str:
    if row_count == 0:
        return "empty"
    if failed_resources > 0:
        return "partial"
    return "ready"


def format_whatsapp_statistic(
    *,
    indicator: str,
    geography: str,
    period: str,
    value: str,
    unit: str,
    source: str,
    updated_at: str,
    note: str | None,
) -> str:
    lines = [
        f"*{indicator}*",
        f"📍 Wilayah: {geography}",
        f"📅 Periode: {period}",
        f"📊 Nilai: *{value} {unit}*",
        "",
        f"Sumber: {source}",
        f"Diperbarui: {updated_at}",
    ]
    if note:
        lines.append(f"Catatan: {note}")
    return "\n".join(lines)
