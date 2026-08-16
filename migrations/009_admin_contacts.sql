-- Migration 009: nomor WhatsApp petugas untuk notifikasi antrean.
--
-- Menggantikan satu JID grup dengan daftar nomor perorangan yang bisa
-- ditambah/dihapus dari dashboard.
--
-- KENAPA NOMOR PETUGAS HARUS DIBLOKIR DARI BOT
-- --------------------------------------------
-- Bot mengirim notifikasi KE nomor petugas. Di WhatsApp, itu membuat
-- percakapan biasa. Tanpa penjaga, tiga hal terjadi berurutan:
--
--   1. Petugas membalas notifikasi ("oke, saya cek") — refleks yang sangat
--      wajar. Bot memperlakukannya sebagai pertanyaan warga dan menjawab
--      soal statistik.
--   2. Nomor petugas muncul di kotak masuk sebagai percakapan warga,
--      mengotori papan triase yang seharusnya hanya berisi orang yang
--      benar-benar menunggu.
--   3. Petugas bisa mengetik "ADMIN" dan masuk antrean sendiri, memicu
--      notifikasi ke dirinya sendiri.
--
-- Karena itu `blocked_from_bot` default TRUE, dan filternya berjalan di
-- webhook SEBELUM percakapan dibuat.

CREATE TABLE IF NOT EXISTS marawa_admin_contacts (
    contact_id        bigserial PRIMARY KEY,
    -- Disimpan sudah dinormalisasi (62xxxxxxxxxx, tanpa +, spasi, atau
    -- akhiran JID). Normalisasi WAJIB seragam: satu nomor yang tersimpan
    -- sebagai "08123" sementara WhatsApp mengirim "628123" berarti filternya
    -- gagal diam-diam — notifikasi tetap terkirim, tetapi blokirnya tidak
    -- bekerja dan tidak ada yang error.
    phone_e164        text NOT NULL UNIQUE,
    label             text NOT NULL,
    admin_id          text REFERENCES marawa_admins(admin_id) ON DELETE SET NULL,
    notify            boolean NOT NULL DEFAULT true,
    blocked_from_bot  boolean NOT NULL DEFAULT true,
    active            boolean NOT NULL DEFAULT true,
    created_by        text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marawa_admin_contacts_phone_normalized
        CHECK (phone_e164 ~ '^[1-9][0-9]{7,17}$')
);

CREATE INDEX IF NOT EXISTS idx_marawa_admin_contacts_notify
    ON marawa_admin_contacts (phone_e164) WHERE active AND notify;

-- Percakapan yang berasal dari nomor petugas ditandai supaya tidak ikut
-- tampil di kotak masuk. Tanpa kolom ini, thread notifikasi ke petugas akan
-- muncul sebagai "warga" dan menenggelamkan orang yang benar-benar menunggu.
ALTER TABLE marawa_conversations
    ADD COLUMN IF NOT EXISTS is_staff_channel boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_marawa_conversations_inbox
    ON marawa_conversations (last_activity_at DESC NULLS LAST)
    WHERE NOT is_staff_channel;

-- Migration 008 predates this table. Without explicit grants the deployed
-- marawa_runtime_rw role gets permission denied although admin tests run as
-- the ingest/admin role.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marawa_admin_contacts TO marawa_runtime_rw;
GRANT USAGE ON SEQUENCE public.marawa_admin_contacts_contact_id_seq TO marawa_runtime_rw;
