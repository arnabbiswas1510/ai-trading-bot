-- Add the company_size column to track Large/Mid/Small cap stocks
ALTER TABLE public.watchlist ADD COLUMN IF NOT EXISTS company_size varchar;
