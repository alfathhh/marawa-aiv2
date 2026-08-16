"""Deterministic number formatting in Indonesian locale convention."""
from __future__ import annotations


def format_id_number(value: float | int | None, max_decimals: int = 4) -> str:
    """Format a number Indonesian-style: dot thousands separator, comma decimal.

    ``467000 → "467.000"``, ``59.7 → "59,7"``, ``1234.56 → "1.234,56"``,
    ``-10500.5 → "-10.500,5"``, ``None → ""``.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    whole = int(magnitude)
    fraction = magnitude - whole
    frac_digits = (
        f"{fraction:.{max_decimals}f}".split(".")[1].rstrip("0") if fraction else ""
    )
    whole_formatted = f"{whole:,}".replace(",", ".")
    return f"{sign}{whole_formatted},{frac_digits}" if frac_digits else f"{sign}{whole_formatted}"
