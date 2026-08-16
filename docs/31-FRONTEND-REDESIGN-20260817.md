# MARAWA — Frontend Redesign 2026-08-17

## Status

**LIVE:** `https://marawa.hatafisme.web.id/admin`
**Surface:** Operate (primary) + Monitor (secondary)
**Implementation:** `apps/dashboard/index.html`, self-contained HTML/CSS/JS,
no CDN/build dependency.

## Visual direction

Rebuilt from zero after the prior dashboard failed the visual bar. Direction:
**quiet operational console** — modern light canvas, restrained borders,
strong typographic hierarchy, cobalt interaction accent, amber queue urgency.

Intentionally excluded: hero layout, marketing feature cards, gradients,
glassmorphism, fake metrics, decorative icons, remote fonts, and arbitrary
color accents.

## Composition

```text
┌ rail 216px ┬ queue 370px ┬ conversation flexible ┐
│ nav/status │ search/filter│ header/messages/reply │
└────────────┴──────────────┴───────────────────────┘
```

- Desktop: persistent rail + queue + conversation pane.
- ≤1040px: compact icon rail.
- ≤760px: bottom navigation, queue full-width, conversation becomes mobile
  overlay.
- Settings and audit are full workspace views, not nested card clutter.

## Visual tokens

```text
canvas       #f6f7f9     surface       #ffffff
ink          #18191b     ink-soft      #3f4247
muted        #777b82     faint         #a2a6ad
line         #e5e7eb     line-strong   #d6d9de
accent       #2563eb     accent-hover  #1d4ed8
waiting      #b45309     waiting-soft  #fffbeb
success      #15803d     success-soft  #f0fdf4
danger       #b42318     danger-soft   #fff4f2
closed       #8b9098
```

System font stack only. No external font request.

## Functional contract retained

The redesign keeps the backend routes and security invariants:

- password login + optional TOTP; Bearer session; 401 returns to login;
- inbox polling 5 s, client timer 1 s, visible stale-data indicator;
- queue search `/`, filters, empty states, mobile navigation;
- open conversation, handover on/off, version conflict handling;
- reply uses stable `client_request_id` for the same retry;
- settings: WhatsApp QR/status, bot switch, timeouts, staff contacts, agent guard;
- audit log; logout; Escape; Ctrl/Cmd+Enter.

## Runtime audit fixes shipped with the redesign

The function audit plus an independent concurrency review found production
Postgres paths that in-memory/serial tests hid. Fixed and covered by
HTTP→Postgres E2E tests:

1. inbound/from-me webhook state persistence via store contract;
2. admin reply state + outbox atomic persistence;
3. duplicate inbound rejected before advancing state;
4. timeout settings no longer serialize structured QR/global-switch rows as ints;
5. global bot switch persists through PostgresStore;
6. notification timestamp update uses a version-guarded store method;
7. pending bot outbox cancellation uses store contract;
8. admin replies appear in the transcript as outbound messages;
9. live status pending count reads the outbox database;
10. `/internal/notifications` no longer assumes InMemoryChannel fields;
11. inbound dedupe, state mutation, and transcript insert are one transaction;
12. simultaneous messages start exactly one agent run;
13. paired-phone takeover stores its message and cancels pending/claimed bot rows;
14. worker calls the final `authorize_send` gate immediately before WhatsApp;
15. timeout sweep works through the PostgresStore contract;
16. timeout settings use an explicit key allowlist, not JSON value type;
17. session verification rejects non-canonical Base64URL token spellings.

## Verification

```text
413 Python PASS (isolated PostgreSQL, 11 warnings only)
9 Node PASS (Baileys worker)
7/7 dashboard HTTP→Postgres E2E PASS (including concurrent inbound)
UI desktop Playwright: login, inbox, search, settings, audit, logout PASS
UI mobile Playwright: bottom rail, queue, empty conversation PASS
UI thread Playwright: open, handover, reply bubble, release PASS
Console/page errors: 0
```

## Account management update — 2026-08-17

- Semua copy frontend memakai istilah **user**, bukan warga.
- Superadmin-only API:
  - `GET /admin/accounts` — daftar akun sanitized;
  - `POST /admin/accounts` — tambah `admin` atau `superadmin`.
- Validasi server: username lowercase allowlist 3–40 karakter, nama 2–80,
  role enum, password 8–128; duplicate username = 409.
- Password langsung di-hash PBKDF2; hash tidak pernah keluar dari list/response
  atau audit detail.
- Dashboard Setelan menampilkan katalog role, form tambah akun, dan daftar akun
  aktif. Admin biasa tetap tidak melihat Setelan/Audit dan API menolak 403.
- Bukti: create/list/login/RBAC lewat Playwright + HTTP→Postgres E2E; suite
  **420 Python + 9 Node PASS**.

Screenshots from the visual audit are in `/tmp/marawa-visual-audit/` during the
session; they are not runtime assets.
