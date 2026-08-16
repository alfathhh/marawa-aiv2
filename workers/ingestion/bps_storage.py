"""PostgreSQL storage for raw and normalized BPS WebAPI data."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS bps_ingestion_runs (
    id uuid PRIMARY KEY,
    run_type text NOT NULL,
    status text NOT NULL,
    config jsonb NOT NULL,
    summary jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS bps_raw_snapshots (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES bps_ingestion_runs(id),
    source_family text NOT NULL,
    resource_type text NOT NULL,
    request_fingerprint text NOT NULL,
    request_json jsonb NOT NULL,
    response_sha256 text NOT NULL,
    response_json jsonb NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_fingerprint, response_sha256)
);
CREATE INDEX IF NOT EXISTS idx_bps_raw_family_resource
    ON bps_raw_snapshots (source_family, resource_type, fetched_at DESC);

CREATE TABLE IF NOT EXISTS bps_snapshot_observations (
    run_id uuid NOT NULL REFERENCES bps_ingestion_runs(id) ON DELETE CASCADE,
    snapshot_id bigint NOT NULL REFERENCES bps_raw_snapshots(id) ON DELETE CASCADE,
    source_family text NOT NULL,
    resource_type text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, snapshot_id, source_family, resource_type)
);
CREATE INDEX IF NOT EXISTS idx_bps_snapshot_observations_resource
    ON bps_snapshot_observations (source_family, resource_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS bps_ingestion_checkpoints (
    checkpoint_key text PRIMARY KEY,
    state jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bps_dynamic_subjects (
    domain text NOT NULL,
    subject_id text NOT NULL,
    title text,
    subcategory_id text,
    subcategory text,
    table_count integer,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, subject_id)
);

CREATE TABLE IF NOT EXISTS bps_dynamic_variables (
    domain text NOT NULL,
    variable_id text NOT NULL,
    title text,
    subject_id text,
    subject_name text,
    csa_subject_id text,
    csa_subject_name text,
    definition text,
    notes text,
    vertical_id text,
    unit text,
    unit_canonical text,
    graph_id text,
    graph_name text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, variable_id)
);
CREATE INDEX IF NOT EXISTS idx_bps_dynamic_variables_title
    ON bps_dynamic_variables USING gin (to_tsvector('simple', coalesce(title, '')));
ALTER TABLE bps_dynamic_variables ADD COLUMN IF NOT EXISTS unit_canonical text;

CREATE TABLE IF NOT EXISTS bps_dynamic_dimensions (
    domain text NOT NULL,
    variable_id text NOT NULL,
    dimension_type text NOT NULL,
    item_id text NOT NULL,
    item_label text,
    group_id text NOT NULL DEFAULT '',
    group_label text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, variable_id, dimension_type, item_id, group_id)
);

CREATE TABLE IF NOT EXISTS bps_dynamic_facts (
    domain text NOT NULL,
    variable_id text NOT NULL,
    content_key text NOT NULL,
    variable_label text,
    unit text,
    vertical_id text,
    vertical_label text,
    derived_variable_id text,
    derived_variable_label text,
    period_id text,
    period_label text,
    derived_period_id text,
    derived_period_label text,
    value_numeric numeric,
    value_text text,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, variable_id, content_key)
);
CREATE INDEX IF NOT EXISTS idx_bps_dynamic_facts_query
    ON bps_dynamic_facts (domain, variable_id, period_label, vertical_id);

CREATE TABLE IF NOT EXISTS bps_census_events (
    event_id text PRIMARY KEY,
    event_name text,
    event_year integer,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bps_census_topics (
    event_id text NOT NULL,
    topic_id text NOT NULL,
    topic_name text,
    topic_name_en text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, topic_id)
);

CREATE TABLE IF NOT EXISTS bps_census_areas (
    event_id text NOT NULL,
    area_id text NOT NULL,
    mfd_code text,
    area_name text,
    slug text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, area_id)
);
CREATE INDEX IF NOT EXISTS idx_bps_census_areas_mfd ON bps_census_areas (event_id, mfd_code);

CREATE TABLE IF NOT EXISTS bps_census_datasets (
    event_id text NOT NULL,
    topic_id text NOT NULL,
    dataset_id text NOT NULL,
    dataset_name text,
    description text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, dataset_id)
);

CREATE TABLE IF NOT EXISTS bps_census_facts (
    event_id text NOT NULL,
    dataset_id text NOT NULL,
    fact_hash text NOT NULL,
    source_timestamp text,
    geography_id text,
    geography_code text,
    geography_name text,
    geography_level integer,
    indicator_id text,
    indicator_name text,
    period text,
    value_numeric numeric,
    value_text text,
    categories jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, dataset_id, fact_hash)
);
CREATE INDEX IF NOT EXISTS idx_bps_census_facts_query
    ON bps_census_facts (geography_code, indicator_id, period);

CREATE TABLE IF NOT EXISTS bps_simdasi_regions (
    region_code text PRIMARY KEY,
    parent_code text,
    region_name text,
    level text,
    mfd_version text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bps_simdasi_subjects (
    region_code text NOT NULL,
    subject_id text NOT NULL,
    chapter text,
    chapter_en text,
    subject text,
    subject_en text,
    mms_id text,
    mms_subject text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, subject_id)
);

CREATE TABLE IF NOT EXISTS bps_simdasi_tables (
    region_code text NOT NULL,
    table_id text NOT NULL,
    table_code text,
    title text,
    title_en text,
    subject_id text,
    chapter text,
    subject text,
    mms_id text,
    mms_subject text,
    available_years jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, table_id)
);
CREATE INDEX IF NOT EXISTS idx_bps_simdasi_tables_title
    ON bps_simdasi_tables USING gin (to_tsvector('simple', coalesce(title, '')));

CREATE TABLE IF NOT EXISTS bps_simdasi_details (
    region_code text NOT NULL,
    table_id text NOT NULL,
    year integer NOT NULL,
    title text,
    unit text,
    source_created_at text,
    raw jsonb NOT NULL,
    response_sha256 text NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, table_id, year)
);

CREATE TABLE IF NOT EXISTS bps_simdasi_columns (
    region_code text NOT NULL,
    table_id text NOT NULL,
    year integer NOT NULL,
    column_id text NOT NULL,
    position integer NOT NULL,
    name text,
    name_en text,
    data_type text,
    decimal_places integer,
    unit text,
    unit_en text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, table_id, year, column_id)
);

CREATE TABLE IF NOT EXISTS bps_simdasi_facts (
    region_code text NOT NULL,
    table_id text NOT NULL,
    year integer NOT NULL,
    row_position integer NOT NULL,
    geography_code text NOT NULL DEFAULT '',
    row_label text,
    row_label_raw text,
    row_unit text,
    column_id text NOT NULL,
    value_numeric numeric,
    value_text text,
    value_raw text,
    value_code text,
    value_note text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, table_id, year, row_position, column_id)
);
CREATE INDEX IF NOT EXISTS idx_bps_simdasi_facts_query
    ON bps_simdasi_facts (region_code, table_id, year, geography_code, column_id);

CREATE TABLE IF NOT EXISTS bps_publications (
    domain text NOT NULL,
    publication_id text NOT NULL,
    title text,
    issn text,
    catalog_number text,
    publication_number text,
    abstract text,
    scheduled_date text,
    release_date text,
    updated_date text,
    cover_url text,
    pdf_url text,
    declared_size text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, publication_id)
);
CREATE INDEX IF NOT EXISTS idx_bps_publications_release ON bps_publications (domain, release_date DESC);
CREATE INDEX IF NOT EXISTS idx_bps_publications_title
    ON bps_publications USING gin (to_tsvector('simple', coalesce(title, '')));

CREATE TABLE IF NOT EXISTS bps_publication_files (
    domain text NOT NULL,
    publication_id text NOT NULL,
    file_type text NOT NULL,
    source_url text NOT NULL,
    local_path text,
    sha256 text,
    bytes bigint,
    content_type text,
    download_status text NOT NULL,
    downloaded_at timestamptz,
    last_error text,
    PRIMARY KEY (domain, publication_id, file_type)
);

CREATE TABLE IF NOT EXISTS bps_glossary (
    glossary_id text PRIMARY KEY,
    external_id text,
    concept text,
    concept_en text,
    definition text,
    definition_en text,
    indicator_title text,
    classification text,
    measure text,
    unit text,
    content_source text,
    data_source text,
    endpoint text,
    raw jsonb NOT NULL,
    snapshot_id bigint REFERENCES bps_raw_snapshots(id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bps_glossary_search
    ON bps_glossary USING gin (to_tsvector('simple', coalesce(concept, '') || ' ' || coalesce(definition, '')));

DROP VIEW IF EXISTS bps_serving_dynamic;
CREATE VIEW bps_serving_dynamic AS
SELECT f.domain, f.variable_id AS indicator_code, f.variable_label AS indicator_name,
       f.vertical_id AS geography_code, f.vertical_label AS geography_name,
       f.vertical_id AS primary_dimension_id, f.vertical_label AS primary_dimension_label,
       f.derived_variable_id AS secondary_dimension_id, f.derived_variable_label AS secondary_dimension_label,
       f.period_label AS period,
       CASE WHEN f.derived_period_label IS NOT NULL AND f.derived_period_label <> f.period_label
            THEN 'quarterly' ELSE 'annual' END AS period_granularity,
       f.derived_period_id AS subperiod_code, f.derived_period_label AS subperiod_label,
       f.derived_variable_label AS category,
       f.value_numeric AS value, f.value_text,
       f.unit AS unit_raw,
       coalesce(v.unit_canonical, f.unit) AS unit,
       CASE
           WHEN v.unit_canonical IS NOT NULL THEN 'canonical'
           WHEN f.unit IS NULL OR f.unit = '' OR f.unit = 'Tidak Ada Satuan' THEN 'unknown_review'
           ELSE 'known'
       END AS unit_state,
       f.snapshot_id
FROM bps_dynamic_facts f
LEFT JOIN bps_dynamic_variables v
  ON v.domain=f.domain AND v.variable_id=f.variable_id;

CREATE OR REPLACE VIEW bps_serving_census AS
SELECT event_id, dataset_id, indicator_id AS indicator_code, indicator_name,
       geography_code, geography_name, period, value_numeric AS value,
       value_text, categories, snapshot_id
FROM bps_census_facts;

CREATE TABLE IF NOT EXISTS bps_simdasi_units (
    region_code text NOT NULL,
    table_code text NOT NULL,
    column_name text NOT NULL,
    unit text,
    unit_source text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region_code, table_code, column_name)
);

CREATE TABLE IF NOT EXISTS bps_simdasi_marker_legend (
    marker text PRIMARY KEY,
    description text NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

DROP VIEW IF EXISTS bps_serving_simdasi;
CREATE VIEW bps_serving_simdasi AS
SELECT f.region_code, f.table_id, t.table_code, t.title AS table_title,
       f.year AS period, f.geography_code, f.row_label AS geography_name,
       f.row_label_raw,
       CASE
           WHEN f.geography_code IS NULL OR f.geography_code = '' THEN 'category'
           WHEN f.geography_code = f.region_code THEN 'kabupaten'
           ELSE 'kecamatan'
       END AS row_role,
       CASE
           WHEN f.geography_code = f.region_code THEN 'kabupaten'
           WHEN f.geography_code IS NOT NULL AND f.geography_code <> '' THEN 'kecamatan'
           ELSE NULL
       END AS geography_level,
       f.column_id AS indicator_code, c.name AS indicator_name,
       f.value_numeric AS value, f.value_text, f.value_code, f.value_note,
       CASE
           WHEN t.title ILIKE '%miliar rupiah%' THEN 'miliar rupiah'
           WHEN t.title ILIKE '%juta rupiah%' THEN 'juta rupiah'
           WHEN t.title ILIKE '%ribu rupiah%' THEN 'ribu rupiah'
           ELSE coalesce(c.unit, u.unit, f.row_unit)
       END AS unit,
       CASE
           WHEN t.title ILIKE '%miliar rupiah%' THEN 'title_matched'
           WHEN t.title ILIKE '%juta rupiah%' THEN 'title_matched'
           WHEN t.title ILIKE '%ribu rupiah%' THEN 'title_matched'
           ELSE coalesce(u.unit_source, CASE WHEN c.unit IS NOT NULL THEN 'column_meta' END)
       END AS unit_source,
       d.source_created_at, f.snapshot_id
FROM bps_simdasi_facts f
JOIN bps_simdasi_tables t
  ON t.region_code=f.region_code AND t.table_id=f.table_id
LEFT JOIN bps_simdasi_columns c
  ON c.region_code=f.region_code AND c.table_id=f.table_id
 AND c.year=f.year AND c.column_id=f.column_id
LEFT JOIN bps_simdasi_units u
  ON u.region_code=f.region_code AND u.table_code=t.table_code
 AND u.column_name=c.name
LEFT JOIN bps_simdasi_details d
  ON d.region_code=f.region_code AND d.table_id=f.table_id AND d.year=f.year;

-- Serving views are recreated on every bootstrap; object-level GRANTs die with
-- the old objects. Re-apply runtime SELECT grants when the role exists so
-- ingestion bootstrap never silently breaks runtime least-privilege access.
DO $runtime_grants$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='marawa_runtime_ro') THEN
        GRANT SELECT ON bps_serving_dynamic TO marawa_runtime_ro;
        GRANT SELECT ON bps_serving_simdasi TO marawa_runtime_ro;
        GRANT SELECT ON bps_serving_census TO marawa_runtime_ro;
        GRANT SELECT ON bps_publications TO marawa_runtime_ro;
    END IF;
END
$runtime_grants$;
"""


def _read_env(path: Path) -> dict[str, str]:
    from workers.ingestion.bps_client import secure_read_secret_file

    values: dict[str, str] = {}
    for line in secure_read_secret_file(str(path)):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def load_postgres_dsn(path: Path) -> str:
    values = _read_env(path)
    return make_conninfo(
        host=values["POSTGRES_HOST"],
        port=values["POSTGRES_PORT"],
        dbname=values["POSTGRES_DB"],
        user=values["POSTGRES_USER"],
        password=values["POSTGRES_PASSWORD"],
    )


class BpsStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ensure_schema(self) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(SCHEMA_SQL)

    def start_run(self, run_type: str, config: dict[str, Any]) -> uuid.UUID:
        run_id = uuid.uuid4()
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "INSERT INTO bps_ingestion_runs (id, run_type, status, config) VALUES (%s, %s, 'running', %s)",
                (run_id, run_type, Jsonb(config)),
            )
        return run_id

    def finish_run(self, run_id: uuid.UUID, status: str, summary: dict[str, Any]) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "UPDATE bps_ingestion_runs SET status=%s, summary=%s, finished_at=now() WHERE id=%s",
                (status, Jsonb(summary), run_id),
            )

    def record_snapshot(
        self,
        run_id: uuid.UUID,
        source_family: str,
        resource_type: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> int:
        from workers.ingestion.bps_webapi import response_sha256

        digest = response_sha256(json_bytes(response))
        with psycopg.connect(self.dsn) as connection:
            row = connection.execute(
                """
                INSERT INTO bps_raw_snapshots
                    (run_id, source_family, resource_type, request_fingerprint, request_json, response_sha256, response_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (request_fingerprint, response_sha256)
                DO UPDATE SET last_seen_at=now()
                RETURNING id
                """,
                (
                    run_id,
                    source_family,
                    resource_type,
                    request["request_fingerprint"],
                    Jsonb(request),
                    digest,
                    Jsonb(response),
                ),
            ).fetchone()
            assert row is not None
            connection.execute(
                """
                INSERT INTO bps_snapshot_observations
                    (run_id, snapshot_id, source_family, resource_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, snapshot_id, source_family, resource_type)
                DO UPDATE SET observed_at=now()
                """,
                (run_id, row[0], source_family, resource_type),
            )
        return int(row[0])

    def save_checkpoint(self, checkpoint_key: str, state: dict[str, Any]) -> None:
        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """
                INSERT INTO bps_ingestion_checkpoints (checkpoint_key, state)
                VALUES (%s, %s)
                ON CONFLICT (checkpoint_key) DO UPDATE SET state=excluded.state, updated_at=now()
                """,
                (checkpoint_key, Jsonb(state)),
            )

    def load_checkpoint(self, checkpoint_key: str) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn) as connection:
            row = connection.execute(
                "SELECT state FROM bps_ingestion_checkpoints WHERE checkpoint_key=%s",
                (checkpoint_key,),
            ).fetchone()
        return None if row is None else row[0]

    def _bulk_upsert(
        self,
        table: str,
        columns: list[str],
        conflict_columns: list[str],
        update_columns: list[str],
        rows: Iterable[dict[str, Any]],
        *,
        json_columns: set[str] | None = None,
    ) -> int:
        values = list(rows)
        if not values:
            return 0
        json_columns = json_columns or set()
        placeholders = ", ".join(f"%({column})s" for column in columns)
        updates = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
        if updates:
            updates += ", last_seen_at=now()"
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {updates}"
        )
        payload: list[dict[str, Any]] = []
        for row in values:
            item = {column: row.get(column) for column in columns}
            for column in json_columns:
                item[column] = Jsonb(item[column])
            payload.append(item)
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(sql, payload)
        return len(values)

    def upsert_dynamic_variables(
        self, domain: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None
    ) -> int:
        from workers.ingestion.bps_units import canonical_unit

        normalized = [
            {
                "domain": domain,
                "variable_id": str(row.get("var_id")),
                "title": row.get("title"),
                "subject_id": None if row.get("sub_id") is None else str(row.get("sub_id")),
                "subject_name": row.get("sub_name"),
                "csa_subject_id": None if row.get("subcsa_id") is None else str(row.get("subcsa_id")),
                "csa_subject_name": row.get("subcsa_name"),
                "definition": row.get("def"),
                "notes": row.get("notes"),
                "vertical_id": None if row.get("vertical") is None else str(row.get("vertical")),
                "unit": row.get("unit"),
                "unit_canonical": canonical_unit(row.get("unit")),
                "graph_id": None if row.get("graph_id") is None else str(row.get("graph_id")),
                "graph_name": row.get("graph_name"),
                "raw": row,
                "snapshot_id": snapshot_id,
            }
            for row in rows
        ]
        columns = [
            "domain", "variable_id", "title", "subject_id", "subject_name",
            "csa_subject_id", "csa_subject_name", "definition", "notes", "vertical_id",
            "unit", "unit_canonical", "graph_id", "graph_name", "raw", "snapshot_id",
        ]
        return self._bulk_upsert(
            "bps_dynamic_variables", columns, ["domain", "variable_id"],
            columns[2:], normalized, json_columns={"raw"}
        )

    def upsert_dynamic_dimensions(
        self, rows: Iterable[dict[str, Any]], snapshot_id: int | None
    ) -> int:
        normalized = [{**row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["domain", "variable_id", "dimension_type", "item_id", "item_label", "group_id", "group_label", "raw", "snapshot_id"]
        return self._bulk_upsert(
            "bps_dynamic_dimensions", columns,
            ["domain", "variable_id", "dimension_type", "item_id", "group_id"],
            columns[4:], normalized, json_columns={"raw"},
        )

    def upsert_dynamic_facts(
        self, rows: Iterable[dict[str, Any]], snapshot_id: int | None
    ) -> int:
        normalized = [
            {
                "domain": row["domain"],
                "variable_id": row["var_id"],
                "content_key": row["content_key"],
                "variable_label": row.get("var_label"),
                "unit": row.get("unit"),
                "vertical_id": row.get("vervar_id"),
                "vertical_label": row.get("vervar_label"),
                "derived_variable_id": row.get("turvar_id"),
                "derived_variable_label": row.get("turvar_label"),
                "period_id": row.get("period_id"),
                "period_label": row.get("period_label"),
                "derived_period_id": row.get("derived_period_id"),
                "derived_period_label": row.get("derived_period_label"),
                "value_numeric": row.get("value_numeric"),
                "value_text": row.get("value_text"),
                "snapshot_id": snapshot_id,
            }
            for row in rows
        ]
        columns = [
            "domain", "variable_id", "content_key", "variable_label", "unit",
            "vertical_id", "vertical_label", "derived_variable_id", "derived_variable_label",
            "period_id", "period_label", "derived_period_id", "derived_period_label",
            "value_numeric", "value_text", "snapshot_id",
        ]
        return self._bulk_upsert(
            "bps_dynamic_facts", columns, ["domain", "variable_id", "content_key"],
            columns[3:], normalized
        )

    def upsert_glossary(
        self, rows: Iterable[dict[str, Any]], snapshot_id: int | None
    ) -> int:
        normalized = [{**row, "snapshot_id": snapshot_id} for row in rows]
        columns = [
            "glossary_id", "external_id", "concept", "concept_en", "definition",
            "definition_en", "indicator_title", "classification", "measure", "unit",
            "content_source", "data_source", "endpoint", "raw", "snapshot_id",
        ]
        return self._bulk_upsert(
            "bps_glossary", columns, ["glossary_id"], columns[1:], normalized,
            json_columns={"raw"}
        )

    def upsert_dynamic_subjects(
        self, domain: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None
    ) -> int:
        normalized = [
            {
                "domain": domain,
                "subject_id": str(row.get("sub_id")),
                "title": row.get("title"),
                "subcategory_id": None if row.get("subcat_id") is None else str(row.get("subcat_id")),
                "subcategory": row.get("subcat"),
                "table_count": row.get("ntable") or row.get("ntabel"),
                "raw": row,
                "snapshot_id": snapshot_id,
            }
            for row in rows
        ]
        columns = ["domain", "subject_id", "title", "subcategory_id", "subcategory", "table_count", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_dynamic_subjects", columns, ["domain", "subject_id"], columns[2:], normalized, json_columns={"raw"})

    def upsert_census_events(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"event_id": str(row.get("id")), "event_name": row.get("kegiatan"), "event_year": row.get("tahun_kegiatan"), "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["event_id", "event_name", "event_year", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_census_events", columns, ["event_id"], columns[1:], normalized, json_columns={"raw"})

    def upsert_census_topics(self, event_id: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"event_id": event_id, "topic_id": str(row.get("id")), "topic_name": row.get("topik"), "topic_name_en": row.get("topic"), "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["event_id", "topic_id", "topic_name", "topic_name_en", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_census_topics", columns, ["event_id", "topic_id"], columns[2:], normalized, json_columns={"raw"})

    def upsert_census_areas(self, event_id: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"event_id": event_id, "area_id": str(row.get("id")), "mfd_code": None if row.get("kode_mfd") is None else str(row.get("kode_mfd")), "area_name": row.get("nama"), "slug": row.get("slug"), "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["event_id", "area_id", "mfd_code", "area_name", "slug", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_census_areas", columns, ["event_id", "area_id"], columns[2:], normalized, json_columns={"raw"})

    def upsert_census_datasets(self, event_id: str, topic_id: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"event_id": event_id, "topic_id": topic_id, "dataset_id": str(row.get("id")), "dataset_name": row.get("nama") or row.get("Nama"), "description": row.get("deskripsi"), "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["event_id", "topic_id", "dataset_id", "dataset_name", "description", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_census_datasets", columns, ["event_id", "dataset_id"], columns[2:], normalized, json_columns={"raw"})

    def upsert_census_facts(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        from workers.ingestion.bps_webapi import census_fact_identity

        normalized = []
        for row in rows:
            normalized.append({**row, "fact_hash": census_fact_identity(row), "categories": row.get("categories", []), "raw": row["raw"], "snapshot_id": snapshot_id})
        columns = ["event_id", "dataset_id", "fact_hash", "source_timestamp", "geography_id", "geography_code", "geography_name", "geography_level", "indicator_id", "indicator_name", "period", "value_numeric", "value_text", "categories", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_census_facts", columns, ["event_id", "dataset_id", "fact_hash"], columns[3:], normalized, json_columns={"categories", "raw"})

    def upsert_simdasi_regions(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None, *, level: str, parent_code: str | None = None, version: str | None = None) -> int:
        normalized = []
        for row in rows:
            code = row.get("kode_mfd") or row.get("kode") or row.get("id") or row.get("wilayah")
            name = row.get("nama") or row.get("nama_wilayah") or row.get("label")
            if code is None:
                continue
            normalized.append({"region_code": str(code), "parent_code": parent_code, "region_name": name, "level": level, "mfd_version": version, "raw": row, "snapshot_id": snapshot_id})
        columns = ["region_code", "parent_code", "region_name", "level", "mfd_version", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_regions", columns, ["region_code"], columns[1:], normalized, json_columns={"raw"})

    def upsert_simdasi_subjects(self, region_code: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"region_code": region_code, "subject_id": str(row.get("id") or row.get("id_subject")), "chapter": row.get("bab"), "chapter_en": row.get("bab_en"), "subject": row.get("subject"), "subject_en": row.get("subject_en"), "mms_id": None if row.get("mms_id") is None else str(row.get("mms_id")), "mms_subject": row.get("mms_subject"), "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["region_code", "subject_id", "chapter", "chapter_en", "subject", "subject_en", "mms_id", "mms_subject", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_subjects", columns, ["region_code", "subject_id"], columns[2:], normalized, json_columns={"raw"})

    def upsert_simdasi_tables(self, region_code: str, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{"region_code": region_code, "table_id": str(row.get("id_tabel")), "table_code": row.get("kode_tabel"), "title": row.get("judul"), "title_en": row.get("judul_en"), "subject_id": None if row.get("id_subject") is None else str(row.get("id_subject")), "chapter": row.get("bab"), "subject": row.get("subject"), "mms_id": None if row.get("mms_id") is None else str(row.get("mms_id")), "mms_subject": row.get("mms_subject"), "available_years": row.get("ketersediaan_tahun") or [], "raw": row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["region_code", "table_id", "table_code", "title", "title_en", "subject_id", "chapter", "subject", "mms_id", "mms_subject", "available_years", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_tables", columns, ["region_code", "table_id"], columns[2:], normalized, json_columns={"available_years", "raw"})

    def upsert_simdasi_details(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        from workers.ingestion.bps_webapi import response_sha256
        normalized = [{**row, "response_sha256": response_sha256(json_bytes(row["raw"])), "snapshot_id": snapshot_id} for row in rows]
        columns = ["region_code", "table_id", "year", "title", "unit", "source_created_at", "raw", "response_sha256", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_details", columns, ["region_code", "table_id", "year"], columns[3:], normalized, json_columns={"raw"})

    def upsert_simdasi_columns(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = [{**row, "snapshot_id": snapshot_id} for row in rows]
        columns = ["region_code", "table_id", "year", "column_id", "position", "name", "name_en", "data_type", "decimal_places", "unit", "unit_en", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_columns", columns, ["region_code", "table_id", "year", "column_id"], columns[4:], normalized, json_columns={"raw"})

    def upsert_simdasi_facts(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        normalized = []
        for row in rows:
            normalized.append({**row, "geography_code": row.get("geography_code") or "", "snapshot_id": snapshot_id})
        columns = ["region_code", "table_id", "year", "row_position", "geography_code", "row_label", "row_label_raw", "row_unit", "column_id", "value_numeric", "value_text", "value_raw", "value_code", "value_note", "raw", "snapshot_id"]
        return self._bulk_upsert("bps_simdasi_facts", columns, ["region_code", "table_id", "year", "row_position", "column_id"], columns[4:], normalized, json_columns={"raw"})

    def upsert_publications(self, rows: Iterable[dict[str, Any]], snapshot_id: int | None) -> int:
        values = list(rows)
        if not values:
            return 0
        sql = """
        INSERT INTO bps_publications (
            domain, publication_id, title, issn, catalog_number, publication_number,
            abstract, scheduled_date, release_date, updated_date, cover_url, pdf_url,
            declared_size, raw, snapshot_id
        ) VALUES (
            %(domain)s, %(publication_id)s, %(title)s, %(issn)s, %(catalog_number)s,
            %(publication_number)s, %(abstract)s, %(scheduled_date)s, %(release_date)s,
            %(updated_date)s, %(cover_url)s, %(pdf_url)s, %(declared_size)s, %(raw)s,
            %(snapshot_id)s
        )
        ON CONFLICT (domain, publication_id) DO UPDATE SET
            title=excluded.title, issn=excluded.issn, catalog_number=excluded.catalog_number,
            publication_number=excluded.publication_number, abstract=excluded.abstract,
            scheduled_date=excluded.scheduled_date, release_date=excluded.release_date,
            updated_date=excluded.updated_date, cover_url=excluded.cover_url,
            pdf_url=excluded.pdf_url, declared_size=excluded.declared_size,
            raw=excluded.raw, snapshot_id=excluded.snapshot_id, last_seen_at=now()
        """
        payload = [{**row, "raw": Jsonb(row["raw"]), "snapshot_id": snapshot_id} for row in values]
        with psycopg.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(sql, payload)
        return len(values)


def json_bytes(value: Any) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
