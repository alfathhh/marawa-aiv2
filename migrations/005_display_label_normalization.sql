-- Migration 005: display-label normalization on registry items.

ALTER TABLE bps_registry.dimension_item_registry
    ADD COLUMN IF NOT EXISTS display_label text,
    ADD COLUMN IF NOT EXISTS normalization_rule text NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS label_raw text;

UPDATE bps_registry.dimension_item_registry
SET label_raw = label
WHERE label_raw IS NULL;
