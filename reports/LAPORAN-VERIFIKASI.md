# Laporan Verifikasi — yang harus dijalankan sendiri

**Tanggal:** 15 Agustus 2026
**Konteks:** 163 tes lulus di lingkungan tanpa DB/jaringan. Dokumen ini mendaftar
semua yang **belum pernah dieksekusi sekali pun** dan hanya bisa dijalankan di
lingkungan lo.

Urutannya penting. Jangan dilompati — beberapa langkah adalah prasyarat langkah
berikutnya, dan satu langkah punya risiko kehilangan data kalau salah urutan.

---

## Ringkasan risiko

| Langkah | Risiko | Kenapa |
|---|---|---|
| 1. Migrasi 006 | **Sedang** | Mengubah view + menambah CHECK constraint. Ada `.down.sql`, sudah diuji strukturnya, belum pernah dijalankan |
| 2. Cek privilege | Rendah | Read-only, hanya membaca katalog PostgreSQL |
| 3. Rebuild registry | **Tinggi** | Script paling banyak diubah dan **belum pernah dijalankan sekali pun** |
| 4. Query prototypes | Rendah | Read-only |
| 5. Probe model | Rendah | Beberapa panggilan API, biaya token kecil |
| 6. Jalankan server | Rendah | In-memory, tidak menyentuh DB |

---

## Langkah 1 — Migrasi 006

```bash
cd marawa
uv run python scripts/run_migrations.py --up 006
```

### Yang dilakukan migrasi ini

- `DROP` + `CREATE` `bps_serving_dynamic` — buang kolom `primary_dimension_*`
  (duplikat geography), tambah `category_code` / `category_label`
- `DROP` + `CREATE` `bps_serving_simdasi` — tambah kolom `unit_state`
- `GRANT SELECT` ulang ke `marawa_runtime_ro` (karena `DROP VIEW` menghapus grant)
- Tambah kolom `queryable`, `quality_flags`, `unit_source` di `measure_registry`
- Tambah CHECK constraint `measure_registry_queryable_requires_unit`
- Backfill: set `queryable=false` untuk measure yang `unit_state` bukan known/unitless

### ⚠️ Yang paling mungkin gagal

**CHECK constraint ditolak karena data lama melanggarnya.** Migrasi menjalankan
`UPDATE` backfill **setelah** `ADD CONSTRAINT`. Kalau PostgreSQL memvalidasi
constraint saat ditambahkan dan ada baris lama yang melanggar, migrasi gagal di
titik itu.

**Kalau gagal dengan pesan constraint violation**, jalankan backfill-nya dulu
secara manual, baru ulangi migrasi:

```sql
UPDATE bps_registry.measure_registry
SET queryable = false
WHERE unit_state NOT IN ('known','unitless')
   OR unit_source = 'title_matched';
```

Ini bug urutan di migrasi yang gw tulis dan tidak bisa gw verifikasi tanpa DB.
Kalau lo kena, kabari — akan gw perbaiki urutannya di file migrasi.

### Kalau berhasil, verifikasi

```sql
\d+ bps_serving_dynamic          -- harus ada category_code, TIDAK ada primary_dimension_id
\d+ bps_serving_simdasi          -- harus ada unit_state
\d bps_registry.measure_registry -- harus ada queryable, quality_flags, unit_source
```

### Rollback kalau perlu

```bash
uv run python scripts/run_migrations.py --down 006
```

---

## Langkah 2 — Cek privilege runtime

```bash
uv run python scripts/check_runtime_privileges.py
```

**Harus:** `runtime privilege check: PASS`

**Kalau `MISSING GRANT`:** `DROP VIEW` di langkah 1 menghapus grant dan
re-grant di migrasi tidak jalan. Jalankan ulang `migrations/004_runtime_readonly_role.sql`.

**Kalau `EXCESS GRANT`:** role runtime bisa membaca tabel mentah — ini serius,
artinya batas read-only tidak seperti yang diklaim dokumen. Kirim outputnya.

Jalankan perintah ini **setiap kali** habis migrasi atau rebuild view. Ini satu-satunya
hal yang mencegah batas read-only rusak diam-diam.

---

## Langkah 3 — Rebuild registry ⚠️ RISIKO TERTINGGI

```bash
# WAJIB: ambil angka SEBELUM rebuild, untuk dibandingkan
psql "$POSTGRES_DSN" -f - <<'SQL'
SELECT 'measures_total', count(*) FROM bps_registry.measure_registry
UNION ALL SELECT 'answerable_datasets', count(*) FROM bps_registry.dataset_registry WHERE answerability='answerable'
UNION ALL SELECT 'unit_unknown', count(*) FROM bps_registry.measure_registry WHERE unit_state='unknown_review'
UNION ALL SELECT 'unit_title_matched', count(*) FROM bps_serving_simdasi WHERE unit_source='title_matched';
SQL

uv run python scripts/build_bps_registry.py
```

### Kenapa risikonya tinggi

`build_bps_registry.py` adalah file yang **paling banyak diubah** dan **belum
pernah dijalankan sekali pun**. Yang berubah: `_unit_state()` (tanda tangan
fungsi +1 argumen), `_aggregation()` (+1 argumen), `_measure_gate()` (fungsi
baru, dipakai 4 tempat), query SIMDASI (+2 kolom), blok gate dataset (ditulis
ulang), integrity gate (+3 pemeriksaan), 8 template (dari 6), dan INSERT
`measure_registry` (+3 kolom).

### Yang SUDAH diverifikasi tanpa DB

- Semua call site `_unit_state` / `_measure_gate` konsisten dengan tanda tangan barunya
- Scope variabel di blok gate dataset benar (`measures`, `datasets` tersedia)
- Logika gate dijalankan lawan data sintetis: measure `known` lolos, `unknown_review`
  dan `review_required` diblokir, dataset campuran tetap answerable dengan flag,
  dataset yang semua measure-nya buruk jadi `blocked_quality`, integrity gate
  tidak melaporkan error palsu
- 8 template: semua parameter terpakai di SQL, `has_own_limit` konsisten

### Yang TIDAK bisa diverifikasi tanpa DB

- Apakah `bps_serving_simdasi` benar-benar punya kolom `unit_source` dan
  `indicator_code` dengan nama persis itu (query SIMDASI `GROUP BY 1,2,3,4` akan
  gagal kalau tidak)
- Apakah INSERT `measure_registry` cocok dengan skema setelah migrasi 006
- Apakah ada measure nyata yang bentuknya tidak terduga

### Setelah rebuild, ambil angka yang sama lagi

```sql
SELECT 'measures_queryable', count(*) FROM bps_registry.measure_registry WHERE queryable
UNION ALL SELECT 'measures_blocked', count(*) FROM bps_registry.measure_registry WHERE NOT queryable
UNION ALL SELECT 'blocked_quality_datasets', count(*) FROM bps_registry.dataset_registry WHERE answerability='blocked_quality'
UNION ALL SELECT 'partial_review_datasets', count(*) FROM bps_registry.dataset_registry WHERE 'partial_measure_review_required' = ANY(quality_flags);
```

### 📌 Cara membaca hasilnya

**Jumlah measure non-queryable AKAN NAIK. Itu perbaikannya bekerja, bukan regresi.**

Measure-measure itu sebelumnya bisa dijawab hanya berbekal tebakan satuan dari
judul tabel, atau tanpa satuan sama sekali. Angka kenaikan itu adalah jawaban
atas pertanyaan yang selama ini tidak ada yang tahu: **berapa banyak angka yang
selama ini bisa keluar dengan satuan yang tidak dipastikan.**

Kirim angka sebelum/sesudahnya ke gw — dari situ bisa disusun paket review
`docs/26` untuk data owner.

---

## Langkah 4 — Query prototypes

```bash
uv run python scripts/validate_bps_query_prototypes.py
```

**Harus:** 13/13 PASS.

**Kalau gagal:** kemungkinan besar karena template berubah di langkah 3
(`primary_dimension_item` dihapus, `category_item` menggantikan
`secondary_dimension_item`, `simdasi_point` sekarang pakai `indicator_code`).
Prototype yang masih memakai nama parameter lama harus disesuaikan. Kirim
outputnya.

---

## Langkah 5 — Probe kapabilitas model (OQ-05)

```bash
export PROBE_BASE_URL="<endpoint OpenAI-compatible>"
export PROBE_API_KEY="<key yang sudah di-rotate>"

python3 scripts/probe_model_capabilities.py --model gemini-3.1-flash
python3 scripts/probe_model_capabilities.py --model deepseek-v4-flash
```

Cari base URL compat-nya di dokumentasi resmi provider. Kalau probe gagal dengan
404/401, kemungkinan URL-nya, bukan kapabilitas modelnya.

### Tiga hasil yang mengubah desain

| Hasil | Artinya |
|---|---|
| `structured_output.supported_mode` | `json_schema` = terbaik. `none` = model tidak layak dipakai |
| `prefill.supported` | `false` = **hapus prefill dari desain**, jangan diandalkan |
| `tool_calling.supported` | `false` = **Lapis 0 harus dirancang ulang**, desain berbeda dan lebih lemah |

Plus `hallucination_pressure.refused_cleanly` — kalau `false`, model menyebut
angka tanpa tool. Gate tetap memblokir, tapi abstention rate akan tinggi dan
MARAWA terasa tidak membantu.

Kirim isi `data/reports/model-capability-probe.json` — aman, tidak ada kredensial
di dalamnya.

---

## Langkah 6 — Jalankan server (opsional, aman)

```bash
pip install fastapi uvicorn
uvicorn scripts.app:app --reload
# buka http://localhost:8000/docs
```

In-memory, tidak menyentuh DB. Berguna untuk melihat bentuk API dashboard
sebelum ada UI-nya. Semua endpoint butuh header `X-Admin-Id: seed-super-1`.

---

## Yang sama sekali belum ada

Bukan "belum dijalankan" — memang belum dibangun:

- **Baileys / koneksi WhatsApp.** Tidak ada kode sama sekali. `scripts/app.py`
  punya endpoint webhook, tapi tidak ada yang memanggilnya.
- **PostgreSQL untuk `Store`.** `scripts/app.py` pakai dict in-memory; hilang
  saat restart, dan tidak bisa dipakai lebih dari satu proses.
- **UI dashboard.** Hanya API, belum ada halaman web.
- **Verifikasi HMAC webhook** aktif hanya kalau `store.webhook_secret` diisi.
  Belum ada mekanisme mengisinya dari env.
- **TOTP / sesi asli.** Auth sekarang hanya header `X-Admin-Id` — placeholder,
  bukan autentikasi.

---

## Yang gw butuh balik dari lo

Prioritas, dari yang paling berguna:

1. **Output langkah 3** (angka sebelum/sesudah rebuild) — ini menentukan isi
   paket review unit untuk data owner
2. **Isi `model-capability-probe.json`** — menentukan apakah desain runtime
   berdiri apa adanya
3. **Error apa pun** dari langkah 1, 2, atau 4 — tempel apa adanya, termasuk
   traceback

Kalau langkah 1 atau 3 gagal, jangan dipaksa. Kirim errornya dulu.
