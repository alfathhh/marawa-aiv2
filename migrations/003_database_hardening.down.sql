-- Rollback migration 003. Restores single-version registry identities.
-- Destructive by design: retains only the published catalog, drops version columns
-- from child tables. Run only in an isolated/test database or after an explicit backup.

DO $rollback$
DECLARE
    keep_version text;
BEGIN
    SELECT registry_version_id INTO keep_version
    FROM bps_registry.registry_versions
    WHERE status='published'
    ORDER BY published_at DESC NULLS LAST
    LIMIT 1;

    DELETE FROM bps_registry.query_template_registry WHERE registry_version_id <> keep_version;
    DELETE FROM bps_registry.dimension_item_registry WHERE registry_version_id <> keep_version;
    DELETE FROM bps_registry.dimension_registry WHERE registry_version_id <> keep_version;
    DELETE FROM bps_registry.measure_registry WHERE registry_version_id <> keep_version;
    DELETE FROM bps_registry.dataset_registry WHERE registry_version_id <> keep_version;
END
$rollback$;

-- 1. Drop every versioned FK/PK dependency first (children before parents).
ALTER TABLE bps_registry.query_template_registry DROP CONSTRAINT IF EXISTS query_template_registry_version_fk;
ALTER TABLE bps_registry.dimension_item_registry DROP CONSTRAINT IF EXISTS dimension_item_registry_dimension_version_fk;
ALTER TABLE bps_registry.dimension_registry DROP CONSTRAINT IF EXISTS dimension_registry_dataset_version_fk;
ALTER TABLE bps_registry.measure_registry DROP CONSTRAINT IF EXISTS measure_registry_dataset_version_fk;
ALTER TABLE bps_registry.dataset_registry DROP CONSTRAINT IF EXISTS dataset_registry_version_fk;

-- 2. dataset_registry: restore single-column identity.
ALTER TABLE bps_registry.dataset_registry
    DROP CONSTRAINT IF EXISTS dataset_registry_pkey,
    DROP CONSTRAINT IF EXISTS dataset_registry_version_resource_key;
ALTER TABLE bps_registry.dataset_registry ADD PRIMARY KEY (dataset_id);
ALTER TABLE bps_registry.dataset_registry
    ADD UNIQUE (registry_version_id, source_family, source_resource_id);
ALTER TABLE bps_registry.dataset_registry ADD FOREIGN KEY (registry_version_id)
    REFERENCES bps_registry.registry_versions(registry_version_id);

-- 3. dimension_registry (parent of items).
ALTER TABLE bps_registry.dimension_registry
    DROP CONSTRAINT IF EXISTS dimension_registry_version_name_key,
    DROP CONSTRAINT IF EXISTS dimension_registry_pkey;
ALTER TABLE bps_registry.dimension_registry ADD PRIMARY KEY (dimension_id);
ALTER TABLE bps_registry.dimension_registry ADD UNIQUE (dataset_id, name);
ALTER TABLE bps_registry.dimension_registry ADD FOREIGN KEY (dataset_id)
    REFERENCES bps_registry.dataset_registry(dataset_id);
ALTER TABLE bps_registry.dimension_registry DROP COLUMN IF EXISTS registry_version_id;

-- 4. dimension_item_registry.
ALTER TABLE bps_registry.dimension_item_registry
    DROP CONSTRAINT IF EXISTS dimension_item_registry_version_source_key,
    DROP CONSTRAINT IF EXISTS dimension_item_registry_pkey;
ALTER TABLE bps_registry.dimension_item_registry ADD PRIMARY KEY (item_id);
ALTER TABLE bps_registry.dimension_item_registry ADD UNIQUE (dimension_id, source_item_id);
ALTER TABLE bps_registry.dimension_item_registry ADD FOREIGN KEY (dimension_id)
    REFERENCES bps_registry.dimension_registry(dimension_id);
ALTER TABLE bps_registry.dimension_item_registry DROP COLUMN IF EXISTS registry_version_id;

-- 5. measure_registry.
ALTER TABLE bps_registry.measure_registry
    DROP CONSTRAINT IF EXISTS measure_registry_version_source_key,
    DROP CONSTRAINT IF EXISTS measure_registry_pkey;
ALTER TABLE bps_registry.measure_registry ADD PRIMARY KEY (measure_id);
ALTER TABLE bps_registry.measure_registry ADD UNIQUE (dataset_id, source_measure_id);
ALTER TABLE bps_registry.measure_registry ADD FOREIGN KEY (dataset_id)
    REFERENCES bps_registry.dataset_registry(dataset_id);
ALTER TABLE bps_registry.measure_registry DROP COLUMN IF EXISTS registry_version_id;

-- 6. query_template_registry.
ALTER TABLE bps_registry.query_template_registry
    DROP CONSTRAINT IF EXISTS query_template_registry_pkey;
ALTER TABLE bps_registry.query_template_registry ADD PRIMARY KEY (template_id, template_version);
ALTER TABLE bps_registry.query_template_registry ADD FOREIGN KEY (registry_version_id)
    REFERENCES bps_registry.registry_versions(registry_version_id);
