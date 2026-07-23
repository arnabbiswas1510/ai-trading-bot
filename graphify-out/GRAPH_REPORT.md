# Graph Report - ai-trading-bot  (2026-07-23)

## Corpus Check
- 99 files · ~151,726 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1210 nodes · 1920 edges · 97 communities (66 shown, 31 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 14 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `0ee7692a`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- AI Scoring Pipeline
- Watchlist Retention Logic
- Margin Safety Tests
- Trading Agent Utilities
- Frontend Dashboard Components
- Telegram Notification System
- Mock Data Factories
- Dynamic Trailing Stop Logic
- Portfolio Rotation Tests
- Technical Analysis Indicators
- Breakout Verdict Validation
- API Backend Routes
- IBKR Price Fetching
- Frontend Dependencies
- IBKR Order Mocks
- Trading Methodology Documentation
- Database Access Layer
- Scoring System Enhancement Plan
- Pre-Breakout Coiling Detection
- FMP Client & Backtester
- IBKR Flex Query Sync
- Manual Force Sell Utility
- Portfolio Reconciliation Logic
- IBKR TOTP Setup Guide
- Technical Trigger Documentation
- Market Direction Filters
- Relative Strength Calculation
- Supabase Client Mocks
- System Configuration Guide
- Intraday Monitoring Daemon
- Risk Management Plan
- Buy Execution Logic
- Fundamental Filter Audit
- Technical Quality Scoring
- Coil Quality Scoring
- Manual Force Buy Utility
- Entry Score Reconciliation
- Sell Execution Logic
- CANSLIM Screener Engine
- Fundamental Screener Overview
- Log Rotation Utility
- Self-Healing Order Tests
- Cool-off Period Plan
- Moving Average Exit Tests
- Plateau Rotation Tests
- Partial Position Liquidation
- Database Security Policies
- Position Archiving Logic
- High Water Mark Tracking
- Screener Retention Helpers
- Docker Infrastructure
- Market Regime Analysis
- Build Verification Scripts
- Moving Average Tests
- TWR Schema Migration
- Scoring Column Migration
- Analysis Column Migration
- Quality Scoring Migration
- Retention Period Migration
- Timezone Compliance Tests
- Breakout Learnings Schema
- Breakout Verdict Schema
- RS Score Schema
- HWM Date Schema
- HWM Price Schema
- HWM RS Schema
- IBKR Fills Schema
- Margin Tracking Schema
- Momentum Health Schema
- Plateau Rotation Schema
- Risk Optimization Schema
- Trigger Type Schema
- Cash Flow Schema
- Cash Flow Patch
- Manual Position Top-up
- Test Generation Script
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
- increment_retention

## God Nodes (most connected - your core abstractions)
1. `make_ib_mock()` - 57 edges
2. `make_supabase_mock()` - 49 edges
3. `make_position()` - 39 edges
4. `FMPClient` - 26 edges
5. `TelegramNotifier` - 26 edges
6. `monitor_portfolio_intraday()` - 23 edges
7. `_compute_dynamic_trail_pct()` - 22 edges
8. `compute_liquidity_score()` - 20 edges
9. `make_trigger()` - 20 edges
10. `fetch_ibkr_delayed_price()` - 19 edges

## Surprising Connections (you probably didn't know these)
- `TeeLogger` --uses--> `TelegramNotifier`  [INFERRED]
  execution_agent.py → telegram_notifier.py
- `main()` --calls--> `TelegramNotifier`  [EXTRACTED]
  ai_evaluator.py → telegram_notifier.py
- `DebugNotifier` --uses--> `TelegramNotifier`  [INFERRED]
  debug_telegram_msg.py → telegram_notifier.py
- `main()` --calls--> `get_own_cash()`  [EXTRACTED]
  force_buy.py → execution_agent.py
- `_place_buy()` --calls--> `place_trailing_stop()`  [EXTRACTED]
  force_buy.py → execution_agent.py

## Import Cycles
- None detected.

## Communities (97 total, 31 thin omitted)

### Community 0 - "AI Scoring Pipeline"
Cohesion: 0.05
Nodes (33): ai_grade_and_bonus(), evaluate_held_position(), fetch_daily_triggers(), fetch_news_headlines(), fetch_trade_history(), fetch_watchlist_data(), main(), Write updated score fields back to daily_triggers for a ticker. (+25 more)

### Community 1 - "Watchlist Retention Logic"
Cohesion: 0.07
Nodes (27): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _make_supabase_mock(), _monday(), datetime, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:, save_screener_results must replace only the current week's rows. (+19 more)

### Community 2 - "Margin Safety Tests"
Cohesion: 0.08
Nodes (27): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer.  Covers two crit, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g (+19 more)

### Community 3 - "Trading Agent Utilities"
Cohesion: 0.09
Nodes (42): calculate_ema(), calculate_sma(), cancel_ticker_sell_orders(), execute_sell(), fetch_historical_closes_with_dates(), get_available_cash(), get_fresh_triggers_today(), get_ibkr_account() (+34 more)

### Community 4 - "Frontend Dashboard Components"
Cohesion: 0.08
Nodes (18): App(), BacktesterView(), BreakoutsView(), BreakoutTable(), sortByConviction(), daysHeld(), ExitConditionsPanel(), formatDate() (+10 more)

### Community 5 - "Telegram Notification System"
Cohesion: 0.09
Nodes (20): DebugNotifier, Exception, Fires from ai_evaluator.py after all 5-component scores are computed.         S, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Dist, Sent at EOD of Day 3 when a position fails the breakout verdict.         Activa, Fires after a successful IBKR market sell order is filled and logged. (+12 more)

### Community 6 - "Mock Data Factories"
Cohesion: 0.09
Nodes (26): make_ibkr_fill(), make_ohlcv_data(), make_portfolio_item(), make_supabase_mock(), make_trigger(), mock_supabase_empty(), Factory for a daily_triggers Supabase row., Factory for an ibkr_fills Supabase row. (+18 more)

### Community 7 - "Dynamic Trailing Stop Logic"
Cohesion: 0.09
Nodes (14): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     o, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the two-lever, Already at 4%, crosses +14% -> should tighten to 3%., Both levers agree on 5%, current already 5% -> None., FR: +5.2% gain, 12 days. Profit lever (5%) beats time lever (6%)., +8% gain (profit->4%) vs 12 days (time->6%). Profit wins., +1% gain (profit->None) vs 15 days (time->5%). Time wins. (+6 more)

### Community 8 - "Portfolio Rotation Tests"
Cohesion: 0.13
Nodes (23): make_position(), Factory for a portfolio_positions Supabase row.      hwm_rs_score: RS score on t, _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm (+15 more)

### Community 9 - "Technical Analysis Indicators"
Cohesion: 0.07
Nodes (28): check_consolidation_floor_break(), check_volume_distribution(), _compute_3day_avg_close(), compute_momentum_health_score(), _compute_param_drift(), compute_rsi(), detect_candlestick_reversals(), fetch_held_position_sentiment() (+20 more)

### Community 10 - "Breakout Verdict Validation"
Cohesion: 0.18
Nodes (19): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD) and I, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent., Day 3 EOD: price +2% but volume 0.5x avg -> FAIL. (+11 more)

### Community 11 - "API Backend Routes"
Cohesion: 0.09
Nodes (17): approve_rotation(), BacktestRequest, check_and_run_weekly_watchlist(), dismiss_rotation(), get_version(), periodic_watchlist_scheduler(), Returns build metadata for the currently deployed image.     GIT_COMMIT and BUI, User approved a Tier 1 or Tier 2 rotation recommendation.      Flow (immediate (+9 more)

### Community 12 - "IBKR Price Fetching"
Cohesion: 0.13
Nodes (18): fetch_ibkr_delayed_price(), Fetch the current price for a contract using IBKR delayed market data (type 3)., _make_ib(), _make_ticker(), tests/test_ibkr_delayed_price.py  Unit tests for fetch_ibkr_delayed_price() -- t, reqMarketDataType(1) must be the last call even on success., reqMarketDataType(1) must be called even when reqTickers raises., reqMarketDataType(3) must be called BEFORE reqTickers. (+10 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.07
Nodes (27): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+19 more)

### Community 14 - "IBKR Order Mocks"
Cohesion: 0.11
Nodes (12): make_ib_mock(), mock_ib(), Default IB mock with no open positions., Creates a mock IB instance whose portfolio() always returns the given symbols., place_trailing_stop() places exactly ONE GTC TRAIL order.     No LimitOrder (pro, TestCancelTickerSellOrders, TestPlaceTrailingStop, Case 4: Cash balance sync from IBKR to Supabase account_balances. (+4 more)

### Community 15 - "Trading Methodology Documentation"
Cohesion: 0.07
Nodes (26): 1.1 What Happens Every Evening, 1.2 TradingView Scanner API Call, 1.3 Fundamental Filter Thresholds, 1.4 What the Watchlist Stores, 2.1 What Happens After the Watchlist Is Built, 2.2 Breakout Detection � Three Hard Gates (all must pass), 2.3 Technical (Quality) Score � 0 to 100, 2.4 Relative Strength Score � 0 to 100 (+18 more)

### Community 16 - "Database Access Layer"
Cohesion: 0.12
Nodes (26): _bg_update_fmp_cache(), buy_position(), get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_historical_triggers(), get_position() (+18 more)

### Community 17 - "Scoring System Enhancement Plan"
Cohesion: 0.08
Nodes (24): `ai_evaluator.py`, Component 1 — Technical Score (30%) — `technical_screener.py`, Component 2 — Liquidity Score (25%) — `technical_screener.py`, Component 3 — AI Score (25%) — `ai_evaluator.py`, Component 4 — Sentiment Score (10%) — `ai_evaluator.py`, Component 5 — Relative Strength vs S&P 500 (10%) — `technical_screener.py`, Current prompt weaknesses, `daily_triggers` table — add columns (+16 more)

### Community 18 - "Pre-Breakout Coiling Detection"
Cohesion: 0.14
Nodes (15): check_pre_breakout_coil(), Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, _coil(), _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None. (+7 more)

### Community 19 - "FMP Client & Backtester"
Cohesion: 0.11
Nodes (15): FMPClient, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Graceful, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint., auto_generate_watchlist() (+7 more)

### Community 20 - "IBKR Flex Query Sync"
Cohesion: 0.13
Nodes (21): check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), main(), _parse_cash_transactions(), _parse_trade_confirms(), Client, flex_query_sync.py — IBKR Flex Query Cash Flow Sync  Fetches cash deposits and w (+13 more)

### Community 21 - "Manual Force Sell Utility"
Cohesion: 0.16
Nodes (20): _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell(), IB, Display a numbered menu and return the chosen ticker. (+12 more)

### Community 22 - "Portfolio Reconciliation Logic"
Cohesion: 0.11
Nodes (15): Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).         The cod, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: stop_loss = avg_cost * (1 - STOP_LOSS_PCT).         profit_target is no, Case 3: In both, share count differs → update Supabase., IBKR has 150 shares, Supabase says 100 → update Supabase to 150., Case 3: IBKR and Supabase both have 100 shares → no update., Critical: reconcile_with_ibkr() must use ib.portfolio() everywhere.     ib.posi, The reconcile function must ONLY call ib.portfolio(), never ib.positions(). (+7 more)

### Community 23 - "IBKR TOTP Setup Guide"
Cohesion: 0.11
Nodes (18): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 1 — Extract the TOTP Base32 Secret from IBKR, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Start execution agent, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings (+10 more)

### Community 24 - "Technical Trigger Documentation"
Cohesion: 0.11
Nodes (18): 50-Day Average Volume, 50-Day Simple Moving Average (SMA-50), 52-Week Rolling High, Breakout Signal Summary, Condition 1 — Above 50-Day SMA, Condition 2 — Volume Surge >= 40% Above Average, Condition 3 — Within 2% of 52-Week Rolling High, Edge Cases Handled (+10 more)

### Community 25 - "Market Direction Filters"
Cohesion: 0.12
Nodes (18): date, calculate_ema(), calculate_sma(), fetch_historical_closes_with_dates(), get_fresh_triggers_today(), get_ma_value(), is_market_bullish(), _nyse_holidays() (+10 more)

### Community 26 - "Relative Strength Calculation"
Cohesion: 0.16
Nodes (10): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, Stock +20%, SPY +5% -> excess +15% -> 100, Excess exactly 10% -> 100, Excess 5% -> 50 + 5*5 = 75, Same return as SPY -> 50, Excess -5% -> 50 + (-5)*5 = 25, Excess exactly -10% -> max(0, 50-50) = 0 (+2 more)

### Community 27 - "Supabase Client Mocks"
Cohesion: 0.16
Nodes (6): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "System Configuration Guide"
Cohesion: 0.12
Nodes (16): Buy Trigger Gating, Configuration Reference, Credentials & APIs, `daily_triggers` table, Execution Agent (`execution_agent.py`), `force_buy.py` Properties, IBKR Connection, Market Direction Filter (CANSLIM "M") (+8 more)

### Community 29 - "Intraday Monitoring Daemon"
Cohesion: 0.20
Nodes (17): execute_sell(), get_available_cash(), get_ibkr_account(), get_own_cash(), main_loop(), monitor_portfolio_intraday(), place_trailing_stop(), IB (+9 more)

### Community 30 - "Risk Management Plan"
Cohesion: 0.12
Nodes (15): 1. Gap risk — the stop doesn't protect you, 2. Higher volatility eats the trailing stop budget, 3. Wider bid-ask spreads, 4. Liquidity and market impact, 5. O'Neil's own guidance, Changes to `execution_agent.py`, Clarifying the actual risk, My recommendation (+7 more)

### Community 31 - "Buy Execution Logic"
Cohesion: 0.13
Nodes (14): Buy Decision Flowchart, Buy Gate 1 — Portfolio Cap Check, Buy Gate 2 — Trigger Availability, Buy Gate 3 — Duplicate Position Guard, Buy Gate 4 — Cooling-Off Period, Buy Gate 5 — Re-verify Portfolio Cap (within loop), Buy Gate 6 — Cash Sufficiency, Buy Gate 7 — Pivot Extension (O'Neil Buy Zone) (+6 more)

### Community 32 - "Fundamental Filter Audit"
Cohesion: 0.13
Nodes (14): Current Filter Audit, Expected Impact, Fundamental Filter Alignment Plan, Implementation Order, Issue 1 — Volume Dead Zone (Highest Priority), Issue 2 — Price Threshold Mismatch, Issue 3 — No Market Cap Floor, Issue 4 — Annual EPS Threshold May Miss Momentum Breakouts (+6 more)

### Community 33 - "Technical Quality Scoring"
Cohesion: 0.19
Nodes (13): scoring.py — Pure scoring functions for the 5-component final_score system.  No, check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync(), get_supabase_client(), get_watchlist_from_supabase() (+5 more)

### Community 34 - "Coil Quality Scoring"
Cohesion: 0.18
Nodes (9): compute_pre_breakout_quality_score(), Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65., Within 5%, 0.8x vol, 2 closes up -> 28+int(0.2*40)+10=28+8+10=46 (rounding gives, Within 8%, 0.9x vol, 2 closes up -> 20+4+10=34 (rounding may give 33)., 0 rising closes -> uptrend=0 -> 35+20+0=55. (+1 more)

### Community 35 - "Manual Force Buy Utility"
Cohesion: 0.21
Nodes (12): cancel_ticker_sell_orders(), get_margin_loan(), Return the current margin loan amount in USD (0.0 if no loan).      A positive, Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sel, get_ibkr_price(), main(), _place_buy(), IB (+4 more)

### Community 36 - "Entry Score Reconciliation"
Cohesion: 0.14
Nodes (13): _fetch_current_rs(), fetch_trade_confirms_for_ticker(), _get_entry_rs(), get_live_price(), Return entry_rs_score for a newly opened position.      Prefers the rs_score a, Fetch the stock's current 12-week return vs SPY and return its live RS score., Fetch current price of a ticker from FMP., Full bidirectional reconciliation between IBKR actual positions and Supabase led (+5 more)

### Community 37 - "Sell Execution Logic"
Cohesion: 0.15
Nodes (12): execute_sell — Order Execution, Exit Mechanism 1 — Trailing Stop Loss (7% below peak, IBKR-managed), Exit Mechanism 2 — Moving Average Support Breach (EOD only), Exit Mechanism 3 — EOD Plateau Rotation (3:45–4:00 PM ET), Exit Priority Summary, Manual Close Reconciliation (IBKR TWS), Monitoring Schedule, Overview (+4 more)

### Community 39 - "Fundamental Screener Overview"
Cohesion: 0.20
Nodes (9): CANSLIM Scoring Engine (Dashboard Only), Data Flow, Filters Applied (All Must Be True), Fundamental Screener, How It Works, Retention Logic, Universe, What Gets Extracted (+1 more)

### Community 40 - "Log Rotation Utility"
Cohesion: 0.33
Nodes (3): Mirrors stdout to a daily rotating log file without touching print() calls., Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.          Uses the, TeeLogger

### Community 41 - "Self-Healing Order Tests"
Cohesion: 0.24
Nodes (7): Even when price is below stop level, Python does NOT call execute_sell., Runs monitor_portfolio_intraday() with standard patches.     live_prices: dict o, If no open SELL orders exist for a position, monitor must re-place the     trail, No open SELL orders -> place_trailing_stop called for self-healing.         Use, Trailing stop already in IBKR -> no self-healing.         Use price=buy_price (0, _run_monitor(), TestSelfHealingTrailingStop

### Community 42 - "Cool-off Period Plan"
Cohesion: 0.22
Nodes (8): Conditional Cooling-Off — Loss-Only Plan, Edge Cases, Implementation Size, Optional Enhancement: Configurable Threshold, Proposed Logic, The One Code Change Required, The Problem with the Current Rule, What This Enables Operationally

### Community 43 - "Moving Average Exit Tests"
Cohesion: 0.22
Nodes (5): Price below threshold near market close -> execute_sell called., Price below MA but within buffer -> no exit., Outside 3:45-4:00 PM and EOD_ONLY enabled -> no exit., FMP historical fetch returns empty -> no exit and no crash., TestMovingAverageExits

### Community 44 - "Plateau Rotation Tests"
Cohesion: 0.32
Nodes (5): EOD plateau rotation: at 3:45-4pm, if portfolio is full AND fresh breakout     t, Helper: run monitor in EOD window., Days 3-6 position with decay is NOT swapped if no triggers exist., Days 3-6 position with decay is NOT swapped if portfolio has open slots (not ful, TestPlateauRotation

### Community 45 - "Partial Position Liquidation"
Cohesion: 0.43
Nodes (6): _get_ibkr_price(), main(), _notify(), IB, Fire-and-forget Telegram notification., Fetch IBKR delayed market data: tries ask first, then last, then close.     Retu

### Community 46 - "Database Security Policies"
Cohesion: 0.33
Nodes (5): account_balances, daily_triggers, portfolio_positions, trade_history, watchlist

### Community 47 - "Position Archiving Logic"
Cohesion: 0.33
Nodes (4): Case 1: In Supabase, NOT in IBKR → closed by IBKR (trailing stop / limit / TWS)., Position in Supabase but not IBKR → archived to trade_history.         IBKR por, Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill., TestReconcileCase1

### Community 48 - "High Water Mark Tracking"
Cohesion: 0.33
Nodes (4): hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., TestHwmDateTracking

### Community 49 - "Screener Retention Helpers"
Cohesion: 0.33
Nodes (9): _cagr(), _ema(), _max_consecutive_losses(), _max_underwater_days(), backend/backtester.py  Runs a historical simulation of the CAN SLIM breakout tra, Exponential moving average (matches pandas ewm default, adjust=False)., Historical simulation of the CAN SLIM breakout strategy.      Parameters     ---, run_backtest() (+1 more)

### Community 51 - "Market Regime Analysis"
Cohesion: 0.50
Nodes (4): _fetch_ohlcv(), _get_market_regime(), Fetch OHLCV rows from FMP for the last `days` calendar days.      Returns a li, Return current market regime based on SPY vs its 21-day EMA.      'uptrend'

### Community 52 - "Build Verification Scripts"
Cohesion: 0.50
Nodes (3): DIST_DIR, failures, FEATURE_FINGERPRINTS

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
Cohesion: 0.29
Nodes (6): API Compatibility, Decision, Decision: Backtester Accuracy Rewrite, Files Changed, New Metrics Added (13), Problem

### Community 96 - "increment_retention"
Cohesion: 0.60
Nodes (3): increment_retention(), get_rating_text(), run_screener()

## Knowledge Gaps
- **232 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+227 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **31 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TelegramNotifier` connect `Telegram Notification System` to `AI Scoring Pipeline`, `Technical Quality Scoring`, `Trading Agent Utilities`, `Manual Force Buy Utility`, `Log Rotation Utility`, `Technical Analysis Indicators`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Why does `make_ib_mock()` connect `IBKR Order Mocks` to `Margin Safety Tests`, `Mock Data Factories`, `Portfolio Rotation Tests`, `Self-Healing Order Tests`, `Moving Average Exit Tests`, `Plateau Rotation Tests`, `Position Archiving Logic`, `High Water Mark Tracking`, `Portfolio Reconciliation Logic`?**
  _High betweenness centrality (0.038) - this node is a cross-community bridge._
- **Why does `compute_liquidity_score()` connect `AI Scoring Pipeline` to `Technical Quality Scoring`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `FMPClient` (e.g. with `BacktestRequest` and `SettingsUpdate`) actually correct?**
  _`FMPClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `TelegramNotifier` (e.g. with `DebugNotifier` and `TeeLogger`) actually correct?**
  _`TelegramNotifier` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `name`, `private`, `version` to the rest of the system?**
  _232 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `AI Scoring Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.05081967213114754 - nodes in this community are weakly interconnected._