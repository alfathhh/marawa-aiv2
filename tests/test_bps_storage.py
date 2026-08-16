from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn


POSTGRES_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


def cleanup_test_rows() -> None:
    dsn = load_postgres_dsn(POSTGRES_ENV)
    with psycopg.connect(dsn) as connection:
        connection.execute("DELETE FROM bps_dynamic_facts WHERE domain='test'")
        connection.execute("DELETE FROM bps_dynamic_variables WHERE domain='test'")
        connection.execute("DELETE FROM bps_publications WHERE domain='test'")
        connection.execute("DELETE FROM bps_glossary WHERE glossary_id LIKE 'fixture-%'")
        connection.execute("DELETE FROM bps_ingestion_checkpoints WHERE checkpoint_key LIKE 'test:%'")
        connection.execute(
            "DELETE FROM bps_raw_snapshots WHERE run_id IN (SELECT id FROM bps_ingestion_runs WHERE config->>'fixture'='true')"
        )
        connection.execute("DELETE FROM bps_ingestion_runs WHERE config->>'fixture'='true'")


@pytest.fixture(autouse=True)
def isolated_storage_fixtures() -> None:
    cleanup_test_rows()
    yield
    cleanup_test_rows()


def test_load_postgres_dsn_uses_external_secret_file() -> None:
    dsn = load_postgres_dsn(POSTGRES_ENV)

    assert "127.0.0.1" in dsn
    assert "55432" in dsn
    assert "marawa_bps" in dsn
    assert "POSTGRES_PASSWORD" not in dsn


def test_schema_and_snapshot_deduplication() -> None:
    store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
    store.ensure_schema()
    run_id = store.start_run("test", {"fixture": True})
    request = {
        "url": "https://webapi.bps.go.id/v1/api/list",
        "params": {"domain": "test", "model": "var"},
        "request_fingerprint": "sha256:test-snapshot-fixture",
    }
    payload = {"status": "OK", "data": [{"page": 1}, []]}

    first = store.record_snapshot(run_id, "test", "fixture", request, payload)
    second = store.record_snapshot(run_id, "test", "fixture", request, payload)
    store.finish_run(run_id, "completed", {"snapshots": 1})

    assert first == second

    with psycopg.connect(store.dsn) as connection:
        observations = connection.execute(
            "SELECT count(*) FROM bps_snapshot_observations WHERE run_id=%s AND snapshot_id=%s",
            (run_id, first),
        ).fetchone()[0]
    assert observations == 1
    with psycopg.connect(store.dsn) as connection:
        count = connection.execute(
            "SELECT count(*) FROM bps_raw_snapshots WHERE request_fingerprint = %s",
            ("sha256:test-snapshot-fixture",),
        ).fetchone()[0]
    assert count == 1


def test_publication_upsert_is_idempotent_and_tracks_snapshot() -> None:
    store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
    store.ensure_schema()
    row = {
        "domain": "test",
        "publication_id": "pub-fixture",
        "title": "Initial title",
        "issn": None,
        "catalog_number": None,
        "publication_number": None,
        "abstract": None,
        "scheduled_date": None,
        "release_date": "2026-01-01",
        "updated_date": None,
        "cover_url": None,
        "pdf_url": "https://example.invalid/pub.pdf",
        "declared_size": "1 MB",
        "raw": {"pub_id": "pub-fixture"},
    }

    store.upsert_publications([row], snapshot_id=None)
    row["title"] = "Updated title"
    store.upsert_publications([row], snapshot_id=None)

    with psycopg.connect(store.dsn) as connection:
        records = connection.execute(
            "SELECT title FROM bps_publications WHERE domain = 'test' AND publication_id = 'pub-fixture'"
        ).fetchall()
    assert records == [("Updated title",)]


def test_checkpoint_round_trip() -> None:
    store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
    store.ensure_schema()

    store.save_checkpoint("test:fixture", {"page": 7, "done": False})

    assert store.load_checkpoint("test:fixture") == {"page": 7, "done": False}


def test_dynamic_variable_and_fact_upserts_are_idempotent() -> None:
    store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
    store.ensure_schema()
    variable = {
        "var_id": 999999,
        "title": "Fixture Indicator",
        "sub_id": 1,
        "sub_name": "Fixture Subject",
        "subcsa_id": 2,
        "subcsa_name": "Fixture CSA",
        "def": "Fixture definition",
        "notes": "",
        "vertical": 5,
        "unit": "Orang",
        "graph_id": 3,
        "graph_name": "line",
    }
    fact = {
        "domain": "test",
        "var_id": "999999",
        "var_label": "Fixture Indicator",
        "unit": "Orang",
        "vervar_id": "1306",
        "vervar_label": "Padang Pariaman",
        "turvar_id": None,
        "turvar_label": None,
        "period_id": "2026",
        "period_label": "2026",
        "derived_period_id": None,
        "derived_period_label": None,
        "content_key": "fixture-content",
        "value_numeric": 42,
        "value_text": None,
    }

    store.upsert_dynamic_variables("test", [variable], None)
    store.upsert_dynamic_facts([fact], None)
    fact["value_numeric"] = 43
    store.upsert_dynamic_facts([fact], None)

    with psycopg.connect(store.dsn) as connection:
        value = connection.execute(
            "SELECT value_numeric FROM bps_dynamic_facts WHERE domain='test' AND variable_id='999999'"
        ).fetchone()[0]
    assert float(value) == 43


def test_glossary_upsert_updates_current_definition() -> None:
    store = BpsStore(load_postgres_dsn(POSTGRES_ENV))
    store.ensure_schema()
    row = {
        "glossary_id": "fixture-glossary",
        "external_id": "fixture-external",
        "concept": "Fixture",
        "concept_en": "Fixture",
        "definition": "First definition",
        "definition_en": None,
        "indicator_title": None,
        "classification": None,
        "measure": None,
        "unit": None,
        "content_source": None,
        "data_source": None,
        "endpoint": "web",
        "raw": {"id": "fixture-glossary"},
    }

    store.upsert_glossary([row], None)
    row["definition"] = "Updated definition"
    store.upsert_glossary([row], None)

    with psycopg.connect(store.dsn) as connection:
        definition = connection.execute(
            "SELECT definition FROM bps_glossary WHERE glossary_id='fixture-glossary'"
        ).fetchone()[0]
    assert definition == "Updated definition"
