-- Down migration 001: restore pre-fix serving view column shapes.
-- Legacy views lacked subperiod/row_role/unit_state and mislabeled category
-- rows as kecamatan. Used only for rollback drills in isolated databases.

DROP VIEW IF EXISTS bps_serving_dynamic;
CREATE VIEW bps_serving_dynamic AS
SELECT f.domain, f.variable_id AS indicator_code, f.variable_label AS indicator_name,
       f.vertical_id AS geography_code, f.vertical_label AS geography_name,
       f.derived_variable_id AS category, f.derived_variable_label AS category_name,
       f.period_label AS period,
       COALESCE(v.unit_canonical, f.unit) AS unit,
       CASE WHEN v.unit_canonical IS NOT NULL THEN 'canonical' ELSE 'raw' END AS unit_source,
       f.value_numeric, f.value_text, f.snapshot_id
FROM bps_dynamic_facts f
LEFT JOIN bps_dynamic_variables v
       ON v.domain = f.domain AND v.variable_id = f.variable_id;

DROP VIEW IF EXISTS bps_serving_simdasi;
CREATE VIEW bps_serving_simdasi AS
SELECT f.region_code, f.table_id, t.table_code, t.title AS table_title,
       f.year AS period, f.geography_code, f.row_label AS geography_name,
       CASE WHEN f.geography_code = f.region_code THEN 'kabupaten' ELSE 'kecamatan' END AS geography_level,
       f.column_id AS indicator_code, c.name AS indicator_name,
       f.value_numeric, f.value_text, f.value_code, f.value_note,
       COALESCE(c.unit, u.unit, f.row_unit) AS unit,
       CASE WHEN c.unit IS NOT NULL THEN 'column_meta'
            WHEN u.unit IS NOT NULL THEN 'unit_registry'
            ELSE 'row'
       END AS unit_source,
       f.snapshot_id
FROM bps_simdasi_facts f
JOIN bps_simdasi_tables t
  ON t.region_code = f.region_code AND t.table_id = f.table_id
LEFT JOIN bps_simdasi_columns c
  ON c.region_code = f.region_code AND c.table_id = f.table_id
 AND c.year = f.year AND c.column_id = f.column_id
LEFT JOIN bps_simdasi_units u
  ON u.region_code = f.region_code AND u.table_code = t.table_code
 AND u.column_name = c.name;
