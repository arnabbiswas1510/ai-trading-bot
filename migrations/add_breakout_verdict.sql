-- Migration: add breakout_verdict and intraday_high_today columns
-- to portfolio_positions for the Breakout Verdict + Intraday Loss Minimiser system.
--
-- breakout_verdict:    NULL (Day 1-3, not yet evaluated) | 'PASS' | 'FAIL'
--                      Set at EOD of Day 3 by monitor_portfolio_intraday().
--
-- intraday_high_today: Rolling intraday price high seen today.
--                      Reset implicitly by being overwritten each day.
--                      Used by the Intraday Loss Minimiser (Day 4+, FAIL positions)
--                      to detect 0.5% pullback from the day's peak.

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS breakout_verdict    TEXT    DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS intraday_high_today NUMERIC DEFAULT NULL;
