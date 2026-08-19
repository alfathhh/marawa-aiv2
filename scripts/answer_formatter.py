#!/usr/bin/env python3
"""Answer formatter — turns evidence into the WhatsApp text of docs/18.

Deterministic on purpose. The model decides WHAT to say (which dataset, which
period, whether it can answer at all); this module decides HOW the number is
printed. Splitting it that way means a fabricated figure has nowhere to enter:
the formatter can only render values it was handed.

It also means the answer_gate almost never has to fire on well-behaved runs,
because every number in the output came from an Evidence object by construction.
The gate remains as the check on the model's own prose — belt and braces.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from scripts.answer_gate import Evidence, NoDataReason, abstention_text

# 'canonical' = unit dari bps_dynamic_variables.unit_canonical (terverifikasi,
# paling terpercaya). 'known' = unit mentah dari fact. 'unitless' = memang tak
# bersatuan. Ketiganya boleh dipublikasikan; 'unknown_review' (unit tak jelas)
# WAJIB ditolak — angka tanpa satuan pasti adalah jawaban yang salah.
PUBLISHABLE_UNIT_STATES = frozenset({"canonical", "known", "unitless"})

FAMILY_PREFIX = {
    "simdasi": ("S", "SIMDASI"),
    "dynamic": ("D", "Data Dinamis"),
    "census": ("C", "Sensus"),
    "publication": ("P", "Publikasi"),
}


def escape_wa(text: str) -> str:
    """Neutralise WhatsApp markup and newlines in upstream data.

    AUDIT L: BPS titles are external data. A stray asterisk turns the rest of
    the message bold, and an embedded newline splits one candidate entry across
    two lines so the reference letter no longer lines up with its title — the
    user then picks the wrong table.
    """
    collapsed = " ".join((text or "").split())
    for marker in ("*", "_", "~", "`"):
        collapsed = collapsed.replace(marker, "")
    return collapsed


def format_number(value: Decimal | int | float, decimals: int = 0) -> str:
    """Indonesian convention: dot groups thousands, comma marks decimals."""
    quantized = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals)) if decimals else Decimal(
        str(value)
    ).quantize(Decimal(1))
    text = f"{quantized:,}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _value_with_unit(evidence: Evidence, decimals: int = 0) -> str:
    number = format_number(evidence.value, decimals)
    if evidence.unit_state == "unitless" or not evidence.unit:
        return f"*{number}*"
    return f"*{number} {evidence.unit}*"


@dataclass(frozen=True)
class Candidate:
    ref: str            # "D1"
    family: str
    title: str
    period_range: str


def format_candidates(
    candidates: Sequence[Candidate],
    recommended_ref: str | None = None,
) -> str:
    """The discovery turn (docs/18 §3). User must pick before any fact query."""
    lines: list[str] = []
    if not candidates:
        lines.append("")
    elif recommended_ref:
        lines.append(
            "Saya menemukan beberapa sumber yang relevan. Untuk angka terbaru "
            f"yang exact, saya paling menyarankan {recommended_ref}."
        )
    else:
        lines.append("Saya menemukan beberapa sumber yang relevan.")

    by_family: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_family.setdefault(candidate.family, []).append(candidate)

    for family in ("simdasi", "dynamic", "census", "publication"):
        group = by_family.get(family)
        if not group:
            continue
        _prefix, label = FAMILY_PREFIX[family]
        lines.append("")
        lines.append(f"*{label}*")
        for candidate in group:
            lines.append(
                f"{candidate.ref}. {escape_wa(candidate.title)} — "
                f"{escape_wa(candidate.period_range)}"
            )

    # AUDIT K: with no candidates the hint used to fall back to a hard-coded
    # "D1", telling the citizen to pick an option that was never shown.
    refs = [c.ref for c in candidates]
    lines.append("")
    if refs:
        lines.append(f'Bisa jawab "{refs[0]}", sebutkan kata kuncinya, atau "lanjut publikasi".')
    else:
        lines.append(
            "Saya belum menemukan sumber yang cocok. Coba sebutkan indikatornya "
            "lebih spesifik, atau balas *ADMIN* untuk dibantu petugas PST."
        )
    return "\n".join(lines)


def format_single_value(
    evidence: Evidence,
    indicator_label: str,
    category_label: str | None = None,
    updated_label: str | None = None,
    decimals: int = 0,
) -> str:
    """docs/18 §4. Refuses to render anything whose unit is not settled."""
    if evidence.value is None:
        return abstention_text(
            NoDataReason.NOT_IN_CATALOGUE, indicator_label=indicator_label
        )
    if evidence.unit_state not in PUBLISHABLE_UNIT_STATES:
        # Never printed with a guessed unit, and never printed bare either —
        # a number without its unit is its own kind of wrong answer.
        return abstention_text(
            NoDataReason.UNIT_UNDER_REVIEW, indicator_label=indicator_label
        )

    heading = indicator_label
    if category_label:
        heading = f"{indicator_label} — {category_label}"

    lines = [
        f"*{heading}*",
        f"📍 Wilayah: {evidence.geography}",
        f"📅 Periode: {evidence.period}",
        f"📊 Nilai: {_value_with_unit(evidence, decimals)}",
        "",
        f"Sumber: {evidence.source_label}",
    ]
    if updated_label:
        lines.append(f"Diperbarui: {updated_label}")
    return "\n".join(lines)


def describe_indicator(
    title: str,
    *,
    topic_name: str | None = None,
    definition: str | None = None,
    unit: str | None = None,
    period_min: str | int | None = None,
    period_max: str | int | None = None,
    period_granularity: str | None = None,
    geography_label: str | None = None,
) -> str:
    """Deskripsi indikator yang JUJUR — dibangun dari metadata terverifikasi,
    bukan karangan model (invariant #3 berlaku juga untuk klaim kualitatif).

    Hanya memakai fakta yang diserahkan: topik, definisi resmi bila ada, unit,
    rentang periode, granularitas, cakupan wilayah. Kalau metadata kosong,
    hasilnya kalimat netral satu baris — tidak pernah mengisi celah dengan
    generalisasi yang terdengar pintar tapi tak bersumber.
    """
    parts: list[str] = []
    # definisi resmi dipakai HANYA bila benar-benar informatif — bukan echo
    # judul + suffix ("Jumlah Penduduk" -> "Jumlah Penduduk Menurut Jenis
    # Kelamin" adalah parafrase yang menyesatkan, bukan definisi konsep).
    if definition:
        d = definition.strip().rstrip(".")
        t = title.strip().lower()
        dl = d.lower()
        if dl != t and not dl.startswith(t) and len(d) > len(title) + 15:
            parts.append(d + ".")
    if topic_name:
        parts.append(f"Indikator ini termasuk kelompok {topic_name}.")
    if period_min and period_max:
        parts.append(f"Data tersedia untuk periode {period_min}–{period_max}.")
    elif period_max:
        parts.append(f"Periode terbaru: {period_max}.")
    if period_granularity:
        gran = {"annual": "tahunan", "quarterly": "triwulanan", "monthly": "bulanan"}.get(
            str(period_granularity).lower(), str(period_granularity)
        )
        parts.append(f"Frekuensi data: {gran}.")
    if unit:
        parts.append(f"Satuan: {unit}.")
    if geography_label:
        parts.append(f"Cakupan wilayah: {geography_label}.")
    return " ".join(parts)


def format_trend(
    rows: Sequence[Evidence],
    indicator_label: str,
    decimals: int = 0,
) -> str:
    """docs/18 §6. Period is always printed; "terbaru" alone is never enough."""
    usable = [r for r in rows if r.value is not None and r.unit_state in PUBLISHABLE_UNIT_STATES]
    if not usable:
        return abstention_text(NoDataReason.NOT_IN_CATALOGUE, indicator_label=indicator_label)

    geography = usable[0].geography
    lines = [f"*{indicator_label}*", f"📍 {geography}", ""]
    for row in usable:
        lines.append(f"• {row.period}: {format_number(row.value, decimals)} {row.unit or ''}".rstrip())

    skipped = len(rows) - len(usable)
    if skipped:
        lines.append("")
        lines.append(
            f"({skipped} periode tidak ditampilkan karena datanya tidak tersedia "
            "atau satuannya belum dikonfirmasi.)"
        )
    lines.append("")
    lines.append(f"Sumber: {usable[0].source_label}")
    return "\n".join(lines)


def format_comparison(
    older: Evidence,
    newer: Evidence,
    indicator_label: str,
) -> str:
    """docs/18 §7. Head-to-head dua periode: nilai masing-masing + selisih + %.

    Selisih & persen dihitung DI SINI (deterministik), bukan oleh LLM —
    invariant #4. Kedua periode SELALU dicetak eksplisit; "terbaru" tidak
    pernah berdiri sendiri. Unit harus cocok; kalau beda, abstain (angka
    tanpa satuan seragam menyesatkan).
    """
    if older.value is None or newer.value is None:
        return abstention_text(NoDataReason.NOT_IN_CATALOGUE, indicator_label=indicator_label)
    if (older.unit or "") != (newer.unit or ""):
        return abstention_text(NoDataReason.UNIT_UNDER_REVIEW, indicator_label=indicator_label)

    delta = newer.value - older.value
    if older.value != 0:
        pct = (delta / abs(older.value)) * Decimal(100)
        pct_str = f"{pct:+.1f}%".replace(".", ",")
    else:
        pct_str = "n/a (basis 0)"

    arah = "naik" if delta > 0 else ("turun" if delta < 0 else "tetap")
    unit = newer.unit or ""
    delta_s = f"{delta:+,.0f}".replace(",", ".")

    lines = [
        f"*{indicator_label}* — perbandingan",
        f"📍 {newer.geography or older.geography}",
        "",
        f"• {older.period}: {format_number(older.value)} {unit}".rstrip(),
        f"• {newer.period}: {format_number(newer.value)} {unit}".rstrip(),
        "",
        f"Perubahan: {delta_s} {unit} ({pct_str}) — {arah}.",
        "",
        f"Sumber: {newer.source_label}",
    ]
    return "\n".join(lines)


def system_counts_for(
    candidates: Iterable[Candidate] = (),
    rows: Iterable[Evidence] = (),
) -> frozenset[int]:
    """Counts the runtime knows, handed to the gate so natural phrasing passes.

    Without this the agent cannot write "ada 3 tabel yang cocok" — the gate would
    read 3 as an unsourced statistic. See AGENT.md §0-BATAS.
    """
    candidate_list = list(candidates)
    row_list = list(rows)
    counts = {len(candidate_list), len(row_list)}
    counts.add(len({r.geography for r in row_list if r.geography}))
    counts.add(len({r.period for r in row_list if r.period}))
    return frozenset(c for c in counts if c > 0)
