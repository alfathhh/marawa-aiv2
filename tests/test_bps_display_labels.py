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


def test_no_registry_item_display_label_contains_markup(connection) -> None:
    dirty, nulls = connection.execute(
        """
        SELECT count(*) FILTER (WHERE display_label ~ '<[^>]*>'),
               count(*) FILTER (WHERE display_label IS NULL OR display_label='')
        FROM bps_registry.dimension_item_registry i
        JOIN bps_registry.registry_versions v USING (registry_version_id)
        WHERE v.status='published'
        """
    ).fetchone()
    assert dirty == 0, f"{dirty} published items still contain HTML in display_label"
    assert nulls == 0, f"{nulls} published items have empty display_label"


def test_raw_label_lineage_preserved_for_normalized_items(connection) -> None:
    normalized, raw_kept = connection.execute(
        """
        SELECT count(*) FILTER (WHERE normalization_rule='strip_html_unescape'),
               count(*) FILTER (
                   WHERE normalization_rule='strip_html_unescape'
                     AND label_raw=label
               )
        FROM bps_registry.dimension_item_registry i
        JOIN bps_registry.registry_versions v USING (registry_version_id)
        WHERE v.status='published'
        """
    ).fetchone()
    assert normalized > 0, "expected at least one normalized item"
    assert raw_kept == normalized, "label_raw must preserve the source label for normalized items"
