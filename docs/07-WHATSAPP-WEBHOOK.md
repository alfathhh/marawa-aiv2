# WhatsApp Adapter dan Internal Webhook

## 1. Arsitektur kanal

Baileys berkomunikasi dengan WhatsApp Web melalui WebSocket dan tidak memerlukan browser.[7] Karena itu, tidak ada public Meta Cloud webhook pada desain ini. “Webhook WhatsApp” MARAWA adalah **signed internal webhook** dari `wa-worker` ke FastAPI, ditambah internal outbound API dari FastAPI ke worker/queue.

Adapter boundary memungkinkan penggantian ke official WhatsApp Cloud API tanpa mengubah agent, conversation, handover, atau dashboard domain.

## 2. Baileys worker responsibilities

- Pairing QR/code dan encrypted auth-state persistence.
- Connection lifecycle/reconnect/logout detection.
- Normalize inbound message dan delivery event.
- Ignore `fromMe` inbound loops, status broadcast, unsupported events, and group chats by default.
- Download attachment hanya jika type/size allowlisted dan feature enabled.
- Consume outbound commands dan send messages.
- Report send/delivery/read/failure state.
- Kirim timed notices dari scheduler API (idle 300s / handover SLA 180s) sebagai outbox command dengan `expires_at`; worker tidak menyimpan timer logic sendiri.
- Tidak menjalankan LLM, RAG, SQL, RBAC, atau handover decision.

## 3. Internal webhook endpoint

```http
POST /internal/webhooks/whatsapp
Content-Type: application/json
X-Marawa-Timestamp: 1786720000
X-Marawa-Nonce: 01J...
X-Marawa-Signature: v1=<hex-hmac-sha256>
```

Canonical signature input:

```text
{timestamp}.{nonce}.{raw_request_body}
```

Server verifies:

- HMAC dengan current/previous rotating secret;
- constant-time comparison;
- timestamp skew default ≤ 300 detik;
- nonce replay cache;
- body size limit;
- schema/version;
- source network allowlist/mTLS optional.

## 4. Event envelope

```json
{
  "schema_version": "1.0",
  "event_id": "wa:message:3EB0...",
  "event_type": "whatsapp.message.received",
  "occurred_at": "2026-08-14T09:00:00Z",
  "account_id": "marawa-main",
  "trace_id": "01J...",
  "data": {
    "message_id": "3EB0...",
    "chat_jid": "628...@s.whatsapp.net",
    "sender_jid": "628...@s.whatsapp.net",
    "from_me": false,
    "is_group": false,
    "message_type": "text",
    "text": "Berapa jumlah penduduk tahun 2025?",
    "quoted_message_id": null,
    "media": null,
    "timestamp": "2026-08-14T09:00:00Z"
  }
}
```

### Supported event types

- `whatsapp.message.received`
- `whatsapp.message.updated`
- `whatsapp.delivery.updated`
- `whatsapp.connection.updated`
- `whatsapp.auth.qr.updated`
- `whatsapp.auth.logged_out`

## 5. Webhook response

### First receipt

```http
HTTP/1.1 202 Accepted

{"accepted":true,"event_id":"wa:message:3EB0...","duplicate":false}
```

### Duplicate

Tetap `202`, `duplicate=true`; tidak enqueue ulang.

### Invalid signature/schema

`401`/`422`, tidak disimpan ke normal inbox; simpan redacted security event dan metric.

FastAPI harus menyimpan event ke `inbox_events` sebelum mengembalikan `202`.

## 6. Idempotency

- Unique event: `(source='whatsapp', event_id)`.
- Unique inbound message: `(account_id, message_id)`.
- Outbound idempotency key: `{conversation_id}:{response_message_id}:{part_index}`.
- Delivery updates boleh datang berulang/tidak berurutan; status transition monotonic (`sent < delivered < read`) kecuali explicit failure metadata.
- Retry tidak boleh membuat text baru; gunakan payload snapshot yang sama.

## 7. Outbound contract

Preferred: FastAPI transactional outbox → Redis Stream `whatsapp.outbound.v1` → worker consumer.

```json
{
  "schema_version": "1.0",
  "command_id": "cmd_01J...",
  "idempotency_key": "conv:msg:0",
  "account_id": "marawa-main",
  "conversation_id": "...",
  "expected_conversation_version": 14,
  "author_type": "ai",
  "destination_ref": "encrypted-resolved-server-side",
  "payload": {
    "type": "text",
    "text": "...",
    "quoted_message_id": "3EB0..."
  },
  "expires_at": "2026-08-14T09:10:00Z"
}
```

Sebelum send AI message, worker meminta/mengecek send authorization atau command sudah ditandai state-validated; API final guard membatalkan command jika state/version berubah ke admin takeover.

Timed notices (`session_end_notice`, `admin_busy_notice`) juga melewati final state guard:

- `session_end_notice`: batal bila ada inbound/claim baru sebelum kirim (timer di-reset server-side);
- `admin_busy_notice`: batal bila admin claim terjadi sebelum kirim; jika state sudah `RESOLVED` karena busy timeout, notice tetap dikirim;
- aktuator timer = scheduler API; worker hanya konsumen outbox.

## 8. Message normalization

Text extraction order:

1. conversation text;
2. extended text;
3. image/video/document caption;
4. button/list reply labels if enabled;
5. unsupported fallback.

Normalize Unicode dan whitespace, tetapi simpan encrypted original payload pointer untuk audit terbatas. Jangan lowercase seluruh body karena dapat merusak proper noun/code.

## 9. Group and status policy

MVP:

- Group messages ignored, kecuali account-specific allowlist di fase berikutnya.
- Status broadcast ignored.
- Call events tidak ditangani; optional static reply configurable tetapi tidak wajib.
- Messages sent from paired phone (`fromMe`): **default ignore TIDAK berlaku untuk Slice 1.** Bila nomor bot adalah nomor yang juga dipegang petugas, `fromMe` adalah sinyal takeover manusia dan wajib membuat percakapan itu `HUMAN_ACTIVE` (`docs/06` §0.2). Yang tetap harus di-ignore adalah pesan `fromMe` yang berasal dari outbound bot sendiri — bedakan dengan mencocokkan `wa_message_id` terhadap outbox; yang tidak ada di outbox berarti diketik manusia dari HP. Tanpa pembedaan ini, bot akan menimpa jawaban petugas dan warga menerima dua jawaban berbeda dari satu nomor resmi.

## 10. Media policy

MVP menerima metadata/caption; media understanding tidak aktif. Jika attachment feature diaktifkan:

- allowlist MIME/extension;
- max bytes;
- stream download, no path traversal;
- malware scan;
- encrypted object storage;
- OCR/parser sandbox;
- retention policy terpisah;
- no automatic corpus ingestion dari user attachment.

## 11. Auth state

- Jangan memakai plaintext `useMultiFileAuthState` directory untuk production.
- Buat encrypted durable auth store dengan atomic key updates dan restricted filesystem permissions.
- Backup auth state encrypted dan aksesnya dibatasi system admin.
- QR/pairing token hanya tampil pada protected dashboard/session dan tidak masuk logs.
- Satu active worker owner per account; leader lock mencegah dual connection.

## 12. Reconnect and backpressure

- Exponential backoff + jitter; bedakan logout dari transient close.
- Logout membutuhkan re-pair, alert kritis; jangan infinite retry.
- Queue outbound punya max attempts, TTL, dead-letter, dan supervisor visibility.
- Per-conversation ordering dan global send pacing.
- Bila backlog melewati threshold, bot dapat memberi maintenance response hanya jika channel masih connected dan response tidak memperburuk antrean.

## 13. Health endpoints/metrics

Worker internal endpoint:

- `/health/live`
- `/health/ready` (session connected + Redis/API reachable)
- `/internal/session/status`

Metrics: connection state, reconnects, QR age, inbound/outbound rate, send latency, failures by reason, queue lag, auth persist failures.

## 14. Contract tests

- Valid/invalid/expired/replayed signature.
- Duplicate event.
- Group/fromMe/status ignored.
- Message variants normalized.
- Reordered receipts monotonic.
- Disconnect/logout.
- Concurrent duplicate outbound.
- Takeover preempts pending AI send.
- Timed notice (`session_end_notice`) dibatalkan oleh inbound yang me-reset idle timer.
- `admin_busy_notice` dibatalkan oleh admin claim; tetap terkirim bila state busy-timeout sudah fixed.
- Duplicate inbound tidak me-reset idle timer dua kali (dedup sebelum timer logic).
- Worker restart preserves session/idempotency.

## Sources

[7] https://baileys.wiki — Baileys Documentation
