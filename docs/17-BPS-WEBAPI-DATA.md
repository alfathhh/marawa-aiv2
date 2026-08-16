# BPS WebAPI Data — Struktur, Ingestion, dan Serving Contract

## 1. Tujuan dan scope

Dokumen ini mendefinisikan sumber data resmi yang ditarik untuk MARAWA AI, struktur aktual WebAPI BPS, model penyimpanan PostgreSQL, lineage, update workflow, dan kontrak serving untuk agent. Scope aktif:

| Family | Scope | Prioritas | Kegunaan utama |
|---|---|---:|---|
| SIMDASI | `wilayah=1306000` | 1 | Tabel DDA/SI regional, data Padang Pariaman lintas bab dan tahun |
| Dynamic Data | `domain=1306` | 2 | Indikator/tabel dinamis website BPS Kabupaten Padang Pariaman |
| Census Data | katalog event/topic/dataset; fact untuk area Padang Pariaman | 3 | Data SP/ST dan sensus lain yang tersedia untuk wilayah target |
| Publication | `domain=1306` | 4 | Katalog, metadata detail, PDF publikasi lokal |
| Glosarium | global BPS | 5 | Konsep, definisi, unit, klasifikasi, dan istilah statistik |

Sumber resmi: dokumentasi WebAPI BPS.[9] App key disimpan di luar repository pada file mode `0600`; key tidak boleh masuk URL/log/snapshot/audit yang dipersist.

## 2. Endpoint map dan dependency graph

### 2.1 Convention

Endpoint umum memakai:

```text
https://webapi.bps.go.id/v1/api/list/model/{model}/.../key/{api_key}/
https://webapi.bps.go.id/v1/api/view/model/{model}/.../key/{api_key}/
```

Interoperabilitas memakai:

```text
https://webapi.bps.go.id/v1/api/interoperabilitas/datasource/{source}/id/{service_id}/{parameter}/{value}/key/{api_key}/
```

**Quirk live:** query-string pada beberapa endpoint SIMDASI menghasilkan HTTP 200 berisi halaman `LTM WAF Block`; path-segment style menghasilkan JSON. Client wajib memeriksa content type dan body, bukan hanya status code.

### 2.2 Dynamic Data

```text
subject ─┐
subcat ──┼→ var → th/turth + vervar/turvar → data
unit ────┘
```

| Resource | Model | Scope/parameter | Output penting |
|---|---|---|---|
| Subject | `subject` | `domain=1306`, paginated | `sub_id`, title, subcategory, table count |
| Subject category | `subcat` | `domain=1306` | kategori subjek |
| Variable | `var` | `domain=1306`, paginated | indicator ID/name, definition, notes, vertical dimension, unit |
| Period | `th` | `domain`, optional `var` | period ID/label |
| Derived period | `turth` | `domain`, optional `var` | month/quarter/other derived period |
| Vertical variable | `vervar` | `domain`, optional `var` | wilayah atau primary row dimension |
| Derived variable | `turvar` | `domain`, optional `var` | category/column dimension |
| Unit | `unit` | `domain` | unit catalogue |
| Fact payload | `data` | `domain`, `var`, **required `th`**, optional `turth/vervar/turvar` | dimension arrays + concatenated-key `datacontent` |

Live API mewajibkan `th` dan menerima maksimal **dua period IDs per request**. Crawler mengambil semua halaman `th` per variable, deduplicates `th_id`, membuat chunks berisi satu/dua ID (`id1;id2`), lalu baru menandai variable selesai setelah semua chunks berhasil disimpan.

`datacontent` bukan row list. Key dibentuk dari concatenation:

```text
{vervar_id}{var_id}{turvar_id}{period_id}{derived_period_id}
```

Karena panjang ID tidak selalu fixed, decoder tidak boleh memotong berdasarkan posisi. Decoder membuat Cartesian product dari exact dimension arrays lalu mencocokkan concatenated key. Unmatched key tetap disimpan dengan raw `content_key`; tidak boleh dibuang diam-diam.

### 2.3 Census Data

```text
37 events
 └─ 38 topics(event)
     ├─ 39 areas(event)
     └─ 40 datasets(event, topic)
         └─ 41 facts(event, area, dataset)
```

| Service ID | Fungsi | Parameter |
|---:|---|---|
| 37 | Census events | — |
| 38 | Data topics | `kegiatan={event_id}` (live lowercase) |
| 39 | Census areas | `kegiatan={event_id}` (live lowercase) |
| 40 | Datasets | `kegiatan`, `topik` (live lowercase) |
| 41 | Data | `kegiatan`, `wilayah_sensus`, `dataset` (live lowercase) |

Dokumentasi section area mencantumkan URL `/id/38/` tetapi deskripsi parameternya menyatakan service `39`. Implementasi menggunakan `39` dan memvalidasi live response.

Parameter census case-sensitive pada live API dan harus lowercase. Beberapa event mengembalikan `status=OK`, `data-availability=available`, tetapi elemen data kedua bernilai explicit `null` untuk area catalogue. Raw response tetap disimpan, normalized rows menjadi kosong, dan `null_area_catalogues` dicatat pada run summary; kondisi ini tidak boleh ditafsirkan sebagai bukti bahwa event tidak memiliki data di sumber lain.

Fact census memiliki:

- geography UUID/code/name/level;
- indicator UUID/name;
- period dan value;
- maksimal empat category slots;
- source timestamp;
- field typo upstream yang mungkin memakai `nama_item_kategori_x` atau `nama_item__kategori_x`.

Normalizer mendukung kedua spelling dan mempertahankan seluruh raw row.

### 2.4 SIMDASI

```text
26 provinces
 └─ 27 regencies(parent province)
     └─ 28 districts(parent regency)

22 subjects(region) ─┐
23 tables(region) ───┼→ 25 detail(region, table, year)
24 tables(region, subject) ─┘
34 master table → 36 master detail
```

| Service ID | Fungsi | Parameter utama |
|---:|---|---|
| 26 | MFD province | — |
| 27 | MFD regency/city | `parent={province_7_digit}` |
| 28 | MFD district | `parent={regency_7_digit}` |
| 22 | Subject/chapter | `wilayah=1306000` |
| 23 | Semua table suatu wilayah | `wilayah=1306000` |
| 24 | Table wilayah per MMS subject | `wilayah`, `id_subjek` |
| 25 | Detail table | `wilayah`, `tahun`, `id_tabel` (live API lowercase; docs menulis stale `Tahun`) |
| 34 | Global master table | — |
| 36 | Master table detail | `id_tabel` |

SIMDASI table catalogue minimum:

```json
{
  "id_tabel": "opaque-base64-like-id",
  "kode_tabel": "1.1.1",
  "judul": "...",
  "judul_en": "...",
  "ketersediaan_tahun": [2019, 2020, 2021],
  "id_subject": "opaque-id",
  "bab": "...",
  "subject": "...",
  "mms_id": 516,
  "mms_subject": "..."
}
```

`id_tabel` adalah opaque identifier. Jangan decode, mengubah padding, atau menebak relasi dari bentuk string. Detail service dipanggil untuk **setiap exact table-year** pada `ketersediaan_tahun`.

### 2.5 Publication

| Resource | Endpoint | Key fields |
|---|---|---|
| List | `list/model/publication/domain/1306` | pub ID, title, dates, cover, PDF URL, declared size |
| Detail | `view/model/publication/domain/1306/id/{pub_id}` | category/publication number, ISBN/ISSN, abstract, revision fields |
| Binary | `pdf` URL dari metadata | exact PDF bytes, checksum, content type |

Metadata dan binary dipisah. PostgreSQL tidak menyimpan PDF sebagai `bytea`; binary disimpan di object/file store dengan local pointer dan SHA-256.

### 2.6 Glosarium

| Resource | Endpoint | Keterangan |
|---|---|---|
| List | `list/model/glosarium` | paginated global concept catalogue |
| Detail | `view/model/glosarium/id/{id}` | satu konsep |

List hit memakai Elasticsearch-style wrapper `_source`. Important fields:

- `id`, `idSds`, `noIndikator`;
- `judulIndikator` / English;
- `konsep` / English;
- `definisi` / English;
- classification, measure, unit;
- content/data source;
- endpoint and flag.

**Quirk live:** endpoint glosarium saat ini balas `500 - Undefined property: stdClass::$hits` untuk SEMUA kombinasi parameter (default, `perpage`, `prefix`). Ini bug server-side BPS (backend Elasticsearch tidak mengembalikan struktur `hits`), bukan masalah client/parameter. Model benar: `glosarium`, tanpa `domain`/`lang`, response ES `_source` wrapper (`_index`, `_id`, `_score`, `_source`, `sort`), 5.078 konsep. Glossary di-skip dari run dan di-retry pada schedule berikutnya; crawler harus tetap fail-closed (jangan menganggap 500 sebagai "kosong").

## 3. PostgreSQL architecture

Database isolated development instance:

```text
container: marawa-bps-postgres
listen:    127.0.0.1:55432
volume:    marawa-bps-postgres-data
secret:    /home/ubuntu/.config/marawa-ai/postgres.env (0600)
```

Production credentials/ports harus dikelola deployment secret manager; nilai development tidak ditulis dalam dokumentasi atau repository.

### 3.1 Raw/history layer

#### `bps_ingestion_runs`

Satu full/update/resume run, status, redacted config, summary, timestamps.

#### `bps_raw_snapshots`

Immutable logical response version:

| Column | Meaning |
|---|---|
| `run_id` | ingestion execution |
| `source_family/resource_type` | family and exact logical resource |
| `request_fingerprint` | SHA-256 canonical request without key |
| `request_json` | key-free URL/path params |
| `response_sha256` | canonical JSON hash |
| `response_json` | exact parsed JSON response |
| `fetched_at/last_seen_at` | first and latest observation |

Unique `(request_fingerprint, response_sha256)` berarti unchanged rerun hanya menaikkan `last_seen_at`; changed response menghasilkan snapshot baru.

#### `bps_snapshot_observations`

Append-only-ish run manifest yang menghubungkan setiap ingestion run dengan exact deduplicated snapshot yang diamati. Ini mempertahankan audit completeness per run walaupun raw response tidak berubah dan row `bps_raw_snapshots.run_id` tetap menunjuk run pertama yang membuat versi tersebut.

#### `bps_ingestion_checkpoints`

Per-family resumable cursor: completed variable IDs, table-year IDs, census combinations, publication detail IDs, and `done` state.

### 3.2 Normalized current layer

| Family | Tables |
|---|---|
| Dynamic | `bps_dynamic_subjects`, `bps_dynamic_variables`, `bps_dynamic_dimensions`, `bps_dynamic_facts` |
| Census | `bps_census_events`, `bps_census_topics`, `bps_census_areas`, `bps_census_datasets`, `bps_census_facts` |
| SIMDASI | `bps_simdasi_regions`, `bps_simdasi_subjects`, `bps_simdasi_tables`, `bps_simdasi_details`, `bps_simdasi_columns`, `bps_simdasi_facts` |
| Publication | `bps_publications`, `bps_publication_files` |
| Glossary | `bps_glossary` |

Current tables memakai natural external identity + scope as PK. Upsert memperbarui current representation dan `snapshot_id`, sedangkan raw history tidak hilang.

### 3.3 Serving layer

Existing views (status 2026-08-15: serving fixes sudah di-build via `migrations/001`):

- `bps_serving_dynamic` — menambahkan `period_granularity` (annual/quarterly), `subperiod_code/label`, `primary/secondary_dimension_id/label`, `unit_raw`, dan `unit_state` (known/unitless/unknown_review).
- `bps_serving_census`
- `bps_serving_simdasi` — menambahkan `row_role` (`category` bila `geography_code` kosong, `kabupaten`/`kecamatan` bila berisi) dan `geography_level` NULL untuk baris kategori; `unit` PDRB diturunkan dari judul tabel (`miliar rupiah`), bukan `Rp`.

Production menambah allowlisted views/tool contracts untuk SIMDASI setelah row/table-detail shapes selesai diprofilkan. Public agent mendapat read-only role atas serving views, bukan ingestion/base/raw tables.

### 3.4 SIMDASI unit registry

`bps_simdasi_units (region_code, table_code, column_name, unit, unit_source)` menyimpan unit efektif setiap indikator SIMDASI. General — dibangun untuk seluruh region dari data ter-ingest, tanpa hardcode:

| Precedence | `unit_source` |
|---|---|
| Metadata kolom (`bps_simdasi_columns.unit`) | `column_meta` |
| Pola nama indikator nasional (laju pertumbuhan, rasio jenis kelamin, persentase) | `column_name` |
| Unit literal pada nama kolom | `column_name` |
| Unit literal pada judul tabel | `table_title` |
| Kolom dengan nama sama di tabel lain | `sibling_table` |
| Family tabel (dua segmen kode, mis. `3.1`) dengan tepat satu unit judul | `family_title` |
| Kolom `Jumlah/Banyaknya/Desa...` tanpa unit → angka polos | `count` |
| Tabel `show_satuan` dengan unit per baris → fallback `row_unit` fact | `row_varied` |
| Kolom `data_type='Teks'` | `text_column` |

Builder: `scripts/build_simdasi_registry.py [--region X]` (default semua region). Legend marker upstream dinormalisasi ke `bps_simdasi_marker_legend` (`–` = tidak ada/nol, `...` = tidak tersedia, `NA`, `e` estimasi, `r` revisi, dst.).

### 3.5 Dynamic unit canonicalization

Dynamic tidak butuh registry: unit langsung dari `bps_dynamic_variables.unit`. `upsert_dynamic_variables` juga menulis `unit_canonical` hasil `canonical_unit()` (`workers/ingestion/bps_units.py`):

- `M2/M3/KM2` → `m²/m³/km²`, `KG` → `kg`, `Milyar Rupiah` → `miliar rupiah`, `Hektar` → `ha`, `Ribuan VA` → `ribuan VA`.
- `Tidak Ada Satuan`/`-`/kosong → `NULL` (bare count).
- Unit lain ditrim + lowercase.

`bps_serving_dynamic.unit = coalesce(v.unit_canonical, f.unit)` (join `bps_dynamic_variables`). Backfill idempotent: `scripts/backfill_dynamic_units.py`.

## 4. Data type policy

| Concern | Policy |
|---|---|
| External IDs | `text`; jangan cast opaque/base64/UUID menjadi integer |
| MFD/geography code | `text`; leading zero harus dipertahankan |
| Period | raw `text` + normalized typed interpretation pada registry |
| Numeric value | `numeric`; special marker masuk `value_text` |
| Categories | normalized JSON array + raw row |
| Publication dates | raw text saat ingestion; parsed date pada serving view setelah quality audit |
| Upstream timestamps | raw text + optional typed projection |
| Unknown fields | selalu preserved in `raw jsonb` |
| Missing vs zero | `NULL` tidak sama dengan `0`; dash/ellipsis tidak dipaksa menjadi zero |

## 5. Ingestion semantics

### 5.1 Full/bootstrap

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/ingest_bps_webapi.py \
  --families simdasi,dynamic,census,publication,glossary
```

### 5.2 Resume

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/ingest_bps_webapi.py \
  --families simdasi,dynamic,census,publication,glossary --resume
```

`--resume` skips completed fine-grained resources based on DB checkpoint. It does not trust a local text file.

### 5.3 Update: manual check dulu, targeted pull setelah approval

**Tidak ada scheduled full crawl.** WebAPI BPS tidak menawarkan webhook, `ETag`, revision cursor, atau `updated_at` pada Dynamic fact payload. Mengulang ribuan request secara periodik hanya untuk "mungkin ada update" berisiko menghabiskan quota/app key dan tidak dijalankan otomatis.

**Tidak ada job terjadwal/cronjob BPS aktif** atas instruksi Tah. Jika ingin melihat signal perubahan, Tah menjalankan manual:

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/check_bps_updates.py
```

Manual sentinel memiliki hard cap tiga request (satu attempt/request, tanpa retry/pull):

1. SIMDASI service 23: seluruh table catalogue (`latest_update` + `ketersediaan_tahun`);
2. Dynamic var page 1: total catalogue + metadata page pertama;
3. Publication page 1: total catalogue + newest/revised metadata page pertama.

Sentinel tidak menarik detail table, Dynamic/Census facts, Publication detail/PDF, atau Glosarium; tidak mengubah PostgreSQL dan tidak menjalankan crawler. Jika ada signal, report `data/reports/bps-update-sentinel-latest.json` menjadi dasar keputusan Tah untuk targeted pull.

Untuk update yang Tah ketahui langsung dari BPS, tidak perlu sentinel: gunakan katalog metadata `data/reports/BPS_CATALOG_TABEL_1306.xlsx`, lalu sebut identifier exact (mis. `SIMDASI 3.1.1 tahun 2025 update`). Prosedur scope, format instruksi, dan bukti targeted update ada di [`20-BPS-MANUAL-UPDATE-WORKFLOW.md`](20-BPS-MANUAL-UPDATE-WORKFLOW.md).

Signal yang dapat dipercaya:

| Family | Sinyal murah | Tindakan setelah approval |
|---|---|---|
| SIMDASI | table baru, `latest_update` berubah, atau year baru di `ketersediaan_tahun` | fetch hanya exact table-year berubah/baru |
| Publication | total berubah, publication baru, atau `updt_date` berubah di newest page | fetch metadata detail hanya publication ID terindikasi; PDF optional/manual |
| Dynamic | total variable berubah atau metadata pada page pertama berubah | fetch katalog untuk identifikasi ID baru; fetch data hanya variable target |
| Census | event-release driven, tidak dipoll periodik | pull saat rilis SP/ST baru diumumkan atau Tah minta |
| Glosarium | upstream saat ini HTTP 500 | tidak dipoll oleh sentinel; test manual setelah upstream pulih |

**Batas jujur:** silent revision pada nilai fact Dynamic lama yang tidak mengubah variable catalogue tidak dapat dideteksi tanpa membaca fact tersebut. Untuk kasus itu, gunakan targeted refresh berdasarkan indikator/periode yang diminta, atau full audit **hanya** bila Tah mengotorisasi.

Manual full audit tetap tersedia, bukan cron:

```bash
scripts/update_bps_webapi.sh full
```

Sebelum manual full audit, wrapper membuat PostgreSQL custom-format dump, SHA-256 sidecar, mode `0600`, memvalidasi restore manifest, dan mempertahankan tujuh backup terbaru secara default.

Update current rows tetap idempotent; changed responses membuat raw snapshot baru. Deletions tidak di-hard-delete otomatis karena absence bisa berasal dari partial upstream failure.

### 5.4 Request discipline

- serialized requests;
- configurable 0.5–1.0 second inter-request pacing baseline, plus actual network latency; serialized only;
- bounded retry and capped exponential backoff;
- HTTP 429/5xx retryable;
- HTTP 200 HTML/WAF retryable failure;
- non-OK JSON is failure, not empty data;
- per-resource checkpoint after committed DB upsert;
- exception/error contains key-free canonical request only.

## 6. Publication binary mirror

Plan capacity before download:

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/download_bps_publications.py --plan
```

Download when gate allows:

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/download_bps_publications.py --reserve-gib 5
```

Controls:

- `.part` resumable files;
- server Range support when available;
- `%PDF` magic validation;
- SHA-256 after full download;
- DB status `downloaded|failed` per publication;
- minimum operational disk reserve;
- no external filename use; path generated from sanitized publication ID.

## 7. Validation and exploration

Generate database-grounded report:

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/analyze_bps_database.py
```

Outputs:

- `data/reports/bps-exploration.json`
- `data/reports/bps-exploration.md`

Fail-closed integrity gate:

```bash
/home/ubuntu/.hermes/bin/uv run python scripts/validate_bps_database.py
```

Gate memeriksa latest run completed, SIMDASI expected-vs-actual table-years, orphan rows, invalid external IDs, snapshot FK integrity, dan semua family checkpoints `done=true`.

Report must include actual row counts, period/year coverage, variables without facts, table-year gaps, census facts by event, publication range/PDF availability, glossary definition coverage, raw snapshot count, and ingestion errors.

## 8. Quality/anomaly checks

### Dynamic

- variable list count vs variables with data;
- unmatched `datacontent` keys;
- empty definition/unit;
- numeric marker rate;
- duplicate indicator names with different IDs/definitions;
- vertical/geography label inconsistencies;
- period label parseability and range.

### Census

- event/topic/dataset dependency completeness;
- exact Padang Pariaman MFD availability;
- category slot spelling variants;
- data count in envelope vs normalized rows;
- indicator ID/name consistency;
- geography code/level consistency;
- duplicate row hashes.

### SIMDASI

- table count and subject coverage;
- expected `ketersediaan_tahun` vs downloaded detail count;
- empty/non-JSON detail;
- table code/title collision;
- unit/footnote/source extraction;
- MFD version drift;
- detail response schema variants by table/year.

### Publication

- list vs detail completeness;
- release/update date parseability;
- duplicate ISBN/ISSN or title;
- broken/non-PDF URL;
- declared vs actual size;
- checksum duplication across publication IDs;
- revised publication lineage.

### Glossary

- empty definitions;
- duplicate normalized concept;
- conflicting definitions;
- missing unit/classification/source;
- Indonesian/English coverage.

## 9. Source priority and conflict

For local statistical answers, default priority is:

1. approved local internal release views;
2. SIMDASI local table if exact table/year/region exists;
3. Dynamic Data domain 1306;
4. Census fact for matching event/area;
5. publication table/text;
6. glossary for definition only.

Priority does not override comparability. If two values differ, compare indicator definition, period, geography, unit, category, provisional/revision status, and update timestamp. If still conflicting, show both with labels rather than silently selecting one.

## 10. Security and access

- Ingestion role: write normalized/raw BPS schemas only.
- Public agent role: SELECT on allowlisted serving views only.
- API key: connector secret; never enters prompt/provider/context.
- Raw upstream text: tainted evidence, never policy/instruction.
- PDF parsing: sandbox, no external fetch, active content disabled.
- No arbitrary SQL: typed tools use dataset registry and parameterized templates.
- Every answer fact stores snapshot/source lineage.

## 11. Acceptance criteria

- All five source families have raw snapshots and normalized/current tables.
- All resources exposed for domain `1306` and region `1306000` are attempted and checkpointed.
- A partial upstream response cannot be interpreted as successful zero data.
- Rerun does not duplicate current rows.
- Changed upstream JSON creates a new raw version.
- Exact actual counts and gaps appear in exploration report.
- Public agent can answer through typed serving views without API-key/provider exposure.

## 12. Independent review disposition (2026-08-14)

Fixed before production sign-off:

- Nested interoperability service status/condition now validated (P0).
- Census never falls back to province; null area catalogue and missing exact area are surfaced (P0).
- Malformed rows reject the whole resource instead of silent drop (P0).
- Publication list metadata (pdf/size/cover/dates) survives a detail-only response via merge (P0).
- Persistent API `null` data is retried then accepted as explicit empty, with raw lineage (P1).
- Required external identifiers are validated (no `"None"` collisions) (P1).
- Dynamic fact responses are validated against the requested variable (P1).
- Secret files must be regular, non-symlink, current-user-owned, and non-group/world readable (P1).

Deferred (documented, non-blocking for bootstrap):

- Resume checkpoints are not yet bound to a specific interrupted run or catalogue fingerprint; a future hardening pass will version them.
- Pagination trusts reported `pages` without cross-checking `total`; add dedupe/gap detection later.
- Concatenated dynamic key decoding keeps first match on theoretical ID-collision; ambiguous keys are flagged raw rather than resolved.
- Standalone `subcat`, `unit`, `turth` catalogues, SIMDASI services 24/34/36, and glossary per-concept detail are not yet fetched; dimension values are captured from `data` responses and the glossary list already carries definitions.
- Fatal-first-attempt attempt counter and SIGTERM finalization are minor; KeyboardInterrupt already finalizes runs.

## Sources

[9] https://webapi.bps.go.id/documentation — Dokumentasi resmi WebAPI BPS
[13] https://github.com/bps-statistics/stadata — STADATA, client package pada organisasi GitHub BPS-Statistics Indonesia
[14] https://padangpariamankab.bps.go.id — BPS Kabupaten Padang Pariaman
[15] https://sensus.bps.go.id — Website Sensus BPS
[16] https://webapi.bps.go.id/developer — Portal WebAPI BPS
