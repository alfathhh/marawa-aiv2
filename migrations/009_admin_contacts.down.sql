-- Down for migration 009.
REVOKE ALL ON SEQUENCE public.marawa_admin_contacts_contact_id_seq FROM marawa_runtime_rw;
REVOKE ALL ON TABLE public.marawa_admin_contacts FROM marawa_runtime_rw;
DROP INDEX IF EXISTS idx_marawa_conversations_inbox;
ALTER TABLE marawa_conversations DROP COLUMN IF EXISTS is_staff_channel;
DROP TABLE IF EXISTS marawa_admin_contacts;
