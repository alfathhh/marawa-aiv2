"""SIMDASI unit resolution for the table-code → unit registry."""
from __future__ import annotations

import re
from typing import Any

# Ordered: longer/more specific units must match before generic ones.
# These are pure units and may also match table titles.
UNIT_PATTERNS: list[tuple[str, str]] = [
    (r"miliar rupiah", "miliar rupiah"),
    (r"juta rupiah", "juta rupiah"),
    (r"ribu rupiah", "ribu rupiah"),
    (r"\brupiah\b", "rupiah"),
    (r"ribu jiwa", "ribu jiwa"),
    (r"\bjiwa\b", "jiwa"),
    (r"persen per tahun", "persen/tahun"),
    (r"\bpersen\b", "persen"),
    (r"\borang\b", "orang"),
    (r"km persegi", "km²"),
    (r"km<sup>2</sup>", "km²"),
    (r"km2", "km²"),
    (r"meter persegi", "m²"),
    (r"\bkilometer\b", "km"),
    (r"\bhektar\w*", "ha"),
    (r"\bkuintal\b", "kuintal"),
    (r"\bton\b", "ton"),
    (r"\bunit\b", "unit"),
    (r"\bkm\b", "km"),
    (r"\bha\b", "ha"),
    (r"\bkw\b", "kuintal"),
]

# Indicator-name patterns imply a unit but must NOT match table titles: a
# multi-indicator title mentioning "laju pertumbuhan" must not label every
# column of the table as percent.
INDICATOR_PATTERNS: list[tuple[str, str]] = [
    (r"laju pertumbuhan", "persen/tahun"),
    (r"rasio jenis kelamin", "rasio"),
    (r"persentase", "persen"),
]


def _match(text: str, patterns: list[tuple[str, str]]) -> str | None:
    if not text:
        return None
    for pattern, unit in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return unit
    return None


def match_unit(text: str | None) -> str | None:
    """Match pure units in any text (column names and table titles)."""
    return _match(text or "", UNIT_PATTERNS)


def resolve_unit(
    column_name: str | None,
    column_unit: str | None,
    table_title: str | None,
    data_type: str | None = None,
) -> tuple[str | None, str]:
    """Resolve a column's unit with explicit provenance.

    Precedence: column metadata → indicator-name pattern → column-name unit →
    table-title unit → count/text fallbacks. Returns ``(unit, source)``.
    """
    if data_type == "Teks":
        return None, "text_column"
    if column_unit and column_unit.strip():
        return column_unit.strip(), "column_meta"
    indicator_match = _match(column_name or "", INDICATOR_PATTERNS)
    if indicator_match:
        return indicator_match, "column_name"
    name_match = match_unit(column_name)
    if name_match:
        return name_match, "column_name"
    title_match = match_unit(table_title)
    if title_match:
        return title_match, "table_title"
    if column_name and re.match(
        r"^(jumlah|banyaknya|desa/kelurahan|jumlah desa|desa kelurahan|tenaga kesehatan)\b",
        column_name.strip(),
        re.IGNORECASE,
    ):
        return None, "count"
    return None, "unresolved"


def table_family(table_code: str) -> str:
    """Return the two-segment family of a SIMDASI table code (``3.1.1`` → ``3.1``)."""
    parts = table_code.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else table_code


# National WebAPI unit vocabulary → canonical symbol. General (not region-specific).
UNIT_ALIASES: dict[str, str] = {
    "m2": "m²",
    "m3": "m³",
    "km2": "km²",
    "milyar rupiah": "miliar rupiah",
    "hektar": "ha",
    "hektare": "ha",
    "ribuan va": "ribuan VA",
}

_BARE_COUNT_MARKERS = {"", "-", "none", "n/a", "tidak ada satuan", "tanpa satuan"}


def canonical_unit(raw: str | None) -> str | None:
    """Normalize a raw dynamic-variable unit to a canonical symbol.

    Collapses whitespace, lowercases, fixes known abbreviations/spelling, and
    maps bare-count markers (``Tidak Ada Satuan``) to ``None``.
    """
    if raw is None:
        return None
    text = " ".join(raw.strip().split())
    if text.lower() in _BARE_COUNT_MARKERS:
        return None
    return UNIT_ALIASES.get(text.lower(), text.lower())


def marker_legend(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten SIMDASI ``keterangan_data`` maps into a marker → description legend."""
    legend: dict[str, str] = {}
    for row in rows:
        raw = row.get("raw") or {}
        keterangan = raw.get("keterangan_data") or {}
        if not isinstance(keterangan, dict):
            continue
        for marker, description in keterangan.items():
            if marker is not None and description is not None and str(marker).strip():
                legend.setdefault(str(marker), str(description))
    return legend
