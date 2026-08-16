-- Migration 007: runtime conversation tables.
--
-- Replaces the in-memory `Store` in scripts/app.py. The reason this matters is
-- not persistence — it is correctness under concurrency.
--
-- `compare_and_set()` in the in-memory store used a Python threading.Lock,
-- which only guards a single process. Two uvicorn workers would each hold
-- their own lock and the lost-update bug (audit F) returns: two officers both
-- receive 200 and both believe they hold the conversation.
--
-- The real guarantee is `UPDATE ... WHERE state_version = %s` with a rowcount
-- check. Same guarantee, different mechanism, and the tests do not change.

CREATE TABLE IF NOT EXISTS marawa_conversations (
    conversation_id         text PRIMARY KEY,
    wa_contact_hash         text NOT NULL,
    display_name            text,
    state                   text NOT NULL DEFAULT 'BOT_ACTIVE',
    state_version           integer NOT NULL DEFAULT 0,
    assigned_admin_id       text,
    bot_paused_by           text,
    bot_paused_at           timestamptz,
    last_admin_activity_at  timestamptz,
    last_activity_at        timestamptz,
    handover_requested_at   timestamptz,
    resume_watermark_at     timestamptz,
    last_notified_at        timestamptz,
    agent_run_active        boolean NOT NULL DEFAULT false,
    unread_count            integer NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marawa_conversations_state_check
        CHECK (state IN ('BOT_ACTIVE','QUEUED','ADMIN_ACTIVE','IDLE_CLOSED')),
    -- A paused conversation must record who paused it, so the audit log can
    -- answer "who silenced the bot here". Phone takeovers adopt an owner on the
    -- first dashboard action (audit A).
    CONSTRAINT marawa_conversations_version_non_negative
        CHECK (state_version >= 0)
);

CREATE INDEX IF NOT EXISTS idx_marawa_conversations_activity
    ON marawa_conversations (last_activity_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_marawa_conversations_state
    ON marawa_conversations (state)
    WHERE state IN ('QUEUED','ADMIN_ACTIVE');

CREATE TABLE IF NOT EXISTS marawa_messages (
    message_id       bigserial PRIMARY KEY,
    conversation_id  text NOT NULL REFERENCES marawa_conversations(conversation_id) ON DELETE CASCADE,
    direction        text NOT NULL,
    sender_type      text NOT NULL,
    sender_admin_id  text,
    body             text NOT NULL,
    wa_message_id    text,
    status           text NOT NULL DEFAULT 'stored',
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marawa_messages_direction_check CHECK (direction IN ('in','out')),
    CONSTRAINT marawa_messages_sender_check CHECK (sender_type IN ('user','bot','admin','system'))
);

CREATE INDEX IF NOT EXISTS idx_marawa_messages_conversation
    ON marawa_messages (conversation_id, created_at);
-- Inbound de-duplication: WhatsApp bridges redeliver. Partial unique index so
-- rows without an id (internal system notices) are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_marawa_messages_wa_id
    ON marawa_messages (wa_message_id) WHERE wa_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS marawa_outbox (
    outbox_id                  text PRIMARY KEY,
    conversation_id            text NOT NULL REFERENCES marawa_conversations(conversation_id) ON DELETE CASCADE,
    body                       text NOT NULL,
    sender_type                text NOT NULL,
    sender_admin_id            text,
    state_version_at_enqueue   integer NOT NULL,
    status                     text NOT NULL DEFAULT 'pending',
    attempts                   integer NOT NULL DEFAULT 0,
    claimed_at                 timestamptz,
    claimed_by                 text,
    next_attempt_at            timestamptz,
    wa_message_id              text,
    idempotency_key            text,
    last_error                 text,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marawa_outbox_status_check
        CHECK (status IN ('pending','claimed','sent','delivered','failed','cancelled','unknown'))
);

-- One logical send, once. Keyed on the client's request id (audit H: keying on
-- message text meant an officer typing "ok" twice lost the second one).
CREATE UNIQUE INDEX IF NOT EXISTS uq_marawa_outbox_idempotency
    ON marawa_outbox (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_marawa_outbox_claimable
    ON marawa_outbox (status, next_attempt_at NULLS FIRST)
    WHERE status IN ('pending','claimed');

CREATE TABLE IF NOT EXISTS marawa_admins (
    admin_id      text PRIMARY KEY,
    name          text NOT NULL,
    role          text NOT NULL,
    totp_secret   text,
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT marawa_admins_role_check CHECK (role IN ('admin','superadmin'))
);

CREATE TABLE IF NOT EXISTS marawa_settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_by  text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Append-only by grant, not by convention (docs/06 §3.0b). The application role
-- receives INSERT and SELECT only; no UPDATE, no DELETE, not even for
-- superadmin. An audit log a privileged user can edit answers nothing.
CREATE TABLE IF NOT EXISTS marawa_audit_log (
    audit_id         bigserial PRIMARY KEY,
    at               timestamptz NOT NULL DEFAULT now(),
    action           text NOT NULL,
    admin_id         text,
    conversation_id  text,
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_marawa_audit_at ON marawa_audit_log (at DESC);

-- Guard against the last superadmin being removed or demoted, which would lock
-- the office out of WhatsApp pairing and user management entirely
-- (docs/06 §3.1 break-glass).
CREATE OR REPLACE FUNCTION marawa_guard_last_superadmin() RETURNS trigger AS $$
DECLARE
    remaining integer;
BEGIN
    SELECT count(*) INTO remaining
    FROM marawa_admins
    WHERE role = 'superadmin' AND active
      AND admin_id <> COALESCE(OLD.admin_id, '');
    IF remaining < 1 THEN
        RAISE EXCEPTION 'refusing to remove the last active superadmin (%). See docs/06 §3.1.', OLD.admin_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_marawa_guard_last_superadmin ON marawa_admins;
CREATE TRIGGER trg_marawa_guard_last_superadmin
    BEFORE UPDATE OR DELETE ON marawa_admins
    FOR EACH ROW
    WHEN (OLD.role = 'superadmin' AND OLD.active)
    EXECUTE FUNCTION marawa_guard_last_superadmin();
