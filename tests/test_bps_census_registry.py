from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn

ADMIN_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


@pytest.fixture(scope="module")
def connection():
    with psycopg.connect(load_postgres_dsn(ADMIN_ENV)) as conn:
        yield conn


def test_census_answerable_datasets_have_typed_category_dimensions(connection) -> None:
    typed, zero = connection.execute(
        """
        SELECT count(*) FILTER (WHERE dim.cardinality > 0),
               count(*) FILTER (WHERE dim.cardinality = 0)
        FROM bps_registry.dataset_registry d
        JOIN bps_registry.dimension_registry dim USING (registry_version_id, dataset_id)
        JOIN bps_registry.registry_versions v USING (registry_version_id)
        WHERE v.status='published'
          AND d.source_family='census' AND d.answerability='answerable'
          AND dim.role='category'
        """
    ).fetchone()
    assert typed > 0, "no typed census category dimensions"
    assert zero == 0, f"{zero} census category dimensions still have cardinality 0"


def test_census_category_items_populated_and_consistent(connection) -> None:
    rows = connection.execute(
        """
        SELECT dim.dimension_id, dim.cardinality, count(item.item_id), dim.total_item_id
        FROM bps_registry.dimension_registry dim
        JOIN bps_registry.registry_versions v ON v.registry_version_id=dim.registry_version_id
        LEFT JOIN bps_registry.dimension_item_registry item
               ON item.registry_version_id=dim.registry_version_id
              AND item.dimension_id=dim.dimension_id
        WHERE v.status='published' AND dim.dataset_id='ds:census:sp2010:10'
        GROUP BY 1,2,4 ORDER BY 1
        """
    ).fetchall()
    assert len(rows) == 2, rows
    for dimension_id, cardinality, item_count, total_item_id in rows:
        assert cardinality == item_count, (dimension_id, cardinality, item_count)
        assert total_item_id is not None, dimension_id


def test_sp2010_10_explicit_total_items_marked(connection) -> None:
    rows = connection.execute(
        """
        SELECT dim.name, item.source_item_code, item.label, item.is_total
        FROM bps_registry.dimension_item_registry item
        JOIN bps_registry.dimension_registry dim
          ON dim.registry_version_id=item.registry_version_id
         AND dim.dimension_id=item.dimension_id
        JOIN bps_registry.registry_versions v ON v.registry_version_id=dim.registry_version_id
        WHERE v.status='published' AND dim.dataset_id='ds:census:sp2010:10'
        ORDER BY dim.name, item.sort_order
        """
    ).fetchall()
    by_axis: dict[str, list[tuple[str, str]]] = {}
    for name, code, label, _is_total in rows:
        by_axis.setdefault(name.strip(), []).append((code, label))
    assert set(by_axis) == {
        "Klasifikasi Jenis Kelamin",
        "Klasifikasi Perkotaan dan Perdesaan",
    }, by_axis
    totals = [label for codes in by_axis.values() for code, label in codes if code == "999"]
    assert len(totals) == 2, totals
    assert all(label == "Total" for label in totals)
