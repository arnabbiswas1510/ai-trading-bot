-- Adds the O'Neil 8-week hold flag to portfolio_positions.
--
-- A position that gains POWER_HOLD_GAIN_PCT (20%) or more within
-- POWER_HOLD_TRIGGER_DAYS (21 calendar days) of entry is behaving like a genuine
-- market leader. From that point until POWER_HOLD_DURATION_DAYS (56) we suppress
-- the discretionary exits (EMA-21 exit, Rank & Replace, Intraday Loss Minimiser)
-- so the position has room to become the outsized winner CAN SLIM expectancy
-- depends on. The IBKR trailing stop is NOT suspended and remains the backstop.
--
-- The flag is persisted rather than recomputed because highest_unrealized_pct
-- keeps climbing after the trigger window closes: without a sticky flag, a
-- position that qualified on day 12 would silently lose its protection on day 22.
--
-- execution_agent.py degrades gracefully (PGRST204) if this has not been run yet,
-- so deploying the code before the migration is safe.

ALTER TABLE portfolio_positions
    ADD COLUMN IF NOT EXISTS power_hold BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN portfolio_positions.power_hold IS
    'O''Neil 8-week hold rule: TRUE once the position gained >=20% within 21 days of entry. Suppresses discretionary exits until 56 days after entry.';
