# MARAWA AI Planning Audit — Status dan Next Gates

> Audit date: 2026-08-15
> Scope: planning pack, live BPS database/registry, tests, cron state, and Obsidian project note.
> Policy: database changes allowed; runtime/agent/WhatsApp/dashboard remain planning unless Tah explicitly changes scope.

## Executive result

Database foundation is built and now enforces its own correctness invariants.
**The verification layer, however, was measuring itself** — corrected in the
15 Aug remediation pass. Runtime implementation still has not started, and the
project still has no external signal of any kind.

This document is the CANONICAL place for status numbers. Other documents must
link here rather than restate a figure; `scripts/validate_docs.py` now warns
when a count is quoted elsewhere, because the pack previously carried three
different test totals and two different episode counts for the same artifacts.

### Evidence strength, not a scoreboard

Every row carries the strength of its evidence. A green number whose source is
a fixture written by the author of the system under test is not the same kind
of fact as an assertion executed against PostgreSQL, and the two must never be
summed into one status line.

| Item | Result | Evidence strength |
|---|---|---|
| Database mirror | BUILT | **Strong** — live DB |
| Serving P0 fixes | BUILT + regression sentinels | **Strong** — live DB |
| `bps_registry` | 1 published; retired snapshots queryable | **Strong** — live DB |
| Query prototypes | 13/13 PASS, read-only | **Strong** — live DB |
| Unit/binder invariants (`tests/test_unit_and_binder_invariants.py`) | 15 PASS | **Strong** — failing-first |
| Runtime privilege assertions (`scripts/check_runtime_privileges.py`) | positive + negative grants | **Strong** — pg catalog |
| Candidate scorer | see note A | **Weak (synthetic)** |
| Cross-family offering | see note A | **Weak (synthetic)** |
| Golden episode harness | see note B | **Partial** |
| Cronjob BPS | 0 | **Strong** |
| Agent runtime / WhatsApp | NOT BUILT | — |

**Note A — scorer metrics are closed-loop.** The 60 utterances, their expected
answers, and the scorer were all written by the same author, and the scorer was
then calibrated against them until the metric rose (see "Scorer hardening"
below, which is a description of exactly that). The reported figures measure
agreement between the scorer and its author's expectations. They do not predict
behaviour on public questions. The runner now labels its output
`synthetic_author_written` and refuses to present the numbers unqualified.
Replace the set by dropping 30 real PST counter questions into
`data/evals/pst-real-questions.json`; the label flips to `real_pst_questions`.

**Note B — the episode harness was not testing behaviour.** Of 46 turns across
19 episodes: **15 executed** against the real offering engine, **25 blocked**
(no session-policy engine, no query runtime), **6 lint-only**. Episodes
`bps-dialog-011`, `014`, `015`, `016`, `017` have no executable assertion at
all. The previous "19/19 PASS" came from `_validation_errors()` reading the
fixture and confirming the fixture said what the fixture said. The harness now
reports the three counts separately and will not collapse them.

### The one number that matters and does not exist

External signal: **none**. No real user question has ever been run through this
system. Until that changes, no metric in this repository predicts production
behaviour.

## Live database facts

Verified from PostgreSQL `127.0.0.1:55432`, database `marawa_bps`:

| Registry object | Live count |
|---|---:|
| Published registry versions | 1 |
| Retired version rows (catalog snapshots retained & queryable) | 8+ |
| Active datasets | 1.148 |
| Measures | 1.458 |
| Dimensions | 1.026 |
| Dimension items | 7.371 |
| Canonical geographies | 18 |
| Geography aliases | 34 |
| Query templates | 6 |

Serving regression sentinels:

```text
Dynamic quarterly facts preserved       918
Dynamic visible duplicate keys          0
SIMDASI category cells mislabeled       0
SIMDASI PDRB wrong-unit rows            0
```

## Planning inventory

### Built and verified

- WebAPI mirror: SIMDASI, Dynamic, Census, Publication, raw snapshots, normalized facts, serving views.
- Serving semantic fixes in `migrations/001_serving_view_fixes.sql` and source-of-truth bootstrap `workers/ingestion/bps_storage.py`.
- Registry schema in `migrations/002_registry_schema.sql`.
- Deterministic registry builder and integrity gates in `scripts/build_bps_registry.py`.
- User-confirmed dataset selection policy: new goal → candidate list → user selection → inspect/probe → fact query.
- Candidate scoring/offering simulations (synthetic set — see note A) and 19 golden episodes / 46 turns as fixtures (see note B for how many are actually executed).
- JSON contract with mandatory selection envelope for fact queries.
- Manual-only BPS update policy; no BPS cronjob.

### Planning only

- Agent loop and provider adapters.
- Candidate search runtime backed by the published registry.
- `inspect_dataset` runtime tool.
- Query compiler using `query_template_registry`.
- Typed result normalization, evidence snapshots, deterministic formatter, and grounding validator.
- Typed working memory/candidate-set persistence in application runtime.
- Live LLM golden evaluation.
- WhatsApp adapter, inbox/outbox, dashboard, RBAC, handover, deployment, and operations.

### Open data-quality decisions

- 13 datasets are `blocked_quality` with `unit_review_required`; data owner must confirm semantics before they become answerable.
- SIMDASI category labels still contain HTML/bilingual source formatting; registry currently retains raw labels. A display-label normalization pass is still needed.
- Census `categories[]` is represented at dataset dimension level, but item-level typed Census category registry is not complete (`cardinality=0` for those draft dimensions).
- Publication identity/version handling is metadata-ready but not yet integrated into runtime pagination state.

## Contradictions corrected by this audit

The following pre-migration statements were stale and have been corrected in the project Markdown where applicable:

- 192/263 Dynamic visible duplicate keys → **0 after subperiod/code-aware serving key**.
- 3.394 SIMDASI category cells mislabeled as kecamatan → **0 after `row_role` fix**.
- 432 SIMDASI PDRB rows exposed as `Rp` → **0 after title-derived unit fix**.
- “registry not built” / “DDL not executed” → **registry is published**.
- “no schema/view/data changes in query phase” → accurate only for the query harness; database migrations are now explicitly documented as built.
- Scheduled sentinel wording → **manual check only; cron count remains 0**.

## Audit severity findings — status setelah hardening (15 Aug 2026)

### Resolved

1. ✅ **Runtime DB role isolation** — `marawa_runtime_ro` dibuat (NOSUPERUSER/NOCREATEDB/NOCREATEROLE, read-only transaction, timeouts, SELECT hanya allowlisted objects); credential terpisah di `/home/ubuntu/.config/marawa-ai/postgres-runtime.env` (0600), mutation/DDL denial teruji live.
2. ✅ **Registry version lifecycle** — semua child table PK composite `(registry_version_id, ...)`; rebuild menahan snapshot versi lama; retired catalog queryable.
3. ✅ **Blocked-quality flags** — `unit_review_required` terpersist; 13/13 blocked di versi published punya alasan eksplisit.
4. ✅ **Migration reversibility/ledger** — `marawa_migrations.schema_migrations` + `scripts/run_migrations.py` + `.down.sql` untuk `001–004`; up/down cycle teruji di DB isolated. Catatan: `ensure_schema` menghapus grant level-objek; re-apply `004_runtime_readonly_role.sql` setelah bootstrap view.
5. ✅ **Geography canonical seed by code** — builder kini memakai 18-entry code→name master + majority label observasi (bukan substring matching); fix bug lama yang menghasilkan 20 row dengan nama bentrok lintas tabel.

### High (masih terbuka)

1. **No external signal at all.** Highest-severity finding of the remediation
   pass. Every metric is closed-loop. Fix: collect 30 real questions at the PST
   counter over 3 days (no workshop needed), drop them into
   `data/evals/pst-real-questions.json`. This is the cheapest action in the
   backlog and unblocks honest measurement of everything else.
2. **Live LLM conversational evaluation** — harness executes 15 assertions
   without an LLM; mode `llm` blocked by OQ-05 (provider/model ID + quota).
3. **Session-policy engine does not exist.** `docs/27` is approved planning with
   no implementation, so 12 of 46 golden turns cannot be evaluated. Slice 1
   (below) deliberately reduces this surface instead of building it.

### Medium (masih terbuka)

2. **Internal source DB (OQ-07)** — approved internal views/data dictionary belum tersedia.
3. **Unit review data owner** — packet siap (`data/reports/bps-unit-review-*.xlsx` + `docs/26`); 13 dataset menunggu approval unit.

### Recap audit planning docs 02/04/07 (15 Aug)

- ✅ **Reconciliation vs built reality:** `docs/02-AGENT-RUNTIME` mendapat session-policy engine (idle 300s + handover SLA 180s, agent-first, pointer `docs/27`) + acceptance criteria menu non-forcing/natural cancel; `docs/04-RAG-AND-DATA` — structured data path ditandai BUILT (serving views + `marawa_runtime_ro` + binder), registry section status live (counts + candidate-set pinning), vector ANN ditandai deferred; `docs/07-WHATSAPP-WEBHOOK` — timed notices via scheduler/outbox dengan final state guard + contract tests baru.

### Resolved hari ini (batch kelima — postgres store, 16 Aug)

- ✅ **`migrations/007_runtime_conversation_tables`** (149 baris): `marawa_conversations/messages/outbox/admins/settings/audit_log`; partial unique index dedup inbound + idempotency key; trigger penjaga superadmin terakhir; indeks parsial sweep. UP/DOWN/UP diuji di DB isolated PG 16.15. Applied ke produksi (checksum `03d72bac…`), ledger 001–007.
- ✅ **`scripts/postgres_store.py`** (339 baris): CAS `UPDATE … WHERE state_version` (lost-update lintas proses), `FOR UPDATE SKIP LOCKED` claim batch (stale 120s), `ON CONFLICT DO NOTHING` first-contact, sweep tanpa `IDLE_CLOSED`. **11/11 tes lawan DB nyata** termasuk lost-update antar-koneksi terpisah.
- ✅ **Bug U** — `SendRecord.sender_admin_id` ditambah (sebelumnya hanya di `OutboxEntry`; guard `reassigned_to_other_admin` di `authorize_send` mustahil menyala di jalur nyata → balasan admin yang sudah dilepas tetap terkirim).
- ✅ **CHECK constraint terbukti di DB** — INSERT measure `queryable=true + unit_source='title_matched'` → `ERROR … violates check constraint measure_registry_queryable_requires_unit`.
- ✅ **Retensi 365 hari (#5)**: `PostgresStore.apply_retention(retention_days=365)` — hapus `marawa_messages` > window, outbox terminal > window (pending/claimed tidak pernah), percakapan `IDLE_CLOSED` kosong > window; `marawa_audit_log` dikecualikan (append-only). Paragraf keputusan OQ-11 ditulis di `docs/15`. 2 tes lawan DB nyata (hapus yang kedaluwarsa; simpan yang fresh + in-flight). **328 PASS**.
- ✅ **DI `app.py` → `PostgresStore`** via `MARAWA_RUNTIME_DSN` (fallback in-memory untuk dev/test); test env-switch dua arah. Diuji 326 PASS (315+11 dengan DSN).

### Resolved hari ini (batch keenam — auth, worker, dashboard, deploy, 16 Aug)

- ✅ **#4 Auth TOTP + sesi**: `scripts/totp_session.py` stdlib murni (RFC 6238 vectors terverifikasi; HMAC-signed session 12 jam; `MARAWA_SESSION_KEY` prod). Route: `/admin/login`, `/admin/enroll-totp`, `/admin/session`; `current_admin` terima Bearer (production) + X-Admin-Id hanya mode dev. 5 tes TOTP + 1 tes login-flow penuh.
- ✅ **#2 WhatsApp worker**: `apps/whatsapp-worker/` Node + Baileys (pinned `7.0.0-rc14`): normalize murni (skip grup/protocol/non-teks), webhook HMAC, outbox claim→send→update. 7 tes node. **LIVE via systemd**; QR pairing ada di journal.
- ✅ **#3 Dashboard**: `apps/dashboard/index.html` satu-file (login TOTP → inbox → takeover → balas) diserve `/admin`; route list/detail dual-store compatible.
- ✅ **#6 Probe OQ-05 — TERBUKTI JALAN (key Google Gemini)**: `gemini-2.5-flash` → **404 "no longer available to new users"** (model lama, key baru); **`gemini-3.1-flash-lite` → USABLE**: envelope `json_schema` (terbaik), tool calling ada, prefill bisa, latency median **0,82s** (max 3,86s), hallucination probe **menolak bersih** tanpa mengarang angka (`volunteered_numbers: []`). Laporan: `data/reports/model-capability-probe.json`. Kredensial disimpan `/etc/marawa/marawa.env` (0600, di luar git). Keputusan produksi final + privacy sign-off tetap milik Tah (OQ-11), tapi syarat teknis sudah terpenuhi.
- ✅ **Eval LLM LIVE (OQ-05 resolved)**: `eval_golden_episodes --mode llm` memainkan 19 episode lawan `gemini-3.1-flash-lite` (model hanya pilih ACTION, retry/backoff utk rate limit, event-only turns di-skip → tugas session-policy engine, bukan model). Hasil jujur: **4/19 passed, 14 failed, 1 not_evaluated (event-only)**. **Temuan desain**: tanpa session state/candidate board, model tak bisa menebak nuansa multi-turn (mis. `resolve_candidate` vs `clarify`) — membuktikan KENAPA docs/27 mewajibkan **action masking server-side per state** (tool calling sudah terbukti ada di probe). Floor action-only, bukan target; runtime yang benar = state engine membatasi action.
- ✅ **Deploy**: migrasi `008_runtime_rw_role` (rw store + read data; audit append-only), seed 2 superadmin, env `/etc/marawa/marawa.env` (secrets 0600), `marawa-api.service` (uvicorn :8130) + `marawa-worker.service` LIVE, Caddy `marawa.hatafisme.web.id` → :8130 (DNS belum menunjuk mesin ini — blocker eksternal). Live smoke: login TOTP → token → `/conversations` → 200.
- ⏭️ **Blocker eksternal deploy**: DNS `marawa.hatafisme.web.id` masih menunjuk `43.159.47.16` (bukan VPS ini); cert ACME ikut gagal. OQ-05 teknis SELESAI (probe Gemini usable) — tinggal keputusan produksi + sign-off privacy Tah.

### Resolved hari ini (batch keempat — remediasi bundle 16 Aug)

- ✅ **Migration 006 live**: views honest-naming settled (`category_code/label`, `unit_state`); constraint `aggregation_semantics` + `unknown`, `unit_state` + `review_required`; backfill-then-check order; registry 51 measures `NOT queryable`, 27 dataset `blocked_quality`.
- ✅ **DB backup + build fix**: `bps_storage.ensure_schema` di-sinkronkan dengan 006 (drift rebuild yang menimpa view view fixed); SIMDASI measure dedup `DISTINCT ON (indicator_code)`.
- ✅ **Answer gate/formatter contract (`docs/18` §20)**: `AnswerGateVerdict` + `SafeRefusalResponse` di schema JSON; `canonical` publishable (row live Jumlah Penduduk 2025 menembus gate sempat mustahil). Test gate 28 case.
- ✅ **Golden E19 re-align**: `produksi beras` → family publication (factual ranking lawan registry); harness exercised 14/14, toleransi test dicabut.
- ✅ **Prototype SQL**: 4 query pindah ke `category_label/category_code`; 13/13.

### Resolved hari ini (batch ketiga)
- ✅ **Display-label normalization** — `migration 005`: `display_label` + `normalization_rule` + `label_raw`; 0 label HTML tersisa; empty-label fallback ke item code dengan rule tercatat.
- ✅ **Query templates + binder** — enam template diperbarui sehingga SETIAP declared parameter dipakai di SQL; `scripts/bps_template_binder.py` validasi tipe/nullable/required; unknown/missing/type/injection ditolak; `row_limit` server-side; test eksekusi live.
- ✅ **Unit review packet** — `scripts/export_unit_review_packet.py` → Excel + `docs/26-BPS-UNIT-REVIEW-PACKET.md`.
- ⚠️ **Golden episode harness** — `scripts/eval_golden_episodes.py` dibuat. Klaim awal "11/11 PASS" ditarik oleh remediasi 15 Agustus: hanya turn discovery yang benar-benar dieksekusi lawan offering engine; sisanya lint fixture. Lihat note B.
- ⚠️ **Scorer "hardening" — reclassified by the 15 Aug audit.** This entry originally read "reclaim 1.0/0.9389 setelah pdrb context marker, banyak perbaikan kalibrasi". That is a description of tuning the scorer against the very set it is scored on. The ranking changes themselves are kept (queryable families preferred, dynamic tie-break 0.2, publication excluded when a meaningful statistic exists) because they encode real domain judgement, but the resulting metric is no longer treated as evidence. Hard-coded typo aliases (`pendudk`, `pendudduk`) that existed only to satisfy this set were removed and replaced by fuzzy matching against the live catalogue vocabulary.

## Audit kelima: binder, registry builder, scorer (15 Agt)

Modul yang tersisa. **Empat bug, 163 tes lulus.**

| # | Bug | Dampak |
|---|---|---|
| Q | `row_limit` bernilai `None` lolos kedua pemeriksaan dan terikat sebagai `LIMIT NULL` | Di PostgreSQL `LIMIT NULL` berarti **TANPA BATAS** — kebalikan persis dari maksudnya, dan senyap: query berhasil, hanya saja mengembalikan seluruh tabel |
| R | `NaN`/`Infinity` sebagai parameter numeric | NaN mengalahkan semua perbandingan: `nan < 0` False, `nan > maximum` juga False. Kedua batas "lolos" dan nilainya sampai ke database |
| S | `jsonb` tanpa batas kedalaman/jumlah node | Payload bersarang dalam membakar CPU di parser JSON sebelum query dijalankan |
| T | Placeholder unit dari upstream (`"-"`, `"NULL"`, `"N/A"`) dianggap satuan sungguhan | Measure tanpa satuan diterbitkan sebagai `known`, dan teks `-` atau `NULL` dicetak ke warga sebagai satuannya. Ini menembus ATURAN KERAS lewat pintu yang tidak dijaga |

Bug T yang paling penting dari empat ini: seluruh gate unit dibangun untuk
mencegah satuan ditebak, tetapi tidak ada yang memeriksa apakah satuan yang
"ada" itu sebenarnya placeholder kosong dari ekspor upstream.

### Tes yang di-skip adalah lubang audit, bukan hasil

Dua tes scorer awalnya `skip` karena modulnya mengimpor `workers.ingestion.*`
yang tidak ada di bundle ini. Skip itu bukan jawaban — ia meninggalkan
**satu-satunya mekanisme yang mencegah substitusi kata secara diam-diam** tanpa
diuji sama sekali. Fungsi murninya diangkat keluar agar bisa diuji tanpa
dependency.

Yang diuji sekarang, dan alasannya: fuzzy matching yang menulis ulang kata
nyata menjadi kata nyata **lain** lebih berbahaya daripada tidak ada toleransi
typo sama sekali — warga bertanya tentang satu hal dan dijawab tentang hal
lain, dengan percaya diri penuh dan sumber terlampir. Tes khusus menjaga
pasangan `kematian` / `kelahiran`: berbeda beberapa karakter, berarti
berlawanan, dan kalau cutoff pernah dilonggarkan inilah pasangan yang pertama
patah — kegagalan yang tidak akan terlihat sama sekali di metrik agregat.

## Audit keempat: serangan penuh ke semua modul (15 Agt)

Input jahat, kondisi ekstrem, dan jalur error di `answer_gate`,
`answer_formatter`, `outbox_worker`, `conversation_state`, `scheduler`.
**Enam bug, semuanya diperbaiki. 149 tes lulus.**

| # | Bug | Dampak |
|---|---|---|
| K | Daftar kandidat kosong tetap menutup dengan ajakan menjawab `"D1"` (hard-coded fallback) | Warga disuruh memilih opsi yang tidak pernah ditampilkan |
| L | Judul tabel dari BPS dirender apa adanya | Satu tanda bintang di judul upstream membuat sisa pesan jadi bold; satu newline memecah satu entri jadi dua baris sehingga huruf referensi tidak lagi sejajar dengan judulnya — **warga memilih tabel yang salah** |
| M | `resolve_unknown()` hanya cocok lewat `wa_message_id` | Tidak terjangkau justru di kasus yang menjadi alasan keberadaannya: TIMEOUT berarti respons tidak pernah sampai, jadi **tidak ada** id untuk dicocokkan. Entry UNKNOWN menumpuk selamanya tanpa pernah bisa diputuskan |
| N | `classify_result()` tetap menambah `attempts` pada record yang sudah FAILED | Callback terlambat bisa membalikkan record terminal jadi PENDING — menghidupkan kembali pesan yang sudah menyerah |
| O | `IDLE_TIMEOUT` menutup percakapan yang `last_activity_at`-nya `None` | Notice "sesi berakhir" dikirim ke orang yang belum pernah memulai sesi |
| P | `plan_sweep()` membandingkan datetime naive dengan aware | `TypeError` di tengah sweep **membatalkan seluruh pass** — satu percakapan bertimestamp naive membuat timeout SEMUA percakapan lain tidak pernah menyala |

Bug P adalah yang paling berbahaya dari enam ini justru karena paling sunyi:
tidak ada yang error di layar, hanya seluruh mekanisme timeout berhenti bekerja.

### Hipotesis yang ternyata salah, dan itu hasil yang bagus

Dugaan awal: angka karangan yang ditulis dengan digit non-ASCII (`４５２９００`,
`٤٥٢٩٠٠`) akan lolos gate, karena `Decimal` gagal mem-parsing lalu pemeriksa
`continue` — melewati angka yang baru saja ditemukannya.

Salah. `\d` di Python memang mencocokkan digit Unicode, **dan** `Decimal` juga
mem-parsingnya dengan benar menjadi `452900`. Jadi gate memblokirnya, dan
memblokirnya karena alasan yang tepat, bukan kebetulan. Tesnya tetap
dipertahankan sebagai regression guard.

Satu koreksi yang perlu dicatat: penjelasan pertama yang ditulis saat
memverifikasi ini ("regex tidak match digit non-ASCII") keliru. Regexnya match.
Kesimpulan akhirnya sama, alasannya berbeda — dan alasan yang salah dalam
dokumen keamanan lebih berbahaya daripada tidak ada penjelasan sama sekali,
karena ia menghentikan orang berikutnya dari memeriksa ulang.

## Audit ketiga: lima bug di `app.py` (15 Agt)

Ditulis dengan bertanya "apa yang dilakukan pemanggil yang bermusuhan atau
sedang sial?", bukan "apakah happy path jalan?". **132 tes lulus.**

| # | Bug | Dampak |
|---|---|---|
| F | **Lost update.** Guard versi ada di dalam `apply()`, tetapi penulisan ke store adalah statement TERPISAH. Dua request bisa sama-sama membaca versi 3, sama-sama lolos guard, sama-sama menulis versi 4 | Dua petugas sama-sama dapat `200` dan sama-sama yakin memegang percakapan. Yang kalah tidak pernah diberi tahu |
| G | `POST /internal/sweep` dan `GET /internal/notifications` **tanpa autentikasi** | Siapa pun yang bisa mencapai port bisa memaksa tutup sesi, membalik handover, dan membaca id percakapan + isi pesan warga. "Internal" di path bukan kontrol akses |
| H | Idempotency key = isi pesan + versi, dan `ADMIN_REPLY` tidak menaikkan versi | Petugas mengetik "ok" dua kali — perilaku manusia biasa — pesan kedua ditelan diam-diam |
| I | Timestamp naive dibandingkan dengan cutoff aware | `TypeError` → 500 ke bridge WhatsApp → bridge retry pesan yang sama selamanya |
| J | Header `x_webhook_signature` dideklarasikan, tidak pernah diverifikasi | Endpoint **terlihat** terautentikasi saat direview padahal tidak memverifikasi apa pun — lebih berbahaya daripada tidak ada parameternya sama sekali, karena menghentikan pembaca dari bertanya |

### Catatan metode: satu tes hijau yang ternyata tidak berarti

Tes concurrency pertama menjalankan dua thread lewat `TestClient` dan **lolos**.
Itu false negative: `TestClient` menyerialkan request lewat satu portal, jadi
interleaving-nya tidak pernah terjadi. Bug F baru terbukti setelah `Store`
diuji langsung — dua snapshot, dua `apply()`, dua penulisan, dan pemenangnya
adalah yang menulis terakhir.

Pelajarannya sama seperti temuan C1 di audit pertama, dan layak diulang karena
terus muncul: **tes hijau hanya berarti kalau ia benar-benar bisa merah.**
Sebelum mempercayai tes yang lolos, pastikan ia pernah gagal karena alasan yang
benar.

Perbaikan F memakai `compare_and_set()` + lock per percakapan. Di PostgreSQL
nanti ini menjadi `UPDATE ... WHERE state_version = %s` dengan pemeriksaan
rowcount — mekanismenya berbeda, jaminannya sama, dan tesnya tidak berubah.

## Sweep + notifikasi, dan dua bug lagi (15 Agt, lanjutan)

`scripts/scheduler.py` (timeout tidak jalan sendiri tanpa sesuatu yang
meriksa) dan `scripts/notifications.py` (efek `notify_*` jadi kiriman
sungguhan, bukan sekadar nama string) — dikabelkan ke `POST /internal/sweep`
dan endpoint webhook/toggle. **125 tes total lulus.**

### Bug ketiga dan keempat, ditemukan wiring test lagi

3. **Dua jalur notifikasi yang tidak saling kenal.** `conversation_state.py`
   punya dua cara menandai "beri tahu petugas": kadang string
   `"notify_officers_auto_revert"` di `effects`, kadang boolean terpisah
   `Transition.notify_officers`. `dispatch_effects()` cuma membaca `effects`,
   jadi jalur boolean (dipakai `_on_inbound` saat `QUEUED` dan
   `_on_request_handover`) diam-diam tidak pernah mengirim apa pun. Disatukan
   di `app.py`: kedua sumber digabung jadi satu daftar sebelum diteruskan ke
   dispatcher, dengan alasan ditulis eksplisit kenapa keduanya bisa berbeda.

4. **Tabrakan nama kelas `Settings`.** `conversation_state.Settings`
   (dataclass konfigurasi) di-import, lalu `app.py` mendefinisikan model
   Pydantic bernama sama untuk endpoint `/settings/timeouts`. Definisi kedua
   **menimpa** nama itu di namespace modul — setiap pemanggilan `Settings()`
   setelah titik itu (di `handover_on`, `run_sweep`) diam-diam memakai kelas
   yang salah dan gagal validasi. Python tidak memperingatkan penimpaan nama
   seperti ini; ketahuan hanya karena tes benar-benar menjalankan endpointnya.
   Diperbaiki dengan alias impor eksplisit (`TimeoutSettings`), dan model
   Pydantic yang tadinya bentrok diberi nama sendiri.

Kedua bug ini punya kesamaan: **tidak ada satu pun error saat kode ditulis atau
di-lint.** Keduanya cuma muncul saat jalur HTTP yang sungguhan dipakai
dijalankan sungguhan. Ini alasan kenapa 25 tes wiring di file ini dan
`test_app_wiring.py` bukan sekadar duplikasi dari tes unit modul — mereka
menguji lapisan integrasi yang tidak ada di tempat lain.

## Wiring HTTP pertama — masih planning-stage (15 Agt)

`scripts/app.py`: FastAPI yang menyatukan `answer_gate`, `conversation_state`,
`outbox_worker`, dan penyimpanan **in-memory** (`Store`) di balik satu
interface, supaya swap ke PostgreSQL nanti tidak menyentuh route handler.

**Ini masih planning/spesifikasi yang bisa dieksekusi, bukan produksi.** Yang
belum: Baileys, PostgreSQL sungguhan, LLM sungguhan, verifikasi HMAC webhook,
sesi TOTP asli. `Store` hilang begitu proses restart.

Diuji lewat `fastapi.testclient.TestClient` — HTTP request sungguhan ke route
sungguhan, tanpa jaringan. **16 rute, 15 tes wiring, total 114 tes lulus.**

### Dua bug yang ditemukan wiring test ini sendiri

1. **`should_run_agent` dipanggil setelah `apply()`.** `apply()` men-set
   `agent_run_active=True` sebagai bagian dari memulai run, jadi memeriksa
   `should_run_agent` pada state SESUDAH transisi selalu membaca "sudah
   berjalan" — bot tidak akan pernah menjawab apa pun. Diperbaiki: cek
   dilakukan pada state SEBELUM transisi (itulah tujuan `should_run_agent`
   sebagai pre-check), `apply()` tetap dipanggil sekali untuk mencatat state.
2. **Header auth hilang mengembalikan 422, bukan 401.** `Header(...)` wajib di
   FastAPI membuat pesan hilang dan pesan tidak valid berbeda kode status —
   yang pertama membocorkan nama header yang diharapkan ke pemanggil yang belum
   terautentikasi. Disatukan jadi 401 untuk keduanya.

Pola yang sama lagi: penjaga yang terlihat benar saat ditulis, gagal begitu
dijalankan sungguhan lewat jalur yang benar-benar dipakai. Ini alasan kenapa
tes wiring HTTP penting sebagai lapisan terpisah dari tes unit modul.

## Komponen runtime yang sudah dibangun (15 Agt)

Logika murni, tanpa DB/network/LLM, jadi semuanya bisa diuji sekarang juga.
**99 tes lulus.**

| Modul | Isi | Tes |
|---|---|---|
| `scripts/answer_gate.py` | grounding angka, unit, selection envelope, evidence, periode, citation, teks "tidak ada" per alasan | 26 |
| `scripts/conversation_state.py` | state machine toggle handover, auto-revert, watermark, concurrency, kill switch | 41 |
| `scripts/outbox_worker.py` | idempotency, retry/backoff, lease, timeout parking, echo reconciliation, health | 10 |
| `scripts/answer_formatter.py` | format jawaban `docs/18`, angka gaya Indonesia, penolakan unit tak pasti | 8 |
| `scripts/probe_model_capabilities.py` | probe OQ-05 (butuh API key, belum dijalankan) | — |

### Keputusan desain yang layak dicatat

**Outbox: timeout tidak pernah di-retry membabi buta.** Ini kasus paling
berbahaya di seluruh jalur pengiriman: WhatsApp mungkin sudah mengirimkannya dan
respons yang tidak sampai ke kita. Retry buta di situ = warga menerima jawaban
yang sama dua kali, dan bot yang mengulang diri terlihat rusak dengan cara yang
di-screenshot orang. Entry diparkir sebagai `UNKNOWN` lalu diselesaikan dari
echo `fromMe`; keputusan kirim ulang tidak pernah otomatis.

**Idempotency key memuat `state_version`.** Sehingga retry dari satu kirim logis
dikenali sebagai duplikat, tetapi teks sama yang dikirim ulang secara sah
(warga bertanya hal serupa setelah handover) tidak ikut tertelan.

**Formatter deterministik, model tidak menyentuh angka.** Model memutuskan APA
yang disampaikan (dataset mana, periode mana, bisa dijawab atau tidak);
formatter memutuskan BAGAIMANA angka dicetak. Konsekuensinya angka karangan
tidak punya pintu masuk — formatter hanya bisa merender nilai yang diberikan
kepadanya. Gate tetap ada sebagai penjaga prosa model, bukan penjaga tunggal.

**Aturan keras ikut sampai ke formatter.** `format_single_value()` menolak
mencetak nilai yang unitnya `review_required`, dan tidak mencetaknya "polos
tanpa satuan" sebagai jalan tengah — angka tanpa satuan adalah jenis jawaban
salah tersendiri.

**Probe OQ-05 siap dijalankan, belum dijalankan.** Butuh `PROBE_BASE_URL` dan
`PROBE_API_KEY`. Tiga hasilnya masing-masing mengubah desain, jadi jalankan ini
sebelum menulis kode runtime lain:

```bash
export PROBE_BASE_URL="https://.../v1"; export PROBE_API_KEY="..."
uv run python scripts/probe_model_capabilities.py --model gemini-3.1-flash
uv run python scripts/probe_model_capabilities.py --model deepseek-v4-flash
```

Kalau `tool_calling.supported = false`, Lapis 0 harus dirancang ulang dan itu
desain yang berbeda serta lebih lemah — lebih baik diketahui sekarang.

## Remediation applied 15 Aug 2026 (post-audit)

Findings and the change that closed each one. Every fix has a failing-first test
in `tests/test_unit_and_binder_invariants.py` unless noted.

| ID | Finding | Fix |
|---|---|---|
| C1a | Episode harness lint-checked its own fixture and reported it as 19/19 PASS | Harness reports `exercised` / `lint_only` / `blocked` separately; `passed` counts only exercised assertions |
| C1b | `QUERY_ALIASES` hard-coded the exact typos in its own eval set (`pendudk`) | Removed; replaced with `_fuzzy_expand()` against live catalogue vocabulary |
| C1c | 60 utterances and their answer keys were author-written | Runner labels the set `synthetic_author_written`; real-question path added |
| C1d | `validate_docs.py` only checked keyword presence | Added episode-count derivation from the fixture, single-source test counts, policy-contradiction gate, unlabelled-metric gate |
| C2a | SIMDASI unit derived from table title by ILIKE, published as `known` | `unit_state='review_required'`; measures with `unit_source='title_matched'` are not queryable |
| C2b | `"menurut" in title` returned `unitless` | Branch removed; falls through to `unknown_review` |
| C2c | Answerability gated per dataset, hiding unit-less measures inside good datasets | Gate moved to the measure (`queryable`, `quality_flags`) + DB CHECK constraint |
| M3 | Unknown unit defaulted to summable `count` | Returns `unknown`, which forbids aggregation |
| H1 | `"LIMIT" not in sql` substring check; `publication_list` unbounded | Explicit `has_own_limit`; wrapping instead of appending; `max:` bounds required on integers |
| H2 | `ILIKE` wildcards unescaped | `like` parameter type escapes `% _ \`, SQL declares `ESCAPE` |
| H3 | `DROP VIEW` silently removed runtime grants | `scripts/check_runtime_privileges.py`; migration 006 re-grants inline |
| H4 | No template for "terbaru" | `dynamic_latest`, `simdasi_latest` added |
| M1 | `primary_dimension_*` was a duplicate of geography | Dropped from the view; real category axis named `category_code`/`category_label` |
| M2 | SIMDASI bound on exact human label | Binds on `indicator_code` |
| M5 | PRD promised scheduled sync while policy forbids cron | PRD corrected; validator gate added |
| M6 | Three test counts, two episode counts | This document is canonical; validator enforces |

Migration `006_unit_provenance_and_serving_clarity.sql` carries the schema half
of C2a/C2c/H1/H3/M1, with a tested `.down.sql`.

**Expected effect on the registry.** The next rebuild will move measures out of
`queryable` wherever the unit was guessed or missing. That count going UP is the
fix working, not a regression — those measures were previously answerable on the
strength of a heuristic. Confirm with the queries in the audit report §7 before
and after, and route the delta into the `docs/26` review packet.

## Dependency graph for the next phase

```text
Data-owner unit/Census decisions
        ↓
Census item registry + label-normalization pass
        ↓
Registry publish v2 + integrity gates
        ↓
query compiler design review (planning)
        ↓
typed result/evidence contract review (planning)
        ↓
only then runtime implementation proposal
```

The database work can continue on Census dimension items, label normalization, and registry rebuilds. Runtime work must wait for the query/evidence contracts to be reviewed against the live registry.

## Recommended next gates

Urutan dikoreksi oleh audit; jangan mulai Census cleanup sebelum lifecycle/security foundation registry aman:

1. **Database-only Critical:** create/test runtime read-only role; remove runtime dependence on superuser ingest credential.
2. **Database-only High:** redesign registry version retention; make retired versions queryable/auditable and compatible with candidate-set pinning.
3. **Database-only High:** persist `quality_flags` reasons and add migration ledger + reversible up/down tests.
4. **Database-only:** build Census category item registry from `categories[]`; set cardinality and explicit total items; add tests for SP2010 dataset 10 margins.
5. **Database-only:** add normalized display labels for SIMDASI HTML/bilingual rows while retaining `row_label_raw` lineage.
6. **Planning:** revise query compiler contract; current six templates are evidence of shapes, not safe runtime templates because several typed parameters are not applied in SQL.
7. **Planning:** define evidence snapshot schema and deterministic Indonesian result formatter.
8. **Planning/testing:** expand golden episodes into live model/tool evaluation; do not connect WhatsApp yet.
9. **Production blockers:** resolve owner, WhatsApp number, model/provider exact IDs, form URL, retention/privacy, approved views/data dictionary, VPS/backup/on-call.

## Audit scope boundary

- No new database migration or registry rebuild was executed during this audit.
- Live DB queries were read-only inspection of roles, grants, registry versions/counts, quality flags, dimensions, labels, and templates.
- Markdown/Obsidian status was updated; runtime/code/schema behavior was not changed.
- `/home/ubuntu/projects/marawa-ai` is not a Git working tree (`git status` unavailable), so verification relies on file validation, tests, live DB assertions, and explicit artifact paths rather than a Git diff/commit.

## Verification commands

```bash
cd /home/ubuntu/projects/marawa-ai
uv run --with pytest pytest tests/ -q
python3 scripts/validate_docs.py
uv run python scripts/check_runtime_privileges.py     # after ANY migration or view rebuild
uv run python scripts/validate_bps_query_prototypes.py
uv run python scripts/simulate_bps_candidate_scoring.py
uv run python scripts/simulate_bps_candidate_scoring.py --offering
uv run python scripts/eval_golden_episodes.py
```

Expected current results:

```text
pytest                     all green (canonical total lives here only)
documentation validation   PASS
runtime privilege check    PASS
query prototypes           13/13 PASS
candidate scoring          evaluation_set = synthetic_author_written  <-- not evidence
golden episode harness     15 exercised assertions; 5/19 episodes not evaluated
```

`scripts/simulate_bps_candidate_scoring.py` intentionally no longer prints a
headline accuracy figure without its `evaluation_set` label attached. If you
find yourself quoting a bare number from it, that is the bug this audit fixed.

### Regression order

Run `scripts/check_runtime_privileges.py` after every migration, registry
rebuild, or `ensure_schema` bootstrap. `DROP VIEW` removes object-level grants,
so the read-only boundary used to depend on a human remembering to re-apply
migration 004. It now fails loudly instead of failing open.
