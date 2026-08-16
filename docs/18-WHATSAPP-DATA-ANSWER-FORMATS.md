# Format Jawaban Data BPS di WhatsApp

## 1. Tujuan

Jawaban harus cepat dibaca di layar ponsel, tetap lengkap secara statistik, dan dapat diaudit. Format bukan template teks mentah dari LLM; values, unit, period, geography, status, dan source dibentuk deterministic formatter dari typed result. Model hanya menulis narasi/caveat yang telah divalidasi.

## 2. Invariant setiap jawaban angka

Setiap angka publik wajib mempunyai:

1. indicator/table title;
2. geography;
3. reference period;
4. value dan unit;
5. category/dimension bila relevan;
6. source family dan BPS unit;
7. update/fetch timestamp;
8. revision/provisional/note bila ada;
9. evidence/result ID internal pada message metadata;
10. link hanya bila berasal dari source registry.

Jangan mencampur release date publikasi dengan period data. Jangan mengubah `persen` menjadi `poin persentase`. Marker `-`, `…`, dan `NA` tidak boleh dinarasikan sebagai nol. Marker SIMDASI `–` berarti “tidak ada atau nol” menurut legend upstream; tetap tampilkan marker+catatan kecuali metadata tabel secara eksplisit mengizinkan coercion ke numeric zero.

### 2.0 Format angka (wajib global)

Semua angka publik diformat deterministik oleh `format_id_number()` (`workers/ingestion/bps_format.py`), konvensi Indonesia — **ribuan titik `.`, desimal koma `,`**:

- `467000` → `467.000`
- `59.7` → `59,7`
- `1234.56` → `1.234,56`
- `-10500.5` → `-10.500,5`

Model tidak boleh memformat angka manual. Angka selalu lewat formatter; unit ditambahkan setelah angka (mis. `467.000 ribu jiwa`).

### 2.1 Unit wajib dari registry (SIMDASI)

Unit angka SIMDASI **tidak boleh** diinfer dari nama indikator oleh model. Sumber unit satu-satunya adalah `bps_serving_simdasi.unit` yang dibangun oleh `scripts/build_simdasi_registry.py` (lihat `docs/17` §3.4):

- `unit` non-null → tampilkan persis (mis. `467 ribu jiwa`).
- `unit_source='count'` (mis. `Jumlah Desa`) → angka polos tanpa unit.
- `unit_source='row_varied'` → unit mengikuti `row_unit` per baris (mis. produksi pertanian: `1.234 kw`).
- `unit_source='text_column'` → render `value_text` apa adanya (mis. nama ibu kota), bukan angka.
- `geography_level` → label jawaban: `kabupaten` (total) vs `kecamatan`.

## 3. Candidate discovery dan pagination

Candidate offering dipakai untuk setiap goal baru yang belum membawa candidate ref/kode exact. Agent boleh merekomendasikan kandidat terbaik, menjelaskan trade-off, dan menambah source approved, tetapi **tidak query facts sebelum user memilih**. Ini tetap bukan menu bot kaku: user dapat memilih dengan bahasa natural dan agent dapat rerank/menjelaskan. Valid explicit ref/kode exact serta follow-up active dataset tidak membutuhkan list ulang. Ref/cursor dibentuk server dari candidate set, bukan dikarang model.

```text
Saya menemukan beberapa sumber yang relevan. Untuk angka terbaru yang exact, saya paling menyarankan D1.

*SIMDASI*
S1. Jumlah penduduk, pertumbuhan, dan kepadatan per kecamatan — 2010, 2018–2025
S2. Penduduk menurut umur dan jenis kelamin — 2021–2026

*Data Dinamis*
D1. Jumlah penduduk per kecamatan dan jenis kelamin — 2002–2025
D2. Penduduk menurut kelompok umur dan jenis kelamin — 2002–2023
D3. Jumlah penduduk miskin per kabupaten/kota — 2005–2023

*Publikasi*
P1. Kabupaten Padang Pariaman Dalam Angka 2026

Bisa jawab “D1”, “yang umur”, “yang dinamis”, atau “lanjut publikasi”.
```

Aturan channel:

- default maksimal 3 kandidat per source family/page;
- tampilkan hanya 2–3 family paling relevan per message; source lain dapat ditawarkan sebagai lanjutan;
- halaman berikut mempertahankan ref lama dan melanjutkan nomor (`D4–D6`);
- `lanjut` memakai focused group; `lanjut dynamic`/`publikasi lainnya` memilih group eksplisit;
- user dapat memilih dengan ref, nomor, judul, atau deskripsi natural;
- kandidat menyebut title, pembeda makna, coverage periode, dimensi utama, dan unit bila relevan;
- jangan menampilkan opaque IDs/cursors internal;
- jika satu kandidat jelas unggul, rekomendasikan alasannya—jangan hanya menumpahkan daftar.

Full candidate/probing/query contract ada di `21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`.

## 4. Single value

```text
*Jumlah Penduduk*
📍 Wilayah: Kabupaten Padang Pariaman
📅 Periode: 2025
📊 Nilai: *462.125 orang*

Sumber: BPS Kabupaten Padang Pariaman — SIMDASI
Diperbarui: 14 Agustus 2026
```

Jika category dibutuhkan:

```text
*Jumlah Penduduk — Laki-laki*
📍 Wilayah: Kecamatan Batang Anai
📅 Periode: 2025
📊 Nilai: *27.420 orang*

Kategori: Jenis Kelamin = Laki-laki
Sumber: BPS Kabupaten Padang Pariaman — SIMDASI
```

## 5. Comparison

```text
*Perbandingan Jumlah Penduduk*
📍 Kabupaten Padang Pariaman

• 2024: 457.820 orang
• 2025: 462.125 orang
• Perubahan: *+4.305 orang (+0,94%)*

Sumber: BPS Kabupaten Padang Pariaman — SIMDASI
Catatan: Persentase perubahan dihitung MARAWA dari dua nilai sumber di atas.
```

Calculation dilakukan deterministic tool. Message metadata menunjuk dua source result IDs dan analysis method/version.

## 6. Trend

Untuk maksimal lima periode:

```text
*Tren Tingkat Pengangguran Terbuka*
📍 Kabupaten Padang Pariaman

2021 — 6,42%
2022 — 6,18%
2023 — 5,97%
2024 — 5,81%
2025 — 5,73%

Ringkas: turun 0,69 poin persentase dari 2021 ke 2025.
Sumber: BPS WebAPI — Dynamic Data, domain 1306
```

Lebih dari lima periode: tampilkan first/latest/key turning points dan tawarkan chart/XLSX; jangan mengirim tabel panjang tak terbaca.

## 7. Ranking/top-bottom

```text
*3 Kecamatan dengan Nilai Tertinggi — [Indikator]*
📅 Periode: 2025 | Unit: persen

1. Kecamatan A — 18,42%
2. Kecamatan B — 16,90%
3. Kecamatan C — 15,77%

Cakupan: 17 kecamatan
Sumber: BPS Kabupaten Padang Pariaman — SIMDASI
```

Footer wajib menyebut coverage/jumlah dibandingkan agar ranking subset tidak terlihat universal.

## 8. Definition/glossary

```text
*Apa itu Tingkat Pengangguran Terbuka?*

Tingkat Pengangguran Terbuka adalah persentase jumlah pengangguran terhadap jumlah angkatan kerja.

Satuan: persen
Sumber definisi: Glosarium BPS
```

Bila definisi glosarium umum tetapi indicator lokal memiliki metadata khusus, tampilkan metadata indicator dahulu dan beri label “Definisi umum BPS” pada glossary.

## 9. Publication result

```text
*Kabupaten Padang Pariaman Dalam Angka 2026*
📅 Rilis: 27 Februari 2026
📄 Ukuran: 12,3 MB

Ringkasan: [abstract resmi atau ringkasan ter-grounding]

Unduh PDF resmi:
https://...
Sumber: BPS Kabupaten Padang Pariaman
```

Jangan membuat URL. Hanya gunakan `pdf_url` dari metadata aktif atau protected local artifact link dengan expiry.

## 10. SIMDASI table result

Jika tabel lebar:

```text
*[Judul Tabel SIMDASI]*
📍 Kabupaten Padang Pariaman
📅 2025 | Unit: orang

[3–8 baris paling relevan]

Tabel ini memiliki 17 baris. Ketik:
• “tampilkan semua” untuk lanjutan terpaginasikan
• “buat Excel” untuk file lengkap
• “buat grafik” untuk visualisasi

Sumber: BPS — SIMDASI, tabel 3.2.1
```

WhatsApp message maksimal memuat subset yang jelas. Full table memakai artifact CSV/XLSX/PDF, bukan puluhan message otomatis.

## 11. Census result

```text
*Jumlah Penduduk — SP2020*
📍 Kabupaten Padang Pariaman
📅 Periode sensus: 2020
📊 Nilai: *430.626 orang*

Kegiatan: Sensus Penduduk 2020
Dataset: [nama dataset]
Kategori: [jika ada]
Sumber: BPS — Data Sensus
```

Selalu sebut event census; nilai sensus tidak boleh terlihat seperti estimasi tahunan terbaru.

## 12. Ambiguity/clarification

```text
Saya menemukan beberapa data “kemiskinan”:

1. Jumlah penduduk miskin — ribu orang
2. Persentase penduduk miskin — persen
3. Garis kemiskinan — rupiah/kapita/bulan

Yang ingin dilihat nomor berapa dan untuk tahun berapa?
```

Maksimal satu clarification question per turn. Pilihan berasal dari dataset registry, bukan tebakan model.

## 13. Conflicting sources

```text
Saya menemukan dua angka yang belum dapat diperlakukan sama:

• SIMDASI 2025: 462.125 orang
• Dynamic Data 2025: 461.980 orang

Perbedaannya mungkin berasal dari cakupan, waktu pemutakhiran, atau revisi sumber. Saya belum akan memilih salah satu sebagai angka final sebelum metadata pembandingnya cocok.

Saya bisa menampilkan rincian sumber atau meneruskan ke petugas PST.
```

Jangan merata-ratakan conflicting values.

## 14. No data / partial ingestion

### Source lengkap, data tidak tersedia

```text
Data [indikator] untuk [wilayah/periode] belum tersedia pada sumber BPS yang telah terindeks.

Data terdekat yang tersedia: [opsional]
Saya bisa mencari periode lain atau menghubungkan ke petugas PST.
```

### Source sedang gagal/partial

```text
Sebagian sumber BPS sedang belum selesai diperbarui, jadi saya belum dapat memastikan jawaban lengkap untuk permintaan ini.

Saya dapat mencoba data yang sudah tervalidasi atau meneruskan ke petugas PST.
```

Jangan menyebut “tidak ada data” jika ingestion run partial/error.

## 15. Notes and footnotes

- Maksimal dua footnotes penting di chat.
- Footnote panjang diringkas tanpa mengubah meaning dan ditawarkan sebagai “lihat catatan lengkap”.
- Provisional/revised/coverage break selalu diprioritaskan.
- Causal caveat wajib pada correlation/association.
- Rounding mengikuti dataset registry; raw value tetap dalam result lineage.

## 16. Source labels

Gunakan label konsisten:

| Database family | Public label |
|---|---|
| `simdasi` | `BPS Kabupaten Padang Pariaman — SIMDASI` |
| `dynamic` | `BPS WebAPI — Dynamic Data, domain 1306` |
| `census` | `BPS — Data Sensus [event]` |
| `publication` | `BPS Kabupaten Padang Pariaman — Publikasi` |
| `glossary` | `Glosarium BPS` |

## 17. Message metadata internal

Outbound message menyimpan metadata non-visible:

```json
{
  "dataset_id": "bps:simdasi:1306000:table-id",
  "snapshot_ids": ["snapshot-opaque-id"],
  "result_id": "opaque",
  "analysis_id": null,
  "indicator": "...",
  "geography_code": "1306000",
  "period": "2025",
  "unit": "orang",
  "source_family": "simdasi",
  "formatter_version": "bps-wa-v1"
}
```

Follow-up menggunakan opaque references ini, bukan menyalin angka dari transcript.

## 18. Deterministic validation

Sebelum send:

- semua displayed values ada pada result rows;
- unit/period/geography/category cocok;
- calculation recomputed dari input result IDs;
- source label cocok dengan source family;
- URL berasal allowlist/snapshot;
- no raw API key/internal DB path;
- no publication release date used as statistical reference period;
- message sesuai Bahasa Indonesia dan panjang channel budget.

## 19. Test examples

Golden tests minimum:

- single integer/decimal/percentage;
- percentage vs percentage-point change;
- marker dash/ellipsis;
- category and geography breakdown;
- multi-period trend;
- ranking with coverage;
- census event label;
- publication metadata and PDF link;
- glossary definition;
- source conflict;
- ingestion partial vs true no-data;
- follow-up “bandingkan”, “tertinggi”, “grafiknya”, dan “sumbernya”.

## 20. Answer gate + formatter reference implementation (16 Aug)

Fondasi deterministik untuk invariant §2 sudah ada di repo (trusted control
plane, read-only; runtime engine memanggil fungsi yang sama):

- `scripts/answer_gate.py` — `Evidence`, `DerivedResult`, `GateContext`,
  `evaluate(envelope, context) -> GateVerdict`, `abstention_text(NoDataReason…)`,
  `safe_response(verdict)`. Berbasis ADR-016/018 rules, bukan prompt:
  numeric grounding (angka draft harus tertelusur ke evidence), unit
  publishable, selection envelope, citation allowlist, period disclosure,
  leakage, bahasa Indonesia. Blokir = teks fixed (tidak menjelaskan internal,
  tidak bocor draft). `NoDataReason`: not_in_catalogue, period_unavailable,
  geography_unavailable, unit_under_review, gate_blocked, unclear_question —
  abstention tiap reason spesifik dengan `available_periods/geographies` dari
  runtime.
- `scripts/answer_formatter.py` — `format_number()` (konvensi Indonesia),
  `format_single_value()`, `format_trend()` (skip baris unpublishable +
  beri tahu jumlah), `format_candidates()` (discovery, tidak pernah bocor
  angka), `escape_wa()` (markup/netralisir upstream data).

Kontrak JSON di `packages/contracts/bps-query-contracts.schema.json`:

- `AnswerGateVerdict` — `{allowed, violations, observations, ungrounded_numbers}`
- `SafeRefusalResponse` — `safe_response()`: `{scope, run_status: abstained,
  answer_type, answer, evidence_ids, blocked_by_gate, internal_violations}`

Cross-family `unit_state` vocabulary: serving view `bps_serving_dynamic`
memakai `canonical|known|unknown_review|review_required`; registry measure
memakai `known|unitless|unknown_review|review_required`. Gate menerima ketiga
`known`, `unitless`, DAN `canonical` (unit dari
`bps_dynamic_variables.unit_canonical` — provenance terbaik) sebagai publishable.

Tests: `tests/test_answer_gate.py` (28 case: parsing angka Indonesia, grounding
anti-hallucination, abstention, leak-proof refusal, `canonical` publishable) dan
`tests/test_outbox_and_formatter.py` (format angka, single value, trend,
candidate tanpa angka, end-to-end gate).
