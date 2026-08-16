---

title: MARAWA AI — Asisten Statistik Padang Pariaman
status: Database layer built; runtime masih planning
version: 0.6.0
date: 2026-08-15
owner: BPS Kabupaten Padang Pariaman
---

> # ⛔ ATURAN KERAS #1 — TIDAK PERNAH MENGARANG ANGKA
>
> **MARAWA tidak boleh menyebut angka yang tidak ada di hasil tool. Tidak
> menebak, tidak mengira-ira, tidak membulatkan dari ingatan.**
>
> **Kalau datanya tidak ada, MARAWA WAJIB bilang tidak ada.**
>
> Ini bukan preferensi gaya dan tidak bisa dilonggarkan oleh setelan, prompt,
> tombol dashboard, atau permintaan siapa pun. Angka salah yang keluar membawa
> nama BPS lebih buruk daripada tidak ada jawaban sama sekali.
>
> Ditegakkan mesin di `scripts/answer_gate.py` (`check_numeric_grounding`),
> bukan oleh kalimat di prompt.

# MARAWA AI — Asisten Statistik Padang Pariaman

Paket requirement dan desain untuk **conversational AI agent** Pelayanan Statistik Terpadu (PST) BPS Kabupaten Padang Pariaman melalui WhatsApp. Agent dapat memakai tools, menjaga konteks lintas turn, mengambil data, melakukan analisis statistik, membuat grafik/ekspor, menjawab pertanyaan lanjutan, dan melakukan live handover ke admin. RAG merupakan salah satu tool agent, bukan keseluruhan sistem.

Brand **MARAWA** sudah muncul pada kanal resmi BPS Kabupaten Padang Pariaman sebagai “Mari Tanya Lewat WA”; produk ini melanjutkannya sebagai **MARAWA AI — Asisten Statistik Padang Pariaman**.[8]

## Keputusan yang sudah dikunci

| Area | Keputusan |
|---|---|
| Pengguna | Masyarakat umum/PST |
| Bahasa | Bahasa Indonesia |
| Kanal | WhatsApp melalui Baileys |
| LLM primary | `PRIMARY_MODEL` via environment; diarahkan ke Gemini 3.1 Flash yang tersedia pada provider |
| LLM fallback | `FALLBACK_MODEL=deepseek-v4-flash` |
| Data terstruktur | PostgreSQL existing melalui role dan views read-only |
| BPS WebAPI mirror | PostgreSQL raw snapshots + normalized/serving data untuk domain `1306`, SIMDASI `1306000`, census, publication, glosarium |
| RAG | PostgreSQL/pgvector + full-text search; dokumen resmi, publikasi, WebAPI BPS, dan metadata |
| Agent runtime | Multi-step plan → tool → observe → analyze → validate → respond; typed working memory lintas turn |
| Analisis | Deterministic statistical tools + isolated analysis sandbox, dengan reproducible lineage |
| Artefak | Grafik, tabel, CSV/XLSX/PDF yang dapat dipakai lagi pada follow-up |
| Anti-jailbreak | CaMeL-inspired control/data-flow separation, provenance/taint, scoped capabilities, Rule-of-Two, effect gates, adaptive red-team |
| Backend | FastAPI |
| Dashboard | Next.js, lengkap dengan RBAC, TOTP, knowledge management, monitoring, feedback, model/prompt config, audit, dan live handover |
| WhatsApp worker | Node.js + Baileys |
| Queue/cache | Redis |
| Deployment | Docker Compose pada VPS |
| Di luar scope/kurang bukti | Bot menyatakan belum dapat menemukan jawaban, lalu menawarkan chat admin atau tautan form publik |
| Retensi chat | Tanpa batas waktu untuk analitik, dengan kontrol akses, audit, pseudonimisasi analitik, dan review kebijakan berkala |

## Sumber resmi unit kerja

- Website: <https://padangpariamankab.bps.go.id>
- PPID: <https://ppid.bps.go.id/?mfd=1306>
- Domain WebAPI BPS Padang Pariaman: `1306`

Portal PPID unit menyatakan permintaan data statistik dilayani melalui PST dan menyediakan kontak resmi unit.[1] WebAPI BPS menyediakan akses JSON untuk publikasi, siaran pers, tabel statistik, tabel dinamis, infografik, dan konten BPS lintas domain.[3][9]

## Peta dokumen

1. [`AGENT.md`](AGENT.md) — kontrak perilaku agent produksi dan prompt template.
2. [`AGENTS.md`](AGENTS.md) — aturan bagi coding agents yang membangun sistem.
3. [`docs/00-INDEX.md`](docs/00-INDEX.md) — indeks dan urutan baca.
4. [`docs/01-PRD.md`](docs/01-PRD.md) — product requirements document.
5. [`docs/02-AGENT-RUNTIME.md`](docs/02-AGENT-RUNTIME.md) — agent loop, memory, tools, skills, analysis, dan follow-up.
6. [`docs/02-SCOPE-AND-CONVERSATION.md`](docs/02-SCOPE-AND-CONVERSATION.md) — scope, intent, alur percakapan, dan copy.
7. [`docs/03-ARCHITECTURE.md`](docs/03-ARCHITECTURE.md) — arsitektur dan alur data.
8. [`docs/04-RAG-AND-DATA.md`](docs/04-RAG-AND-DATA.md) — ingestion, retrieval, structured query, dan grounding.
9. [`docs/05-DATABASE.md`](docs/05-DATABASE.md) — model data, role DB, dan skema.
10. [`docs/06-DASHBOARD-AND-HANDOVER.md`](docs/06-DASHBOARD-AND-HANDOVER.md) — dashboard dan takeover admin.
11. [`docs/07-WHATSAPP-WEBHOOK.md`](docs/07-WHATSAPP-WEBHOOK.md) — Baileys, webhook internal, idempotensi, dan delivery.
12. [`docs/08-API-CONTRACT.md`](docs/08-API-CONTRACT.md) — REST/SSE contracts.
13. [`docs/09-SECURITY-PRIVACY.md`](docs/09-SECURITY-PRIVACY.md) — threat model, controls, dan data governance.
14. [`docs/09A-ANTI-JAILBREAK.md`](docs/09A-ANTI-JAILBREAK.md) — anti-jailbreak/prompt-injection subsystem.
15. [`docs/09B-ANTI-JAILBREAK-REDTEAM.md`](docs/09B-ANTI-JAILBREAK-REDTEAM.md) — attack corpus, fuzzing, eval, dan release gates.
16. [`docs/09C-ANTI-JAILBREAK-RESEARCH.md`](docs/09C-ANTI-JAILBREAK-RESEARCH.md) — deep research, evidence review, benchmark limits, dan architecture rationale.
17. [`docs/10-TEST-EVALUATION.md`](docs/10-TEST-EVALUATION.md) — test pyramid dan agent/RAG/LLM evaluation.
18. [`docs/11-DEPLOYMENT.md`](docs/11-DEPLOYMENT.md) — Docker VPS, konfigurasi, backup, dan release.
19. [`docs/12-OBSERVABILITY-RUNBOOK.md`](docs/12-OBSERVABILITY-RUNBOOK.md) — SLO, alert, dan incident runbook.
20. [`docs/13-ADR.md`](docs/13-ADR.md) — keputusan arsitektur.
21. [`docs/14-IMPLEMENTATION-PLAN.md`](docs/14-IMPLEMENTATION-PLAN.md) — fase dan acceptance gate.
22. [`docs/15-OPEN-QUESTIONS.md`](docs/15-OPEN-QUESTIONS.md) — input organisasi yang masih dibutuhkan.
23. [`docs/16-GLOSSARY.md`](docs/16-GLOSSARY.md) — glosarium statistik dan teknis.
24. [`docs/17-BPS-WEBAPI-DATA.md`](docs/17-BPS-WEBAPI-DATA.md) — endpoint graph, raw/normalized schema, full/update/resume workflow, quality gates.
25. [`docs/18-WHATSAPP-DATA-ANSWER-FORMATS.md`](docs/18-WHATSAPP-DATA-ANSWER-FORMATS.md) — deterministic WhatsApp data-answer formats.
26. [`docs/19-BPS-BOOTSTRAP-CHECKLIST.md`](docs/19-BPS-BOOTSTRAP-CHECKLIST.md) — checklist bootstrap/live-status database BPS.
27. [`docs/20-BPS-MANUAL-UPDATE-WORKFLOW.md`](docs/20-BPS-MANUAL-UPDATE-WORKFLOW.md) — manual targeted update dari identifier yang Tah berikan.
28. [`docs/21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`](docs/21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md) — hasil eksplorasi live, candidate offering, probing, pagination, dan typed query contract.
29. [`docs/22-BPS-QUERY-PROTOTYPE-TEST-MATRIX.md`](docs/22-BPS-QUERY-PROTOTYPE-TEST-MATRIX.md) — SQL prototype read-only, live assertions, candidate search tests, dan P0/P1 query gaps.
30. [`docs/23-BPS-OPTIMIZED-REGISTRY-AND-TYPED-CONTRACTS.md`](docs/23-BPS-OPTIMIZED-REGISTRY-AND-TYPED-CONTRACTS.md) — optimasi context, registry design, typed contracts, dan candidate scoring simulation.
31. [`docs/24-BPS-QUERY-REGISTRY-DDL-DRAFT.md`](docs/24-BPS-QUERY-REGISTRY-DDL-DRAFT.md) — registry DDL + status build terkini (sudah di-build).
32. [`docs/25-PLANNING-AUDIT-STATUS.md`](docs/25-PLANNING-AUDIT-STATUS.md) — audit canonical: actual state, drift corrected, blockers, dependency graph, next gates.
33. [`docs/26-BPS-UNIT-REVIEW-PACKET.md`](docs/26-BPS-UNIT-REVIEW-PACKET.md) — paket review unit untuk data owner (13 dataset blocked_quality).
34. [`docs/27-BPS-SERVICE-MENU-FLOW.md`](docs/27-BPS-SERVICE-MENU-FLOW.md) — layanan menu 1–5, idle timeout 5 mnt, admin handover SLA 3 mnt + busy/cancel, state machine, konfigurasi, golden episodes 012–017.

## Prinsip desain

- **Evidence first:** angka, definisi, unit, periode, wilayah, dan status revisi harus berasal dari sumber terpilih.
- **Agent, bukan FAQ bot:** agent dapat merencanakan dan menjalankan beberapa tool steps hingga tujuan statistik pengguna terpenuhi.
- **Conversational continuity:** follow-up mewarisi goal, indikator, wilayah, periode, result, analysis, dan artifact aktif.
- **No unrestricted text-to-SQL:** agent hanya boleh memakai tools query yang tervalidasi, berparameter, read-only, dan berbasis katalog dataset.
- **Abstain beats hallucinate:** tanpa bukti memadai, jangan menebak.
- **Human takeover is a first-class state:** saat admin aktif, bot berhenti total pada chat itu sampai admin mengembalikan kendali.
- **Provider portable:** model ID, endpoint, key, timeout, dan fallback dikonfigurasi; tidak di-hard-code.
- **Traceable:** setiap jawaban menyimpan evidence IDs, versi prompt/model, latency, dan keputusan routing.
- **Policy outside the model:** jailbreak tidak bisa menaikkan tool/data/memory permission karena enforcement final berada pada server sebelum dan sesudah model.
- **Assume model compromise:** trusted task contracts, provenance/taint, scoped capabilities, Rule-of-Two, dan final effect gates mencegah unsafe draft menjadi unauthorized action.

## Status terkini (2026-08-15, pasca-audit)

**Database foundation sudah di-build; belum runtime-ready.** Agent/WhatsApp tetap tahap planning. Audit canonical dan gates: [`docs/25-PLANNING-AUDIT-STATUS.md`](docs/25-PLANNING-AUDIT-STATUS.md).

> **Cara membaca angka di dokumen ini.** Audit 15 Agustus menemukan bahwa seluruh metrik verifikasi proyek ini berasal dari fixture yang ditulis oleh penulis sistem dan dinilai terhadap ekspektasi penulis yang sama. Angka seperti "Recall@3 1.000" mengukur konsistensi internal, **bukan** kualitas terhadap pertanyaan warga. Setiap angka di bawah kini membawa label sumbernya. Angka tanpa label sinyal eksternal belum boleh dipakai sebagai bukti kesiapan.

### Sudah dibangun dan live di PostgreSQL lokal

- **BPS WebAPI mirror** domain `1306` / SIMDASI `1306000`: 47 tabel SIMDASI (40.525 cells), 335 variabel Dynamic (68.220 facts + 918 quarterly), Census SP2010/ST2023 answerable, 602 publikasi.
- **Serving view P0 fixed** (migration `001`): Dynamic subperiod + `unit_state`; SIMDASI `row_role` category/kabupaten/kecamatan; unit PDRB dari judul (`miliar rupiah`), bukan `Rp`; visible duplicate keys 0.
- **`bps_registry` built & published** (migration `002` + `scripts/build_bps_registry.py`): 1.148 datasets, 1.458 measures, 1.026 dimensions, 7.371 dimension items, 18 geografi canonical + 34 alias lintas family, 6 query templates runtime-safe (semua declared parameter terpakai di SQL), 13 dataset `blocked_quality`. Publish dijaga integrity gates + checksum; rebuild atomic.
- **Audit caveat (15 Aug 2026, hardening + open-items selesai):** runtime DB role `marawa_runtime_ro` ✅; registry version retention ✅; `quality_flags` terpersist ✅; migration ledger + reversible up/down ✅; geography canonical 18 ✅; **Census typed category registry** ✅ (cardinality > 0, total items 999); **display-label normalization** ✅ (0 HTML); **query templates runtime-bindable + binder validator** ✅ (semua param terpakai, injection/type/required ditolak); **unit review packet** ✅ (`docs/26` + Excel); **golden episode harness** ⚠️ (dilaporkan ulang jujur pasca-audit: 15 assertion dieksekusi, 5 dari 19 episode belum pernah diuji; mode llm diblokir OQ-05). **Remediasi audit 15 Aug** ✅ (unit tidak lagi ditebak, row limit benar-benar server-side, privilege di-assert mesin — tabel lengkap di `docs/25`). Sisa eksternal: OQ-07 internal views, unit approval data owner — dan **sinyal eksternal pertama yang masih nol**.

### Sudah teruji, dengan label kekuatan buktinya

| Yang diuji | Hasil | Kekuatan bukti |
|---|---|---|
| SQL query prototypes, transaksi `READ ONLY` | 13/13 PASS | **Kuat** — dijalankan lawan DB asli |
| Invariant unit + binder (`tests/test_unit_and_binder_invariants.py`) | 15 PASS | **Kuat** — gagal duluan sebelum fix |
| Privilege runtime role (`scripts/check_runtime_privileges.py`) | assertion positif + negatif | **Kuat** — lawan katalog PostgreSQL |
| Candidate scoring, 60 kalimat | Recall@3 & MRR — lihat catatan | **Lemah (sintetis)** — kalimat, kunci jawaban, dan scorer ditulis penulis yang sama, lalu scorer dikalibrasi sampai metrik naik |
| Candidate offering cross-family | golden family 1.000, agreement 0.85 | **Lemah (sintetis)** — set yang sama |
| Golden episode harness | 15 assertion dieksekusi; **5 dari 19 episode belum pernah dieksekusi sama sekali** | **Sebagian** — lihat catatan di bawah |
| Golden multi-turn episodes | 19 episode / 46 turn | Fixture, bukan hasil uji |

**Catatan golden episode.** Dari 46 turn: 15 dieksekusi lawan offering engine sungguhan, 25 diblokir (session-policy engine dan query runtime belum ada), 6 hanya lint fixture. Episode `011`, `014`, `015`, `016`, `017` tidak punya satu pun assertion yang bisa dieksekusi. Harness kini melaporkan ketiga angka terpisah dan menolak menggabungkannya jadi "19/19 PASS".

**Catatan metrik sintetis.** Sampai `data/evals/pst-real-questions.json` diisi 30 pertanyaan nyata dari loket PST, angka retrieval berlabel `synthetic_author_written` dan tidak boleh dikutip sebagai ukuran kesiapan. Sinyal eksternal pertama proyek ini belum ada.

- Kontrak JSON: discovery → candidate list → user select → inspect → typed query, selection envelope wajib (`packages/contracts/bps-query-contracts.schema.json`).

### Kebijakan data yang berlaku

- **Tidak ada cronjob/polling BPS.** Update hanya manual: Tah memberi identifier dari katalog Excel (`Inbox/BPS_CATALOG_TABEL_1306.xlsx`), lalu targeted pull resource tersebut.
- **List tabel dulu sebelum query:** goal baru tanpa ref/kode exact selalu menampilkan kandidat (`S/D/C/P`) dan menunggu user memilih; rekomendasi agent bukan persetujuan.
- Numeric answer wajib evidence; marker (`–`, `...`, `NA`) tidak di-coerce ke nol; precision tolerance per sumber; unit tidak ditebak model.

### Belum dibangun (masih planning)

Agent runtime, probing/working memory, query compiler dari template registry, formatter/grounding validator, evaluation harness live, WhatsApp adapter, dashboard, dan deployment. Urutan aman ada di `docs/21` §14 dan `docs/24` §10.


## Sources

[1] https://ppid.bps.go.id/?mfd=1306 — Portal PPID BPS Kabupaten Padang Pariaman
[3] https://webapi.bps.go.id/developer — WebAPI BPS
[8] https://padangpariamankab.bps.go.id/id/publication/2026/02/27/632a70da42c6c2f59eb034ce/kabupaten-padang-pariaman-dalam-angka-2026.html — Kabupaten Padang Pariaman Dalam Angka 2026 / MARAWA
[9] https://webapi.bps.go.id/documentation — Dokumentasi WebAPI BPS
