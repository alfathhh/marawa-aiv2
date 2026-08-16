from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_bps_registry import (  # noqa: E402
    ALLOWED_VIEWS,
    build_registry,
    run_integrity_gates,
)
from workers.ingestion.bps_storage import load_postgres_dsn

POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


@pytest.fixture(scope="module")
def connection():
    with psycopg.connect(load_postgres_dsn(POSTGRES_ENV)) as conn:
        yield conn


def _fetch_all(connection, sql: str) -> list[tuple]:
    return connection.execute(sql).fetchall()


def test_registry_schema_applied(connection) -> None:
    tables = {row[0] for row in _fetch_all(
        connection,
        "SELECT tablename FROM pg_tables WHERE schemaname='bps_registry'",
    )}
    assert {
        "registry_versions",
        "dataset_registry",
        "measure_registry",
        "dimension_registry",
        "dimension_item_registry",
        "geography_registry",
        "geography_aliases",
        "query_template_registry",
        "candidate_sets",
    } <= tables


def test_build_publishes_exactly_one_version(connection) -> None:
    report = build_registry(POSTGRES_ENV)
    assert report["version_id"]
    assert report["checksum"]
    versions = _fetch_all(
        connection,
        "SELECT count(*) FROM bps_registry.registry_versions WHERE status='published'",
    )
    assert versions[0][0] == 1


def test_dataset_counts_match_known_catalog(connection) -> None:
    counts = {
        (row[0], row[1]): row[2]
        for row in _fetch_all(
            connection,
            """
            SELECT d.source_family, d.answerability, count(*)
            FROM bps_registry.dataset_registry d
            JOIN bps_registry.registry_versions v USING (registry_version_id)
            WHERE d.active AND v.status='published'
            GROUP BY 1,2 ORDER BY 1,2
            """,
        )
    }
    assert counts[("dynamic", "answerable")] >= 300
    assert counts[("dynamic", "blocked_quality")] >= 5
    assert counts[("simdasi", "answerable")] + counts[("simdasi", "blocked_quality")] == 47
    assert counts[("census", "answerable")] >= 91
    assert counts[("census", "metadata_only")] >= 70
    assert counts[("publication", "answerable")] == 602


def test_geography_registry_has_18_canonical_rows(connection) -> None:
    rows = _fetch_all(
        connection,
        "SELECT level, count(*) FROM bps_registry.geography_registry GROUP BY level",
    )
    levels = dict(rows)
    assert levels["kabupaten"] == 1
    assert levels["kecamatan"] == 17


def test_geography_aliases_cover_cross_family_labels(connection) -> None:
    aliases = _fetch_all(
        connection,
        """
        SELECT g.name, a.source_family, a.source_label
        FROM bps_registry.geography_aliases a
        JOIN bps_registry.geography_registry g USING (geography_id)
        WHERE g.name IN ('Lubuak Aluang','Koto Patamuan','Padang Pariaman')
        """,
    )
    labels = {(row[0], row[1], row[2]) for row in aliases}
    assert ("Lubuak Aluang", "census", "LUBUK ALUNG") in labels
    assert ("Koto Patamuan", "census", "PATAMUAN") in labels
    assert ("Koto Patamuan", "simdasi", "VII Koto Patamuan") in labels
    assert ("Padang Pariaman", "census", "PADANG PARIAMAN") in labels or any(
        name == "Padang Pariaman" for name, _, _ in labels
    )


def test_unit_state_classification(connection) -> None:
    rows = _fetch_all(
        connection,
        """
        SELECT m.unit_state
        FROM bps_registry.measure_registry m
        JOIN bps_registry.dataset_registry d USING (dataset_id)
        WHERE d.source_family='dynamic' AND d.source_resource_id='161'
        """,
    )
    assert rows[0][0] == "unknown_review"

    population = _fetch_all(
        connection,
        """
        SELECT m.unit_state
        FROM bps_registry.measure_registry m
        JOIN bps_registry.dataset_registry d USING (dataset_id)
        WHERE d.source_family='dynamic' AND d.source_resource_id='29'
        """,
    )
    assert population[0][0] == "known"


def test_dimension_roles_are_deterministic(connection) -> None:
    roles = dict(_fetch_all(
        connection,
        """
        SELECT d.source_resource_id, dim.role
        FROM bps_registry.dimension_registry dim
        JOIN bps_registry.dataset_registry d USING (dataset_id)
        WHERE d.source_family='dynamic'
          AND d.source_resource_id IN ('29','188')
          AND dim.name='primary'
        """,
    ))
    assert roles["29"] == "geography"
    assert roles["188"] == "category"


def test_integrity_gates_reject_unknown_view(connection) -> None:
    fake_rows = [
        {"template_id": "bad", "view_name": "pg_catalog.pg_class", "dataset_shape": "geography_series"},
    ]
    errors = run_integrity_gates(connection, fake_rows, datasets=[], measures=[], dimensions=[])
    assert any("view" in error.lower() for error in errors)


def test_all_templates_use_allowlisted_views(connection) -> None:
    views = {row[0] for row in _fetch_all(
        connection,
        "SELECT DISTINCT view_name FROM bps_registry.query_template_registry",
    )}
    assert views and views <= ALLOWED_VIEWS
