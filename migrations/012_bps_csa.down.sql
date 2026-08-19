-- down Migration 012
DROP INDEX IF EXISTS public.bps_csa_subjects_title_trgm;
DROP TABLE IF EXISTS public.bps_csa_tables;
DROP TABLE IF EXISTS public.bps_csa_subjects;
DROP TABLE IF EXISTS public.bps_csa_subcategories;
