-- ============================================================================
-- fix_nbix_reconstructed_sell.sql
--
-- *** ALREADY APPLIED TO PRODUCTION on 2026-09-15. Kept as the record of what
-- *** was done. Re-running is a safe no-op: the guard requires the old 152.74
-- *** price, which no longer matches.
--
-- Corrects trade_history id=29 (NBIX, 2026-07-31 -> 2026-08-12), whose sell
-- price was never sourced from a real fill. It was reconstructed from an FMP
-- quote on the WRONG DAY, overstating the loss by $835.83.
--
-- WHAT WENT WRONG
-- ---------------
-- The row's own sell_reason admits it:
--
--   "Trailing stop (IBKR GTC TRAIL) fired ~2026-08-13 - RECONSTRUCTED, NOT
--    manual; SLD fill missed by all 3 tiers. ... sell_price $152.74 = FMP
--    ESTIMATE; verify via IBKR Flex."
--
-- All three tiers of the sell-price ladder in reconcile_with_ibkr() were down
-- at the same time:
--
--   Tier 1  ibkr_fills          INERT. The execDetailsEvent hook was being
--                               rejected by Supabase RLS; 62 consecutive
--                               denials left the table empty for six weeks
--                               with no alert. The table's earliest surviving
--                               row is 2026-09-08 - nothing exists for August.
--   Tier 2  reqExecutions()     MISS. Session cache only. The stop fired
--                               2026-08-12; reconcile saw it in a later
--                               session, and prior-session fills are not cached.
--   Tier 3  Flex TradeConfirm   MISS at the time.
--   Fallback                    FMP live quote -> the wrong day's price.
--
-- IBKR's authoritative record (operator-supplied, TradeConfirm) shows the
-- trailing stop actually filled on 2026-08-12 in two child fills:
--
--   8/12  SELL  82 @ 158.50
--   8/12  SELL  63 @ 158.51
--              ---
--              145 sh   weighted avg 158.5043448276
--
-- 145 matches the stored share count exactly. The recorded $152.74 is not a
-- possible 8/12 print at all: NBIX traded 154.86-159.98 that day. It sits
-- inside the 8/13 range (150.20-155.81), which is precisely the off-by-one-day
-- error this ladder exists to prevent.
--
-- This is the SECOND instance of this bug class. The first was RSI on
-- 2026-07-17, which is why the three-tier ladder was built on 2026-07-21
-- (see the comment block at execution_agent.py ~L2934). NBIX slipped through
-- because Tier 1 was silently inert rather than merely unlucky.
--
-- WHAT THIS MIGRATION DOES
-- ------------------------
-- Re-prices the exit from the real fills. Unlike backfill_ntra_round_trips.sql,
-- this DOES change the book total, because the recorded price was simply wrong:
--
--                    sell_price   proceeds     P&L        pct
--   recorded         152.7400     22,147.30   -2,260.55  -9.26%
--   actual (fills)   158.5043     22,983.13   -1,424.72  -5.84%
--                                             --------
--                                  loss overstated by      835.83
--
-- Book net moves -4,722.99 -> -3,887.16. NBIX ceases to be the largest single
-- loss in the book (CDNA -1,539.37 takes that place); NBIX becomes 3rd.
--
-- NOT AFFECTED
-- ------------
--   * breakout_learnings  - has no NBIX row, so no learner contamination
--                           (this is the key difference from the NTRA incident).
--   * portfolio_positions - NBIX is closed; nothing open to repair.
--   * commissions         - commission_complete stays false. The child-fill
--                           commissions were never captured (ibkr_fills was
--                           inert), so this remains a GROSS figure. Do not set
--                           commission_complete = true.
--
-- SAFETY
-- ------
-- Idempotent: the WHERE clause matches only the uncorrected row (it requires
-- the old 152.74 price), so re-running is a no-op. Verify with the SELECTs below.
--
-- SCHEMA CONSTRAINTS -- do not reintroduce these:
--   * net_profit_loss     is GENERATED: profit_loss - buy_commission - sell_commission.
--   * commission_complete is GENERATED from whether both commissions are present.
--     Writing to either raises SQLSTATE 428C9. Set profit_loss and let net follow.
--   * sell_reason is varchar(200). The prior NBIX reason was EXACTLY 200 chars,
--     so an append (sell_reason || '...') overflows. Replace it, never append.
-- ============================================================================

BEGIN;

-- ── Before ────────────────────────────────────────────────────────────────
SELECT 'BEFORE' AS stage, id, ticker, shares, buy_price, sell_price,
       sell_date, profit_loss, net_profit_loss, percent_return
FROM   trade_history
WHERE  id = 29;

-- ── Correct the row ───────────────────────────────────────────────────────
UPDATE trade_history
SET    sell_price     = 158.5043448276,
       sell_date      = '2026-08-12T00:00:00+00:00',
       profit_loss    = -1424.72,
       percent_return = -5.84,
       sell_reason    = 'Trail stop filled 2026-08-12: 82@158.50 + 63@158.51, '
                     || 'wavg 158.5043 (IBKR TradeConfirm, corrected 2026-09-15). '
                     || 'Was FMP est 152.74 dated 8/13; all 3 fill tiers missed. '
                     || 'Loss overstated $835.83.'
WHERE  id = 29
  AND  ticker = 'NBIX'
  AND  shares = 145
  AND  ROUND(sell_price::numeric, 2) = 152.74;   -- idempotency guard

-- ── After ─────────────────────────────────────────────────────────────────
SELECT 'AFTER' AS stage, id, ticker, shares, buy_price, sell_price,
       sell_date, profit_loss, net_profit_loss, percent_return
FROM   trade_history
WHERE  id = 29;

-- ── Book total (expect -3887.16) ──────────────────────────────────────────
SELECT 'BOOK NET' AS stage, ROUND(SUM(net_profit_loss)::numeric, 2) AS net
FROM   trade_history;

-- Inspect the three SELECTs above, then COMMIT (or ROLLBACK to abort).
COMMIT;
