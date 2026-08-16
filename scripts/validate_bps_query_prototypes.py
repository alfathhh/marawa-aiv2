#!/usr/bin/env python3
"""Run read-only BPS query prototypes and assert live semantic invariants.

This is a planning/query-lab artifact, not a runtime API. It never changes schema/data.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn
POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
DEFAULT_OUTPUT = ROOT / "data" / "reports" / "bps-query-prototype-validation.json"
Validator = Callable[[list[tuple[Any, ...]]], dict[str, Any]]


@dataclass(frozen=True)
class QueryCase:
    case_id: str
    purpose: str
    sql: str
    validate: Validator


def _number(value: Any) -> float:
    return float(value) if isinstance(value, Decimal) else value


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_read_only_sql(sql: str) -> None:
    cleaned = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip()
    without_trailing = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    if ";" in without_trailing:
        raise ValueError("multiple SQL statements are forbidden")
    if not re.match(r"^(select|with)\b", without_trailing, re.I):
        raise ValueError("query prototype must start with SELECT or WITH")
    forbidden = re.compile(
        r"\b(insert|update|delete|merge|alter|drop|truncate|create|grant|revoke|copy|call|do|vacuum|analyze|refresh|reindex|cluster|comment)\b",
        re.I,
    )
    match = forbidden.search(without_trailing)
    if match:
        raise ValueError(f"mutating/administrative SQL forbidden: {match.group(1)}")


def exact_total(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    _assert(len(rows) == 1, f"expected one row, got {len(rows)}")
    value, unit, snapshot_id = rows[0]
    _assert(value == 467038 and unit == "jiwa", f"unexpected total/unit: {value} {unit}")
    return {"value": _number(value), "unit": unit, "snapshot_id": snapshot_id}


def breakdown(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    count_rows, numeric_rows, distinct_names, component_sum = rows[0]
    _assert((count_rows, numeric_rows, distinct_names) == (17, 17, 17), str(rows[0]))
    _assert(component_sum == 467038, f"subdistrict sum={component_sum}")
    return {"coverage": 17, "component_sum": _number(component_sum)}


def trend_compare(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    _assert([row[0] for row in rows] == ["2020", "2021", "2022", "2023", "2024", "2025"], str(rows))
    values = {period: value for period, value in rows}
    delta = values["2025"] - values["2024"]
    pct = Decimal(100) * delta / values["2024"]
    _assert(delta == 7869, f"delta={delta}")
    _assert(abs(pct - Decimal("1.7137")) < Decimal("0.0001"), f"pct={pct}")
    return {"periods": 6, "absolute_change": _number(delta), "percent_change": round(float(pct), 4)}


def composition(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    values = {label: value for label, value in rows}
    _assert(values.get("Jumlah") == 207131, str(values))
    components = sum(value for label, value in rows if label != "Jumlah")
    _assert(components == values["Jumlah"], f"components={components}, total={values['Jumlah']}")
    return {"components": len(rows) - 1, "total": _number(values["Jumlah"])}


def quarterly(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    values = {label: value for label, value in rows}
    required = {"Triwulan I", "Triwulan II", "Triwulan III", "Triwulan IV", "Tahunan"}
    _assert(set(values) == required, str(values))
    quarter_sum = sum(value for label, value in rows if label.startswith("Triwulan"))
    _assert(abs(quarter_sum - values["Tahunan"]) <= Decimal("0.01"), f"quarter_sum={quarter_sum}")
    return {"subperiods": 5, "annual": _number(values["Tahunan"]), "quarter_sum": _number(quarter_sum)}


def simdasi_total(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    _assert(len(rows) == 1 and rows[0][0] == Decimal("467") and rows[0][1] == "ribu jiwa", str(rows))
    return {"value": 467, "unit": "ribu jiwa", "snapshot_id": rows[0][2]}


def rounded_coverage(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    coverage, component_sum, explicit_total = rows[0]
    _assert(coverage == 17, f"coverage={coverage}")
    difference = abs(component_sum - explicit_total)
    _assert(difference <= Decimal("0.85"), f"rounding difference={difference}")
    return {
        "coverage": coverage,
        "component_sum": _number(component_sum),
        "explicit_total": _number(explicit_total),
        "rounding_difference": _number(difference),
        "tolerance": 0.85,
    }


def markers(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    found = {code: (count, nulls) for code, count, nulls in rows}
    for code in ("–", "...", "NA"):
        _assert(code in found and found[code][0] == found[code][1], f"marker not preserved: {code}={found.get(code)}")
    return {code: {"facts": count, "numeric_null": nulls} for code, (count, nulls) in found.items()}


def census_cross_tab(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    _assert(len(rows) == 1, str(rows))
    grand_total, gender_sum, urban_rural_sum, subdistrict_sum, subdistricts = rows[0]
    _assert(grand_total == gender_sum == urban_rural_sum == subdistrict_sum == 391056, str(rows[0]))
    _assert(subdistricts == 17, f"subdistricts={subdistricts}")
    return {"grand_total": _number(grand_total), "subdistricts": subdistricts, "all_margins_equal": True}


def publication_pages(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    _assert(len(rows) == 6, f"rows={len(rows)}")
    refs = [row[0] for row in rows]
    ids = [row[1] for row in rows]
    _assert(refs == ["P1", "P2", "P3", "P4", "P5", "P6"], str(refs))
    _assert(len(ids) == len(set(ids)), "duplicate publication id across pages")
    return {"page_size": 3, "refs": refs, "unique_ids": len(ids)}


def answerability(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    result = {family: (total, answerable) for family, total, answerable in rows}
    _assert(result["dynamic"] == (335, 334), str(result))
    _assert(result["simdasi"] == (47, 47), str(result))
    _assert(result["census_sp2010"] == (69, 65), str(result))
    _assert(result["census_sp2020"] == (5, 0), str(result))
    _assert(result["census_sp2022"] == (65, 0), str(result))
    _assert(result["census_st2023"] == (26, 26), str(result))
    return {family: {"catalog": total, "answerable": answerable} for family, (total, answerable) in result.items()}


def gaps(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    result = {name: value for name, value in rows}
    _assert(result["simdasi_category_cells_mislabeled_kecamatan"] == 0, str(result))
    _assert(result["dynamic_visible_duplicate_keys"] == 0, str(result))
    _assert(result["dynamic_quarterly_facts"] == 918, str(result))
    _assert(result["simdasi_pdrb_wrong_unit_rows"] == 0, str(result))
    return result


def source_precision(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    dynamic, simdasi_normalized, difference, relative_pct, same_at_precision = rows[0]
    _assert(dynamic == 467038 and simdasi_normalized == 467000, str(rows[0]))
    _assert(difference == 38 and same_at_precision, str(rows[0]))
    return {
        "dynamic_jiwa": _number(dynamic),
        "simdasi_normalized_jiwa": _number(simdasi_normalized),
        "difference": _number(difference),
        "relative_percent": _number(relative_pct),
        "equivalent_at_simdasi_precision": bool(same_at_precision),
    }


CASES = [
    QueryCase(
        "dynamic_exact_total",
        "Exact point lookup with explicit total category",
        """SELECT value,unit,snapshot_id FROM bps_serving_dynamic
           WHERE domain='1306' AND indicator_code='29' AND period='2025'
             AND geography_name='Kabupaten Padang Pariaman' AND category_label='Total'""",
        exact_total,
    ),
    QueryCase(
        "dynamic_breakdown_coverage",
        "Per-kecamatan breakdown and coverage invariant",
        """SELECT count(*),count(value),count(DISTINCT geography_name),sum(value)
           FROM bps_serving_dynamic WHERE domain='1306' AND indicator_code='29' AND period='2025'
             AND category_label='Total' AND geography_name<>'Kabupaten Padang Pariaman'""",
        breakdown,
    ),
    QueryCase(
        "dynamic_trend_compare",
        "Ordered trend and deterministic two-period comparison",
        """SELECT period,value FROM bps_serving_dynamic
           WHERE domain='1306' AND indicator_code='29' AND geography_name='Kabupaten Padang Pariaman'
             AND category_label='Total' AND period BETWEEN '2020' AND '2025' ORDER BY period""",
        trend_compare,
    ),
    QueryCase(
        "dynamic_composition",
        "Category composition with explicit total validation",
        """SELECT vertical_label,value_numeric FROM bps_dynamic_facts
           WHERE domain='1306' AND variable_id='282' AND period_label='2023'
           ORDER BY vertical_id::int""",
        composition,
    ),
    QueryCase(
        "dynamic_quarterly",
        "Quarterly fact query preserving subperiod",
        """SELECT derived_period_label,value_numeric FROM bps_dynamic_facts
           WHERE domain='1306' AND variable_id='398' AND period_label='2025'
             AND vertical_label='Produk Domestik Regional Bruto' ORDER BY derived_period_id""",
        quarterly,
    ),
    QueryCase(
        "simdasi_exact_total",
        "SIMDASI point lookup with inherited unit",
        """SELECT value,unit,snapshot_id FROM bps_serving_simdasi
           WHERE region_code='1306000' AND table_code='3.1.1' AND period=2025
             AND indicator_name='Jumlah Penduduk' AND geography_level='kabupaten'""",
        simdasi_total,
    ),
    QueryCase(
        "simdasi_rounded_coverage",
        "Rounded subdistrict components use precision-aware tolerance",
        """SELECT count(*) FILTER(WHERE geography_level='kecamatan'),
                  sum(value) FILTER(WHERE geography_level='kecamatan'),
                  max(value) FILTER(WHERE geography_level='kabupaten')
           FROM bps_serving_simdasi WHERE region_code='1306000' AND table_code='3.1.1'
             AND period=2025 AND indicator_name='Jumlah Penduduk'""",
        rounded_coverage,
    ),
    QueryCase(
        "simdasi_marker_preserved",
        "Unavailable/zero-combined markers remain non-numeric",
        """SELECT value_code,count(*),count(*) FILTER(WHERE value IS NULL)
           FROM bps_serving_simdasi WHERE value_code IN ('–','...','NA')
           GROUP BY value_code ORDER BY value_code""",
        markers,
    ),
    QueryCase(
        "census_cross_tab",
        "SP2010 cross-tab totals across both dimensions and geography",
        """WITH kab AS (
               SELECT value_numeric,categories FROM bps_census_facts
               WHERE event_id='sp2010' AND dataset_id='10' AND geography_code='1306'
           ), sub AS (
               SELECT value_numeric FROM bps_census_facts
               WHERE event_id='sp2010' AND dataset_id='10' AND geography_level=3
                 AND categories @> '[{"category_id":"1027","item_code":"999"},{"category_id":"1026","item_code":"999"}]'::jsonb
           )
           SELECT
             max(value_numeric) FILTER(WHERE categories @> '[{"category_id":"1027","item_code":"999"},{"category_id":"1026","item_code":"999"}]'::jsonb),
             sum(value_numeric) FILTER(WHERE categories @> '[{"category_id":"1027","item_code":"999"}]'::jsonb AND NOT categories @> '[{"category_id":"1026","item_code":"999"}]'::jsonb),
             sum(value_numeric) FILTER(WHERE categories @> '[{"category_id":"1026","item_code":"999"}]'::jsonb AND NOT categories @> '[{"category_id":"1027","item_code":"999"}]'::jsonb),
             (SELECT sum(value_numeric) FROM sub),(SELECT count(*) FROM sub)
           FROM kab""",
        census_cross_tab,
    ),
    QueryCase(
        "publication_pagination",
        "Stable page refs and unique publication IDs",
        """WITH q AS (SELECT plainto_tsquery('simple','penduduk') query), p AS (
             SELECT publication_id,title,release_date,
                    ts_rank_cd(to_tsvector('simple',coalesce(title,'')||' '||coalesce(abstract,'')),q.query) score
             FROM bps_publications CROSS JOIN q WHERE domain='1306'
               AND to_tsvector('simple',coalesce(title,'')||' '||coalesce(abstract,'')) @@ q.query
           ), r AS (
             SELECT *,row_number() OVER(ORDER BY score DESC,release_date DESC NULLS LAST,publication_id) rn FROM p
           ) SELECT 'P'||rn,publication_id,title,release_date FROM r WHERE rn<=6 ORDER BY rn""",
        publication_pages,
    ),
    QueryCase(
        "candidate_answerability",
        "Metadata-only resources are not offered as queryable candidates",
        """SELECT 'dynamic',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_dynamic_facts f WHERE f.domain=v.domain AND f.variable_id=v.variable_id)) FROM bps_dynamic_variables v WHERE domain='1306'
           UNION ALL SELECT 'simdasi',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_simdasi_facts f WHERE f.region_code=t.region_code AND f.table_id=t.table_id)) FROM bps_simdasi_tables t WHERE region_code='1306000'
           UNION ALL SELECT 'census_sp2010',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_census_facts f WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)) FROM bps_census_datasets d WHERE event_id='sp2010'
           UNION ALL SELECT 'census_sp2020',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_census_facts f WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)) FROM bps_census_datasets d WHERE event_id='sp2020'
           UNION ALL SELECT 'census_sp2022',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_census_facts f WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)) FROM bps_census_datasets d WHERE event_id='sp2022'
           UNION ALL SELECT 'census_st2023',count(*),count(*) FILTER(WHERE EXISTS (SELECT 1 FROM bps_census_facts f WHERE f.event_id=d.event_id AND f.dataset_id=d.dataset_id)) FROM bps_census_datasets d WHERE event_id='st2023'""",
        answerability,
    ),
    QueryCase(
        "serving_gap_sentinels",
        "Serving view semantics are fixed; these counts must stay zero (regression guard)",
        """WITH role AS (
             SELECT table_code,bool_or(geography_code<>'' AND geography_code<>region_code) has_geo
             FROM bps_serving_simdasi GROUP BY table_code
           ), gaps AS (
             SELECT 'simdasi_category_cells_mislabeled_kecamatan' name,count(*)::bigint value
             FROM bps_serving_simdasi s JOIN role r USING(table_code) WHERE NOT r.has_geo AND s.geography_level='kecamatan'
             UNION ALL SELECT 'dynamic_visible_duplicate_keys',count(*) FROM (
               SELECT indicator_code,period,geography_code,coalesce(category_code,''),coalesce(subperiod_code,'')
               FROM bps_serving_dynamic
               GROUP BY 1,2,3,4,5 HAVING count(*)>1
             ) x
             UNION ALL SELECT 'dynamic_quarterly_facts',count(*) FROM bps_dynamic_facts
               WHERE domain='1306' AND derived_period_label IN ('Triwulan I','Triwulan II','Triwulan III','Triwulan IV')
             UNION ALL SELECT 'simdasi_pdrb_wrong_unit_rows',count(*) FROM bps_serving_simdasi
               WHERE table_code IN ('12.1','12.2') AND unit='Rp'
           ) SELECT name,value FROM gaps ORDER BY name""",
        gaps,
    ),
    QueryCase(
        "source_precision_comparability",
        "Dynamic exact value and rounded SIMDASI value are comparable",
        """WITH d AS (SELECT value FROM bps_serving_dynamic WHERE domain='1306' AND indicator_code='29' AND period='2025' AND geography_name='Kabupaten Padang Pariaman' AND category_label='Total'),
           s AS (SELECT value*1000 value FROM bps_serving_simdasi WHERE region_code='1306000' AND table_code='3.1.1' AND period=2025 AND indicator_name='Jumlah Penduduk' AND geography_level='kabupaten')
           SELECT d.value,s.value,d.value-s.value,round(100*abs(d.value-s.value)/d.value,6),round(d.value/1000,1)=s.value/1000 FROM d,s""",
        source_precision,
    ),
]


def run(output: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        connection.autocommit = False
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '10s'")
            for case in CASES:
                validate_read_only_sql(case.sql)
                try:
                    rows = connection.execute(case.sql).fetchall()
                    evidence = case.validate(rows)
                    results.append({"case_id": case.case_id, "status": "pass", "purpose": case.purpose, "evidence": evidence})
                except Exception as error:
                    results.append({"case_id": case.case_id, "status": "fail", "purpose": case.purpose, "error": f"{type(error).__name__}: {error}"})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "planning_query_lab_read_only",
        "transaction_read_only": True,
        "statement_timeout": "10s",
        "cases": results,
        "summary": {
            "total": len(results),
            "passed": sum(item["status"] == "pass" for item in results),
            "failed": sum(item["status"] == "fail" for item in results),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
