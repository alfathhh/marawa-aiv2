# Laporan Lengkap — MARAWA AI, dari Audit Awal sampai Sekarang

**Periode:** 15 Agustus 2026, satu sesi kerja berkelanjutan
**Status akhir:** 163 tes otomatis lulus, 0 gagal, `validate_docs.py` PASS
**Sifat pekerjaan:** planning + implementasi logika yang bisa dieksekusi (bukan produksi — lihat §7)

---

## 0. Peta perjalanan

```
1. Audit awal (docs lama)          →  3 kritis, 4 high, 7 medium
2. Fix kode + sinkronisasi 14 dok  →  invariant unit & binder ditegakkan tes
3. Desain kepatuhan agent          →  model 4 lapis, answer_gate.py
4. Desain dashboard & handover     →  role, keamanan QR, batas setelan
5. 14 keputusan produk dikunci     →  dicatat di docs/15 + konsekuensinya
6. Implementasi runtime            →  state machine, outbox, formatter, probe
7. Wiring FastAPI                  →  scripts/app.py, 16 rute HTTP nyata
8. Lima putaran audit adversarial  →  21 bug ditemukan & diperbaiki
9. Laporan verifikasi untuk user   →  langkah yang butuh DB/API key live
```

Total across semua fase: **163 tes**, **~2.760 baris kode baru** di 9 file
`scripts/`, **1 migrasi SQL** baru (006) dengan `.down.sql`, **21 bug**
ditemukan lewat audit dan semuanya diperbaiki dengan tes regresi.

---

## 1. Audit awal — dokumen perencanaan lama

**Input:** `marawa-docs-full.zip` — 83 file (36 markdown, 21 script Python,
10 migrasi SQL, kontrak JSON, report).

### Temuan Kritis

**C1 — Seluruh stack verifikasi adalah loop tertutup.** Semua angka hijau di
ringkasan status (Recall@3 1.000, 19/19 PASS, 150 PASS) dihasilkan dari
fixture yang ditulis penulis sistem sendiri, dinilai terhadap ekspektasi
penulis yang sama:
- **C1a**: harness golden-episode cuma menjalankan engine sungguhan di turn
  pertama; sisanya melint fixture, bukan menguji perilaku
- **C1b**: alias typo (`pendudk`, `pendudduk`) di-hardcode persis sama dengan
  soal ujian sendiri — bukan mekanisme toleransi typo
- **C1c**: 60 kalimat evaluasi + kunci jawabannya ditulis sendiri, lalu
  scorer dikalibrasi sampai metriknya naik
- **C1d**: `validate_docs.py` cuma mengecek keberadaan keyword, tidak bisa
  mendeteksi kontradiksi angka antar-dokumen

**C2 — Invariant "unit tidak ditebak" dilanggar kode sendiri di 3 tempat:**
- **C2a**: unit mata uang diturunkan dari `ILIKE '%miliar rupiah%'` pada
  judul tabel — menerapkan satuan level-tabel ke data level-kolom
- **C2b**: kata "menurut" pada judul dianggap bukti measure tanpa satuan
- **C2c**: gate `blocked_quality` cuma aktif kalau SELURUH measure dalam
  dataset bermasalah — measure buruk di dalam dataset baik tetap lolos

**C3 — Scope MVP terlalu besar.** ~49.500 kata rencana untuk sistem dengan
0 baris kode runtime; kelima baris sign-off masih TBD.

### Temuan High
- **H1**: binder mengendus substring `"LIMIT" not in sql` — case-sensitive,
  `publication_list` kehilangan cap row-limit server-side
- **H2**: wildcard `ILIKE` tidak di-escape — search `"%"` mencocokkan semua
- **H3**: `DROP VIEW` menghapus grant read-only, bergantung ingatan manual
- **H4**: tidak ada template untuk "terbaru" — bentuk pertanyaan paling umum

### Temuan Medium (M1–M7)
Dimensi duplikat di serving view, binding SIMDASI pakai label bukan kode,
default agregasi "count" untuk unit tak diketahui, report 19MB masuk repo,
kontradiksi kebijakan cron, tiga angka test count berbeda untuk fixture yang
sama, basis internal FR-C belum ada.

**Output:** `AUDIT-MARAWA-2026-08-15.md` — laporan audit lengkap.

---

## 2. Perbaikan kode dan sinkronisasi dokumentasi (16 file)

Setiap temuan di §1 diperbaiki di kode, bukan cuma ditulis ulang di dokumen:

| Perbaikan | Bukti |
|---|---|
| `_unit_state()`: cabang "menurut" dihapus, unit hasil ILIKE → `review_required` | tes gagal-duluan |
| Gate `queryable` pindah ke level measure + CHECK constraint di DB | migrasi 006 |
| `_aggregation()`: unit tak diketahui → `"unknown"`, bukan `"count"` | — |
| Binder: `has_own_limit` eksplisit, wrapping bukan append, `like` type di-escape | — |
| `check_runtime_privileges.py` (baru): assertion positif+negatif privilege | — |
| Template baru `dynamic_latest` + `simdasi_latest` | — |
| `eval_golden_episodes.py`: melaporkan exercised/lint_only/blocked terpisah | 15 exercised, 5/19 episode belum pernah diuji |
| Alias typo hardcode dihapus → `_fuzzy_expand()` pakai vocabulary katalog live | — |
| `validate_docs.py`: gate drift angka, kontradiksi kebijakan, metrik tak berlabel | — |

Dokumen yang disinkronkan: `README.md`, `docs/00`, `01` (rescope Slice 1),
`13` (ADR-015/016/017), `14`, `15`, `21`, `22`, `23`, `24`, `25`, `26`, `27`.

**Prinsip yang dipegang:** tidak membuat dokumen baru (`docs/28`) — sesuai
rekomendasi audit sendiri untuk membekukan penulisan dokumen.

---

## 3. Desain kepatuhan agent — "gimana AI ini bisa nurut?"

Pertanyaan user: bagaimana membuat agent patuh — apakah lewat prefill prompt?

**Jawaban inti:** prompt bukan mekanisme kepatuhan, hanya menggeser
probabilitas. Kepatuhan ditegakkan di 4 lapis, dari terkuat ke terlemah:

```
Lapis 0  Bentuk action space   — model tidak pernah keluarkan SQL, hanya
                                  {template_id, params}; SQL injection oleh
                                  model tidak bisa terjadi
Lapis 1  Structured output     — JSON wajib, gagal parse → repair 1x → abstain
Lapis 2  Gate sebelum kirim    — scripts/answer_gate.py
Lapis 3  Prompt                — nada, format, kapan bertanya (paling lemah)
```

**`scripts/answer_gate.py`** dibangun sebagai penegak Lapis 2:
- `check_numeric_grounding`: setiap angka di jawaban wajib tertelusur ke
  evidence atau derived result; menerima varian skala ("451 ribu") dan
  pembulatan, menolak angka yang tidak ada sumbernya
- `check_unit_publishable`: unit `review_required`/`unknown_review` tidak
  pernah sampai ke user
- `check_selection_envelope`, `check_evidence_declared`,
  `check_period_disclosed` ("terbaru" wajib menyebut tahun), `check_citations`
- `abstention_text()` dengan `NoDataReason` — teks "tidak ada" yang spesifik
  per alasan (tidak di katalog / beda periode / unit under review / gate
  blocked), bukan satu kalimat generik

**Bug yang ditemukan saat itu juga:** regex angka lama berakhir dengan
lookahead `(?![\w.,])`, sehingga angka yang **menutup kalimat** (diikuti
titik) — posisi paling umum di Bahasa Indonesia — tidak pernah match sama
sekali. Angka karangan di posisi itu invisible ke gate. Diperbaiki dengan
mewajibkan grup 3-digit penuh pada alternatif pemisah-ribuan.

`AGENT.md` diperbarui: peta "aturan → tempat penegakan", draft system prompt
runtime (~300 kata), loop repair (satu kali, lalu abstain, tanpa pernah
mengirim jawaban benar ke model saat repair).

---

## 4. Desain dashboard, handover, dan role (respons ke 3 permintaan berurutan)

### 4a. Dashboard & handover (permintaan pertama)
Ditulis §0–§0.10 di `docs/06`: handover Slice 1 (fallback eksplisit + guard
takeover dari HP), pembalikan model ancaman (bot = data publik, dashboard =
data pribadi warga), retensi direframe dari "selamanya" jadi agregat vs
mentah dengan retensi pendek.

### 4b. Role superadmin/admin (permintaan kedua, spesifikasi user)
User menentukan: superadmin CRUD role + QR pairing + dashboard + timeout +
setelan agent + chat; admin cuma chat. Diaudit dan ditambah:
- Status koneksi WhatsApp harus terlihat **kedua** peran (bukan cuma
  superadmin) — admin yang pertama sadar ada yang salah
- Audit log **append-only**, tidak bisa dihapus siapa pun termasuk superadmin
- Peringatan: akun superadmin tidak boleh dipakai bersama
- Break-glass: recovery code + CLI reset + minimal 2 akun superadmin
- **QR pairing** ditandai sebagai halaman paling berbahaya di dashboard —
  skenario serangan QR palsu dari dashboard yang dikompromikan, mitigasi
  notifikasi pairing lewat WA petugas lain (bukan dashboard)
- **Setelan agent AI**: daftar putih (model, temperature 0.0–0.3, teks) dan
  daftar hitam permanen (tidak pernah ada toggle mematikan answer_gate)
- **Setelan timeout**: semua nilai wajib rentang min/maks, divalidasi server-side

### 4c. Daftar 14 open question (permintaan ketiga)
Disusun berdasarkan siapa yang menjawab: 7 keputusan user sendiri, 3 tanya
PST, 2 pimpinan, 2 IT. User menjawab seluruhnya dalam satu balasan; semua
dicatat di `docs/15` dengan konsekuensi teknisnya masing-masing (mis.
`pairing_cutoff_ts` wajib karena nomor lama dipakai ulang; bot 24/7 berarti
teks handover tidak boleh menjanjikan balasan segera).

---

## 5. Implementasi logika runtime

Permintaan user: "gaskan semuanya" untuk 3 modul (probe → outbox → formatter),
lalu model **toggle** untuk handover (bukan claim/return).

| Modul | Isi |
|---|---|
| `probe_model_capabilities.py` | Probe OQ-05: structured output, prefill, tool calling, tekanan halusinasi, latency — untuk endpoint OpenAI-compatible (Gemini/DeepSeek) |
| `outbox_worker.py` | Idempotency key (memuat `state_version`), retry/backoff finite, lease timeout, **timeout diparkir sebagai UNKNOWN — tidak pernah di-retry membabi buta** (kasus paling berbahaya: WhatsApp mungkin sudah kirim) |
| `answer_formatter.py` | Format jawaban gaya `docs/18`, angka format Indonesia, **menolak mencetak unit yang belum pasti** — aturan keras sampai ke formatter, bukan cuma gate |
| `conversation_state.py` | State machine **toggle**: `HANDOVER_ON/OFF`, auto-revert (toggle lupa dimatikan), `resume_watermark` (bot tidak menyembur jawab backlog), kill switch global, round-robin antar-percakapan, debounce notifikasi |

**Audit pertama terhadap `conversation_state.py` sendiri** (5 bug, semua
diperbaiki sebelum modul dianggap selesai):
- **A**: takeover dari HP membuat percakapan tanpa pemilik → macet selamanya
- **B**: idle timeout menutup percakapan yang sedang `QUEUED` — menghukum
  warga karena kelambatan kita sendiri
- **C**: notice "sesi berakhir" tidak mungkin terkirim (circular: diblokir
  oleh state yang baru saja dimasuki notice itu sendiri)
- **D**: `{form_url}` mentah bocor ke chat kalau pemanggil lupa format
- **E**: guard versi tidak mencegah **dua agent run bersamaan** dari orang
  yang sama — pesan beruntun menghasilkan dua jawaban

**Aturan keras eksplisit** (setelah user menegaskan "kalau ngarang angka itu
gaboleh, wajib bold"): banner ATURAN KERAS #1 dipasang di `AGENT.md`,
`README.md`, `docs/00-INDEX.md`. Ditulis tes yang mencoba 4 fraseologi
berbeda untuk angka karangan — satu lolos karena bug regex di atas,
diperbaiki, semua sekarang tertangkap.

**Koreksi arah setelah kritik user** ("ini agent bukan bot kaku"): 3 gate
yang ternyata menegakkan gaya (bukan fakta) dicabut — batas 1 pertanyaan
klarifikasi, wajib Bahasa Indonesia, aturan sembarang "bilangan kecil 0-10
boleh". Diganti `system_counts`: runtime mendeklarasikan hitungan yang ia
tahu, sehingga narasi alami ("ada 3 tabel") lolos sementara statistik
karangan ("naik 7 persen") tetap diblokir. Prinsip ditulis di `AGENT.md`
§0-BATAS: *"Ini menangkap FAKTA yang salah, atau cuma GAYA yang tak terduga?"*

---

## 6. Wiring FastAPI dan lima putaran audit adversarial

### 6a. `scripts/app.py` — 16 rute HTTP nyata
Menyatukan semua modul di §5 di belakang satu `Store` in-memory (interface
siap diganti PostgreSQL). Endpoint webhook, dashboard (list/thread/handover
toggle/reply), setelan superadmin, status WhatsApp, audit log.

**Bug ditemukan langsung saat pertama kali dijalankan lewat `TestClient`:**
1. `should_run_agent` dipanggil **setelah** `apply()` — `apply()` sendiri
   men-set `agent_run_active=True`, jadi pengecekan selalu membaca "sudah
   berjalan" dan bot tidak pernah menjawab apa pun
2. Header auth hilang → 422 (bukan 401) — membocorkan nama header yang
   diharapkan ke pemanggil belum terautentikasi

### 6b. Sweep + notifikasi
`scheduler.py` (timeout tidak jalan sendiri tanpa yang memeriksa) dan
`notifications.py` (efek `notify_*` jadi kiriman sungguhan). Ditemukan 2 bug
lagi:
3. Dua jalur notifikasi tidak saling kenal — satu pakai string di `effects`,
   satu pakai boolean field terpisah `Transition.notify_officers`; dispatcher
   cuma baca yang pertama
4. **Tabrakan nama kelas `Settings`** — di-import dari `conversation_state`,
   lalu didefinisikan ulang sebagai model Pydantic dengan nama sama;
   definisi kedua menimpa yang pertama di namespace modul tanpa peringatan
   apa pun dari Python

### 6c. Audit `app.py` — 5 bug (permintaan: "sepertinya bakal banyak bug")
5. **Lost update**: guard versi ada di dalam `apply()`, penulisan ke store
   statement terpisah — dua request bisa sama-sama lolos guard dan menulis
   versi yang sama, dua petugas sama-sama yakin memegang percakapan
   - *Catatan metode penting*: tes concurrency pertama pakai `TestClient`
     dua-thread dan **lolos** — false negative, karena `TestClient`
     menyerialkan request lewat satu portal. Bug baru terbukti setelah
     `Store` diuji langsung
6. Endpoint `/internal/*` tanpa autentikasi sama sekali
7. Idempotency key dari isi pesan → petugas ketik "ok" dua kali, yang kedua
   ditelan diam-diam
8. Timestamp naive vs aware → `TypeError` → 500 ke bridge → bridge retry
   selamanya
9. Header signature dideklarasikan, tidak pernah diverifikasi — endpoint
   **terlihat** terautentikasi saat review padahal tidak memverifikasi apa pun

### 6d. Audit adversarial penuh (permintaan: "audit full")
6 bug di formatter/outbox/state/scheduler:
10. Daftar kandidat kosong tetap menyuruh jawab `"D1"` yang tidak pernah
    ditampilkan
11. Judul tabel BPS dirender apa adanya — satu tanda bintang/newline
    merusak format WhatsApp dan bisa membuat warga memilih tabel yang salah
12. `resolve_unknown()` cuma cocok lewat `wa_message_id` — tidak terjangkau
    justru di kasus timeout (yang tidak punya id sama sekali)
13. Record yang sudah `FAILED` masih bisa dibangkitkan callback telat
14. Idle timeout menutup percakapan yang belum pernah aktif
15. **`plan_sweep()` crash di satu percakapan bertimestamp naive membatalkan
    seluruh pass** — timeout SEMUA percakapan lain berhenti bekerja tanpa
    ada error yang terlihat

*Koreksi metode:* dugaan awal (digit non-ASCII lolos gate) **salah** —
`Decimal` mem-parsingnya dengan benar dan gate memblokirnya. Tapi penjelasan
pertama yang ditulis saat verifikasi juga sempat keliru ("regex tidak
match") padahal regex-nya match. Dicatat sebagai koreksi eksplisit di
dokumen, karena alasan salah dalam dokumen keamanan lebih berbahaya daripada
tidak ada penjelasan.

### 6e. Audit binder + registry builder + scorer (permintaan: "kan udah gw
suruh audit semuanya")
4 bug lagi:
16. `row_limit=None` lolos kedua pemeriksaan → terikat sebagai `LIMIT NULL`
    → di PostgreSQL berarti **TANPA BATAS**, kebalikan dari maksudnya
17. `NaN`/`Infinity` sebagai parameter numeric — mengalahkan semua
    perbandingan batas
18. `jsonb` tanpa batas kedalaman/jumlah node
19. **Placeholder unit upstream** (`"-"`, `"NULL"`, `"N/A"`) dianggap satuan
    sungguhan — menembus ATURAN KERAS lewat pintu yang tidak dijaga

*Lubang audit yang dikoreksi:* dua tes scorer awalnya di-skip karena modul
mengimpor `workers.ingestion.*` yang tidak ada di bundle. Skip bukan
jawaban — fungsi murninya diangkat keluar agar bisa diuji tanpa dependency,
mengungkap bahwa fuzzy matching yang menulis ulang kata nyata jadi kata nyata
lain (mis. `kematian` → `kelahiran`) lebih berbahaya daripada tanpa toleransi
sama sekali. Tes khusus dibuat menjaga pasangan itu.

---

## 7. Status: planning-stage yang bisa dieksekusi, bukan produksi

Ditegaskan eksplisit ke user saat ditanya: kode di `scripts/*.py` **nyata dan
bisa dijalankan** — 163 tes lulus dengan `pytest`, bukan pseudocode. Tapi:

| Sudah ada | Belum ada |
|---|---|
| Logika state machine, gate, outbox, formatter | Baileys / koneksi WhatsApp sungguhan |
| 16 rute HTTP nyata (FastAPI + TestClient) | PostgreSQL untuk `Store` (masih in-memory) |
| Migrasi SQL 006 tertulis + terverifikasi statis | Migrasi belum pernah dijalankan ke DB live |
| Script probe kapabilitas model | Probe belum dijalankan (butuh API key user) |
| Auth placeholder (header `X-Admin-Id`) | TOTP / sesi asli |

**Insiden keamanan di tengah sesi:** user menempel API key Gemini asli di
chat. Ditolak untuk dipakai/disimpan, diarahkan rotate segera, dan
dijelaskan kenapa Claude secara teknis maupun kebijakan tidak akan
menjalankan credential yang diketik langsung di percakapan.

---

## 8. Laporan verifikasi untuk dijalankan user

Karena migrasi 006, rebuild registry, dan probe model butuh PostgreSQL/API
key live yang tidak tersedia di lingkungan ini, disusun
`LAPORAN-VERIFIKASI.md` berisi:
- Urutan 6 langkah dengan level risiko masing-masing
- **Verifikasi statis tambahan** dilakukan sebelum menulis laporan: logika
  gate dataset dijalankan lawan data sintetis yang meniru bentuk BPS nyata,
  membuktikan classifier bekerja benar tanpa DB
- Peringatan bug urutan yang ditemukan lewat pembacaan ulang: migrasi 006
  menjalankan `ADD CONSTRAINT` sebelum `UPDATE` backfill — berpotensi gagal
  kalau ada data lama yang melanggar, dengan SQL workaround disertakan
- Instruksi eksplisit: "jumlah measure non-queryable akan naik — itu
  perbaikan bekerja, bukan regresi"
- Daftar yang harus dikirim balik untuk melanjutkan pekerjaan

---

## 9. Angka akhir

| Metrik | Nilai |
|---|---|
| Total tes lulus | **163**, 0 gagal |
| File `scripts/` baru/ditulis-ulang signifikan | 9 (~2.760 baris) |
| File `tests/` baru | 9 |
| Migrasi SQL baru | 1 (006, dengan `.down.sql`) |
| Dokumen markdown disinkronkan | 16 |
| ADR baru | 3 (015, 016, 017) |
| Bug ditemukan lewat audit & diperbaiki | **21** |
| Putaran audit adversarial | 5 |
| Tes yang gagal-duluan lalu dibuktikan fix (bukan cuma ditulis) | seluruh 21 bug |

**Pola yang berulang di semua 21 bug**, dicatat eksplisit di beberapa titik
karena nilainya berlaku umum: hampir tidak ada yang salah logika di dalam
satu fungsi. Yang bocor selalu **jahitan** — antar-modul, antar-tipe data,
antar-asumsi yang masing-masing terlihat wajar sendiri-sendiri. Dan sebuah
tes hijau hanya berarti sesuatu kalau ia pernah terbukti bisa merah — dua
kali dalam sesi ini tes yang "lolos" ternyata false negative sampai
diverifikasi ulang dengan cara yang berbeda.
