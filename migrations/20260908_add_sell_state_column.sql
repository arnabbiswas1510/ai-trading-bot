-- ============================================================
-- Migration: Sell-state transition tracking
-- ============================================================
-- Adds one column used by the sell-state transition notifier:
--
--   sell_state  TEXT — the last GOVERNING exit regime observed for this open
--               position by monitor_portfolio_intraday(). One of:
--               UNPROVEN, PROVEN, PROVEN_FLOOR, PROFIT_LOCKED, POWER_HOLD,
--               EXITING. Each cycle the agent recomputes the regime; when it
--               differs from this stored value it sends a concise Telegram and
--               updates the column, so every transition fires exactly once and
--               survives restarts.
--
-- Until this column exists the feature is inert (the agent logs a one-line
-- notice and skips the notification rather than spamming every cycle).
--
-- See sell_state_code() / maybe_notify_sell_state() in execution_agent.py and
-- decisions/2026-09-08_sell-state-transitions.md.
-- ============================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS sell_state TEXT;
