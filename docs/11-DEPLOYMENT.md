# Deployment — Docker VPS

## 1. Target

Single hardened Linux VPS menjalankan Docker Compose. Arsitektur ini memprioritaskan kesederhanaan dan recovery yang jelas. Database sumber PostgreSQL existing tetap berada di lokasi/network miliknya dan diakses read-only.

## 2. Prerequisites

- Domain final dan DNS.
- VPS sizing hasil load test; baseline awal yang layak untuk pilot: 4 vCPU, 8–16 GB RAM, SSD 100+ GB, lalu ukur.
- Docker Engine + Compose plugin.
- Firewall 80/443 publik; SSH restricted; admin monitoring via VPN/IP allowlist.
- Provider/BPS API credentials dan WhatsApp dedicated number.
- Backup object target/remote host.
- SMTP/notification integration bila dipakai.

## 3. Compose layout

```yaml
services:
  caddy:
    image: caddy:<pinned>
    ports: ["80:80", "443:443"]
    networks: [edge, app]

  api:
    image: registry/marawa-api:${IMAGE_TAG}
    env_file: /etc/marawa/api.env
    networks: [app, data]
    read_only: true

  dashboard:
    image: registry/marawa-dashboard:${IMAGE_TAG}
    networks: [app]
    read_only: true

  wa-worker:
    image: registry/marawa-wa:${IMAGE_TAG}
    env_file: /etc/marawa/wa.env
    volumes:
      - wa_state:/var/lib/marawa/wa-state
    networks: [app]

  ingestion-worker:
    image: registry/marawa-ingestion:${IMAGE_TAG}
    env_file: /etc/marawa/ingestion.env
    networks: [app, data]

  analysis-worker:
    image: registry/marawa-analysis:${IMAGE_TAG}
    env_file: /etc/marawa/analysis.env
    networks: [data]
    read_only: true
    # non-root; no external egress; strict CPU/memory/pid/tmp limits

  scheduler:
    image: registry/marawa-ingestion:${IMAGE_TAG}
    command: ["scheduler"]
    networks: [app, data]

  postgres:
    image: pgvector/pgvector:<pinned-digest>
    volumes: ["pg_data:/var/lib/postgresql/data"]
    networks: [data]

  redis:
    image: redis:<pinned>
    command: ["redis-server", "/etc/redis/redis.conf"]
    networks: [data]

networks:
  edge: {}
  app: {internal: true}
  data: {internal: true}
```

Gunakan exact versions/digests pada implementation, healthchecks, memory/CPU limits, non-root users, `no-new-privileges`, cap drop, dan tmpfs untuk writable temp.

## 4. Environment contract

```env
APP_NAME=MARAWA AI
APP_ENV=production
PUBLIC_BASE_URL=https://<domain>
TZ=Asia/Jakarta

OFFICIAL_WEBSITE_URL=https://padangpariamankab.bps.go.id
OFFICIAL_PPID_URL=https://ppid.bps.go.id/?mfd=1306
BPS_DOMAIN=1306
PUBLIC_FORM_URL=<TBD>
SKD_URL=https://skd.bps.go.id/skd/p/1306

PRIMARY_PROVIDER=gemini
PRIMARY_MODEL=<exact-supported-id>
PRIMARY_API_BASE=<provider-endpoint>
PRIMARY_API_KEY_FILE=/run/secrets/primary_api_key
FALLBACK_PROVIDER=deepseek
FALLBACK_MODEL=deepseek-v4-flash
FALLBACK_API_KEY_FILE=/run/secrets/fallback_api_key
EMBEDDING_PROVIDER=<TBD>
EMBEDDING_MODEL=<TBD>
MAX_AGENT_STEPS=8
MAX_AGENT_WALL_SECONDS=45
ANALYSIS_JOB_TIMEOUT_SECONDS=120
ANALYSIS_MAX_MEMORY_MB=512
ANALYSIS_MAX_OUTPUT_BYTES=52428800

APP_DATABASE_URL=<secret>
SOURCE_DATABASE_URL=<read-only-secret>
REDIS_URL=redis://redis:6379/0
BPS_API_KEY_FILE=/run/secrets/bps_api_key

WEBHOOK_CURRENT_SECRET_FILE=/run/secrets/webhook_current
WEBHOOK_PREVIOUS_SECRET_FILE=/run/secrets/webhook_previous
PII_ENCRYPTION_KEY_FILE=/run/secrets/pii_key
WA_STATE_ENCRYPTION_KEY_FILE=/run/secrets/wa_state_key
```

## 5. Caddy/routes

- `/api/*` → FastAPI.
- `/internal/*` tidak dipublikasikan; hanya Docker app network.
- `/` → Next.js dashboard; optional VPN/IP allowlist.
- HSTS setelah TLS verified, secure headers, request limits, access log redaction.
- Health endpoint publik minimal dan tidak membocorkan dependencies/secrets.

## 6. Build/release pipeline

1. Lint/type/unit tests.
2. Integration/contract tests.
3. Security scans and SBOM.
4. Build reproducible images; tag commit + digest.
5. Push registry.
6. Deploy staging.
7. Migrations + smoke + eval.
8. Manual production approval.
9. Backup and preflight.
10. Deploy immutable tag/digest.
11. Migrations expand phase.
12. Health/smoke/WhatsApp/handover tests.
13. Monitor release window.

## 7. Database migrations

- Run one-shot migration job, not every app replica blindly.
- Check app DB backup and migration compatibility.
- Source DB is never migrated by app.
- Pgvector extension created by privileged migration/bootstrap role, not runtime role.
- Rollback plan distinguishes code rollback from irreversible data migration.

## 8. Backup

### App PostgreSQL

- Daily encrypted logical/full backup; optional WAL archiving for tighter RPO.
- Retention tiers decided operations policy.
- Monthly restore drill to isolated environment with checksums and application smoke.

## 13A. BPS WebAPI mirror deployment

- `BPS_WEBAPI_KEY` and optional authenticated proxy live in root/operator-readable secret config outside repo.
- Ingestion database/listener is localhost/internal only; production may place mirror schemas in app PostgreSQL with a separate ingestion role.
- Scheduler invokes `scripts/update_bps_webapi.sh`; the script has a nonblocking `flock` overlap guard.
- Full refresh runs `scripts/backup_bps_database.sh` first; custom-format dumps and SHA-256 sidecars are mode `0600`, default retention seven copies.
- Publication PDFs require an object-storage or protected-volume capacity plan and minimum disk reserve.
- Public API/agent receives only read-only serving views; raw snapshots/checkpoints/API-key config are not mounted into model/sandbox workers.
- Proxy is availability transport, not trust boundary; HTTPS and response schema/hash validation remain mandatory.

### Baileys auth state

- Encrypted snapshot after consistent key update; restricted access.
- Restore test on staging/test number only to avoid dual active sessions.

### Config/artifacts

- Versioned repo/images, prompt/knowledge releases in DB backup, secret backup separately encrypted.

Redis is not source of truth; no backup requirement beyond queue recovery design.

## 9. Startup order/readiness

1. Postgres/Redis healthy.
2. Migrations complete.
3. API starts, validates config, probes DB/Redis/model capabilities.
4. Dashboard starts.
5. Ingestion/scheduler starts.
6. WhatsApp worker acquires account leader lock, loads auth state, connects.
7. Caddy routes only ready services.

Readiness fails when critical dependency unavailable; liveness only indicates process health to avoid restart storms.

## 10. Deployment verification

- `/health/live` and `/health/ready`.
- Dashboard login + TOTP.
- RBAC forbidden test.
- Source DB read-only probe and one approved dataset query.
- RAG known question with expected evidence.
- Provider primary probe; forced fallback probe.
- WhatsApp test inbound/outbound with dedicated test number.
- Admin claim → confirm bot silent → reply → return bot.
- Metrics/log/alert receipt.
- Backup job success and most recent restore drill status.

## 11. Rollback

- Keep previous image digest and compatible schema.
- Stop new ingestion/config publish during incident.
- Roll back app images; database rollback only if specifically safe/tested.
- Prompt/model/knowledge have independent instant release rollback.
- Drain/cancel stale AI outbox before re-enabling bot if conversation state consistency uncertain.

## 12. Capacity controls

- Separate ingestion resource limits from chat API.
- DB connection pools bounded.
- Per-number/global rate limit.
- LLM concurrency and token budgets.
- Agent step/wall-time budgets; long analysis routed to asynchronous jobs.
- Analysis worker CPU/memory/pid/disk/output quotas and no external egress.
- Max message length/tool rows/context size.
- Disk alerts for DB, logs, uploads, backup staging.
