---
title: Product Requirements Document — MARAWA AI
version: 0.6.0
status: Database foundation built; stakeholder approval pending; runtime planning
owner: BPS Kabupaten Padang Pariaman
---

# PRD — MARAWA AI

## 1. Ringkasan

MARAWA AI adalah **domain-bounded conversational AI agent** untuk Pelayanan Statistik Terpadu BPS Kabupaten Padang Pariaman. Seperti agent umum, sistem dapat memahami tujuan, menjaga konteks lintas turn, menyusun rencana, memilih dan menjalankan beberapa tools, membaca hasil, melakukan analisis statistik, membuat artefak, lalu melanjutkan pertanyaan berikutnya. Batas domainnya adalah statistik dan BPS. Kanal utama adalah WhatsApp; sumbernya meliputi PostgreSQL read-only, RAG, dan WebAPI BPS. Dashboard internal memungkinkan petugas memonitor agent run, mengelola knowledge/skills, mengevaluasi jawaban, dan mengambil alih chat secara live.

Portal PPID BPS Kabupaten Padang Pariaman menyatakan permintaan data statistik dilayani melalui PST.[1] Situs unit juga sudah memperkenalkan MARAWA sebagai layanan tanya lewat WhatsApp; produk ini memperluasnya dengan AI dan human handover.[8]

## 2. Masalah

- Pengguna harus mencari data melalui banyak publikasi, tabel, metadata, dan kanal layanan.
- Pertanyaan sering ambigu pada indikator, tahun, wilayah, unit, atau definisi.
- Petugas mengulang jawaban yang sama dan sulit mengukur pertanyaan yang belum terlayani.
- Chatbot generatif biasa berisiko membuat angka, mencampur tahun, atau tidak menyebut sumber.
- Chatbot FAQ/RAG satu langkah tidak mampu menjalankan analisis bertahap atau memahami pertanyaan lanjutan seperti “bandingkan”, “kenapa”, dan “buat grafiknya”.
- Otomasi tanpa takeover menghambat kasus yang membutuhkan interpretasi manusia.

## 3. Tujuan

1. Memberikan jawaban statistik ter-grounding, cepat, dan mudah dipahami melalui WhatsApp.
2. Menjaga semua jawaban dalam scope statistik dan layanan BPS.
3. Menyediakan sumber, periode, unit, dan catatan penting pada jawaban faktual.
4. Mengalihkan kasus tidak terjawab ke admin atau form publik tanpa membuat jawaban palsu.
5. Menghasilkan analytics layanan untuk peningkatan corpus dan proses PST.
6. Menyediakan pengalaman agentic: multi-step tool use, analisis, visualisasi/ekspor, dan conversational continuity dalam scope statistik/BPS.
7. Mempertahankan scope dan kerahasiaan terhadap direct, obfuscated, multi-turn, indirect/RAG, tool, memory, artifact, dan fallback jailbreak attempts.

## 4. Non-goals

- Menggantikan judgement resmi petugas/statistisi; agent boleh membantu analisis kompleks tetapi harus menunjukkan metode, bukti, dan batas interpretasinya.
- Menyediakan data individu, rahasia statistik, atau data belum dirilis.
- Menjalankan arbitrary SQL atau memberi akses langsung ke database internal.
- Menjadi general-purpose agent di luar statistik/BPS.
- Menjadi sistem tiket/form; form publik tetap layanan terpisah dan bot hanya memberi tautan.
- Mengirim broadcast/promosi massal.

## 5. Persona

| Persona | Kebutuhan |
|---|---|
| Masyarakat | Menemukan angka/publikasi dan memahami definisinya |
| Mahasiswa/peneliti | Data runtun waktu, metadata, sumber dan cara kutip |
| OPD/pelaku usaha | Indikator wilayah dan panduan layanan statistik |
| Petugas PST | Antrean handover, konteks percakapan, jawaban cepat, disposition |
| Knowledge manager | Ingest/review/publish corpus dan memantau content gap |
| Supervisor | SLA, kualitas, beban petugas, insiden, dan audit |
| Auditor/admin sistem | RBAC, konfigurasi, log perubahan, akses data |

## 6. User journeys utama

### J1 — Pertanyaan angka jelas

Pengguna bertanya indikator + tahun + wilayah → router memilih dataset → tool parameterized query → validator memeriksa unit/periode → agent menjawab dengan sumber → pengguna memberi feedback opsional.

### J2 — Pertanyaan ambigu

Pengguna meminta “jumlah penduduk terbaru” → agent menanyakan cakupan wilayah bila perlu → retrieval/query → jawaban menyebut tahun data dan publikasi, bukan sekadar kata “terbaru”.

### J3 — Pertanyaan dokumen/definisi

Agent melakukan hybrid search → rerank → menyusun jawaban hanya dari chunk approved → mencantumkan publikasi/halaman/URL.

### J4 — Bukti tidak cukup

Agent menyatakan belum menemukan jawaban → menawarkan `ADMIN` atau `PUBLIC_FORM_URL` → bila admin dipilih, conversation masuk antrean.

### J5 — Live handover

Petugas claim conversation → state `ADMIN_ACTIVE` → bot berhenti → petugas membalas dari dashboard melalui worker → petugas resolve/return-to-bot → audit lengkap tersimpan.

### J6 — Analisis bertahap dan follow-up

Pengguna meminta data penduduk 2025 → agent query dan menjawab → pengguna berkata “bandingkan dengan tahun sebelumnya” → agent memakai indikator/wilayah aktif, mengambil 2024 dan menghitung perubahan → pengguna meminta kecamatan dengan kenaikan terbesar → agent query breakdown, ranking, dan menjawab → pengguna meminta grafik/XLSX → agent membuat artefak dari result aktif tanpa meminta konteks ulang.

## 7. Functional requirements

### FR-A — WhatsApp

- Menerima text dan caption dokumen/gambar; MVP hanya memahami text/caption, attachment diberi pesan kemampuan terbatas.
- Menormalisasi message menjadi internal event.
- Dedup berdasarkan `wa_message_id`.
- Menyimpan status `received`, `processing`, `sent`, `delivered`, `read`, `failed` jika event tersedia.
- Menjaga ordering per conversation dengan distributed lock/sequence.
- Menyediakan reconnect, QR/pairing, health state, dan alert disconnect.

### FR-B — Conversational agent runtime

- Intent/task: data lookup, analysis, knowledge, methodology, service, artifact, clarify, handover, dan out-of-scope.
- Bahasa keluaran publik hanya Indonesia.
- Maksimal satu pertanyaan klarifikasi per giliran.
- Structured output divalidasi server.
- Fallback model otomatis hanya untuk provider failure yang diklasifikasikan retryable.
- Menolak/abstain jika evidence tidak cukup.
- Mempertahankan typed working memory: goal, indikator, wilayah, periode, dataset, filters, result/analysis/artifact references, assumptions, dan open questions.
- Menjalankan bounded multi-step loop: understand → plan → tool → observe → iterate/analyze → validate → respond → remember.
- Dapat memakai beberapa tools berurutan dan mengubah strategi setelah hasil kosong/error.
- Dapat melanjutkan referensi seperti “tahun sebelumnya”, “yang tertinggi”, “kenapa”, “buat grafiknya”, atau “ringkas” dari active context.
- Menyediakan versioned statistical skills yang membantu tool planning tanpa menambah permission.

### FR-C — Data dan RAG

- Sumber: allowlisted views PostgreSQL internal; dokumen resmi internal approved; website dan PPID unit; publikasi/API BPS; metadata BPS.
- Structured queries memakai registry + template/tool, bukan text-to-SQL bebas.
- Hybrid retrieval: full-text + vector + metadata filters + reranker.
- Versioning, effective date, release status, checksum, provenance, dan approval.
- Jawaban menyimpan evidence snapshot agar dapat diaudit walau sumber berubah.
- RAG hanya salah satu tool; agent juga mempunyai catalog discovery, typed query, statistical analysis, visualization, dan export tools.

### FR-C2 — Analysis dan artifacts

- Mendukung comparison, change/growth, share/composition, ranking, descriptive statistics, trend, distribution/outlier, cross-tab, dan correlation dengan caveat.
- Typed deterministic operations menjadi default; analisis kompleks dapat memakai sandbox terisolasi tanpa network/secrets/host filesystem.
- Setiap derived result menyimpan input result IDs, method/version, parameters, diagnostics, caveats, dan reproducibility hash.
- Membuat grafik, tabel, CSV, XLSX, dan PDF dari result/analysis approved.
- Artifact dapat dirujuk dan diolah lagi pada follow-up.

### FR-D — Dashboard

- Login lokal, RBAC, dan TOTP.
- Inbox real-time dengan filter status/priority/assigned agent.
- Claim/reassign/resolve/return-to-bot.
- Riwayat chat, tool/evidence drawer, internal notes, canned response.
- Knowledge upload/sync/review/publish/rollback.
- Prompt/model configuration dengan draft-review-publish dan rollback.
- Feedback/content gaps/evaluation reports.
- User/RBAC/session management dan append-only audit log.

### FR-E — Escalation/form

- Trigger manual: keyword `ADMIN`, tombol/link yang tersedia, atau menu numerik.
- Trigger agent: low evidence, repeated failure, atau explicit user request.
- Bot memberi status antrean tanpa menjanjikan waktu yang belum dikonfigurasi.
- Form publik adalah URL configurable; bot tidak mengumpulkan field form.

### FR-F — Analytics

- Volume chat dan intent.
- Answered/abstained/handover rate.
- Grounded answer rate dan citation coverage.
- Feedback positif/negatif.
- Latency per stage, provider fallback/error, token/cost estimate.
- Top unanswered questions dan stale knowledge.
- Handover queue wait dan handling time.

### FR-G — Anti-jailbreak dan prompt-injection defense

- Server-side trust hierarchy; user/source/tool text tidak dapat menjadi system/developer instruction.
- Canonical detection views untuk Unicode homoglyph, zero-width/bidi, spacing, dan bounded common encodings tanpa mengeksekusi payload.
- Ensemble rules/classifier/conversation-risk detection; keyword saja tidak cukup untuk memblokir.
- Policy decisions: allow, constrained allow, clarify, block scope/security, atau hold review.
- Retrieved content dan tool text diberi taint; indirect instructions tidak dijalankan atau dipromosikan ke memory.
- Tool/action authorization, working-memory validation, analysis sandbox isolation, dan output DLP berada di luar LLM.
- System prompt, internal config/tool schema, credentials, PII, hidden paths/hosts, dan admin notes tidak boleh keluar.
- Primary/fallback parity; security block dan no-evidence tidak memicu model fallback.
- Multi-turn abuse controls, near-duplicate probes, risk decay, cooldown/review, dan generic responses yang tidak memberi detector oracle.
- Prompt/model/skill/tool/source release diblok jika critical anti-jailbreak effect suite gagal.
- Trusted control plane dipisahkan dari tainted user/RAG/source/tool/model data plane.
- Direct-user goal diikat menjadi immutable task contract; observation tidak dapat mengubah goal, allowed effects, atau destination.
- Setiap tool/data/memory/artifact/reply effect memerlukan opaque server-created scoped capability dengan provenance, purpose, resource, conversation, dan expiry binding.
- Derived values mewarisi taint/data classification; data-dependent branching hanya melalui bounded typed declassification dan registry validation.
- Autonomous run tidak boleh sekaligus memproses untrusted input, mengakses sensitive/private systems/data, dan mengubah state/berkomunikasi keluar tanpa trusted validation/supervision.
- Release report membedakan hard invariant, Draft-ASR, Effect-ASR, containment, utility, false-positive, repeated-attempt risk, adaptive budget, dan confidence bound.
- Static attack corpus hanya regression baseline; critical release diuji oleh defense-aware attacker terhadap exact deployed stack.

### FR-H — BPS WebAPI mirror dan serving

- Bootstrap dan update menarik seluruh resource tersedia untuk Dynamic Data domain `1306`, SIMDASI region `1306000`, Census Data lokal, Publication domain `1306`, dan Glosarium BPS.
- Raw API response disimpan immutable dan deduplicated berdasarkan key-free canonical request + canonical response hash.
- Normalized current tables memakai idempotent natural-key upsert dan menunjuk source snapshot aktif.
- Ingestion serialized, rate-limited, bounded retry, WAF/HTML-aware, checkpointed, resumable, dan mampu melanjutkan family lain jika satu resource gagal.
- HTTP 200 berisi HTML/WAF tidak boleh dianggap empty success.
- Publication metadata/detail masuk PostgreSQL; PDF mirror resumable, magic-checked, SHA-256 verified, dan capacity-gated.
- Chat runtime membaca allowlisted serving views; tidak memukul WebAPI langsung per pesan.
- Jawaban WhatsApp mengikuti deterministic templates pada `18-WHATSAPP-DATA-ANSWER-FORMATS.md` dan selalu membawa indicator, geography, period, value, unit, source, update state, dan internal lineage.
- Update absence tidak langsung menghapus record; inactive/superseded state memerlukan successful complete catalogs dan grace policy.

## 8. Non-functional requirements

| ID | Requirement awal | Catatan |
|---|---|---|
| NFR-1 | API availability target 99.5%/bulan | Di luar planned maintenance |
| NFR-2 | p95 first response ≤ 12 detik untuk query biasa | Diukur message received → send accepted |
| NFR-3 | Webhook internal ack ≤ 1 detik | Proses berat asynchronous |
| NFR-4 | Tidak ada duplicate outbound untuk duplicate inbound | Idempotency contract test |
| NFR-5 | RPO aplikasi ≤ 24 jam; target 1 jam setelah matang | Backup + WAL bila tersedia |
| NFR-6 | RTO ≤ 4 jam | Single VPS baseline |
| NFR-7 | Audit event untuk semua aksi admin sensitif | Append-only |
| NFR-8 | Source DB read-only dan query timeout ≤ 5 detik | Configurable per dataset |
| NFR-9 | Dashboard responsive desktop/tablet | Chat ops desktop-first |
| NFR-10 | Semua waktu storage UTC, UI WIB | `Asia/Jakarta` |

## 9. Success metrics dan acceptance gate

Target awal, dikalibrasi setelah pilot:

- ≥ 90% jawaban faktual memiliki citation/evidence lengkap.
- 0 jawaban angka tanpa evidence pada automated invariant test.
- ≥ 80% golden questions answered correctly; ≥ 95% safe abstention untuk unanswerable set.
- ≤ 5% duplicate/failed delivery; target produksi < 1% setelah stabil.
- 100% takeover tests memastikan bot diam pada `ADMIN_ACTIVE`.
- ≥ 70% pertanyaan publik terselesaikan tanpa admin setelah bulan pertama, tanpa mengorbankan grounded accuracy.
- p95 latency memenuhi NFR-2 pada beban pilot.

## 10. Product constraints

- Baileys merupakan adapter WhatsApp Web berbasis WebSocket tanpa browser; dependency ini tidak resmi sehingga harus diisolasi di worker yang dapat diganti.[7]
- Model primary wajib configurable lewat `PRIMARY_MODEL`. Dokumentasi Google yang terverifikasi saat penyusunan mencantumkan `gemini-3.1-flash-lite` dengan structured output dan function calling; exact model tetap ditentukan konfigurasi/capability probe.[5]
- Fallback default `deepseek-v4-flash`; DeepSeek mendokumentasikan model ID tersebut pada API resminya.[6]
- BPS WebAPI memakai token, response JSON, domain, dan menyediakan static/dynamic data serta konten produk.[3][9]

## 11. Release scope

> **Rescoped 15 Aug 2026 after audit finding C3.** The previous "MVP" listed
> WhatsApp + RAG + agent loop + statistical analysis + charts + exports +
> dashboard with RBAC/TOTP + knowledge management + analytics + audit + backup.
> That is a full product, realistically 4–6 months for a team. It was also being
> specified at 49.000+ words against 0 lines of runtime code, with all five
> sign-off rows still TBD. Scope is now cut to a slice that can actually ship
> and that routes around every blocker except two.

### Slice 1 — the only thing being built next

```text
WhatsApp (Baileys) → FastAPI → offering engine → user picks a table
                            → binder → query templates → Indonesian formatter
                            → answer + source + period + unit
```

In scope:

- Text messages only.
- Discovery → candidate list → user selection → typed query (the path already
  built and tested at the database layer).
- Deterministic answer formats from `18-WHATSAPP-DATA-ANSWER-FORMATS.md`.
- Idle session timeout.
- Cannot answer → send the officer's WhatsApp number and stop.

Explicitly deferred out of Slice 1 (not cancelled, just not now): RAG and
pgvector, statistical analysis, charts, XLSX/PDF export, the dashboard and all
of RBAC/TOTP/knowledge management/analytics, model fallback, complex multi-turn
working memory, and dashboard-driven live handover with a claim queue.

**Why this slice.** It clears OQ-06 (no embeddings needed), OQ-07 (all data is
already in the public mirror), OQ-04 and OQ-10 (no dashboard). Only **OQ-02**
(WhatsApp number) and **OQ-05** (model) remain, and OQ-05 can be de-risked today
with a small personal-quota API key before asking procurement for anything.

### Slice 2 — after Slice 1 has real users

- Live handover with queue, claim, and SLA (`docs/27` §5 as written).
- Dashboard inbox, RBAC, TOTP, audit.
- Prompt/model release workflow and golden eval UI.

### Slice 3+

- RAG over publications and definitions; analysis, charts, and exports.
- OCR attachments, richer tables, official WhatsApp adapter, SSO, HA deployment.
- Source freshness dashboard.

**Data updates stay manual at every slice.** There is no scheduled BPS sync in
any release. The locked policy is manual targeted pulls from the catalogue
(`docs/20`), and `scripts/validate_docs.py` fails the build if a scheduled sync
reappears in any document.

## 12. Dependencies

- Read-only connection dan curated views PostgreSQL internal.
- API keys/provider endpoints.
- BPS WebAPI key.
- Dedicated WhatsApp number dan pairing access.
- Final `PUBLIC_FORM_URL`, service hours, SLA, operator roster.
- VPS, domain/TLS, backup target, SMTP/notification channel.
- Approved corpus dan owner per source.

## 13. Risks ringkas

| Risk | Impact | Mitigation |
|---|---|---|
| Hallucinated statistic | Tinggi | Evidence invariant, deterministic tools, abstention, eval |
| Baileys breaking/session logout | Tinggi | Isolated adapter, pinned version, reconnect runbook, adapter interface |
| Internal data exposure | Kritis | Views/role read-only, source allowlist, no arbitrary SQL, egress controls |
| Stale/conflicting data | Tinggi | Version/effective date, source priority, freshness alerts, evidence snapshot |
| Admin takeover race | Tinggi | Transactional state machine, lock, event sequence, invariant test |
| Infinite retention of PII | Tinggi | Approval, strict access, pseudonymized analytics, export/delete workflow, periodic review |
| LLM/provider outage | Sedang | Timeout, retry classification, circuit breaker, fallback, queue |
| Jailbreak/prompt injection | Kritis | Defense in depth outside model, taint, action/memory/output guards, red-team release gate |

## 14. Sign-off

**Nobody has approved anything yet.** All five rows below have been TBD/Pending
for the entire life of this document, which now runs to tens of thousands of
words of specification. Audit finding C3 flagged this ordering as inverted: the
cheapest way to fill this table is to show a working Slice 1 handling 30 real
PST questions, not to write more specification.

| Area | Approver | Status |
|---|---|---|
| Product/PST | TBD | Pending |
| Data owner | TBD | Pending |
| Security/privacy | TBD | Pending |
| Infrastructure | TBD | Pending |
| WhatsApp operations | TBD | Pending |

## Sources

[1] https://ppid.bps.go.id/?mfd=1306 — Portal PPID BPS Kabupaten Padang Pariaman
[3] https://webapi.bps.go.id/developer — WebAPI BPS
[5] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite — Gemini 3.1 Flash-Lite
[6] https://api-docs.deepseek.com/updates — DeepSeek API Change Log
[7] https://baileys.wiki — Baileys Documentation
[8] https://padangpariamankab.bps.go.id/id/publication/2026/02/27/632a70da42c6c2f59eb034ce/kabupaten-padang-pariaman-dalam-angka-2026.html — Kabupaten Padang Pariaman Dalam Angka 2026 / MARAWA
[9] https://webapi.bps.go.id/documentation — Dokumentasi WebAPI BPS
