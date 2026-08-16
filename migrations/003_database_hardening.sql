-- Migration 003: database hardening.
-- - migration ledger
-- - versioned registry identities preserving retired snapshots
-- - blocked-quality reason persistence support (column already exists)

CREATE SCHEMA IF NOT EXISTS bps_registry;

-- Candidate sets were not in use; clear draft runtime state before FK changes.
TRUNCATE TABLE bps_registry.candidate_sets;

-- Remove current single-version foreign keys and uniqueness constraints.
ALTER TABLE bps_registry.query_template_registry
    DROP CONSTRAINT IF EXISTS query_template_registry_registry_version_id_fkey;
ALTER TABLE bps_registry.dimension_item_registry
    DROP CONSTRAINT IF EXISTS dimension_item_registry_dimension_id_fkey;
ALTER TABLE bps_registry.dimension_registry
    DROP CONSTRAINT IF EXISTS dimension_registry_dataset_id_fkey;
ALTER TABLE bps_registry.measure_registry
    DROP CONSTRAINT IF EXISTS measure_registry_dataset_id_fkey;
ALTER TABLE bps_registry.dataset_registry
    DROP CONSTRAINT IF EXISTS dataset_registry_registry_version_id_fkey;

ALTER TABLE bps_registry.dataset_registry
    DROP CONSTRAINT IF EXISTS dataset_registry_registry_version_id_source_family_source_res_key;
ALTER TABLE bps_registry.measure_registry
    DROP CONSTRAINT IF EXISTS measure_registry_dataset_id_source_measure_id_key;
ALTER TABLE bps_registry.dimension_registry
    DROP CONSTRAINT IF EXISTS dimension_registry_dataset_id_name_key;
ALTER TABLE bps_registry.dimension_item_registry
    DROP CONSTRAINT IF EXISTS dimension_item_registry_dimension_id_source_item_id_key;

-- Every versioned child carries registry_version_id explicitly.
ALTER TABLE bps_registry.measure_registry ADD COLUMN IF NOT EXISTS registry_version_id text;
ALTER TABLE bps_registry.dimension_registry ADD COLUMN IF NOT EXISTS registry_version_id text;
ALTER TABLE bps_registry.dimension_item_registry ADD COLUMN IF NOT EXISTS registry_version_id text;

UPDATE bps_registry.measure_registry m
SET registry_version_id = d.registry_version_id
FROM bps_registry.dataset_registry d
WHERE m.dataset_id = d.dataset_id AND m.registry_version_id IS NULL;
UPDATE bps_registry.dimension_registry x
SET registry_version_id = d.registry_version_id
FROM bps_registry.dataset_registry d
WHERE x.dataset_id = d.dataset_id AND x.registry_version_id IS NULL;
UPDATE bps_registry.dimension_item_registry i
SET registry_version_id = x.registry_version_id
FROM bps_registry.dimension_registry x
WHERE i.dimension_id = x.dimension_id AND i.registry_version_id IS NULL;

ALTER TABLE bps_registry.measure_registry ALTER COLUMN registry_version_id SET NOT NULL;
ALTER TABLE bps_registry.dimension_registry ALTER COLUMN registry_version_id SET NOT NULL;
ALTER TABLE bps_registry.dimension_item_registry ALTER COLUMN registry_version_id SET NOT NULL;

-- Replace global IDs with per-version identities.
ALTER TABLE bps_registry.dimension_item_registry DROP CONSTRAINT IF EXISTS dimension_item_registry_pkey;
ALTER TABLE bps_registry.dimension_registry DROP CONSTRAINT IF EXISTS dimension_registry_pkey;
ALTER TABLE bps_registry.measure_registry DROP CONSTRAINT IF EXISTS measure_registry_pkey;
ALTER TABLE bps_registry.dataset_registry DROP CONSTRAINT IF EXISTS dataset_registry_pkey;

ALTER TABLE bps_registry.dataset_registry
    ADD PRIMARY KEY (registry_version_id, dataset_id),
    ADD CONSTRAINT dataset_registry_version_resource_key
      UNIQUE (registry_version_id, source_family, source_resource_id),
    ADD CONSTRAINT dataset_registry_version_fk
      FOREIGN KEY (registry_version_id)
      REFERENCES bps_registry.registry_versions(registry_version_id);

ALTER TABLE bps_registry.measure_registry
    ADD PRIMARY KEY (registry_version_id, measure_id),
    ADD CONSTRAINT measure_registry_version_source_key
      UNIQUE (registry_version_id, dataset_id, source_measure_id),
    ADD CONSTRAINT measure_registry_dataset_version_fk
      FOREIGN KEY (registry_version_id, dataset_id)
      REFERENCES bps_registry.dataset_registry(registry_version_id, dataset_id);

ALTER TABLE bps_registry.dimension_registry
    ADD PRIMARY KEY (registry_version_id, dimension_id),
    ADD CONSTRAINT dimension_registry_version_name_key
      UNIQUE (registry_version_id, dataset_id, name),
    ADD CONSTRAINT dimension_registry_dataset_version_fk
      FOREIGN KEY (registry_version_id, dataset_id)
      REFERENCES bps_registry.dataset_registry(registry_version_id, dataset_id);

ALTER TABLE bps_registry.dimension_item_registry
    ADD PRIMARY KEY (registry_version_id, item_id),
    ADD CONSTRAINT dimension_item_registry_version_source_key
      UNIQUE (registry_version_id, dimension_id, source_item_id),
    ADD CONSTRAINT dimension_item_registry_dimension_version_fk
      FOREIGN KEY (registry_version_id, dimension_id)
      REFERENCES bps_registry.dimension_registry(registry_version_id, dimension_id);

-- Template versions are scoped to a catalog publication.
ALTER TABLE bps_registry.query_template_registry DROP CONSTRAINT IF EXISTS query_template_registry_pkey;
ALTER TABLE bps_registry.query_template_registry
    ADD PRIMARY KEY (registry_version_id, template_id, template_version),
    ADD CONSTRAINT query_template_registry_version_fk
      FOREIGN KEY (registry_version_id)
      REFERENCES bps_registry.registry_versions(registry_version_id);

-- Ledger row is recorded by scripts/run_migrations.py (checksum of this file).
