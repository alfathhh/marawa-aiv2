-- Migration 004: runtime role least privilege.
-- Password is supplied at apply time; this file contains no secret.

DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='marawa_runtime_ro') THEN
        CREATE ROLE marawa_runtime_ro
            LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END
$role$;

ALTER ROLE marawa_runtime_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE marawa_runtime_ro SET default_transaction_read_only = on;
ALTER ROLE marawa_runtime_ro SET statement_timeout = '5s';
ALTER ROLE marawa_runtime_ro SET lock_timeout = '1s';
ALTER ROLE marawa_runtime_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE marawa_runtime_ro SET search_path = public,bps_registry;

REVOKE ALL ON SCHEMA public FROM marawa_runtime_ro;
REVOKE ALL ON SCHEMA bps_registry FROM marawa_runtime_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM marawa_runtime_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA bps_registry FROM marawa_runtime_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM marawa_runtime_ro;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA bps_registry FROM marawa_runtime_ro;

DO $db$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO marawa_runtime_ro', current_database());
END
$db$;
GRANT USAGE ON SCHEMA public, bps_registry TO marawa_runtime_ro;
GRANT SELECT ON public.bps_serving_dynamic TO marawa_runtime_ro;
GRANT SELECT ON public.bps_serving_simdasi TO marawa_runtime_ro;
GRANT SELECT ON public.bps_serving_census TO marawa_runtime_ro;
GRANT SELECT ON public.bps_publications TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.registry_versions TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.dataset_registry TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.measure_registry TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.dimension_registry TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.dimension_item_registry TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.geography_registry TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.geography_aliases TO marawa_runtime_ro;
GRANT SELECT ON bps_registry.query_template_registry TO marawa_runtime_ro;

ALTER DEFAULT PRIVILEGES FOR ROLE marawa_ingest IN SCHEMA public
    REVOKE ALL ON TABLES FROM marawa_runtime_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE marawa_ingest IN SCHEMA bps_registry
    REVOKE ALL ON TABLES FROM marawa_runtime_ro;
