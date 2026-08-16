from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


@pytest.fixture(scope="module")
def connection():
    with psycopg.connect(load_postgres_dsn(POSTGRES_ENV)) as conn:
        yield conn


def test_dynamic_serving_view_exposes_subperiod(connection) -> None:
    rows = connection.execute(
        """
        SELECT DISTINCT period_granularity, subperiod_label
        FROM bps_serving_dynamic
        WHERE domain='1306' AND indicator_code='398' AND period='2025'
        """
    ).fetchall()
    assert rows, "quarterly variable must be present"
    granularity = {row[0] for row in rows}
    subperiods = {row[1] for row in rows}
    assert "quarterly" in granularity
    assert {"Triwulan I", "Triwulan II", "Triwulan III", "Triwulan IV", "Tahunan"} <= subperiods


def test_dynamic_serving_view_has_no_visible_duplicate_keys(connection) -> None:
    duplicates = connection.execute(
        """
        SELECT count(*)
        FROM (
            SELECT domain, indicator_code, geography_code, geography_name, period,
                   category, coalesce(subperiod_label,'') AS subperiod
            FROM bps_serving_dynamic
            GROUP BY 1,2,3,4,5,6,7
            HAVING count(*) > 1
        ) dup
        """
    ).fetchone()[0]
    assert duplicates == 0


def test_simdasi_category_rows_have_row_role_not_kecamatan(connection) -> None:
    mislabeled = connection.execute(
        """
        SELECT count(*)
        FROM bps_serving_simdasi
        WHERE region_code='1306000'
          AND geography_code IS NULL
          AND geography_level='kecamatan'
        """
    ).fetchone()[0]
    assert mislabeled == 0
    role_counts = connection.execute(
        """
        SELECT row_role, count(*)
        FROM bps_serving_simdasi
        WHERE region_code='1306000'
        GROUP BY row_role
        ORDER BY row_role
        """
    ).fetchall()
    roles = dict(role_counts)
    assert roles["category"] > 0
    assert roles["kecamatan"] > 0
    assert roles["kabupaten"] > 0


def test_simdasi_pdrb_unit_derived_from_title(connection) -> None:
    units = connection.execute(
        """
        SELECT DISTINCT unit
        FROM bps_serving_simdasi
        WHERE region_code='1306000' AND table_code IN ('12.1','12.2')
        """
    ).fetchall()
    assert {row[0] for row in units} == {"miliar rupiah"}
