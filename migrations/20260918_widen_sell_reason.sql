-- 20260918_widen_sell_reason.sql
--
-- Widen trade_history.sell_reason from varchar(200) to text.
--
-- WHY THIS IS URGENT, NOT COSMETIC
--
-- sell_reason is not a label, it is the exit's flight recorder: trail in force,
-- high-water mark, trigger price, hold day, peak excursion, armed state. It is
-- the only record of WHY a position was closed, and 200 characters is no longer
-- enough to hold it. Live rows already reach 190:
--
--   190  "Trail stop filled 2026-08-12: 82@158.50 + 63@158.51, wavg 158.5043 ..."
--   188  "Trailing stop (IBKR GTC TRAIL), day 6. Basis corrected 2026-09-15 ..."
--
-- and migrations/20260915_fix_nbix_reconstructed_sell.sql records one that was
-- EXACTLY 200, forcing that repair to replace the string rather than append to
-- it. The margin is already gone.
--
-- The failure mode this prevents is worse than a lost log line. In
-- handle_mock_sell() and the reconcile path the position is DELETED from
-- portfolio_positions BEFORE the trade_history insert. Postgres raises on a
-- varchar overflow rather than truncating, so an over-long reason would abort
-- the insert after the delete had already committed -- destroying the position
-- row and leaving no trade record at all. A diagnostic string must never be
-- able to lose a trade.
--
-- text has no length limit and, in Postgres, is stored identically to varchar
-- with no performance or storage difference. There was never a reason for the
-- cap; it was an arbitrary default carried from the original schema.
--
-- Widening is non-destructive and non-blocking: varchar(n) -> text is a binary
-- coercible change, so Postgres performs it without rewriting the table and
-- without a long ACCESS EXCLUSIVE hold. No existing value is altered.
--
-- See decisions/2026-09-18_sell-reason-fill-derived-anchor.md for the change
-- that exposed this.

ALTER TABLE trade_history
    ALTER COLUMN sell_reason TYPE text;

-- buy_reason is written by the same code paths, carries the same kind of
-- context, and has the same cap and the same delete-then-insert exposure.
-- Leaving it at varchar(200) would simply move the failure rather than fix it.
ALTER TABLE trade_history
    ALTER COLUMN buy_reason TYPE text;

-- Verification (expect data_type = 'text', character_maximum_length = NULL):
--
--   SELECT column_name, data_type, character_maximum_length
--     FROM information_schema.columns
--    WHERE table_name = 'trade_history'
--      AND column_name IN ('sell_reason', 'buy_reason');
