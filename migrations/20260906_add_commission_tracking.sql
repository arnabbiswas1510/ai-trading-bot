-- Migration: record IBKR commissions so reported P&L matches the broker
--
-- Purpose: every P&L number the dashboard shows was GROSS. `profit_loss` is
--          computed as (sell_price - buy_price) * shares and has never carried a
--          cost term, while the summary's `total_pnl` is derived from IBKR's own
--          cash balance -- which is real money and therefore already net of
--          commissions.
--
--          The two disagreed by exactly the commissions paid. On 2026-09-04:
--
--              total_pnl (from IBKR cash)              -4,009.20
--              realized (sum profit_loss) + unrealized -3,528.29
--              difference                                -480.91
--
--          $480.91 across 65 fills. The dashboard was contradicting itself and
--          neither figure was labelled, so there was no way to tell which one to
--          believe.
--
-- Design:  `profit_loss` stays GROSS and is never rewritten. The exit-rule
--          research in research/exit_rule_replay.py explicitly does not model
--          commissions and every threshold in decisions/ was measured against
--          gross figures; silently redefining the column would make all of those
--          benchmarks non-comparable with future runs without any signal that
--          the basis had changed.
--
--          Net is therefore a separate, derived column.
--
-- NULL vs 0: a NULL commission means UNKNOWN -- the fill data has not been
--          recovered from IBKR yet. It does NOT mean the trade was free. Writing
--          0 for an unrecovered fill would silently overstate net P&L and would
--          be indistinguishable from a genuinely zero-commission fill. Callers
--          must surface the difference; see the commission_complete note below.
--
-- See decisions/2026-09-06_commission-accounting.md
-- Run once in Supabase SQL Editor.

-- ── Closed trades ────────────────────────────────────────────────────────────
ALTER TABLE trade_history
  ADD COLUMN IF NOT EXISTS buy_commission  NUMERIC DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS sell_commission NUMERIC DEFAULT NULL;

COMMENT ON COLUMN trade_history.buy_commission IS
  'IBKR commission paid on the ENTRY fill(s), in USD, as a positive number. '
  'Sourced from CommissionReport.commission via ibkr_fills, or from the IBKR Flex '
  'Trades report during backfill. NULL means not yet recovered from IBKR -- it does '
  'NOT mean zero. Never populate this with an estimate from a fee schedule.';

COMMENT ON COLUMN trade_history.sell_commission IS
  'IBKR commission paid on the EXIT fill(s), in USD, as a positive number. '
  'Same NULL semantics as buy_commission. Exits triggered by a resting IBKR GTC '
  'trailing stop fill while the agent may be disconnected, so this is frequently '
  'recovered later by backfill rather than captured live.';

-- Derived, not authored. A generated column cannot drift from its inputs, and it
-- updates automatically when a backfill later supplies a missing commission.
--
-- COALESCE to 0 is required (a NULL input would poison the whole expression), so
-- this column ALONE cannot distinguish "no commission" from "commission unknown".
-- Consumers MUST check commission_complete before presenting it as final.
ALTER TABLE trade_history
  ADD COLUMN IF NOT EXISTS net_profit_loss NUMERIC
    GENERATED ALWAYS AS (
      profit_loss - COALESCE(buy_commission, 0) - COALESCE(sell_commission, 0)
    ) STORED;

COMMENT ON COLUMN trade_history.net_profit_loss IS
  'profit_loss minus both commissions. Generated, so it can never drift and it '
  'self-corrects when a backfill fills in a NULL. Treats unknown commissions as 0, '
  'so it is an UPPER BOUND on true net whenever commission_complete is false.';

ALTER TABLE trade_history
  ADD COLUMN IF NOT EXISTS commission_complete BOOLEAN
    GENERATED ALWAYS AS (
      buy_commission IS NOT NULL AND sell_commission IS NOT NULL
    ) STORED;

COMMENT ON COLUMN trade_history.commission_complete IS
  'True only when BOTH legs have real IBKR commission data. The dashboard must '
  'label net_profit_loss as provisional wherever this is false, for the same reason '
  'the position table labels an FMP price: an unlabelled number is read with the '
  'same confidence as a verified one.';

-- ── Open positions ───────────────────────────────────────────────────────────
-- The entry commission is known at buy time but the position may stay open for
-- weeks. Capturing it here means it is already available when the position
-- closes, rather than having to re-derive an entry fill from months of history.
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS buy_commission NUMERIC DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.buy_commission IS
  'IBKR commission paid to OPEN this position, in USD, positive. Carried over to '
  'trade_history.buy_commission when the position closes. NULL means not yet '
  'recovered; see the NULL-vs-zero note on trade_history.buy_commission.';

-- No back-fill of zeros. Every existing row stays NULL until real IBKR fill data
-- is recovered, which is what makes the "provisional" label on the dashboard
-- honest rather than decorative.

-- Verification:
-- SELECT ticker, shares, profit_loss, buy_commission, sell_commission,
--        net_profit_loss, commission_complete
-- FROM trade_history
-- ORDER BY sell_date DESC;
--
-- Outstanding recovery work:
-- SELECT count(*) FILTER (WHERE NOT commission_complete) AS needs_backfill,
--        count(*)                                       AS total
-- FROM trade_history;
