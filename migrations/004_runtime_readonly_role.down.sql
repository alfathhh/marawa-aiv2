-- Rollback migration 004.
-- Role is cluster-global: privileges granted in other databases can keep it
-- alive, so the drop is best-effort after revoking everything here.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM marawa_runtime_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA bps_registry FROM marawa_runtime_ro;
REVOKE ALL ON SCHEMA public, bps_registry FROM marawa_runtime_ro;
DO $db$
BEGIN
    EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM marawa_runtime_ro', current_database());
END
$db$;
DO $drop$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='marawa_runtime_ro') THEN
        BEGIN
            DROP ROLE marawa_runtime_ro;
        EXCEPTION WHEN dependent_objects_still_exist THEN
            RAISE NOTICE 'marawa_runtime_ro kept: privileges remain in other databases';
        END;
    END IF;
END
$drop$;
