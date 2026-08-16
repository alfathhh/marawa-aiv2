# Remediasi Audit MARAWA AI — Ringkasan Perubahan

**Tanggal:** 15 Agustus 2026
**Basis:** `AUDIT-MARAWA-2026-08-15.md`
**Hasil:** 9 file kode/migrasi diubah atau dibuat, 14 dokumen disinkronkan, 15 tes baru lulus, `validate_docs.py` PASS

---

## Cara verifikasi (jalankan di repo penuh)

```bash
python3 -m pytest tests/ -q                      # 15 passed
python3 scripts/validate_docs.py                 # documentation validation: PASS
uv run python scripts/check_runtime_privileges.py   # butuh DB
uv run python scripts/run_migrations.py --up 006    # butuh DB
uv run python scripts/build_bps_registry.py         # rebuild registry
```

Tiga perintah terakhir butuh PostgreSQL, jadi belum bisa dijalankan dari bundle ini.
Dua perintah pertama sudah dijalankan dan hijau.

---

## 1. Kode: invariant unit (C2)

`scripts/build_bps_registry.py`

| Sebelum | Sesudah |
|---|---|
| `if COUNT_TITLE_RE.match(title) or "menurut" in lowered: return "unitless"` | cabang `"menurut"` dihapus; jatuh ke `unknown_review` |
| unit dari `ILIKE '%miliar rupiah%'` → `unit_state='known'` | → `unit_state='review_required'`, tidak queryable |
| gate `blocked_quality` hanya bila SELURUH measure bermasalah | gate per-measure: `queryable` + `quality_flags` |
| `return "count" if unit in ("", "Tidak Ada Satuan") else "additive"` | unit tidak diketahui → `"unknown"` (melarang penjumlahan) |
| — | integrity gate memblokir publish bila ada measure queryable tanpa unit bersumber |

Ditambah CHECK constraint di migrasi 006, supaya builder mana pun di masa depan
tidak bisa meregresikan ini tanpa error dari database.

**Efek yang diharapkan pada rebuild:** jumlah measure non-queryable akan naik.
Itu perbaikannya bekerja, bukan regresi — measure tersebut sebelumnya bisa
dijawab hanya berbekal heuristik. Jalankan tiga query di audit §7 sebelum dan
sesudah rebuild, lalu masukkan delta-nya ke paket review `docs/26`.

## 2. Kode: binder (H1, H2)

`scripts/bps_template_binder.py` — ditulis ulang.

- `"LIMIT" not in sql` (case-sensitive, cocok dengan alias/komentar) → field `has_own_limit` eksplisit
- template tanpa limit sendiri **dibungkus** `SELECT * FROM (...) LIMIT`, bukan ditempel di ekor
- tipe `like` meng-escape `%`, `_`, `\` — dipasangkan dengan `ESCAPE` di SQL
- setiap parameter integer wajib punya `max:`; teks dibatasi 512 karakter; NUL ditolak
- `Decimal` diterima untuk numeric (psycopg mengembalikan Decimal)

Terverifikasi langsung: `page_size: 50_000_000` → ditolak; `"100% padi_2025"` → `100\% padi\_2025`.

## 3. Kode: kejujuran pelaporan (C1)

| File | Perubahan |
|---|---|
| `eval_golden_episodes.py` | melaporkan `exercised` / `lint_only` / `blocked` terpisah; `passed` hanya dihitung dari yang dieksekusi. Angka nyata: **15 dieksekusi, 25 diblokir, 6 lint** dari 46 turn; episode 011/014/015/016/017 tanpa assertion yang bisa jalan |
| `simulate_bps_candidate_scoring.py` | alias typo `pendudk`/`pendudduk` dihapus → `_fuzzy_expand()` terhadap vocabulary katalog live; output berlabel `synthetic_author_written`; jalur `data/evals/pst-real-questions.json` |
| `validate_docs.py` | gate baru: hitungan episode diturunkan dari fixture, hitungan tes single-source, kontradiksi kebijakan (sadar negasi), metrik loop tertutup tanpa label |

Validator langsung menangkap 5 drift nyata di dokumen lama — semuanya sudah diperbaiki.

## 4. Kode & migrasi: sisanya

- `scripts/check_runtime_privileges.py` (baru) — assertion positif (12 objek) + negatif (tidak boleh baca tabel raw) + atribut role + timeout. **H3 tidak lagi bergantung ingatan.**
- `migrations/006_*.sql` + `.down.sql` — `unit_state` SIMDASI, kolom `category_code`/`category_label` Dynamic (M1), kolom gate measure, CHECK constraint, dan **re-grant inline** setelah `DROP VIEW`
- template baru `dynamic_latest` + `simdasi_latest` (H4) — "terbaru" akhirnya punya jalur, dan periode yang dilayani dikembalikan di hasil supaya jawaban menyebut tahunnya
- `simdasi_point` bind `indicator_code`, bukan label manusia (M2)
- `primary_dimension_item` dibuang dari semua template (M1)
- `tests/test_unit_and_binder_invariants.py` (baru) — 15 tes, semuanya gagal duluan sebelum fix

## 5. Dokumentasi

Tidak ada dokumen baru. Sesuai rekomendasi audit §6.1, yang dilakukan adalah
menyinkronkan yang ada — 50.000 kata sudah melewati titik di mana dokumen
tambahan menambah kejelasan.

| Dokumen | Perubahan utama |
|---|---|
| `README.md` | tabel status dengan kolom **kekuatan bukti**; peringatan cara membaca angka; `19/47` → `19/46` |
| `docs/00-INDEX.md` | status jujur + urutan baca untuk yang bingung proyeknya di mana |
| `docs/01-PRD.md` | **MVP dipotong jadi Slice 1**; "scheduled sync" dicabut (M5); sign-off diberi konteks |
| `docs/13-ADR.md` | ADR-015 (kekuatan bukti wajib dicatat), ADR-016 (gate unit level measure), ADR-017 (scope Slice 1) |
| `docs/14` | jalur kritis 7 langkah menggantikan waterfall; **Fase 9.1 dicabut dari jalur kritis** |
| `docs/15` | aksi termurah (30 pertanyaan PST) + tabel blocker mana yang benar-benar mengunci Slice 1 → **2, bukan 12** |
| `docs/25` | dokumen status canonical: tabel kekuatan bukti, note A (metrik sintetis), note B (harness), tabel remediasi 16 temuan |
| `docs/26` | peringatan paket belum lengkap + dua kelompok baru yang akan muncul setelah rebuild |
| `docs/27` | pembagian Slice: handover Slice 1 = kirim nomor petugas lalu berhenti; antrean/SLA/race ditunda ke Slice 2 |
| `docs/02, 21, 22, 23, 24` | label sintetis pada metrik, perubahan template, checklist diperbarui |

---

## Yang sengaja TIDAK dikerjakan

- **Tidak membuat `docs/28`.** Audit merekomendasikan membekukan penulisan dokumen; membuat dokumen baru untuk merayakan perbaikan akan melanggar rekomendasinya sendiri.
- **Tidak menyentuh `09A/09B/09C`.** Desainnya bagus dan tetap utuh untuk Slice 2+; yang berubah hanya posisinya di jalur kritis (`docs/14`).
- **Tidak mengarang angka pengganti.** Metrik retrieval tidak diganti dengan angka baru yang terdengar lebih jujur — diberi label, dan angka sesungguhnya menunggu 30 pertanyaan nyata.
- **Tidak menjalankan migrasi atau rebuild.** Butuh DB live; perintahnya ada di atas.

## Langkah berikutnya, berurutan

1. `run_migrations.py --up 006`, lalu `check_runtime_privileges.py`
2. Rebuild registry; catat berapa measure yang pindah ke non-queryable
3. Regenerasi paket review `docs/26`, kirim ke data owner
4. **Kumpulkan 30 pertanyaan di loket PST** — 3 hari, tidak menunggu keputusan siapa pun
5. Jalankan scorer terhadap set nyata; laporkan apa adanya (prediksi: 0,6–0,8)
6. Probe kapabilitas model dengan API key kuota kecil (de-risk OQ-05)
7. Bangun Slice 1
