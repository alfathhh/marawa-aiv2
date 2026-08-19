-- down Migration 011
DROP INDEX IF EXISTS public.rag_selection_one_selected;
DROP INDEX IF EXISTS public.rag_selection_conv_updated;
DROP TABLE IF EXISTS public.rag_selection;
