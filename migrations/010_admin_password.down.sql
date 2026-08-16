-- Down for migration 010.
ALTER TABLE marawa_admins
    DROP CONSTRAINT IF EXISTS marawa_admins_password_hash_shape;
ALTER TABLE marawa_admins
    DROP COLUMN IF EXISTS password_hash;
