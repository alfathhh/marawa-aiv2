-- Down for migration 007. Drops the runtime conversation tables.
-- WARNING: this destroys live conversation history and outbox state.
DROP TRIGGER IF EXISTS trg_marawa_guard_last_superadmin ON marawa_admins;
DROP FUNCTION IF EXISTS marawa_guard_last_superadmin();
DROP TABLE IF EXISTS marawa_audit_log;
DROP TABLE IF EXISTS marawa_settings;
DROP TABLE IF EXISTS marawa_admins;
DROP TABLE IF EXISTS marawa_outbox;
DROP TABLE IF EXISTS marawa_messages;
DROP TABLE IF EXISTS marawa_conversations;
