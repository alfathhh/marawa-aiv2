#!/usr/bin/env python3
"""Build and publish the BPS query registry from the local mirror database.

Deterministic, read-mostly; writes only to schema bps_registry. No WebAPI, no LLM.
Classification rules are explicit; anything unknown becomes unknown_review, never guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
ALLOWED_VIEWS = {
    "bps_serving_dynamic",
    "bps_serving_simdasi",
    "bps_serving_census",
    "bps_publications",
}
COUNT_TITLE_RE = re.compile(r"^(jumlah|banyaknya|banyak|luas|produksi)", re.IGNORECASE)

# Unit provenance that is inferred, not sourced. Serving views record these in
# `unit_source`; the registry must never present them as settled facts.
HEURISTIC_UNIT_SOURCES = frozenset({"title_matched"})

# Only these unit states may be exposed to a fact query. Anything else needs a
# data-owner decision first (docs/26 unit review packet).
QUERYABLE_UNIT_STATES = frozenset({"known", "unitless"})
NORM_RE = re.compile(r"[^a-z0-9]+")

MANUAL_GEOGRAPHY_ALIASES: dict[str, tuple[str, ...]] = {
    "Lubuak Aluang": ("LUBUK ALUNG", "Lubuk Alung", "Lubuk Aluang"),
    "Ulakan Tapakih": ("ULAKAN TAPAKIS", "Ulakan Tapakis"),
    "Sungai Garinggiang": ("SUNGAI GERINGGING", "Sungai Geringging"),
    "Koto Patamuan": ("VII Koto Patamuan", "PATAMUAN", "Koto Patamuan"),
    "Anam Lingkuang": ("ENAM LINGKUNG", "Enam Lingkung"),
    "2 X 11 Anam Lingkuang": ("2x11 ENAM LINGKUNG", "2 X 11 Enam Lingkung"),
    "V Koto": ("V KOTO", "5 Koto"),
    "VII Koto Padang Sago": ("VII KOTO PADANG SAGO", "7 Koto Padang Sago"),
    "IV Koto Aua Malintang": ("IV KOTO AUR MALINTANG", "4 Koto Aur Malintang"),
}


def _norm(text: str) -> str:
    return NORM_RE.sub("", (text or "").lower())


def _pid() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:12]}"


def _apply_schema(connection: psycopg.Connection) -> None:
    sql = (ROOT / "migrations" / "002_registry_schema.sql").read_text(encoding="utf-8")
    connection.execute(sql)


def _fetch(connection: psycopg.Connection, sql: str, params: tuple | None = None) -> list[tuple]:
    return connection.execute(sql, params).fetchall()


def _q(connection: psycopg.Connection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


# Canonical geography master (data-owner-verifiable seed: BPS code -> official name).
# Source labels are kept as aliases; this table prevents cross-table label drift.
CANONICAL_NAMES_BY_CODE: dict[str, str] = {
    "1306000": "Padang Pariaman",
    "1306010": "Batang Anai",
    "1306020": "Lubuak Aluang",
    "1306021": "Sintuak Toboh Gadang",
    "1306030": "Ulakan Tapakih",
    "1306040": "Nan Sabaris",
    "1306050": "2 X 11 Anam Lingkuang",
    "1306051": "Anam Lingkuang",
    "1306052": "2 X 11 Kayu Tanam",
    "1306060": "VII Koto Sungai Sarik",
    "1306061": "Koto Patamuan",
    "1306062": "VII Koto Padang Sago",
    "1306070": "V Koto Kampung Dalam",
    "1306071": "V Koto Timur",
    "1306080": "Sungai Limau",
    "1306081": "Batang Gasan",
    "1306090": "Sungai Garinggiang",
    "1306100": "IV Koto Aua Malintang",
}

KECAMATAN_SORT_ORDER = ("1306010", "1306020", "1306021", "1306030", "1306040",
                        "1306050", "1306051", "1306052", "1306060", "1306061",
                        "1306062", "1306070", "1306071", "1306080", "1306081",
                        "1306090", "1306100")


def _load_geography(connection: psycopg.Connection) -> list[dict[str, Any]]:
    # Majority label per code, then canonical override BY CODE (never substring).
    rows = _fetch(
        connection,
        """
        SELECT geography_code,
               mode() WITHIN GROUP (ORDER BY geography_name) AS majority_name,
               count(*) AS occurrences
        FROM bps_serving_simdasi
        WHERE region_code='1306000' AND row_role IN ('kabupaten','kecamatan')
        GROUP BY geography_code
        """,
    )
    by_code = {code: name for code, name, _count in rows}
    geographies: list[dict[str, Any]] = []
    for code, canonical in CANONICAL_NAMES_BY_CODE.items():
        observed = by_code.get(code)
        level = "kabupaten" if code == "1306000" else "kecamatan"
        geographies.append({
            "code": code,
            "name": canonical,
            "level": level,
            "sort": 0 if level == "kabupaten" else KECAMATAN_SORT_ORDER.index(code) + 1,
            "source_label": observed,
        })
    assert len(geographies) == 18, f"expected 18 geographies, got {len(geographies)}"
    assert len({geo["code"] for geo in geographies}) == 18, "duplicate codes"
    return geographies


def _load_geography_aliases(
    connection: psycopg.Connection, geographies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    geo_names_norm = {_norm(geo["name"]): geo for geo in geographies}
    aliases: list[dict[str, Any]] = []
    source_rows = _fetch(
        connection,
        """
        SELECT 'simdasi', geography_code, geography_name
        FROM bps_serving_simdasi
        WHERE region_code='1306000' AND row_role IN ('kabupaten','kecamatan')
        GROUP BY 1,2,3
        UNION ALL
        SELECT 'dynamic', vertical_id::text, vertical_label
        FROM bps_dynamic_facts
        WHERE domain='1306' AND variable_id='29'
        GROUP BY 1,2,3
        UNION ALL
        SELECT 'census', geography_code, geography_name
        FROM bps_census_facts
        WHERE event_id='sp2010' AND dataset_id='10'
        GROUP BY 1,2,3
        """,
    )
    for family, code, label in source_rows:
        if not label:
            continue
        geo = None
        for candidate in geographies:
            if candidate["code"] == code:
                geo = candidate
                break
        if geo is None:
            geo = geo_names_norm.get(_norm(label))
        if geo is None:
            label_norm = _norm(label)
            if len(label_norm) >= 5:
                for candidate in geographies:
                    name_norm = _norm(candidate["name"])
                    if label_norm in name_norm or name_norm in label_norm:
                        geo = candidate
                        break
        if geo is None:
            continue
        match_type = "exact_code" if geo["code"] == code else "approved_alias"
        if _norm(label) != _norm(geo["name"]):
            aliases.append({
                "geography_id": f"geo:{geo['code']}",
                "source_family": family,
                "source_code": code if code != geo["code"] else None,
                "source_label": label,
                "match_type": "exact_code" if geo["code"] == code else "approved_alias",
            })
    for name, extra_labels in MANUAL_GEOGRAPHY_ALIASES.items():
        geo = geo_names_norm.get(_norm(name))
        if geo is None:
            continue
        for label in extra_labels:
            if _norm(label) != _norm(geo["name"]):
                aliases.append({
                    "geography_id": f"geo:{geo['code']}",
                    "source_family": "simdasi",
                    "source_code": None,
                    "source_label": label,
                    "match_type": "approved_alias",
                })
    seen: set[tuple] = set()
    deduped: list[dict[str, Any]] = []
    for alias in aliases:
        key = (alias["geography_id"], alias["source_family"], alias["source_label"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alias)
    return deduped


def _unit_state(
    title: str,
    raw_unit: str | None,
    canonical: str | None,
    unit_source: str | None = None,
) -> str:
    """Classify how well the unit of a measure is known.

    States:
      known            unit came from source metadata or the canonical map.
      unitless         title itself declares a bare count/area/production measure.
      review_required  unit was DERIVED BY HEURISTIC (title string match) and is
                       therefore a guess; must be confirmed by the data owner.
      unknown_review   no unit available at all; must be confirmed by the owner.

    Audit 2026-08-15 (C2a/C2b): the previous version returned "unitless" whenever
    the title contained "menurut", and treated title-derived currency units as
    "known". Both are guesses and both violated the project invariant "unit tidak
    ditebak". "Menurut" marks a breakdown dimension ("... Menurut Kecamatan"), it
    says nothing about whether the measure has a unit.
    """
    if unit_source in HEURISTIC_UNIT_SOURCES:
        return "review_required"
    if canonical:
        return "known"
    unit = (raw_unit or "").strip()
    # AUDIT T: upstream exports carry several spellings of "no value here".
    # Treating any of them as a real unit means a measure with NO known unit is
    # published as `known` with the literal text "-" or "NULL" printed to a
    # citizen as its satuan. Normalise before deciding.
    if unit.upper() in ("", "NULL", "NONE", "N/A", "NA", "-", "--", "TIDAK ADA SATUAN"):
        if COUNT_TITLE_RE.match(title or ""):
            return "unitless"
        return "unknown_review"
    return "known"


def _aggregation(title: str, unit: str, unit_state: str | None = None) -> str:
    """Aggregation class for a measure.

    Audit 2026-08-15 (M3): an unknown unit previously defaulted to "count", i.e.
    a summable class. Unknown must never imply "safe to add up"; it now returns
    "unknown", which the runtime treats as aggregation-forbidden.
    """
    if unit_state in ("unknown_review", "review_required"):
        return "unknown"
    lowered = (title or "").lower()
    if "persentase" in lowered or "persen" in lowered or unit.strip() == "persen":
        return "share"
    if any(token in lowered for token in ("indeks", "rasio", "laju", "gini")):
        return "index"
    if unit.strip() in ("", "Tidak Ada Satuan"):
        return "count" if COUNT_TITLE_RE.match(title or "") else "unknown"
    return "additive"


def _measure_gate(unit_state: str) -> dict[str, Any]:
    """Per-measure answerability gate.

    Audit 2026-08-15 (C2c): answerability used to be decided per DATASET, so a
    dataset holding one well-defined measure plus six unit-less ones stayed fully
    answerable and those six could be queried and quoted. The gate now travels
    with the measure; the runtime refuses any measure whose `queryable` is false.
    """
    if unit_state in QUERYABLE_UNIT_STATES:
        return {"queryable": True, "quality_flags": []}
    flag = "unit_guessed_review_required" if unit_state == "review_required" else "unit_review_required"
    return {"queryable": False, "quality_flags": [flag]}


def _build_dynamic(
    connection: psycopg.Connection,
    geography_by_name: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    datasets: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    variables = _fetch(
        connection,
        """
        SELECT v.variable_id, v.title, coalesce(v.subject_name,''), v.unit,
               coalesce(v.unit_canonical,''), coalesce(v.definition,''),
               min(f.period_label), max(f.period_label),
               count(*) FILTER (WHERE f.derived_period_label IN
                   ('Triwulan I','Triwulan II','Triwulan III','Triwulan IV')) > 0 AS quarterly
        FROM bps_dynamic_variables v
        JOIN bps_dynamic_facts f
          ON f.domain=v.domain AND f.variable_id=v.variable_id
        WHERE v.domain='1306'
        GROUP BY 1,2,3,4,5,6
        """,
    )
    for variable_id, title, subject, unit, canonical, definition, period_min, period_max, quarterly in variables:
        dataset_id = f"ds:dynamic:{variable_id}"
        verticals = _fetch(
            connection,
            """
            SELECT vertical_id, vertical_label
            FROM bps_dynamic_facts
            WHERE domain='1306' AND variable_id=%s
            GROUP BY 1,2
            """,
            (variable_id,),
        )
        geo_hits = sum(
            1 for _, label in verticals if label and _norm(label) in geography_by_name
        )
        primary_role = "geography" if geo_hits >= 10 else "category"
        shape = "quarterly_series" if quarterly else (
            "geography_series" if primary_role == "geography" else "category_series"
        )
        datasets.append({
            "dataset_id": dataset_id,
            "source_family": "dynamic",
            "source_resource_id": variable_id,
            "title": title,
            "summary": f"{title} — {subject}".strip(" —"),
            "topic_name": subject,
            "dataset_shape": shape,
            "answerability": "answerable",
            "period_granularity": "quarterly" if quarterly else "annual",
            "period_min": period_min,
            "period_max": period_max,
            "period_latest": period_max,
            "search_document": f"{title} {subject} {unit} {definition}",
            "supported_operations": ["lookup", "breakdown", "trend", "compare", "rank"],
        })
        measure_unit_state = _unit_state(title, unit, canonical)
        measures.append({
            "measure_id": f"ms:dynamic:{variable_id}",
            "dataset_id": dataset_id,
            "source_measure_id": "value",
            "name": title,
            "value_type": "number",
            "unit_state": measure_unit_state,
            "unit_display": canonical or unit,
            "decimal_places": 0,
            "aggregation_semantics": _aggregation(title, unit, measure_unit_state),
            "comparability_group": title.lower().strip(),
            **_measure_gate(measure_unit_state),
        })
        dim_id = f"dim:{variable_id}:primary"
        dimensions.append({
            "dimension_id": dim_id,
            "dataset_id": dataset_id,
            "name": "primary",
            "role": primary_role,
            "required": False,
            "total_item_id": None,
            "cardinality": len(verticals),
            "display_order": 0,
        })
        ordered = sorted(
            verticals,
            key=lambda row: (int(row[0]) if row[0] and row[0].isdigit() else 10**9, row[1]),
        )
        for position, (item_id, label) in enumerate(ordered, start=1):
            canonical_geo = None
            if primary_role == "geography":
                geo_code = geography_by_name.get(_norm(label))
                if geo_code:
                    canonical_geo = f"geo:{geo_code}"
            items.append({
                "item_id": f"it:{variable_id}:primary:{item_id}",
                "dimension_id": dim_id,
                "source_item_id": item_id,
                "source_item_code": item_id,
                "label": label,
                "canonical_geography_id": canonical_geo,
                "is_total": (label or "").strip().lower() in {"total", "jumlah"},
                "sort_order": position,
            })
        secondary = _fetch(
            connection,
            """
            SELECT derived_variable_id, derived_variable_label
            FROM bps_dynamic_facts
            WHERE domain='1306' AND variable_id=%s
              AND derived_variable_id IS NOT NULL
            GROUP BY 1,2
            """,
            (variable_id,),
        )
        if secondary:
            dim2_id = f"dim:{variable_id}:secondary"
            dimensions.append({
                "dimension_id": dim2_id,
                "dataset_id": dataset_id,
                "name": "secondary",
                "role": "category",
                "required": False,
                "cardinality": len(secondary),
                "display_order": 1,
            })
            for position, (item_id, label) in enumerate(secondary, start=1):
                items.append({
                    "item_id": f"it:{variable_id}:secondary:{item_id}",
                    "dimension_id": dim2_id,
                    "source_item_id": item_id,
                    "source_item_code": item_id,
                    "label": label,
                    "is_total": (label or "").strip().lower() in {"total", "jumlah"},
                    "sort_order": position,
                })
        if quarterly:
            dim3_id = f"dim:{variable_id}:subperiod"
            dimensions.append({
                "dimension_id": dim3_id,
                "dataset_id": dataset_id,
                "name": "subperiod",
                "role": "subperiod",
                "required": False,
                "cardinality": 5,
                "display_order": 2,
            })
            for position, label in enumerate(
                ("Triwulan I", "Triwulan II", "Triwulan III", "Triwulan IV", "Tahunan"), start=1
            ):
                items.append({
                    "item_id": f"it:{variable_id}:subperiod:{label}",
                    "dimension_id": dim3_id,
                    "source_item_id": label,
                    "source_item_code": label,
                    "label": label,
                    "is_total": label == "Tahunan",
                    "sort_order": position,
                })
    return datasets, measures, dimensions, items


def _build_simdasi(
    connection: psycopg.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    datasets: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    tables = _fetch(
        connection,
        """
        SELECT t.table_code, t.title, coalesce(t.chapter,''),
               min(s.period), max(s.period),
               count(*) FILTER (WHERE s.row_role='kecamatan') > 0 AS has_geo,
               coalesce(string_agg(DISTINCT s.indicator_name, ' '), '') AS indicators
        FROM bps_simdasi_tables t
        JOIN bps_serving_simdasi s
          ON s.region_code=t.region_code AND s.table_id=t.table_id
        WHERE t.region_code='1306000'
        GROUP BY 1,2,3
        """,
    )
    for table_code, title, chapter, period_min, period_max, has_geo, indicators in tables:
        dataset_id = f"ds:simdasi:{table_code}"
        shape = "geography_series" if has_geo else "category_series"
        datasets.append({
            "dataset_id": dataset_id,
            "source_family": "simdasi",
            "source_resource_id": table_code,
            "title": title,
            "summary": f"{title} — {chapter}".strip(" —"),
            "topic_name": chapter,
            "dataset_shape": shape,
            "answerability": "answerable",
            "period_granularity": "annual",
            "period_min": str(period_min),
            "period_max": str(period_max),
            "period_latest": str(period_max),
            "search_document": f"{title} {chapter} {indicators}",
            "supported_operations": ["lookup", "breakdown", "trend", "rank"],
        })
        # `unit_source` travels with the unit so title-derived (guessed) units are
        # never promoted to "known" in the registry. See audit finding C2a.
        measure_rows = _fetch(
            connection,
            """
            SELECT DISTINCT ON (indicator_code) indicator_name, unit, unit_source, indicator_code
            FROM bps_serving_simdasi
            WHERE region_code='1306000' AND table_code=%s
              AND indicator_name IS NOT NULL
            ORDER BY indicator_code, unit_source NULLS LAST, unit NULLS LAST
            """,
            (table_code,),
        )
        for position, (indicator_name, unit, unit_source, indicator_code) in enumerate(
            measure_rows, start=1
        ):
            measure_id = f"ms:simdasi:{table_code}:{position}"
            measure_unit_state = _unit_state(indicator_name, unit, None, unit_source)
            measures.append({
                "measure_id": measure_id,
                "dataset_id": dataset_id,
                # Audit M2: bind queries on the stable column code, not the label.
                "source_measure_id": indicator_code or f"col:{position}",
                "name": indicator_name,
                "value_type": "number",
                "unit_state": measure_unit_state,
                "unit_display": unit,
                "unit_source": unit_source,
                "decimal_places": 0,
                "aggregation_semantics": _aggregation(
                    indicator_name, unit or "", measure_unit_state
                ),
                **_measure_gate(measure_unit_state),
            })
        row_rows = _fetch(
            connection,
            """
            SELECT agg.geography_code, coalesce(g.name, agg.min_name), agg.row_role
            FROM (
                SELECT geography_code, row_role, min(geography_name) AS min_name
                FROM bps_serving_simdasi
                WHERE region_code='1306000' AND table_code=%s
                GROUP BY 1, 2
            ) agg
            LEFT JOIN bps_registry.geography_registry g ON g.code = agg.geography_code
            """,
            (table_code,),
        )
        dim_role = "geography" if has_geo else "category"
        dim_id = f"dim:simdasi:{table_code}:primary"
        dimensions.append({
            "dimension_id": dim_id,
            "dataset_id": dataset_id,
            "name": "primary",
            "role": dim_role,
            "required": False,
            "cardinality": len(row_rows),
            "display_order": 0,
        })
        ordered = sorted(
            row_rows,
            key=lambda row: (
                0 if row[2] == "kabupaten" else 1 if row[2] == "kecamatan" else 2,
                row[0] or "",
                row[1] or "",
            ),
        )
        for position, (code, label, role) in enumerate(ordered, start=1):
            key = code if code else f"cat:{position}"
            items.append({
                "item_id": f"it:simdasi:{table_code}:primary:{key}",
                "dimension_id": dim_id,
                "source_item_id": key,
                "source_item_code": code,
                "label": label,
                "canonical_geography_id": f"geo:{code}" if dim_role == "geography" and code else None,
                "is_total": code == "1306000",
                "sort_order": position,
            })
    return datasets, measures, dimensions, items


def _normalize_display_label(label: str, fallback: str | None = None) -> tuple[str, str]:
    """HTML/entity cleanup for display. Returns (display_label, rule)."""
    import html as _html

    cleaned = re.sub(r"<[^>]+>", "", label or "")
    cleaned = _html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned and fallback:
        return fallback, "source_label_empty_fallback"
    rule = "strip_html_unescape" if cleaned != label else "none"
    return cleaned, rule


def _build_census(connection: psycopg.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    datasets: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    rows = _fetch(
        connection,
        """
        SELECT d.event_id, d.dataset_id, d.dataset_name, e.event_name, e.event_year,
               EXISTS (SELECT 1 FROM bps_census_facts f
                        WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)
        FROM bps_census_datasets d
        JOIN bps_census_events e USING (event_id)
        """,
    )
    axis_rows = _fetch(
        connection,
        """
        SELECT f.event_id, f.dataset_id,
               trim(cat->>'category_id') AS category_id,
               trim(cat->>'category_name') AS category_name
        FROM bps_census_facts f, jsonb_array_elements(f.categories) cat
        GROUP BY 1,2,3,4
        ORDER BY 1,2,3
        """,
    )
    item_rows = _fetch(
        connection,
        """
        SELECT f.event_id, f.dataset_id,
               trim(cat->>'category_id') AS category_id,
               cat->>'item_code' AS item_code,
               trim(cat->>'item_name') AS item_name
        FROM bps_census_facts f, jsonb_array_elements(f.categories) cat
        GROUP BY 1,2,3,4,5
        ORDER BY 1,2,3,4
        """,
    )
    axes_by_rid: dict[str, list[tuple[str, str]]] = {}
    for event_id, dataset_id, category_id, category_name in axis_rows:
        axes_by_rid.setdefault(f"{event_id}:{dataset_id}", []).append((category_id, category_name))
    items_by_axis: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for event_id, dataset_id, category_id, item_code, item_name in item_rows:
        items_by_axis.setdefault((f"{event_id}:{dataset_id}", category_id), []).append(
            (item_code, item_name)
        )

    for event_id, dataset_id, dataset_name, event_name, event_year, answerable in rows:
        rid = f"{event_id}:{dataset_id}"
        dataset_ref = f"ds:census:{rid}"
        datasets.append({
            "dataset_id": dataset_ref,
            "source_family": "census",
            "source_resource_id": rid,
            "title": dataset_name,
            "summary": f"{event_name} — {dataset_name}",
            "topic_name": event_name,
            "dataset_shape": "cross_tab",
            "answerability": "answerable" if answerable else "metadata_only",
            "period_granularity": "event",
            "period_min": str(event_year),
            "period_max": str(event_year),
            "period_latest": str(event_year),
            "search_document": f"{event_name} {dataset_name}",
            "supported_operations": ["lookup", "cross_tab"],
        })
        measures.append({
            "measure_id": f"ms:census:{rid}",
            "dataset_id": dataset_ref,
            "source_measure_id": "value",
            "name": dataset_name,
            "value_type": "number",
            "unit_state": "unitless",
            "unit_display": None,
            "decimal_places": 0,
            "aggregation_semantics": "count",
            **_measure_gate("unitless"),
        })
        if answerable:
            for position, (category_id, category_name) in enumerate(axes_by_rid.get(rid, []), start=1):
                axis_items = items_by_axis.get((rid, category_id), [])
                axis_name = category_name.strip()
                dimension_id = f"dim:census:{rid}:cat{position}"
                total_item_id = next(
                    (
                        f"it:{dimension_id}:total"
                        for item_code, item_name in axis_items
                        if item_code == "999" or item_name.strip().lower() == "total"
                    ),
                    None,
                )
                dimensions.append({
                    "dimension_id": dimension_id,
                    "dataset_id": dataset_ref,
                    "name": axis_name,
                    "role": "category",
                    "required": True,
                    "cardinality": len(axis_items),
                    "display_order": position,
                    "total_item_id": total_item_id,
                })
                for sort, (item_code, item_name) in enumerate(axis_items, start=1):
                    is_total = item_code == "999" or item_name.strip().lower() == "total"
                    source_item_id = "total" if is_total else item_code
                    items.append({
                        "item_id": f"it:{dimension_id}:{source_item_id}",
                        "dimension_id": dimension_id,
                        "source_item_id": source_item_id,
                        "source_item_code": item_code,
                        "label": item_name.strip(),
                        "is_total": is_total,
                        "sort_order": sort,
                    })
        else:
            dimensions.append({
                "dimension_id": f"dim:census:{rid}:categories",
                "dataset_id": dataset_ref,
                "name": "categories",
                "role": "category",
                "required": False,
                "cardinality": 0,
                "display_order": 0,
            })
    return datasets, measures, dimensions, items


def _build_publications(connection: psycopg.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    datasets: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    rows = _fetch(
        connection,
        """
        SELECT publication_id, title, coalesce(release_date,''), coalesce(abstract,'')
        FROM bps_publications WHERE domain='1306'
        """,
    )
    for publication_id, title, release_date, abstract in rows:
        datasets.append({
            "dataset_id": f"ds:publication:{publication_id}",
            "source_family": "publication",
            "source_resource_id": publication_id,
            "title": title,
            "summary": "Publikasi BPS Kabupaten Padang Pariaman",
            "topic_name": "publication",
            "dataset_shape": "publication_metadata",
            "answerability": "answerable",
            "period_granularity": "release",
            "period_min": release_date[:4] or None,
            "period_max": release_date[:4] or None,
            "period_latest": release_date[:4] or None,
            "search_document": f"{title} {abstract}",
            "supported_operations": ["publication_list"],
        })
        measures.append({
            "measure_id": f"ms:publication:{publication_id}",
            "dataset_id": f"ds:publication:{publication_id}",
            "source_measure_id": "metadata",
            "name": title,
            "value_type": "text",
            "unit_state": "unitless",
            "unit_display": None,
            "decimal_places": 0,
            "aggregation_semantics": "count",
            **_measure_gate("unitless"),
        })
    return datasets, measures


TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "dynamic_point",
        "dataset_shape": "geography_series",
        "view_name": "bps_serving_dynamic",
        "has_own_limit": False,
        "parameter_schema": {
            "indicator_code": "text",
            "period": "text",
            "geography_code": "text|nullable",
            # Audit M1: `primary_dimension_item` was a second name for
            # geography_code and filtered the identical column. Removed.
            "category_item": "text|nullable",
            "subperiod_code": "text|nullable",
        },
        "sql_template": (
            "SELECT indicator_code, period, geography_code, geography_name, "
            "category_code, category_label, "
            "period_granularity, subperiod_code, subperiod_label, "
            "value, unit, unit_state, snapshot_id "
            "FROM bps_serving_dynamic "
            "WHERE domain='1306' AND indicator_code=%(indicator_code)s AND period=%(period)s "
            "AND (%(geography_code)s::text IS NULL OR geography_code=%(geography_code)s) "
            "AND (%(category_item)s::text IS NULL OR category_code=%(category_item)s) "
            "AND (%(subperiod_code)s::text IS NULL OR subperiod_code=%(subperiod_code)s) "
            "ORDER BY geography_code"
        ),
        "validation_rules": ["coverage", "component_total", "value_lineage"],
    },
    {
        "template_id": "dynamic_trend",
        "dataset_shape": "geography_series",
        "view_name": "bps_serving_dynamic",
        "has_own_limit": False,
        "parameter_schema": {
            "indicator_code": "text",
            "period_start": "text",
            "period_end": "text",
            "geography_code": "text|nullable",
            "category_item": "text|nullable",
        },
        "sql_template": (
            "SELECT indicator_code, period, geography_code, geography_name, "
            "category_code, category_label, subperiod_label, "
            "value, unit, unit_state, snapshot_id "
            "FROM bps_serving_dynamic "
            "WHERE domain='1306' AND indicator_code=%(indicator_code)s "
            "AND period BETWEEN %(period_start)s AND %(period_end)s "
            "AND (%(geography_code)s::text IS NULL OR geography_code=%(geography_code)s) "
            "AND (%(category_item)s::text IS NULL OR category_code=%(category_item)s) "
            "ORDER BY period"
        ),
        "validation_rules": ["trend_ordering", "value_lineage"],
    },
    {
        "template_id": "dynamic_quarterly",
        "dataset_shape": "quarterly_series",
        "view_name": "bps_serving_dynamic",
        "has_own_limit": False,
        "parameter_schema": {
            "indicator_code": "text",
            "period": "text",
            "geography_code": "text|nullable",
            "category_item": "text|nullable",
        },
        "sql_template": (
            "SELECT indicator_code, period, geography_code, geography_name, "
            "category_code, category_label, "
            "subperiod_code, subperiod_label, value, unit, unit_state, snapshot_id "
            "FROM bps_serving_dynamic "
            "WHERE domain='1306' AND indicator_code=%(indicator_code)s AND period=%(period)s "
            "AND period_granularity='quarterly' "
            "AND (%(geography_code)s::text IS NULL OR geography_code=%(geography_code)s) "
            "AND (%(category_item)s::text IS NULL OR category_code=%(category_item)s) "
            "ORDER BY subperiod_code"
        ),
        "validation_rules": ["subperiod_preserved", "annual_quarter_sum"],
    },
    {
        "template_id": "simdasi_point",
        "dataset_shape": "geography_series",
        "view_name": "bps_serving_simdasi",
        "has_own_limit": False,
        "parameter_schema": {
            # Audit M2: bind on the stable column code. Exact-matching a human
            # label breaks on whitespace, casing, and upstream label edits.
            "table_code": "text",
            "period": "integer",
            "indicator_code": "text",
            "geography_level": "text|nullable",
        },
        "sql_template": (
            "SELECT table_code, period, row_role, geography_code, geography_name, "
            "indicator_code, indicator_name, value, value_text, unit, unit_source, unit_state, snapshot_id "
            "FROM bps_serving_simdasi "
            "WHERE region_code='1306000' AND table_code=%(table_code)s "
            "AND period=%(period)s AND indicator_code=%(indicator_code)s "
            "AND (%(geography_level)s::text IS NULL OR geography_level=%(geography_level)s) "
            "ORDER BY geography_name"
        ),
        "validation_rules": ["coverage", "rounded_tolerance", "value_lineage"],
    },
    {
        "template_id": "census_cross_tab",
        "dataset_shape": "cross_tab",
        "view_name": "bps_serving_census",
        "has_own_limit": False,
        "parameter_schema": {
            "event_id": "text",
            "dataset_id": "text",
            "geography_code": "text",
            "categories": "jsonb|nullable",
        },
        "sql_template": (
            "SELECT event_id, dataset_id, geography_code, geography_name, "
            "value, categories, snapshot_id "
            "FROM bps_serving_census "
            "WHERE event_id=%(event_id)s AND dataset_id=%(dataset_id)s "
            "AND geography_code=%(geography_code)s "
            "AND (%(categories)s::jsonb IS NULL OR categories @> %(categories)s::jsonb) "
            "ORDER BY categories::text"
        ),
        "validation_rules": ["margin_equality", "category_filtered"],
    },
    {
        "template_id": "publication_list",
        "dataset_shape": "publication_metadata",
        "view_name": "bps_publications",
        # Audit H1: this template carries its own LIMIT/OFFSET, so the binder must
        # NOT append another one. Declared explicitly instead of sniffed from SQL.
        "has_own_limit": True,
        "parameter_schema": {
            # Audit H2: `like` escapes % and _ so a user searching "%" cannot
            # match the entire catalogue.
            "search": "like|nullable",
            "page_size": "integer|max:100",
            "offset": "integer|max:10000",
        },
        "sql_template": (
            "SELECT publication_id, title, release_date "
            "FROM bps_publications "
            "WHERE domain='1306' "
            # Binder escapes %, _ and \\ in `search`; ESCAPE makes that effective.
            "AND (%(search)s::text IS NULL OR title ILIKE '%%' || %(search)s || '%%' ESCAPE '\\\\') "
            "ORDER BY release_date DESC LIMIT %(page_size)s OFFSET %(offset)s"
        ),
        "validation_rules": ["release_ordered", "stable_pagination"],
    },
    {
        # Audit H4: "terbaru" is the single most common shape of a public
        # question and had no template at all, forcing the runtime to invent a
        # metadata lookup. The period actually served is returned in the result
        # so the answer can state the year instead of the word "terbaru".
        "template_id": "dynamic_latest",
        "dataset_shape": "geography_series",
        "view_name": "bps_serving_dynamic",
        "has_own_limit": False,
        "parameter_schema": {
            "indicator_code": "text",
            "geography_code": "text|nullable",
            "category_item": "text|nullable",
        },
        "sql_template": (
            "SELECT indicator_code, period, geography_code, geography_name, "
            "category_code, category_label, "
            "period_granularity, subperiod_code, subperiod_label, "
            "value, unit, unit_state, snapshot_id "
            "FROM bps_serving_dynamic "
            "WHERE domain='1306' AND indicator_code=%(indicator_code)s "
            "AND period = ("
            "  SELECT max(period) FROM bps_serving_dynamic "
            "  WHERE domain='1306' AND indicator_code=%(indicator_code)s "
            "    AND value IS NOT NULL"
            ") "
            "AND (%(geography_code)s::text IS NULL OR geography_code=%(geography_code)s) "
            "AND (%(category_item)s::text IS NULL OR category_code=%(category_item)s) "
            "ORDER BY geography_code"
        ),
        "validation_rules": ["period_disclosed", "coverage", "value_lineage"],
    },
    {
        "template_id": "simdasi_latest",
        "dataset_shape": "geography_series",
        "view_name": "bps_serving_simdasi",
        "has_own_limit": False,
        "parameter_schema": {
            "table_code": "text",
            "indicator_code": "text",
            "geography_level": "text|nullable",
        },
        "sql_template": (
            "SELECT table_code, period, row_role, geography_code, geography_name, "
            "indicator_code, indicator_name, value, value_text, unit, unit_source, "
            "unit_state, snapshot_id "
            "FROM bps_serving_simdasi "
            "WHERE region_code='1306000' AND table_code=%(table_code)s "
            "AND indicator_code=%(indicator_code)s "
            "AND period = ("
            "  SELECT max(period) FROM bps_serving_simdasi "
            "  WHERE region_code='1306000' AND table_code=%(table_code)s "
            "    AND indicator_code=%(indicator_code)s AND value IS NOT NULL"
            ") "
            "AND (%(geography_level)s::text IS NULL OR geography_level=%(geography_level)s) "
            "ORDER BY geography_name"
        ),
        "validation_rules": ["period_disclosed", "coverage", "value_lineage"],
    },
]


def run_integrity_gates(
    connection: psycopg.Connection,
    template_rows: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    items: list[dict[str, Any]] | None = None,
) -> list[str]:
    errors: list[str] = []
    for template in template_rows:
        template_id = template.get("template_id", "?")
        sql = template.get("sql_template")
        if sql is None:
            errors.append(f"template {template_id} missing sql_template")
            sql = ""
        if template.get("view_name") not in ALLOWED_VIEWS:
            errors.append(f"template {template_id} uses non-allowlisted view {template.get('view_name')}")
        if ":" in (template.get("view_name") or ""):
            errors.append(f"template {template_id} view name must be unqualified")
        # Every declared parameter must actually be applied in the SQL, otherwise
        # the runtime silently ignores a filter the agent believes it applied.
        for name in template.get("parameter_schema", {}):
            if f"%({name})s" not in sql:
                errors.append(f"template {template_id} declares unused parameter {name!r}")
        # Audit H1: limit ownership is declared, never sniffed from the SQL text.
        if "has_own_limit" not in template:
            errors.append(f"template {template_id} must declare has_own_limit explicitly")
        else:
            sql_has_limit = re.search(r"\bLIMIT\b", sql, flags=re.IGNORECASE) is not None
            if bool(template["has_own_limit"]) != sql_has_limit:
                errors.append(
                    f"template {template_id} has_own_limit={template['has_own_limit']} "
                    f"but SQL {'contains' if sql_has_limit else 'does not contain'} LIMIT"
                )
    answerable_ids = {d["dataset_id"] for d in datasets if d["answerability"] == "answerable"}
    measure_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for measure in measures:
        measure_by_dataset.setdefault(measure["dataset_id"], []).append(measure)
    for dataset_id in answerable_ids:
        dataset_measures = measure_by_dataset.get(dataset_id, [])
        valid = [m for m in dataset_measures if m["unit_state"] in QUERYABLE_UNIT_STATES]
        if not valid:
            errors.append(f"answerable dataset {dataset_id} has no measure with known/unitless unit_state")
    # Hard invariant (audit C2): no measure may be queryable unless its unit is
    # sourced. A guessed unit is not a unit. This gate blocks the publish.
    for measure in measures:
        if measure.get("queryable") and measure["unit_state"] not in QUERYABLE_UNIT_STATES:
            errors.append(
                f"measure {measure['measure_id']} is queryable with unit_state "
                f"{measure['unit_state']!r}; unit must be sourced, never guessed"
            )
        if measure.get("queryable") and measure.get("unit_source") in HEURISTIC_UNIT_SOURCES:
            errors.append(
                f"measure {measure['measure_id']} is queryable with heuristic unit_source "
                f"{measure['unit_source']!r}; requires data-owner approval first"
            )
    item_ids_by_dimension: dict[str, set[str]] = {}
    for dimension in dimensions:
        item_ids_by_dimension.setdefault(dimension["dimension_id"], set())
    for item in items or []:
        item_ids_by_dimension.setdefault(item["dimension_id"], set()).add(item["item_id"])
    for dim in dimensions:
        if dim.get("total_item_id") and dim["total_item_id"] not in item_ids_by_dimension[dim["dimension_id"]]:
            errors.append(f"dimension {dim['dimension_id']} total_item_id points to unknown item")
    return errors


def _canonical_catalog(datasets: list[dict[str, Any]], templates: list[dict[str, Any]]) -> str:
    payload = {
        "datasets": sorted((d["source_family"], d["source_resource_id"], d["title"]) for d in datasets),
        "templates": sorted((t["template_id"], t["template_version"], t["view_name"]) for t in templates),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def build_registry(env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or POSTGRES_ENV
    dsn = load_postgres_dsn(env_path)
    version_id = _pid()
    with psycopg.connect(dsn) as connection:
        _apply_schema(connection)
        # Remove only abandoned draft snapshots. Published/retired catalogs are
        # immutable and remain queryable for candidate-set version pinning.
        draft_versions = [
            row[0]
            for row in connection.execute(
                "SELECT registry_version_id FROM bps_registry.registry_versions WHERE status='draft'"
            ).fetchall()
        ]
        for draft_version in draft_versions:
            connection.execute(
                "DELETE FROM bps_registry.query_template_registry WHERE registry_version_id=%s",
                (draft_version,),
            )
            connection.execute(
                "DELETE FROM bps_registry.dimension_item_registry WHERE registry_version_id=%s",
                (draft_version,),
            )
            connection.execute(
                "DELETE FROM bps_registry.dimension_registry WHERE registry_version_id=%s",
                (draft_version,),
            )
            connection.execute(
                "DELETE FROM bps_registry.measure_registry WHERE registry_version_id=%s",
                (draft_version,),
            )
            connection.execute(
                "DELETE FROM bps_registry.dataset_registry WHERE registry_version_id=%s",
                (draft_version,),
            )
            connection.execute(
                "DELETE FROM bps_registry.registry_versions WHERE registry_version_id=%s",
                (draft_version,),
            )
        # Geography is a global canonical reference (not a version snapshot).
        # Rebuild aliases deterministically to prevent NULL-unique duplicates.
        connection.execute("DELETE FROM bps_registry.geography_aliases")
        connection.execute(
            "INSERT INTO bps_registry.registry_versions (registry_version_id, checksum, status) VALUES (%s, 'pending', 'draft')",
            (version_id,),
        )
        geographies = _load_geography(connection)
        geography_aliases = _load_geography_aliases(connection, geographies)
        geography_by_name = {_norm(geo["name"]): geo["code"] for geo in geographies}

        dynamic = _build_dynamic(connection, geography_by_name)
        simdasi = _build_simdasi(connection)
        census = _build_census(connection)
        publications = _build_publications(connection)

        datasets = dynamic[0] + simdasi[0] + census[0] + publications[0]
        measures = dynamic[1] + simdasi[1] + census[1] + publications[1]
        dimensions = dynamic[2] + simdasi[2] + census[2]
        items = dynamic[3] + simdasi[3] + census[3]

        # Unit-review gate (audit C2c).
        #
        # Before: a dataset was blocked only when EVERY measure was unknown_review,
        # so a dataset with one good measure and six unit-less ones stayed fully
        # answerable and all seven were queryable.
        #
        # Now: the gate lives on the measure (`queryable`), the dataset is blocked
        # only when it has no queryable measure left, and any dataset that is
        # partially blocked is flagged so the review packet can pick it up.
        measures_by_dataset: dict[str, list[dict[str, Any]]] = {}
        for measure in measures:
            measures_by_dataset.setdefault(measure["dataset_id"], []).append(measure)
        for dataset in datasets:
            if dataset["answerability"] != "answerable":
                continue
            dataset_measures = measures_by_dataset.get(dataset["dataset_id"], [])
            if not dataset_measures:
                continue
            blocked = [m for m in dataset_measures if not m.get("queryable", True)]
            if not blocked:
                continue
            flags = sorted({flag for m in blocked for flag in m.get("quality_flags", [])})
            if len(blocked) == len(dataset_measures):
                dataset["answerability"] = "blocked_quality"
                dataset["quality_flags"] = flags
            else:
                # Dataset stays answerable through its good measures; the blocked
                # ones are refused individually by the runtime.
                dataset["quality_flags"] = sorted(
                    set(dataset.get("quality_flags", []))
                    | set(flags)
                    | {"partial_measure_review_required"}
                )

        template_rows = [
            {**template, "template_version": 1, "registry_version_id": version_id, "row_limit": 100, "timeout_ms": 5000, "result_schema_id": "normalized_query_result_v1"}
            for template in TEMPLATES
        ]

        errors = run_integrity_gates(connection, template_rows, datasets, measures, dimensions, items)
        if errors:
            connection.execute(
                "DELETE FROM bps_registry.registry_versions WHERE registry_version_id=%s",
                (version_id,),
            )
            raise ValueError(f"registry integrity gates failed: {'; '.join(errors)}")

        for geo in geographies:
            connection.execute(
                """
                INSERT INTO bps_registry.geography_registry (geography_id, code, name, level, sort_order)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, level=EXCLUDED.level, sort_order=EXCLUDED.sort_order
                """,
                (f"geo:{geo['code']}", geo["code"], geo["name"], geo["level"], geo["sort"]),
            )
        for alias in geography_aliases:
            connection.execute(
                """
                INSERT INTO bps_registry.geography_aliases
                    (alias_id, geography_id, source_family, source_code, source_label, match_type)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (geography_id, source_family, source_code, source_label) DO NOTHING
                """,
                (
                    f"ga:{uuid.uuid4().hex[:12]}",
                    alias["geography_id"],
                    alias["source_family"],
                    alias["source_code"],
                    alias["source_label"],
                    alias["match_type"],
                ),
            )
        for dataset in datasets:
            dataset["registry_version_id"] = version_id
            connection.execute(
                """
                INSERT INTO bps_registry.dataset_registry (
                    dataset_id, registry_version_id, source_family, source_resource_id,
                    title, summary, topic_name, dataset_shape, answerability,
                    period_granularity, period_min, period_max, period_latest,
                    search_document, supported_operations, quality_flags, active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
                """,
                (
                    dataset["dataset_id"], version_id, dataset["source_family"],
                    dataset["source_resource_id"], dataset["title"], dataset["summary"],
                    dataset.get("topic_name"), dataset["dataset_shape"], dataset["answerability"],
                    dataset["period_granularity"], dataset["period_min"], dataset["period_max"],
                    dataset["period_latest"], dataset["search_document"],
                    dataset["supported_operations"], dataset.get("quality_flags", []),
                ),
            )
        for measure in measures:
            connection.execute(
                """
                INSERT INTO bps_registry.measure_registry (
                    registry_version_id, measure_id, dataset_id, source_measure_id, name, value_type,
                    unit_state, unit_display, unit_source, decimal_places, aggregation_semantics,
                    queryable, quality_flags
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    version_id, measure["measure_id"], measure["dataset_id"], measure["source_measure_id"],
                    measure["name"], measure["value_type"], measure["unit_state"],
                    measure["unit_display"], measure.get("unit_source"), measure["decimal_places"],
                    measure["aggregation_semantics"],
                    measure.get("queryable", True), measure.get("quality_flags", []),
                ),
            )
        for dimension in dimensions:
            connection.execute(
                """
                INSERT INTO bps_registry.dimension_registry (
                    registry_version_id, dimension_id, dataset_id, name, role, required, total_item_id,
                    cardinality, display_order
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    version_id, dimension["dimension_id"], dimension["dataset_id"], dimension["name"],
                    dimension["role"], dimension["required"], dimension.get("total_item_id"),
                    dimension["cardinality"], dimension["display_order"],
                ),
            )
        for item in items:
            display_label, normalization_rule = _normalize_display_label(
                item["label"], fallback=item.get("source_item_code")
            )
            connection.execute(
                """
                INSERT INTO bps_registry.dimension_item_registry (
                    registry_version_id, item_id, dimension_id, source_item_id, source_item_code, label,
                    display_label, normalization_rule, label_raw,
                    aliases, canonical_geography_id, is_total, sort_order
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    version_id, item["item_id"], item["dimension_id"], item["source_item_id"],
                    item["source_item_code"], item["label"],
                    display_label, normalization_rule, item["label"],
                    [], item.get("canonical_geography_id"), item["is_total"], item["sort_order"],
                ),
            )
        for template in template_rows:
            connection.execute(
                """
                INSERT INTO bps_registry.query_template_registry (
                    template_id, template_version, registry_version_id, dataset_shape,
                    view_name, parameter_schema, sql_template, row_limit, timeout_ms,
                    result_schema_id, validation_rules
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    template["template_id"], template["template_version"], version_id,
                    template["dataset_shape"], template["view_name"],
                    json.dumps(template["parameter_schema"]), template["sql_template"],
                    template["row_limit"], template["timeout_ms"],
                    template["result_schema_id"], template["validation_rules"],
                ),
            )

        checksum = hashlib.sha256(_canonical_catalog(datasets, template_rows).encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE bps_registry.registry_versions SET status='retired' WHERE status='published'"
        )
        connection.execute(
            """
            UPDATE bps_registry.registry_versions
            SET status='published', checksum=%s, published_at=now()
            WHERE registry_version_id=%s
            """,
            (checksum, version_id),
        )
        connection.commit()
        counts = {
            "datasets": len(datasets),
            "measures": len(measures),
            "dimensions": len(dimensions),
            "items": len(items),
            "geographies": len(geographies),
            "aliases": len(geography_aliases),
            "templates": len(template_rows),
        }
        return {"version_id": version_id, "checksum": checksum, "counts": counts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=POSTGRES_ENV)
    args = parser.parse_args()
    report = build_registry(args.env)
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
