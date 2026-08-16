# MARAWA AI Implementation Plan

> **Untuk coding agents:** ikuti `AGENTS.md`, gunakan TDD, implementasikan task kecil, dan lakukan spec-compliance review lalu code-quality/security review pada tiap fase.

**Status (2026-08-15, pasca-audit):** Fase 0 sebagian selesai — BPS WebAPI mirror terbangun, serving view fixes + `bps_registry` published (`migrations/001–006`, `scripts/build_bps_registry.py`), query prototypes teruji read-only, invariant unit/binder ditegakkan tes. Fase 1+ (runtime, WhatsApp, dashboard) belum dimulai. Kebijakan: tidak ada cronjob BPS; update data manual via katalog Excel; dataset selection user-confirmed sebelum fact query. Angka status canonical hanya di `docs/25`.

> **Rencana ini di-rescope 15 Agustus (audit C3).** Sepuluh fase berurutan di
> bawah adalah rencana waterfall untuk produk penuh, disusun sebelum ada satu
> pun approver yang tanda tangan dan sebelum ada satu pun pertanyaan pengguna
> nyata masuk. Jalur kritis sekarang adalah **Slice 1** di `docs/01-PRD` §11;
> fase-fase di bawah tetap sebagai referensi untuk pekerjaan sesudahnya, bukan
> sebagai urutan yang harus dikerjakan sekarang.

## Jalur kritis saat ini (menggantikan urutan fase di bawah)

| # | Aksi | Estimasi | Membuka |
|---|---|---|---|
| 1 | ✅ Fix invariant unit (C2a/b/c, M3) + binder (H1/H2) + privilege assertion (H3) | selesai 15 Aug | integritas angka |
| 2 | Rebuild registry dengan builder baru; review delta measure yang jadi non-queryable | 0,5 hari | registry v2 jujur |
| 3 | **Kumpulkan 30 pertanyaan nyata di loket PST (3 hari, paralel)** | 3 hari | sinyal eksternal pertama |
| 4 | Jalankan scorer terhadap set nyata; laporkan apa adanya | 0,5 hari | baseline pertama yang berarti |
| 5 | Probe kapabilitas model dengan API key kuota kecil (de-risk OQ-05) | 0,5 hari | argumen untuk procurement |
| 6 | Bangun Slice 1 (`docs/01-PRD` §11) | 2 minggu | produk yang berjalan |
| 7 | Uji ke 5 pegawai BPS, bukan publik | 1 minggu | sinyal eksternal kedua |

Apa pun yang bukan prasyarat langkah berikutnya: tunda.

### Slice 1 mencakup inbox dashboard (keputusan 15 Agt)

Petugas menjawab dari dashboard, bukan dari HP. Cakupan minimum di `docs/06` §0.
Konsekuensi jadwal: Slice 1 bukan lagi ~2 minggu melainkan **~4–5 minggu**, dan
keamanan data warga naik dari "Slice 2" menjadi prasyarat rilis.

Urutan build yang disarankan — bot dulu sampai bisa menjawab, baru inbox, karena
inbox yang tidak punya percakapan untuk ditampilkan tidak bisa diuji:

| Minggu | Fokus | Selesai berarti |
|---|---|---|
| 1 | Kanal + state machine + outbox | pesan masuk tersimpan, bot balas satu kalimat statis, state berpindah benar |
| 2 | Agent path | discovery → pilih tabel → query → jawaban bersumber, lewat `answer_gate` |
| 3 | Auth + daftar chat + thread (read-only) | petugas bisa login dan membaca; belum bisa membalas |
| 4 | Balas + claim + return + SSE + notifikasi | handover utuh |
| 5 | Retensi, audit log, uji race, uji ke 5 pegawai | siap uji internal |

Retensi dan audit log berada di minggu 5 hanya karena butuh data untuk diuji —
**bukan** karena boleh dilewati. Keduanya prasyarat sebelum nomor publik
disebarkan.

Anti-jailbreak (Fase 9) tetap di luar jalur kritis sampai data internal (OQ-07)
tersambung. Yang naik prioritas adalah keamanan data warga, bukan ketahanan
prompt.

## Anti-jailbreak suite dicabut dari jalur kritis

Fase 9.1 mensyaratkan effect-oracle environment plus corpus benign/static/
mutation/adaptive/repeated/cross-provider sebagai **release gate produksi**. Itu
proyek berbulan-bulan sendiri. Blast radius Slice 1: bot read-only yang
menyajikan data yang sudah publik di `webapi.bps.go.id`, tanpa data internal
(OQ-07 masih blocked), tanpa dashboard, tanpa kemampuan menulis apa pun.

Yang **tetap wajib** di Slice 1 karena murah dan sudah efektif: tidak ada raw
SQL, template parameterized via binder, role read-only + assertion privilege,
system prompt/konfigurasi internal tidak pernah keluar, dan abstain saat bukti
kurang. `docs/09A/09B/09C` tetap tersimpan utuh sebagai desain untuk Slice 2+,
saat dashboard dan data internal benar-benar tersambung dan blast radius-nya
naik.

**Goal:** Menghasilkan MVP MARAWA AI end-to-end yang menerima pertanyaan WhatsApp, mengambil evidence statistik secara aman, menjawab ter-grounding, dan dapat diambil alih petugas melalui dashboard.

**Architecture:** FastAPI orchestrator, Next.js dashboard, Node/Baileys adapter, app PostgreSQL/pgvector, Redis, dan source PostgreSQL read-only, semuanya Docker Compose pada VPS.

**Success gate:** Semua mandatory invariants pada `10-TEST-EVALUATION.md` lulus, UAT PST sign-off, staging WhatsApp smoke lulus, backup restore drill lulus, dan security/privacy checklist disetujui.

### Fase completion map (audit 2026-08-15)

| Fase/task | Status aktual | Evidence |
|---|---|---|
| 0.1 Stakeholder sign-off | **blocked** | OQ-01–OQ-12 belum seluruhnya resolved |
| 0.2 Approved internal source DB | **blocked untuk internal views** | public WebAPI mirror/registry built; OQ-07 dipersempit |
| 0.3 Provider/WhatsApp prerequisites | **blocked** | exact model/provider/number belum final |
| 0.4 BPS WebAPI mirror | **complete** | mirror + serving fixes + registry published; hitungan tes canonical di `docs/25` |
| 1.1–1.3 Runtime scaffold/contracts | **partial planning artifacts only** | query JSON schema/eval fixtures ada; FastAPI/TS monorepo belum dibangun |
| 2–5 | **not started** | identity, channel, dashboard, RAG runtime belum dibangun |
| 6.1–6.2 | **database foundation complete, runtime compiler not started** | serving views, `bps_registry`, six query templates |
| 6.3–6.4 | **planning only** | evidence/result/calculation contracts belum diimplementasi |
| 7–10 | **planning only** | agent/model/eval/deploy belum dibangun |

Database-only next work: **selesai.** Census category-item registry dan SIMDASI display-label cleanup sudah rampung, dan remediasi audit 15 Agustus menutup sisa cacat integritas unit. Pekerjaan database berikutnya hanya rebuild registry + review delta measure. Fokus berpindah ke langkah 3 pada jalur kritis di atas: sinyal eksternal.

---

## Fase 0 — Discovery, policy, dan data contract

### Task 0.1 — Stakeholder sign-off PRD

**Files:** review `docs/01-PRD.md`, update `docs/15-OPEN-QUESTIONS.md`.

- Isi owner, SLA, service hours, form URL, contact/branding, traffic/capacity.
- Setujui retention purpose dan access policy.
- **Verify:** PRD sign-off table complete; tidak ada production blocker yang belum diputuskan.

### Task 0.4 — Bootstrap BPS WebAPI mirror

- Provision isolated local PostgreSQL, external `0600` secrets, raw/current/serving schemas.
- Crawl SIMDASI `1306000`, Dynamic `1306`, Census local scope, Publication `1306`, and global Glosarium per `17-BPS-WEBAPI-DATA.md`.
- Download publication binaries only after capacity plan; persist file SHA-256/status.
- Generate actual exploration/quality report and close all unexplained coverage gaps or mark them upstream-not-available.
- **TDD:** parser/client/storage/crawler/formatter tests fail first; integration uses real PostgreSQL and cleans fixtures.
- **Verify:** rerun idempotent, resume works, secret scan pass, all full-crawl resources checkpointed, report counts match SQL.

### Task 0.2 — Inventory source DB

- Data owner membuat list approved datasets/views dan prohibited fields.
- Tulis data dictionary dan sample frozen outputs.
- **Verify:** role read-only cannot DML/DDL; views contain no individual/unreleased data.

### Task 0.3 — Provider/WhatsApp prerequisites

- Get Gemini/DeepSeek/BPS API credentials via secret process.
- Confirm exact primary model ID with capability probe plan.
- Provision dedicated WhatsApp test/production numbers.
- **Verify:** provider terms/privacy approved; test accounts available.

## Fase 1 — Monorepo, contracts, infrastructure

### Task 1.1 — Scaffold repository

**Create:** `apps/api`, `apps/dashboard`, `apps/whatsapp-worker`, `workers/ingestion`, `packages/contracts`, `packages/prompts`, `packages/evals`, `infra`, `tests`.

- Configure Python 3.12/uv, pnpm workspaces, linters, strict typing, test runners.
- **Verify:** clean build/lint/test skeleton in CI.

### Task 1.2 — Docker dev stack

**Create:** `compose.yaml`, `infra/docker/*`, `.env.example` adaptation.

- App Postgres+pgvector, Redis, API, dashboard, worker mocks.
- **Verify:** one command starts stack; healthchecks green; no DB ports public.

### Task 1.3 — Shared contracts

**Create:** JSON schemas/OpenAPI event schemas and generated TS types.

- Webhook, outbound command, error envelope, SSE events.
- **TDD:** schema valid/invalid compatibility tests.

## Fase 2 — Identity, RBAC, audit

### Task 2.1 — App DB migrations

**Create:** Alembic schema for identity/audit/session.

- **TDD:** migration up/down; uniqueness/FK/index tests.

### Task 2.2 — Login/password/TOTP/session

- Argon2id, enrollment, verify, recovery, session rotation/revoke.
- **TDD:** replay, expiry, lockout, recovery one-time, no enumeration.

### Task 2.3 — RBAC/object authorization

- Permission registry and policy layer.
- **TDD:** role matrix and IDOR test for every sensitive endpoint.

### Task 2.4 — Audit log

- Append-only audit service and read/export permission.
- **TDD:** sensitive operations emit expected redacted diff.

## Fase 3 — Conversation reliability and WhatsApp adapter

### Task 3.1 — Conversation/inbox/outbox schema

- Implement tables and repositories from `05-DATABASE.md`.
- **TDD:** duplicate inbox/outbox constraints and state transitions.

### Task 3.2 — Signed webhook

- HMAC, timestamp, nonce, schema, body limits, durable inbox before 202.
- **TDD:** valid, bad, expired, replayed, duplicate.

### Task 3.3 — Baileys session/normalization

- Encrypted auth store, leader lock, reconnect, event normalization.
- **TDD:** fixtures for all message variants/fromMe/group/status.

### Task 3.4 — Outbound delivery

- Redis consumer, API final authorization, idempotency, status callbacks, DLQ.
- **TDD:** duplicate/reordered receipts, restart replay, stale AI preemption.

## Fase 4 — Dashboard inbox and handover

### Task 4.1 — Dashboard shell/auth

- Login/TOTP/session UI, permission-aware navigation.
- **Verify:** server-side access denial, responsive/keyboard smoke.

### Task 4.2 — Inbox list/thread/SSE

- Cursor APIs, SSE resumability, thread rendering, redacted previews.
- **TDD:** reconnect/reset and permission-filtered events.

### Task 4.3 — Claim/reply/reassign/resolve/return

- Transactional state machine and composer/outbox.
- **TDD:** two-agent race, AI in-flight, pending admin outbound.

### Task 4.4 — Notes/canned responses/disposition

- Internal notes never sent to user/provider.
- **TDD:** permission and leakage tests.

## Fase 5 — Knowledge ingestion and retrieval

### Task 5.1 — Source/version/document/chunk schema

- Migrations + immutable versions/evidence.
- **TDD:** publish/rollback lineage.

### Task 5.2 — Connectors

- Website, PPID `mfd=1306`, WebAPI domain `1306`, approved upload.
- **TDD:** checksum, pagination, retry, source allowlist, no SSRF.

### Task 5.3 — Parsing/chunking/embedding

- Narrative/table/metadata parsing, sandbox, pgvector/FTS indexes.
- **TDD:** golden parser fixtures with page/table/footnote preservation.

### Task 5.4 — Hybrid retrieval/rerank

- FTS + vector + filters + fusion/rerank.
- **Verify:** Recall@5 threshold on frozen corpus.

### Task 5.5 — Knowledge dashboard

- Preview/review/approve/publish/quarantine/rollback + job progress.
- **TDD:** production approval and audit permissions.

## Fase 6 — Structured statistics tools

### Task 6.1 — Source RO adapter

- TLS/pool/timeout/read-only transaction.
- **TDD:** DML/DDL denied; timeout and pool limits.

### Task 6.2 — Dataset registry/compiler

- Typed schema, allowlisted identifiers, parameterized query.
- **TDD:** SQL injection/property tests, row limit, unknown filter rejection.

### Task 6.3 — Evidence/result normalization

- typed values, units, periods, notes, citation template, immutable snapshot.
- **TDD:** null/missing/suppressed/provisional/revised values.

### Task 6.4 — Calculation tools

- Formula registry with evidence lineage.
- **TDD:** rounding and percentage vs percentage-point cases.

## Fase 7 — Agent/model orchestration

### Task 7.1 — Provider adapters and capability probe

- Gemini and DeepSeek adapters, exact configurable IDs, timeout/error mapping.
- **TDD:** mocked responses, schema repair, retry/fallback matrix.

### Task 7.2 — Session context and typed working memory

- Recent transcript + structured compaction, goal/indicator/geography/period/result references, validated memory patches, topic reset.
- **TDD:** multi-turn coreference, stale context, compaction fidelity, fake reference rejection.

### Task 7.3 — Bounded planner/tool observation loop

- Server-side tool registry, plan/action schema, observations, step/wall budget, stop reason, strategy change guard.
- **TDD:** multi-tool sequence, empty result retry with changed strategy, identical-call loop rejection, async handoff.

### Task 7.4 — Statistical skills

- Versioned skill registry for discovery, comparison, trend, composition, ranking, distribution, correlation, publication, methodology, dan PST service.
- **TDD:** trigger/prerequisite/tool sequence/caveat/evaluation; skill cannot add permission.

### Task 7.5 — Analysis worker and artifacts

- Typed methods + isolated sandbox, analysis jobs, lineage, chart/table/CSV/XLSX/PDF artifacts.
- **TDD:** reproducibility, resource/network/filesystem isolation, chart/export from active result IDs.

### Task 7.6 — Answer generation/grounding validator

- Implement `AGENT.md` structured envelope and numeric/citation validator.
- **TDD:** zero numeric-without-evidence, wrong period/unit/link blocked.

### Task 7.7 — Abstention/scope/handover

- Approved Indonesian copy and form/admin path.
- **TDD:** unanswerable/out-of-scope/repeated failures.

### Task 7.8 — Prompt/model/skill release workflow

- Draft/eval/approve/publish/rollback for prompts, models, and statistical skills; secret references.
- **TDD:** invalid capability/config cannot publish.

## Fase 8 — Quality, analytics, observability

### Task 8.1 — Multi-turn golden eval harness

- Conversation episodes, frozen corpus/dataset, follow-up resolution, plan/tool/analysis/artifact checks, per-model reports, regression budget.
- **Verify:** initial thresholds reached.

### Task 8.2 — Feedback/content gaps

- Store/triage and dashboard views; no automatic corpus promotion.

### Task 8.3 — Metrics/log/traces/alerts

- Implement `12-OBSERVABILITY-RUNBOOK.md` with PII redaction tests.

### Task 8.4 — Analytics dashboard

- Aggregate facts, intent/latency/handover/source/model charts.
- **TDD:** aggregate permission and pseudonymization.

## Fase 9 — Security/resilience/performance

### Task 9.1 — Security suite

- SAST/dependency/container/secret scan, DAST, RBAC/CSRF, dan anti-jailbreak suite.
- Implement/test task contracts, value provenance/taint, bounded declassification, capability broker, Rule-of-Two gate, memory/output/destination guards, dan fallback parity.
- Build deterministic effect-oracle environment serta benign/static/mutation/adaptive/repeated/cross-provider corpus sesuai `09B-ANTI-JAILBREAK-REDTEAM.md`.
- **TDD:** forged/expired/cross-conversation capability, tainted forbidden sink, destination smuggling, guard failure, memory poisoning, artifact injection, dan A+B+C autonomous run gagal sebelum effect.
- **Verify:** hard invariants 100%; no observed critical Effect-ASR pada exact release; report UTR, Draft-ASR, containment, attack budget/repetitions/confidence bound, and human scorer validation.

### Task 9.2 — Load/chaos scenarios

- Provider/Redis/API/source DB/WhatsApp failures and queue replay.
- **Verify:** SLO/load baseline and no lost/duplicate effect.

### Task 9.3 — Backup/restore/key rotation

- Automate encrypted backup and perform real restore/rotation drills.
- **Verify:** actual RPO/RTO recorded.

## Fase 10 — Staging, pilot, production

### Task 10.1 — Staging deployment

- Deploy exact images, real provider test project, test WhatsApp number, sanitized/frozen data.

### Task 10.2 — PST UAT and operator training

- Inbox/takeover, wrong-answer process, re-pair, source publish, incident runbook.

### Task 10.3 — Limited pilot

- Controlled users/hours, close monitoring, daily content gap review.

### Task 10.4 — Production readiness review

- All open blockers closed, retention/provider approval, restore, security, eval, capacity, on-call.

### Task 10.5 — Production launch and hypercare

- Release, smoke, observe, daily review for initial period, rollback ready.

## Recommended commit boundaries

Satu task kecil/behavior per commit: `feat(api): ...`, `feat(wa): ...`, `test(agent): ...`, `docs: ...`, `security: ...`. Jangan gabungkan migrations, major prompt changes, dan unrelated UI refactor.
