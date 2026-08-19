from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import BpsStore, load_postgres_dsn
from scripts.run_migrations import down as runner_down
from scripts.run_migrations import up as runner_up

ADMIN_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")
TEMP_ENV = Path("/tmp/marawa-migtest-{}.env".format(uuid.uuid4().hex[:8]))


@pytest.fixture(scope="module")
def isolated_env():
    """Create a throwaway database and env pointing at it."""
    test_db = "marawa_migtest_{}".format(uuid.uuid4().hex[:8])
    admin_dsn = load_postgres_dsn(ADMIN_ENV)
    # Copy the admin env key=values but point only at the new database.
    environment = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in ADMIN_ENV.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    environment["POSTGRES_DB"] = test_db
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE {test_db}')
    TEMP_ENV.write_text(
        "\n".join(f"{key}={value}" for key, value in environment.items()), encoding="utf-8"
    )
    os.chmod(TEMP_ENV, 0o600)
    test_dsn = load_postgres_dsn(TEMP_ENV)
    _bootstrap(test_dsn)
    yield test_dsn, test_db
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(f"DROP DATABASE IF EXISTS {test_db} WITH (FORCE)")
    TEMP_ENV.unlink(missing_ok=True)


def _bootstrap(dsn: str) -> None:
    """Base mirror schema; app migration stack builds on top of it."""
    store = BpsStore(dsn)
    store.ensure_schema()


def test_migrations_up_and_down_cycle(isolated_env) -> None:
    dsn, test_db = isolated_env

    result = runner_up(TEMP_ENV)
    applied = [item["migration_id"] for item in result["results"]]
    assert applied == ["001", "002", "003", "004", "005", "006", "007", "008", "009", "010", "011", "012"], applied

    with psycopg.connect(dsn) as connection:
        ledger = set(
            connection.execute("SELECT migration_id FROM marawa_migrations.schema_migrations").fetchall()
        )
        # All migrations are registered by the runner.
        assert len(ledger) == 12

        # 001 effect: fixed serving columns exist.
        dynamic_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='bps_serving_dynamic'"
            ).fetchall()
        }
        assert "subperiod_label" in dynamic_columns
        assert "unit_state" in dynamic_columns

        # 002/003 effect: versioned registry keys exist.
        keys = connection.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid='bps_registry.dataset_registry'::regclass "
            "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
        ).fetchall()
        assert "registry_version_id" in [key[0] for key in keys]
        assert connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='bps_registry.dataset_registry'::regclass AND contype='p'"
        ).fetchone()[0] == "PRIMARY KEY (registry_version_id, dataset_id)"

        # 004 effect: runtime role exists.
        assert connection.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname='marawa_runtime_ro'"
        ).fetchone()[0] == 1

    # Rollback one by one, newest first.
    down_005 = runner_down(TEMP_ENV, "005")
    with psycopg.connect(dsn) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='dimension_item_registry'"
            ).fetchall()
        }
        assert "display_label" not in columns

    down_004 = runner_down(TEMP_ENV, "004")
    with psycopg.connect(dsn) as connection:
        # Cluster-shared role may survive if other DBs still grant it; the
        # security property is: zero privileges in THIS database.
        assert connection.execute(
            """
            SELECT count(*) FROM information_schema.role_table_grants
            WHERE grantee='marawa_runtime_ro'
            """
        ).fetchone()[0] == 0
        # CONNECT via PUBLIC remains possible; assert no direct grant remains.
        assert connection.execute(
            """
            SELECT count(*) FROM pg_database db
            CROSS JOIN LATERAL aclexplode(db.datacl) acl
            WHERE db.datname=current_database()
              AND acl.grantee = (SELECT oid FROM pg_roles WHERE rolname='marawa_runtime_ro')
              AND acl.privilege_type='CONNECT'
            """
        ).fetchone()[0] == 0

    down_003 = runner_down(TEMP_ENV, "003")
    with psycopg.connect(dsn) as connection:
        pk = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='bps_registry.dataset_registry'::regclass AND contype='p'"
        ).fetchone()[0]
        assert pk == "PRIMARY KEY (dataset_id)", pk

    down_002 = runner_down(TEMP_ENV, "002")
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname='bps_registry'"
        ).fetchone()[0] == 0

    down_001 = runner_down(TEMP_ENV, "001")
    with psycopg.connect(dsn) as connection:
        legacy_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='bps_serving_dynamic'"
            ).fetchall()
        }
        assert "subperiod_label" not in legacy_columns
        assert "unit_state" not in legacy_columns

    assert [item["migration_id"] for item in down_001["results"]] == ["001"]
