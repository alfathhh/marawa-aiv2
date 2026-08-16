# BPS Query Prototype Test Matrix

> Status: planning + query validation. Serving view fixes dan `bps_registry` **sudah di-build** (izin Tah); runtime/API/agent masih belum diimplementasikan.
>
> Live validation artifact: `data/reports/bps-query-prototype-validation.json`.
> Reproducible runner: `scripts/validate_bps_query_prototypes.py`.

## 1. Batas tahap

Yang dilakukan:

- membaca PostgreSQL mirror lokal;
- membuat SQL prototype read-only;
- menguji hasil, coverage, total, precision, marker, pagination, dan lineage;
- menemukan serving/data-registry gap sebelum implementation.

Sejak izin Tah (15 Aug 2026), yang **sudah di-build**: serving view fixes (`migrations/001`) dan `bps_registry` + builder (`migrations/002`, `scripts/build_bps_registry.py`). Harness ini tetap read-only dan kini menjadi regression guard.

Yang masih belum dilakukan:

- tidak membuat query API/runtime/WhatsApp;
- tidak menghubungi BPS WebAPI;
- tidak mengaktifkan cronjob;
- tidak mengizinkan LLM menulis SQL.

Harness berjalan dengan:

```text
SET TRANSACTION READ ONLY
SET LOCAL statement_timeout = '10s'
SQL hanya SELECT/WITH; multi-statement dan mutation keyword ditolak
```

Jalankan ulang:

```bash
cd /home/ubuntu/projects/marawa-ai
uv run python scripts/validate_bps_query_prototypes.py
```

## 2. Hasil harness

```text
13 query prototypes
13 PASS
0 FAIL
```

| Case | Query shape | Evidence live | Status calon runtime |
|---|---|---|---|
| `dynamic_exact_total` | exact lookup | 467.038 jiwa, 2025, snapshot 1040 | safe setelah candidate registry |
| `dynamic_breakdown_coverage` | breakdown kecamatan | 17/17 kecamatan; sum 467.038 | safe untuk geography-primary |
| `dynamic_trend_compare` | trend + compare | 2020–2025; +7.869 jiwa; +1,7137% pada 2025 vs 2024 | safe, analysis deterministik |
| `dynamic_composition` | category composition | 6 pendidikan menjumlah ke explicit total 207.131 | safe jika explicit total tersedia |
| `dynamic_quarterly` | year + subperiod | Q1–Q4 = 27.793,21 miliar rupiah = annual | safe pada fixed serving view dengan explicit subperiod |
| `simdasi_exact_total` | exact lookup | 467 ribu jiwa, 2025, snapshot 96 | safe untuk geography-primary |
| `simdasi_rounded_coverage` | breakdown + rounded reconciliation | 17 kecamatan; sum 467,2 vs total 467; difference 0,2 | safe dengan precision tolerance |
| `simdasi_marker_preserved` | missing/marker semantics | `–` 2.334, `...` 90, `NA` 12; semuanya numeric NULL | safe jika marker dipertahankan |
| `census_cross_tab` | multidimensional exact totals | grand total = gender margin = urban/rural margin = sum 17 kecamatan = 391.056 | safe setelah category/geography registry |
| `publication_pagination` | relevance/date pagination | P1–P6 stabil, 6 unique publication IDs | safe dengan cursor server-owned |
| `candidate_answerability` | catalog filtering | Dynamic 334/335; SIMDASI 47/47; SP2010 65/69; SP2020 0/5; SP2022 0/65; ST2023 26/26 | mandatory filter |
| `serving_gap_sentinels` | regression guard post-fix | 918 quarterly facts preserved; visible duplicate keys 0; category cells mislabeled 0; PDRB wrong-unit rows 0 | must stay zero |
| `source_precision_comparability` | cross-source normalization | Dynamic 467.038 vs SIMDASI 467.000 normalized; diff 38 (0,008136%); equal at SIMDASI precision | comparable, not conflict |

## 3. Query shapes yang sudah dibuktikan

### 3.1 Exact value

Required typed slots:

```text
candidate + measure + period + geography + explicit total/category filters
```

Verified example:

```text
Dynamic variable 29
2025
Kabupaten Padang Pariaman
Jenis Kelamin = Total
→ 467.038 jiwa
```

`Total` bukan asumsi LLM; ia explicit item pada dimension catalog.

### 3.2 Breakdown/ranking

Verified:

```text
Dynamic variable 29, 2025, Total
17 kecamatan numeric lengkap
sum = total kabupaten = 467.038
Top 3: Batang Anai 59.681; Lubuak Aluang 50.848; VII Koto 37.778
```

Ranking gate:

- dimension role harus `geography`;
- period, definition, unit, category filters sama;
- hanya numeric value;
- marker/non-comparable rows dikeluarkan;
- tampilkan coverage `17/17`;
- jangan memasukkan total kabupaten ke ranking kecamatan.

### 3.3 Trend/compare

Verified series:

| Tahun | Penduduk |
|---:|---:|
| 2020 | 430.626 |
| 2021 | 433.018 |
| 2022 | 436.129 |
| 2023 | 451.388 |
| 2024 | 459.169 |
| 2025 | 467.038 |

2025 vs 2024:

```text
absolute change = +7.869 jiwa
percentage change = +1,7137%
```

Calculation harus berasal deterministic analysis tool, bukan narasi model.

### 3.4 Composition

Dynamic variable 282, 2023:

```text
Tidak/Belum Tamat SD     0
Sekolah Dasar       68.578
SMP/MTs             34.834
SMA/MA              73.023
Diploma/Akademi      4.608
Universitas         26.088
Explicit total     207.131
```

Component sum = explicit total. Query engine tetap wajib memakai explicit total sebagai source value dan hanya memakai sum sebagai validation.

### 3.5 Quarterly

Dynamic variable 398, PDRB ADHB menurut pengeluaran 2025:

| Subperiode | PDRB (miliar rupiah) |
|---|---:|
| Triwulan I | 6.787,50 |
| Triwulan II | 7.129,21 |
| Triwulan III | 7.069,34 |
| Triwulan IV | 6.807,16 |
| Tahunan | 27.793,21 |

Sum Q1–Q4 = annual. Legacy `bps_serving_dynamic` membuang subperiod dan tidak boleh dipakai untuk query ini.

### 3.6 Census cross-tab

SP2010 dataset 10:

```text
Wilayah × Perkotaan/Perdesaan × Jenis Kelamin
```

Padang Pariaman:

```text
grand total          391.056
Laki-laki+Perempuan  391.056
Perkotaan+Perdesaan  391.056
sum 17 kecamatan     391.056
```

Census kabupaten memakai source code `1306`, bukan SIMDASI canonical `1306000`. Category filters memakai exact `category_id + item_code`, bukan label bebas.

## 4. Candidate discovery tests

### 4.1 Query normalization

PostgreSQL FTS behavior live:

| Input literal | Hasil |
|---|---|
| `penduduk` | S=2, D=30, C answerable=40, P=114 |
| `jumlah penduduk` | S=2, D=8, P=1; terlalu restriktif untuk kandidat luas |
| `data penduduk` | structured families 0 karena `plainto_tsquery` memakai `data & penduduk` |
| `pendudk` | 0; FTS tidak fuzzy |

Contract:

1. AI/context layer memahami maksud user;
2. deterministic normalizer membuang conversational stop terms (`data`, `minta`, `dong`, dll.) dan memetakan approved aliases (`TPT ↔ pengangguran`, typo katalog `Pengagguran`);
3. catalog engine mencari canonical terms;
4. response menyimpan `original_query`, `canonical_query`, dan `match_reasons`;
5. kelak `pg_trgm`/alias lexicon boleh menjadi deterministic fuzzy fallback; bukan SQL bebas dari LLM.

### 4.2 Ranking bukan FTS score saja

Raw candidate ranking menunjukkan:

- `kemiskinan` menaruh P1/P2/Garis Kemiskinan sebelum jumlah/persentase;
- `sekolah` menaruh Harapan Lama Sekolah sebelum jumlah sekolah;
- `PDRB` menaruh quarterly 2026 sebelum diketahui ADHB/ADHK, tahunan/triwulanan, lapangan usaha/pengeluaran;
- Census `penduduk` memiliki 40 answerable datasets dengan FTS score banyak yang sama.

Ranking membutuhkan typed intent features:

```text
concept/measure match
query action: jumlah | persen | indeks | daftar | publikasi
source/family policy
answerability
period/freshness
shape match: geography | category | cross-tab | quarterly
exact alias/acronym
working-memory context
FTS/lexical score
```

Agent boleh menjelaskan/rekomendasikan kandidat, tetapi opaque candidate set berasal server.

### 4.3 Answerability

Resource metadata-only tidak boleh ditawarkan seperti queryable:

| Family/event | Catalog | Answerable |
|---|---:|---:|
| Dynamic | 335 | 334 |
| SIMDASI | 47 | 47 |
| Census SP2010 | 69 | 65 |
| Census SP2020 | 5 | 0 |
| Census Long Form SP2020/SP2022 | 65 | 0 |
| Census ST2023 | 26 | 26 |

Metadata-only masih dapat ditampilkan jika user meminta katalog, tetapi candidate harus berstatus `metadata_only` dan tidak boleh mengarah ke `query_stat_data`.

### 4.4 Publication pagination

Prototype `penduduk`, page size 3:

```text
Page 1: P1–P3
Page 2: P4–P6
```

Enam IDs unique dan refs tidak berubah. Duplicate normalized title ada pada 7 title groups; dedup/version resolution tidak boleh memakai title saja.

Performance live dengan query expression sekarang:

```text
Dynamic search: ~3,3 ms (335 rows; seq scan)
Publication search: ~42,6 ms (602 rows; seq scan)
```

Existing GIN indexes hanya mengindeks title, sedangkan prototype mencari title+definition/abstract. Registry/search index materialized perlu direncanakan sebelum skala membesar; saat ini latency masih kecil tetapi query plan tidak memakai index yang diharapkan.

## 5. Geography identity tests

Canonical mapping tidak dapat dibuat dari normalized label saja.

| Canonical code | Dynamic | SIMDASI | Census |
|---|---|---|---|
| `1306020` | Lubuak Aluang | Lubuak Aluang | LUBUK ALUNG |
| `1306030` | Ulakan Tapakih | Ulakan Tapakih | ULAKAN TAPAKIS |
| `1306090` | Sungai Garinggiang | Sungai Garinggiang | SUNGAI GERINGGING |
| `1306061` | Koto Patamuan | VII Koto Patamuan | PATAMUAN |

Kabupaten:

```text
canonical/SIMDASI = 1306000
Census             = 1306
Dynamic            = vertical item 18
```

Canonical registry membutuhkan explicit source alias rows. Label fuzzy hanya membantu discovery; source code/approved mapping menentukan identity final.

## 6. Precision, total, dan marker policy

### 6.1 Precision-aware reconciliation

Dynamic 2025:

```text
467.038 jiwa
```

SIMDASI 2025:

```text
467 ribu jiwa → normalized 467.000 jiwa
```

Difference 38 jiwa = 0,008136%; equivalent pada satu desimal ribu-jiwa. Comparability engine harus mempertimbangkan unit scale + source decimal precision, bukan exact equality saja.

SIMDASI sum kecamatan 467,2 ribu vs explicit total 467 ribu. Tolerance untuk 17 rounded components pada one-decimal source secara konservatif:

```text
n × 0,05 = 0,85 ribu jiwa
```

Actual difference 0,2, sehingga valid. Tolerance harus berasal precision metadata, bukan konstanta global.

### 6.2 Marker

```text
–    2.334 facts → numeric NULL; “tidak ada atau nol”
...     90 facts → numeric NULL; data tidak tersedia
NA      12 facts → numeric NULL; tidak dapat ditampilkan
```

`–` tidak otomatis menjadi numeric zero karena legend menggabungkan dua makna.

## 7. P0 blockers — status 2026-08-15

1. ✅ **SIMDASI row-role** — fixed: `row_role` + `geography_level` NULL untuk kategori (migration `001`).
2. ✅ **Dynamic subperiod** — fixed: `subperiod_code/label` + `period_granularity` di serving view; visible duplicate keys 0.
3. ✅ **SIMDASI PDRB units** — fixed: unit diturunkan dari judul tabel (`miliar rupiah`).
4. ✅ **SIMDASI row cleanup** — fixed: `display_label` + `normalization_rule` + `label_raw` di `dimension_item_registry` (migration 005); 0 label HTML tersisa, raw label tetap untuk lineage.
5. ✅ **Canonical geography** — fixed: 18 geography canonical + 34 alias lintas family di `bps_registry`.
6. ✅ **Census dimensions** — fixed: typed item-level registry dari `categories[]` (axes per dataset, total items code 999, cardinality terisi).
7. ✅ **Candidate answerability** — fixed: status `answerable|metadata_only|blocked_quality|unavailable` di registry.
8. ✅ **Dynamic unit state** — fixed: `unit_state` known/unitless/unknown_review di view + registry; 13 dataset `blocked_quality` menunggu review data owner.

## 8. P1 quality/relevance issues

1. Search normalizer/alias lexicon belum ada (`data`, `dong`, `TPT`, typo `Pengagguran`).
2. Candidate relevance perlu intent/shape/source features; FTS score saja tidak cukup.
3. `Harapan Lama Sekolah` raw unit `Tidak Ada Satuan`; secara semantik perlu data-owner review, bukan model inference.
4. Publication duplicate titles perlu version/identity treatment.
5. Candidate search query expression belum match existing indexes.
6. Candidate references/pagination belum diuji pada real conversation state—baru SQL-level stable ordering.

## 9. Planning acceptance gate

Tahap planning/query validation dianggap siap menuju desain registry ketika:

- [x] family shapes dieksplor live;
- [x] candidate discovery ambiguity diuji;
- [x] exact/breakdown/trend/compare/rank/composition diuji;
- [x] quarterly dan Census cross-tab diuji;
- [x] precision/marker/answerability/pagination diuji;
- [x] reproducible read-only harness 13/13 PASS;
- [x] P0/P1 gaps terdokumentasi;
- [x] serving fixes + `bps_registry` di-build & published (izin Tah);
- [x] candidate scoring/offering simulation dijalankan — **tetapi pada set sintetis; bukan bukti kualitas** (audit C1b/C1c, lihat `docs/25` note A);
- [x] invariant unit + binder ditegakkan tes failing-first (`tests/test_unit_and_binder_invariants.py`);
- [x] privilege runtime role di-assert mesin (`scripts/check_runtime_privileges.py`), tidak lagi bergantung ingatan;
- [ ] **30 pertanyaan PST nyata dikumpulkan** → `data/evals/pst-real-questions.json` (aksi termurah, lihat `docs/15`);
- [ ] data owner mengonfirmasi unit/semantics bermasalah — daftar akan **bertambah** setelah rebuild registry (`docs/26`);
- [ ] golden multi-turn candidate/probing episodes diuji live terhadap LLM.

Runtime/agent/WhatsApp masih belum dibangun. Langkah berikutnya bukan lagi query compiler, melainkan **sinyal eksternal**: kumpulkan pertanyaan nyata, ukur ulang, baru bangun Slice 1 (`docs/01-PRD` §11) dengan TDD mengikuti `AGENTS.md`.
