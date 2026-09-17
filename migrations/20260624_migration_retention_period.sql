ALTER TABLE public.watchlist DROP COLUMN IF EXISTS weeks_retained;
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS first_seen_at;
ALTER TABLE public.watchlist DROP COLUMN IF EXISTS last_seen_at;

ALTER TABLE public.watchlist ADD COLUMN retention_period TEXT DEFAULT '1d';
ALTER TABLE public.daily_triggers ADD COLUMN retention_period TEXT DEFAULT '1d';
