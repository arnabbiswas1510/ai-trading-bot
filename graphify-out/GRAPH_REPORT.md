# Graph Report - ai-trading-bot  (2026-08-04)

## Corpus Check
- 108 files · ~155,864 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1315 nodes · 2169 edges · 85 communities (76 shown, 9 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 16 edges (avg confidence: 0.69)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0bcc46d0`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- compute_liquidity_score
- datetime
- _AV
- technical_screener.py
- Frontend Dashboard Components
- TelegramNotifier
- make_position
- _compute_dynamic_trail_pct
- test_plateau_rotation.py
- compute_final_score
- _run
- main.py
- fetch_ibkr_delayed_price
- Frontend Dependencies
- make_ib_mock
- Trading Methodology Documentation
- database.py
- Scoring System Enhancement Plan
- Decision: Repository-Wide Dead Code Cleanup
- FMPClient
- flex_query_sync.py
- execution_agent.py
- Portfolio Reconciliation Logic
- IBKR TOTP Setup Guide
- Technical Triggers Logic
- 🟠 High Severity — Structural Weaknesses
- compute_rs_score
- FakeQuery
- System Configuration Guide
- ai_evaluator.py
- Risk Management Plan
- Buy Logic
- Fundamental Filter Audit
- Backtest-corrected exit parameters; keep the entry tightening
- make_supabase_mock
- _place_sell
- Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser
- Sell Logic
- monitor_portfolio_intraday
- Fundamental Screener Overview
- TeeLogger
- Self-Healing Order Tests
- Cool-off Period Plan
- patch
- force_buy.py
- _triggers
- Decision: Breakout Quality Floor + Quota Waterfall
- Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals
- check_and_run_weekly_watchlist
- backtester.py
- docker-compose.yml
- _coil
- Build Verification Scripts
- 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers
- _pos
- date
- reconcile_with_ibkr
- _reload
- compute_pre_breakout_quality_score
- Timezone Compliance Tests
- Decision
- increment_retention
- Tune exits on the breakout population, not on the trades being eliminated
- _fetch_current_rs
- FMP API Integration
- IBKR API Integration
- OpenAI API Integration
- Supabase Integration
- TradingView API Integration
- Decision: Breakout Verdict + Intraday Loss Minimiser
- Decision: Separate Container Architecture (execution-agent vs trading-bot)
- Decision: Dynamic ATR Trailing Stop
- Decision: Plateau Rotation — Simplify from 3-Tier to 2-Rule
- Decision: Pre-Breakout (VCP/Handle) Detection as Second Pass
- Decision: Replace get_available_cash with margin-safe functions
- decisions/
- Local SQLite (trading_bot.db)
- Decision: Backtester Accuracy Rewrite
- restart_and_health_check.py
- restart_6am.sh

## God Nodes (most connected - your core abstractions)
1. `make_ib_mock()` - 63 edges
2. `make_supabase_mock()` - 56 edges
3. `make_position()` - 46 edges
4. `make_trigger()` - 27 edges
5. `FMPClient` - 26 edges
6. `monitor_portfolio_intraday()` - 26 edges
7. `TelegramNotifier` - 22 edges
8. `_compute_dynamic_trail_pct()` - 21 edges
9. `compute_liquidity_score()` - 20 edges
10. `_run()` - 20 edges

## Surprising Connections (you probably didn't know these)
- `TeeLogger` --uses--> `TelegramNotifier`  [INFERRED]
  execution_agent.py → telegram_notifier.py
- `main()` --calls--> `compute_final_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `compute_liquidity_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `TelegramNotifier`  [EXTRACTED]
  ai_evaluator.py → telegram_notifier.py
- `main()` --calls--> `get_own_cash()`  [EXTRACTED]
  force_buy.py → execution_agent.py

## Import Cycles
- None detected.

## Communities (85 total, 9 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.13
Nodes (10): compute_liquidity_score(), Penalises low-price, low-volume, and small-cap stocks (0-100).      Price tier, NVDA-like: $750, 42M avg vol, Large -> max score, Mid-tier stock: $35, 800K vol, Mid, SGHC-like: $8, 180K vol, Small -> very low, $15 exact -> price tier = 20, $14.99 -> price tier = 10, $50 exactly -> price tier = 40 (+2 more)

### Community 1 - "datetime"
Cohesion: 0.08
Nodes (28): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _make_supabase_mock(), _monday(), datetime, patch, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:   1 (+20 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (30): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer.  Covers two crit, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g (+22 more)

### Community 3 - "technical_screener.py"
Cohesion: 0.19
Nodes (13): scoring.py — Pure scoring functions for the 5-component final_score system.  No, check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync(), get_supabase_client(), get_watchlist_from_supabase() (+5 more)

### Community 4 - "Frontend Dashboard Components"
Cohesion: 0.08
Nodes (18): App(), BacktesterView(), BreakoutsView(), BreakoutTable(), sortByConviction(), daysHeld(), ExitConditionsPanel(), formatDate() (+10 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.10
Nodes (19): Fires from ai_evaluator.py after all 5-component scores are computed.         Sh, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Distin, Sent at EOD of Day 3 when a position fails the breakout verdict.         Activat, Fires after a successful IBKR market sell order is filled and logged., Fires when reconcile_with_ibkr() detects a position closed manually in TWS., Rate-limited exception alert. Suppresses duplicate (same context + error type) (+11 more)

### Community 6 - "make_position"
Cohesion: 0.08
Nodes (26): make_ibkr_fill(), make_ohlcv_data(), make_portfolio_item(), make_position(), mock_ib(), mock_supabase_empty(), fixture, Factory for an ibkr_fills Supabase row. (+18 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.08
Nodes (19): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     ot, parametrize, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the dynamic t, +20% (profit->6.5%) vs 30 days (time->3.5%) - time is tighter., +50% (profit->5%) vs 8 days (time->6%) - profit is tighter., Was at 6% trail, dipped to +22% (would suggest 6.5%). Must not loosen., Already at 6.5%, crosses +30% -> should tighten to 6%. (+11 more)

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.12
Nodes (21): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload., Within 3-6 days, even with param drift, no swap occurs if there are no fresh tri (+13 more)

### Community 9 - "compute_final_score"
Cohesion: 0.12
Nodes (11): compute_final_score(), Weighted blend of 5 components (all 0-100) -> 0-100 final score.        Technica, TestPreBreakoutScoreBoost, tests/test_score_components.py  Unit tests for the new 5-component scoring funct, NVDA-like scores -> should be around 80, SGHC-like scores -> should be around 40-50, Weighted formula: tech=100, rest=0 -> score = 30, liq=100, rest=0 -> score = 25 (+3 more)

### Community 10 - "_run"
Cohesion: 0.10
Nodes (31): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), fixture, tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD), Intr, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent. (+23 more)

### Community 11 - "main.py"
Cohesion: 0.11
Nodes (29): approve_rotation(), auto_generate_watchlist(), BacktestRequest, dismiss_rotation(), get_account_balances(), get_benchmark_returns(), get_breakouts(), get_cash_flows() (+21 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.13
Nodes (18): fetch_ibkr_delayed_price(), Fetch the current price for a contract using IBKR delayed market data (type 3)., _make_ib(), _make_ticker(), tests/test_ibkr_delayed_price.py  Unit tests for fetch_ibkr_delayed_price() -- t, reqMarketDataType(1) must be the last call even on success., reqMarketDataType(1) must be called even when reqTickers raises., reqMarketDataType(3) must be called BEFORE reqTickers. (+10 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.07
Nodes (27): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+19 more)

### Community 14 - "make_ib_mock"
Cohesion: 0.13
Nodes (10): make_ib_mock(), Creates a mock IB instance whose portfolio() always returns the given symbols., place_trailing_stop() places exactly ONE GTC TRAIL order.     No LimitOrder (pro, TestCancelTickerSellOrders, TestPlaceTrailingStop, Case 4: Cash balance sync from IBKR to Supabase account_balances., Large change in cash → upsert to account_balances called., New logic: write daily snapshots for cash, positions_value, total_value. (+2 more)

### Community 15 - "Trading Methodology Documentation"
Cohesion: 0.07
Nodes (26): 1.1 What Happens Every Evening, 1.2 TradingView Scanner API Call, 1.3 Fundamental Filter Thresholds, 1.4 What the Watchlist Stores, 2.1 What Happens After the Watchlist Is Built, 2.2 Breakout Detection � Three Hard Gates (all must pass), 2.3 Technical (Quality) Score � 0 to 100, 2.4 Relative Strength Score � 0 to 100 (+18 more)

### Community 16 - "database.py"
Cohesion: 0.19
Nodes (16): get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results(), get_setting(), get_supabase_client() (+8 more)

### Community 17 - "Scoring System Enhancement Plan"
Cohesion: 0.08
Nodes (24): `ai_evaluator.py`, Component 1 — Technical Score (30%) — `technical_screener.py`, Component 2 — Liquidity Score (25%) — `technical_screener.py`, Component 3 — AI Score (25%) — `ai_evaluator.py`, Component 4 — Sentiment Score (10%) — `ai_evaluator.py`, Component 5 — Relative Strength vs S&P 500 (10%) — `technical_screener.py`, Current prompt weaknesses, `daily_triggers` table — add columns (+16 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.20
Nodes (9): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Follow-up pass: one-off scripts not wired into the main programs, Problem (+1 more)

### Community 19 - "FMPClient"
Cohesion: 0.11
Nodes (17): _bg_update_fmp_cache(), FMPClient, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Gracefull, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint. (+9 more)

### Community 20 - "flex_query_sync.py"
Cohesion: 0.13
Nodes (21): check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), main(), _parse_cash_transactions(), _parse_trade_confirms(), Client, flex_query_sync.py — IBKR Flex Query Cash Flow Sync  Fetches cash deposits and w (+13 more)

### Community 21 - "execution_agent.py"
Cohesion: 0.11
Nodes (21): calculate_ema(), calculate_sma(), check_volume_distribution(), compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), fetch_held_position_sentiment(), fetch_historical_closes_with_dates() (+13 more)

### Community 22 - "Portfolio Reconciliation Logic"
Cohesion: 0.11
Nodes (15): Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).         The code, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: stop_loss = avg_cost * (1 - STOP_LOSS_PCT).         profit_target is no, Case 3: In both, share count differs → update Supabase., IBKR has 150 shares, Supabase says 100 → update Supabase to 150., Case 3: IBKR and Supabase both have 100 shares → no update., Critical: reconcile_with_ibkr() must use ib.portfolio() everywhere.     ib.posit, The reconcile function must ONLY call ib.portfolio(), never ib.positions(). (+7 more)

### Community 23 - "IBKR TOTP Setup Guide"
Cohesion: 0.11
Nodes (18): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 1 — Extract the TOTP Base32 Secret from IBKR, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Start execution agent, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings (+10 more)

### Community 24 - "Technical Triggers Logic"
Cohesion: 0.10
Nodes (20): 50-Day Average Volume, 50-Day Simple Moving Average (SMA-50), 52-Week Rolling High, Breakout Signal Summary, Condition 1 — Above 50-Day SMA, Condition 2 — Volume Surge >= 40% Above Average, Condition 3 — Within 2% of 52-Week Rolling High, Configuration Parameters (+12 more)

### Community 25 - "🟠 High Severity — Structural Weaknesses"
Cohesion: 0.10
Nodes (19): AI Trading Bot — Performance Analysis, Bug 1 — Market Direction Filter Is Dead Code, Bug 2 — Day 3 Breakout Verdict Volume Check Reads 97-Day-Old Data, Bug 3 — No Minimum `final_score` Gate in the Buy Loop, 🔴 Critical Bugs (Confirmed Code Defects), 🟠 High Severity — Structural Weaknesses, 📋 Priority-Ordered Action List, 🔍 Root Cause Map (+11 more)

### Community 26 - "compute_rs_score"
Cohesion: 0.16
Nodes (10): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, Stock +20%, SPY +5% -> excess +15% -> 100, Excess exactly 10% -> 100, Excess 5% -> 50 + 5*5 = 75, Same return as SPY -> 50, Excess -5% -> 50 + (-5)*5 = 25, Excess exactly -10% -> max(0, 50-50) = 0 (+2 more)

### Community 27 - "FakeQuery"
Cohesion: 0.15
Nodes (7): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, patch, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "System Configuration Guide"
Cohesion: 0.12
Nodes (16): Buy Trigger Gating, Configuration Reference, Credentials & APIs, `daily_triggers` table, Execution Agent (`execution_agent.py`), `force_buy.py` Properties, IBKR Connection, Market Direction Filter (CANSLIM "M") (+8 more)

### Community 29 - "ai_evaluator.py"
Cohesion: 0.16
Nodes (18): ai_grade_and_bonus(), build_prompt(), call_ai_batch(), evaluate_triggers(), fetch_daily_triggers(), fetch_news_headlines(), fetch_trade_history(), fetch_watchlist_data() (+10 more)

### Community 30 - "Risk Management Plan"
Cohesion: 0.12
Nodes (15): 1. Gap risk — the stop doesn't protect you, 2. Higher volatility eats the trailing stop budget, 3. Wider bid-ask spreads, 4. Liquidity and market impact, 5. O'Neil's own guidance, Changes to `execution_agent.py`, Clarifying the actual risk, My recommendation (+7 more)

### Community 31 - "Buy Logic"
Cohesion: 0.12
Nodes (15): Buy Decision Flowchart, Buy Gate 1 — Portfolio Cap Check, Buy Gate 2 — Trigger Availability, Buy Gate 3 — Duplicate Position Guard, Buy Gate 4 — Cooling-Off Period, Buy Gate 5 — Re-verify Portfolio Cap (within loop), Buy Gate 6 — Cash Sufficiency, Buy Gate 7 — Pivot Extension (O'Neil Buy Zone) (+7 more)

### Community 32 - "Fundamental Filter Audit"
Cohesion: 0.13
Nodes (14): Current Filter Audit, Expected Impact, Fundamental Filter Alignment Plan, Implementation Order, Issue 1 — Volume Dead Zone (Highest Priority), Issue 2 — Price Threshold Mismatch, Issue 3 — No Market Cap Floor, Issue 4 — Annual EPS Threshold May Miss Momentum Breakouts (+6 more)

### Community 33 - "Backtest-corrected exit parameters; keep the entry tightening"
Cohesion: 0.14
Nodes (13): Backtest-corrected exit parameters; keep the entry tightening, Consequences, Context, Decision, Fidelity limits (important), Finding 1 — there was no right tail to protect, Finding 2 — ablation: only one of the four exit changes helps, Finding 3 — the early-loss reasoning was simply wrong (+5 more)

### Community 34 - "make_supabase_mock"
Cohesion: 0.19
Nodes (13): make_supabase_mock(), make_trigger(), Factory for a daily_triggers Supabase row.      final_score defaults to 75 (a no, Returns a MagicMock Supabase client where each table's queries return     realis, adjusted_score (post-penalty) remains the primary gate input., Runs run_market_open_buys() with standard patches applied.     Returns the mock_, Gate 1: 4 stock positions → portfolio full → no order placed., Regression: ai_evaluator.py silently drops tickers from its batch         ("lost (+5 more)

### Community 35 - "_place_sell"
Cohesion: 0.16
Nodes (20): _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell(), IB, Display a numbered menu and return the chosen ticker. (+12 more)

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.22
Nodes (8): 1. Dynamic Trailing Stop Loss (IBKR-Managed), 2. Day 3 Breakout Verdict & Intraday Loss Minimiser, 3. Moving Average Support Breach (Day 7+ EOD), Active Exit Mechanisms, Key Parameters, Legacy / Manual Scripts, Overview, Sell Logic

### Community 38 - "monitor_portfolio_intraday"
Cohesion: 0.17
Nodes (20): arm_exit(), get_available_cash(), get_ibkr_account(), get_own_cash(), get_supabase_client(), main_loop(), monitor_portfolio_intraday(), place_trailing_stop() (+12 more)

### Community 39 - "Fundamental Screener Overview"
Cohesion: 0.20
Nodes (9): CANSLIM Scoring Engine (Dashboard Only), Data Flow, Filters Applied (All Must Be True), Fundamental Screener, How It Works, Retention Logic, Universe, What Gets Extracted (+1 more)

### Community 40 - "TeeLogger"
Cohesion: 0.33
Nodes (3): Mirrors stdout to a daily rotating log file without touching print() calls., Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.          Uses the da, TeeLogger

### Community 41 - "Self-Healing Order Tests"
Cohesion: 0.24
Nodes (7): Even when price is below stop level, Python does NOT call execute_sell., Runs monitor_portfolio_intraday() with standard patches.     live_prices: dict o, If no open SELL orders exist for a position, monitor must re-place the     trail, No open SELL orders -> place_trailing_stop called for self-healing.         Use, Trailing stop already in IBKR -> no self-healing.         Use price=buy_price (0, _run_monitor(), TestSelfHealingTrailingStop

### Community 42 - "Cool-off Period Plan"
Cohesion: 0.22
Nodes (8): Conditional Cooling-Off — Loss-Only Plan, Edge Cases, Implementation Size, Optional Enhancement: Configurable Threshold, Proposed Logic, The One Code Change Required, The Problem with the Current Rule, What This Enables Operationally

### Community 43 - "patch"
Cohesion: 0.09
Nodes (16): patch, hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., Price below threshold near market close -> execute_sell called., Price below MA but within buffer -> no exit., Outside 3:45-4:00 PM and EOD_ONLY enabled -> no exit., FMP historical fetch returns empty -> no exit and no crash. (+8 more)

### Community 44 - "force_buy.py"
Cohesion: 0.16
Nodes (15): cancel_ticker_sell_orders(), get_margin_loan(), Return the current margin loan amount in USD (0.0 if no loan).      A positive r, Factory for IBKR TRAIL order type.     `ib_insync` 0.9.x does not export a Trail, Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sel, TrailingStopOrder(), get_ibkr_price(), main() (+7 more)

### Community 45 - "_triggers"
Cohesion: 0.22
Nodes (5): Regression tests for the AI evaluator batching / completeness logic.  Background, Simulates the exact production failure: model drops middle entries., TestBatching, TestPromptCompleteness, _triggers()

### Community 46 - "Decision: Breakout Quality Floor + Quota Waterfall"
Cohesion: 0.33
Nodes (5): Consequences, Decision, Decision: Breakout Quality Floor + Quota Waterfall, Problem, Rationale

### Community 47 - "Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals"
Cohesion: 0.33
Nodes (5): Decision, Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals, Files changed, Problem, Why these specific numbers

### Community 48 - "check_and_run_weekly_watchlist"
Cohesion: 0.40
Nodes (5): check_and_run_weekly_watchlist(), periodic_watchlist_scheduler(), Checks if more than 7 days have passed since the last watchlist generation, and, startup_event(), on_event

### Community 49 - "backtester.py"
Cohesion: 0.33
Nodes (9): _cagr(), _ema(), _max_consecutive_losses(), _max_underwater_days(), backend/backtester.py  Runs a historical simulation of the CAN SLIM breakout tra, Exponential moving average (matches pandas ewm default, adjust=False)., Historical simulation of the CAN SLIM breakout strategy.      Position sizing ma, run_backtest() (+1 more)

### Community 51 - "_coil"
Cohesion: 0.14
Nodes (15): check_pre_breakout_coil(), Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, _coil(), _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None. (+7 more)

### Community 52 - "Build Verification Scripts"
Cohesion: 0.50
Nodes (3): DIST_DIR, failures, FEATURE_FINGERPRINTS

### Community 53 - "2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers"
Cohesion: 0.17
Nodes (11): 1. Fail closed on un-vetted triggers (`execution_agent.py`), 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers, 2. Batch the AI calls (`ai_evaluator.py`), 3. Demand completeness in the prompt, 4. Validate and retry, then alert, Consequences, Context, Decision (+3 more)

### Community 54 - "_pos"
Cohesion: 0.13
Nodes (16): Exception, is_power_hold_active(), maybe_arm_power_hold(), O'Neil 8-week hold rule.      True while a position is inside its protected wind, Persists the power-hold flag the first time a position qualifies.      Returns T, _client(), _pos(), test_power_hold.py - Tests for the O'Neil 8-week hold rule.  From "How to Make M (+8 more)

### Community 55 - "date"
Cohesion: 0.18
Nodes (13): date, execute_sell(), _fetch_ohlcv(), _get_market_regime(), is_market_bullish(), _nyse_holidays(), CANSLIM 'M' (Market Direction) filter.     Returns True  if MARKET_DIRECTION_TIC, Fetch OHLCV rows from FMP for the last `days` calendar days.      Returns a list (+5 more)

### Community 56 - "reconcile_with_ibkr"
Cohesion: 0.15
Nodes (12): fetch_trade_confirms_for_ticker(), get_live_price(), _matches_account(), Fetch current price of a ticker from FMP., Return True if obj belongs to target_account, or if obj has no account string se, Full bidirectional reconciliation between IBKR actual positions and Supabase led, reconcile_with_ibkr(), fetch_trade_confirms_for_ticker() (+4 more)

### Community 57 - "_reload"
Cohesion: 0.13
Nodes (10): fixture, test_screener_filters.py - Tests for the CAN SLIM fundamental gate in tv_api_scr, Re-import the screener module with the given env overrides applied., Regression: was 0, which admitted SWK on 0.6% revenue growth., Regression: was 15. O'Neil requires ~25%., Thresholds must be adjustable without a code change, for A/B and rollback., _reload(), screener() (+2 more)

### Community 58 - "compute_pre_breakout_quality_score"
Cohesion: 0.18
Nodes (9): compute_pre_breakout_quality_score(), Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65., Within 5%, 0.8x vol, 2 closes up -> 28+int(0.2*40)+10=28+8+10=46 (rounding gives, Within 8%, 0.9x vol, 2 closes up -> 20+4+10=34 (rounding may give 33)., 0 rising closes -> uptrend=0 -> 35+20+0=55. (+1 more)

### Community 60 - "Decision"
Cohesion: 0.17
Nodes (11): Cause 1 — the exits amputated winners, Cause 2 — the screener was not selecting CAN SLIM stocks, Consequences, Context, Decision, Entries — actually buy CAN SLIM stocks, Exits — stop selling on noise, Follow-up (+3 more)

### Community 61 - "increment_retention"
Cohesion: 0.60
Nodes (3): increment_retention(), get_rating_text(), run_screener()

### Community 62 - "Tune exits on the breakout population, not on the trades being eliminated"
Cohesion: 0.17
Nodes (11): 1. The wide profit ladder wins on the real population, 2. The Intraday Loss Minimiser is the most damaging exit in the system, 3. The 7% base trailing stop is too tight — not acted on yet, 4. The breakout timing signal has no measurable edge, Consequences, Context, Decision, Findings (+3 more)

### Community 63 - "_fetch_current_rs"
Cohesion: 0.50
Nodes (4): _fetch_current_rs(), _get_entry_rs(), Return entry_rs_score for a newly opened position.      Prefers the rs_score alr, Fetch the stock's current 12-week return vs SPY and return its live RS score.

### Community 87 - "Decision: Breakout Verdict + Intraday Loss Minimiser"
Cohesion: 0.29
Nodes (6): Decision, Decision: Breakout Verdict + Intraday Loss Minimiser, Files changed, Problem, What was removed, Why the specific thresholds?

### Community 88 - "Decision: Separate Container Architecture (execution-agent vs trading-bot)"
Cohesion: 0.33
Nodes (5): Constraints this imposes, Decision, Decision: Separate Container Architecture (execution-agent vs trading-bot), Network setup, Rationale

### Community 89 - "Decision: Dynamic ATR Trailing Stop"
Cohesion: 0.33
Nodes (5): Decision, Decision: Dynamic ATR Trailing Stop, Files changed, Problem, Why two levers?

### Community 90 - "Decision: Plateau Rotation — Simplify from 3-Tier to 2-Rule"
Cohesion: 0.33
Nodes (5): Decision, Decision: Plateau Rotation — Simplify from 3-Tier to 2-Rule, Files changed, Problem, Why simpler is better here

### Community 91 - "Decision: Pre-Breakout (VCP/Handle) Detection as Second Pass"
Cohesion: 0.33
Nodes (5): Decision, Decision: Pre-Breakout (VCP/Handle) Detection as Second Pass, Files changed, Problem, Why these specific gates?

### Community 92 - "Decision: Replace get_available_cash with margin-safe functions"
Cohesion: 0.33
Nodes (5): Decision, Decision: Replace get_available_cash with margin-safe functions, Files changed, Problem, Why not just fix AvailableFunds?

### Community 93 - "decisions/"
Cohesion: 0.40
Nodes (4): decisions/, Naming convention, Template, When to add a file

### Community 95 - "Decision: Backtester Accuracy Rewrite"
Cohesion: 0.25
Nodes (7): API Compatibility, Correction Note (2026-07-24), Decision, Decision: Backtester Accuracy Rewrite, Files Changed, New Metrics Added (13), Problem

### Community 96 - "restart_and_health_check.py"
Cohesion: 0.36
Nodes (7): main(), now_et_str(), restart_and_health_check.py — 6:00 AM IB Gateway Health Check & Telegram Notifie, Send HTML Telegram message to all configured chat IDs., Connects to IB Gateway, verifies account U12941651, and checks own cash.     Ret, send_telegram(), verify_health()

## Knowledge Gaps
- **274 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+269 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TelegramNotifier` connect `TelegramNotifier` to `technical_screener.py`, `TeeLogger`, `force_buy.py`, `execution_agent.py`, `ai_evaluator.py`?**
  _High betweenness centrality (0.100) - this node is a cross-community bridge._
- **Why does `_compute_dynamic_trail_pct()` connect `_compute_dynamic_trail_pct` to `execution_agent.py`, `monitor_portfolio_intraday`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `make_ib_mock()` connect `make_ib_mock` to `make_supabase_mock`, `_AV`, `make_position`, `test_plateau_rotation.py`, `Self-Healing Order Tests`, `patch`, `Portfolio Reconciliation Logic`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `FMPClient` (e.g. with `BacktestRequest` and `SettingsUpdate`) actually correct?**
  _`FMPClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `name`, `private`, `version` to the rest of the system?**
  _274 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.12666666666666668 - nodes in this community are weakly interconnected._
- **Should `datetime` be split into smaller, more focused modules?**
  _Cohesion score 0.08115942028985507 - nodes in this community are weakly interconnected._