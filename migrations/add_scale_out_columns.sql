-- ============================================================
-- Migration: Partial scale-out bookkeeping
-- ============================================================
-- Adds the two columns the partial scale-out rule needs on every
-- open position:
--
--   scaled_out     BOOLEAN — set TRUE the first (and only) time a
--                  position's PEAK gain reaches +SCALE_OUT_TRIGGER_PCT
--                  and SCALE_OUT_FRACTION of the shares is sold at
--                  market. Guarantees the rule fires exactly once per
--                  position.
--   scaled_out_at  TIMESTAMPTZ — the instant of that partial sell.
--                  reconcile_with_ibkr() passes it as the `since`
--                  filter when it later archives the CLOSED position,
--                  so the scale-out SLD fill is excluded from the
--                  final close's weighted-average sell price and
--                  commission sum (the scale-out P&L is already booked
--                  in its own trade_history row).
--
-- See execute_scale_out() and monitor_portfolio_intraday() in
-- execution_agent.py and decisions/2026-09-08_partial-scale-out.md.
-- ============================================================

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS scaled_out    BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS scaled_out_at TIMESTAMPTZ;
