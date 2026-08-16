# Laporan Kerja — Postgres Store & Verifikasi SQL Nyata

**Tanggal:** 16 Agustus 2026
**Lingkup:** lanjutan setelah verifikasi bundle `marawa-docs-audit-20260816`
**Hasil:** 283 tes lulus (naik dari 272), 1 bug baru ditemukan & diperbaiki, migrasi 007 baru
**Bundle:** `marawa-postgres-store-20260816.zip`

---

## 0. Ringkasan satu paragraf

Berhasil memasang PostgreSQL 16.14 di lingkungan audit, sehingga untuk pertama
kalinya SQL proyek ini dieksekusi di luar server produksi. Seluruh rantai
migrasi 002→006 dijalankan dari skema kosong tanpa satu error pun, dan CHECK
constraint ATURAN KERAS terbukti benar-benar menolak data yang melanggarnya —
bukan lagi klaim. Di atas fondasi itu dibangun `migrations/007` (tabel runtime
percakapan) dan `scripts/postgres_store.py` (pengganti store in-memory dengan
compare-and-swap sungguhan), diuji 11 tes lawan database nyata. Satu bug
struktural ditemukan saat menulisnya.

---

## 1. Terobosan: PostgreSQL nyata di lingkungan audit

### Yang dilakukan

```bash
apt-get update && apt-get install -y postgresql     # 16.14
initdb -D /home/claude/pgdata -U postgres --auth=trust -E UTF8
pg_ctl -D /home/claude/pgdata -o "-p 5433 -k /home/claude/pgrun" start
createdb marawa_test
CREATE ROLE marawa_ingest;  CREATE ROLE marawa_runtime_ro;
```

Percobaan pertama gagal: `initdb` dan `pg_ctl` menolak dijalankan sebagai root.
Diselesaikan dengan menjalankan keduanya sebagai user `postgres`
(`su postgres -c ...`) dan memberi kepemilikan direktori data ke user tersebut.

### Kenapa ini penting

Sebelum ini, seluruh SQL proyek — migrasi 001–006, view serving, CHECK
constraint — berstatus **klaim yang belum pernah dieksekusi** di luar server
produksi. Laporan verifikasi sebelumnya bahkan mencantumkan peringatan bahwa
migrasi 006 berpotensi gagal karena urutan `ADD CONSTRAINT` sebelum `UPDATE`
backfill. Sekarang hal itu bisa dibuktikan, bukan diprediksi.

---

## 2. Verifikasi rantai migrasi 002 → 006

Bootstrap skema dasar diambil dari konstanta `SCHEMA_SQL` di
`workers/ingestion/bps_storage.py` (472 baris DDL), menghasilkan **24 tabel dan
3 view**. Lalu migrasi dijalankan berurutan:

```
=== 002_registry_schema ===                       (tanpa error)
=== 003_database_hardening ===                    (tanpa error)
=== 004_runtime_readonly_role ===                 (tanpa error)
=== 005_display_label_normalization ===           (tanpa error)
=== 006_unit_provenance_and_serving_clarity ===   (tanpa error)
```

**Seluruh rantai bersih.** Bug urutan backfill yang dikhawatirkan di laporan
verifikasi sebelumnya sudah diperbaiki di bundle 16 Agustus (backfill kini
dijalankan sebelum `ADD CONSTRAINT`), dan perbaikan itu terbukti bekerja.

### Hasil struktural yang diverifikasi lewat `information_schema`

| Temuan audit | Verifikasi di DB nyata |
|---|---|
| **M1** — kolom `primary_dimension_*` duplikat geography harus hilang | ✅ Kolom `bps_serving_dynamic` kini: `domain, indicator_code, indicator_name, geography_code, geography_name, category_code, category_label, period, period_granularity, subperiod_code, subperiod_label, value, value_text, unit_raw, unit, unit_state, snapshot_id` — tidak ada `primary_dimension_*` |
| **C2a** — `unit_state` harus ada di serving SIMDASI | ✅ Kolom ada |
| **C2c** — gate level measure | ✅ `queryable`, `quality_flags`, `unit_source` ada di `measure_registry` |
| Constraint terpasang | ✅ `measure_registry_queryable_requires_unit`, `measure_registry_unit_state_check`, `measure_registry_aggregation_semantics_check`, `measure_registry_value_type_check` |

---

## 3. ATURAN KERAS diuji langsung ke database

Ini bagian terpenting. Selama ini invariant "unit tidak ditebak" ditegakkan
Python dan ditulis di dokumen. Sekarang diuji ke penegak terakhirnya:

```sql
-- 1. measure queryable dengan unit hasil TEBAKAN judul tabel
INSERT INTO bps_registry.measure_registry (... unit_state='known',
    unit_source='title_matched', queryable=true ...);
```
```
ERROR: new row for relation "measure_registry" violates check constraint
       "measure_registry_queryable_requires_unit"
```

```sql
-- 2. measure queryable dengan unit TIDAK DIKETAHUI
INSERT INTO bps_registry.measure_registry (... unit_state='unknown_review',
    queryable=true ...);
```
```
ERROR: new row for relation "measure_registry" violates check constraint
       "measure_registry_queryable_requires_unit"
```

**Kedua percobaan ditolak PostgreSQL.** Artinya: apa pun yang dilakukan kode
aplikasi di atasnya — builder baru, script ad-hoc, query manual — database
menolak menyimpan measure yang bisa dijawab dengan satuan yang ditebak.

Ini menutup lingkaran penegakan ATURAN KERAS #1:

```
Lapis prompt      → model diarahkan tidak mengarang       (paling lemah)
Lapis answer_gate → angka wajib tertelusur ke evidence
Lapis formatter   → menolak mencetak unit tak pasti
Lapis DATABASE    → menolak MENYIMPAN measure unit tebakan (paling kuat) ← BARU
```

---

## 4. `migrations/007_runtime_conversation_tables.sql` (baru, 149 baris)

Tabel runtime untuk menggantikan penyimpanan in-memory.

### Tabel yang dibuat

`marawa_conversations`, `marawa_messages`, `marawa_outbox`, `marawa_admins`,
`marawa_settings`, `marawa_audit_log`

### Keputusan desain yang layak dicatat

**Dedup pesan masuk lewat partial unique index.**
```sql
CREATE UNIQUE INDEX uq_marawa_messages_wa_id
    ON marawa_messages (wa_message_id) WHERE wa_message_id IS NOT NULL;
```
Bridge WhatsApp mengirim ulang pesan. Partial index dipakai agar notice sistem
internal (yang tidak punya `wa_message_id`) tidak ikut terkena.

**Idempotency outbox berbasis kunci klien, bukan isi pesan.**
```sql
CREATE UNIQUE INDEX uq_marawa_outbox_idempotency
    ON marawa_outbox (idempotency_key) WHERE idempotency_key IS NOT NULL;
```
Sesuai audit H: keying pada teks pesan membuat petugas yang mengetik "ok" dua
kali kehilangan pesan keduanya.

**Trigger penjaga superadmin terakhir.**
```sql
CREATE TRIGGER trg_marawa_guard_last_superadmin
    BEFORE UPDATE OR DELETE ON marawa_admins ...
```
Menolak menghapus atau menonaktifkan superadmin aktif terakhir. Break-glass di
`docs/06` §3.1 sebelumnya hanya berupa catatan kebijakan ("minimal 2 akun
superadmin") — sekarang ditegakkan database. Tanpa ini, kantor bisa terkunci
total dari pairing WhatsApp dan manajemen user.

**Index parsial untuk sweep.**
```sql
CREATE INDEX idx_marawa_conversations_state ON marawa_conversations (state)
    WHERE state IN ('QUEUED','ADMIN_ACTIVE');
```

### Reversibility diuji

```
007 UP    : OK
007 DOWN  : OK
007 UP    : OK   (dijalankan ulang setelah down — bersih)
```

---

## 5. `scripts/postgres_store.py` (baru, 339 baris)

Pengganti drop-in untuk `Store` in-memory di `scripts/app.py`. Route handler
tidak berubah sama sekali; hanya objek yang di-inject lewat `get_store()`.

### Alasan sesungguhnya — bukan persistensi

`compare_and_set()` versi in-memory memakai `threading.Lock`. Itu menutup bug
lost-update (audit F) untuk **satu proses saja**. Jalankan dua worker uvicorn,
masing-masing memegang lock sendiri, dan bug-nya kembali persis seperti semula:
dua petugas sama-sama membaca versi 3, sama-sama lolos guard, sama-sama menulis
versi 4 — dan yang kalah tidak pernah diberi tahu.

Versi PostgreSQL:

```sql
UPDATE marawa_conversations
   SET ..., state_version = %s
 WHERE conversation_id = %s AND state_version = %s
```

`cur.rowcount == 0` berarti ada yang bergerak lebih dulu. Jaminan ini berlaku
lintas proses, lintas mesin, dan bertahan melewati restart.

**Mekanismenya berbeda, jaminannya sama, dan tesnya tidak berubah** — itu justru
bukti bahwa abstraksinya benar.

### Metode lain yang layak disebut

**`claim_outbox_batch()` memakai `FOR UPDATE SKIP LOCKED`:**
```sql
WITH claimable AS (
    SELECT outbox_id FROM marawa_outbox
    WHERE (status='pending' AND (next_attempt_at IS NULL OR next_attempt_at <= %s))
       OR (status='claimed' AND claimed_at < %s - interval '120 seconds')
    ORDER BY created_at LIMIT %s
    FOR UPDATE SKIP LOCKED
)
UPDATE marawa_outbox o SET status='claimed', ... FROM claimable c ...
```
Memungkinkan beberapa worker menguras antrean yang sama tanpa saling merebut
baris, dan tanpa terserialisasi di belakang satu baris lambat. Klausa
`claimed_at < now - 120s` adalah yang membebaskan baris yang tersangkut karena
worker-nya mati di tengah jalan.

**`conversations_needing_sweep()`** hanya mengembalikan baris yang bukan
`IDLE_CLOSED`. Tanpa ini, biaya sweep tumbuh linear selamanya — percakapan yang
ditutup enam bulan lalu tidak mungkin butuh event lagi.

**`get_conversation()` memakai `ON CONFLICT DO NOTHING`** supaya dua pesan masuk
bersamaan dari kontak baru tidak berlomba membuat baris duplikat.

---

## 6. Bug U — ditemukan saat menulis store

### Gejala

Empat tes Postgres gagal:
```
AttributeError: 'SendRecord' object has no attribute 'sender_admin_id'
```

### Akar masalah

Dua tipe memodelkan hal yang sama dan tidak sepakat:

| Tipe | Dipakai oleh | Punya `sender_admin_id`? |
|---|---|---|
| `OutboxEntry` (`conversation_state.py`) | `authorize_send()` | ✅ Ya |
| `SendRecord` (`outbox_worker.py`) | worker & store | ❌ Tidak |

### Kenapa ini serius

`authorize_send()` berisi guard:
```python
if entry.sender_admin_id != current.assigned_admin_id:
    return False, "reassigned_to_other_admin"
```

Guard itu **tidak pernah bisa menyala di jalur nyata**, karena tipe yang
benar-benar mengalir melalui worker dan store tidak punya field-nya. Akibat
konkretnya: balasan dari petugas yang sudah tidak memegang percakapan tetap
terkirim ke warga.

### Perbaikan

Field `sender_admin_id` ditambahkan ke `SendRecord`, dengan komentar yang
menjelaskan kenapa ketidaksepakatan ini berbahaya.

Polanya identik dengan 21 bug sebelumnya di sesi audit: **bukan logika yang
salah di dalam satu fungsi, melainkan jahitan antar-komponen** — di sini,
antar-tipe data yang seharusnya konsisten.

---

## 7. `tests/test_postgres_store.py` (baru, 11 tes)

Di-skip otomatis kalau `MARAWA_TEST_DSN` tidak diset, jadi tidak merusak CI di
lingkungan tanpa DB.

| Tes | Yang dibuktikan |
|---|---|
| `test_lost_update_is_impossible_across_separate_connections` | **Tes yang tidak mungkin diberikan store in-memory.** Dua koneksi berbeda, keduanya lolos guard optimistik, tepat satu menang dan yang kalah diberi tahu |
| `test_state_survives_a_new_store_instance` | Restart tidak menghilangkan siapa yang sedang menangani percakapan |
| `test_redelivered_inbound_message_is_stored_once` | Bridge yang mengirim ulang tidak menggandakan pesan |
| `test_duplicate_send_action_is_rejected_by_the_database` | Retry klik yang sama ditolak |
| `test_two_genuine_sends_of_the_same_text_both_queue` | "ok" dua kali tetap terkirim dua kali (audit H) |
| `test_claim_batch_does_not_hand_the_same_row_to_two_workers` | `SKIP LOCKED` bekerja |
| `test_handover_cancels_pending_bot_outbox` | Outbox bot dibatalkan saat handover |
| `test_audit_log_is_append_only_and_readable` | Audit tercatat |
| `test_sweep_query_excludes_closed_conversations` | Sweep tidak tumbuh selamanya |
| + 2 tes pembuatan percakapan | Kontak pertama & tanpa duplikat |

Perintah menjalankan:
```bash
export MARAWA_TEST_DSN="host=... port=5432 user=... dbname=marawa_bps"
uv run pytest tests/test_postgres_store.py -q
```

---

## 8. Angka akhir sesi ini

| Metrik | Sebelum | Sesudah |
|---|---|---|
| Tes lulus (lingkungan tanpa DB produksi) | 272 | **283** |
| Tes gagal | 3 | 3 *(identik — semua karena `postgres.env` produksi tidak ada di sandbox)* |
| Error | 39 | 39 *(identik — butuh DB produksi)* |
| Total terkumpul | 314 | 325 |
| Migrasi | 001–006 | 001–**007** |
| Bug ditemukan & diperbaiki | 21 (kumulatif) | **22** |

Tiga kegagalan dan 39 error **identik dengan baseline sebelum perubahan** —
dipastikan bukan regresi dari pekerjaan ini.

`scripts/validate_docs.py` → **PASS**

---

## 9. Yang masih belum ada

Tetap dicatat terbuka, tidak ditutupi:

1. **`app.py` belum memakai `PostgresStore`.** Store-nya sudah ada dan teruji,
   tetapi dependency injection di `app.py` masih menunjuk ke `Store`
   in-memory. Ini langkah berikutnya yang paling kecil dan paling jelas.
2. **Baileys / koneksi WhatsApp** — belum ada kode sama sekali.
3. **UI dashboard** — hanya API, belum ada halaman web.
4. **Auth sungguhan** — masih header `X-Admin-Id` (placeholder, bukan
   autentikasi). TOTP dan sesi belum ada.
5. **Retensi 365 hari** — kebijakan sudah diputuskan, kodenya belum ada.
6. **Probe model (OQ-05)** — belum dijalankan; `data/reports/` belum berisi
   `model-capability-probe.json`.
7. **Sinyal eksternal** — belum ada pertanyaan pengguna nyata yang melewati
   sistem. Rencana: dicoba langsung lewat WhatsApp ke pegawai BPS dulu.

---

## 10. Cara mereproduksi verifikasi ini di server sendiri

```bash
# 1. Rantai migrasi dari nol (gunakan database TERPISAH, bukan produksi)
createdb marawa_verify
psql -d marawa_verify -f /tmp/base_schema.sql        # SCHEMA_SQL dari bps_storage.py
for m in 002 003 004 005 006 007; do
  psql -d marawa_verify -v ON_ERROR_STOP=1 -f migrations/${m}_*.sql
done

# 2. Buktikan CHECK constraint menolak unit tebakan
psql -d marawa_verify -c "INSERT INTO bps_registry.measure_registry
  (registry_version_id,measure_id,dataset_id,source_measure_id,name,value_type,
   unit_state,unit_display,unit_source,decimal_places,aggregation_semantics,
   queryable,quality_flags)
  VALUES ('v','m','d','c','PDRB','number','known','miliar rupiah',
          'title_matched',0,'additive',true,'{}')"
# harus: ERROR ... violates check constraint

# 3. Tes Postgres store
export MARAWA_TEST_DSN="dbname=marawa_verify"
uv run pytest tests/test_postgres_store.py -q      # 11 passed
```
