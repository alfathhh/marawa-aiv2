-- Migration 010: password-based login untuk dashboard.
-- Operator meminta login "username + password biasa" (16 Agt 2026), menggantikan
-- TOTP sebagai jalur utama. Kolom nullable dulu: admin lama (seed-super-1/2)
-- tetap bisa login selama belum di-set, login menolak admin tanpa hash dengan
-- pesan eksplisit (bukan gagal senyap). TOTP tetap didukung bila ter-enroll.
ALTER TABLE marawa_admins
    ADD COLUMN IF NOT EXISTS password_hash text;

-- Format yang diterima: pbkdf2_sha256$iterations$salt_b64$hash_b64
-- (panjang hash hex/sha256 64 char + overhead). Longgar supaya migrasi tidak
-- menolak hash yang dihasilkan versi iterasi berbeda; verifikasi terjadi di
-- aplikasi.
ALTER TABLE marawa_admins
    DROP CONSTRAINT IF EXISTS marawa_admins_password_hash_shape;
ALTER TABLE marawa_admins
    ADD CONSTRAINT marawa_admins_password_hash_shape
    CHECK (password_hash IS NULL OR length(password_hash) BETWEEN 60 AND 512);
