-- ============================================================================
-- backfill_ntra_round_trips.sql
--
-- Repairs the NTRA (2026-08-26 → 2026-09-10) accounting damage caused by the
-- contaminated-averageCost bug in reconcile_with_ibkr().
--
-- WHAT WENT WRONG
-- ---------------
-- IBKR's authoritative TradeConfirm record shows NTRA was round-tripped THREE
-- times, not once:
--
--   8/26 11:32  BUY   40 @ 338.4300
--   8/31 09:35  SELL  40 @ 320.8203   (2 child fills, 20 + 20)
--   8/31 09:46  BUY   61 @ 320.4900
--   8/31 10:26  SELL  61 @ 317.8600
--   8/31 10:32  BUY   61 @ 317.4295   <- the lot actually held
--   9/10 09:30  SELL  61 @ 321.0500
--
-- Only the final round trip reached trade_history (id 53). IBKR then reported
-- averageCost = 331.70 for the surviving lot, which is exactly
--   (total buys + commissions - total sell proceeds) / 61
-- i.e. the two earlier realised losses buried inside the open lot's basis.
-- 331.70 is above NTRA's entire 8/31 trading range, so no fill occurred there.
--
-- The drift guard adopted 331.70 over the true 317.4295, so row 53 recorded a
-- phantom -$649.65 loss on what was really a +$220.85 gross winner. The book
-- total was right by accident; every per-trade number was wrong.
--
-- WHAT THIS MIGRATION DOES
-- ------------------------
-- Splits the single contaminated row back into the three real round trips.
-- The aggregate is preserved EXACTLY -- this migration does not change how much
-- money was made or lost, only which trade made or lost it:
--
--   RT1  40 sh  338.4300 -> 320.8203   gross -704.39  comm 2.27  net -706.66
--   RT2  61 sh  320.4900 -> 317.8600   gross -160.43  comm 2.41  net -162.84
--   RT3  61 sh  317.4295 -> 321.0500   gross +220.85  comm 2.42  net +218.43
--                                                          SUM   net -651.07
--
-- -651.07 is precisely the net_profit_loss currently stored on row 53.
--
-- Commissions are IBKR's reported figures, so commission_complete is set true.
--
-- SAFETY
-- ------
-- Affects the trade_history ledger only. It does NOT touch live IBKR positions
-- and cannot place or cancel an order. Idempotent: the inserts are guarded so
-- re-running will not duplicate the backfilled rows.
-- ============================================================================

BEGIN;

-- ── 1. Correct the surviving row to its true cost basis ─────────────────────
-- 317.4295 is the actual 10:32 execution price; 331.70 was the contaminated
-- averageCost. This flips row 53 from a -$649.65 loss to a +$218.43 winner.
UPDATE trade_history
SET buy_price           = 317.4295,
    buy_date            = '2026-08-31T14:32:05+00:00',
    buy_commission      = 1.000183,
    sell_commission     = 1.4155,
    profit_loss         = 220.85,
    net_profit_loss     = 218.43,
    percent_return      = 1.14,
    commission_complete = true,
    sell_reason         = sell_reason ||
        ' [corrected 2026-09-10: buy_price was 331.70, a contaminated IBKR '
        'averageCost that folded in two earlier NTRA round trips; true fill '
        'was 317.4295 — this trade was a +$218 winner]'
WHERE id = 53
  AND ticker = 'NTRA'
  AND buy_price = 331.7;

-- ── 2. Backfill round trip 1: 8/26 buy 40 → 8/31 sell 40 ────────────────────
INSERT INTO trade_history (
    ticker, shares, buy_price, sell_price, buy_date, sell_date,
    buy_commission, sell_commission, commission_complete,
    profit_loss, net_profit_loss, percent_return, buy_reason, sell_reason
)
SELECT 'NTRA', 40, 338.4300, 320.8203,
       '2026-08-26T15:32:15+00:00', '2026-08-31T13:35:56+00:00',
       1.00012, 1.272276, true,
       -704.39, -706.66, -5.20,
       'CANSLIM Breakout [daily_triggers]',
       'Backfilled 2026-09-10 from IBKR TradeConfirm — this exit was never '
       'written to trade_history; its loss was silently absorbed into row 53 '
       'via a contaminated averageCost.'
WHERE NOT EXISTS (
    SELECT 1 FROM trade_history
    WHERE ticker = 'NTRA' AND shares = 40 AND buy_price = 338.4300
);

-- ── 3. Backfill round trip 2: 8/31 buy 61 → 8/31 sell 61 (same day) ─────────
-- This is the churn the cooling-off gate failed to prevent: sold 10:26,
-- re-bought 10:32.
INSERT INTO trade_history (
    ticker, shares, buy_price, sell_price, buy_date, sell_date,
    buy_commission, sell_commission, commission_complete,
    profit_loss, net_profit_loss, percent_return, buy_reason, sell_reason
)
SELECT 'NTRA', 61, 320.4900, 317.8600,
       '2026-08-31T13:46:14+00:00', '2026-08-31T14:26:13+00:00',
       1.000183, 1.411501, true,
       -160.43, -162.84, -0.82,
       'CANSLIM Breakout [daily_triggers]',
       'Backfilled 2026-09-10 from IBKR TradeConfirm — same-day round trip '
       'never written to trade_history, which left the cooling-off gate blind '
       'and allowed a re-entry six minutes later.'
WHERE NOT EXISTS (
    SELECT 1 FROM trade_history
    WHERE ticker = 'NTRA' AND shares = 61 AND buy_price = 320.4900
);

COMMIT;

-- ── Verification ────────────────────────────────────────────────────────────
-- Expect 3 rows summing to -651.07, unchanged from the single row before:
--
--   SELECT ticker, shares, buy_price, sell_price, net_profit_loss
--   FROM trade_history WHERE ticker = 'NTRA' ORDER BY buy_date;
--
--   SELECT ROUND(SUM(net_profit_loss)::numeric, 2) AS should_be_minus_651_07
--   FROM trade_history WHERE ticker = 'NTRA';
