# Open Questions dan Input yang Masih Dibutuhkan

Dokumen inti sudah mengambil default yang aman. **Database layer (mirror + registry) sudah di-build (15 Aug 2026); runtime/WhatsApp/dashboard masih planning.** Item berikut tidak boleh ditebak oleh implementer karena memerlukan keputusan/aset BPS Kabupaten Padang Pariaman.

## Keputusan yang sudah diambil (15 Agt 2026)

| # | Keputusan | Konsekuensi teknis yang mengikat |
|---|---|---|
| OQ-02/02b | Nomor ditentukan saat pairing; scan QR sendiri | **Wajib `pairing_cutoff_ts`**: semua pesan dengan timestamp sebelum pairing diabaikan. Bila nomor lama dipakai ulang, Baileys menarik riwayat dan bot bisa membalas percakapan berbulan-bulan lalu |
| OQ-13 | Notifikasi antrean → grup WA petugas | Bot mengirim notifikasi lewat kanal yang sama; jangan gabung ke thread warga |
| OQ-14 | 3 petugas | Tidak ada assignment, tidak ada reassign, tidak ada priority. Daftar chat = daftar belum dibaca |
| OQ-15 | Superadmin ditentukan saat setting | **Guard di kode**: seed membuat 2 akun superadmin; sistem menolak menghapus/menurunkan superadmin terakhir |
| OQ-16 | Admin boleh baca semua percakapan | Tercatat sebagai keputusan sadar; setiap pembukaan thread masuk audit |
| OQ-09 | Retensi transkrip mentah **365 hari**; agregat non-identifiable disimpan panjang | Pemisahan agregat/mentah tetap wajib dibangun sejak awal (`docs/09` §10) |
| OQ-05 | OpenAI-compatible: Gemini 3.1 Flash (primary), DeepSeek V4 Flash (fallback) | Probe wajib memverifikasi 3 hal, lihat `docs/15` §Probe |
| OQ-01/03 | Fallback = admin handover + link form. Bot menyala 24/7 termasuk hari libur | **Bot tidak boleh menyiratkan balasan segera.** Di luar jam kerja, teks handover harus menyatakan petugas membalas pada jam kerja berikutnya |
| OQ-11 | Approval retensi dianggap tidak perlu | Lihat catatan di bawah — tetap tulis satu paragraf keputusan |
| OQ-06/07/08 | Ditunda (di luar Slice 1) | — |

### Probe kapabilitas model — 3 hal yang wajib dicek sebelum build

Endpoint "OpenAI-compatible" berbeda-beda dukungannya. Cek ini dulu, karena
ketiganya mengubah desain runtime:

1. **Structured output / JSON schema mode.** Ada atau tidak? Kalau tidak, andalkan
   prompt + parser ketat + satu kali repair (`AGENT.md` §0C).
2. **Assistant prefill** (mengisi awal giliran assistant). Banyak proxy
   OpenAI-compatible **tidak** mendukungnya. Kalau tidak didukung, hapus prefill
   dari desain dan naikkan ketergantungan pada JSON mode.
3. **Tool/function calling.** Agent memerlukan typed tool call; tanpa ini seluruh
   Lapis 0 harus diganti parser manual.

Catat hasilnya di `docs/25`. Ini pekerjaan setengah hari dengan API key pribadi
dan menghapus risiko terbesar dari Slice 1.

### Catatan retensi (OQ-11)

Keputusan "tidak perlu approval" dihormati, dan memang tidak dibutuhkan proses
formal. Tetapi tetap tulis **satu paragraf** di dokumen internal: apa yang
disimpan, berapa lama, untuk apa, siapa yang memutuskan, tanggal berapa.

Alasannya bukan birokrasi. Ini menyimpan nomor WhatsApp dan percakapan warga
selama setahun atas nama instansi pemerintah. Kalau suatu saat ada insiden atau
pertanyaan, satu paragraf itu adalah bedanya antara "ini keputusan yang diambil
dan dicatat" dan "tidak ada yang tahu kenapa data ini ada". Yang dilindungi
paragraf itu adalah orang yang membangun sistemnya.

## Dua pertanyaan yang bisa ditanyakan hari ini ke PST

Keduanya tidak butuh rapat, tidak butuh keputusan pimpinan, dan keduanya
mengubah desain secara material:

1. **"Nomor MARAWA yang sekarang, nanti dipakai bot atau bikin nomor baru?"**
   Kalau dipakai ulang: petugas bisa ambil alih percakapan cuma dengan mengetik
   dari HP, dan dashboard inbox tidak dibutuhkan untuk waktu yang lama. Kalau
   nomor baru: petugas tidak punya cara mengambil alih sama sekali sampai
   dashboard jadi, dan itu menaikkan prioritas Slice 2.
2. **"Kalau bot tidak bisa jawab, warga sebaiknya diarahkan ke mana?"**
   Nomor petugas, link formulir, atau datang ke kantor. Ini mengunci teks
   fallback dan menghapus OQ-01 dari jalur kritis.

## Aksi termurah yang membuka paling banyak — belum dikerjakan

**Kumpulkan 30 pertanyaan nyata di loket PST.** Tidak perlu workshop 90 menit.
Minta petugas front desk mencatat pertanyaan yang masuk selama 3 hari, apa
adanya, termasuk typo dan yang ambigu. Simpan ke
`data/evals/pst-real-questions.json`:

```json
{"questions": [
  {"utterance": "berapa penduduk lubuk alung sekarang", "family": "dynamic", "ids": ["29"]}
]}
```

Ini satu-satunya item di dokumen ini yang tidak menunggu keputusan siapa pun,
dan ia membuka:

- mengganti set evaluasi sintetis yang selama ini jadi sumber semua angka retrieval (audit C1b/C1c);
- angka pertama yang benar-benar menjawab "berapa persen pertanyaan bisa dijawab dari mirror sekarang" — saat ini tidak ada yang tahu;
- bukti apakah menu 5 layanan cocok dengan cara orang benar-benar bertanya;
- sesuatu yang konkret untuk ditunjukkan ke lima approver yang masih TBD.

Ekspektasi realistis: Recall@3 akan turun ke kisaran 0,6–0,8, dan mayoritas
kegagalan kemungkinan bukan soal ranking melainkan pertanyaan bertipe "terbaru"
atau indikator yang memang tidak ada di mirror. Tulis prediksinya dulu, lalu
cek — kalau meleset, itu informasi bagus.

## Blocker mana yang benar-benar mengunci Slice 1

Slice 1 (`docs/01-PRD` §11) sengaja dirancang melewati sebagian besar blocker:

| ID | Masih mengunci Slice 1? | Alasan |
|---|---|---|
| OQ-02 nomor WhatsApp | **YA** | tidak ada kanal tanpa ini |
| OQ-05 model/provider | **YA** — tapi bisa di-de-risk hari ini | probe kapabilitas pakai API key kuota kecil dulu, sebelum minta procurement |
| OQ-01 URL form | Tidak | Slice 1 cukup mengirim nomor petugas |
| OQ-03 jam layanan/SLA | Tidak | tanpa antrean, tidak ada SLA untuk dijanjikan |
| OQ-04 roster RBAC | Tidak | tidak ada dashboard di Slice 1 |
| OQ-06 embedding | Tidak | tidak ada RAG di Slice 1 |
| OQ-07 internal views | Tidak | seluruh data Slice 1 sudah ada di mirror publik |
| OQ-08 corpus internal | Tidak | tidak ada RAG di Slice 1 |
| OQ-09 retensi chat | Sebagian | tetap perlu sebelum publik; tidak menghalangi uji internal. Versi yang direframe (`docs/09` §10) jauh lebih mudah disetujui daripada versi lama |
| OQ-02b nomor dipakai ulang? | **YA untuk desain handover** | jawabannya menentukan bentuk handover Slice 1 |
| OQ-10 VPS/domain | Tidak | uji internal bisa jalan sebelum deploy final |
| OQ-11 privacy provider | Sebagian | wajib sebelum publik, tidak untuk uji internal |
| OQ-12 alert/on-call | Tidak | belum ada produksi untuk di-page |

Artinya yang benar-benar tersisa adalah **dua**, bukan dua belas.

## Production blockers

| ID | Pertanyaan/input | Owner | Default sementara | Dampak bila kosong |
|---|---|---|---|---|
| OQ-01 | URL form publik final | PST | `PUBLIC_FORM_URL=TBD` | Bot hanya bisa menawarkan admin, belum form |
| OQ-02 | Nomor WhatsApp dedicated dan siapa yang dapat pairing | PST/IT | Tidak ada | Channel tidak dapat diuji/deploy |
| OQ-02b | Apakah bot memakai ulang nomor MARAWA yang sekarang, atau nomor baru? | PST | Sebagian terjawab: petugas membalas dari **dashboard**, bukan HP (keputusan 15 Agt). Nomor mana yang dipakai tetap perlu dipastikan | Pairing Baileys; `fromMe` reconciliation tetap wajib karena petugas mungkin sesekali membalas dari HP |
| OQ-13 | **Ke mana notifikasi antrean dikirim?** (grup WA petugas / nomor pribadi / email) | PST | Belum ditanyakan | Tanpa ini inbox terisi diam-diam dan tidak ada yang tahu ada warga menunggu (`docs/06` §0.5) |
| OQ-14 | Berapa petugas yang akan memakai dashboard? | PST | Peran diputuskan 15 Agt: `admin` + `superadmin` | Jumlah menentukan apakah perlu assignment; <6 orang tidak perlu |
| OQ-15 | **Siapa dua orang yang memegang `superadmin`?** | Pimpinan/PST | Belum ditanyakan | Satu superadmin = layanan mati saat TOTP-nya hilang (`docs/06` §3.1) |
| OQ-16 | Apakah disepakati setiap admin bisa membaca SELURUH percakapan warga? | Pimpinan/Privacy | Belum ditanyakan | Default saat ini "ya"; harus keputusan sadar, bukan kelalaian (`docs/06` §3.0d) |
| OQ-03 | Jam layanan, hari libur, dan teks SLA antrean admin | PST | Jangan janjikan waktu | UX handover tanpa ETA |
| OQ-04 | Daftar petugas, supervisor, knowledge manager, auditor, system admin | Pimpinan | Tidak ada | RBAC/accounts belum dapat provision |
| OQ-05 | Exact primary Gemini model ID, provider endpoint/project, quota | IT/Procurement | Env configurable | Capability/cost/SLA belum terukur |
| OQ-06 | Embedding model/provider dan data processing approval | IT/Data owner | TBD | RAG index belum bisa final |
| OQ-07 | Approved **internal/existing** PostgreSQL views + data dictionary + release flag | Data owner | Public BPS mirror/registry built; internal views none | Public WebAPI queries tersedia, structured internal data tetap blocked |
| OQ-08 | Approved corpus internal dan masing-masing owner/validity | PST/Data owner | Public sources only | FAQ/SOP lokal terbatas |
| OQ-09 | ~~Approval retensi chat tanpa batas~~ → **Berapa lama transkrip mentah disimpan (usul: 30–90 hari), dengan agregat non-identifiable disimpan panjang?** | Pimpinan/Privacy | Direframe 15 Agt; approval pending | Production privacy sign-off blocked |
| OQ-10 | Domain dashboard, VPS specification, backup target | IT | Docker VPS generic | Production deploy blocked |
| OQ-11 | Provider privacy/data retention/logging settings/contract | IT/Privacy | Minimize payload | Production provider approval blocked |
| OQ-12 | Notification channel untuk alert dan on-call roster | IT/PST | Dashboard only | Incident response lambat |

## Product decisions to calibrate during pilot

- Apakah attachment/OCR masuk V1 atau ditunda?
- Berapa maksimal baris tabel pada WhatsApp dan apakah tersedia export link?
- Feedback prompt frequency.
- Confidence/abstention thresholds berdasarkan golden set.
- Queue priority rules dan supervisor reassignment.
- Apakah admin boleh mengirim file/media dari dashboard?
- Apakah manual messages dari paired phone direkonsiliasi ke dashboard?
- Apakah dashboard hanya VPN atau publik dengan auth+2FA?
- Target concurrent users/daily message volume dan budget provider.
- Berapa lama working context tetap aktif sebelum dianggap stale?
- Analysis methods mana yang masuk MVP dan mana yang butuh review statistisi?
- Format artifact mana yang wajib: chart image, CSV, XLSX, dan/atau PDF?
- Apakah user boleh mengunggah dataset sendiri untuk dianalisis; jika ya, format, ukuran, dan retention-nya?
- Berapa step/wall-time budget agent biasa sebelum analysis menjadi asynchronous job?

## Data discovery questionnaire

Untuk setiap dataset PostgreSQL:

1. Nama indikator, definisi, unit, desimal.
2. Dimensi/filter yang sah.
3. Level wilayah dan kode master.
4. Periode referensi vs release date.
5. Sementara/final/revisi dan supersession.
6. Missing/suppressed value semantics.
7. Catatan kaki/sumber/metodologi.
8. Klasifikasi akses dan approval diseminasi.
9. Expected refresh dan owner.
10. Sample query/result yang telah diverifikasi.

## Suggested next workshop

**Prasyarat: lakukan pengumpulan 30 pertanyaan di atas lebih dulu.** Workshop
dengan data mentah di tangan menghasilkan keputusan; workshop tanpanya
menghasilkan dokumen — dan proyek ini sudah punya cukup banyak dokumen.

90 menit dengan PST + data owner + IT:

- 15m: scope/brand/copy/handover.
- 25m: top 30 pertanyaan dan sumber.
- 20m: DB datasets/views.
- 15m: operator/RBAC/SLA.
- 15m: provider/VPS/privacy/retention.

Output workshop: semua production blockers mendapat owner dan due date, top-30 golden questions, serta daftar tiga dataset pertama untuk MVP.
