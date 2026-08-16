-- Migration 008: runtime read-write role for the conversation store.
-- The API process (conversation state, outbox, admins, settings, audit) writes
-- marawa_* tables; the agent also reads serving views + registry for answers.
-- Least privilege: rw role gets nothing beyond these two surfaces.
-- Password is supplied at deploy time; this file contains no secret.

DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='marawa_runtime_rw') THEN
        CREATE ROLE marawa_runtime_rw
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END
$role$;

ALTER ROLE marawa_runtime_rw NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE marawa_runtime_rw SET statement_timeout = '10s';
ALTER ROLE marawa_runtime_rw SET lock_timeout = '2s';
ALTER ROLE marawa_runtime_rw SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE marawa_runtime_rw SET search_path = public,bps_registry;

REVOKE ALL ON SCHEMA public FROM marawa_runtime_rw;
REVOKE ALL ON SCHEMA bps_registry FROM marawa_runtime_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM marawa_runtime_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA bps_registry FROM marawa_runtime_rw;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM marawa_runtime_rw;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA bps_registry FROM marawa_runtime_rw;

DO $db$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO marawa_runtime_rw', current_database());
END
$db$;

GRANT USAGE ON SCHEMA public, bps_registry TO marawa_runtime_rw;

-- conversation store surface
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_conversations TO marawa_runtime_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_messages TO marawa_runtime_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_outbox TO marawa_runtime_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_admins TO marawa_runtime_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_settings TO marawa_runtime_rw;
-- audit log: append-only by grant — INSERT + SELECT only, no UPDATE/DELETE
-- (docs/06 §3.0b: an audit log a privileged user can edit answers nothing).
GRANT SELECT, INSERT ON public.marawa_audit_log TO marawa_runtime_rw;
GRANT USAGE ON SEQUENCE marawa_messages_message_id_seq TO marawa_runtime_rw;
GRANT USAGE ON SEQUENCE marawa_audit_log_audit_id_seq TO marawa_runtime_rw;

-- data surface (agent read path)
GRANT SELECT ON public.bps_serving_dynamic TO marawa_runtime_rw;
GRANT SELECT ON public.bps_serving_simdasi TO marawa_runtime_rw;
GRANT SELECT ON public.bps_serving_census TO marawa_runtime_rw;
GRANT SELECT ON public.bps_publications TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.registry_versions TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.dataset_registry TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.measure_registry TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.dimension_registry TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.dimension_item_registry TO marawa_runtime_rw;
GRANT SELECT ON bps_registry.query_template_registry TO marawa_runtime_rw;
