# MARAWA — Spesifikasi Frontend (Dashboard Petugas PST)

**Versi:** 1.0 — 16 Agustus 2026
**Status:** dasar rombak full frontend. API backend = source of truth; UI
bebas dirombak selama kontrak API (bagian 4) tidak berubah.
**Implementasi saat ini:** `apps/dashboard/index.html` — satu berkas vanilla
JS/HTML/CSS tanpa build step, diserve oleh FastAPI `GET /admin`.
**Target arsitektur (AGENTS.md):** `apps/dashboard/` Next.js App Router,
TypeScript strict, pnpm, permission checks server-side. Migrasi bertahap;
jangan pecah API.

---

## 1. Produk & pengguna

Panel dipakai **3 petugas PST** di loket BPS Kabupaten Padang Pariaman. Mereka
melayani orang di meja dan **sesekali menengok layar** — bukan duduk memandang.

> Tugas tunggal halaman: **"Siapa yang sedang menunggu manusia, dan sudah
> berapa lama."**

Konsekuensi desain:

1. Daftar percakapan **bertingkat menurut urgensi**, bukan daftar seragam.
2. Satu-satunya elemen besar & berwarna panas di layar = kartu "menunggu".
3. Semua keputusan visual boleh berubah; **kontrak API tidak boleh berubah**.

## 2. Halaman & alur

```text
/                        → redirect /admin
/admin                   → SPA tunggal (login → panel)
```

| View | Trigger | Isi |
|---|---|---|
| Login | belum ada token / sesi 401 | form admin_id + password (+ TOTP opsional) |
| Panel (default) | login sukses | 3 kolom: queue (urgensi) · thread (percakapan) · info |
| Setelan | tab/nav | WhatsApp (QR+status) · timeouts · nomor petugas · agent (read-only) |
| Audit | tab/nav | tabel audit-log superadmin |

Semua view dalam satu halaman SPA (state view di JS), **bukan** multi-route.

## 3. Auth

### 3.1 Login (jalur utama — password)

```http
POST /admin/login
Content-Type: application/json
{ "admin_id": "seed-super-1", "password": "…", "totp_code": null }
```

- `200` → `{ "token": "<jwt-like session>", "admin_id": …, "role": "superadmin" }`
- `401` → kredensial salah (pesan seragam, jangan bedakan user vs password)
- `429` → rate limit (header `Retry-After`; tampilkan detik)
- `503` → sesi dinonaktifkan (MARAWA_SESSION_KEY belum di-set)

### 3.2 Sesudah login

- Simpan token di `sessionStorage` (hilang saat tab ditutup = lebih aman).
- Semua request API: header `Authorization: Bearer <token>`.
- **Setiap respons `401`** → `logout()` → kembali ke view login.
- `GET /admin/session` → `{ admin_id, role }` untuk verifikasi awal.

### 3.3 Operasional superadmin

- `POST /admin/set-password` `{ admin_id, password }` — ganti/reset password
  admin lain (min 8 karakter).
- `POST /admin/enroll-totp` `{ admin_id }` → `{ otpauth_uri }` — TOTP opsional.

## 4. Kontrak API (backend = otoritas)

Auth: Bearer. Format error: `{ "detail": … }`. Kode khusus:

- `401` sesi tidak valid → logout
- `403` bukan superadmin → sembunyikan tombol/endpoint
- `409` `{"code":"CONVERSATION_STATE_CONFLICT",…}` → versi bentrok → **re-fetch
  percakapan lalu ulangi aksi** (jangan diam)
- `409` duplikat kirim → pesan "sudah diproses"
- `422` validasi (settings di luar rentang, nomor invalid, label kosong)

### 4.1 Percakapan

```http
GET /conversations                      # superadmin+admin
→ [{ conversation_id, state, state_version, assigned_admin_id,
     bot_paused_by, last_activity_at }]

GET /conversations/{conversation_id}
→ { "conversation": { <semua field ConversationState> },
    "messages": [ { message_id, conversation_id, direction, sender_type,
                    sender_admin_id, body, wa_message_id, status, created_at } ] }

POST /conversations/{id}/handover/on    # ambil alih
{ "expected_version": int }             → { state, state_version }

POST /conversations/{id}/handover/off   # lepas kembali ke bot
{ "expected_version": int }             → { state, state_version }

POST /conversations/{id}/messages       # balas sebagai petugas
{ "body": str, "expected_version": int,
  "client_request_id": "<uuid per aksi kirim>" }
→ { outbox_id, status }
```

Aturan balas:

- **`client_request_id` WAJIB uuid baru per klik kirim.** Retry kiriman yang
  sama memakai uuid yang sama → server menolak duplikat (`409`). Tanpa ini,
  petugas mengetik "ok" dua kali = pesan kedua hilang senyap (audit H).
- Kirim tombol harus disabled selama request; kegagalan = re-enable + toast,
  jangan auto-retry dengan uuid baru.

### 4.2 ConversationState (field penting UI)

```text
conversation_id        JID WhatsApp (62812…@s.whatsapp.net)
state                  BOT_ACTIVE | QUEUED | ADMIN_ACTIVE | IDLE_CLOSED
state_version          int — WAJIB dikirim balik di handover/reply
assigned_admin_id      admin yang pegang (ADMIN_ACTIVE)
bot_paused_by          admin yang mendiamkan bot (ADMIN_ACTIVE)
last_activity_at       ISO8601 UTC — dasar hitung "menunggu"
handover_requested_at  ISO8601 UTC — saat warga minta petugas (mulai timer)
last_notified_at       kapan notifikasi petugas terakhir
```

### 4.3 State → perilaku UI

| State | Arti | UI |
|---|---|---|
| `BOT_ACTIVE` | dijawab bot | baris tipis, tanpa tombol ambil-alih |
| `QUEUED` | menunggu petugas | **kartu besar + timer**, tombol "Ambil alih" |
| `ADMIN_ACTIVE` | dipegang petugas | baris/thread, tombol "Lepas ke bot"; balasan hanya oleh pemegang (`bot_paused_by == me`) |
| `IDLE_CLOSED` | selesai / timeout | baris paling redup, bisa dibuka read-only |

### 4.4 Setelan (superadmin)

```http
GET  /settings/timeouts                 → { citizen_idle_minutes, queue_expiry_minutes,
                                           handover_sla_minutes, handover_auto_revert_minutes, … }
PUT  /settings/timeouts { "values": {…} } → validasi server; 422 di luar rentang

GET  /settings/admin-contacts           → [{ contact_id, phone_e164, display, label,
                                            admin_id, notify, active, created_at }]
POST /settings/admin-contacts { phone, label, notify } → 409 duplikat, 422 invalid
DELETE /settings/admin-contacts/{id}    → soft-delete (active=false)

GET  /settings/whatsapp                 → { qr, qr_expires_at, connected, connection_state }
GET  /settings/whatsapp-qr.png          → PNG (pakai Authorization; fetch→blob→objectURL)

GET  /settings/agent                    → { note, editable, never_editable } (read-only!)
POST /settings/bot-global-switch?enabled=true&reason=…   → { enabled }
GET  /audit-log                         → [{ audit_id, at, action, admin_id, conversation_id, detail }]
```

### 4.5 Worker/status (dashboard boleh pakai)

```http
GET /status/whatsapp
→ { status, connected, pending_count, bot_globally_enabled }
```

## 5. Perilaku panel (wajib dipertahankan)

1. **Polling data 5 detik** (daftar + thread aktif). Timer "menunggu" berdetak
   **tiap detik di klien** dari `handover_requested_at` — jangan polling 1 Hz.
2. **Kontrol manual eksplisit** untuk QR (tombol "Muat ulang QR") — preferensi
   operator: jangan auto-polling untuk hal yang jarang berubah.
3. **Urgensi**: pita kiri menebal (5/8/12px) + denyut di level tertinggi —
   terbaca dari seberang meja & ramah buta warna (ketebalan, bukan warna saja).
4. **Empty state itu ajakan**: "Tidak ada yang menunggu. Bot sedang menangani
   N percakapan."
5. **Gagal itu arah**: konflik versi → "Percakapan ini sedang dipegang petugas
   lain." (dan re-fetch).
6. **Konfirmasi akibat**: "Mengambil alih akan mendiamkan bot di percakapan ini."
7. `Esc` tutup thread; `Ctrl/Cmd+Enter` kirim; fokus keyboard terlihat;
   `prefers-reduced-motion` dihormati.
8. Semua data → DOM lewat escape (`esc()`); **nol** `innerHTML` mentah, nol
   `onclick` inline, delegasi event (daftar digambar ulang 5 detik).
9. Responsif: pada layar sempit, panel thread jadi overlay penuh.

## 6. Identitas visual (dari docs/28 — boleh dirombak, ini kerangka)

| Token | Nilai | Pakai untuk |
|---|---|---|
| `--waiting` | `#B23C10` bata | menunggu (satu-satunya warna panas) |
| `--human` | `#1B4ACC` biru | dipegang petugas |
| `--bot` | `#05704F` hijau | dijawab bot |
| `--closed` | `#8A96A4` abu | selesai |
| `--paper` | `#EEF1F4` | latar (abu dingin) |

Tipografi: Barlow Condensed (label/eyebrow) · IBM Plex Sans (teks) · IBM Plex
Mono (timer & referensi) — semua fallback sistem; **jangan bergantung CDN**.

## 7. Kata-kata

- Tombol menyebut akibat: **Ambil alih** → toast **Diambil alih**.
- Jangan "maaf", kasih arah: "Percakapan ini sedang dipegang petugas lain."
- Nomor tampil WIB (`Asia/Jakarta`); data API UTC.

## 8. Roadmap rombak (urutan yang disarankan)

1. **Kerangka**: Next.js App Router + TS strict + pnpm; proxy `/api/*` →
   FastAPI :8130 (jangan panggil API dari server component; semua client).
2. **Auth**: cookie httpOnly opsional ATAU tetap Bearer di sessionStorage;
   minimal: tidak melemahkan rate limit & fail-closed backend.
3. **Queue view** (urgenci bertingkat + timer) — port dari index.html.
4. **Thread view** (handover on/off, reply + uuid, konflik 409).
5. **Setelan**: timeouts, admin-contacts, WhatsApp QR (blob fetch).
6. **Audit log** + global bot switch.
7. **Pindah API contracts ke `packages/contracts`** (OpenAPI/types) — ini target
   AGENTS.md; sambil jalan, jangan ubah route backend.

## 9. Larangan (jangan ulangi)

- Jangan ubah `expected_version`/`client_request_id` — itu pengaman anti
  lost-update & dedup (audit F/H).
- Jangan bikin endpoint baru hanya karena UI nyaman — tambah lewat kontrak
  dulu, backend dulu, test dulu.
- Jangan auto-polling QR/switch; manual eksplisit.
- Jangan render HTML dari data tanpa `esc()`.
- Jangan tampilkan token di URL/log.
- Bahasa publik UI: **Indonesia**.

## 10. Kontak & kredensial (produksi 16-Agu-2026)

```text
URL       https://marawa.hatafisme.web.id/admin
Username  seed-super-1
Password  /etc/marawa/admin-passwords.txt (VPS, mode 0600) — jangan commit
```
