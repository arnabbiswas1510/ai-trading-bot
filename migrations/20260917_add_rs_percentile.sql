-- Migration: shadow relative-strength ranking columns
-- Run once in Supabase SQL Editor. Requires 20260809_add_trigger_history.sql first.
--
-- WHY THIS EXISTS
-- ---------------
-- compute_rs_score() (scoring.py) returns a flat 100 for ANY stock beating SPY
-- by >= 10% over 12 weeks. The watchlist is pre-filtered to growth names already
-- trading near their 52-week highs, so almost every candidate clears that bar:
-- measured 2026-09-17 over all 233 rows of trigger_history, rs_score was
-- exactly 100 in 76% of them. A feature that takes the same value for three of
-- every four candidates cannot rank them, so the 10% weight it carries in
-- compute_final_score is doing far less work than the formula implies.
--
-- O'Neil's RS Rating is a market-wide PERCENTILE (1-99) precisely to avoid this
-- degeneracy. These columns exist to test that alternative.
--
-- THESE COLUMNS ARE SHADOW / RESEARCH ONLY.
-- ------------------------------------------
-- Nothing in the buy path, the ranking, the sizing or any exit rule reads them.
-- compute_final_score() is UNCHANGED and still consumes the saturated rs_score.
-- The screener writes these, the weekly backfill labels them with forward
-- returns, and a scheduled review then decides — on evidence — whether the
-- percentile ranks better than the score it would replace.
--
-- WHY SHADOW AND NOT A DIRECT FIX
-- -------------------------------
-- The saturation is an established defect, but the SIGN of the repair is not
-- established. Every sample available on 2026-09-17 suggests that within this
-- already-momentum-filtered universe, MORE relative strength has been WORSE:
--
--   * corr(rs_score, fwd_20d_pct) = -0.68 on the first 16 labelled triggers
--   * across 48 closed trades, mean 12-week return at entry was 28.1% for
--     winners and 31.1% for losers
--   * CDNA entered on a +121% 12-week return and became the largest loss in
--     the book (-$1,539)
--
-- If that sign holds, a "correct" percentile that promotes the highest-RS
-- candidates would make selection worse, not better. Shipping the shadow column
-- first makes the decision a measurement instead of an argument.
-- See decisions/2026-09-17_rs-percentile-shadow-column.md.
--
-- DESIGN NOTES
-- ------------
-- 1. The RAW inputs are stored, not just the derived percentile. rs_12w_return
--    and rs_excess_return are what any future ranking scheme needs; storing only
--    a percentile would lock the archive into today's definition of the rank and
--    make a different one un-testable without re-fetching every price series.
--
-- 2. rs_percentile ranks within the day's own trigger cohort, not the market.
--    That is deliberate: final_score is used to SORT one morning's candidates
--    against each other, so the cohort is the comparison that actually decides
--    which ticker gets a slot.
--
-- 3. Nullable with no default. NULL means "screener had not yet computed this
--    when the row was written", which must stay distinguishable from a genuine
--    50th percentile.

ALTER TABLE daily_triggers
  ADD COLUMN IF NOT EXISTS rs_12w_return    FLOAT,
  ADD COLUMN IF NOT EXISTS rs_excess_return FLOAT,
  ADD COLUMN IF NOT EXISTS rs_percentile    INT;

ALTER TABLE trigger_history
  ADD COLUMN IF NOT EXISTS rs_12w_return    FLOAT,
  ADD COLUMN IF NOT EXISTS rs_excess_return FLOAT,
  ADD COLUMN IF NOT EXISTS rs_percentile    INT;

COMMENT ON COLUMN daily_triggers.rs_12w_return IS
  'Raw 12-week price return of the stock, percent. Shadow/research only.';
COMMENT ON COLUMN daily_triggers.rs_excess_return IS
  'rs_12w_return minus the SPY 12-week return, percent. This is the quantity compute_rs_score() clips at +10%. Shadow/research only.';
COMMENT ON COLUMN daily_triggers.rs_percentile IS
  'Rank of rs_excess_return within the same trigger cohort, 1-99. Shadow/research only -- NOT read by final_score or any buy/sell rule.';

COMMENT ON COLUMN trigger_history.rs_12w_return IS
  'Archived copy of daily_triggers.rs_12w_return. Shadow/research only.';
COMMENT ON COLUMN trigger_history.rs_excess_return IS
  'Archived copy of daily_triggers.rs_excess_return. Shadow/research only.';
COMMENT ON COLUMN trigger_history.rs_percentile IS
  'Archived copy of daily_triggers.rs_percentile. Paired with fwd_5d_pct / fwd_20d_pct by backfill_trigger_outcomes.py, this is what tests whether a percentile ranks better than the saturated rs_score.';

-- Verification
SELECT 'daily_triggers.rs_percentile'    AS column, CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='daily_triggers'  AND column_name='rs_percentile')    THEN 'OK' ELSE 'FAIL' END AS status
UNION ALL SELECT 'daily_triggers.rs_12w_return',    CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='daily_triggers'  AND column_name='rs_12w_return')    THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'daily_triggers.rs_excess_return', CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='daily_triggers'  AND column_name='rs_excess_return') THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'trigger_history.rs_percentile',   CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='trigger_history' AND column_name='rs_percentile')    THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'trigger_history.rs_12w_return',   CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='trigger_history' AND column_name='rs_12w_return')    THEN 'OK' ELSE 'FAIL' END
UNION ALL SELECT 'trigger_history.rs_excess_return',CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='trigger_history' AND column_name='rs_excess_return') THEN 'OK' ELSE 'FAIL' END;
