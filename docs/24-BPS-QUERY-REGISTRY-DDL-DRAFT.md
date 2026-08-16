# BPS Query Registry — DDL dan Status Build

> **Status: sudah di-build & published** (izin Tah, 15 Aug 2026). Semua tabel di area `bps_registry`, dibaca runtime read-only, ditulis hanya oleh registry builder saat publish version. Tidak ada SQL yang diterima dari LLM.

## 1. Versioning

Setiap publish menghasilkan immutable `registry_version_id` (ULID) + `checksum`. Runtime selalu memakai satu active version per conversation turn; version lama dipertahankan sampai candidate sets yang memakainya expire.

```sql
CREATE TABLE bps_registry.registry_versions (
    registry_version_id TEXT PRIMARY KEY,          -- ULID
    checksum           TEXT NOT NULL,              -- sha256 canonical catalog
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','published','retired')),
    published_at       TIMESTAMPTZ,
    built_from_snapshot_ids TEXT[] NOT NULL,       -- lineage ke bps mirror snapshots
    notes              TEXT
);
```

## 2. Dataset registry

```sql
CREATE TABLE bps_registry.dataset_registry (
    dataset_id          TEXT PRIMARY KEY,          -- ULID, opaque ke model
    registry_version_id TEXT NOT NULL REFERENCES bps_registry.registry_versions(registry_version_id),
    source_family       TEXT NOT NULL CHECK (source_family IN ('simdasi','dynamic','census','publication')),
    source_resource_id  TEXT NOT NULL,             -- table_code / variable_id / event:dataset / publication_id
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,
    topic_id            TEXT,
    topic_name          TEXT,
    dataset_shape       TEXT NOT NULL CHECK (dataset_shape IN
                         ('geography_series','category_series','cross_tab',
                          'quarterly_series','publication_metadata')),
    answerability       TEXT NOT NULL DEFAULT 'answerable'
                        CHECK (answerability IN ('answerable','metadata_only','blocked_quality','unavailable')),
    period_granularity  TEXT NOT NULL CHECK (period_granularity IN ('annual','quarterly','event','release')),
    period_min          TEXT,
    period_max          TEXT,
    period_latest       TEXT,
    search_document     TEXT NOT NULL,             -- canonical precomputed text
    search_aliases      TEXT[] NOT NULL DEFAULT '{}',
    supported_operations TEXT[] NOT NULL DEFAULT '{}',
    quality_flags       TEXT[] NOT NULL DEFAULT '{}',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (registry_version_id, source_family, source_resource_id)
);
CREATE INDEX ON bps_registry.dataset_registry (registry_version_id, source_family);
CREATE INDEX ON bps_registry.dataset_registry USING GIN (search_aliases);
```

## 3. Measure registry

```sql
CREATE TABLE bps_registry.measure_registry (
    measure_id          TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES bps_registry.dataset_registry(dataset_id),
    source_measure_id   TEXT NOT NULL,             -- column_id / indicator code
    name                TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    value_type          TEXT NOT NULL CHECK (value_type IN ('number','text','marker')),
    unit_state          TEXT NOT NULL CHECK (unit_state IN ('known','unitless','unknown_review')),
    unit_display        TEXT,
    unit_scale          NUMERIC,
    decimal_places      SMALLINT NOT NULL DEFAULT 0,
    aggregation_semantics TEXT NOT NULL CHECK (aggregation_semantics IN
                         ('additive','non_additive','index','rate','share','count')),
    allowed_operations  TEXT[] NOT NULL DEFAULT '{}',
    marker_policy       TEXT,
    comparability_group TEXT,
    UNIQUE (dataset_id, source_measure_id)
);
```

`unit_state='unknown_review'` memblokir formatter memakai unit bebas; jawaban menampilkan nilai tanpa unit sampai data owner approve.

## 4. Dimension registry

```sql
CREATE TABLE bps_registry.dimension_registry (
    dimension_id      TEXT PRIMARY KEY,
    dataset_id        TEXT NOT NULL REFERENCES bps_registry.dataset_registry(dataset_id),
    name              TEXT NOT NULL,               -- wilayah / jenis_kelamin / kelompok_umur
    role              TEXT NOT NULL CHECK (role IN ('geography','category','subperiod')),
    required          BOOLEAN NOT NULL DEFAULT FALSE,
    default_item_id   TEXT,                        -- hanya bila aman secara statistik
    total_item_id     TEXT,
    cardinality       INTEGER NOT NULL DEFAULT 0,
    display_order     SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (dataset_id, name)
);
```

`default_item_id` hanya diisi bila item explicit Total upstream; bukan tebakan model.

## 5. Dimension item registry

```sql
CREATE TABLE bps_registry.dimension_item_registry (
    item_id             TEXT PRIMARY KEY,
    dimension_id        TEXT NOT NULL REFERENCES bps_registry.dimension_registry(dimension_id),
    source_item_id      TEXT NOT NULL,
    source_item_code    TEXT,
    label               TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    canonical_geography_id TEXT,                   -- hanya role geography
    is_total            BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (dimension_id, source_item_id)
);
```

`is_total` hanya true untuk item Total/Jumlah explicit upstream.

## 6. Geography registry dan aliases

```sql
CREATE TABLE bps_registry.geography_registry (
    geography_id   TEXT PRIMARY KEY,               -- ULID
    code           TEXT NOT NULL UNIQUE,           -- canonical MFD 1306000/1306010…
    name           TEXT NOT NULL,
    level          TEXT NOT NULL CHECK (level IN ('kabupaten','kecamatan')),
    parent_id      TEXT REFERENCES bps_registry.geography_registry(geography_id),
    sort_order     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE bps_registry.geography_aliases (
    alias_id        TEXT PRIMARY KEY,
    geography_id    TEXT NOT NULL REFERENCES bps_registry.geography_registry(geography_id),
    source_family   TEXT NOT NULL CHECK (source_family IN ('simdasi','dynamic','census','publication')),
    source_code     TEXT,
    source_item_id  TEXT,
    source_label    TEXT NOT NULL,
    match_type      TEXT NOT NULL CHECK (match_type IN ('exact_code','approved_alias','historical_name')),
    valid_from      TEXT,
    valid_until     TEXT,
    UNIQUE (geography_id, source_family, source_code, source_label)
);
```

Seed canonical contoh:

```text
1306000 Padang Pariaman  ← Census 1306 (exact_code) ← Dynamic vervar 18 (exact_code)
1306020 Lubuak Aluang    ← Census LUBUK ALUNG (approved_alias)
1306030 Ulakan Tapakih   ← Census ULAKAN TAPAKIS (approved_alias)
1306061 Koto Patamuan    ← SIMDASI VII Koto Patamuan (approved_alias) ← Census PATAMUAN (approved_alias)
```

Fuzzy text hanya untuk discovery; identity final dari row approved di atas.

## 7. Query template registry

```sql
CREATE TABLE bps_registry.query_template_registry (
    template_id       TEXT PRIMARY KEY,
    template_version  INTEGER NOT NULL,
    registry_version_id TEXT NOT NULL REFERENCES bps_registry.registry_versions(registry_version_id),
    dataset_shape     TEXT NOT NULL,
    view_name         TEXT NOT NULL,               -- allowlisted identifier
    parameter_schema  JSONB NOT NULL,
    sql_template      TEXT NOT NULL,               -- server-owned parameterized SQL
    row_limit         INTEGER NOT NULL DEFAULT 100,
    timeout_ms        INTEGER NOT NULL DEFAULT 5000,
    result_schema_id  TEXT NOT NULL,
    validation_rules  TEXT[] NOT NULL DEFAULT '{}',
    UNIQUE (template_id, template_version)
);
```

Compiler menerima `candidate_id + typed spec`, me-resolve registry → template → parameterized query. View name/table name tidak pernah berasal dari model/candidate text.

## 8. Candidate sets (runtime state)

```sql
CREATE TABLE bps_registry.candidate_sets (
    candidate_set_id    TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL,
    registry_version_id TEXT NOT NULL,
    normalized_goal     JSONB NOT NULL,
    focused_family      TEXT,
    shown_refs          JSONB NOT NULL DEFAULT '{}',   -- family -> [refs already shown]
    next_cursor         JSONB NOT NULL DEFAULT '{}',
    selected_candidate_id TEXT,
    unresolved_slots    JSONB NOT NULL DEFAULT '[]',
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Ref display append-only dalam satu set; tidak pernah di-recycle.

## 9. Integrity gates saat publish

Registry builder menolak publish bila:

- ada dataset `answerable` tanpa minimal satu measure `known|unitless`;
- geography item mengacu `canonical_geography_id` yang tidak ada;
- `default_item_id`/`total_item_id` tidak menunjuk item di dimension yang sama;
- template `view_name` tidak ada di allowlist;
- checksum tidak cocok dengan canonical catalog;
- SIMDASI category-primary row masih dilabeli `geography_level='kecamatan'` (P0 blocker);
- Dynamic quarterly dataset tanpa subperiod dimension (P0 blocker).

## 10. Scope catatan

Draft ini fokus registry + offering/query. Belum termasuk: working_memory schema runtime, evidence snapshots, result cache (dibahas `docs/23` §6), dan relasi registry ke snapshot mirror (dibahas `docs/17`).

## 11. Status build (15 Aug 2026)

Schema dan builder **sudah di-build dan published** (izin Tah: "yang database boleh di-build"):

```text
versions_published        : 1 (checksum + lineage di registry_versions)
datasets                  : 1.148
measures                  : 1.458
dimensions                : 1.026
dimension_items           : 7.371
geographies               : 18 (1 kabupaten + 17 kecamatan)
aliases                   : 34
query_templates           : 8   (6 lama + dynamic_latest, simdasi_latest)
blocked_quality_datasets  : 13 (unit_review_required)
```

- Migration: `migrations/001_serving_view_fixes.sql`, `migrations/002_registry_schema.sql`.
- Serving P0 sudah diperbaiki di view + source of truth `workers/ingestion/bps_storage.py` (Dynamic subperiod + unit_state, SIMDASI row_role + unit title-derived).
- Builder: `scripts/build_bps_registry.py` — deterministik, full-rebuild atomic, integrity gates sebelum publish (view allowlist, answerable dataset wajib punya measure known/unitless, unit unknown → downgrade `blocked_quality`).
- Rebuild: `uv run python scripts/build_bps_registry.py`; versi lama diberi status `retired` di `registry_versions`.

### Audit caveat — database hardening (15 Aug 2026)

- ✅ **Runtime DB role `marawa_runtime_ro`**: NOSUPERUSER/NOCREATEDB/NOCREATEROLE, `default_transaction_read_only=on`, timeouts, GRANT SELECT hanya pada allowlisted serving views + registry projections; mutation/DDL denial teruji (`tests/test_bps_database_hardening.py`).
- ✅ **Registry version retention**: primary key semua child table kini composite `(registry_version_id, ...)`; rebuild menahan snapshot versi lama (published + retired tetap queryable). Test `test_retired_registry_version_catalog_remains_queryable`.
- ✅ **`quality_flags` persist**: `unit_review_required` sekarang terpersist; test memastikan semua blocked dataset pada versi published membawa alasan eksplisit.
- ✅ **Migration ledger**: `marawa_migrations.schema_migrations` + `scripts/run_migrations.py` (status/up/down/backfill); `001–004` + `.down.sql`; up/down cycle teruji di database isolated. Catatan: recreate serving views (`ensure_schema`) menghapus grant level-objek — jalankan ulang `004_runtime_readonly_role.sql` setelah bootstrap view.
- ✅ Delapan query template runtime-safe: setiap declared parameter dipakai di SQL dengan cast eksplisit (ditegakkan integrity gate, bukan diperiksa manual); binder `scripts/bps_template_binder.py` memvalidasi required/type/injection.

### Perubahan template pasca-audit 15 Agustus

| Perubahan | Alasan |
|---|---|
| `has_own_limit` wajib dideklarasikan tiap template | Audit H1 — binder dulu mengendus substring `"LIMIT" not in sql`. Case-sensitive, dan cocok dengan alias/komentar. Efek nyatanya: `publication_list` kehilangan cap server-side dan `page_size` dari caller tak terbatas |
| Template tanpa limit sendiri kini **dibungkus** `SELECT * FROM (...) LIMIT`, bukan ditempel di ekor | Menempel di ekor salah bila SQL berakhir dengan komentar atau UNION |
| `page_size` → `integer\|max:100`, `offset` → `integer\|max:10000`; setiap integer wajib punya `max:` | Parameter integer sebelumnya hanya dicek tipenya |
| `search` → tipe `like`, SQL menambah `ESCAPE` | Audit H2 — `%` dan `_` dari user diperlakukan wildcard; search `"%"` mencocokkan seluruh katalog |
| `primary_dimension_item` dihapus dari semua template | Audit M1 — parameter itu memfilter kolom yang sama persis dengan `geography_code` |
| `secondary_dimension_item` → `category_item`; view mengekspos `category_code`/`category_label` | Sumbu kategori sesungguhnya adalah `derived_variable`; penamaan lama menyesatkan agent |
| `simdasi_point` bind `indicator_code`, bukan `indicator_name` | Audit M2 — mencocokkan label manusia string-eksak rapuh terhadap spasi, kapitalisasi, dan perubahan label upstream |
| Template baru `dynamic_latest` + `simdasi_latest` | Audit H4 — "terbaru" adalah bentuk pertanyaan publik paling umum dan sama sekali tidak punya template. Periode yang dilayani dikembalikan di hasil agar jawaban menyebut tahunnya, bukan kata "terbaru" |

Migrasi `006` menambah kolom `has_own_limit` pada `query_template_registry`, serta `queryable` / `quality_flags` / `unit_source` pada `measure_registry` dengan CHECK constraint yang melarang measure queryable tanpa unit bersumber.
- ✅ Census item-level registry sudah built (typed axes per dataset, total items code 999, cardinality terisi). 13 dataset unit-review siap paket approval (Excel + `docs/26`).
