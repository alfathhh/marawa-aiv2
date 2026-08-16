-- Migration 001: BPS serving view semantic fixes (P0).
-- Adds subperiod semantics to Dynamic, row_role to SIMDASI, and
-- title-derived currency units for SIMDASI tables.

-- ============================================================
-- 1. Dynamic serving: preserve subperiod + dimension semantics
-- ============================================================
DROP VIEW IF EXISTS bps_serving_dynamic;
CREATE VIEW bps_serving_dynamic AS
SELECT
    f.domain,
    f.variable_id AS indicator_code,
    f.variable_label AS indicator_name,
    f.vertical_id AS geography_code,
    f.vertical_label AS geography_name,
    f.vertical_id AS primary_dimension_id,
    f.vertical_label AS primary_dimension_label,
    f.derived_variable_id AS secondary_dimension_id,
    f.derived_variable_label AS secondary_dimension_label,
    f.period_label AS period,
    CASE
        WHEN f.derived_period_label IS NOT NULL
             AND f.derived_period_label <> f.period_label
        THEN 'quarterly'
        ELSE 'annual'
    END AS period_granularity,
    f.derived_period_id AS subperiod_code,
    f.derived_period_label AS subperiod_label,
    f.derived_variable_label AS category,
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
-- 2. SIMDASI serving: row_role + title-derived currency units
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
    d.source_created_at,
    f.snapshot_id
FROM bps_simdasi_facts f
JOIN bps_simdasi_tables t
  ON t.region_code = f.region_code AND t.table_id = f.table_id
LEFT JOIN bps_simdasi_columns c
  ON c.region_code = f.region_code AND c.table_id = f.table_id
 AND c.year = f.year AND c.column_id = f.column_id
LEFT JOIN bps_simdasi_units u
  ON u.region_code = f.region_code AND u.table_code = t.table_code
 AND u.column_name = c.name
LEFT JOIN bps_simdasi_details d
  ON d.region_code = f.region_code AND d.table_id = f.table_id
 AND d.year = f.year;
