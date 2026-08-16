# Optimized BPS Registry and Typed Contracts — Planning Design

> Status: desain optimasi + kontrak. Serving view fixes dan `bps_registry` **sudah di-build** (izin Tah 15 Aug 2026); runtime/query compiler masih planning.
> Evidence benchmark berasal dari PostgreSQL lokal pada 2026-08-15.

## 1. Objective

Membuat discovery/probing/query agent cepat dan hemat context tanpa menjadikannya menu bot kaku. LLM menangani goal, bahasa natural, rekomendasi, probing, dan strategy; server menangani catalog identity, answerability, pagination, dimensions, SQL templates, evidence, dan validation. Untuk goal baru tanpa candidate ref/kode exact, user harus memilih candidate sebelum fact query. Agent boleh merekomendasikan tetapi tidak auto-select.

## 2. Benchmark baseline

### Fact query PostgreSQL

| Query | Execution time live |
|---|---:|
| Dynamic exact total | 0,187 ms |
| Dynamic 17-kecamatan breakdown | 0,086 ms |
| Dynamic 6-year trend | 0,151 ms |
| Dynamic quarterly exact | 0,142 ms |
| Census exact cross-tab cell | 0,260 ms |
| SIMDASI exact lookup | 0,420 ms |
| Publication FTS title+abstract | 42,550 ms |

Fact DB bukan bottleneck. Existing indexes sudah cukup untuk exact query MVP. Optimization utama harus mengurangi catalog payload, LLM tokens, dan round trips.

### Catalog/context economics

```text
Unified catalog: 1.074 answerable/metadata documents
Full catalog JSON ke LLM: ±206 KB / ±51.535 token
Grouped top candidates: ±1,2 KB / ±306 token
Selected dataset inspect: ±613 B / ±153 token
Prompt reduction: 99,407%
```

Mengirim full catalog ke model tidak diperbolehkan.

### In-memory discovery prototype

```text
Load 1.074 docs from DB: ±244,9 ms (startup/manual refresh)
Linear scan: ±3,0 ms/query
Simple inverted index: ±2,7 ms/query
Inverted index memory prototype: ±6,2 MB
```

Pada katalog kecil sekarang, inverted index hanya memberi improvement kecil karena scoring/normalization mendominasi. MVP boleh memakai in-memory linear/compact index yang sederhana dan deterministic; jangan menambah Elasticsearch/vector DB sebelum golden eval membuktikan perlu.

## 3. Optimization decision

### Chosen MVP architecture

```text
PostgreSQL registry/facts (source of truth)
  → immutable catalog snapshot at API startup/manual refresh
  → compact in-process search structures
  → deterministic canonicalizer + alias lexicon + feature scorer
  → grouped top candidates only
  → user selects candidate (natural text/ref/title)
  → cached inspect metadata
  → parameterized fact query
  → immutable normalized result cache
  → LLM receives only candidate/inspect/result summaries
```

Redis digunakan untuk candidate-set/working-state lintas API instance dan optional result-cache coordination. Redis bukan source of truth dan bukan search engine.

### Deferred

- embeddings/vector search untuk structured catalog;
- Elasticsearch/OpenSearch;
- generic agent-generated SQL;
- per-message WebAPI access;
- premature materialized cube untuk seluruh facts.

Reconsider vector/hybrid retrieval hanya bila alias+lexical+typed-feature scorer gagal target Recall@5 pada golden set.

## 4. Registry logical schema

Semua IDs opaque pada model-facing contract. Source IDs tetap disimpan internal untuk query compiler.

### 4.1 `dataset_registry`

| Field | Purpose |
|---|---|
| `dataset_id` | opaque stable ID |
| `source_family` | `simdasi`, `dynamic`, `census`, `publication` |
| `source_resource_id` | table/variable/event:dataset/publication ID internal |
| `title` / `summary` | display metadata |
| `topic_id` / `topic_name` | grouping/ranking |
| `dataset_shape` | `geography_series`, `category_series`, `cross_tab`, `quarterly_series`, `publication_metadata` |
| `answerability` | `answerable`, `metadata_only`, `blocked_quality`, `unavailable` |
| `period_min/max/latest` | coverage |
| `period_granularity` | annual/quarterly/event/release |
| `source_version_ref` | current version/snapshot lineage |
| `search_document` | canonical precomputed text |
| `search_aliases` | approved aliases/acronyms/known upstream typo |
| `supported_operations` | typed allowlist |
| `quality_flags` | P0/P1 gates |
| `active` | runtime visibility |

Unique internal identity: `(source_family, source_resource_id, source_version_ref)`.

### 4.2 `measure_registry`

| Field | Purpose |
|---|---|
| `measure_id` | opaque ID |
| `dataset_id` | owner dataset |
| `source_measure_id` | column/indicator ID internal |
| `name` / `aliases` | discovery |
| `value_type` | numeric/text/marker |
| `unit_state` | `known`, `unitless`, `unknown_review` |
| `unit_display` / `unit_scale` | deterministic formatting/normalization |
| `decimal_places` | source precision |
| `aggregation_semantics` | additive/non-additive/index/rate/share |
| `allowed_operations` | lookup/trend/rank/share/etc. |
| `marker_policy` | legend reference |
| `comparability_group` | cross-source validation |

`Tidak Ada Satuan` tidak otomatis berarti `unitless`; metadata builder mengklasifikasikan atau memberi `unknown_review`.

### 4.3 `dimension_registry`

| Field | Purpose |
|---|---|
| `dimension_id` | opaque ID |
| `dataset_id` | owner |
| `name` | e.g. wilayah, jenis_kelamin, kelompok_umur |
| `role` | `geography`, `category`, `subperiod` |
| `required` | filter requirement |
| `default_item_id` | only if statistically safe |
| `total_item_id` | explicit upstream Total/Jumlah |
| `cardinality` | inspect summary |
| `display_order` | stable output |

### 4.4 `dimension_item_registry`

| Field | Purpose |
|---|---|
| `item_id` | opaque ID |
| `dimension_id` | owner |
| `source_item_id/code` | compiler input |
| `label` / `aliases` | display/resolution |
| `canonical_geography_id` | only role geography |
| `is_total` | explicit total only |
| `sort_order` | deterministic output |
| `active_period` | optional shape drift |

### 4.5 `geography_registry` + `geography_aliases`

Canonical geography has stable code/name/level/parent. Alias rows carry:

```text
canonical geography ID
source family
source code/item ID
source label
valid period/version
match type: exact_code | approved_alias | historical_name
```

Examples:

```text
1306000 ↔ Census 1306 ↔ Dynamic vervar 18 ↔ SIMDASI 1306000
1306061 ↔ Koto Patamuan ↔ VII Koto Patamuan ↔ PATAMUAN
```

Fuzzy text helps find candidates; approved mapping determines identity.

### 4.6 `query_template_registry`

| Field | Purpose |
|---|---|
| `template_id/version` | immutable compiler version |
| `dataset_shape` | dispatch key |
| `view_name` | allowlisted identifier |
| `parameter_schema` | exact filters |
| `sql_template` | server-owned parameterized SQL |
| `row_limit` / `timeout_ms` | resource guard |
| `result_schema_id` | output validation |
| `validation_rule_ids` | totals/coverage/marker/etc. |

No SQL or view name comes from user/model/candidate text.

### 4.7 Candidate/session state

`candidate_sets` and `candidate_items` may live in Redis with short TTL and optional DB audit summary:

```text
candidate_set_id
conversation_id
normalized_goal_hash
source snapshot version
focused_family
shown refs per family
next cursor per family
selected candidate ID
unresolved slots
expires_at
```

Display refs are append-only within a set and never recycled.

## 5. Search pipeline optimized

```text
1. Agent proposes normalized goal/concepts/action/known slots.
2. Server validates bounded schema.
3. Canonicalizer removes conversational stop terms and expands approved aliases.
4. Candidate index retrieves answerable resources.
5. Feature scorer ranks by:
   - concept/measure match
   - requested action/shape
   - source policy
   - answerability/quality gate
   - period/freshness
   - geography support
   - context/candidate history
   - lexical score
6. Diversity selector avoids five near-identical candidates.
7. Return max 3/source and 2–3 source groups/message.
8. LLM recommends/explains/probes naturally.
```

Candidate score components are observable; candidate text remains untrusted data.

## 6. Cache design

### Catalog snapshot cache

- build at API startup or explicit registry publish;
- immutable version ID/checksum;
- atomic swap only after validation;
- no periodic WebAPI dependency;
- retain previous snapshot for in-flight candidate sets.

### Inspect cache

Key: `dataset_id + registry_version`. Value: compact typed metadata. TTL may be long because version change invalidates key naturally.

### Result cache

Key:

```text
query_template_version
+ normalized typed query spec
+ ordered source snapshot/version IDs
```

Cache stores immutable normalized `result_id`, not free text. It must distinguish subperiod/category/geography exact filters.

### Response/narrative cache

Not default. Agent answers depend on context/probing. Cache deterministic data/result/artifact, not conversational prose.

## 7. Typed contracts

### 7.1 Discovery request

```json
{
  "original_query": "jumlah penduduk berdasarkan kecamatan",
  "goal": {
    "concepts": ["jumlah penduduk"],
    "operation": "breakdown",
    "dimension_roles": ["geography"],
    "known_slots": {"geography_level": "kecamatan"}
  },
  "source_families": ["dynamic", "simdasi", "census", "publication"],
  "page_size_per_family": 3,
  "cursors": {},
  "candidate_set_id": null
}
```

### 7.2 Candidate group response

```json
{
  "candidate_set_id": "opaque",
  "registry_version": "opaque",
  "groups": [
    {
      "source_family": "dynamic",
      "items": [
        {
          "candidate_id": "opaque",
          "display_ref": "D1",
          "title": "Jumlah Penduduk",
          "summary": "Per kecamatan dan jenis kelamin, 2002–2025",
          "period_latest": "2025",
          "answerability": "answerable",
          "match_reasons": ["concept_exact", "geography_breakdown", "latest_exact_values"],
          "recommended": true
        }
      ],
      "next_cursor": "opaque-or-null",
      "has_more": true
    }
  ],
  "probing_hints": [
    {"slot": "period", "question_kind": "exact_or_latest", "information_gain": 0.88}
  ]
}
```

The model may paraphrase candidate presentation but cannot alter refs, coverage, answerability, or IDs.

### 7.3 Inspect response

```json
{
  "candidate_id": "opaque-D1",
  "dataset_shape": "geography_series",
  "measures": [{"measure_id": "opaque", "name": "Jumlah Penduduk", "unit": "jiwa"}],
  "period": {"granularity": "annual", "min": "2002", "max": "2025", "latest": "2025"},
  "dimensions": [
    {"dimension_id": "wilayah", "role": "geography", "level": "kecamatan", "items": 17, "has_total": true},
    {"dimension_id": "jenis_kelamin", "role": "category", "items": 3, "default_item": "total"}
  ],
  "safe_defaults": {"period": "latest", "jenis_kelamin": "total"},
  "required_slots": [],
  "supported_operations": ["lookup", "breakdown", "trend", "compare", "rank"],
  "quality_flags": []
}
```

For user query `jumlah penduduk berdasarkan kecamatan`, only period remains unresolved; agent asks `tahun tertentu atau data terbaru?`.

### 7.4 Query request

```json
{
  "candidate_id": "opaque-D1",
  "measure_id": "opaque-population",
  "period": {"mode": "latest"},
  "subperiod": null,
  "geography": {"mode": "breakdown", "level": "kecamatan", "item_ids": []},
  "dimension_filters": {"jenis_kelamin": ["total"]},
  "operation": "list",
  "order": {"by": "registry_order", "direction": "asc"},
  "limit": 20
}
```

### 7.5 Normalized result

```json
{
  "result_id": "opaque",
  "query_spec_hash": "sha256",
  "dataset_id": "opaque-D1",
  "registry_version": "opaque",
  "source_versions": ["snapshot:sv1040"],
  "schema": {
    "dimensions": ["geography"],
    "measure": "jumlah_penduduk",
    "value_type": "number",
    "unit": "jiwa"
  },
  "rows": [
    {"geography_id": "1306010", "geography": "Batang Anai", "period": "2025", "value": 59681, "marker": null}
  ],
  "coverage": {"returned": 17, "expected": 17, "complete": true},
  "validation": {"status": "pass", "checks": ["coverage", "component_total"]},
  "lineage": {"snapshot_ids": ["sv1040"]},
  "cache": {"hit": false}
}
```

### 7.6 Error/clarification contract

```json
{
  "status": "needs_clarification",
  "reason": "material_slot_missing",
  "slot": "price_basis",
  "choices": ["ADHB", "ADHK"],
  "question_hint": "PDRB harga berlaku atau harga konstan?"
}
```

Do not use fallback model to guess missing material slots.

## 8. Golden multi-turn episodes

### E01 — Natural breakdown query, list before query

```text
U: jumlah penduduk berdasarkan kecamatan
A: shows S1/D1/C1; recommends D1 but does not query.
U: D1
A: selection accepted; asks tahun tertentu atau data terbaru?
U: terbaru
A: query D1, 2025, jenis_kelamin=Total → 17 rows; answer with coverage/source.
```

Assertions:

- operation resolves to geography breakdown;
- candidate list is required because similar tables/sources exist;
- recommendation does not auto-select;
- only period is probed;
- user is not asked for gender; explicit safe Total is used and disclosed;
- result 17/17, sum 467.038.

### E02 — Broad concept and candidate offering

```text
U: data penduduk dong
A: recommends D1, groups relevant S/D/C/P candidates with stable refs.
U: yang umur
A: resolves D2/S2 candidates, explains period/source difference or asks source if material.
U: yang terbaru aja
A: selects source with latest answerable period and states actual year.
```

### E03 — Pagination

```text
U: publikasi tentang penduduk
A: P1–P3.
U: lanjut publikasi
A: P4–P6, old refs unchanged.
U: yang kedua halaman tadi
A: resolves P5 through focused group/page history.
```

### E04 — Source selection and precision

```text
U: penduduk 2025 berapa?
A: shows S1/D1/C1 and recommends D1; waits for selection.
U: D1
A: returns Dynamic exact 467.038 jiwa.
U: ada versi simdasi?
A: offers S1 and waits for selection.
U: S1
A: retrieves 467 ribu jiwa and explains rounding; not a conflict.
```

### E05 — PDRB material probing

```text
U: data PDRB terbaru
A: probes annual vs quarterly or recommends based on context.
U: triwulanan
A: asks lapangan usaha vs pengeluaran and ADHB vs ADHK when still material.
U: pengeluaran harga berlaku, Q2
A: exact subperiod-aware query; never legacy Dynamic view.
```

### E06 — Census cross-tab

```text
U: penduduk sensus berdasarkan jenis kelamin
A: offers C1/SP2010 answerable and metadata-only newer Census with status explained, not as queryable.
U: yang SP2010 per kecamatan
A: applies category totals for urban/rural and breakdown geography; preserves gender split.
```

### E07 — Topic correction

```text
U: data sekolah
A: offers HLS/RLS, facility counts, student/teacher counts grouped by meaning.
U: bukan pendidikan, jumlah SD per kecamatan
A: reranks to Dynamic 230/SIMDASI school table; does not force old menu.
```

### E08 — Alias/typo

```text
U: pendudk lubuk alung terbaru
A: canonicalizes concept and geography alias to Lubuak Aluang/1306020, states normalization if useful, queries D1.
```

### E09 — Marker handling

```text
U: jumlah pulau per kecamatan 2025
A: returns available numbers and `–` with explanation “tidak ada atau nol”; never coerces all `–` to 0.
```

### E10 — Follow-up analysis/artifact

```text
U: jumlah penduduk berdasarkan kecamatan
A: probes period.
U: 2025
A: 17-row result summary.
U: urutkan tertinggi
A: reuses result ID and ranks.
U: bandingkan 2024
A: queries second period, deterministic deltas.
U: buat Excel
A: artifact from result/analysis IDs, no re-discovery.
```

## 9. Optimization targets for golden eval

| Metric | MVP target |
|---|---:|
| Catalog Recall@5 for answerable intended dataset | ≥95% |
| Candidate page p95 server latency | <25 ms warm |
| Inspect p95 | <10 ms cache hit; <50 ms miss |
| Exact fact query p95 | <100 ms |
| Full catalog content delivered to model | 0 |
| Candidate payload | ≤2 KB typical |
| Candidate groups/message | ≤3 |
| Candidate items/family/page | ≤3 |
| Material clarification questions/turn | ≤1 |
| New goal fact query before user candidate selection | 0 |
| Auto-select recommended candidate | 0 |
| Duplicate/recycled refs within set | 0 |
| Numeric answers without evidence/result | 0 |
| Quarterly query through legacy view | 0 |
| Metadata-only resource queried as facts | 0 |

## 10. Optimization alternatives and trade-offs

| Option | Benefit | Cost/risk | Decision |
|---|---|---|---|
| Direct DB fact query | already sub-ms to <1 ms | needs semantic registry | keep |
| PostgreSQL unified registry table/view | strong consistency and audit | migration/build work | choose source of truth |
| In-process catalog snapshot | lowest discovery latency, no DB roundtrip | per-instance refresh/versioning | choose MVP |
| Redis search/state | shared state/cache | operational dependency | Redis for state/cache only |
| `pg_trgm` + aliases | typo fallback deterministic | extension/index maintenance | plan after registry; likely useful |
| Vector catalog search | semantic recall | embedding cost, harder ranking audit | defer until eval gap |
| Elasticsearch/OpenSearch | scale/fuzzy tooling | overkill for 1.074 docs | reject MVP |
| LLM rerank all catalog | flexible | ~51k tokens/query, latency/cost/injection risk | reject |
| LLM rerank top 10–20 | contextual recommendation | extra model call | optional only if main planner can rank in same turn |
| Precomputed data cube | fast aggregates | semantic/refresh complexity | reject now; fact DB already fast |

## 11. Candidate scoring simulation (review Tah)

`scripts/simulate_bps_candidate_scoring.py` mensimulasikan scorer deterministik terhadap 60 kalimat user realistis; report: `data/reports/bps-candidate-scoring-simulation.json`. Read-only, tanpa LLM/WebAPI/schema change.

Hasil live:

```text
overall Recall@3 : 1.000 (60/60)   <-- SINTETIS, bukan bukti; lihat catatan di bawah
overall MRR      : 0.9389          <-- SINTETIS
per family       : census 1.00, dynamic 1.00, publication 1.00, simdasi 1.00
```

Scorer deterministik yang terbukti:

- slot extraction: geografi (17 kecamatan + alias) dan tahun;
- canonicalizer: stop words conversational + alias/typo (`pendudk`, `pengagguran`, `TPT`, `TPAK`, `IPM`, `PNS`, `SD`, `ekonomi`→PDRB, `kemiskinan`→`kemiskinan+miskin`);
- feature score: title hit 1.5/token, context hit 0.8/token, exact phrase title +2.5/context +1.2, tahun exact +3.0, boost `jumlah`/`persen`/`kecamatan`, penalty `indeks`/`garis`/`rata`/`harapan`/`rasio`/`laju`/`kedalaman`/`keparahan`/`gini`, specificity penalty token judul unmatched, micro-recency;
- doc-side alias expansion juga berlaku pada context phrase (bug ditemukan: `context_phrase` memakai compact mentah sehingga `SD` tidak menjadi `sekolah dasar`);
- tahun publikasi fallback dari judul bila `release_date` kosong.

Temuan simulasi:

- variabel baru 2026 (PDRB triwulanan 17 kategori, penduduk bekerja 2024/2025) adalah kandidat sah yang menggantikan var lama pada golden expectation;
- SIMDASI 4.1.3 memakai kata "Pendidik" di judul; "Guru SD" ada di nama indikator → context phrase weight penting;
- kata `dasar` pada "Kementerian Pendidikan Dasar dan Menengah" mencemari semua judul 4.1.x → phrase match kontekstual menyelesaikan;
- Publication "Dalam Angka 2026" butuh exact-year title boost karena judul antar-kecamatan hampir identik.

> **Angka di atas adalah metrik loop tertutup (audit C1b/C1c).** 60 kalimat, kunci
> jawabannya, dan scorer-nya ditulis oleh penulis yang sama, lalu scorer
> dikalibrasi terhadap set itu sampai metriknya naik. Yang diukur adalah
> kecocokan scorer dengan ekspektasi penulisnya, bukan kualitas retrieval.
> Alias typo yang dulu di-hardcode agar cocok dengan set ini (`pendudk`,
> `pendudduk`) sudah dihapus dan diganti fuzzy matching terhadap vocabulary
> katalog live, sehingga toleransi typo kini berupa mekanisme yang bisa
> menggeneralisasi. Runner melabeli outputnya `synthetic_author_written`.

Batasan saat ini: per-family scoring dan cross-family offering/diversity simulation sudah ada; yang belum diuji live adalah working-memory/context features dan LLM conversation execution.

**Threshold regresi ditangguhkan.** Batas lama (Recall@3 ≥ 0.95, golden-family offering = 1.0) diukur pada set sintetis, jadi menahannya hanya mengunci scorer pada tebakan penulisnya. Threshold baru ditetapkan setelah 30 pertanyaan PST nyata masuk `data/evals/pst-real-questions.json`; ekspektasi realistis pada set nyata ada di kisaran 0,6–0,8, dan angka itulah yang akan menjadi baseline sesungguhnya.

## 12. Remaining planning decisions

1. Review registry DDL shape and versioning strategy.
2. Approve unit-state policy and resolve PDRB/HLS metadata issues with data owner.
3. Approve geography alias seed from source codes.
4. Convert golden episodes into machine-readable eval cases.
5. Run simulated candidate scorer against 50–100 realistic user utterances.
6. Only after review decide whether to implement schema/view changes.

## 13. References

- `21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`
- `22-BPS-QUERY-PROTOTYPE-TEST-MATRIX.md`
- `data/reports/bps-query-prototype-validation.json`
- `AGENT.md`
- `02-AGENT-RUNTIME.md`
- `04-RAG-AND-DATA.md`
- `08-API-CONTRACT.md`
- `10-TEST-EVALUATION.md`
