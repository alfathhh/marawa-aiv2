# Observability dan Runbook

## 1. SLOs

| SLI | Initial SLO |
|---|---|
| API availability | 99.5% monthly |
| WhatsApp processing success | 99% excluding upstream logout/ban |
| p95 end-to-end first response | ≤ 12 s ordinary queries |
| Grounded numeric response | 100% with evidence |
| Takeover silence | 100% no AI send during `ADMIN_ACTIVE` |
| Knowledge sync freshness | Per source SLA |

## 2. Metrics

### Channel

`wa_connection_state`, reconnect/logout count, inbound/outbound, send duration, failure by code, queue lag, duplicate events, auth persistence errors.

### Agent/RAG/analysis

Intent/task class, follow-up resolution, selected skill, step/tool count, repeated-tool guard, stop reason, retrieval score, result/evidence reuse, analysis method/status/reproducibility, artifact generation, working-memory patch rejection, grounding/lineage validation failure, provider/model/fallback, token/cost, dan end-to-end latency.

Anti-jailbreak metrics: task-contract ID/version, capability issue/deny/expiry/replay, provenance/taint violation, bounded-declassification reject, Rule-of-Two class, detector/guard versions, `Draft-ASR`/`Effect-ASR` pada eval traffic, containment, repeated probe state, dan final policy decision. Labels tidak menyimpan raw payload, secret, phone, atau detector threshold.

### Dashboard

Login/TOTP failure, active sessions, queue size/age, claim conflicts, handling time, SSE clients/reconnect.

### Data/infra

DB pool/query duration/timeout, Redis lag, disk/memory/CPU, ingestion state, stale sources, backup age/restore test age.

BPS WebAPI mirror metrics: run/family status, canonical requests, raw snapshot versions, bytes, WAF/non-JSON/429/5xx, retry count, checkpoint progress, expected-vs-downloaded SIMDASI table-years, variables with/without facts, census combinations, publication detail/PDF status, glossary count, last successful completion, and last known-good serving age.

Metrics labels must avoid phone/message/raw query.

## 3. Logs/traces

Structured JSON fields:

```json
{
  "timestamp":"...",
  "level":"INFO",
  "service":"api",
  "event":"agent.response.persisted",
  "request_id":"...",
  "trace_id":"...",
  "conversation_id":"opaque",
  "message_id":"opaque",
  "model_config_id":"...",
  "evidence_count":2,
  "duration_ms":1840
}
```

Distributed trace spans: webhook → inbox → queue → scope/context resolution → plan/skill → repeated tool/observation steps → analysis/artifact worker → compose → validator → memory update → DB/outbox → send. Payload content and private chain-of-thought are not captured by default.

## 4. Alerts

| Alert | Severity | Trigger example |
|---|---|---|
| WhatsApp logged out | SEV-2 | auth logout event |
| No inbound/outbound while expected traffic | SEV-3 | anomaly + connection check |
| Grounding invariant failure reached outbound guard | SEV-1/2 | any attempted ungrounded numeric send |
| Source DB unauthorized/write attempt | SEV-1 | DB audit/security event |
| Provider both unavailable | SEV-2 | circuit open both |
| Queue oldest age high | SEV-2 | > configured threshold |
| Handover AI-send prevented | SEV-3 | count spike indicates race/bug |
| Disk/DB capacity | SEV-2/3 | 80/90% |
| Backup stale/restore drill overdue | SEV-2/3 | policy threshold |
| Auth brute force/security spike | SEV-2 | rate threshold |
| Knowledge source stale/sync failed | SEV-3 | repeated failure |
| BPS mirror full/update partial | SEV-2/3 | family errors, no complete run within freshness window |
| BPS serving accidentally emptied | SEV-1 | successful prior rows replaced by zero/partial upstream response |
| Publication disk reserve threatened | SEV-2 | binary plan/download would cross configured reserve |
| Agent step loop/budget exhausted spike | SEV-3 | repeated identical actions or abnormal stop reason |
| Analysis sandbox isolation violation | SEV-1 | network/host/secret/process policy attempt |
| Working-memory lineage rejection spike | SEV-2/3 | fake/stale references or compaction defect |
| Unauthorized effect reached tool/memory/artifact/outbound | SEV-1 | any critical effect; stop affected capability/release |
| Capability/provenance invariant violation | SEV-1 | forged/cross-scope/replayed authority accepted |
| Adaptive security regression | SEV-1/2 | exact-release Effect-ASR or Rule-of-Two gate failure |

## 5. Runbook — WhatsApp disconnected

1. Confirm `wa_connection_state` and error class.
2. If transient: verify reconnect backoff and one active leader.
3. If logged out: pause outbound drain, alert authorized operator, initiate protected re-pair.
4. Do not expose QR in logs/chat.
5. Verify session persistence and send/receive with test number.
6. Drain non-expired outbox in order; expired items move dead-letter/review.
7. Record timeline/root cause and evaluate Baileys version compatibility.

## 6. Runbook — Wrong statistic/public answer

1. Pause affected dataset/source/prompt; if broad, enable global bot maintenance or force abstention.
2. Identify response, evidence snapshot, source version, dataset, prompt/model release, and tool result.
3. Determine cause: source wrong/stale, mapping/SQL, retrieval, calculation, model narration, validator gap.
4. Quarantine/rollback source or prompt/model release.
5. Add failing golden regression case.
6. Fix and run targeted + full relevant eval.
7. Notify PST supervisor and follow correction/communication procedure.
8. Re-enable and monitor.

## 7. Runbook — Primary/fallback provider outage

1. Check provider status/error classification/key/quota/network.
2. Verify circuit breaker and fallback health.
3. If fallback healthy, monitor quality/latency/cost and lower concurrency if needed.
4. If both fail, keep inbound stored; send approved maintenance/offer admin only if channel capacity permits.
5. Do not repeatedly retry non-retryable auth/config failures.
6. Restore primary via capability probe before closing circuit.

## 8. Runbook — Source DB incident

1. Ensure role remains read-only; any write/privilege anomaly = SEV-1.
2. Disable structured query tool/circuit.
3. Document RAG may continue only for still-valid approved content.
4. Check pool saturation, timeout, network, view contract changes.
5. Coordinate with source DB owner; app must not alter source schema.
6. Re-run read-only and contract probes before enable.

## 9. Runbook — Handover race/bot replies during admin

1. Immediately pause bot for affected conversation/global if systemic.
2. Inspect conversation version, handover audit, AI outbox authorization and send timestamps.
3. Cancel pending AI outbox for `ADMIN_ACTIVE` conversations.
4. Preserve evidence/logs; do not edit audit history.
5. Add concurrency regression test reproducing exact order.
6. Fix final send guard/state transition; verify 100% invariant suite.
7. Inform active admin/user with approved corrective message if necessary.

## 10. Runbook — Suspected secret/session compromise

1. SEV-1: revoke/rotate affected secret immediately.
2. Isolate service/account, preserve evidence securely.
3. For Baileys: logout linked session/re-pair after containment; rotate auth-state encryption key per procedure.
4. Rotate dependent credentials in blast-radius order.
5. Review audit/provider/source DB access and outbound messages.
6. Restore from known-good image/config; monitor.
7. Incident review and mandatory control update.

## 11. Runbook — Restore

1. Provision isolated recovery target.
2. Verify backup checksum and decrypt with controlled key access.
3. Restore app DB; run integrity checks/migration compatibility.
4. Start API without public/channel egress; run smoke/evidence/audit checks.
5. Restore WhatsApp auth only after ensuring no competing active worker.
6. Switch routing/enable egress, monitor queues.
7. Record actual RPO/RTO.

## 12. Maintenance mode

Configuration levels:

- `normal`
- `ai_disabled_admin_available`
- `read_only_dashboard`
- `full_maintenance`

Maintenance response is approved template and rate-limited. Never let LLM invent outage ETA.

## 12A. Runbook — Jailbreak/agent-hijacking effect lolos

1. Disable affected capability/tool/source/model release; bila scope belum jelas, force constrained read-only/abstention.
2. Stop pending outbound/artifacts dan invalidate affected capabilities/session references.
3. Preserve encrypted raw episode serta redacted control/data-flow trace; rotate exposed secret/canary bila ada.
4. Tentukan failed boundary: task contract, provenance/declassification, action, memory, sandbox, output, destination, atau fallback.
5. Reproduce dengan deterministic environment oracle; add exact case, semantic variants, repeated and adaptive attacks.
6. Perbaiki server-side invariant sebelum prompt/classifier tuning.
7. Re-run hard invariants, full primary/fallback suite, utility suite, dan adaptive gate sebelum re-enable.
8. Record incident, correction/public communication decision, dan monitor recurrence.

## 13. Operational cadence

- Daily: health, queue, source sync, backup, high-priority feedback.
- Weekly: content gaps, failure trends, provider/fallback, knowledge freshness.
- Monthly: restore sample, access/session review, dependency review, eval regression.
- Quarterly: RBAC review, secret rotation schedule, incident drill, retention/access review.
- Annually: threat model, provider/privacy, DR drill, data retention necessity.

## 14. Runbook — BPS change detection dan approved pull

### Manual check perubahan (bukan full crawler, bukan cron)

Tidak ada cronjob BPS aktif. Jika Tah ingin cek signal perubahan, jalankan manual:

```bash
cd /home/ubuntu/projects/marawa-ai
/home/ubuntu/.hermes/bin/uv run python scripts/check_bps_updates.py
```

Sentinel memiliki hard cap **3 HTTP request per manual run**, `max_attempts=1`, tanpa retry dan tanpa automatic pull:

1. SIMDASI table catalogue (table baru / `latest_update` / tahun baru);
2. Dynamic variable catalogue page pertama (jumlah atau metadata catalogue berubah);
3. Publication catalogue page pertama (jumlah / publication baru / revisi terbaru).

Tidak ada detail/fact/Census/PDF/Glosarium yang diambil oleh sentinel. Jika tidak ada signal, job silent. Jika ada signal, report `data/reports/bps-update-sentinel-latest.json` dikirim ke Tah; data hanya ditarik setelah approval.

### Manual targeted pull atau audit penuh

```bash
# Hanya recovery run terputus
scripts/update_bps_webapi.sh resume

# Audit penuh — manual dan eksplisit, bukan cron
scripts/update_bps_webapi.sh full
```

- `flock` mencegah overlapping update;
- mode `full` membuat verified PostgreSQL dump sebelum fetch;
- unchanged response deduplicated, changed response membuat raw version;
- normalized current rows idempotent upserts;
- `validate_bps_database.py` fail-closed sebelum report dianggap final.

**Batas kontrak:** revisi nilai Dynamic yang tidak mengubah catalogue tidak dapat dideteksi tanpa membaca endpoint fact target. Lakukan targeted refresh ketika indikator/periode itu dibutuhkan atau Tah meminta audit penuh.

Jika partial/error:

1. Preserve last known-good serving rows; jangan truncate.
2. Inspect latest `bps_ingestion_runs.summary` dan failed resource IDs.
3. Classify timeout, proxy failure, WAF/HTML, 429/5xx, non-OK JSON, schema drift, atau DB error.
4. Validate satu exact failed request dengan key-free metadata.
5. Patch parser/contract hanya setelah failing regression test.
6. Resume atau targeted pull hanya atas approval Tah.
7. Regenerate exploration report dan compare counts/coverage sebelum declare healthy.

Publication PDF download tetap action manual capacity-gated; metadata update tidak bergantung pada binary mirror.
