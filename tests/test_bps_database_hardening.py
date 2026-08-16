from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn

ADMIN_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
RUNTIME_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres-runtime.env")


@pytest.fixture(scope="module")
def admin_connection():
    with psycopg.connect(load_postgres_dsn(ADMIN_ENV)) as connection:
        yield connection


@pytest.fixture(scope="module")
def runtime_connection():
    assert RUNTIME_ENV.exists(), "runtime DB credential env must exist"
    with psycopg.connect(load_postgres_dsn(RUNTIME_ENV)) as connection:
        yield connection


def test_geography_registry_has_18_unique_canonical_rows(admin_connection) -> None:
    rows = admin_connection.execute(
        """
        SELECT code, name, level
        FROM bps_registry.geography_registry
        ORDER BY code
        """
    ).fetchall()
    assert len(rows) == 18, rows
    by_code = {code: name for code, name, _level in rows}
    assert len(by_code) == 18, "duplicate codes not allowed"
    assert by_code["1306000"] == "Padang Pariaman"
    assert by_code["1306061"] == "Koto Patamuan"
    assert by_code["1306071"] == "V Koto Timur", by_code["1306071"]
    assert by_code["1306060"] == "VII Koto Sungai Sarik", by_code["1306060"]
    assert by_code["1306070"] == "V Koto Kampung Dalam", by_code["1306070"]


def test_runtime_role_is_non_privileged_and_read_only(admin_connection) -> None:
    row = admin_connection.execute(
        """
        SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
               coalesce(array_to_string(rolconfig, ','), '')
        FROM pg_roles WHERE rolname='marawa_runtime_ro'
        """
    ).fetchone()
    assert row is not None
    can_login, superuser, create_role, create_db, config = row
    assert can_login is True
    assert superuser is False
    assert create_role is False
    assert create_db is False
    assert "default_transaction_read_only=on" in config
    assert "statement_timeout=" in config


def test_view_rebootstrap_preserves_runtime_grants(admin_connection) -> None:
    """ensure_schema recreates serving views; runtime grants must survive."""
    dsn = load_postgres_dsn(ADMIN_ENV)
    store = BpsStore(dsn)
    store.ensure_schema()
    with psycopg.connect(load_postgres_dsn(RUNTIME_ENV)) as runtime_connection:
        assert runtime_connection.execute(
            "SELECT count(*) FROM bps_serving_dynamic"
        ).fetchone()[0] > 0
        assert runtime_connection.execute(
            "SELECT count(*) FROM bps_serving_simdasi"
        ).fetchone()[0] > 0


def test_runtime_role_can_read_only_approved_objects(runtime_connection) -> None:
    assert runtime_connection.execute("SELECT count(*) FROM bps_serving_dynamic").fetchone()[0] > 0
    assert runtime_connection.execute("SELECT count(*) FROM bps_serving_simdasi").fetchone()[0] > 0
    assert runtime_connection.execute("SELECT count(*) FROM bps_serving_census").fetchone()[0] > 0
    assert runtime_connection.execute("SELECT count(*) FROM bps_publications").fetchone()[0] == 602
    assert runtime_connection.execute(
        "SELECT count(*) FROM bps_registry.dataset_registry"
    ).fetchone()[0] > 0


def test_runtime_role_cannot_read_raw_or_secret_adjacent_tables(runtime_connection) -> None:
    for relation in (
        "bps_raw_snapshots",
        "bps_dynamic_facts",
        "bps_simdasi_facts",
        "bps_census_facts",
        "bps_ingestion_runs",
    ):
        runtime_connection.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_connection.execute(f"SELECT count(*) FROM {relation}").fetchone()
    runtime_connection.rollback()


def test_runtime_role_denies_mutation_and_ddl(runtime_connection) -> None:
    statements = (
        "INSERT INTO bps_registry.candidate_sets "
        "(candidate_set_id, conversation_id, registry_version_id, expires_at) "
        "VALUES ('forbidden-test','forbidden-test','forbidden-test',now())",
        "UPDATE bps_registry.dataset_registry SET active=false WHERE false",
        "DELETE FROM bps_registry.dataset_registry WHERE false",
        "CREATE TABLE public.forbidden_runtime_table(id integer)",
    )
    for statement in statements:
        runtime_connection.rollback()
        with pytest.raises(
            (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)
        ):
            runtime_connection.execute(statement)
    runtime_connection.rollback()


def test_blocked_quality_datasets_have_explicit_reason(admin_connection) -> None:
    blocked, flagged = admin_connection.execute(
        """
        SELECT count(*), count(*) FILTER (
            WHERE d.quality_flags @> ARRAY['unit_review_required']::text[]
        )
        FROM bps_registry.dataset_registry d
        JOIN bps_registry.registry_versions v USING (registry_version_id)
        WHERE v.status='published' AND d.answerability='blocked_quality'
        """
    ).fetchone()
    assert blocked > 0
    assert flagged == blocked


def test_retired_registry_version_catalog_remains_queryable(admin_connection) -> None:
    retired = admin_connection.execute(
        """
        SELECT registry_version_id FROM bps_registry.registry_versions
        WHERE status='retired' ORDER BY published_at DESC NULLS LAST LIMIT 1
        """
    ).fetchone()
    assert retired is not None
    count = admin_connection.execute(
        """
        SELECT count(*) FROM bps_registry.dataset_registry
        WHERE registry_version_id=%s
        """,
        (retired[0],),
    ).fetchone()[0]
    assert count > 0, "retired registry catalog snapshot must be retained"
