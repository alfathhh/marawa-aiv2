# Audit Independen — MARAWA AI

**Tanggal:** 15 Agustus 2026
**Basis audit:** `marawa-docs-full.zip` (83 file: 36 markdown, 21 script Python, 10 migrasi SQL, 12 report, 2 kontrak JSON)
**Sifat:** review eksternal, tidak ada akses ke database live

---

## 0. Batas audit

Yang **bisa** diverifikasi: isi dokumen, kode script, SQL migrasi, file kontrak, dan file report.

Yang **tidak bisa** diverifikasi: semua klaim tentang state PostgreSQL (1.148 dataset, 0 duplicate key, 918 quarterly facts, dst). Alasannya bukan cuma "tidak ada DB" — bundle ini memang tidak self-contained:

- `tests/` tidak ada → klaim "150 PASS" tidak dapat direproduksi dari bundle ini
- `workers/ingestion/` tidak ada → 8 script gagal import (`from workers.ingestion.bps_storage import ...`)
- `.env.example` tidak ada → `scripts/validate_docs.py` justru **gagal** kalau dijalankan di bundle ini (file itu ada di daftar `REQUIRED`)

Jadi angka-angka DB di bawah diperlakukan sebagai **klaim yang dilaporkan**, bukan fakta terverifikasi. Ini penting untuk temuan C1.

---

## 1. Jawaban langsung: "ini mau dibawa ke mana?"

Kebingunganmu itu akurat, dan penyebabnya bukan karena kamu kehilangan konteks. Penyebabnya struktural:

**Proyek ini sudah membangun sistem verifikasi yang menilai dirinya sendiri, lalu memakai hasil penilaian itu sebagai bukti kemajuan.**

Semua angka hijau di ringkasan eksekutif — 13/13 PASS, Recall@3 1.000, 19/19 PASS, 150 PASS, validation PASS — dihasilkan dari fixture yang ditulis oleh penulis sistem yang sama, dinilai terhadap ekspektasi yang ditulis penulis yang sama. Tidak satu pun menyentuh pengguna nyata, LLM nyata, atau pertanyaan PST nyata. Rincian buktinya di §2.

Efeknya: dashboard status penuh hijau, tapi tidak ada satu pun sinyal yang datang dari luar sistem. Itu sebabnya "selesai" terasa tidak seperti selesai — karena secara epistemik memang belum ada yang selesai divalidasi, yang selesai adalah **konsistensi internal**.

**Rekomendasi arah:** hentikan penambahan cakupan, potong scope MVP jadi sekitar seperempatnya, dan ganti satu metrik sintetis dengan satu sinyal eksternal (30 pertanyaan asli dari PST). Rute konkret di §6.

---

## 2. Temuan kritis

### C1 — Seluruh stack verifikasi adalah loop tertutup

Ini temuan utama; sisanya turunan.

**C1a. Golden episode harness tidak menguji perilaku apa pun untuk 8 dari 19 episode.**

`scripts/eval_golden_episodes.py` cuma mengeksekusi engine sungguhan (`offer_candidates`) pada **turn pertama**, dan hanya kalau `action ∈ {offer_candidates, offer_candidate_clusters}` **dan** `new_goal is True`. Untuk semua turn lain yang dijalankan hanyalah `_validation_errors()` — dan fungsi itu memeriksa **dict ekspektasi itu sendiri**, bukan sistem:

```python
if action in QUERY_ACTIONS:
    if turn_expect.get("query_facts") is not True:
        errors.append("fact query must set query_facts=true")
```

Ini membaca file JSON, lalu mengecek bahwa file JSON itu menuliskan `query_facts: true`. Itu linting fixture, bukan evaluasi.

Konsekuensinya: episode `bps-dialog-012` s/d `019` — **seluruh policy percakapan yang baru disetujui di `docs/27`** (menu agent-first, handover SLA 180 detik, busy notice, natural cancel, idle timeout 300 detik) — lolos tanpa ada satu baris pun kode yang mengimplementasikannya. Mereka PASS karena string nama action-nya ada di dalam set yang di-hardcode di baris 44–52. Tidak ada state machine yang diuji karena tidak ada state machine.

Ada juga dua cabang yang terlihat seperti pengecekan tapi no-op:

```python
if expect.get("requires_user_selection") is True:
    pass   # "enforced by design"
...
    pass   # allow clarify; fact queries gated by selection_source check
```

Klaim jujurnya bukan "golden episode harness 19/19 PASS", tapi: *"11 episode diuji tipis pada turn pertama terhadap offering engine; 8 episode belum diuji sama sekali."*

**C1b. Skor retrieval di-tuning ke set evaluasinya sendiri.**

`scripts/simulate_bps_candidate_scoring.py` baris 46–48:

```python
QUERY_ALIASES = {
    "pendudk": ("penduduk",),
    "pendudduk": ("penduduk",),
    ...
}
```

`"pendudk"` muncul persis satu kali di seluruh proyek selain di sini: di baris 294, sebagai utterance evaluasi `"pendudk lubuk alung terbaru"`.

Jadi "toleransi typo" bukan mekanisme (tidak ada edit distance, tidak ada trigram, tidak ada `pg_trgm`) — melainkan lookup untuk typo yang persis ada di soal ujian. Typo yang tidak pernah ditulis penulis akan langsung miss. Catatan: `DOC_ALIASES["pengagguran"]` beda kasus dan sah, karena typo itu ada di judul sumber BPS aslinya.

Doc `25` mencatat sendiri prosesnya: *"reclaim 1.0/0.9389 setelah: pdrb context marker, banyak perbaikan kalibrasi"*. Itu deskripsi tuning sampai metrik naik — di set yang sama. **Recall@3 = 1.000 di sini tidak memprediksi apa pun tentang performa di pertanyaan warga.** Angka jujurnya: unknown.

**C1c. 60 utterance evaluasi ditulis sendiri, termasuk kunci jawabannya.**

`GOLDEN` di baris 291 adalah list literal Python. Penulis menentukan pertanyaannya, menentukan dataset mana yang "benar" (`ids: ["29"]`), lalu menulis scorer, lalu mengukur kecocokan scorer dengan tebakannya sendiri.

Sementara itu `docs/15` §"Suggested next workshop" masih menuliskan agenda *"25m: top 30 pertanyaan dan sumber"* sebagai **belum dilakukan**. Artinya: daftar pertanyaan nyata belum pernah dikumpulkan, tapi sistem retrieval sudah dioptimasi sampai 1.000 terhadap penggantinya.

**C1d. `documentation validation: PASS` adalah pengecekan keberadaan keyword.**

`scripts/validate_docs.py` memeriksa: file ada, link resolve, code fence balance, dan apakah string seperti `"Effect-ASR"`, `"Agents Rule of Two"`, `"analysis sandbox"` **muncul di suatu tempat**. Validator ini secara desain tidak bisa mendeteksi kontradiksi antar-dokumen atau angka yang melenceng — dan memang tidak mendeteksinya (lihat M6).

---

### C2 — Invariant "unit tidak ditebak" dilanggar oleh kodemu sendiri, di 3 tempat

Ini prinsip yang paling sering diulang di seluruh pack (README, `docs/27` §1.6, PRD FR-H). Implementasinya menebak.

**C2a. Unit mata uang dari string judul tabel.** `migrations/001_serving_view_fixes.sql`:

```sql
CASE WHEN t.title ILIKE '%miliar rupiah%' THEN 'miliar rupiah'
     WHEN t.title ILIKE '%juta rupiah%'   THEN 'juta rupiah'
```

Ini menerapkan unit **level tabel** ke data **level kolom**. Untuk tabel PDRB yang judulnya menyebut satu satuan tapi kolomnya campur (mis. kolom nilai dalam miliar + kolom distribusi persen + kolom laju pertumbuhan), semua kolom mewarisi `'miliar rupiah'`. Migrasi ini "memperbaiki" 432 baris, tapi mekanismenya bisa memproduksi kesalahan baru tanpa suara. `unit_source='title_matched'` tercatat — bagus — tapi tidak ada gate yang memperlakukannya sebagai perlu-review.

**C2b. Kata "menurut" dianggap bukti bahwa measure tidak bersatuan.** `scripts/build_bps_registry.py:212`:

```python
if COUNT_TITLE_RE.match(title or "") or "menurut" in lowered:
    return "unitless"
```

"Menurut" adalah kata paling umum di judul tabel BPS dan artinya *breakdown by* — sama sekali bukan sinyal bahwa angkanya tanpa satuan. "Rata-rata Lama Sekolah Menurut Kecamatan" (satuan: tahun) atau "Luas Panen Padi Menurut Kecamatan" (satuan: hektar) dengan field unit kosong akan diklasifikasikan **`unitless`**, bukan `unknown_review` — sehingga lolos gate review sepenuhnya.

**C2c. Gate `blocked_quality` bekerja di level dataset, celahnya di level measure.** `build_bps_registry.py:930`:

```python
if dataset["answerability"] == "answerable" and states and states <= {"unknown_review"}:
```

Dataset di-block hanya kalau **seluruh** measure-nya `unknown_review`. Dataset dengan 1 measure ber-unit jelas + 6 measure `unknown_review` tetap `answerable`, dan keenam measure tanpa unit itu bisa di-query dan dijawab.

Ketiganya punya perbaikan yang murah dan tesnya jelas — lihat §7.

---

### C3 — Yang menghambat bukan "3 keputusan eksternal", tapi ukuran scope

Ringkasan eksekutif menyatakan tinggal 3 blocker eksternal. Membaca `docs/01-PRD` §11 dan `docs/14`, itu tidak cocok dengan isinya.

Isi "MVP" menurut PRD §11: WhatsApp text + RAG + curated structured query + multi-turn working memory + multi-step agent loop + analisis statistik + chart + export CSV/XLSX + Gemini primary & DeepSeek fallback + dashboard (auth, RBAC, TOTP, inbox, takeover, knowledge upload, analytics) + signed webhook + audit + metrics + backup.

Itu bukan MVP. Itu produk penuh — realistis 4–6 bulan untuk tim, bukan untuk satu orang.

Angka pendukung: **49.495 kata dokumen perencanaan** (±150 halaman) untuk sistem dengan **0 baris kode runtime**. Rasionya sekitar 10 kata rencana per baris SQL yang benar-benar dieksekusi.

Tabel sign-off PRD §14: **kelima approver TBD/Pending.** Belum ada satu pun pihak yang menyetujui apa pun, tapi spesifikasinya sudah 150 halaman. Ini urutan yang terbalik, dan ini penjelasan paling langsung kenapa kamu tidak tahu mau dibawa ke mana: tidak ada seorang pun di luar proyek yang pernah bilang "ya, ini yang kami mau."

Contoh konkret bahwa scope-nya lepas dari ancaman nyata: `09A/09B/09C` = **7.674 kata** subsistem anti-jailbreak (CaMeL control/data-flow separation, taint inheritance, capability broker, Rule-of-Two gate, adaptive red-team corpus, Effect-ASR dengan confidence bound). Blast radius aktualnya saat ini: bot read-only yang menyajikan data **yang sudah publik di webapi.bps.go.id**, dengan data internal masih terblokir di OQ-07. `docs/14` Fase 9.1 menjadikan suite ini **release gate produksi** — itu sendiri proyek berbulan-bulan, memblokir peluncuran, untuk melindungi aset yang saat ini bernilai nol karena belum tersambung.

Risiko nyata nomor satu untuk MARAWA bukan jailbreak. Risiko nomor satu adalah **angka yang salah keluar membawa nama BPS** — dan itu justru risiko yang dilemahkan oleh C2.

---

## 3. Temuan teknis (High)

**H1 — Binder gagal menerapkan row limit; satu template tanpa batas sama sekali.**
`scripts/bps_template_binder.py:88`:

```python
final_sql = f"{sql.rstrip().rstrip(';')}\nLIMIT %(row_limit)s" if "LIMIT" not in sql else sql
```

Substring check, case-sensitive. Dua arah rusak:

1. Template `publication_list` sudah mengandung `LIMIT %(page_size)s OFFSET %(offset)s` → kondisi `"LIMIT" not in sql` **False** → `row_limit` server-side tidak pernah ditempel. Yang membatasi baris adalah `page_size`, yang **datang dari caller** dan hanya divalidasi bertipe integer. `page_size = 50_000_000` lolos. Klaim `docs/25` "row_limit server-side" tidak berlaku untuk template ini.
2. SQL yang memakai `limit` huruf kecil, atau punya kolom/alias/komentar berisi kata "LIMIT", akan salah klasifikasi ke arah sebaliknya → `LIMIT` ganda → syntax error.

Perbaikan: hapus heuristik string. Tambahkan field eksplisit di template (`has_own_limit: bool`), atau bungkus: `SELECT * FROM (<sql>) t LIMIT %(row_limit)s`, dan cap `page_size` server-side.

**H2 — `ILIKE` tanpa escape wildcard.** Template `publication_list`:

```sql
AND (%(search)s::text IS NULL OR title ILIKE '%%' || %(search)s || '%%')
```

Bukan SQL injection (parameterized, aman). Tapi `%` dan `_` dari input user diperlakukan sebagai wildcard: search `"%"` mencocokkan 602 publikasi dan dengan H1 bisa ditarik semua. Escape input atau pakai `position()` / `pg_trgm`.

**H3 — Enforcement read-only bisa hilang diam-diam saat rebuild view.**
`docs/25` sudah mencatat footgun-nya: *"`ensure_schema` menghapus grant level-objek; re-apply `004` setelah bootstrap view."* Itu benar, dan itu artinya jaminan keamanan utama bergantung pada seseorang mengingat langkah manual. `DROP VIEW` + `CREATE VIEW` (yang persis dilakukan `001`) menghapus grant; `CREATE OR REPLACE` tidak. Tidak ada tes yang menangkap drift ini.

Perbaikan (30 menit): tes assertion privilege yang wajib jalan setelah migrasi apa pun —

```sql
SELECT relname, has_table_privilege('marawa_runtime_ro', oid, 'SELECT')
FROM pg_class WHERE relname IN ('bps_serving_dynamic','bps_serving_simdasi',
                                 'bps_serving_census','bps_publications');
```

Plus assertion negatif: role tidak punya SELECT ke tabel raw/fact mana pun.

**H4 — Tidak ada template untuk "periode terbaru".**
Enam template semuanya menuntut `period` eksak (`dynamic_point`, `dynamic_quarterly`) atau rentang eksplisit (`dynamic_trend`). Padahal "terbaru" adalah bentuk pertanyaan warga yang paling umum — dan muncul di set evaluasimu sendiri (`"pendudk lubuk alung terbaru"`). Runtime harus melakukan query metadata dulu untuk menemukan periode terakhir, dan tidak ada template untuk itu. Ini gap fungsional, bukan sekadar kenyamanan.

---

## 4. Temuan teknis (Medium)

**M1 — `primary_dimension` adalah geography yang diberi nama ulang.** Di `bps_serving_dynamic`, `primary_dimension_id = vertical_id = geography_code` — kolom yang sama diekspos tiga kali dengan tiga nama. Akibatnya `dynamic_point` punya dua parameter (`geography_code`, `primary_dimension_item`) yang memfilter kolom identik. Untuk agent yang harus memilih parameter, ini jebakan yang mengundang query salah. Dimensi kategori yang sesungguhnya adalah `secondary_dimension` (`derived_variable`). Sarannya: buang `primary_dimension_*` dari serving view, atau ganti nama supaya jujur.

**M2 — `simdasi_point` mencocokkan `indicator_name` sebagai teks eksak.** Mencocokkan label manusia string-eksak itu rapuh terhadap spasi, kapitalisasi, dan perubahan label di sisi BPS. Sudah ada `indicator_code` di view — pakai itu sebagai parameter, `indicator_name` untuk tampilan saja.

**M3 — `_aggregation()` default ke `"count"` saat unit tidak diketahui** (`build_bps_registry.py:225`). Mengklasifikasikan measure yang tidak diketahui ke kelas yang berimplikasi bisa dijumlahkan. Default yang aman untuk unit tidak diketahui adalah `"unknown"` yang **memblokir** agregasi, bukan mengizinkannya.

**M4 — Report 19 MB masuk repo.** `bps-candidate-offering-simulation.json` = 19.711.088 byte; 60 case × ±276 KB, karena tiap case menyimpan seluruh daftar kandidat. Simpan metrik + hash, bukan dump penuh; atau taruh di luar repo. Ini juga berarti "pagination ref stabil" diverifikasi terhadap dump yang tidak pernah ada manusia baca.

**M5 — Kontradiksi kebijakan cron.** README/`docs/20`/`docs/25` mengunci: **0 cronjob BPS, update manual saja.** PRD §11 "V1" tetap menuliskan: *"BPS WebAPI scheduled sync domain `1306`"*. Salah satu harus dicabut.

**M6 — Angka melenceng antar-dokumen** (persis yang tidak bisa dilihat oleh validator C1d):

| Klaim | Nilai yang beredar |
|---|---|
| Jumlah test | `74` (`bps-workflow-state.json`) / `126` (`docs/14` baris 20) / `150` (`docs/25` baris 19 & 174) |
| Golden episodes | `11 episode / 28 turn` (`docs/25` baris 58) vs `19 episode / 47 turn` (README baris 114) |
| Turn sebenarnya di fixture | **46** — bukan 47 (hitung langsung dari `bps-agent-query-episodes.json`) |

Kalau angka-angka status sendiri tidak bisa dipercaya, statusnya memang tidak diketahui.

**M7 — Basis internal untuk FR-C tidak ada.** PRD FR-C: *"Sumber: allowlisted views PostgreSQL internal."* OQ-07: internal views = none, belum di-approve. Jadi seluruh premis "structured internal data" masih nol, sementara dokumen memperlakukannya sebagai fondasi.

---

## 5. Yang memang bagus (dan patut dipertahankan)

Ini bukan basa-basi; kualitas lapisan database di sini di atas rata-rata dan sayang kalau ikut terbuang saat memangkas scope.

- **Disiplin lineage.** `row_label_raw`, `label_raw` + `normalization_rule`, `unit_source`, `snapshot_id` di setiap baris serving. Kamu bisa selalu menelusuri angka kembali ke response API mentah. Ini justru bagian yang paling sering dilewat orang, dan kamu tidak melewatinya.
- **Migrasi 003.** Konversi ke PK komposit `(registry_version_id, ...)` supaya snapshot versi lama tetap queryable, ditulis dengan urutan drop-constraint → backfill → set NOT NULL → re-add yang benar. Ini kerja migrasi yang matang.
- **Ledger + `.down.sql` untuk 001–005.** Reversibility yang jarang dibuat orang di tahap ini.
- **Role `marawa_runtime_ro`.** `default_transaction_read_only`, `statement_timeout=5s`, `lock_timeout=1s`, `idle_in_transaction_session_timeout=10s`, grant allowlist per objek. Rancangan yang benar (isunya di H3 adalah durabilitas grant-nya, bukan desainnya).
- **Sentinel jujur soal keterbatasannya.** `bps-update-sentinel-latest.json` mencantumkan sendiri bahwa Dynamic tidak punya timestamp update sehingga revisi diam-diam tidak terdeteksi. Menuliskan yang tidak bisa dideteksi itu tanda kematangan.
- **`09A` §1.1 "Batas klaim keamanan"** — pemisahan hard invariant vs empirical robustness vs residual risk, dengan larangan eksplisit mengklaim "100% aman". Penalarannya bagus. Masalahnya timing dan proporsi, bukan mutu.

Pola yang muncul: **kualitas eksekusi tinggi, pemilihan target lemah.** Yang perlu diperbaiki adalah apa yang dikerjakan, bukan bagaimana mengerjakannya.

---

## 6. Rekomendasi arah

### 6.1 Hentikan sekarang

1. **Bekukan penulisan dokumen.** 50.000 kata sudah melewati titik di mana dokumen tambahan menambah kejelasan. Jangan buat `docs/28`.
2. **Berhenti melaporkan metrik loop-tertutup sebagai progres.** Recall@3 1.000, 19/19 PASS, 150 PASS — hapus dari ringkasan status sampai ada input eksternal. Angka-angka itu aktif menyesatkan pengambilan keputusanmu sendiri.
3. **Bekukan anti-jailbreak suite sebagai release gate.** Simpan dokumennya. Cabut `docs/14` Fase 9.1 dari jalur kritis sampai ada data internal yang benar-benar tersambung. Pertahankan yang murah dan sudah efektif: no raw SQL, parameterized templates, read-only role, output tidak pernah memuat system prompt.
4. **Berhenti membangun database lebih jauh.** Census item registry dan label normalization sudah selesai. Selanjutnya hanya perbaikan C2 di §7.

### 6.2 Satu hal yang mengubah segalanya

**Kumpulkan 30 pertanyaan nyata dari PST.** Bukan workshop 90 menit — cukup minta petugas front desk mencatat pertanyaan yang masuk selama 3 hari, apa adanya, termasuk typo dan yang ambigu.

Ini akan:

- mengganti `GOLDEN` sintetis dengan set evaluasi jujur (langsung menjawab C1b/C1c)
- menunjukkan berapa persen pertanyaan yang benar-benar bisa dijawab dari mirror sekarang — angka yang saat ini tidak diketahui siapa pun
- memberi tahu apakah "menu 5 layanan" cocok dengan cara orang benar-benar bertanya
- memberikan sesuatu yang konkret untuk ditunjukkan ke approver yang masih TBD

Prediksiku (tulis dulu, lalu cek): Recall@3 terhadap 30 pertanyaan asli akan turun ke kisaran **0,6–0,8**, dan sebagian besar kegagalan bukan soal ranking melainkan pertanyaan yang menuntut "terbaru" (H4) atau indikator yang tidak ada di mirror sama sekali. Kalau tebakanku salah, itu informasi bagus. Kalau benar, kamu tahu persis apa yang harus dikerjakan berikutnya.

### 6.3 Slice pertama yang layak dibangun (2–3 minggu, bukan 6 bulan)

Bangun ini, **hanya** ini:

```
WhatsApp (Baileys) → FastAPI → offering engine → user pilih tabel
                            → binder → 6 template → formatter Indonesia
                            → jawaban + sumber + periode + unit
```

Yang **masuk**: text saja, discovery + selection + query, format jawaban `docs/18`, idle timeout, "tidak bisa jawab → ini nomor admin".

Yang **keluar** (tunda semua): RAG, pgvector, analisis statistik, chart, XLSX/PDF, dashboard, RBAC, TOTP, knowledge management, analytics, model fallback, working memory multi-turn kompleks, live handover dari dashboard.

Handover versi pertama cukup: bot mengirim nomor WhatsApp petugas dan berhenti. Tanpa antrean, tanpa claim, tanpa SLA 180 detik, tanpa race condition. `docs/27` §5 mendeskripsikan state machine dengan tiga skenario race — itu mahal untuk dibangun dan diuji, dan sebelum ada pengguna kamu belum tahu apakah antreannya perlu.

Kenapa slice ini: ia melewati semua blocker eksternal kecuali satu. OQ-07 (internal views) tidak dibutuhkan — semua data sudah ada di mirror publik. OQ-06 (embedding) tidak dibutuhkan — tidak ada RAG. OQ-04 (roster RBAC) tidak dibutuhkan — tidak ada dashboard. Yang tersisa benar-benar cuma **OQ-02 (nomor WhatsApp)** dan **OQ-05 (model)**, dan OQ-05 bisa dites hari ini dengan API key pribadi berkuota kecil untuk membuktikan kelayakannya sebelum minta procurement.

### 6.4 Urutan yang disarankan

| # | Aksi | Estimasi | Membuka |
|---|---|---|---|
| 1 | Perbaiki C2a/C2b/C2c + tes unit (§7) | 1 hari | integritas angka — prasyarat semuanya |
| 2 | Perbaiki H1, H2, H3 | 1 hari | binder benar-benar aman seperti yang diklaim |
| 3 | Kumpulkan 30 pertanyaan PST nyata | 3 hari (paralel) | evaluasi jujur pertama |
| 4 | Jalankan scorer terhadap 30 pertanyaan itu, laporkan apa adanya | 0,5 hari | angka pertama yang berarti |
| 5 | Tambah template `latest_period` (H4) | 0,5 hari | bentuk pertanyaan paling umum |
| 6 | Bangun slice §6.3 | 2 minggu | produk yang berjalan |
| 7 | Uji ke 5 pegawai BPS, bukan publik | 1 minggu | sinyal eksternal kedua |

Semua yang bukan prasyarat langkah berikutnya: tunda.

---

## 7. Perbaikan konkret untuk C2 (bisa dikerjakan besok)

**Cek dulu seberapa besar masalahnya:**

```sql
-- Berapa measure "unitless" yang sebenarnya cuma menang karena kata "menurut"?
SELECT count(*) FROM bps_registry.measure_registry m
JOIN bps_registry.dataset_registry d USING (registry_version_id, dataset_id)
WHERE m.unit_state = 'unitless' AND d.title ILIKE '%menurut%'
  AND d.title !~* '^(jumlah|banyaknya)';

-- Berapa dataset "answerable" yang menyembunyikan measure tanpa unit? (C2c)
SELECT count(DISTINCT d.dataset_id) FROM bps_registry.dataset_registry d
JOIN bps_registry.measure_registry m USING (registry_version_id, dataset_id)
WHERE d.answerability = 'answerable' AND m.unit_state = 'unknown_review';

-- Berapa baris yang unit-nya berasal dari tebakan judul? (C2a)
SELECT count(*), count(DISTINCT table_code) FROM bps_serving_simdasi
WHERE unit_source = 'title_matched';
```

**Perbaikannya:**

1. `_unit_state()` — buang cabang `"menurut" in lowered`. Kata itu menandai dimensi breakdown, bukan ketiadaan satuan. Pertahankan `COUNT_TITLE_RE` (judul "Jumlah…"/"Banyaknya…" memang measure hitungan).
2. Gate C2c pindah ke level measure: measure `unknown_review` tidak boleh masuk hasil query walau dataset-nya `answerable`. Paling sederhana: tandai measure-nya, dan biarkan binder menolak.
3. Perlakukan `unit_source='title_matched'` sebagai `unit_state='review'`, bukan otoritatif — masuk ke paket review `docs/26` bersama 13 dataset yang sudah ada.
4. `_aggregation()` — default `"unknown"` untuk unit yang tidak diketahui, dan `"unknown"` memblokir penjumlahan.
5. Tambahkan satu tes yang gagal duluan: *"tidak ada measure yang bisa di-query yang unit-nya tidak diketahui atau ditebak."* Ini menjadikan invariant yang paling sering kamu tulis akhirnya benar-benar ditegakkan mesin, bukan cuma tertulis di dokumen.

---

## 8. Ringkasan satu paragraf

Lapisan database MARAWA dibangun dengan baik dan layak dipertahankan; disiplin lineage, migrasi versioned, dan role read-only-nya di atas rata-rata. Yang rusak adalah lapisan verifikasi: setiap metrik hijau di ringkasan status dihasilkan oleh fixture yang menilai dirinya sendiri, sehingga proyek ini kehilangan kemampuan mengukur kemajuannya sendiri — dan itulah, bukan kelelahan, penyebab kamu tidak lagi tahu ini mau dibawa ke mana. Dua invariant yang paling sering ditegaskan ("unit tidak ditebak", "row limit server-side") saat ini dilanggar oleh kode sendiri di empat lokasi yang bisa diperbaiki dalam dua hari. Rekomendasi: bekukan dokumen dan anti-jailbreak gate, perbaiki keempat lokasi itu, kumpulkan 30 pertanyaan PST nyata sebagai sinyal eksternal pertama, lalu bangun satu slice sempit (WhatsApp → discovery → query → jawaban bersumber) yang secara sengaja melewati semua blocker kecuali nomor WhatsApp dan API key.
