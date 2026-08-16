# MARAWA AI — Service Menu, Session Timeout, dan Admin Handover Flow

> Status: planning (approved 15 Aug 2026). **Belum ada runtime yang menjalankan flow ini, dan Slice 1 sengaja tidak membangun sebagian besarnya.**
>
> **Pembagian Slice (ADR-017).** Dokumen ini mendeskripsikan target akhir. Yang
> masuk **Slice 1** hanya: menu orientasi, intent dari ucapan natural, discovery
> → pilih tabel → query, idle timeout 5 menit, dan fallback "kirim nomor
> petugas lalu berhenti". Yang ditunda ke **Slice 2**: antrean handover, tombol
> Ambil Alih di dashboard, SLA 180 detik, busy notice, dan seluruh race
> condition di §5 — semuanya mahal dibangun dan diuji, dan sebelum ada pengguna
> nyata kita belum tahu apakah antreannya dibutuhkan sama sekali.
>
> **Catatan uji (audit C1a).** Episode `012`–`019` di `packages/evals` menguji
> flow dokumen ini. Karena session-policy engine belum ada, 12 dari 46 turn
> berstatus `blocked_no_session_policy_engine` dan episode `014`–`017` tidak
> punya satu pun assertion yang bisa dieksekusi. Harness lama melaporkannya
> sebagai "19/19 PASS"; itu keliru dan sudah diperbaiki. Jangan membaca episode
> ini sebagai bukti flow-nya bekerja.
> Prinsip utama: **MARAWA adalah agent AI, bukan bot kaku.** Menu hanyalah penunjuk arah awal — user boleh bicara natural dan melompat antar layanan kapan saja. Yang berbentuk aturan ketat HANYA hal operasional (idle timeout 5 mnt, SLA handover admin 3 mnt, bukti seleksi sebelum query) dan keamanan.
> Terkait: `docs/02-AGENT-RUNTIME.md`, `docs/06-DASHBOARD-AND-HANDOVER.md`, `docs/13-ADR.md`, `packages/contracts/bps-query-contracts.schema.json`, `packages/evals/bps-agent-query-episodes.json`.

## 1. Agent-first principles

1. **Intent dari ucapan, bukan dari tombol.** Nomor 1–5 adalah pintasan, bukan satu-satunya jalan. "Aku mau tanya data padi", "admin dong", "ini definisinya jadi angka gimana?" langsung diproses ke tujuan yang cocok.
2. **Tanpa paksaan memilih menu.** Menu hanya muncul di awal sesi sebagai orientasi, atau saat intent benar-benar ambigu. Kalau user sudah jelas (mis. "produksi padi 2025"), agent langsung ke discovery — tidak menyuruh kembali ke menu.
3. **Lompat layanan bebas.** Data → tanya konsultasi → minta admin — agent mengikuti percakapan, membawa konteks aktif, tanpa memaksa menyelesaikan "layanan" sebelumnya.
4. **Multi-intent ditangani bertingkat:** agent mengejar primary goal; layanan tambahan (form, konsultasi, admin) ditawarkan sebagai opsi di akhir, bukan memotong.
5. **Cancel kontekstual.** Kata kunci tetap dikenali (`batal`, `cancel`), tetapi frasa natural ("gajadi deh", "skip", "lanjut aja") dipahami sebagai niat membatalkan/mengalihkan — agent mengapung sesuai arah baru user, bukan menolak karena di luar daftar.
6. **Kebijakan yang TETAP ketat** (bukan kekakuan, tapi kejujuran data & keamanan):
   - jangan query database sebelum user memilih kandidat tabel (selection envelope wajib);
   - unit tidak ditebak; angka tidak di-coerce; marker non-numeric;
   - evidence/snapshot selalu menyertai angka;
   - daftar tabel dulu, user menentukan, baru ambil data;
   - tidak pernah invent jawaban; kalau tidak bisa → tawarkan admin/form, bukan mengarang.

## 2. Menu pembuka (affordance, bukan state)

Saat sesi baru mulai, bot menyapa dan MENAWARKAN peta layanan (tanpa mengunci):

```text
Halo! MARAWA AI siap membantu data BPS Kabupaten Padang Pariaman.

Anda bisa langsung bertanya, misalnya:
"berapa jumlah penduduk tahun 2025?"

Atau pilih layanan:
1. Permintaan Data / Perpustakaan
2. Rekomendasi Statistik
3. Konsultasi Statistik
4. Chat Admin
5. Keluar
```

Semantik:

- Pesan berikutnya BEBAS: nomor, pertanyaan langsung, atau topik baru — semuanya valid input agent.
- Nomor 1–5 adalah shortcut menuju flow yang sama dengan ucapan natural ekuivalen:
  - "1" ≈ "aku mau minta data…"
  - "4" ≈ "sambungkan ke admin"
  - "5" ≈ "cukup/makasih"
- Intent ambigu dan tidak jelas arahnya → agent bertanya satu pertanyaan klarifikasi singkat atau menampilkan ulang menu — pilihan agent berdasarkan percakapan, bukan template wajib.
- "Keluar" natural ("cukup", "makasih", "sudah") → `end_session` ucapan penutup.
- Setiap sesi selesai (`end_session`) menutup state; pesan berikutnya dari nomor yang sama memulai sesi baru.

## 3. Pemetaan layanan ke flow (oleh intent, bukan urutan menu)

| Intent user | Flow utama | Fallback (jika bot tidak dapat melayani) |
|---|---|---|
| Permintaan angka/tabel/publikasi | discovery (`offer_candidates` → user select → clarify period/geography → `query_stat_data` via binder) | tawarkan `chat admin` ATAU link form permintaan data |
| "Indikator apa yang cocok…" / rekomendasi | discovery untuk indikator yang cocok + ringkasan rekomendasi | form rekomendasi statistik + `chat admin` |
| "Bisa konsultasi…" / konsep/definisi mendalam | jawab berbasis data/glosarium bila relevan | form konsultasi + `chat admin` |
| "Admin"/"petugas"/"orangnya" | handover queue (lihat §5) | — |
| "Keluar"/selesai | `end_session` | — |

Batas antar layanan cair: agent boleh menyelesaikan permintaan data lalu menawarkan "Mau saya teruskan ke form rekomendasi atau petugas?" tanpa memaksa.

Fallback form: bot mengirim teks singkat + link/URL form (nilai konstan dari application config).

## 4. Session timeout (idle 5 menit)

- **Timer idle, bukan absolute**: `session_idle_timeout_seconds = 300`, di-reset setiap inbound message dari user.
- Timeout dihitung server-side (worker), bukan klien.
- Saat timeout:
  - state → `RESOLVED`;
  - bot mengirim: "Sesi berakhir karena tidak ada balasan selama 5 menit. Kirim pesan apa pun untuk memulai sesi baru." (outbound tetap melewati state/version guard, `docs/13-ADR.md`);
  - tidak ada pesan lanjutan dari sesi lama.
- Selama `ADMIN_ACTIVE`, idle timer bot tidak berlaku (admin yang mengendalikan).

## 5. Chat admin — handover queue

### Alur

1. User meminta admin (langsung/shortcut/fallback) → agent membuat `AdminHandoverRequest` dengan alasan (`user_request`, `data_unavailable`, `consultation`, `service_form`).
2. State → `QUEUED`; bot mengirim **satu** queue acknowledgement:
   "Permintaan Anda diteruskan ke petugas. Mohon tunggu… (bot tidak akan membalas selama menunggu)"
3. Admin dari dashboard menekan **Ambil Alih (claim)**; optimistic transition ke `ADMIN_ACTIVE` dengan guard `409 CONVERSATION_STATE_CONFLICT` bila sudah di-claim.
4. Bot tidak memproses inbound selama `QUEUED`/`ADMIN_ACTIVE`; inbound tetap disimpan dan tampil di dashboard.
5. Selama antrean, user dapat membatalkan — termasuk dengan bahasa natural ("gajadi deh", "skip") — agent kembali menawarkan langkah berikutnya (bukan paksa menu).

### Slice 1: fallback tanpa antrean

Sampai Slice 2, permintaan admin ditangani dengan satu langkah:

```text
User minta admin  →  bot mengirim nama + nomor WhatsApp petugas PST
                  →  bot menutup sesi (RESOLVED) dengan ucapan penutup
```

Tanpa state `QUEUED`, tanpa claim, tanpa timer 180 detik, tanpa busy notice.
Tidak ada janji waktu yang tidak bisa ditepati, dan tidak ada race condition
untuk diuji. Kalau data pemakaian nanti menunjukkan orang benar-benar menunggu
di dalam chat, barulah antrean di bawah dibangun.

### SLA handover 3 menit (Slice 2)

- `admin_handover_timeout_seconds = 180` sejak state `QUEUED`.
- **Admin claim sebelum timeout** → system notice "Percakapan dilanjutkan oleh petugas." lalu bot diam (admin memegang kendali penuh).
- **Tidak ada claim dalam 3 menit** → state `RESOLVED` + bot mengirim:
  "Admin sedang sibuk saat ini. Silakan coba lagi nanti. ✓ Batalkan"
  - **Batalkan** (kata kunci ATAU bahasa natural) → kembali ke orientasi/menu (sesi tetap aktif, timer idle reset).
  - Pesan lain setelah notice → agent memprosesnya sebagai arah baru user (intent apapun), bukan error.
- "Coba lagi nanti" = user dapat memicu handover baru kapan saja; tidak ada cooldown bot.

### Race conditions (wajib diuji — Slice 2)

Dari `docs/06` §7 ditambah:

- Admin claim **tepat saat** timer 3 menit kedaluwarsa (guard: state `QUEUED` → claim menang, busy notice batal; state `RESOLVED` → claim ditolak `409`).
- Busy notice terbuat tapi belum terkirim, lalu admin claim (notice dibatalkan, system notice handoff yang dikirim).
- User membatalkan dengan natural language sementara claim sedang diproses — yang pertama menentukan state; sisanya no-op berurut.

## 6. Conversational control model (bukan state machine menu kaku)

```text
                          (intent terdeteksi kapan saja)
        ┌──────────────── INVOICE ────────────────► [discovery/query flows]
        │                                              │  user minta admin/fallback
 SESSION ── sapaan + menu orientasi ──►  QUEUED ── claim ≤180s ──► ADMIN_ACTIVE ── resolve ──► RESOLVED
 START                                   │
        ┌── user bicara intent ──┐       └── 180s tanpa claim ──► busy notice (+batalkan)
        └────────────────────────► berlaku dari MANA PUN ──► (natural cancel) ► orientasi baru
```

Kontrol state yang berlaku hanyalah yang OPERASIONAL:

```text
AI_ACTIVE          agent memproses percakapan secara bebas
CLARIFYING         agent sedang menunggu jawaban user
QUEUED             menunggu claim admin (SLA 180s)
ADMIN_ACTIVE       admin memegang kendali
RESOLVED           sesi selesai (user exit | idle timeout | admin timeout/busy)
```

Tidak ada state "MENU" yang mengunci percakapan; menu adalah output respons ketika dibutuhkan, bukan kondisi yang membatasi input.

## 7. Konfigurasi (bukan hardcode)

```toml
[conversation]
session_idle_timeout_seconds   = 300   # 5 menit tanpa balasan -> chat ended
admin_handover_timeout_seconds = 180   # 3 menit menunggu admin handover
menu_service_count             = 5
form_urls = {
  data_request    = "<dari BPS PST>",
  statistik_rekomendasi = "<dari BPS PST>",
  statistik_konsultasi  = "<dari BPS PST>",
}
```

Form URL dan teks template bot (menu orientasi, ack queue, busy notice, timeout notice) masuk application config yang dapat di-publish lewat dashboard (four-eyes untuk production).

## 8. Golden episodes

`bps-dialog-012..019` di `packages/evals/bps-agent-query-episodes.json`:

| ID | Skenario | Kunci ekspektasi |
|---|---|---|
| 012 | menu → pilih 1 → permintaan data penduduk | `show_service_menu` → discovery `offer_candidates` (S/D/C) |
| 013 | rekomendasi statistik | menu → rekomendasi data + tawaran form/chat admin |
| 014 | konsultasi statistik | fallback form + `request_admin_handover` |
| 015 | chat admin → handover sukses ≤ 3 mnt | `request_admin_handover` → `admin_active` notice |
| 016 | chat admin → timeout 3 mnt | `admin_busy_notice` + offer_cancel → `batal` → orientasi |
| 017 | idle 5 menit | event `session_idle_timeout` → `end_session` + ucapan penutup |
| 018 | **user langsung bertanya tanpa pilih nomor** | tanpa menu recall → `offer_candidates` langsung |
| 019 | **cancel natural saat antrean** | "gajadi deh, mau cari data beras" → langsung discovery baru |

Struktur turn: turn boleh berupa `event` (timer) tanpa `user`; field `event` bernilai `session_idle_timeout | admin_handover_timeout | admin_claimed`.

## 9. Kontrak JSON

`packages/contracts/bps-query-contracts.schema.json` menambah defs:

- `ServiceMenuResponse` — daftar layanan + `session_idle_timeout_seconds` + prompt; field `advisory`: menu bersifat orientasi, bukan mandatory;
- `AdminHandoverRequest` — alasan handover + sumber (user/tugas sistem);
- `AdminHandoverOutcome` — `state ∈ admin_active | admin_busy | cancelled`, notice, `offer_cancel`;
- `SessionEndNotice` — `reason ∈ user_exit | idle_timeout | admin_timeout_end`, `goodbye_message`.

## 10. Explicit non-goals (planning ini)

- Belum mendesain tombol interaktif WhatsApp Banner/List; teks + agent NLU cukup untuk fase pertama.
- Tidak ada multi-admin round-robin; claim manual per dashboard (`docs/06`).
- Tidak ada SLA jawaban admin setelah claim (SOP PST).
