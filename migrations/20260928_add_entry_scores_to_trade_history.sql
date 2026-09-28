-- 20260928_add_entry_scores_to_trade_history.sql
--
-- Archive the entry-decision provenance onto closed trades so the AI's value can
-- be measured, not inferred.
--
-- WHY THIS EXISTS
--
-- Every candidate the bot buys is graded twice at entry: the technical CAN SLIM
-- quality score, and the AI rating (ai_evaluator.py) that maps to a letter grade
-- and a +5/+15 score bonus. Those four values are written onto the
-- portfolio_positions row at buy time (buying.py / force_buy.py):
--
--     entry_quality_score   technical quality score (0-100)
--     entry_ai_rating       raw AI rating 1-100
--     entry_ai_grade        the AI letter grade A/B/C/D
--     entry_final_score     quality_score + AI grade bonus (what slots rank on)
--
-- but the position row is DELETED when the trade closes, so none of them reach
-- trade_history. The realised return of every closed trade therefore has no
-- durable record of how good the screener or the AI thought the setup was.
--
-- That gap is not hypothetical. On 2026-09-28 the review "is the AI actually
-- helping pick winners?" could recover entry_final_score for only 35 of 67
-- closed trades (via the separate breakout_learnings table) and the raw AI grade
-- for NONE of them. The analysis had to infer the AI's contribution from the
-- veto log and the composite score rather than correlate entry_ai_grade against
-- percent_return directly. These columns close that gap for every trade closed
-- from here on, and are the precondition for the AI on/off A/B replay.
--
-- Design: these mirror the identically named columns already on
-- portfolio_positions. profit_loss / percent_return stay the authoritative
-- OUTCOME columns; these are the authoritative ENTRY columns. NULL means the
-- provenance was not captured (a trade closed before this migration, or a
-- position row that never carried the value), never that the score was zero.
--
-- Re-runnable: guarded with IF NOT EXISTS per the idempotent-migrations rule.
--
-- See decisions/2026-09-28_archive-entry-scores-to-trade-history.md

ALTER TABLE trade_history
    ADD COLUMN IF NOT EXISTS entry_quality_score NUMERIC DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS entry_ai_rating     NUMERIC DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS entry_ai_grade      TEXT    DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS entry_final_score   NUMERIC DEFAULT NULL;

COMMENT ON COLUMN trade_history.entry_quality_score IS
  'Technical CAN SLIM quality score (0-100) at the moment this trade was bought, '
  'copied from portfolio_positions.entry_quality_score before that row was '
  'deleted. NULL means not captured (trade closed before the 2026-09-28 '
  'archival migration), never zero.';

COMMENT ON COLUMN trade_history.entry_ai_rating IS
  'Raw AI rating 1-100 from ai_evaluator.py at buy time. NULL = not captured.';

COMMENT ON COLUMN trade_history.entry_ai_grade IS
  'AI letter grade (A/B/C/D) the rating mapped to at buy time. A>=70 (+15 bonus), '
  'B>=55 (+5), C>=50 (0), D<50 (veto). NULL = not captured.';

COMMENT ON COLUMN trade_history.entry_final_score IS
  'entry_quality_score + the AI grade bonus -- the composite value the 5 slots '
  'were ranked on at entry. NULL = not captured. This is the entry counterpart '
  'to the percent_return / profit_loss outcome columns.';

-- Verification (expect 4 rows, all present after this runs):
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(c, ', ') INTO missing
    FROM (VALUES ('entry_quality_score'), ('entry_ai_rating'),
                 ('entry_ai_grade'), ('entry_final_score')) AS want(c)
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'trade_history' AND column_name = want.c
    );
    IF missing IS NULL THEN
        RAISE NOTICE 'OK: trade_history entry-score columns all present.';
    ELSE
        RAISE WARNING 'FAIL: trade_history missing columns: %', missing;
    END IF;
END $$;
