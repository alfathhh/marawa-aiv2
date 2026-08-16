from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.ingestion.bps_storage import load_postgres_dsn
from scripts.bps_template_binder import TemplateValidationError, bind_template

ADMIN_ENV = Path("/home/ubuntu/.config/marawa-ai/postgres.env")


@pytest.fixture(scope="module")
def connection():
    with psycopg.connect(load_postgres_dsn(ADMIN_ENV)) as conn:
        yield conn


@pytest.fixture(scope="module")
def templates(connection):
    return connection.execute(
        """
        SELECT template_id, parameter_schema, sql_template, row_limit, timeout_ms, has_own_limit
        FROM bps_registry.query_template_registry t
        JOIN bps_registry.registry_versions v USING (registry_version_id)
        WHERE v.status='published'
        """
    ).fetchall()


def test_every_declared_parameter_is_used_in_sql(templates) -> None:
    param_re = re.compile(r"%\((\w+)\)s")
    for template_id, schema, sql, _row_limit, _timeout, _has_limit in templates:
        declared = set(schema.keys())
        used = set(param_re.findall(sql))
        missing = declared - used
        assert not missing, f"{template_id} declares params not used in SQL: {missing}"
        unknown = used - declared
        assert not unknown, f"{template_id} SQL uses undeclared params: {unknown}"


def test_bind_template_valid_dynamic_point_returns_rows(connection, templates) -> None:
    template = None
    for row in templates:
        template_id, schema, sql, row_limit, timeout, has_own_limit = row
        if template_id == "dynamic_point":
            template = {
                "template_id": template_id,
                "parameter_schema": schema,
                "sql_template": sql,
                "row_limit": row_limit,
                "timeout_ms": timeout,
                "has_own_limit": has_own_limit,
            }
            break
    assert template is not None, "dynamic_point template not found"
    sql, params = bind_template(
        template,
        {"indicator_code": "29", "period": "2025"},
    )
    with connection.cursor() as cursor:
        rows = cursor.execute(sql, params).fetchall()
    assert len(rows) > 0
    assert all(row[11] is not None for row in rows)  # value present


def test_bind_template_rejects_unknown_parameter(templates) -> None:
    schema = {t[0]: t[1] for t in templates}
    sql_map = {t[0]: t[2] for t in templates}
    row_limit = {t[0]: t[3] for t in templates}
    timeout = {t[0]: t[4] for t in templates}
    own = {t[0]: t[5] for t in templates}
    template = {
        "template_id": "dynamic_trend",
        "parameter_schema": schema["dynamic_trend"],
        "sql_template": sql_map["dynamic_trend"],
        "row_limit": row_limit["dynamic_trend"],
        "timeout_ms": timeout["dynamic_trend"],
        "has_own_limit": own["dynamic_trend"],
    }
    with pytest.raises(TemplateValidationError, match="unknown"):
        bind_template(template, {"indicator_code": "29", "hacked_column": "x"})


def test_bind_template_rejects_missing_required(templates) -> None:
    schema = {t[0]: t[1] for t in templates}
    sql_map = {t[0]: t[2] for t in templates}
    row_limit = {t[0]: t[3] for t in templates}
    timeout = {t[0]: t[4] for t in templates}
    own = {t[0]: t[5] for t in templates}
    template = {
        "template_id": "simdasi_point",
        "parameter_schema": schema["simdasi_point"],
        "sql_template": sql_map["simdasi_point"],
        "row_limit": row_limit["simdasi_point"],
        "timeout_ms": timeout["simdasi_point"],
        "has_own_limit": own["simdasi_point"],
    }
    with pytest.raises(TemplateValidationError, match="required"):
        bind_template(template, {})


def test_bind_template_rejects_wrong_type(templates) -> None:
    schema = {t[0]: t[1] for t in templates}
    sql_map = {t[0]: t[2] for t in templates}
    row_limit = {t[0]: t[3] for t in templates}
    timeout = {t[0]: t[4] for t in templates}
    own = {t[0]: t[5] for t in templates}
    template = {
        "template_id": "publication_list",
        "parameter_schema": schema["publication_list"],
        "sql_template": sql_map["publication_list"],
        "row_limit": row_limit["publication_list"],
        "timeout_ms": timeout["publication_list"],
        "has_own_limit": own["publication_list"],
    }
    with pytest.raises(TemplateValidationError, match="type"):
        bind_template(template, {"page_size": "seratus", "offset": 0})


def test_bind_template_is_injection_safe(connection, templates) -> None:
    schema = {t[0]: t[1] for t in templates}
    sql_map = {t[0]: t[2] for t in templates}
    row_limit = {t[0]: t[3] for t in templates}
    timeout = {t[0]: t[4] for t in templates}
    own = {t[0]: t[5] for t in templates}
    template = {
        "template_id": "dynamic_point",
        "parameter_schema": schema["dynamic_point"],
        "sql_template": sql_map["dynamic_point"],
        "row_limit": row_limit["dynamic_point"],
        "timeout_ms": timeout["dynamic_point"],
        "has_own_limit": own["dynamic_point"],
    }
    sql, params = bind_template(
        template,
        {
            "indicator_code": "29",
            "period": "2025",
            "geography_code": "1306010'; DROP TABLE bps_registry.geography_registry;--",
        },
    )
    with connection.cursor() as cursor:
        rows = cursor.execute(sql, params).fetchall()
    assert connection.execute(
        "SELECT count(*) FROM bps_registry.geography_registry"
    ).fetchone()[0] == 18
    assert len(rows) in (0, 1)  # geography filter matched nothing or one row; table alive
