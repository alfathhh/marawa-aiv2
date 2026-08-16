# Eksplorasi Data BPS dan Desain Query Agent

> Diverifikasi dari PostgreSQL lokal pada `2026-08-15T07:09:17+08:00`. Dokumen ini mendefinisikan discovery, candidate offering, probing, dan typed-query sebelum implementasi runtime.

## 1. Prinsip produk

MARAWA adalah **AI agent**, bukan menu bot dan bukan text-to-SQL. User boleh datang dengan pesan tidak lengkap seperti:

- `data penduduk dong`
- `yang terbaru berapa?`
- `per kecamatan ada?`
- `buatkan datanya`
- `yang dinamis aja`

Agent menjalankan loop:

```text
pesan + konteks
→ pahami goal sementara
→ search catalog lokal lintas source
→ kelompokkan kandidat berdasarkan makna
→ rekomendasikan dan/atau tampilkan kandidat
→ probing satu slot paling informatif per turn
→ inspect dataset/dimensi yang dipilih
→ query typed setelah scope cukup
→ validate evidence, unit, periode, dimensi
→ jawab/analisis/artefak
```

Candidate list adalah alat grounding dan selection. Untuk goal baru tanpa candidate ref/kode exact, agent selalu menampilkan kandidat dan menunggu user memilih sebelum query facts. Agent boleh merekomendasikan kandidat terbaik, tetapi tidak auto-select. User tidak dipaksa memakai format kaku: pilihan natural tetap di-resolve ke candidate ID. Valid explicit ref/kode exact serta follow-up active dataset tidak membutuhkan list ulang.

## 2. Inventory live

| Family | Katalog | Facts/cells | Periode | Catatan bentuk |
|---|---:|---:|---|---|
| SIMDASI | 47 tabel, 353 table-indicator | 40.525 | 2010–2026 | 30 tabel geography-primary; 17 category-primary |
| Dynamic | 335 variable, 29 subject | 68.220 | 2002–2026 | 211 geography-primary; 123 category-primary; 24 quarterly variables |
| Census | 4 event, 165 dataset | 58.041 | 2010/2023 tersedia lokal | kategori berupa array multi-dimensi |
| Publication | 602 metadata | — | rilis 1994–2026 | title/abstract/date/URL; duplicate title ada |
| Glossary | 0 | — | — | upstream BPS HTTP 500; tidak ditawarkan sebagai active source |

Raw snapshot dan `snapshot_id` tetap menjadi lineage. Runtime tidak memanggil WebAPI on-demand.

## 3. Temuan skema yang memengaruhi query

### 3.1 SIMDASI: row tidak selalu geografi

`bps_serving_simdasi` saat ini memberi field `geography_name`, tetapi:

- **30 tabel** memang memakai row sebagai wilayah/kecamatan;
- **17 tabel** memakai row sebagai kategori, misalnya lapangan usaha PDRB, kelompok umur, jenis tanaman, pangkat/pendidikan PNS.

Contoh kategori PDRB di tabel `12.1`: Pertanian, Pertambangan, Industri, ... dan total PDRB. Nilai `geography_code` kosong. Karena itu query engine membutuhkan:

```text
row_dimension_role = geography | category
row_item_code
row_item_label
```

Nama `geography_name` tidak boleh dipakai generik untuk semua tabel.

SIMDASI geography-primary memiliki 38.339 cell kecamatan dan 2.186 cell kabupaten pada view sekarang. Marker seperti `–`, `...`, `NA`, `e`, `r`, dan catatan kaki harus dipertahankan; bukan dipaksa numeric.

### 3.2 Dynamic: vervar bisa wilayah atau kategori

Dari 334 variable yang memiliki facts:

- **211** geography-primary;
- **123** category-primary;
- **14** memiliki dimensi sekunder nyata;
- **24** memiliki subperiode (triwulan/tahunan).

Contoh:

- variable `29`: `vervar=wilayah`, `turvar=jenis kelamin`;
- variable `188`: `vervar=kelompok umur`, `turvar=jenis kelamin`;
- variable `282`: `vervar=tingkat pendidikan`, tidak ada dimensi sekunder;
- variable `398`: `vervar=komponen pengeluaran`, `turtahun=Triwulan I–IV/Tahunan`.

Field `bps_serving_dynamic.geography_name` juga tidak selalu wilayah.

### 3.3 Dynamic subperiode — fixed di serving layer

Ada **918 quarterly facts** dan **1.082 quarterly/tahunan subperiod facts** pada 24 variable. Audit awal menemukan legacy `bps_serving_dynamic` tidak membawa `derived_period_id/label`, sehingga terlihat 192/263 duplicate keys (bergantung key label yang dipakai).

Status 2026-08-15: **fixed** melalui `migrations/001_serving_view_fixes.sql` dan source-of-truth bootstrap `workers/ingestion/bps_storage.py`. View sekarang membawa:

```text
period_granularity = annual | quarterly
subperiod_code
subperiod_label
primary/secondary dimension IDs + labels
```

Regression key berbasis code (`indicator_code + period + geography_code + secondary_dimension_id + subperiod_code`) menghasilkan **0 visible duplicate keys**. PDRB triwulanan sekarang boleh dibaca melalui view fixed, tetap dengan typed template dan explicit subperiod filter.

### 3.4 Census adalah multidimensi

Census facts menyimpan `categories[]`, misalnya dataset SP2010 `10`:

```text
Wilayah × Perkotaan/Perdesaan × Jenis Kelamin
```

Dataset lain dapat memuat umur, agama, pendidikan, status pekerjaan, lapangan usaha, disabilitas, dan lainnya. Query tanpa filter kategori dapat mengembalikan banyak cell valid untuk indikator dan wilayah yang sama. Tool wajib melakukan `inspect_dataset` dan menetapkan setiap category filter atau total.

Geography labels antar sumber juga punya alias/ejaan berbeda:

- `Lubuak Aluang` vs `LUBUK ALUNG`;
- `Ulakan Tapakih` vs `ULAKAN TAPAKIS`;
- `Sungai Garinggiang` vs `SUNGAI GERINGGING`;
- `IV Koto Aua Malintang` vs `IV KOTO AUR MALINTANG`.

Dibutuhkan canonical geography registry dengan alias per source.

### 3.5 Publication bukan statistical fact

Publication dipakai untuk navigasi/metadata/narasi, bukan sebagai angka terstruktur. Search menggunakan title + abstract + release date. Duplicate normalized title ada, sehingga `publication_id` tetap identifier dan kandidat harus menyebut tahun rilis.

## 4. Candidate registry dan source prefixes

Candidate reference hanya berlaku dalam satu `candidate_set_id` pada satu percakapan:

| Prefix | Source family | Contoh |
|---|---|---|
| `S` | SIMDASI | `S1`, `S2` |
| `D` | Dynamic Data | `D1`, `D2` |
| `C` | Census | `C1`, `C2` |
| `P` | Publication | `P1`, `P2` |
| `K` | Approved knowledge/corpus lain kelak | `K1`, `K2` |

Agent boleh menambah approved source family lain pada iterasi discovery berikutnya. Prefix tidak memberi permission; ia hanya alias UI ke opaque `candidate_id` yang server validasi.

Candidate minimum:

```json
{
  "candidate_id": "opaque",
  "display_ref": "D1",
  "source_family": "dynamic",
  "resource_id": "29",
  "title": "Jumlah Penduduk",
  "summary": "Total/per kecamatan, jenis kelamin, 2002–2025",
  "available_periods": {"min": "2002", "max": "2025"},
  "dimension_summary": ["wilayah", "jenis_kelamin"],
  "unit": "jiwa",
  "match_reasons": ["judul", "konteks aktif"],
  "score": 0.93
}
```

## 5. Candidate offering yang agentic

### 5.1 Bukan menu wajib

Agent dapat:

- merekomendasikan satu kandidat sambil menunjukkan alternatif, tanpa auto-select;
- menampilkan kandidat per source;
- mencari halaman berikutnya atau memperluas source;
- menerima pilihan dengan ref, nomor, judul, deskripsi, atau bahasa natural;
- menggabungkan dua source untuk conflict/comparability check jika user meminta.

Fact query baru berjalan setelah user memilih kandidat. Pengecualian: user memberi valid ref/kode exact dari candidate set aktif, atau follow-up memakai selected/active dataset sebelumnya.

### 5.2 Contoh natural

User:

```text
data penduduk dong
```

Agent:

```text
Ada beberapa jenis data penduduk. Yang paling umum adalah jumlah penduduk terbaru per kecamatan (D1). Saya juga menemukan rincian umur/jenis kelamin dan data sensus.

SIMDASI
S1. Jumlah penduduk, pertumbuhan, kepadatan, dan rasio jenis kelamin per kecamatan — 2010, 2018–2025
S2. Penduduk menurut kelompok umur dan jenis kelamin — 2021–2026

Data Dinamis
D1. Jumlah penduduk per kecamatan dan jenis kelamin — 2002–2025
D2. Penduduk menurut kelompok umur dan jenis kelamin — 2002–2023
D3. Jumlah penduduk miskin per kabupaten/kota — 2005–2023

Data Sensus
C1. SP2010: penduduk menurut wilayah, perkotaan/perdesaan, dan jenis kelamin

Publikasi
P1. Kabupaten Padang Pariaman Dalam Angka 2026

Bisa jawab “D1”, “yang umur”, “yang sensus”, atau “lanjut sumber berikutnya”.
```

Agent tidak harus selalu menampilkan semua grup. Ia memilih subset paling relevan terhadap goal dan channel budget.

## 6. Pagination kandidat

Pagination dilakukan **per source family**, bukan satu nomor global yang mudah bergeser.

Default WhatsApp:

- maksimal 3 kandidat per family/page;
- maksimal 2–3 family pada satu message;
- kandidat yang sudah ditampilkan mempertahankan `display_ref` selama `candidate_set_id` aktif;
- halaman berikutnya melanjutkan nomor: `S4–S6`, bukan mengulang `S1–S3`;
- cursor opaque dan server-owned;
- sort stabil: relevance desc → freshness desc → canonical resource ID;
- dedup berdasarkan canonical resource/version, bukan judul saja.

State minimum:

```json
{
  "candidate_set_id": "cs_opaque",
  "query": "data penduduk",
  "groups": {
    "simdasi": {"next_cursor": "opaque", "shown": ["S1", "S2"]},
    "dynamic": {"next_cursor": "opaque", "shown": ["D1", "D2", "D3"]},
    "census": {"next_cursor": null, "shown": ["C1"]},
    "publication": {"next_cursor": "opaque", "shown": ["P1"]}
  },
  "selected_candidate_id": null,
  "expires_at": "..."
}
```

Interpretasi follow-up:

- `lanjut` → source group paling baru/aktif;
- `lanjut dynamic` → page berikut Dynamic;
- `publikasi lainnya` → page berikut Publication;
- `yang nomor dua` → ref kedua pada group yang sedang menjadi fokus; jika dua group sama-sama mungkin, agent bertanya singkat;
- `D2 tahun terbaru` → pilih D2 + set period policy latest;
- `bukan itu, yang miskin` → rerank/search ulang, tidak memaksa menu lama.

Candidate set menjadi stale ketika goal berubah total, user meminta reset, atau expiry terlewati. Ref lama tidak pernah direcycle dalam set yang sama.

## 7. Probing dan slot filling

### 7.1 Slots

```text
intent              lookup | breakdown | trend | compare | rank | definition | publication | export
candidate_id        resource exact dari registry
measure/indicator   kolom/indicator dalam resource
period              exact | latest | range | comparison pair
subperiod           annual | Q1 | Q2 | Q3 | Q4
geography           canonical code + level
row_dimension       geography/category item
category_filters    typed map dari dimensi dataset
aggregation         total | breakdown | top_n | bottom_n
output               chat | table | chart | xlsx
source_policy        recommended | explicit source | compare sources
```

### 7.2 Tanya satu hal paling informatif

Agent tidak harus menanyakan slot dalam urutan tetap. Ia memilih pertanyaan yang paling memperkecil kandidat atau mencegah jawaban salah.

Contoh setelah D1 dipilih:

- `Mau total kabupaten atau per kecamatan?`
- jika user bilang `per kecamatan`, periode yang belum ada → `Tahun tertentu atau data terbaru?`
- jika user bilang `terbaru`, gender belum disebut → default `Total` boleh digunakan karena dataset menyediakan explicit Total dan tidak mengubah makna “jumlah penduduk”. Agent menyebutkan bahwa hasil adalah total laki-laki+perempuan.

Contoh PDRB:

- ADHB vs ADHK adalah material → harus ditanya;
- tahunan vs triwulanan adalah material → harus ditanya;
- lapangan usaha vs pengeluaran adalah material → harus ditanya/rekomendasikan berdasarkan frasa user;
- subperiode wajib untuk quarterly dataset.

### 7.3 Default yang diperbolehkan

- geography default: Kabupaten Padang Pariaman hanya jika pesan tidak menyebut breakdown dan kandidat adalah kabupaten-level;
- period default: `latest_available`, tetapi jawaban wajib menyebut tahun aktual;
- total category: hanya jika dataset menyediakan item `Total/Jumlah` explicit;
- source: agent boleh merekomendasikan source dengan precision/freshness terbaik, tetapi tidak menyembunyikan konflik comparable.

Agent tidak boleh default pada pilihan yang mengubah konsep: jumlah vs persen, ADHB vs ADHK, tahunan vs triwulanan, estimasi tahunan vs census, atau kecamatan vs kabupaten ketika user meminta breakdown.

## 8. Typed discovery/query tools

### `search_data_catalog`

```json
{
  "query": "data penduduk",
  "source_families": ["simdasi", "dynamic", "census", "publication"],
  "context_filters": {"geography": "1306", "period": null},
  "page_size_per_family": 3,
  "cursors": {},
  "candidate_set_id": null
}
```

Response grouped by family, stable refs, reason, coverage, dimension summary, cursor, and `has_more`. Tool does not return facts.

### `inspect_dataset`

Input exact `candidate_id`; output:

- resource identity/title/source;
- row dimension role;
- measures/indicators;
- dimensions and allowed items;
- geography levels/aliases;
- period + subperiod coverage;
- unit and missing-marker semantics;
- safe defaults;
- query templates supported;
- comparability warnings.

### `query_stat_data`

```json
{
  "candidate_id": "opaque",
  "measure_id": "opaque-registry-id",
  "period": {"mode": "exact", "values": ["2025"]},
  "subperiod": null,
  "geography": {"mode": "breakdown", "level": "kecamatan", "codes": []},
  "dimension_filters": {"jenis_kelamin": ["total"]},
  "operation": "list",
  "order": {"by": "value", "direction": "desc"},
  "limit": 20
}
```

Server, bukan LLM, mengubahnya menjadi parameterized SQL terhadap allowlisted registry query template. Response menyertakan rows, coverage, unit, marker/note, source, snapshot IDs, and normalized query spec.

### `get_candidate_page`

Menerima `candidate_set_id + source_family + cursor`. Tool hanya memperpanjang candidate set dan tidak query facts.

## 9. Query patterns

| User goal | Required slots | Query shape |
|---|---|---|
| Satu nilai | candidate, measure, period, geography, category total | exact lookup |
| Per kecamatan | candidate, measure, period, geography level | breakdown + coverage |
| Tren | candidate, measure, geography/category, period range | ordered series |
| Banding tahun | candidate, measure, geography/category, 2 periods | two exact rows + deterministic delta |
| Ranking | candidate, measure, period, dimension role geography | numeric rows only + coverage + top/bottom N |
| Komposisi | candidate, measure, period, category dimension | breakdown; total validation |
| PDRB triwulanan | candidate, price basis, approach, year, subperiod, component | exact subperiod-aware query |
| Census cross-tab | event/dataset, geography, all category filters | category-array typed matching |
| Publication | candidate or search query | metadata result, no statistical period inference |

Ranking hanya boleh pada comparable numeric cells dengan unit/period/definition sama. Marker/non-numeric excluded dan coverage dilaporkan.

## 10. Source choice dan conflict

Contoh jumlah penduduk 2025:

- Dynamic D1: `467.038 jiwa`, explicit total, kecamatan, gender, snapshot `1040`;
- SIMDASI S1: `467 ribu jiwa`, rounded, kecamatan, snapshot `96`;
- Census SP2010: `391.056 jiwa`, tetapi periode sensus 2010, bukan estimasi terbaru.

Agent sebaiknya merekomendasikan D1 untuk angka exact terbaru, S1 untuk tabel statistik multi-indikator, dan Census hanya bila user meminta census/historical cross-tab. Angka `467` ribu dan `467.038` jiwa tidak diperlakukan konflik karena precision/unit representation berbeda dan masih comparable setelah metadata-aware normalization.

Jika dua source comparable tetap berbeda di luar tolerance, agent menampilkan kedua nilai dan konteks; tidak merata-ratakan.

## 11. Registry yang harus dibangun sebelum query API

1. **Dataset registry** per exact resource/version.
2. **Dimension-role registry** (`geography`, `category`, `subperiod`).
3. **Canonical geography registry + source aliases**.
4. **Measure registry** dengan unit, decimal, marker, allowed operations.
5. **Source precedence/comparability policy** per concept, bukan global saja.
6. **Candidate search index** untuk title, indicator, aliases, subject, summary, period, source.
7. **Query templates** parameterized per dataset shape.

P0 schema fixes:

- SIMDASI row-role + Dynamic primary/secondary/subperiod semantics sudah fixed di serving views; runtime harus memakai fields baru, bukan semantics generik legacy;
- canonical Census dimension items dari `categories[]` sudah di-build (typed axes + total 999, migration batch hardening; test `tests/test_bps_census_registry.py`);
- geography alias mapping lintas family sudah published (18 canonical + 34 alias), runtime tinggal memakai registry.
- SIMDASI display-label normalization sudah di-build (0 HTML, raw lineage terjaga di `label_raw`).

## 12. Evaluasi dialog agent

Golden episodes wajib menguji, bukan hanya single-turn:

1. `data penduduk dong` → grouped candidates;
2. `D1` → agent inspect, tanya total/per kecamatan bila perlu;
3. `per kecamatan` → tanya/latest default secara natural;
4. `yang terbaru` → query 2025, explicit Total, result grounded;
5. `bandingkan tahun sebelumnya` → reuse candidate/geography/category;
6. `lanjut dynamic` → page berikutnya tanpa ref renumber;
7. `yang nomor dua` → resolve focus group;
8. `bukan itu, yang miskin` → rerank new candidates;
9. `pakai S1 juga` → comparability check lintas source;
10. `buat Excel` → artifact from result IDs.

Uji juga typo alias wilayah, candidate expiry, pagination duplicate, source addition, topic reset, ambiguous PDRB, quarterly subperiod, Census category totals, markers, no-data, conflict, dan prompt injection dalam title/metadata.

## 13. Query prototype validation

Tahap ini tetap planning/read-only. Harness `scripts/validate_bps_query_prototypes.py` menjalankan 13 SQL prototype dalam transaksi `READ ONLY` dengan timeout 10 detik; hasil live **13 PASS / 0 FAIL** disimpan di `data/reports/bps-query-prototype-validation.json`. Matriks lengkap, evidence, candidate search behavior, reconciliation rules, dan blocker ada di `22-BPS-QUERY-PROTOTYPE-TEST-MATRIX.md`.

Delta temuan penting dari query lab dan status tindak lanjut:

- candidate harus punya status `answerable`; Census SP2020/SP2022 catalog tersedia tetapi facts lokal nol — **fixed di registry answerability**;
- query natural perlu canonicalization (`data penduduk` → `penduduk`, `TPT` ↔ typo upstream `Pengagguran`) — **mekanisme kini fuzzy matching terhadap vocabulary katalog live**. Catatan audit C1b: alias typo `pendudk`/`pendudduk` dulu di-hardcode padahal string itu hanya ada di set evaluasi buatan sendiri, sehingga "toleransi typo" sebenarnya nol untuk typo yang tidak diantisipasi penulis;
- relevance membutuhkan concept/action/shape/source/freshness features — **angka scoring berlabel sintetis dan bukan bukti kualitas** (`docs/25` note A);
- 3.394 SIMDASI category cells salah dilabeli kecamatan — **fixed, regression count 0**;
- Dynamic legacy view punya 263 visible duplicate keys/1.074 rows terdampak — **fixed dengan subperiod/code-aware key, count 0**;
- 432 SIMDASI PDRB rows salah keluar sebagai `Rp` — **diganti `miliar rupiah` dari judul tabel, wrong-unit count 0; TETAPI unit itu hasil tebakan.** Audit C2a: menurunkan satuan level-tabel ke data level-kolom bisa memproduksi kesalahan baru tanpa suara pada tabel berkolom campur. Baris ini kini `unit_state='review_required'` dan measure-nya tidak queryable sampai data owner mengonfirmasi (`docs/26`);
- Census kabupaten memakai source code `1306`; canonical/SIMDASI memakai `1306000`; Dynamic memakai vertical item `18` — **18 canonical geographies + 34 aliases published** (Census typed items + display labels juga sudah built);
- total/reconciliation tetap precision-aware; marker tetap non-numeric.

## 14. Kesimpulan implementasi

Urutan aman dan status:

```text
✓ fix serving dimension semantics
✓ build dataset/dimension/geography registry
✓ validate catalog scoring + grouped candidate pagination
△ build Census item registry + SIMDASI display-label cleanup (database-only next)
□ build inspect_dataset runtime tool
□ build query compiler from typed templates
□ build probing/candidate working-memory state
□ run live golden multi-turn agent evaluation
□ baru integrasi runtime WhatsApp
```

Jangan mulai dari satu endpoint `ask(text)` yang menghasilkan SQL. Agentic behavior berada pada planning/probing/source selection; data effects tetap typed dan server-controlled.
