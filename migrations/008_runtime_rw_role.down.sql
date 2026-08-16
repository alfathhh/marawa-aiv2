-- Rollback: revoke and drop the runtime rw role.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM marawa_runtime_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA bps_registry FROM marawa_runtime_rw;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM marawa_runtime_rw;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA bps_registry FROM marawa_runtime_rw;
DROP ROLE IF EXISTS marawa_runtime_rw;
