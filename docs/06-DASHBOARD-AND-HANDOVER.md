# Dashboard Internal dan Live Handover

> **Keputusan produk 15 Agt 2026: petugas menjawab dari dashboard, bukan dari HP.**
> Dashboard menampilkan daftar chat seperti WhatsApp. Ini menjawab OQ-02b dan
> membatalkan rencana "Slice 1 tanpa dashboard" — inbox masuk jalur kritis.
> §0 sekarang mendefinisikan versi minimalnya; §2–§12 tetap target akhir.

## 0. Inbox minimum — yang dibangun sekarang

### 0.1 Konsekuensi keputusan ini (baca dulu)

Memindahkan balasan ke dashboard itu pilihan yang sah dan punya alasan bagus:
jejak audit jelas, petugas tidak perlu memegang HP kantor, dan balasan bisa
dilihat siapa yang menulis. Tapi tiga hal ikut berubah dan tidak bisa ditunda:

1. **Slice 1 tidak lagi 2 minggu.** Realistis 4–5 minggu, karena inbox berarti
   auth + real-time + outbox + state machine, bukan hanya bot.
2. **Data pribadi warga masuk browser sejak hari pertama.** Retensi (`docs/09`
   §10) dan auth tidak bisa lagi ditunda ke "nanti" — keduanya jadi prasyarat
   rilis, bukan pekerjaan lanjutan.
3. **Inbox hanya berguna kalau ada yang melihatnya.** Ini kegagalan operasional
   yang paling sering terjadi dan paling sering dilupakan di desain: petugas PST
   tidak duduk memandangi aplikasi web seharian. Kalau tidak ada notifikasi
   keluar, antrean terisi diam-diam dan warga menunggu tanpa ada yang tahu.
   **Notifikasi wajib masuk versi minimum**, lihat §0.5.

### 0.2 Cakupan versi minimum

Masuk:

| Bagian | Kenapa wajib |
|---|---|
| Login + TOTP, 2 peran (`petugas`, `admin`) | dashboard memegang nomor & transkrip warga |
| Daftar percakapan gaya WhatsApp | permintaan utama |
| Thread view + kirim balasan | inti fitur |
| Transactional outbox + worker pengirim | Baileys bisa putus; balasan tidak boleh hilang |
| State machine `BOT_ACTIVE` / `QUEUED` / `ADMIN_ACTIVE` / `BOT_COOLDOWN` | mencegah bot & petugas menjawab bersamaan |
| `fromMe` reconciliation | petugas tetap bisa membalas dari HP; kedua jalur harus bertemu di state yang sama |
| Notifikasi keluar saat ada antrean | tanpa ini inbox tidak dipakai |
| Audit log siapa membalas apa | data warga |
| Retensi (`docs/09` §10) | jangan menumpuk dulu baru diperbaiki |

Ditunda: knowledge management, config prompt/model, analytics, 5 peran, canned
response, SLA/priority/reassign, skill inspector, block/report.

### 0.3 Model data minimum

```text
conversation
  id, wa_contact_hash, display_name, state, state_version,
  assigned_to, last_message_at, unread_count, created_at

message
  id, conversation_id, direction (in|out), sender_type (user|bot|admin),
  sender_admin_id, body, wa_message_id, status (pending|sent|delivered|failed),
  created_at, sent_at

outbox
  id, conversation_id, body, sender_admin_id, state_version_at_enqueue,
  status (pending|claimed|sent|cancelled), attempts, cancel_reason
```

`state_version` adalah kunci seluruh keamanan race condition: setiap transisi
menaikkannya, dan worker menolak mengirim bila versi berubah sejak enqueue.

### 0.4 API minimum

```text
POST /auth/login              →  session + TOTP challenge
GET  /conversations           →  list, sort last_message_at desc
GET  /conversations/{id}      →  thread + context ringkas
POST /conversations/{id}/claim    →  QUEUED → ADMIN_ACTIVE (409 bila sudah diambil)
POST /conversations/{id}/messages →  masuk outbox, bukan langsung kirim
POST /conversations/{id}/return   →  ADMIN_ACTIVE → BOT_COOLDOWN
GET  /stream                  →  SSE update daftar & thread
```

Aturan yang tidak boleh dilanggar: **endpoint balasan tidak pernah memanggil
Baileys langsung.** Ia menulis ke outbox dalam transaksi yang sama dengan
perubahan state; worker terpisah yang mengirim. Kalau tidak begitu, request
timeout saat WhatsApp lambat akan menghasilkan balasan ganda atau hilang.

### 0.5 Notifikasi — bagian yang paling mudah dilupakan

Saat percakapan masuk `QUEUED`, kirim pemberitahuan ke luar dashboard. Urutan
dari yang paling murah:

1. Bot mengirim pesan WA ke grup/nomor petugas: *"1 chat menunggu — buka
   dashboard."* Gratis, memakai kanal yang sudah ada, dan sampai ke HP petugas.
2. Notifikasi browser saat tab dashboard terbuka.
3. Email, kalau memang dibaca.

Sebelum ini ada, jangan janjikan waktu tunggu apa pun ke warga.

### 0.6 Yang ditiru dari WhatsApp, dan yang tidak

Ditiru: daftar urut pesan terbaru, badge belum dibaca, gelembung kiri/kanan,
timestamp WIB, centang status kirim, scroll ke pesan terbaru, kirim dengan
Enter.

**Jangan** ditiru: indikator "sedang mengetik" dan read receipt ke warga. Warga
sedang bicara dengan layanan resmi, bukan dengan teman; menampilkan "dibaca"
menciptakan ekspektasi balasan segera yang tidak bisa dipenuhi kantor, dan
"sedang mengetik" membocorkan apakah ada petugas yang hadir.

### 0.7 Race condition yang wajib ditangani sejak versi minimum

Empat ini nyata dan pasti terjadi, bukan hipotetis:

| Situasi | Penanganan |
|---|---|
| Dua petugas menekan Ambil Alih bersamaan | transisi state bersyarat versi; yang kalah dapat `409`, UI refresh |
| Warga kirim `ADMIN` saat bot sedang menyusun jawaban | outbox AI dibatalkan `handover_preempted` sebelum kirim |
| Petugas membalas saat pesan bot sudah di outbox tapi belum terkirim | worker cek `state_version` sebelum kirim; batalkan bila berubah |
| Petugas membalas dari HP padahal chat `ADMIN_ACTIVE` di dashboard | `fromMe` yang tidak ada di outbox → catat sebagai pesan admin, jangan buat pesan hantu |

Sisanya (duplicate webhook, reordered receipt) ditangani idempotency di
`docs/07` dan tidak butuh kerja tambahan.

## 0A. Dashboard membalik model ancaman — dan itu penting

Audit menilai subsistem anti-jailbreak tidak proporsional **untuk bot**, karena
blast radius Slice 1 adalah data yang sudah publik di `webapi.bps.go.id`, tanpa
kemampuan menulis apa pun.

Dashboard adalah kebalikannya. Isinya:

- nomor WhatsApp warga;
- transkrip percakapan lengkap, termasuk pertanyaan yang mungkin sensitif
  (bantuan sosial, kemiskinan, ketenagakerjaan);
- disimpan **tanpa batas waktu** (`docs/09` §10);
- diakses beberapa akun manusia lewat browser.

Itu data pribadi sungguhan, dan pengendalinya instansi pemerintah. Jadi bukan
"security-nya berlebihan" yang berubah, melainkan **sasarannya**: usaha keamanan
harus pindah dari melindungi angka publik ke melindungi data warga. Prioritas
Slice 2 mengikuti itu — auth, RBAC, audit log, retensi, dan kontrol ekspor
mendahului kecanggihan agent.

## 1. Tujuan

Dashboard adalah control plane internal untuk petugas PST, knowledge manager, supervisor, auditor, dan admin sistem. Dashboard tidak publik dan semua aksi sensitif memerlukan authentication, permission check server-side, serta audit.

## 2. Information architecture

```text
/login
/2fa
/inbox
/inbox/[conversationId]
/knowledge/sources
/knowledge/documents/[id]
/knowledge/ingestion
/quality/feedback
/quality/evaluations
/analytics
/config/prompts
/config/models
/admin/users
/admin/roles
/audit
/system/health
```

## 0.8 Implementasi + audit state machine (15 Agt)

`scripts/conversation_state.py` — logika murni, tanpa DB/Baileys, 29 tes.
Aturan tunggal yang menopang semuanya: **setiap transisi menaikkan
`state_version`, dan tidak ada yang dikirim tanpa memeriksa ulang versi itu.**

Implementasi pertama lulus 24 tes, lalu di-audit dengan menulis tes yang
sengaja mencoba mematahkannya. Lima gagal — kelimanya bug nyata:

| # | Bug | Akibat kalau lolos ke produksi |
|---|---|---|
| A | Takeover dari HP membuat percakapan **tanpa pemilik** (`fromMe` tidak membawa identitas petugas), lalu claim & reply sama-sama menolak `None != "budi"` | Percakapan macet selamanya; warga menunggu di chat yang tidak menerima balasan dari siapa pun |
| B | Idle 5 menit menutup percakapan yang berstatus `QUEUED` | Warga dihukum karena **kita** yang lambat. Petugas balik dari istirahat menemukan case tertutup dan warga yang tidak diberi tahu apa-apa |
| C | Notice "sesi berakhir" tidak mungkin terkirim — `authorize_send` menolak semua di `IDLE_CLOSED`, padahal notice itu justru di-enqueue setelah penutupan | Sesi berakhir diam-diam; warga tidak tahu harus mengulang |
| D | `handover_notice()` mengembalikan string berisi `{form_url}` mentah yang harus di-`format()` pemanggil | Warga melihat `{form_url}` di chat resmi BPS |
| E | Guard versi tidak mencegah **dua agent run bersamaan** — inbound saat `BOT_ACTIVE` sengaja tidak mengubah versi, jadi dua pesan beruntun menghasilkan dua jawaban yang sama-sama lolos otorisasi | Warga menerima dua jawaban untuk satu pertanyaan |

Perbaikan:

- **A** → claim boleh *mengadopsi* percakapan tanpa pemilik; reply menolak dengan
  pesan yang memberi tahu petugas untuk menekan Ambil Alih dulu (supaya tetap
  ada penanggung jawab yang tercatat di audit).
- **B** → `queue_expiry_minutes` terpisah (default 240, rentang 30–1440). Idle
  warga dan batas tunggu antrean adalah dua hal berbeda dan tidak boleh memakai
  satu angka.
- **C** → `sender_type == "system"` dikecualikan dari blokir `IDLE_CLOSED`.
- **D** → `form_url` jadi parameter fungsi; placeholder tidak bisa lupa diisi.
- **E** → flag `agent_run_active`; inbound kedua menghasilkan `queue_followup`,
  bukan `run_agent` kedua.

Pelajaran yang berlaku umum di proyek ini: **guard versi optimistik menutup race
antar-aktor (petugas vs petugas, bot vs petugas), tapi tidak menutup race
satu-aktor** — dua pesan dari orang yang sama, berurutan cepat, tidak mengubah
versi apa pun. Itu butuh mekanisme terpisah. Ini persis pola yang sama dengan
temuan audit sebelumnya: penjaga yang terlihat menyeluruh ternyata hanya
menutup satu sumbu.

## 0.9 Model toggle handover (keputusan 15 Agt)

Handover pakai **toggle**, bukan claim/return. Petugas buka chat, nyalakan
toggle → bot diam; matikan toggle → bot hidup lagi. Lebih sederhana dan lebih
mudah dijelaskan ke petugas.

```text
Toggle ON   → bot diam untuk percakapan itu
            → outbox bot pending dibatalkan
            → warga diberi tahu ("petugas akan membalas")
            → dicatat siapa yang menyalakan

Toggle OFF  → bot hidup lagi
            → resume_watermark dipasang di detik itu
            → warga diberi tahu
```

### Empat mode gagal khas model toggle, dan penutupnya

Toggle lebih sederhana, tapi ia memindahkan tanggung jawab ke manusia yang bisa
lupa. Empat ini semuanya sudah bertes:

**1. Toggle lupa dimatikan.** Ini mode gagal yang mendefinisikan model toggle:
petugas menyalakan, lalu beralih ke pekerjaan lain. Bot mati, warga menunggu
selamanya, tidak ada yang menjawab, dan tidak ada apa pun yang memulihkan
sendiri. Penutup: **auto-revert** setelah `handover_auto_revert_minutes`
(default 30, rentang 5–480) tanpa aktivitas petugas. Setiap balasan petugas
memperpanjang jendelanya, jadi percakapan yang benar-benar sedang ditangani
tidak pernah direbut kembali.

**2. Bot menyembur menjawab antrean lama saat toggle dimatikan.** Warga mengirim
3 pesan selama ditangani manusia. Begitu toggle mati, tanpa penjaga bot akan
menjawab ketiganya sekaligus — padahal petugas sudah menanganinya. Penutup:
`resume_watermark_at`. Bot hanya menjawab pesan **setelah** toggle dimatikan.

**3. Dua petugas menyalakan toggle bersamaan.** Yang kedua ditolak dengan
`ALREADY_HELD` **dan diberi tahu siapa yang memegang** — bukan sekadar "gagal".
Petugas perlu tahu harus menghubungi siapa, bukan sekadar tahu tombolnya tidak
berfungsi.

**4. Toggle dinyalakan dari HP, bukan dashboard.** `fromMe` juga mengisi
`bot_paused_by`, sehingga percakapan tidak berakhir tanpa penanggung jawab
(bug AUDIT A sebelumnya).

## 0.10 Banyak warga chat bersamaan

Baileys memegang **satu koneksi**. Begitu nomor disebarkan, "banyak yang chat
barengan" bukan skenario teoretis melainkan keadaan normal.

| Masalah | Penutup |
|---|---|
| Satu percakapan panjang memonopoli koneksi, yang lain menunggu | `order_send_queue()` — round-robin satu pesan per percakapan per putaran. Urutan **di dalam** satu percakapan tidak pernah diacak |
| Dua pesan beruntun dari orang yang sama memicu dua agent run | `agent_run_active`; pesan kedua jadi `queue_followup` |
| Lima warga minta admin sekaligus → lima notifikasi beruntun | `should_notify_officers()` debounce per percakapan per `queue_notify_repeat_minutes` |
| Keputusan "bot jawab atau tidak" tersebar di banyak tempat lalu melenceng | `should_run_agent()` — satu titik keputusan, dipakai state handler, worker, dan outbox guard |
| Bot bermasalah dan perlu didiamkan segera tanpa deploy | `GlobalBotSwitch` — kill switch superadmin, satu notice per percakapan, bukan spam |

`should_run_agent()` sengaja dijadikan **satu fungsi**. Sebelumnya pertanyaan
yang sama dijawab di tiga tempat berbeda, dan cukup satu di antaranya melenceng
untuk mengembalikan bug "bot menimpa petugas".

## 3. Roles dan permissions

### 3.0 Model peran yang dipakai (keputusan 15 Agt)

Dua peran. Matriks lima peran di §3.2 tetap sebagai target akhir, bukan titik awal.

| Kapabilitas | `admin` | `superadmin` |
|---|:---:|:---:|
| Lihat daftar chat & thread | ✓ | ✓ |
| Balas warga, claim, return to bot | ✓ | ✓ |
| Lihat status koneksi WhatsApp | ✓ | ✓ |
| Kelola user & role (CRUD) | — | ✓ |
| Pairing WhatsApp (QR) | — | ✓ |
| Setelan timeout | — | ✓ |
| Setelan agent AI | — | ✓ |
| Lihat audit log | — | ✓ |
| Hapus data warga atas permintaan | — | ✓ |

Empat catatan yang mengubah desain, bukan sekadar pelengkap:

**(a) Status koneksi WhatsApp dilihat kedua peran.** Terlihat seperti setelan,
sebenarnya informasi operasional. `admin`-lah yang pertama sadar "kok tidak ada
chat masuk" — dan tanpa indikator koneksi, ia tidak bisa membedakan "memang
sepi" dari "sesi Baileys putus sejak kemarin". Menyembunyikannya di menu
superadmin berarti kerusakan baru diketahui saat ada warga mengeluh.

**(b) Audit log tidak dapat dihapus siapa pun, termasuk superadmin.** Append-only
di level database: role aplikasi tidak diberi `DELETE`/`UPDATE` pada tabel audit.
Murah, dan ia satu-satunya hal yang membuat audit log bermakna ketika peran
tertinggi juga memegang semua kunci lain.

**(c) Akun superadmin tidak boleh dipakai bersama.** Dengan dua peran, godaannya
besar: satu akun `superadmin` dipakai ramai-ramai "biar gampang". Begitu itu
terjadi, seluruh audit log jadi satu nama dan tidak bisa menjawab siapa membalas
warga atau siapa mengubah setelan. Aturan: superadmin adalah peran yang dipegang
**orang bernama**, satu akun satu orang, dan pekerjaan harian (membalas chat)
sebaiknya dilakukan dari akun `admin` pribadinya. Kalau hanya satu orang yang
memenuhi syarat, tetap buatkan dua akun untuknya.

**(d) `admin` melihat SEMUA percakapan, bukan hanya yang di-assign.** Untuk tim
3–5 orang itu benar — tidak ada yang perlu assignment. Tapi konsekuensinya harus
disadari: setiap admin bisa membaca seluruh percakapan warga. Keputusan yang sah,
asal diambil sadar dan tercatat, bukan default yang tidak pernah dibahas.

### 3.1 Break-glass: superadmin kehilangan TOTP

Risiko nyata untuk kantor kecil dengan satu superadmin: HP hilang, TOTP hilang,
lalu tidak ada yang bisa memasangkan ulang WhatsApp atau membuat user baru.
Layanan mati tanpa jalan masuk.

Wajib ada sejak hari pertama:

1. Recovery code sekali pakai, dicetak dan disimpan fisik saat enrollment.
2. Perintah CLI di server (`scripts/reset_admin_totp.py`) yang hanya jalan lewat
   SSH — kendalinya akses server, bukan akses web.
3. Minimal **dua** akun superadmin sejak awal, walau satu jarang dipakai.

Nomor 3 paling sering dilewat dan paling murah.

### 3.2 Matriks lima peran (target akhir, bukan sekarang)

| Capability | PST Agent | Supervisor | Knowledge Manager | Auditor | System Admin |
|---|:---:|:---:|:---:|:---:|:---:|
| Lihat inbox assigned | ✓ | ✓ | — | read | ✓ |
| Lihat semua conversation | — | ✓ | — | read | ✓ |
| Claim/reply/resolve | ✓ | ✓ | — | — | ✓ |
| Reassign/priority | — | ✓ | — | — | ✓ |
| Internal note | ✓ | ✓ | — | read | ✓ |
| Review evidence | ✓ | ✓ | ✓ | read | ✓ |
| Upload/edit knowledge draft | — | — | ✓ | read | ✓ |
| Publish/rollback knowledge | — | approval | ✓* | read | ✓ |
| Prompt/model draft | — | — | ✓ | read | ✓ |
| Prompt/model publish | — | ✓ | — | read | ✓ |
| Analytics | own/basic | all | content | read | ✓ |
| User/role management | — | — | — | read | ✓ |
| Audit log | — | ✓ | limited | ✓ | ✓ |

`✓*` tetap mengikuti four-eyes approval bila environment production.

## 3A. Pairing WhatsApp lewat QR — halaman paling berbahaya di dashboard

Setelah pairing, server menyimpan **auth state Baileys**. File itu memberi
pemegangnya kemampuan penuh mengirim sebagai nomor resmi BPS dan membaca seluruh
riwayat. Nilainya lebih tinggi daripada isi database — database berisi data
publik plus transkrip; auth state berisi *identitas* BPS di WhatsApp.

Perlakukan seperti kunci, bukan seperti konfigurasi:

| Kontrol | Alasan |
|---|---|
| QR hanya dirender untuk sesi `superadmin` yang baru re-auth | QR di layar = undangan untuk difoto |
| QR punya masa hidup pendek dan sekali pakai; tutup halaman → batalkan | mengurangi jendela paparan |
| QR **tidak pernah** masuk log, screenshot otomatis, atau error report | `docs/09` §120 sudah melarang; pastikan berlaku juga di UI |
| Auth state `0600`, di luar repo, di luar backup biasa | file inilah targetnya |
| Setiap pairing/unpair tercatat audit **dan** memicu notifikasi keluar | lihat di bawah |
| Tampilkan daftar linked device + tanggal pairing terakhir | satu-satunya cara menyadari ada perangkat asing |

**Serangan yang harus dicegah secara eksplisit.** Kalau dashboard berhasil
dikompromikan, penyerang bisa menampilkan QR dari **instans Baileys miliknya
sendiri** di halaman yang terlihat resmi. Petugas memindainya dengan HP BPS,
dan nomor resmi BPS ikut tertaut ke server penyerang. Warga lalu menerima pesan
dari nomor resmi yang bukan berasal dari BPS.

Karena itu notifikasi pairing tidak boleh lewat dashboard: **kirim ke WhatsApp
petugas lain**. Kalau pairing terjadi tanpa ada yang merasa melakukannya, itu
harus ketahuan dalam hitungan menit, dan tidak boleh bergantung pada kanal yang
mungkin sudah dikuasai penyerang.

Prosedur unpair darurat harus tertulis dan pernah dicoba: dari HP BPS, buka
WhatsApp → Perangkat Tertaut → keluarkan semua. Itu memutus akses server
seketika, dan harus diketahui lebih dari satu orang.

## 3B. Setelan agent AI — sumber angka salah paling mungkin

Ini halaman yang paling gampang merusak kebenaran jawaban, justru karena
terlihat tidak berbahaya.

### Yang boleh diatur dari dashboard

| Setelan | Batas keras | Kenapa dibatasi |
|---|---|---|
| Model primary/fallback | daftar tertutup hasil capability probe | model sembarang bisa tidak mendukung structured output |
| Temperature | `0.0 – 0.3` | slider sampai 1.5 mengundang eksperimen di produksi |
| Max output tokens | `256 – 2048` | |
| Teks sapaan, fallback, penutup | panjang maksimum | ini memang wilayah operasional |
| System prompt | draft → uji → publish → rollback, **bukan** textarea langsung simpan | perubahan tanpa uji = regresi diam-diam ke warga |

### Yang TIDAK boleh muncul sebagai tombol, selamanya

```text
✗ Matikan pemeriksaan bukti (answer_gate)
✗ Izinkan menjawab tanpa evidence
✗ Izinkan measure yang unitnya belum di-review
✗ Lewati keharusan user memilih tabel
✗ Naikkan row limit / statement timeout database
```

Alasannya bukan teoretis. Ketika bot sering menjawab "saya belum menemukan
datanya", tekanan wajar dari kantor adalah *"bisa nggak dibikin lebih membantu?"*
Kalau ada tombol yang menjanjikan itu, cepat atau lambat tombol itu ditekan —
dan sejak saat itu MARAWA menerbitkan angka tanpa bukti dengan nama BPS.

Gate hidup di kode dan diubah lewat deploy + review, bukan lewat browser. Ini
konsisten dengan `AGENT.md` §0: penegakan ada di Lapis 0–2, dan lapis itu bukan
milik UI.

**Setiap perubahan setelan agent tercatat audit dengan nilai lama → baru**, dan
`superadmin` bisa rollback ke versi sebelumnya dalam satu klik.

## 3C. Setelan timeout — semua nilai wajib punya batas

Halaman setelan tanpa batas min/maks adalah cara paling umum sebuah sistem
dimatikan oleh orang yang berniat baik.

| Setelan | Rentang | Default | Kalau salah isi |
|---|---|---|---|
| Idle sesi warga | 2–30 menit | 5 menit | aman |
| Cooldown kembali ke bot | 1–60 menit | 5 menit | aman |
| Notifikasi antrean berulang | 1–30 menit | 5 menit | aman |
| Idle sesi dashboard | 15–120 menit | 30 menit | terlalu lama = layar terbuka berisi data warga |
| Absolute session dashboard | 4–24 jam | 12 jam | idem |
| Budget langkah agent | 3–12 | 8 | terlalu tinggi = kuota LLM habis dalam sehari |

Tidak boleh diatur dari UI sama sekali: `statement_timeout` dan `lock_timeout`
database (dipegang role `marawa_runtime_ro`, `migrations/004`), timeout HTTP
provider, dan interval retry worker. Menaikkan `statement_timeout` dari browser
adalah cara satu klik untuk menggantung database.

Validasi rentang ditegakkan **server-side**, bukan hanya di form. Form bisa
dilewati; endpoint tidak.

## 4. Authentication

- Local account + password + TOTP.
- First login memaksa TOTP enrollment.
- Recovery code one-time dan regenerate membatalkan set lama.
- Session idle timeout dan absolute timeout configurable.
- Re-authentication untuk TOTP reset, role change, prompt/model publish, knowledge rollback, dan secret rotation.
- Login/rate-limit/lockout harus membedakan abuse dari typo tanpa membuka username enumeration.

## 5. Inbox UI

### Queue list

Menampilkan:

- pseudonymized/display name sesuai permission;
- last message preview redacted;
- state, priority, wait time, assigned agent;
- intent dan handover reason;
- unread count dan timestamp WIB;
- indicators: AI failed, negative feedback, repeated request, attachment.

Filter: state, assignment, priority, intent, date, feedback, SLA breach. Search nomor hanya menggunakan exact lookup melalui HMAC, bukan plaintext broad search.

### Conversation workspace

Tiga panel:

1. **Thread** — inbound/outbound/admin/system events.
2. **Context** — user request slots, concise AI summary, evidence, source links, model/tool trace sesuai permission.
3. **Action** — reply composer, canned response, internal note, claim/reassign, resolve, return bot, block/report.

Context panel juga menampilkan active goal, indicators, geographies, periods, datasets, working-memory version, agent run/steps, selected skill, query results, analyses, dan artifacts. Supervisor dapat melihat plan/action summary dan provenance, bukan private chain-of-thought.

AI summary hanya bantuan dan ditandai; admin tetap dapat membaca raw thread sesuai akses.

## 6. Handover state semantics (Slice 2)

### Queue SLA (3 menit)

- `admin_handover_timeout_seconds = 180` sejak `QUEUED`.
- Admin claim dalam SLA → `ADMIN_ACTIVE` + system notice handoff.
- SLA kedaluwarsa tanpa claim → state `RESOLVED`; bot mengirim busy notice: "Admin sedang sibuk saat ini. Silakan coba lagi nanti. ✓ Batalkan" lalu user dapat `batal` untuk kembali ke menu (sesi tetap aktif).
- Detail flow, timer idle 5 menit, kata kunci batal/keluar, state machine, dan config: `docs/27-BPS-SERVICE-MENU-FLOW.md`.

### Request

- User atau agent memicu `QUEUED`.
- Bot mengirim satu queue acknowledgement.
- Conversation muncul real-time.

### Claim

- Admin menekan **Ambil Alih**.
- API melakukan optimistic transition ke `ADMIN_ACTIVE` dan membuat audit/outbox system notice.
- Jika sudah di-claim orang lain, UI mendapat `409 CONVERSATION_STATE_CONFLICT` dan refresh.

### While admin active

- Inbound tetap disimpan dan di-stream ke dashboard.
- AI pipeline tidak dijalankan.
- Admin outbound masuk transactional outbox, lalu dikirim worker.
- Auto greeting, feedback prompt, retry AI, scheduled nudges semua dilarang.

### Resolve / return

- `Resolve`: menutup case dan mencatat disposition.
- `Return to bot`: membersihkan assignment, masuk `BOT_COOLDOWN`, mengirim pesan penutup, lalu AI aktif pada pesan user berikutnya.
- Tombol “Nyalakan bot sekarang” memerlukan confirmation dan audit agar tidak memotong admin message yang masih pending.

## 7. Race conditions yang wajib diuji

- Dua admin claim bersamaan.
- User mengirim `ADMIN` saat AI response sedang diproses.
- Admin claim setelah AI outbox dibuat tetapi belum dikirim.
- Admin return-to-bot sementara outbound admin pending.
- Duplicate webhook dan reordered delivery receipt.

Rule penting: sebelum worker mengirim AI outbound, worker/API melakukan final state/version guard. Jika conversation sudah `ADMIN_ACTIVE`, AI outbox dibatalkan dengan reason `handover_preempted`.

## 8. Knowledge management

Workflow UI:

1. Add source/upload document.
2. Set owner, authority, access class, release/effective dates.
3. Parse preview dan warnings.
4. Table/chunk preview + metadata correction.
5. Run scoped evaluation.
6. Submit review.
7. Approve/publish.
8. Monitor usage/freshness.
9. Rollback/quarantine.

Tidak ada hard delete source yang pernah menjadi evidence; archive dan revoke active status.

## 9. Prompt/model configuration

- Draft, compare diff, test sample, run golden eval, approve, publish, rollback.
- Model ID, provider, endpoint reference, timeout, temperature/thinking, max tokens, and enabled capabilities.
- API key hanya secret reference; UI tidak pernah menampilkan value.
- Publishing config invalid ditolak capability probe.
- Emergency rollback satu klik dengan re-auth dan audit.

## 9A. Statistical skills and agent runs

- Skill management: draft, review, evaluate, activate, supersede, quarantine.
- Skill detail menunjukkan trigger, prerequisites, tool sequence, methods, caveats, dan eval cases.
- Agent run inspector menunjukkan context resolution, chosen skill, ordered tool/analysis steps, observations, stop reason, token/cost/latency, evidence/result/analysis/artifact lineage.
- Admin dapat retry dari sanitized input atau membuat regression case; tidak dapat mengedit run history.
- Working memory dapat di-reset untuk topik baru oleh permissioned admin tanpa menghapus transcript.

## 10. Analytics

Cards/charts:

- daily/weekly conversation volume;
- intent distribution;
- answer/abstain/handover rates;
- positive/negative feedback;
- p50/p95 latency;
- queue wait and handling time;
- top unanswered/content gaps;
- source utilization/freshness;
- model primary/fallback split, errors, token and estimated cost;
- admin workload/disposition.

Metrics harus dapat difilter waktu dan tidak mengekspos raw nomor di aggregate view.

## 11. Real-time transport

- SSE untuk queue/thread update pada MVP; WebSocket tidak wajib.
- Event IDs resumable (`Last-Event-ID`).
- Permission filter dijalankan sebelum publish ke client.
- Client reconnect exponential backoff.
- REST tetap source of truth; SSE hanya notification/update stream.

## 12. Accessibility dan UX

- Keyboard navigation untuk inbox/composer.
- Status tidak hanya dibedakan warna.
- Konfirmasi untuk block, resolve, rollback, TOTP reset, dan role mutation.
- Semua timestamp tampil WIB dengan tooltip UTC.
- Jangan auto-mark read hanya karena list terlihat; gunakan explicit/opened semantics.
