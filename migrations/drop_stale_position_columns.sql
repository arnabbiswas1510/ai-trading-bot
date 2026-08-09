-- Migration: Drop stale / dead columns from portfolio_positions
-- Run once in Supabase SQL Editor.
--
-- Every column below was verified to have ZERO decision-logic readers before
-- being listed here. See decisions/2026-08-09_drop-stale-position-columns.md.
--
-- ⚠️ DESTRUCTIVE AND IRREVERSIBLE. Take a snapshot of portfolio_positions first:
--     CREATE TABLE portfolio_positions_backup_20260809 AS
--       SELECT * FROM portfolio_positions;

BEGIN;

-- ── 1. stop_loss — stale mirror of a broker-managed value ────────────────────
-- Written once at insert as fill_price * (1 - trail%) and NEVER updated again,
-- while the real stop ratchets upward with the high-water mark inside IBKR and
-- stop_loss_pct is kept current (execution_agent.py:2540). The column therefore
-- drifts further from reality every day a position rises.
--
-- It drove no decision: the only read was backend/database.py serialising it
-- into the API payload, and the dashboard never referenced it — it already
-- derives the true level as hwm_price * (1 - stop_loss_pct)
-- (DashboardView.jsx:231, 966), which matches IBKR's own calculation.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS stop_loss;

-- ── 2. Columns that belong to `watchlist`, not `portfolio_positions` ─────────
-- tv_api_screener.py writes these four, but exclusively into the watchlist
-- table (tv_api_screener.py:205-230). On portfolio_positions they have never
-- been written by any code path and are NULL on every live row.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS tv_exchange;
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS ib_exchange;
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS currency;
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS fmp_ticker;

-- ── 3. Write-only or entirely unreferenced columns ───────────────────────────
-- highest_rs_score: written only by force_buy.py:214; never read anywhere.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS highest_rs_score;

-- hwm_rs_score: zero references in the entire codebase outside its own
-- migration (add_hwm_rs_score.sql). NULL on every live row.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS hwm_rs_score;

-- analysis_date: zero references outside its migration. Its siblings
-- analysis_reason and analysis_ai_grade ARE read by the dashboard and by
-- execution_agent.py:3012, so those two are deliberately KEPT.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS analysis_date;

-- oca_group: force_buy.py writes the IBKR OCA group name, but the self-healing
-- path that was meant to consume it instead queries ib.openTrades() directly
-- (execution_agent.py:2608-2614). NULL on every live row.
ALTER TABLE portfolio_positions DROP COLUMN IF EXISTS oca_group;

COMMIT;

-- ── Explicitly NOT dropped ───────────────────────────────────────────────────
-- stop_loss_pct           live trailing %, ratcheted tighter by
--                         _compute_dynamic_trail_pct() and re-applied on
--                         self-heal; the UI derives the stop from it
-- hwm_price / hwm_date    running peak + plateau clock
-- highest_unrealized_pct  power-hold arming input
-- power_hold              suppresses EMA / stale / minimiser exits
-- intraday_high_today     Intraday Loss Minimiser state
-- exit_armed*             armed-exit state machine
-- param_drift,            read by DashboardView.jsx:422-438 and
-- analysis_reason,        execution_agent.py:3011-3012
-- analysis_ai_grade
-- entry_volume_surge,     write-only today, but retained deliberately as the
-- entry_pivot_distance_pct  entry-conviction audit trail used to tune the
--                         screener retrospectively
