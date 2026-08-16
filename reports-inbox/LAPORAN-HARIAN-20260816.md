# Laporan Harian — MARAWA AI · 16 Agustus 2026

**Operator:** Tah · **Eksekutor:** Lord (Hermes) · **Host:** VPS3 (43.133.153.252)
**Status akhir:** Deploy penuh LIVE di `https://marawa.hatafisme.web.id` — 3/3 arah operator selesai.

---

## 1. Ringkasan eksekutif

Hari ini MARAWA bergerak dari "foundation database + planning" ke **sistem
runtime produksi yang bisa dipakai**: Postgres store live, API + worker
systemd, dashboard dengan login password, QR pairing tampil di dashboard,
notifikasi fanout ke nomor petugas, model LLM terpilih dan ter-evaluasi
(hybrid eval hard 18/18 clean), domain publik HTTPS aktif via Cloudflare.
Semua klaim diverifikasi lawan PostgreSQL asli dan endpoint publik — bukan
output tercollect.

## 2. Yang dikerjakan (urutan)

### 2.1 Remediasi bundle `marawa-docs-fixed-2026-08-15-3`
- Migrasi **006** applied produksi (unit provenance + serving clarity).
- Fix 3 bug bundle: join `bps_simdasi_units` salah kolom, enum constraint
  kurang, dedup SIMDASI.
- Hasil: 51 measures jadi `NOT queryable` (sebelumnya invisible), 27 dataset
  `blocked_quality`, ledger 001–006.

### 2.2 Bundle `marawa-postgres-store`
- Migrasi **007** (6 tabel runtime) applied; `scripts/postgres_store.py`
  (CAS `WHERE state_version`, `SKIP LOCKED`, dedup index) **11/11 tes lawan
  DB nyata**.
- Bug U fixed: `SendRecord.sender_admin_id` (guard reassign mati).

### 2.3 Deploy item §9 laporan internal
| Item | Hasil |
|---|---|
| #1 DI | `app.py` → PostgresStore via `MARAWA_RUNTIME_DSN` |
| #2 Worker | Node + Baileys (pin 7.0.0-rc14), systemd live |
| #3 Dashboard | satu-file di `/admin` (login→inbox→takeover→balas) |
| #4 Auth | TOTP stdlib + sesi Bearer → **diganti password** (2.7) |
| #5 Retensi | `apply_retention(365)` + paragraf keputusan OQ-11 |
| #6 Probe | **RESOLVED** — lihat 2.5 |
| #7 Field test | menunggu pairing QR + unit review BPS |

Migrasi **008** `marawa_runtime_rw_role` + seed 2 superadmin; env
`/etc/marawa/` (0600). Smoke: login → token → `/conversations` 200.

### 2.4 Bundle final 16-Agu (staff contact + security)
- Migrasi **009**: `marawa_admin_contacts` + `is_staff_channel` — filter nomor
  petugas sebelum percakapan dibuat, staff-thread tak muncul di inbox/sweep,
  fanout notifikasi per petugas (idempotency key per tujuan), CRUD dashboard.
- Audit **V/W/X**: webhook HMAC (X-Marawa-Signature + secret dari env),
  production fail-closed (`MARAWA_ENV=production` + startup check), TOTP
  rate limit per akun+IP. `scripts/prompt_cache.py` + `rate_limit.py`.
- **Remediasi merge** (ditemukan via verifikasi DB nyata + subagent):
  GRANT 009 untuk rw role, fanout buat thread staff sebelum enqueue (FK),
  PostgresStore wiring FanoutChannel + webhook_secret, upsert reactivate
  soft-deleted contact, fix prefix `00` phone.
- Audit subagent **STAFF-001..009** → semuanya ditutup:
  staff-policy lookup **fail-closed** (503, bukan "bukan petugas"), channel
  notifikasi return bool → `last_notified_at` akurat, Baileys v7 **LID** →
  `remoteJidAlt`.

### 2.5 OQ-05 — provider model (blocked → LIVE)
- Key Google Gemini: `gemini-2.5-flash` → 404 (model retired untuk user baru);
  **`gemini-3.5-flash-lite` = USABLE** (json_schema, tool calling, latency
  ~0,8s, menolak halusinasi). Diputuskan sebagai model produksi.
- **Eval LLM HYBRID** (observation + tool-calling + action masking):
  action-only 4/19 → hybrid **HARD 18/18 clean** (0 forbidden/masking/infra),
  1 not-evaluated; soft mismatch 28 → review manusia (anti-overfit; fixture
  tidak disentuh). Fix: Gemini replay butuh `thought_signature`/raw object;
  selection dari user menyebut ref.

### 2.6 Arah operator (3/3 selesai)
1. **Login password**: migrasi `010_admin_password` + `scripts/password_auth.py`
   (PBKDF2-HMAC-SHA256 stdlib, 210k iterasi); TOTP opsional; `/admin/set-password`.
2. **QR di dashboard**: worker push QR/status → `marawa_settings` → panel
   Setelan → WhatsApp (PNG via `qrcode[pil]`, tombol muat ulang manual).
3. **DNS Cloudflare**: token `cfat_` (format baru 2026) valid → record
   `marawa.hatafisme.web.id` → **43.133.153.252** → **HTTPS LIVE**.

### 2.7 Dokumen rombak frontend
- `docs/29-FRONTEND-SPEC.md` — kontrak API, auth, state UI, perilaku, roadmap
  Next.js (sumber kebenaran backend).
- `docs/30-FRONTEND-KOSMETIK.md` — token visual, komponen per state, motion,
  responsive, aksesibilitas, checklist.

## 3. Verifikasi (bukti nyata)

```text
Python suite       385/385 PASS (+16 skip tanpa DSN) — lawan PostgreSQL asli
Node (worker)      9/9 PASS
Migrasi            001–010 applied produksi; 009 & 010 UP→DOWN→UP isolated
Validator          docs PASS · 13/13 prototypes · privilege checker PASS
Probe model        gemini-3.5-flash-lite usable + anti-halusinasi
Eval LLM           hard 18/18 clean · 0 forbidden/masking/infra
HTTPS publik       /admin 200 · no-auth 401 · login password 200 ·
                   settings/whatsapp QR present · QR PNG 200 (2196 B)
Smoke runtime      CRUD kontak via role runtime · webhook no-sig 401 ·
                   fanout outbox pending + is_staff_channel=true (artefak dibersihkan)
```

## 4. Aset & kredensial (jangan disebar)

| Aset | Lokasi |
|---|---|
| Env runtime | `/etc/marawa/marawa.env` (0600) |
| Password seed-super-1 | `/etc/marawa/admin-passwords.txt` (0600) |
| TOTP seed (legacy) | `/etc/marawa/totp-seed-super-1.txt` (0600) |
| Cloudflare token + zone IDs | `/etc/marawa/cloudflare.env` (0600) |
| Dashboard | `https://marawa.hatafisme.web.id/admin` (seed-super-1) |

⚠️ Password seed dan token CF sempat tampil di chat — **disarankan rotasi**
(set-password / CF roll) setelah dipakai.

## 5. Commit & tag hari ini

```text
e46fffe…1ef6886   hybrid eval + masking + probe
f46da78           bundle final (009, audit V/W/X, prompt cache)
bbf27a7           login password + QR dashboard + STAFF close
0c3c6e1, 8db43d9  docs + DNS resolved
0c3c6e1+           docs/29, docs/30
Tag               remediasi-20260816-3 … -7
```

## 6. Blocker tersisa (eksternal)

1. **Pairing WhatsApp** — QR live di dashboard, tinggal di-scan (HP bot).
2. **Nomor petugas** — belum terisi (notifikasi antrean mati sampai diisi).
3. **OQ-07** (internal views) + **unit review BPS** (13 dataset `blocked_quality`)
   — menunggu data owner.
4. Rotasi kredensial yang bocor di chat (password seed, token CF).

## 7. Next steps yang disarankan

1. Scan QR + isi nomor petugas → tes E2E warga: webhook → percakapan →
   notifikasi fanout → dashboard.
2. Rombak frontend (Next.js) pakai docs/29 + docs/30.
3. Runtime agent loop: masking + candidate board → binder query → answer-gate
   → outbox (komponen 80% sudah ada; tinggal loop nyata vs model).
