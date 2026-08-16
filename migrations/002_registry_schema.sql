-- Migration 002: bps_registry schema (planning DDL docs/24, now applied).
-- Runtime reads read-only; only the registry builder writes.

CREATE SCHEMA IF NOT EXISTS bps_registry;

CREATE TABLE IF NOT EXISTS bps_registry.registry_versions (
    registry_version_id TEXT PRIMARY KEY,
    checksum            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','published','retired')),
    published_at        TIMESTAMPTZ,
    built_from_snapshot_ids TEXT[] NOT NULL DEFAULT '{}',
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS bps_registry.dataset_registry (
    dataset_id          TEXT PRIMARY KEY,
    registry_version_id TEXT NOT NULL REFERENCES bps_registry.registry_versions(registry_version_id),
    source_family       TEXT NOT NULL CHECK (source_family IN ('simdasi','dynamic','census','publication')),
    source_resource_id  TEXT NOT NULL,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,
    topic_id            TEXT,
    topic_name          TEXT,
    dataset_shape       TEXT NOT NULL CHECK (dataset_shape IN
                         ('geography_series','category_series','cross_tab',
                          'quarterly_series','publication_metadata')),
    answerability       TEXT NOT NULL DEFAULT 'answerable'
                        CHECK (answerability IN ('answerable','metadata_only','blocked_quality','unavailable')),
    period_granularity  TEXT NOT NULL DEFAULT 'annual'
                        CHECK (period_granularity IN ('annual','quarterly','event','release')),
    period_min          TEXT,
    period_max          TEXT,
    period_latest       TEXT,
    search_document     TEXT NOT NULL,
    search_aliases      TEXT[] NOT NULL DEFAULT '{}',
    supported_operations TEXT[] NOT NULL DEFAULT '{}',
    quality_flags       TEXT[] NOT NULL DEFAULT '{}',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (registry_version_id, source_family, source_resource_id)
);
CREATE INDEX IF NOT EXISTS idx_registry_dataset_family
    ON bps_registry.dataset_registry (registry_version_id, source_family);
CREATE INDEX IF NOT EXISTS idx_registry_dataset_aliases
    ON bps_registry.dataset_registry USING GIN (search_aliases);

CREATE TABLE IF NOT EXISTS bps_registry.measure_registry (
    measure_id          TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES bps_registry.dataset_registry(dataset_id),
    source_measure_id   TEXT NOT NULL,
    name                TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    value_type          TEXT NOT NULL CHECK (value_type IN ('number','text','marker')),
    unit_state          TEXT NOT NULL CHECK (unit_state IN ('known','unitless','unknown_review')),
    unit_display        TEXT,
    unit_scale          NUMERIC,
    decimal_places      SMALLINT NOT NULL DEFAULT 0,
    aggregation_semantics TEXT NOT NULL CHECK (aggregation_semantics IN
                         ('additive','non_additive','index','rate','share','count')),
    allowed_operations  TEXT[] NOT NULL DEFAULT '{}',
    marker_policy       TEXT,
    comparability_group TEXT,
    UNIQUE (dataset_id, source_measure_id)
);

CREATE TABLE IF NOT EXISTS bps_registry.dimension_registry (
    dimension_id      TEXT PRIMARY KEY,
    dataset_id        TEXT NOT NULL REFERENCES bps_registry.dataset_registry(dataset_id),
    name              TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('geography','category','subperiod')),
    required          BOOLEAN NOT NULL DEFAULT FALSE,
    default_item_id   TEXT,
    total_item_id     TEXT,
    cardinality       INTEGER NOT NULL DEFAULT 0,
    display_order     SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (dataset_id, name)
);

CREATE TABLE IF NOT EXISTS bps_registry.dimension_item_registry (
    item_id             TEXT PRIMARY KEY,
    dimension_id        TEXT NOT NULL REFERENCES bps_registry.dimension_registry(dimension_id),
    source_item_id      TEXT NOT NULL,
    source_item_code    TEXT,
    label               TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    canonical_geography_id TEXT,
    is_total            BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (dimension_id, source_item_id)
);

CREATE TABLE IF NOT EXISTS bps_registry.geography_registry (
    geography_id TEXT PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    level        TEXT NOT NULL CHECK (level IN ('kabupaten','kecamatan')),
    parent_id    TEXT REFERENCES bps_registry.geography_registry(geography_id),
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bps_registry.geography_aliases (
    alias_id        TEXT PRIMARY KEY,
    geography_id    TEXT NOT NULL REFERENCES bps_registry.geography_registry(geography_id),
    source_family   TEXT NOT NULL CHECK (source_family IN ('simdasi','dynamic','census','publication')),
    source_code     TEXT,
    source_item_id  TEXT,
    source_label    TEXT NOT NULL,
    match_type      TEXT NOT NULL CHECK (match_type IN ('exact_code','approved_alias','historical_name')),
    valid_from      TEXT,
    valid_until     TEXT,
    UNIQUE (geography_id, source_family, source_code, source_label)
);

CREATE TABLE IF NOT EXISTS bps_registry.query_template_registry (
    template_id         TEXT NOT NULL,
    template_version    INTEGER NOT NULL,
    registry_version_id TEXT NOT NULL REFERENCES bps_registry.registry_versions(registry_version_id),
    dataset_shape       TEXT NOT NULL,
    view_name           TEXT NOT NULL,
    parameter_schema    JSONB NOT NULL DEFAULT '{}'::jsonb,
    sql_template        TEXT NOT NULL,
    row_limit           INTEGER NOT NULL DEFAULT 100,
    timeout_ms          INTEGER NOT NULL DEFAULT 5000,
    result_schema_id    TEXT NOT NULL,
    validation_rules    TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (template_id, template_version)
);

CREATE TABLE IF NOT EXISTS bps_registry.candidate_sets (
    candidate_set_id     TEXT PRIMARY KEY,
    conversation_id      TEXT NOT NULL,
    registry_version_id  TEXT NOT NULL,
    normalized_goal      JSONB NOT NULL DEFAULT '{}'::jsonb,
    focused_family       TEXT,
    shown_refs           JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_cursor          JSONB NOT NULL DEFAULT '{}'::jsonb,
    selected_candidate_id TEXT,
    unresolved_slots     JSONB NOT NULL DEFAULT '[]'::jsonb,
    expires_at           TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
