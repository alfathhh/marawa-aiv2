-- Migration 012: taksonomi CSA (Classification of Statistical Activities).
--
-- CSA = klasifikasi resmi aktivitas statistik (standar CES/BPS). Tujuannya di
-- MARAWA: memperkaya kualitas kandidat RAG — offering dikelompokkan berdasarkan
-- taksonomi resmi, bukan hanya FTS leksikal di judul (yang kita lihat di 500
-- skenario sering salah-cocok karena kata umum "jumlah"/"total").
--
-- Tiga tingkat: kategori -> subjek -> tabel. Ditambah peta subjek->judul untuk
-- memberi label kandidat RAG.

CREATE TABLE IF NOT EXISTS public.bps_csa_subcategories (
    subcat_id      integer PRIMARY KEY,
    title          text NOT NULL,
    snapshot_id    integer,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.bps_csa_subjects (
    sub_id         integer PRIMARY KEY,
    title          text NOT NULL,
    subcat_id      integer REFERENCES public.bps_csa_subcategories(subcat_id),
    snapshot_id    integer,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.bps_csa_tables (
    table_id       text NOT NULL,
    subject_csa_id integer NOT NULL REFERENCES public.bps_csa_subjects(sub_id),
    title          text NOT NULL,
    snapshot_id    integer,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (table_id, subject_csa_id)
);

-- FTS atas judul subjek CSA — dipakai RAG untuk memberi label topik kandidat.
CREATE INDEX IF NOT EXISTS bps_csa_subjects_title_trgm
    ON public.bps_csa_subjects USING gin (to_tsvector('indonesian', title));
