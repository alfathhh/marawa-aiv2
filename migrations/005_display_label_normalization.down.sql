-- Rollback migration 005.
ALTER TABLE bps_registry.dimension_item_registry
    DROP COLUMN IF EXISTS display_label,
    DROP COLUMN IF EXISTS normalization_rule,
    DROP COLUMN IF EXISTS label_raw;
