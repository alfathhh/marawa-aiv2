# API Contract

## 1. Conventions

- Base: `/api/v1`; internal endpoints: `/internal`.
- JSON UTF-8; timestamps RFC 3339 UTC.
- IDs opaque UUID/ULID.
- Auth dashboard: secure HTTP-only session cookie + CSRF protection; no token in localStorage.
- Every response: `X-Request-ID`; client may send `X-Request-ID` if valid.
- Mutating endpoints support `Idempotency-Key` where retry is expected.
- Error envelope:

```json
{
  "error": {
    "code": "CONVERSATION_STATE_CONFLICT",
    "message": "Percakapan telah diambil petugas lain.",
    "request_id": "01J...",
    "details": {}
  }
}
```

## 2. Auth endpoints

### `POST /api/v1/auth/login`

```json
{"username":"agent01","password":"..."}
```

Response bila TOTP required:

```json
{"challenge_id":"...","next":"totp"}
```

### `POST /api/v1/auth/totp/verify`

```json
{"challenge_id":"...","code":"123456"}
```

Sets secure session cookie. Endpoint lain: logout, session list/revoke, TOTP enroll/confirm/recovery, admin reset dengan re-auth.

## 3. Inbox/conversations

### `GET /api/v1/conversations`

Query: `state`, `assigned_to`, `priority`, `intent`, `feedback`, `from`, `to`, `cursor`, `limit`.

### `GET /api/v1/conversations/{id}`

Returns conversation, messages (cursor paginated), state version, assignment, handover, permissions, and redacted evidence summary.

### `POST /api/v1/conversations/{id}/claim`

```json
{"expected_version":13}
```

Response:

```json
{"id":"...","state":"ADMIN_ACTIVE","assigned_admin":{"id":"...","name":"..."},"version":14}
```

### `POST /api/v1/conversations/{id}/messages`

```json
{
  "expected_version": 14,
  "text": "Baik, kami bantu periksa datanya.",
  "quoted_message_id": null
}
```

Requires `conversation.reply`, state `ADMIN_ACTIVE`, and assignment/supervisor permission. Creates message and outbox atomically.

### State actions

- `POST .../handover-request`
- `POST .../reassign`
- `POST .../resolve`
- `POST .../return-to-bot`
- `POST .../priority`
- `POST .../notes`
- `POST .../block`

Semua menerima `expected_version` dan menghasilkan audit event.

## 4. SSE

`GET /api/v1/events/stream`

Event examples:

```text
id: 01J...
event: conversation.updated
data: {"conversation_id":"...","version":14,"state":"ADMIN_ACTIVE"}

id: 01J...
event: message.created
data: {"conversation_id":"...","message_id":"..."}
```

Server filters event berdasarkan permission. Client reconnect dengan `Last-Event-ID`; jika retention window lewat, event `stream.reset` memerintahkan full refetch.

## 5. Knowledge API

- `GET/POST /api/v1/knowledge/sources`
- `POST /api/v1/knowledge/sources/{id}/sync`
- `POST /api/v1/knowledge/uploads` (multipart, strict limits)
- `GET /api/v1/knowledge/versions/{id}/preview`
- `POST /api/v1/knowledge/versions/{id}/submit-review`
- `POST /api/v1/knowledge/versions/{id}/approve`
- `POST /api/v1/knowledge/versions/{id}/publish`
- `POST /api/v1/knowledge/versions/{id}/quarantine`
- `POST /api/v1/knowledge/sources/{id}/rollback`

Long jobs return `202` + `job_id`. Job endpoint exposes state/progress/error code, bukan raw parser stack/secret.

## 5A. Agent context, analysis, artifacts, dan skills

- `GET /api/v1/conversations/{id}/context`
- `POST /api/v1/conversations/{id}/context/reset`
- `GET /api/v1/conversations/{id}/agent-runs`
- `GET /api/v1/agent-runs/{id}`
- `GET /api/v1/results/{id}`
- `POST /api/v1/analyses` — membuat analysis job dari approved result IDs
- `GET /api/v1/analyses/{id}`
- `GET /api/v1/analysis-jobs/{id}`
- `POST /api/v1/artifacts` — chart/table/export dari result/analysis IDs
- `GET /api/v1/artifacts/{id}`
- `GET/POST /api/v1/config/skills`
- `POST /api/v1/config/skills/{id}/releases`
- `POST /api/v1/config/skill-releases/{id}/evaluate`
- `POST /api/v1/config/skill-releases/{id}/publish`
- `POST /api/v1/config/skill-releases/{id}/rollback`

Endpoint result/analysis/artifact memeriksa access class dan conversation/admin permissions. Artifact download memakai short-lived signed URL atau authorized streaming endpoint.

## 6. Prompt/model API

- `GET/POST /api/v1/config/prompts`
- `POST /api/v1/config/prompts/{id}/releases`
- `POST /api/v1/config/prompt-releases/{id}/evaluate`
- `POST /api/v1/config/prompt-releases/{id}/publish`
- `POST /api/v1/config/prompt-releases/{id}/rollback`
- `GET/POST /api/v1/config/models`
- `POST /api/v1/config/models/{id}/probe`
- `POST /api/v1/config/models/{id}/publish`

Secret fields are references (`secret_ref`) and write-only. `GET` never returns secret values.

## 7. Analytics/quality

- `GET /api/v1/analytics/overview`
- `GET /api/v1/analytics/intents`
- `GET /api/v1/analytics/latency`
- `GET /api/v1/analytics/handovers`
- `GET /api/v1/quality/feedback`
- `GET /api/v1/quality/content-gaps`
- `GET/POST /api/v1/quality/eval-cases`
- `POST /api/v1/quality/evaluations`
- `GET /api/v1/quality/evaluations/{id}`

Analytics default aggregate/pseudonymous; raw chat access membutuhkan permission terpisah.

## 8. Admin/audit

- CRUD users/roles dengan safe deletion (disable, bukan hard delete).
- Session revoke dan TOTP reset.
- `GET /api/v1/audit/events` immutable/cursor paginated/export permission.
- Audit export ditandatangani/checksummed bila digunakan formal.

## 9. Agent internal API

Prefer queue untuk normal message processing. Endpoint diagnostic/internal:

### `POST /internal/agent/process`

Hanya service-to-service auth; menerima `inbox_event_id`, bukan arbitrary user text dari publik.

### `POST /internal/tools/search-data-catalog`

Menerima query natural yang sudah dinormalisasi, approved source families, context filters, `page_size_per_family`, per-family cursors, dan optional `candidate_set_id`. Response mengelompokkan kandidat per source (`S`/SIMDASI, `D`/Dynamic, `C`/Census, `P`/Publication), menggunakan stable display refs dan opaque candidate IDs, serta mengembalikan `next_cursor`/`has_more` per group. Tool ini tidak membaca facts.

### `POST /internal/tools/candidate-page`

Menerima `candidate_set_id`, exact source family, dan opaque cursor. Memperpanjang set tanpa menomori ulang ref lama. Cursor tidak boleh dibuat model/user.

### `POST /internal/tools/inspect-dataset`

Menerima exact opaque `candidate_id`; mengembalikan measures, dimension roles/items, geography aliases/levels, period/subperiod coverage, unit/marker semantics, required slots, safe defaults, dan comparability warnings.

### `POST /internal/tools/query-stat-dataset`

Typed request dari orchestrator; tidak menerima SQL string. Request selalu membawa exact `candidate_id`, measure, period/subperiod, geography, dimension filters, operation/order/limit. Server me-resolve query template allowlisted dari registry; quarterly query tanpa subperiod dan Census query dengan category slot material yang belum resolved ditolak `422`.

Candidate refs adalah convenience UI dalam conversation state, bukan permission atau SQL/database identity. Agent boleh merespons natural, merekomendasikan kandidat, menambah approved source, dan probing; endpoint hanya menegakkan grounding/pagination/query contract.

### `POST /internal/agent/runs/{id}/steps`

Mencatat structured action/observation references dari bounded agent loop; tidak menerima atau menyimpan private chain-of-thought.

### `POST /internal/analysis/jobs`

Menerima method/parameters dan approved input result IDs. Worker tidak menerima database credentials, arbitrary network targets, atau host paths.

### `POST /internal/outbound/authorize`

```json
{
  "command_id":"...",
  "conversation_id":"...",
  "expected_version":14,
  "author_type":"ai"
}
```

Returns `authorized` false jika takeover/version mismatch/expired.

## 10. Webhook

Full contract ada di `07-WHATSAPP-WEBHOOK.md`. OpenAPI menandai endpoint internal dan security scheme `MarawaWebhookHmac`.

## 11. Pagination/filter/export

- Cursor pagination, stable sort `(timestamp,id)`.
- Maximum page size 100.
- Export asynchronous untuk dataset besar.
- Formula injection protection untuk CSV export (`=`, `+`, `-`, `@` prefixes escaped).
- PII fields omitted unless explicit export permission and reason recorded.

## 12. Status codes

| Code | Use |
|---:|---|
| 200/201 | Successful sync/create |
| 202 | Accepted async/idempotent webhook |
| 204 | Logout/delete-like disable success |
| 400 | Invalid request semantics |
| 401 | Unauthenticated/bad webhook signature |
| 403 | Permission denied/CSRF |
| 404 | Not found or deliberately hidden resource |
| 409 | Version/state/idempotency conflict |
| 413 | Upload too large |
| 422 | Schema validation |
| 429 | Rate limit |
| 503 | Dependency unavailable/readiness false |

## 13. Contract governance

- Generate OpenAPI from FastAPI; CI diffs spec.
- Shared TypeScript client/types generated from pinned OpenAPI artifact.
- Breaking change requires `/v2` or compatibility window + ADR.
- Consumer-driven contract tests between API, dashboard, and WhatsApp worker.
