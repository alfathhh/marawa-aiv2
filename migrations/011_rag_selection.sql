-- Migration 011: state seleksi kandidat RAG per percakapan.
--
-- RagPipeline menawarkan daftar kandidat (OFFER), lalu user memilih (SELECT).
-- Pilihan itu harus bertahan lintas pesan dan lintas restart agent. Disimpan
-- terpisah dari conversation_state agar lifecycle kandidat tidak mengotori
-- state machine handover (ADMIN_ACTIVE dsb).
--
-- status: 'offered' (daftar baru ditampilkan) | 'selected' (user sudah pilih,
--         query fakta baru boleh jalan — invariant #3) | 'cleared'.
CREATE TABLE IF NOT EXISTS public.rag_selection (
    conversation_id  text        NOT NULL,
    status           text        NOT NULL CHECK (status IN ('offered','selected','cleared')),
    family           text,
    dataset_id       text,
    indicator_code   text,
    indicator_name   text,
    period           text,
    -- snapshot daftar yang ditawarkan, agar ref "D1" bisa dipetakan balik ke
    -- dataset persis seperti yang user lihat saat memilih (auditability).
    offered_payload  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, status)
);

-- Satu seleksi aktif per percakapan: hanya satu baris 'selected'.
CREATE UNIQUE INDEX IF NOT EXISTS rag_selection_one_selected
    ON public.rag_selection (conversation_id) WHERE status = 'selected';

CREATE INDEX IF NOT EXISTS rag_selection_conv_updated
    ON public.rag_selection (conversation_id, updated_at DESC);
