-- Down migration 002: remove the bps_registry schema entirely.
-- Destructive: drops registry, geography, templates, and candidate sets.
-- Data is rebuildable via scripts/build_bps_registry.py.

DROP SCHEMA IF EXISTS bps_registry CASCADE;
