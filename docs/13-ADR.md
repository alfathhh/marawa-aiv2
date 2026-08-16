# Architecture Decision Records

## ADR-001 — FastAPI + Next.js + Baileys worker + PostgreSQL/pgvector + Redis

**Status:** Accepted

**Context:** Target deployment adalah Docker VPS; membutuhkan Python ecosystem untuk RAG/data, dashboard modern, dan Node runtime untuk Baileys.

**Decision:** FastAPI mengelola orchestration/API; Next.js dashboard; Baileys pada isolated TypeScript worker; app PostgreSQL/pgvector; Redis untuk streams/cache/locks.

**Consequences:** Polyglot deployment tetapi batas tanggung jawab jelas. Shared contracts harus generated/versioned.

---

## ADR-002 — Baileys sebagai channel adapter, bukan core dependency

**Status:** Accepted

**Context:** WhatsApp integration dipilih unofficial Baileys. Baileys menggunakan WhatsApp Web WebSocket tanpa browser.[7]

**Decision:** Semua Baileys code/session/protocol berada pada `wa-worker` dan berkomunikasi melalui versioned internal events/commands.

**Consequences:** Memudahkan penggantian ke official API; menambah internal webhook/idempotency complexity.

---

## ADR-003 — Provider-configurable Gemini primary, DeepSeek V4 Flash fallback

**Status:** Accepted

**Decision:** `PRIMARY_MODEL` tidak hard-coded; startup capability probe memastikan structured output/tool support. Default fallback `deepseek-v4-flash`.

**Rationale:** Exact Gemini alias berbeda antar provider. Google yang terverifikasi mendokumentasikan `gemini-3.1-flash-lite`; DeepSeek mendokumentasikan `deepseek-v4-flash`.[5][6]

**Consequences:** Config release/eval wajib menyimpan exact model ID/provider/version.

---

## ADR-004 — Multi-path agent tools dan larangan unrestricted text-to-SQL

**Status:** Accepted

**Decision:** RAG, typed structured data, statistical analysis, visualization, dan export adalah tool families agent. LLM tidak menerima tool yang mengeksekusi SQL bebas atau generic shell/network/filesystem.

**Consequences:** Dataset onboarding membutuhkan registry/view/metadata, tetapi risiko exfiltration dan angka salah berkurang drastis.

---

## ADR-005 — Source PostgreSQL dipisah dan read-only

**Status:** Accepted

**Decision:** Existing source DB memakai dedicated role, allowlisted views, transaction read-only, timeout, row limit. Operational app DB terpisah.

**Consequences:** Perlu koordinasi data owner dan contract testing; app bebas menyimpan chat/index tanpa mengotori source.

---

## ADR-006 — Human takeover sebagai transactional conversation state

**Status:** Accepted

**Decision:** Handover bukan sekadar flag UI. State/version disimpan DB; AI outbound membutuhkan final authorization sebelum send.

**Consequences:** Menambah state/locking, tetapi mencegah bot dan admin berbicara bersamaan.

---

## ADR-007 — PostgreSQL hybrid search untuk MVP

**Status:** Accepted

**Decision:** pgvector + PostgreSQL full-text search + reranking, tanpa vector database terpisah.

**Rationale:** Single-VPS simplicity, transactional metadata/versioning, dan scale awal cukup.

**Review trigger:** Corpus/latency/recall tidak memenuhi SLO setelah tuning/indexing atau operasional DB terganggu.

---

## ADR-008 — Infinite raw-chat retention is policy-constrained

**Status:** Accepted with mandatory review

**Decision:** Memenuhi keputusan produk “simpan tanpa batas untuk analitik”, tetapi raw operational data encrypted dan derived analytics pseudonymous. Policy/lawful basis/access/delete/export/review harus ditandatangani sebelum production.

**Consequences:** Storage dan privacy burden meningkat. Schema harus memungkinkan policy dipendekkan tanpa redesign.

---

## ADR-009 — Configuration and knowledge are versioned releases

**Status:** Accepted

**Decision:** Prompt, model config, corpus/source version tidak diedit langsung in place pada production. Workflow draft → evaluate/review → publish → rollback.

**Consequences:** Auditability dan safe rollback lebih baik; dashboard/workflow lebih kompleks.

---

## ADR-010 — Redis is acceleration/queue, app DB is source of truth

**Status:** Accepted

**Decision:** Inbox/outbox dan conversation state durable di PostgreSQL. Redis Streams/cache/locks dapat direkonstruksi/replayed.

**Consequences:** Lebih tahan Redis loss; perlu reconciliation scheduler.

---

## ADR-011 — Domain-bounded conversational agent, bukan chatbot RAG

**Status:** Accepted

**Context:** Produk harus mampu mengambil data, melakukan analisis, menjawab pertanyaan lanjutan, dan membuat artefak seperti agent umum, tetapi hanya dalam statistik/BPS.

**Decision:** Runtime memakai bounded plan → tool → observe → analyze → validate loop, typed working memory lintas turn, statistical skills, dan artifact references. RAG adalah satu tool, bukan arsitektur utama.

**Consequences:** Kemampuan follow-up dan analisis meningkat; schema, evaluation, observability, dan security harus mencakup agent runs/steps dan memory lineage.

---

## ADR-012 — Reproducible analysis sandbox

**Status:** Accepted

**Decision:** Operasi typed deterministik menjadi default. Analisis kompleks dijalankan pada worker/sandbox non-root, ephemeral, tanpa network/secrets/source DB/host filesystem, dengan input result IDs approved dan resource limits.

**Consequences:** Mendukung analisis lebih luas tanpa memberi agent akses host. Membutuhkan sandbox hardening, method/parameter lineage, artifact storage, dan adversarial tests.

---

## ADR-013 — Typed working memory and immutable result references

**Status:** Accepted

**Decision:** Follow-up context disimpan sebagai typed working memory yang menunjuk immutable evidence/result/analysis/artifact IDs. Model mengusulkan patch dan server memvalidasinya. Fakta statistik tidak disimpan sebagai memory text tanpa provenance.

**Consequences:** Follow-up konsisten dan auditable; perlu compaction/reference validation dan lifecycle untuk context/artifacts.

---

## ADR-014 — CaMeL-inspired control/data-flow security with scoped capabilities

**Status:** Accepted

**Context:** Research menunjukkan model tidak dapat dijadikan trust boundary untuk memisahkan instruksi dari data; prompt/classifier/marking defenses dapat ditembus adaptive attacker. Agent tetap perlu data-dependent statistical workflows.

**Decision:** Pisahkan trusted control plane dari tainted data plane. Direct-user goal diikat menjadi task contract; setiap value membawa provenance/taint; declassification hanya typed dan bounded; setiap tool/data/memory/artifact/reply effect memerlukan opaque server-created capability. Autonomous runs tunduk pada Agents Rule of Two. Primary/fallback memakai enforcement instance yang sama.

**Alternatives rejected:** prompt-only hardening, classifier-as-authorizer, unrestricted plan-then-execute, atau menghapus seluruh data-dependent branching.

**Consequences:** Blast radius dan unauthorized effects dapat dibatasi secara deterministik, tetapi schema/runtime/eval lebih kompleks dan utility harus diukur. Klaim model-level tetap empiris; exact releases memerlukan adaptive/repeated effect-based evaluation.

**Review trigger:** capability model menghambat approved statistical task class, new external-write tool/channel, sensitive/private dataset onboarding, atau research baru menyediakan stronger verified architecture.

---

## ADR-015 — Evidence strength is recorded with every reported metric

**Status:** Accepted (15 Aug 2026)

**Context:** Audit menemukan seluruh metrik verifikasi proyek bersifat loop tertutup: fixture, kunci jawaban, dan sistem yang dinilai ditulis pihak yang sama, lalu scorer dikalibrasi terhadap set itu. Dashboard status penuh hijau (`Recall@3 1.000`, `19/19 PASS`, `150 PASS`) sementara tidak ada satu pun sinyal dari luar sistem. Efeknya bukan sekadar kosmetik: pengambilan keputusan scope memakai angka-angka itu sebagai bukti kesiapan.

**Decision:** Setiap angka yang dilaporkan membawa label kekuatan bukti. Runner scorer melabeli set evaluasinya (`synthetic_author_written` vs `real_pst_questions`). Harness episode melaporkan `exercised` / `lint_only` / `blocked` terpisah dan tidak boleh menggabungkannya. `scripts/validate_docs.py` menggagalkan build jika metrik loop tertutup muncul tanpa label. Hitungan status hanya boleh ditulis di `docs/25`.

**Alternatives rejected:** menghapus metrik sintetis sepenuhnya (menghilangkan smoke test yang berguna); mempertahankan pelaporan lama dengan disclaimer prosa saja (terbukti tidak dibaca — kontradiksi angka bertahan lintas dokumen selama berbulan-bulan).

**Consequences:** Status terlihat lebih buruk dan itu memang tujuannya — status sekarang mencerminkan yang benar-benar diketahui. Threshold regresi retrieval ditangguhkan sampai set nyata tersedia.

**Review trigger:** `data/evals/pst-real-questions.json` terisi; atau muncul kelas metrik baru yang belum punya label kekuatan bukti.

---

## ADR-016 — Unit provenance gates answerability at measure level

**Status:** Accepted (15 Aug 2026)

**Context:** Invariant "unit tidak ditebak" adalah aturan yang paling sering diulang di seluruh pack, tetapi implementasinya menebak di tiga tempat: unit mata uang diturunkan dari string judul tabel via ILIKE, kata "menurut" pada judul dianggap bukti measure tanpa satuan, dan gate `blocked_quality` hanya berlaku bila SELURUH measure dalam dataset tidak diketahui unitnya — sehingga measure tanpa unit di dalam dataset yang baik tetap bisa di-query dan dikutip.

**Decision:** Unit hasil heuristik mendapat state `review_required` dan tidak pernah `known`. Cabang "menurut" dihapus. Gate answerability pindah ke level measure (`queryable`, `quality_flags`), diperkuat CHECK constraint di PostgreSQL sehingga builder mana pun di masa depan tidak bisa meregresikannya diam-diam. Agregasi untuk unit tidak diketahui adalah `unknown` (melarang penjumlahan), bukan `count`.

**Alternatives rejected:** memblokir seluruh dataset yang mengandung satu measure bermasalah (membuang data yang sah); mempercayai judul tabel sebagai metadata kolom (justru akar masalahnya).

**Consequences:** Jumlah measure non-queryable akan naik pada rebuild berikutnya; kenaikan itu adalah perbaikan yang bekerja, bukan regresi. Delta-nya masuk paket review `docs/26` untuk keputusan data owner.

**Review trigger:** data owner menyetujui semantik unit untuk sekelompok measure; atau BPS menyediakan metadata unit level kolom.

---

## ADR-017 — Scope dipotong ke Slice 1 sebelum implementasi runtime

**Status:** Accepted (15 Aug 2026)

**Context:** "MVP" mencakup WhatsApp + RAG + agent loop + analisis + chart + export + dashboard (RBAC/TOTP/knowledge/analytics) + audit + backup: realistis 4–6 bulan untuk satu tim, disusun sebagai ~49.000 kata spesifikasi terhadap 0 baris kode runtime, dengan kelima baris sign-off masih TBD. Anti-jailbreak suite (7.674 kata) berdiri sebagai release gate produksi untuk sistem yang blast radius-nya saat ini adalah data yang sudah publik.

**Decision:** Slice 1 = WhatsApp → discovery → user pilih tabel → typed query → jawaban bersumber. Handover cukup mengirim nomor petugas lalu berhenti; tanpa antrean, claim, atau SLA. Semua sisanya ditunda ke Slice 2+. Fase 9.1 dicabut dari jalur kritis; kontrol murah yang sudah efektif tetap wajib.

**Alternatives rejected:** melanjutkan urutan fase 0–10 (menunggu 12 blocker eksternal, tidak satu pun bergerak dalam berbulan-bulan); membangun dashboard lebih dulu (tidak ada yang bisa ditangani sebelum bot menjawab apa pun).

**Consequences:** Slice 1 melewati OQ-04, OQ-06, OQ-07, dan OQ-10 sepenuhnya. Tersisa OQ-02 dan OQ-05. Race condition handover yang dirancang di `docs/27` §5 tidak dibangun sampai terbukti dibutuhkan pengguna nyata.

**Review trigger:** Slice 1 berjalan dengan pengguna nyata; atau data internal (OQ-07) disetujui, yang menaikkan blast radius dan mengaktifkan kembali Fase 9.

## ADR governance

ADR baru menggunakan ID berikutnya, status Proposed/Accepted/Superseded/Rejected, context, decision, alternatives, consequences, dan review trigger. Jangan edit keputusan lama seolah tidak pernah ada; supersede dengan ADR baru.

## Sources

[5] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite — Gemini 3.1 Flash-Lite
[6] https://api-docs.deepseek.com/updates — DeepSeek API Change Log
[7] https://baileys.wiki — Baileys Documentation
