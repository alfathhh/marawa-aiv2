# Dashboard Petugas — Desain & Langkah Berikutnya

**Berkas:** `apps/dashboard/index.html` (satu berkas, tanpa build step)
**Tanggal:** 16 Agustus 2026

---

## 1. Keputusan desain, dan alasannya

### Subjek dan tugas tunggal

Panel ini dipakai **3 petugas PST** di loket BPS Kabupaten Padang Pariaman.
Mereka tidak duduk memandangi layar; mereka melayani orang di depan meja dan
sesekali menengok apakah ada yang butuh dibantu lewat WhatsApp.

Maka tugas tunggal halaman ini bukan "menampilkan percakapan", melainkan:

> **Siapa yang sedang menunggu manusia, dan sudah berapa lama.**

### Kenapa layout chat biasa justru salah

Bentuk default aplikasi pesan — daftar seragam di kiri, panel percakapan di
kanan — memperlakukan semua percakapan setara. Padahal 12 percakapan yang
sedang dijawab bot **tidak butuh perhatian sama sekali**, dan satu orang yang
menunggu 14 menit butuh perhatian sekarang. Daftar seragam menyembunyikan
perbedaan itu di balik urutan waktu.

Karena itu daftarnya **bertingkat menurut urgensi**, bukan seragam:

```
┌──────────────────────────────┬──────────────────────────┐
│ MENUNGGU PETUGAS      [ 2 ]  │                          │
│ ┌──────────────────────────┐ │                          │
│ │▌▌▌ 6281234···     14:02  │ │   panel percakapan       │
│ │▌▌▌ "berapa penduduk…"    │ │                          │
│ └──────────────────────────┘ │                          │
│ ┌──────────────────────────┐ │                          │
│ │▌ 6285···           01:47 │ │                          │
│ └──────────────────────────┘ │                          │
│                              │                          │
│ DITANGANI PETUGAS     [ 1 ]  │                          │
│ │ 6287···            budi   │  ← menyusut jadi baris    │
│                              │                          │
│ DIJAWAB BOT          [ 12 ]  │                          │
│ │ · · · (baris tipis)       │  ← nyaris tak terlihat    │
└──────────────────────────────┴──────────────────────────┘
```

### Elemen tanda tangan: pita yang menebal

Kartu "menunggu" adalah satu-satunya elemen besar dan berwarna panas di layar.
Pita kiri **menebal seiring lama menunggu** — 5px, 8px, 12px — dan pada
tingkat tertinggi diberi denyut halus.

Dua alasan, keduanya praktis:

1. **Terbaca dari seberang meja.** Petugas yang sedang melayani tamu bisa
   melirik layar dan tahu ada yang terlantar tanpa membaca angka.
2. **Ketebalan, bukan hanya warna.** Sekitar 1 dari 12 laki-laki kesulitan
   membedakan merah–hijau. Kalau urgensi hanya dikodekan warna, sebagian
   petugas kehilangan informasinya sepenuhnya.

Timer berdetak tiap detik di sisi klien, sementara data disegarkan tiap 5
detik — angkanya hidup tanpa memaksa API dipolling per detik.

### Palet: warna hanya untuk mengkode state

| Warna | Arti | Kenapa |
|---|---|---|
| `#B23C10` bata | menunggu petugas | satu-satunya warna panas di layar |
| `#1B4ACC` biru | dipegang petugas | tenang, tidak menarik perhatian |
| `#05704F` hijau | dijawab bot | paling tenang, tidak butuh tindakan |
| `#8A96A4` abu | selesai | mundur ke belakang |
| `#EEF1F4` kertas | latar | abu dingin, bukan krem |

Latarnya sengaja abu dingin, bukan krem hangat: krem + serif kontras tinggi
adalah tampilan yang sedang di mana-mana, dan tidak ada hubungannya dengan
kantor statistik. Netral dingin juga membuat satu warna panas benar-benar
menonjol.

### Tipografi

| Peran | Huruf | Alasan |
|---|---|---|
| Label struktural | Barlow Condensed | Terkondensasi seperti papan petunjuk administratif; dipakai terbatas untuk eyebrow dan judul |
| Teks | IBM Plex Sans | Dirancang untuk lembaga teknis, diakritik Indonesia lengkap |
| Angka & referensi | IBM Plex Mono | Timer perlu lebar tetap agar tidak bergoyang tiap detik; referensi tabel (D1/S1/P1) memang data |

Semua punya fallback sistem — kantor kabupaten tidak selalu punya koneksi
lancar ke CDN font.

### Struktur membawa informasi

Eyebrow tidak dekoratif. `MENUNGGU PETUGAS [ 2 ]` menyebutkan jumlah nyata, dan
berubah warna hanya ketika jumlahnya bukan nol. Tidak ada penomoran `01 / 02 /
03` karena percakapan bukan urutan langkah.

### Kata-kata

- Tombol menyebut yang terjadi: **Ambil alih** → toast **Diambil alih**.
- Kosong itu ajakan, bukan suasana: *"Tidak ada yang menunggu. Bot sedang
  menangani 12 percakapan."*
- Gagal itu arah, bukan permintaan maaf: *"Percakapan ini sedang dipegang
  petugas lain."*
- Petunjuk menerangkan akibat sebelum ditekan: *"Mengambil alih akan
  mendiamkan bot di percakapan ini."*

### Lantai kualitas

Responsif sampai ponsel (panel percakapan jadi overlay penuh), fokus keyboard
terlihat, `prefers-reduced-motion` dihormati, `Esc` menutup percakapan,
`Ctrl/Cmd+Enter` mengirim. Semua data yang masuk ke DOM lewat `esc()` — 18
titik, nol `onclick` inline, nol interpolasi mentah.

Delegasi event dipakai karena daftar digambar ulang tiap 5 detik; handler
inline akan hilang bersama simpulnya.

### Yang sengaja TIDAK dibuat

Grafik, ringkasan harian, kartu statistik. Panel ini dipakai sambil berdiri
melayani orang. Setiap elemen yang tidak menjawab "siapa yang menunggu"
memperlambat yang menjawabnya.

---

## 2. Langkah berikutnya

### Sekarang — sebelum nomor disebar ke warga

| # | Pekerjaan | Kenapa mendesak |
|---|---|---|
| 1 | Set `MARAWA_ENV=production`, `MARAWA_SESSION_KEY`, `MARAWA_WEBHOOK_SECRET` di `/etc/marawa/` | Tanpa yang ketiga, `/webhook/whatsapp` menerima pesan dari siapa pun yang menjangkau port. Tanpa yang pertama, salah konfigurasi gagal-terbuka |
| 2 | Panggil `assert_production_config()` saat startup dan hentikan proses bila ada yang kurang | Gagal berisik lebih baik daripada berjalan tanpa autentikasi |
| 3 | Pasang `prompt_cache` ke jalur agent, catat `CacheStats` | Hemat ~80% biaya input; tanpa pelacakan, kegagalan cache tidak terlihat |
| 4 | Scan QR pairing WhatsApp | `journalctl -u marawa-worker -f` |
| 5 | Arahkan DNS `marawa.hatafisme.web.id` ke mesin ini | Sertifikat ACME menunggu ini |

### Setelah live — dua minggu pertama

| # | Pekerjaan | Catatan |
|---|---|---|
| 6 | **Uji ke 5 pegawai BPS dulu, bukan warga** | Sinyal eksternal pertama proyek ini. Kesalahan di depan kolega jauh lebih murah daripada di depan warga |
| 7 | Kumpulkan pertanyaan nyata yang masuk → `data/evals/pst-real-questions.json` | Mengubah metrik retrieval dari sintetis jadi berarti |
| 8 | Jalankan scorer terhadap set nyata, laporkan apa adanya | Prediksi: recall@3 turun ke 0,6–0,8 |
| 9 | Review unit 13 dataset + delta dari migrasi 006 oleh data owner | Membuka lebih banyak data untuk bisa dijawab |

### Ditunda sampai ada bukti dibutuhkan

- Antrean handover dengan SLA, claim, dan reassign (`docs/06` §6). Dengan 3
  petugas, daftar belum-dibaca sudah cukup. Bangun kalau data pemakaian
  menunjukkan orang benar-benar menunggu di dalam chat.
- Dashboard analitik. Belum ada yang bisa dianalisis.
- RAG dan definisi/FAQ. Slice 1 tidak memerlukannya.
- Anti-jailbreak suite sebagai release gate. Blast radius masih data publik;
  aktifkan kembali kalau OQ-07 (data internal) disetujui.

---

## 3. Yang diselesaikan setelahnya (16 Agt, lanjutan)

### Notifikasi keluar — sekarang benar-benar mengirim

Sebelumnya `InMemoryChannel` hanya menampung: efek `notify_officers` tercatat
di daftar Python dan berhenti di situ. Sekarang ada `OutboxChannel` yang
mengirim sungguhan lewat **outbox yang sama** dengan pesan warga.

Lewat outbox, bukan langsung ke Baileys, karena alasannya persis sama dengan
pesan warga: WhatsApp gagal dengan cara paling canggung — terkirim di sisi
mereka, responsnya tidak sampai ke kita. Notifikasi yang dikirim langsung dari
handler akan hilang saat worker sedang putus, dan **justru saat worker putus
itulah petugas paling perlu tahu**.

Bagi worker, notifikasi hanyalah baris outbox lain dengan tujuan berbeda. Satu
jalur pengiriman, satu tempat yang bisa salah — dan notifikasi ikut mendapat
retry, idempotensi, serta pencatatan yang sama.

Konfigurasi: `MARAWA_OFFICER_GROUP_JID` (JID grup WA petugas). **Kalau tidak
di-set, dicatat keras ke stderr**, tidak ditelan diam-diam — panel yang tidak
pernah memberi tahu siapa pun adalah panel yang tidak dibuka, dan itu tidak
boleh jadi kegagalan senyap.

### Setelan dan Riwayat masuk UI

Dua tab tambahan, hanya terlihat oleh `superadmin`:

**Setelan** — empat nilai timeout dengan rentang yang tertulis di formulir dan
ditegakkan server. Bagian Agent menampilkan daftar **yang tidak dapat diubah
selamanya** (mematikan `answer_gate`, melewati review unit, melewati keharusan
memilih tabel), dengan alasannya tertulis di layar: *tombol yang bisa
mematikan pemeriksaan kebenaran, cepat atau lambat, akan ditekan saat bot
terasa kurang membantu.*

**Riwayat** — audit log, dengan keterangan bahwa catatan ini tidak dapat
dihapus siapa pun termasuk superadmin, karena catatan yang bisa disunting
tidak menjawab apa pun.

### Polling dipertahankan — dan itu keputusan, bukan penundaan

SSE tidak dipakai. Alasannya bukan kemalasan:

**Mode gagal SSE lebih berbahaya daripada polling di antarmuka ini.** Aliran
yang mati diam-diam meninggalkan papan basi tanpa gejala apa pun — dan papan
triase basi terlihat **persis sama** dengan "tidak ada yang menunggu". Itu
kegagalan paling berbahaya yang bisa dimiliki panel ini. Polling pulih sendiri
tiap 5 detik.

Sebagai gantinya ditambahkan **indikator kesegaran**: bila data lebih tua dari
20 detik, status koneksi berubah merah dan menulis "Data 34 detik lalu —
periksa koneksi". Papan yang membeku sekarang terlihat membeku.

## 4. Yang masih terbuka

1. **Belum diuji di peramban sungguhan.** Diverifikasi tersaji 200 dari server,
   seluruh endpoint UI terjawab, 31 titik `esc()`, nol `onclick` inline, nol
   interpolasi mentah — tetapi tata letaknya belum pernah dilihat mata manusia.
   Buka di ponsel dan desktop sebelum dipakai petugas.
2. **`MARAWA_OFFICER_GROUP_JID` belum diisi.** Cari JID grup lewat log worker
   setelah pairing, lalu isikan ke `/etc/marawa/`.
3. **Notifikasi ke grup belum pernah benar-benar terkirim** — logikanya bertes
   (8 tes), tetapi belum ada pesan nyata yang sampai ke HP petugas. Uji ini
   masuk daftar setelah pairing.


---

## 5. Nomor petugas & blokir bot (16 Agt, lanjutan)

Notifikasi tidak lagi ke satu JID grup, melainkan **fanout ke setiap nomor
petugas** yang terdaftar. Daftarnya dikelola dari Dashboard > Setelan, bukan
dari env — supaya petugas bisa menambah/menghapus tanpa akses server dan tanpa
restart.

### Kenapa nomor petugas WAJIB diblokir dari bot

Bot mengirim notifikasi **ke** nomor petugas. Di WhatsApp itu membuat
percakapan biasa. Tanpa penjaga, tiga hal terjadi berurutan:

1. Petugas membalas notifikasi — *"oke, saya cek"* — refleks yang sangat wajar.
   Bot memperlakukannya sebagai pertanyaan warga dan menjawab soal statistik.
2. Nomor petugas muncul di kotak masuk sebagai warga, **menenggelamkan orang
   yang benar-benar menunggu** — kebalikan persis dari tugas papan triase.
3. Petugas bisa mengetik `ADMIN` dan masuk antrean sendiri, lalu memicu
   notifikasi ke dirinya sendiri.

Filter berjalan di webhook **sebelum percakapan dibuat**. Kalau ditaruh
belakangan, thread petugas tetap lahir dan mengotori kotak masuk meski botnya
diam. Percakapan yang terlanjur ada ditandai `is_staff_channel` dan
dikecualikan dari kotak masuk maupun sweep.

### Titik paling rawan: normalisasi nomor

`scripts/phone.py` ada sebagai modul tersendiri karena kegagalannya **senyap**.
Nomor yang sama datang sebagai `08123…`, `+62 812-34…`,
`628123…@s.whatsapp.net`, `628123…:12@s.whatsapp.net`. Kalau normalisasinya
tidak seragam di kedua sisi, daftar blokir berhenti bekerja tanpa error apa
pun: tersimpan `08123`, WhatsApp mengirim `628123`, tidak cocok, bot mulai
menjawab petugas.

Ini pola yang sama dengan bug-bug lain di proyek ini — bukan logika yang salah,
melainkan dua sisi memodelkan hal sama dengan bentuk berbeda. Karena itu satu
fungsi dipakai di semua tempat, dan masukan tak masuk akal **ditolak keras**,
tidak diterima diam-diam.

### Idempotensi fanout

Satu percakapan mengantre = satu baris outbox **per petugas**. Kunci
idempotensi memuat nomor tujuan; kalau tidak, tiga baris bertabrakan jadi satu
dan hanya satu petugas yang diberi tahu — kegagalan yang terlihat seperti
"notifikasi terkirim".

### Migrasi 009

`marawa_admin_contacts` (nomor, label, notify, blocked_from_bot, active) +
kolom `is_staff_channel` pada `marawa_conversations`. Up/down/up teruji bersih
di PostgreSQL 16.14.
