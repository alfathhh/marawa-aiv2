# Security, Privacy, dan Data Governance

## 1. Security objectives

1. Tidak ada data individu/rahasia statistik atau data belum rilis yang keluar melalui MARAWA AI.
2. Source database tidak dapat diubah dan hanya approved views yang dapat dibaca.
3. Admin actions authenticated, authorized, dan auditable.
4. Prompt injection tidak dapat memperluas tool/data access.
5. Channel/provider compromise memiliki blast radius terbatas.
6. Chat, nomor, credentials, dan auth state terlindungi at rest dan in transit.

UU Pelindungan Data Pribadi mengatur pemrosesan data pribadi serta kewajiban pengendali/prosesor; desain dan kebijakan retensi perlu direview pemilik kebijakan/pejabat berwenang sebelum produksi.[11] Operasi statistik juga tunduk pada kerangka UU Statistik dan aturan kerahasiaan/diseminasi BPS.[12][4]

## 2. Data classification

| Class | Examples | Default handling |
|---|---|---|
| PUBLIC | Publikasi/tabel/metadata resmi sudah rilis | Boleh menjadi corpus setelah provenance validation |
| INTERNAL | SOP, prompt, analytics, internal notes | Authenticated access, no public answer unless approved |
| CONFIDENTIAL | Raw chats, nomor WA, admin identity, feedback | Encryption, least privilege, audit |
| RESTRICTED | Provider keys, DB creds, Baileys auth state, TOTP secret | Secret store/encrypted volume, no UI/readback/log |
| PROHIBITED FOR BOT | Data individu/responden, unreleased tables, secret statistical data | Do not ingest/query/expose |

## 3. Threat model

| Threat | Control |
|---|---|
| User prompt injection | Fixed server policy, typed tools, evidence-only answer, no document instruction execution |
| RAG poisoning | Source allowlist, checksum/version, approval, quarantine, diff, parser sandbox |
| SQL injection/data exfiltration | No arbitrary SQL, allowlisted views/templates, parameters, RO role, timeout/limit, egress restrictions |
| Broken RBAC/IDOR | Server-side object authorization, permission tests, scoped queries |
| Admin account takeover | Argon2id, TOTP, lockout/rate limit, session revoke, re-auth sensitive ops |
| Webhook spoof/replay | HMAC, timestamp, nonce, body hash, internal network/mTLS optional |
| Duplicate/race messages | Inbox/outbox idempotency, locks, version checks |
| Baileys session theft | Encrypted auth store, filesystem isolation, restricted backup, no QR logs. **Aset paling sensitif di sistem** — pemegangnya bisa mengirim sebagai nomor resmi BPS dan membaca seluruh riwayat |
| QR pairing palsu dari dashboard yang dikompromikan | Penyerang menampilkan QR instans miliknya; petugas memindai; nomor BPS tertaut ke server penyerang. Mitigasi: notifikasi pairing dikirim ke **WhatsApp petugas lain**, bukan ke dashboard; daftar linked device terlihat; prosedur unpair darurat tertulis dan pernah dilatih (`docs/06` §3A) |
| Gate kebenaran dimatikan lewat UI | Tidak ada toggle `answer_gate` di dashboard, selamanya. Tekanan "bikin lebih membantu" itu wajar dan akan datang; penegakan harus di luar jangkauan browser (`docs/06` §3B) |
| Secret leakage | Secret references, redaction, scanning, rotation, no debug payloads |
| LLM data leakage | Minimize provider payload, redact PII when unnecessary, provider contract/config review |
| Persistent chat misuse | Strict raw-chat permission, pseudonymous analytics, access/export/delete workflows |
| Supply-chain compromise | Lockfiles, pinned images/digests, SBOM, image/dependency scans, signed release artifacts |
| Adaptive prompt injection | Exact-release adaptive/human red-team, repeated attempts, effect oracle, no static-suite-only claim |
| Capability/data-flow smuggling | Server-created scoped capabilities, task contract, provenance/taint, forbidden sinks |
| Memory poisoning | Typed allowlisted memory, immutable references, provenance, version validation, selective repair |

## 4. Authentication/session

- Password: Argon2id tuned pada deployment hardware.
- TOTP required; issuer `MARAWA AI` dan account label tidak membocorkan secret.
- Recovery codes hashed, one-time, minimum count policy.
- Secure, HttpOnly, SameSite cookies; CSRF token untuk mutations.
- Session rotation after login/privilege change, idle and absolute timeout.
- Disable user revokes all sessions; role changes invalidate/re-evaluate sessions.
- Audit success/failure without password/TOTP code.

## 5. Authorization

- Deny by default.
- Permission check di API/domain service, bukan hanya UI.
- Object-level filter: PST Agent default hanya assigned conversation.
- Sensitive permissions terpisah: raw PII, export, prompt publish, model publish, source publish, audit, user admin.
- Four-eyes approval disarankan untuk production knowledge/prompt/model release.
- Break-glass account disabled by default, credential offline, every use alerts and requires post-incident review.

## 6. Database controls

### Source DB

- Dedicated read-only role + allowlisted schema/views.
- Network allowlist hanya API/approved ingestion IP/container.
- TLS, connection pool limits, statement timeout, transaction read-only.
- Query audit: dataset ID, normalized params hash, duration, row count—not raw secret/data dumps.

### App DB

- No public port.
- Separate app/migration/backup roles.
- Column/application encryption for phone/JID, raw message, TOTP, destination refs.
- HMAC blind index for exact identity lookup.
- Backups encrypted and restore-tested.

## 7. LLM/provider privacy

Before production, record for each provider:

- endpoint/region;
- retention/training/data logging terms;
- DPA/contract status;
- key/project isolation;
- supported no-log/enterprise setting;
- subprocessors and incident contact;
- approved data classes.

Default prompt payload excludes raw phone, admin notes, credentials, database schema, and unrelated history. Conversation context is minimized and redacted where possible.

## 8. Prompt/tool security

- System prompt server-owned and immutable to user/document content.
- Retrieved chunks wrapped/tagged as untrusted evidence.
- Tool registry server-side; model cannot define tool or SQL.
- Max tool calls, result bytes, rows, and execution time.
- URL citation allowlist from evidence; no arbitrary model-generated links.
- Structured output schema and numeric grounding validator.
- Prompt/model changes release-gated and eval-gated.
- Working-memory patches divalidasi terhadap existing evidence/result/analysis/artifact IDs; model tidak dapat menyuntikkan fakta tanpa provenance.
- Statistical skills versioned/reviewed/evaluated dan tidak dapat menambah permission.
- Analysis sandbox non-root, ephemeral, no network/host filesystem/secrets, input artifact allowlist, resource/time/output limits, dan output validation.
- Agent publik tidak memiliki generic shell, browser, arbitrary HTTP, atau filesystem tools.
- Control plane (policy/task contract/capability/authorization) dipisahkan dari tainted data plane (user/RAG/tool/source/model values).
- Semua tool/action memerlukan opaque, server-created, purpose-bound, expiring capability; classifier `benign` tidak menciptakan authority.
- Derived values mewarisi taint/data classification; declassification hanya deterministic, typed, allowlisted, dan auditable.
- Destination outbound terikat server ke conversation asal, bukan parameter dari model/source text.
- Autonomous run tidak boleh mempunyai seluruh properti Agents Rule of Two (`untrusted input + sensitive/private access + external state/communication`).
- Static jailbreak corpus hanya regression baseline; critical release menjalankan adaptive/repeated effect-based suite pada exact stack.
- Detail subsystem ada di `09A-ANTI-JAILBREAK.md`, methodology di `09B-ANTI-JAILBREAK-REDTEAM.md`, dan evidence review di `09C-ANTI-JAILBREAK-RESEARCH.md`.

## 9. Logging/observability privacy

Never log:

- raw passwords/TOTP/recovery code;
- API/DB keys, auth headers, Baileys QR/auth state;
- complete JID/phone;
- raw message by default;
- full provider prompts/responses in general logs.

Use request/conversation opaque IDs, error codes, latency, token counts, dataset IDs, evidence IDs. Restricted debug capture must be opt-in, time-bounded, encrypted, audited, and auto-delete.

## 10. Retention decision and controls

> **Temuan audit — ini liabilitas terbesar yang belum diperiksa di seluruh
> proyek, dan jauh lebih mendesak daripada jailbreak.**
>
> Keputusan "chat disimpan tanpa batas waktu untuk analitik" berarti instansi
> pemerintah menyimpan selamanya: nomor WhatsApp warga, isi percakapan lengkap,
> dan pertanyaan yang bisa sangat sensitif (bantuan sosial, kemiskinan,
> pengangguran). UU 27/2022 mensyaratkan dasar hukum, pembatasan tujuan, dan
> pembatasan retensi **sebelum** pemrosesan, bukan sesudahnya.[11]
>
> Poin 8 di bawah menuliskan urutannya terbalik: *"annual necessity review; if
> indefinite retention cannot be justified, policy must change"*. Itu artinya
> mengumpulkan dulu, membenarkan belakangan. Untuk pengendali data pemerintah,
> urutan itu tidak bisa dipertahankan.
>
> **Yang membuat ini mudah diperbaiki:** analitik yang benar-benar diminta di
> `docs/06` §10 — distribusi intent, tingkat abstain/handover, latency p50/p95,
> pertanyaan yang belum terjawab, pemanfaatan sumber — **tidak satu pun butuh
> transkrip mentah yang disimpan selamanya.** Semuanya adalah agregat yang bisa
> diturunkan saat pesan masuk.
>
> ### Desain pengganti yang disarankan
>
> ```text
> Saat pesan masuk  →  turunkan agregat sekarang (intent, latency, hit/miss,
>                      dataset yang dipakai, ada/tidaknya handover)
>                   →  simpan agregat TANPA nomor, retensi panjang: aman
>                   →  simpan transkrip mentah + nomor, retensi PENDEK
>                      (mis. 30–90 hari, ditetapkan pimpinan), lalu hapus
> ```
>
> Untuk "top unanswered questions" yang butuh teks: simpan teks pertanyaannya
> saja, terpisah dari nomor, tanpa kunci yang menghubungkan keduanya.
>
> Hasilnya: seluruh nilai analitik tetap didapat, jendela paparan menyusut dari
> selamanya menjadi hitungan minggu, dan permintaan hapus data dari warga jadi
> mudah dipenuhi. Kalau nanti pimpinan tetap memilih retensi tanpa batas, itu
> keputusan yang sah — tetapi harus diambil sadar, dengan mengetahui bahwa versi
> murahnya tersedia dan tidak mengorbankan apa pun yang mereka inginkan.
>
> OQ-09 seharusnya berbunyi: *"Berapa lama transkrip mentah disimpan?"* — bukan
> *"Setujui retensi tanpa batas."* Pertanyaan pertama punya jawaban; yang kedua
> meminta tanda tangan untuk risiko yang tidak perlu ada.

Product decision currently: chat retained indefinitely for analytics. This materially increases privacy/security impact. Baseline requirements before production:

1. Stakeholder signs documented purpose and lawful basis.
2. Raw operational chat encrypted; analytics derived with pseudonymous contact IDs.
3. Default dashboard analytics does not expose raw chats/numbers.
4. Access review quarterly; raw export approval and audit.
5. Key rotation and crypto-shredding design.
6. User/data-subject request workflow where applicable.
7. Backups/logs/cache each have explicit lifecycle despite raw chat retention.
8. Annual necessity review; if indefinite retention cannot be justified, policy must change without schema redesign.

## 11. Network/host hardening

- Caddy only public service; app/database ports internal.
- SSH key-only, minimal admins, firewall, automatic security patches/reboot window.
- Container non-root, read-only filesystem where possible, drop capabilities, no Docker socket mount.
- Separate networks: edge, app, data, observability.
- Egress allowlist for provider APIs, BPS sources, DNS/NTP; source DB route restricted.
- Admin dashboard optionally VPN/IP allowlist in addition to auth.

## 12. Secret management

MVP acceptable: root-owned `0600` env files outside repo + encrypted backup; better: SOPS/age or Vault-compatible secret manager. Rotation runbook for LLM keys, webhook HMAC, DB passwords, session encryption, TOTP wrapping key, and Baileys state key. Support current + previous webhook secret for bounded rotation.

## 13. Upload/parser security

- MIME magic validation, not filename only.
- Size/page/token/archive-depth limits.
- Reject executables, scripts, macros, encrypted archives by default.
- Malware scan and parser sandbox with CPU/memory/time limits.
- Sanitize filenames; object IDs generated server-side.
- No external URL fetch from inside uploaded document.
- Extracted instruction-like text remains evidence data, never agent policy.

## 14. Vulnerability management

- CI: secret scan, SAST, dependency audit, container scan, IaC lint, license check.
- Pre-release DAST and authorization tests.
- Monthly dependency review; expedited critical CVE response.
- Baileys upgrades require session and protocol compatibility test.
- SBOM stored with release.

## 15. Incident priorities

| Severity | Examples |
|---|---|
| SEV-1 | Data/secret exposure, unauthorized DB access, widespread wrong public data |
| SEV-2 | WhatsApp outage, admin auth bypass attempt, fallback/provider complete outage |
| SEV-3 | Partial ingestion failure, elevated latency, isolated answer defect |
| SEV-4 | Cosmetic/dashboard noncritical issue |

Detailed operational steps in `12-OBSERVABILITY-RUNBOOK.md`.

## 16. Security acceptance checklist

- [ ] Threat model reviewed.
- [ ] Source views approved and no prohibited fields.
- [ ] Provider privacy/DPA/settings approved.
- [ ] RBAC matrix and object authorization tests pass.
- [ ] TOTP/recovery/session tests pass.
- [ ] HMAC/replay/idempotency tests pass.
- [ ] Prompt injection and data exfiltration eval pass.
- [ ] Capability/provenance/task-contract hard invariants pass.
- [ ] Exact-release adaptive/repeated red-team dan primary/fallback parity pass.
- [ ] Backup restore and key rotation drill pass.
- [ ] Retention purpose/policy signed.
- [ ] Vulnerability scans no unresolved critical/high without formal exception.

## Sources

[4] https://ppid.bps.go.id/app/konten/0000/Layanan-BPS.html — Layanan dan Kebijakan Diseminasi BPS
[11] https://peraturan.bpk.go.id/Details/229798/uuno-27-tahun-2022 — UU No. 27 Tahun 2022 Pelindungan Data Pribadi
[12] https://peraturan.bpk.go.id/Home/Details/45944/1000 — UU No. 16 Tahun 1997 Statistik
