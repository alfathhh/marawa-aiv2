# BPS Manual Targeted Update Workflow

## Tujuan

BPS WebAPI tidak dipoll otomatis dan tidak ada cronjob BPS aktif. Saat operator mengetahui suatu tabel/indikator diperbarui, operator menyebutkan identifier dari katalog Excel. Update kemudian dibatasi ke resource yang disebut, bukan bootstrap ulang seluruh mirror.

Katalog identifier yang dibuat dari database lokal:

```text
data/reports/BPS_CATALOG_TABEL_1306.xlsx
/home/ubuntu/Obsidian/Hermes/Inbox/BPS_CATALOG_TABEL_1306.xlsx
```

File ini **metadata saja**: tidak ada value/cell/fact statistik. Ia berisi identifier, judul, klasifikasi, unit/tahun, dan tanggal update metadata bila tersedia.

Inventory workbook saat dibuat: **47** SIMDASI table · **335** Dynamic indicator · **165** Census dataset · **602** publication metadata.

Regenerasi katalog setelah suatu manual update:

```bash
cd /home/ubuntu/projects/marawa-ai
/home/ubuntu/.hermes/bin/uv run python scripts/export_bps_catalog_excel.py
```

## Cara memberi instruksi update

Gunakan identifier pada kolom pertama dari sheet yang relevan. Sertakan tahun/period jika revisi tidak mencakup seluruh riwayat.

| Sheet Excel | Identifier utama | Format instruksi ke operator |
|---|---|---|
| `SIMDASI` | `Kode Tabel` | `SIMDASI 3.1.1 tahun 2025 update` |
| `DYNAMIC` | `ID Indikator` | `DYNAMIC 29 tahun 2025 update` |
| `CENSUS` | `ID Event` + `ID Dataset` | `CENSUS sp2010 dataset 10 update` |
| `PUBLICATION` | `ID Publikasi` | `PUBLICATION 9d824be2b30029991c8aed8a update metadata` |

Jika BPS merevisi tahun lama, **tahun wajib disebutkan**. Jika periode/lingkup belum diketahui, operator akan membaca metadata lokal dahulu dan melaporkan exact resource yang akan diambil sebelum request API dilakukan.

## Aturan scope pull

| Family | Scope yang ditarik setelah instruksi valid | Tidak ditarik |
|---|---|---|
| SIMDASI | satu exact `table_code + year`; kode tabel dipetakan ke opaque `table_id` dari DB lokal | tabel SIMDASI lain dan year lain |
| Dynamic | satu `variable_id` dan period target; periode BPS internal dipetakan dari katalog period variable | variable lain, histori period lain |
| Census | satu `event_id + dataset_id` untuk area Padang Pariaman yang sudah ada di mirror | event/dataset/area lain |
| Publication | satu detail metadata `publication_id` | PDF binary, kecuali operator meminta terpisah |

Setiap pull target tetap menjalankan validasi response, snapshot lineage, upsert idempotent, dan post-update validation untuk resource tersebut. Response upstream yang kosong, HTML/WAF, HTTP non-OK, atau schema tidak cocok adalah failure—bukan dianggap nilai nol.

## Kapan memakai audit penuh

Audit penuh hanya manual dan eksplisit:

```bash
scripts/update_bps_webapi.sh full
```

Pakai hanya jika:

- operator meminta audit menyeluruh;
- banyak tabel/revisi tidak bisa dipisahkan dengan aman;
- ada indikasi perubahan katalog luas; atau
- validasi targeted update menemukan schema/lineage inconsistency.

Tidak ada audit penuh atau PDF download yang berjalan otomatis.

## Batas deteksi

Dynamic WebAPI tidak menyediakan webhook, ETag, revision cursor, atau timestamp per fact. Revisi nilai lama yang tidak mengubah katalog tidak dapat dideteksi tanpa membaca fact target. Karena itu laporan operator tentang identifier tabel/indikator adalah trigger utama untuk targeted update.

## Bukti update

Setiap targeted update harus menghasilkan catatan ringkas:

- family dan identifier;
- period/year/area yang ditarik;
- request count aktual;
- status validasi;
- snapshot lineage lama/baru bila content berubah;
- jumlah row/fact sebelum dan sesudah; dan
- daftar anomaly/upstream failure bila ada.

Tidak menampilkan API key, proxy secret, atau PDF kecuali diminta terpisah.

## Referensi

- Kontrak sumber, raw/current/serving, dan batas update: [`17-BPS-WEBAPI-DATA.md`](17-BPS-WEBAPI-DATA.md)
- Format angka/jawaban WhatsApp: [`18-WHATSAPP-DATA-ANSWER-FORMATS.md`](18-WHATSAPP-DATA-ANSWER-FORMATS.md)
- Runbook observability: [`12-OBSERVABILITY-RUNBOOK.md`](12-OBSERVABILITY-RUNBOOK.md)
- Katalog operasional: `data/reports/BPS_CATALOG_TABEL_1306.xlsx`
