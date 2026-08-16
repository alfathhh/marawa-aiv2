---
title: BPS WebAPI Bootstrap Checklist
status: live
updated_at: 2026-08-15T05:31:57+07:00
---

# BPS WebAPI Bootstrap — Live Checklist

<!-- state-sha256:8a833d27775bc7741e6fedf12d47404184f3e6ee9a410dde1b39cae4c0795ae3 -->
> File ini diperbarui otomatis dari PostgreSQL/checkpoints dan artifact aktual. Jangan centang manual.

## Status saat ini

- **Run:** `2f7d6df0-c448-4b37-8a0c-6122eb0ff187`
- **Status:** `partial`
- **Family aktif:** `publication`
- **Raw snapshots:** **4,340**

## 1. Persiapan dan proteksi

- [x] PostgreSQL backup + SHA-256 tersedia
- [x] Secret API/proxy/PostgreSQL berada di luar repository (mode 0600)
- [x] Schema raw/current/serving + checkpoint tersedia
- [x] HTTP client serialized, retry/backoff, WAF/non-JSON aware

## 2. Ingestion data

- [x] **SIMDASI `1306000`** — 47 tabel; 371/371 table-year; 40,525 cells
- [x] **Dynamic Data `1306`** — 335/335 variables selesai; 10,670 dimensions; 68,220 facts
- [x] **Census Data** — 4 events; 95 local dataset-area requests; 58,041 facts
- [x] **Publication metadata/detail `1306`** — 602 publications; 602 details selesai
- [ ] **Glosarium BPS** — 0 concepts (upstream HTTP 500; deferred)
- [x] Core ingestion run selesai; Glosarium upstream-deferred tidak mengosongkan mirror

## 3. Post-ingestion

- [x] Fail-closed database integrity validation
- [x] Exploration/quality report final dari database completed run
- [x] Publication PDF mirror + checksum (602/602)
- [x] Full automated tests — 135 pytest pass (serving fixes, registry build + hardening, scoring/offering, migration up/down isolated, query lab)
- [x] Documentation validator + Obsidian sync — documentation validator PASS; Obsidian synchronized
- [x] Scheduled catalog sentinel — **dibatalkan**: semua cron BPS dimatikan atas instruksi Tah; update hanya manual (`check_bps_updates.py` bila diminta)

## Artifact

- Data contract: `docs/17-BPS-WEBAPI-DATA.md`
- Format WhatsApp: `docs/18-WHATSAPP-DATA-ANSWER-FORMATS.md`
- Exploration report: `data/reports/bps-exploration.md`
- Integrity report: `data/reports/bps-integrity-validation.json`
- Backup: `data/backups/`
