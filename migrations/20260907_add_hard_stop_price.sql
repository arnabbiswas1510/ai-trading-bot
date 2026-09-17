-- ============================================================
-- Migration: Static broker-side hard stop (disconnect-proof floor)
-- ============================================================
-- Adds the resting price of the STATIC STP hard stop that now sits
-- in an OCA group with the base trailing stop on every open
-- position. Unlike the trailing stop — which freezes at its
-- last-placed % when the bot disconnects and then trails from the
-- HWM — the hard stop is a fixed price that a bot outage cannot
-- move, so a fresh position cannot bleed past it to the full ATR
-- base trail. It rests at entry*(1-MAX_LOSS_PCT) while unproven and
-- ratchets UP to the give-back floor once the position proves and
-- arms (+2% peak). See hard_stop_price() and place_protective_stops()
-- in execution_agent.py and decisions/2026-09-07_static-hard-stop.md.
-- ============================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS hard_stop_price NUMERIC;
