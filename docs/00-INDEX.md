# Indeks Dokumentasi MARAWA AI

> # ⛔ ATURAN KERAS #1 — TIDAK PERNAH MENGARANG ANGKA
>
> **MARAWA tidak boleh menyebut angka yang tidak ada di hasil tool. Tidak
> menebak, tidak mengira-ira, tidak membulatkan dari ingatan.**
>
> **Kalau datanya tidak ada, MARAWA WAJIB bilang tidak ada.**
>
> Ini bukan preferensi gaya dan tidak bisa dilonggarkan oleh setelan, prompt,
> tombol dashboard, atau permintaan siapa pun. Angka salah yang keluar membawa
> nama BPS lebih buruk daripada tidak ada jawaban sama sekali.
>
> Ditegakkan mesin di `scripts/answer_gate.py` (`check_numeric_grounding`),
> bukan oleh kalimat di prompt.


## Status saat ini (2026-08-15)

- **Database foundation sudah di-build**: BPS WebAPI mirror (SIMDASI 47 tabel, Dynamic 335 variabel, Census, 602 publikasi), serving view fixes (`migrations/001`), `bps_registry` published (1.148 datasets, 1.458 measures, 1.026 dimensions, 7.371 items, 18 geografi + 34 alias, 6 query templates runtime-safe + binder validator) via `scripts/build_bps_registry.py` (`migrations/002`). **Hardening done (15 Aug)**: read-only DB role, immutable retired snapshots, quality flags, reversible migration ledger 001–005, Census typed items, display-label, unit packet, golden eval harness. **Belum dibangun (planning)**: agent runtime, query compiler, formatter/grounding, eval live, WhatsApp adapter, dashboard, deployment — menunggu izin scope + OQ-05/OQ-07.
- **Teruji**: hitungan canonical hanya di [`25-PLANNING-AUDIT-STATUS.md`](25-PLANNING-AUDIT-STATUS.md), lengkap dengan kolom kekuatan bukti. Ringkas: query prototypes dan invariant unit/binder terverifikasi lawan DB asli; metrik retrieval berlabel **sintetis** dan bukan bukti; harness episode mengeksekusi sebagian kecil turn dan 5 dari 19 episode belum pernah dieksekusi sama sekali.
- **Sinyal eksternal**: **belum ada.** Belum satu pun pertanyaan pengguna nyata melewati sistem ini. Aksi termurah untuk mengubahnya ada di `15-OPEN-QUESTIONS.md`.
- **Kebijakan**: tanpa cronjob/polling BPS; update manual dari katalog Excel; list kandidat tabel dulu, user pilih, baru query; unit tidak pernah ditebak (ditegakkan `tests/` + CHECK constraint, bukan cuma tertulis).
- **Scope**: dipotong ke **Slice 1** (`01-PRD.md` §11) — WhatsApp → discovery → pilih tabel → query → jawaban bersumber. Sisanya ditunda. Alasan: `13-ADR.md` ADR-017.

> **Baca ini dulu kalau bingung proyeknya di mana:** `25-PLANNING-AUDIT-STATUS.md` → `01-PRD.md` §11 (Slice 1) → `13-ADR.md` ADR-015..017.

## Urutan baca per peran

### Product owner / pimpinan

1. `01-PRD.md`
2. `02-AGENT-RUNTIME.md`
3. `02-SCOPE-AND-CONVERSATION.md`
4. `06-DASHBOARD-AND-HANDOVER.md`
5. `14-IMPLEMENTATION-PLAN.md`
6. `15-OPEN-QUESTIONS.md`

### Tech lead / architect

1. `03-ARCHITECTURE.md`
2. `02-AGENT-RUNTIME.md`
3. `04-RAG-AND-DATA.md`
4. `05-DATABASE.md`
5. `07-WHATSAPP-WEBHOOK.md`
6. `08-API-CONTRACT.md`
7. `09-SECURITY-PRIVACY.md`
8. `09A-ANTI-JAILBREAK.md`
9. `09B-ANTI-JAILBREAK-REDTEAM.md`
10. `09C-ANTI-JAILBREAK-RESEARCH.md`
11. `13-ADR.md`
12. `17-BPS-WEBAPI-DATA.md`
13. `18-WHATSAPP-DATA-ANSWER-FORMATS.md`
14. `20-BPS-MANUAL-UPDATE-WORKFLOW.md`
15. `21-BPS-DATA-EXPLORATION-AND-AGENT-QUERY-DESIGN.md`
16. `22-BPS-QUERY-PROTOTYPE-TEST-MATRIX.md`
17. `23-BPS-OPTIMIZED-REGISTRY-AND-TYPED-CONTRACTS.md`
18. `24-BPS-QUERY-REGISTRY-DDL-DRAFT.md`
19. `25-PLANNING-AUDIT-STATUS.md`
20. `26-BPS-UNIT-REVIEW-PACKET.md`
21. `27-BPS-SERVICE-MENU-FLOW.md`

### AI/RAG engineer

1. `../AGENT.md`
2. `02-AGENT-RUNTIME.md`
3. `04-RAG-AND-DATA.md`
4. `10-TEST-EVALUATION.md`
5. `12-OBSERVABILITY-RUNBOOK.md`

### Backend / frontend / DevOps

- Backend: `05`, `07`, `08`, `09`, `10`
- Data/ingestion: `04`, `05`, `10`, `17`, `18`
- Frontend: `06`, `08`, `09`, `10`
- DevOps: `11`, `12`, `09`
- Security/red team: `09`, `09A`, `09B`, `09C`, `10`, `12`
- Semua coding agent: `../AGENTS.md`

## Status

| Dokumen | Status | Exit criterion |
|---|---|---|
| PRD | Draft approved-by-user requirements | Stakeholder BPS memberi sign-off |
| Architecture | Implementation-ready | Capacity dan networking dikonfirmasi |
| Agent/RAG | Implementation-ready | Golden dataset tersedia |
| Dashboard/handover | Implementation-ready | Jadwal/SLA operator dikonfirmasi |
| Security/privacy | Draft control baseline | DPO/PPID/legal/security review |
| Anti-jailbreak research/design | Implementation-ready research baseline | Capability/effect harness implemented dan exact-release adaptive gate lulus |
| Deployment/runbook | Implementation-ready | Domain, VPS, backup target, dan secrets tersedia |

## Konvensi

- **MUST/WAJIB:** acceptance requirement.
- **SHOULD/SEBAIKNYA:** default kuat; penyimpangan perlu ADR.
- **MAY/BOLEH:** opsional.
- `TBD`: keputusan organisasi belum diberikan; tidak boleh ditebak saat implementasi.
