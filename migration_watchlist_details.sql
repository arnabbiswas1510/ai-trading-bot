-- 1. Add new fundamental columns
ALTER TABLE public.watchlist ADD COLUMN IF NOT EXISTS analyst_rating varchar;
ALTER TABLE public.watchlist ADD COLUMN IF NOT EXISTS float_shares bigint;
ALTER TABLE public.watchlist ADD COLUMN IF NOT EXISTS roe float;

-- 2. Drop unused columns (safe as per technical screener usage)
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS composite_score;
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS inst_count;
