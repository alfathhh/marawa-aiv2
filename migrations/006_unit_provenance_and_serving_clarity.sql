-- Migration 006: unit provenance + serving-view clarity (audit 2026-08-15).
--
-- C2a  SIMDASI serving now exposes `unit_state`. A unit derived from the table
--      title by ILIKE is a heuristic, not source metadata, and is published as
--      'review_required' so neither the registry nor the formatter can present
--      it as settled. Lineage (`unit_source`) is unchanged.
-- M1   Dynamic serving dropped the duplicated `primary_dimension_*` columns.
--      They were three names for `vertical_id`/`geography_code`, which made the
--      query templates expose two parameters filtering the identical column.
--      The real category axis (`derived_variable`) is now named honestly as
--      `category_code` / `category_label`.
-- H3   DROP VIEW removes object-level grants. Every view rebuilt here re-grants
--      SELECT to marawa_runtime_ro at the end of the same transaction, and
--      scripts/check_runtime_privileges.py asserts the result. This closes the
--      "re-apply 004 after view bootstrap" manual step that previously guarded
--      read-only enforcement by memory alone.

-- ============================================================
-- 1. Dynamic serving: honest dimension naming
-- ============================================================
DROP VIEW IF EXISTS bps_serving_dynamic;
CREATE VIEW bps_serving_dynamic AS
SELECT
    f.domain,
    f.variable_id AS indicator_code,
    f.variable_label AS indicator_name,
    f.vertical_id AS geography_code,
    f.vertical_label AS geography_name,
    -- Audit M1: was primary_dimension_id/label (a copy of geography) plus
    -- secondary_dimension_id/label (the actual category). Only the category
    -- axis is a real dimension, so only it survives, under its own name.
    f.derived_variable_id AS category_code,
    f.derived_variable_label AS category_label,
    f.period_label AS period,
    CASE
        WHEN f.derived_period_label IS NOT NULL
             AND f.derived_period_label <> f.period_label
        THEN 'quarterly'
        ELSE 'annual'
    END AS period_granularity,
    f.derived_period_id AS subperiod_code,
    f.derived_period_label AS subperiod_label,
    f.value_numeric AS value,
    f.value_text,
    f.unit AS unit_raw,
    COALESCE(v.unit_canonical, f.unit) AS unit,
    CASE
        WHEN v.unit_canonical IS NOT NULL THEN 'canonical'
        WHEN f.unit IS NULL OR f.unit = '' OR f.unit = 'Tidak Ada Satuan'
        THEN 'unknown_review'
        ELSE 'known'
    END AS unit_state,
    f.snapshot_id
FROM bps_dynamic_facts f
LEFT JOIN bps_dynamic_variables v
       ON v.domain = f.domain AND v.variable_id = f.variable_id;

-- ============================================================
-- 2. SIMDASI serving: unit provenance is visible downstream
-- ============================================================
DROP VIEW IF EXISTS bps_serving_simdasi;
CREATE VIEW bps_serving_simdasi AS
SELECT
    f.region_code,
    f.table_id,
    t.table_code,
    t.title AS table_title,
    f.year AS period,
    f.geography_code,
    f.row_label AS geography_name,
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
    f.column_id AS indicator_code,
    c.name AS indicator_name,
    f.value_numeric AS value,
    f.value_text,
    f.value_code,
    f.value_note,
    CASE
        WHEN t.title ILIKE '%miliar rupiah%' THEN 'miliar rupiah'
        WHEN t.title ILIKE '%juta rupiah%' THEN 'juta rupiah'
        WHEN t.title ILIKE '%ribu rupiah%' THEN 'ribu rupiah'
        ELSE COALESCE(c.unit, u.unit, f.row_unit)
    END AS unit,
    CASE
        WHEN t.title ILIKE '%miliar rupiah%' THEN 'title_matched'
        WHEN t.title ILIKE '%juta rupiah%' THEN 'title_matched'
        WHEN t.title ILIKE '%ribu rupiah%' THEN 'title_matched'
        ELSE COALESCE(u.unit_source,
            CASE WHEN c.unit IS NOT NULL THEN 'column_meta' END)
    END AS unit_source,
    -- Audit C2a: a title-derived unit is a guess about column semantics made
    -- from a table-level string. It must never be presented as known.
    CASE
        WHEN t.title ILIKE '%miliar rupiah%'
          OR t.title ILIKE '%juta rupiah%'
          OR t.title ILIKE '%ribu rupiah%' THEN 'review_required'
        WHEN COALESCE(c.unit, u.unit, f.row_unit) IS NULL
          OR COALESCE(c.unit, u.unit, f.row_unit) IN ('', 'Tidak Ada Satuan')
        THEN 'unknown_review'
        ELSE 'known'
    END AS unit_state,
    d.source_created_at,
    f.snapshot_id
FROM bps_simdasi_facts f
JOIN bps_simdasi_tables t
  ON t.table_id = f.table_id AND t.region_code = f.region_code
LEFT JOIN bps_simdasi_columns c
  ON c.table_id = f.table_id AND c.column_id = f.column_id
LEFT JOIN bps_simdasi_units u
  ON u.table_id = f.table_id AND u.column_id = f.column_id
LEFT JOIN bps_simdasi_documents d
  ON d.table_id = f.table_id;

-- ============================================================
-- 3. Re-grant (audit H3) — DROP VIEW dropped these
-- ============================================================
GRANT SELECT ON public.bps_serving_dynamic TO marawa_runtime_ro;
GRANT SELECT ON public.bps_serving_simdasi TO marawa_runtime_ro;

-- ============================================================
-- 4. Registry: measure-level answerability gate (audit C2c)
-- ============================================================
ALTER TABLE bps_registry.measure_registry
    ADD COLUMN IF NOT EXISTS queryable boolean NOT NULL DEFAULT true;
ALTER TABLE bps_registry.measure_registry
    ADD COLUMN IF NOT EXISTS quality_flags text[] NOT NULL DEFAULT '{}';
ALTER TABLE bps_registry.measure_registry
    ADD COLUMN IF NOT EXISTS unit_source text;

-- Remove an older copy first so this migration remains idempotent on retry.
ALTER TABLE bps_registry.measure_registry
    DROP CONSTRAINT IF EXISTS measure_registry_queryable_requires_unit;

-- Backfill existing rows BEFORE adding the CHECK constraint. PostgreSQL validates
-- existing rows when ADD CONSTRAINT runs; the previous order could make migration
-- 006 fail precisely on the rows it was intended to quarantine.
UPDATE bps_registry.measure_registry
SET queryable = false,
    quality_flags = CASE
        WHEN 'unit_review_required' = ANY(quality_flags) THEN quality_flags
        ELSE array_append(quality_flags, 'unit_review_required')
    END
WHERE unit_state NOT IN ('known', 'unitless')
   OR unit_source = 'title_matched';

-- A measure may only be queryable when its unit is sourced, never guessed.
-- Enforced by the database so no future builder can regress it silently.
ALTER TABLE bps_registry.measure_registry
    ADD CONSTRAINT measure_registry_queryable_requires_unit
    CHECK (
        NOT queryable
        OR (unit_state IN ('known', 'unitless')
            AND (unit_source IS NULL OR unit_source <> 'title_matched'))
    );

-- Template registry: limit ownership is declared, never sniffed (audit H1).
ALTER TABLE bps_registry.query_template_registry
    ADD COLUMN IF NOT EXISTS has_own_limit boolean NOT NULL DEFAULT false;

-- Ledger row is recorded by scripts/run_migrations.py (checksum of this file).
