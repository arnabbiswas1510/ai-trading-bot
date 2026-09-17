-- Migration: add position analysis columns to portfolio_positions
-- These columns support the EOD PARAM_DRIFT analysis loop in execution_agent.py:
-- - entry_volume_surge / entry_pivot_distance_pct: captured at buy time (missing previously)
-- - param_drift: JSON snapshot of all 6 parameter drifts, updated each EOD for underperformers
-- - analysis_reason: deterministic human-readable attribution string
-- - analysis_ai_grade: AI re-evaluation grade (A-F) for held underperforming position
-- - analysis_date: date of last analysis (to avoid redundant re-runs on same day)

ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS entry_volume_surge       NUMERIC DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS entry_pivot_distance_pct NUMERIC DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS param_drift              JSONB   DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS analysis_reason          TEXT    DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS analysis_ai_grade        TEXT    DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS analysis_date            DATE    DEFAULT NULL;

COMMENT ON COLUMN portfolio_positions.entry_volume_surge IS
  'Volume surge ratio at entry (from daily_triggers row). Used as baseline for drift detection.';

COMMENT ON COLUMN portfolio_positions.entry_pivot_distance_pct IS
  'Distance from pivot at entry (negative = above pivot). Baseline for drift.';

COMMENT ON COLUMN portfolio_positions.param_drift IS
  'JSONB: per-parameter drift at last EOD analysis. '
  'Keys: volume_surge, rs_score, technical_score, ai_rating, sentiment_score, pivot_distance_pct. '
  'Each value: {"entry": N, "current": N, "drift": N, "failed": bool}';

COMMENT ON COLUMN portfolio_positions.analysis_reason IS
  'Deterministic human-readable string written by execution_agent.py at EOD analysis. '
  'Explains which breakout parameters have shifted and why the position is losing.';

COMMENT ON COLUMN portfolio_positions.analysis_ai_grade IS
  'AI re-evaluation grade (A/B/C/D/F) from evaluate_held_position() in ai_evaluator.py. '
  'Written alongside param_drift for UI display.';

COMMENT ON COLUMN portfolio_positions.analysis_date IS
  'Date of last param_drift analysis. Used to skip redundant same-day re-analysis.';

-- Phase 2: adjusted_score and failure_penalty are written to daily_triggers (not portfolio_positions)
-- No migration needed here — daily_triggers is truncated daily so columns added below survive:
ALTER TABLE daily_triggers
  ADD COLUMN IF NOT EXISTS adjusted_score  INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS failure_penalty INTEGER DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS penalty_reason  TEXT    DEFAULT NULL;

COMMENT ON COLUMN daily_triggers.adjusted_score IS
  'final_score minus failure_penalty. Used as the buy gate threshold when Phase 2 is active '
  '(breakout_learnings has >= 10 rows). NULL = Phase 2 not yet active, use final_score.';

COMMENT ON COLUMN daily_triggers.failure_penalty IS
  'Points deducted from final_score based on time-decayed historical failure patterns '
  'from breakout_learnings (30d=3x, 30-90d=2x, >90d=1x). Capped at 20 pts.';

COMMENT ON COLUMN daily_triggers.penalty_reason IS
  'Human-readable explanation of why the penalty was applied.';
