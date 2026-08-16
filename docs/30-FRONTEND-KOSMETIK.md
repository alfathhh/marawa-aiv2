# MARAWA — Spek Kosmetik Frontend (Visual & Komponen)

**Versi:** 1.0 — 16 Agustus 2026
**Berkas acuan:** `apps/dashboard/index.html` (CSS berjalan — spek ini
mengekstraknya jadi token & aturan) + `docs/29-FRONTEND-SPEC.md` (kontrak &
perilaku). Kosmetik boleh dirombak total; dua hal ini yang tidak berubah:
**bahasa Indonesia** dan **warna = kode state** (bukan hiasan).

---

## 1. Design tokens

### 1.1 Warna

| Token | Nilai | Peran |
|---|---|---|
| `--paper` | `#EEF1F4` | latar halaman (abu dingin, bukan krem) |
| `--surface` | `#FFFFFF` | kartu, panel, thread |
| `--ink` | `#0F1419` | teks utama, tombol primer |
| `--muted` | `#5B6572` | teks sekunder, label, hint |
| `--rule` | `#D5DBE2` | border, pemisah |
| `--waiting` | `#B23C10` | **satu-satunya warna panas** — menunggu petugas |
| `--waiting-wash` | `#FDF0EA` | latar kartu menunggu / alert |
| `--human` | `#1B4ACC` | dipegang petugas, balasan petugas |
| `--human-wash` | `#EDF1FE` | baris aktif / highlight milik petugas |
| `--bot` | `#05704F` | dijawab bot, balasan bot |
| `--closed` | `#8A96A4` | selesai / tidak aktif / locked |
| hover tint | `rgba(15,20,25,.045)` | hover baris/tab |
| border tint | `rgba(15,20,25,.25)` | border fokus milik petugas |
| alert border | `rgba(178,60,16,.25)` | border alert menunggu |

Aturan: **selain `--waiting`, tidak ada warna panas di layar.** Abu/biru/hijau
hanya mengkode state; dekorasi (gradasi, aksen acak) dilarang.

### 1.2 Tipografi

| Peran | Keluarga | Ukuran | Catatan |
|---|---|---|---|
| Brand / eyebrow / judul / tab / pill | Barlow Condensed | 13–27px, weight 600–700, `letter-spacing .04–.1em`, uppercase | label struktural |
| Teks utama | IBM Plex Sans | 14–15px, lh 1.5 | fallback system-ui |
| Mono (timer, id, nomor, referensi) | IBM Plex Mono | 10.5–19px | **tabular-nums** untuk timer |
| Label form | Barlow Condensed | 12px 600 uppercase ls .1em muted | |

Semua via `font-family` stack dengan fallback sistem — **dilarang dependensi
CDN font** (koneksi kantor kabupaten tidak stabil).

### 1.3 Skala lain

```text
Radius   --r: 6px   (kartu, input, tombol, tab)
         kartu login 10px · panel 8px · bubble 12px (tajam 3px di sisi sumber)
Shadow   --shadow: 0 1px 2px rgba(15,20,25,.06), 0 4px 12px rgba(15,20,25,.04)
Spacing  dasar 4px; gap umum 10–16px; padding kartu 14–18px; panel 20–22px;
         page 26–28px; topbar 56px tinggi
Border   1px var(--rule); kartu menunggu: border-kiri 5/8/12px
```

## 2. Komponen — spesifikasi per state

### 2.1 Topbar (56px, sticky, surface, border-bottom)

```
MARAWA  PST Padang Pariaman        [• memeriksa…]  [Kotak masuk|Setelan|Riwayat]  [siapa] [Keluar]
brand(cond 21px 700)  ·  spacer  ·  conn(mono 12px muted + dot 7px)  ·  tabs  ·  who  ·  logout ghost
```

- `conn dot`: `.ok` hijau (bot) · `.bad` bata (putus) · default abu (memeriksa).
- **Indikator kesegaran** (`.stale`, warna bata): polling gagal → label
  "koneksi terputus — data bisa basi". Papan yang membeku harus TERLIHAT membeku.

### 2.2 Tabs

- Default: muted, hover tint 5%, aktif `.is-on` = ink solid + teks putih, radius 6px.
- "Setelan" & "Riwayat" hanya tampil untuk **superadmin**.

### 2.3 Login

- Card 380px di tengah (grid place-items center, min-height 100%−56px).
- H1 cond 27px; input: 10px 12px, border rule, radius 6px; label cond uppercase.
- Input TOTP: mono 19px `letter-spacing .3em`, center — **opsional** (password = jalur utama).
- Error: `.alert` bata-wash + border `rgba(178,60,16,.25)`.
- Tombol submit full-width; state "Memeriksa…" saat pending (disabled).

### 2.4 Kartu menunggu (elemen tanda tangan — WAJIB dipertahankan)

```
┌▌▌▌ 6281234···                   14:02 ┐
│▌▌▌ "berapa penduduk…"                │
└──────────────────────────────────────┘
```

| Urgensi | Pita kiri | Latar | Animasi |
|---|---|---|---|
| 1 (< 10 mnt) | 5px | surface | — |
| 2 (10–30 mnt) | 8px | waiting-wash | — |
| 3 (> 30 mnt) | 12px | waiting-wash | denyut `breathe 2.6s` (opacity .12↔.5) di border luar |

- Timer mono 26px 600 tabular-nums bata + `small` label (JAM/MENIT).
- Hover: `translateY(-1px)` + shadow membesar.
- **Ketebalan = informasi kedua** selain warna (buta warna merah-hijau).

### 2.5 Baris (row) — selain menunggu

- Flex, padding 9px 12px, radius 6px; tick kiri 3px (state): human (ADMIN_ACTIVE),
  bot (BOT_ACTIVE), abu (default).
- `.active`: human-wash + inset border `rgba(27,74,204,.25)`.
- ID mono 12.5px ellipsis; "by" 12px muted (nama pemegang).

### 2.6 Thread

- Head: mono 16px + **pill state** (cond 12px 600 uppercase, putih di atas
  warna state; QUEUED=bata, ADMIN_ACTIVE=human, BOT_ACTIVE=bot, IDLE_CLOSED=abu).
- Bubble: max-width 74% (88% mobile); meta mono 10.5px uppercase opacity .65.
  - in (warga): paper, radius 12px, sudut kiri-bawah 3px.
  - out (petugas): human solid, teks putih, sudut kanan-bawah 3px.
  - out by-bot: bot solid.
  - teks `pre-wrap`, `word-break: break-word`.
- Composer: textarea min 84px, border rule; disabled → paper. Footer: hint 13px
  muted + tombol kirim. `Ctrl/Cmd+Enter` = kirim.

### 2.7 Tombol

| Varian | Isi |
|---|---|
| `.btn` | ink solid, putih, 14px 500, radius 6px; hover opacity .88 |
| `.btn.ghost` | transparan, border rule, ink; hover border ink |
| `.btn.take` | waiting solid (ambil alih — satu-satunya tombol panas) |
| disabled | opacity .45, not-allowed |

### 2.8 Panel & form setelan

- Panel: surface, border rule, radius 8px, padding 20–22px, margin-bottom 18px.
- `.setting`: grid `1fr 120px`, separator border-bottom; input mono kanan;
  **`:invalid` → border bata + wash** (validasi klien, server tetap otoritas).
- `.locked` (agent): paper, border-kiri 3px abu; daftar mono 12.5px — kesan
  "dikunci" visual, bukan sekadar teks.
- Kontak: baris mono num + label; tombol Hapus ghost; add-form grid
  `1fr 1fr auto` (stack di <560px).

### 2.9 Toast

- Fixed bottom-center, ink solid, radius 999px, 14px; muncul fade+slide .18s,
  auto-hide 2.8s; `pointer-events: none`.

### 2.10 Audit log

- Tabel 13.5px; th cond 12px uppercase muted; when/who mono 12px nowrap;
  detail mono 11.5px muted `word-break: all`.

### 2.11 Empty state

- Dashed border rule, radius 6px, muted — **kalimat ajakan**:
  "Tidak ada yang menunggu. Bot sedang menangani 12 percakapan." (`b` = ink).

## 3. Motion

```text
Kartu hover   transform .12s ease + shadow
Toast         opacity/transform .18s
Denyut        breathe 2.6s ease-in-out (hanya urgensi 3)
Semua lainnya  tanpa animasi
prefers-reduced-motion: reduce  →  animation & transition NONE di semua elemen
```

## 4. Responsive

| Breakpoint | Perubahan |
|---|---|
| ≤ 860px | shell 1 kolom; thread jadi `position:fixed; inset:56px 0 0` (overlay penuh); bubble 88% |
| ≤ 560px | contact-add 1 kolom |

## 5. Aksesibilitas visual (non-negosiasi)

1. `:focus-visible` → outline 2px `--human`, offset 2px.
2. Urgensi dikode **ketebalan + denyut**, bukan warna saja.
3. Kontras: teks ink di paper ≥ 12:1; muted ≥ 5:1; putih di atas
   human/bot/waiting ≥ 4.5:1.
4. Teks tidak pernah < 10.5px (meta) dan bukan satu-satunya pembawa info.
5. Target sentuh minimal ~40px untuk tombol utama panel.
6. Nol informasi hanya lewat warna (pill punya teks, tick punya state teks di row?).

## 6. Ikonografi

**Tanpa ikon.** Semua aksi pakai kata (Ambil alih, Lepas ke bot, Muat ulang QR,
Keluar). Kalau rombak memperkenalkan ikon, wajib: inline SVG (bukan font),
`aria-hidden` + teks pendamping, stroke konsisten 1.5–2px.

## 7. Copy kosmetik (tetap Indonesia)

| Konteks | Teks |
|---|---|
| Ambil alih | tombol "Ambil alih" → toast "Diambil alih" |
| Dilepas | "Dilepas ke bot" |
| Konflik | "Percakapan ini sedang dipegang petugas lain." |
| QR | "Belum terhubung. Pindai QR dengan WhatsApp yang dipakai bot:" |
| Kosong | "Tidak ada yang menunggu. Bot sedang menangani N percakapan." |
| Timeout | "Rentang X–Y menit." |

## 8. Checklist rombak (kosmetik)

- [ ] Design tokens dipindah ke CSS variables / tema (jangan hardcode hex di komponen)
- [ ] Warna state hanya lewat token — tidak ada aksen baru
- [ ] Font lokal/fallback, tanpa CDN
- [ ] Urgensi bertingkat (pita) + timer tabular-nums + denyut level 3
- [ ] Indikator kesegaran (stale) saat polling gagal
- [ ] Empty state ajakan + angka nyata
- [ ] `prefers-reduced-motion`, `:focus-visible`, Esc, Ctrl+Enter
- [ ] Semua data lewat `esc()`; zero inline onclick
- [ ] Konfirmasi akibat sebelum ambil alih
