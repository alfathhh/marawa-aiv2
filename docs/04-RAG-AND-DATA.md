# RAG dan Data Architecture

## 1. Prinsip

RAG adalah satu keluarga tools dalam MARAWA AI agent. Agent memakai **tiga execution paths** yang dapat dikombinasikan dalam satu run:

1. **Structured data path** untuk angka/tabel—query deterministik atas allowlisted PostgreSQL views. **Built (15 Aug):** serving views `bps_serving_dynamic/simdasi/census` + `bps_publications` (P0 fixed: subperiod/row_role/unit), role read-only `marawa_runtime_ro`, query templates runtime-safe via `scripts/bps_template_binder.py`.
2. **Unstructured knowledge path** untuk definisi, metodologi, narasi, publikasi, layanan, dan FAQ—hybrid RAG atas approved corpus. **Status desain:** pipeline FTS/vector di bawah, vector ANN deferred sampai evaluasi gap (`docs/23`, `docs/25`).
3. **Analysis/artifact path** untuk mengolah result ter-grounding menjadi perbandingan, tren, ranking, statistik deskriptif, visualisasi, dan file ekspor.

LLM tidak menjadi database, kalkulator, atau sumber fakta.

Agent dapat berpindah antar-path secara iteratif. Contoh: cari dataset → inspect metadata → query dua periode → analisis pertumbuhan → cari narasi pendukung → buat grafik → jawab dan menyimpan seluruh lineage untuk follow-up.

## 2. Source registry dan prioritas

| Priority | Source | Use |
|---:|---|---|
| 1 | Curated/approved internal statistical views | Angka resmi internal yang sudah berstatus boleh diseminasi |
| 2 | Website BPS Kabupaten Padang Pariaman | Publikasi, tabel, berita, layanan unit |
| 3 | PPID BPS Kabupaten Padang Pariaman | Profil, layanan, regulasi, informasi publik |
| 4 | WebAPI BPS domain `1306` | Konten JSON dan tabel resmi |
| 5 | Approved internal documents | SOP/FAQ/layanan dengan owner dan approval |
| 6 | BPS pusat/provinsi | Definisi/metodologi/konteks bila relevan |

Data BPS yang didiseminasikan dalam tabel/publikasi bersifat agregat; metadata dan sumber wajib dipertahankan. Kebijakan diseminasi juga membedakan data mikro dan agregat serta menyatakan data BPS yang dikutip harus mencantumkan sumber.[4]

## 3. Source lifecycle

```text
DISCOVERED → FETCHED → PARSED → VALIDATED → REVIEW_REQUIRED → APPROVED → INDEXED → ACTIVE
                                                ↘ REJECTED
ACTIVE → SUPERSEDED | ARCHIVED | QUARANTINED
```

Tidak ada dokumen yang menjadi evidence publik sebelum `APPROVED`.

Selain approval, ingestion memberi taint label pada instruction-like content, active content, fake role boundaries, encoded imperatives, dan suspicious metadata/table cells. Taint tidak otomatis berarti fakta dokumen salah; ia berarti teks tidak pernah boleh menjadi agent instruction. Active content default dikarantina.

## 4. Ingestion pipeline

1. Fetch via allowlisted connector.
2. Compute content checksum and source identity.
3. Malware/type/size check untuk upload.
4. Parse layout-aware: heading, paragraph, table, footnote, page.
5. Extract metadata: title, publisher, release date, reference period, geography, catalog, URL, access class.
6. Detect duplicate/version/revision.
7. Validate release status dan policy label.
8. Human review bila source internal atau parser confidence rendah.
9. Chunk per semantic section/table; jangan memutus definisi dari indikator.
10. Generate embedding dengan `EMBEDDING_MODEL` terpisah dari chat model.
11. Build PostgreSQL full-text index.
12. Publish version atomically; previous version remains auditable.

## 5. Chunking

### Narrative

- Target 400–800 tokens, overlap 10–15%.
- Pertahankan heading hierarchy, publication, page, reference period.
- Jangan gabungkan dua topik/indikator berbeda demi ukuran chunk.

### Tables

- Simpan table title, dimensions, units, notes, source, period, geography.
- Untuk tabel besar: parent table record + row-group chunks.
- Nilai numerik utama sebaiknya dinormalisasi ke structured dataset, bukan hanya vector text.
- Footnotes ikut setiap relevant row-group melalui reference IDs.

### Metadata/concept definitions

- Satu concept/indicator per chunk.
- Simpan aliases/acronyms dan valid-from/valid-to.

## 6. Hybrid retrieval

```text
Query normalization
  → intent/slot extraction
  → metadata filters (domain, geography, period, source type, active version)
  → parallel BM25/Postgres FTS + vector ANN
  → reciprocal rank fusion
  → cross-encoder/LLM-free reranker preferred
  → authority/freshness boost
  → evidence diversity/dedup
  → context budget builder
```

Retrieval result minimum:

```json
{
  "evidence_id": "ev_01...",
  "source_version_id": "sv_...",
  "chunk_id": "ch_...",
  "title": "...",
  "quote_or_rows": "...",
  "page": 42,
  "period": "2025",
  "geography": "Kabupaten Padang Pariaman",
  "unit": "persen",
  "url": "https://...",
  "authority": 1.0,
  "retrieval_score": 0.91
}
```

## 7. Structured dataset registry

Setiap dataset harus memiliki:

- `dataset_id`, owner, description;
- view name allowlisted;
- dimensions/measures/filter schema;
- indicator definition, unit, decimal policy;
- geographic level/codes;
- reference period semantics;
- source/publication and citation template;
- max rows, timeout, cache TTL;
- release status and access classification;
- validation queries and sample expected results.

BPS WebAPI menggunakan domain dan key token serta menyediakan endpoint untuk dynamic data, static table, publication, press release, strategic indicator, glossary, dan lainnya.[3][9] Connector wajib mem-pin `domain=1306` untuk sumber lokal kecuali query memang meminta pembanding lain yang diizinkan.

MARAWA memirror lima family prioritas: SIMDASI `1306000`, Dynamic Data `1306`, Census Data yang matching Padang Pariaman, Publication `1306`, dan Glosarium global. Runtime query memakai normalized/serving PostgreSQL, bukan WebAPI call on-demand. Raw/current/serving schema, update semantics, and commands bersifat normative di `17-BPS-WEBAPI-DATA.md`.

**Status: BUILT (15 Aug).** Registry live di schema `bps_registry` — 1.148 datasets, 1.458 measures, 1.026 dimensions, 7.371 items (termasuk Census typed axes + total 999), 18 geography canonical + 34 alias, 6 query templates runtime-safe; quality `unit_review_required` terpersist; candidate-set version pinning didukung (retired snapshots retained). Builder deterministik `scripts/build_bps_registry.py` + integrity gates; klasifikasi tidak menebak unit (unknown_review → blocked_quality). Validators/binder di `scripts/bps_template_binder.py`. Item cukup: registry menggantikan "Setiap dataset harus memiliki" di atas sebagai sumber truth live; kolom yang belum terisi penuh (owner, citation template) diisi saat source internal disetujui (OQ-07).

## 8. Query planner policy

Discovery adalah langkah agentic terpisah dari fact query. Untuk goal baru tanpa candidate ref/kode exact, grouped candidate set dari beberapa source approved **wajib ditampilkan sebelum query facts**, sekalipun satu kandidat tampak dominan. Candidate refs memakai prefix per family (`S` SIMDASI, `D` Dynamic, `C` Census, `P` Publication), cursor pagination per family, dan opaque server-owned `candidate_id`. Agent boleh merekomendasikan, menambah source, dan rerank; rekomendasi tidak auto-select. Query baru diizinkan setelah user memilih kandidat. Valid explicit ref/kode exact serta follow-up active dataset tidak perlu discovery ulang. Detail normative: `21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`.

- Intent model hanya memilih tool/dataset + typed parameters.
- LLM tidak mengubah candidate ref menjadi SQL identifier; server me-resolve `candidate_id` melalui registry.
- `inspect_dataset` wajib mendahului query ketika dimension role atau required filters belum ada pada working memory.
- Query quarterly wajib membawa `subperiod`; Census cross-tab wajib membawa setiap material category filter atau explicit total.
- Server rejects unknown dimensions/measures/filters.
- Semua SQL parameterized; identifier berasal registry, bukan user/model text.
- `SET LOCAL statement_timeout`, `transaction_read_only=on`, row limit.
- Blok `;`, DDL/DML, system catalog, cross-schema access, functions unapproved.
- Query result dinormalisasi menjadi typed cells, bukan raw database dump.

## 9. Calculations

Tool `run_stat_analysis` menyediakan operasi typed/formula terdaftar, misalnya:

- absolute difference;
- percentage point difference;
- percentage change;
- share/proportion;
- average yang definisinya approved.

Setiap output menyimpan input evidence IDs, formula ID/version, rounding rule, dan result. Model hanya menarasikan.

## 10. Grounding validator

Sebelum outbound:

- Deteksi semua numeric claims pada answer.
- Pastikan setiap claim dapat diikat ke evidence/result ID.
- Cocokkan unit dan rounding tolerance.
- Pastikan citation URL ada pada evidence allowlist.
- Cek wilayah/periode tidak berubah pada paraphrase.
- Cek source version masih active atau explicitly historical.
- Jika gagal: regenerate dengan feedback validator sekali; setelah itu abstain.

## 11. Conflict resolution

Jika sumber berbeda:

1. Bandingkan definisi, cakupan, tahun referensi, status sementara/revisi, dan unit.
2. Pilih source priority hanya jika comparable.
3. Jika tetap konflik, tampilkan kedua nilai beserta konteks dan tawarkan admin.
4. Catat conflict event untuk knowledge manager.

## 12. Freshness

| Source | Update mode | Stale trigger |
|---|---|---|
| Internal views | Release event/manual owner signal | Melewati owner-defined freshness |
| BPS WebAPI mirror | Manual targeted update berdasarkan identifier yang Tah berikan; audit penuh hanya eksplisit | Known release/revision belum ditarik |
| PPID/service info | Manual verification | Kontak/jam layanan tidak diverifikasi 90 hari |
| Approved internal docs | Event-driven/manual owner signal | Valid-to lewat atau owner tidak reconfirm |

Tidak ada cronjob/polling BPS aktif. Failed/partial manual run mempertahankan last known-good serving state dan menandai freshness, bukan menulis empty dataset. SOP update exact resource ada di `20-BPS-MANUAL-UPDATE-WORKFLOW.md`; eksplorasi/query contract ada di `21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`.

## 13. Evaluation dataset

Golden set minimum mencakup:

- pertanyaan angka jelas/ambigu;
- definisi mirip;
- tahun publikasi vs tahun data;
- unit persen vs percentage point;
- kecamatan/nagari yang namanya mirip;
- revised vs provisional values;
- unanswerable dan out-of-scope;
- prompt injection di dokumen/pesan;
- konflik sumber;
- query besar/rate-limit.

## 14. Admin controls

Knowledge manager dapat upload/sync, melihat parse preview, metadata, table extraction, chunk, diff versi, hasil eval, approve/publish/rollback/quarantine. Setiap perubahan menghasilkan audit event dan tidak menghapus evidence snapshot historis.

## 15. Result, analysis, dan artifact lineage

- `result` adalah normalized output dari query data dan selalu menunjuk dataset/source/evidence.
- `analysis` adalah hasil olahan reproducible dengan method/version, parameters, input result IDs, diagnostics, dan caveats.
- `artifact` adalah chart/table/export yang menunjuk result/analysis IDs serta render specification.
- Follow-up tidak mengandalkan angka yang disalin ke memory; ia memakai IDs tersebut untuk mengambil data aktif yang tervalidasi.
- Jika source/version berubah, hasil lama tetap immutable dan ditandai historical; analisis baru menggunakan source aktif kecuali user meminta reproduksi hasil lama.

## Sources

[3] https://webapi.bps.go.id/developer — WebAPI BPS
[4] https://ppid.bps.go.id/app/konten/0000/Layanan-BPS.html — Layanan dan Kebijakan Diseminasi BPS
[9] https://webapi.bps.go.id/documentation — Dokumentasi WebAPI BPS
