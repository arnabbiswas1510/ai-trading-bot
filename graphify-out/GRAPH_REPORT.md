# Graph Report - ai-trading-bot  (2026-08-03)

## Corpus Check
- 101 files · ~146,746 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1170 nodes · 1936 edges · 76 communities (65 shown, 11 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 14 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `db5a559f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- compute_liquidity_score
- datetime
- _AV
- monitor_portfolio_intraday
- Frontend Dashboard Components
- TelegramNotifier
- make_supabase_mock
- _compute_dynamic_trail_pct
- test_plateau_rotation.py
- execution_agent.py
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
- date
- Portfolio Reconciliation Logic
- IBKR TOTP Setup Guide
- Technical Triggers Logic
- 🟠 High Severity — Structural Weaknesses
- technical_screener.py
- FakeQuery
- System Configuration Guide
- run_market_open_buys
- Risk Management Plan
- Buy Logic
- Fundamental Filter Audit
- _fetch_current_rs
- force_buy.py
- Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser
- Sell Logic
- run_canslim_screener
- Fundamental Screener Overview
- TeeLogger
- Self-Healing Order Tests
- Cool-off Period Plan
- make_position
- TestCancelTickerSellOrders
- Decision: Breakout Quality Floor + Quota Waterfall
- Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals
- check_and_run_weekly_watchlist
- backtester.py
- docker-compose.yml
- _coil
- Build Verification Scripts
- Moving Average Tests
- mock_ib
- Timezone Compliance Tests
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
- test_reconcile_detects_short_positions

## God Nodes (most connected - your core abstractions)
1. `make_ib_mock()` - 60 edges
2. `make_supabase_mock()` - 53 edges
3. `make_position()` - 43 edges
4. `FMPClient` - 26 edges
5. `monitor_portfolio_intraday()` - 24 edges
6. `make_trigger()` - 24 edges
7. `_compute_dynamic_trail_pct()` - 22 edges
8. `TelegramNotifier` - 22 edges
9. `compute_liquidity_score()` - 20 edges
10. `_AV` - 20 edges

## Surprising Connections (you probably didn't know these)
- `TeeLogger` --uses--> `TelegramNotifier`  [INFERRED]
  execution_agent.py → telegram_notifier.py
- `main()` --calls--> `TelegramNotifier`  [EXTRACTED]
  ai_evaluator.py → telegram_notifier.py
- `main()` --calls--> `get_own_cash()`  [EXTRACTED]
  force_buy.py → execution_agent.py
- `main()` --calls--> `get_margin_loan()`  [EXTRACTED]
  force_buy.py → execution_agent.py
- `reconcile_with_ibkr()` --calls--> `fetch_trade_confirms_for_ticker()`  [EXTRACTED]
  execution_agent.py → flex_query_sync.py

## Import Cycles
- None detected.

## Communities (76 total, 11 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.05
Nodes (32): ai_grade_and_bonus(), fetch_daily_triggers(), fetch_news_headlines(), fetch_trade_history(), fetch_watchlist_data(), main(), Write updated score fields back to daily_triggers for a ticker., Return (letter_grade, score_bonus) for an AI rating 1-100. (+24 more)

### Community 1 - "datetime"
Cohesion: 0.08
Nodes (28): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _make_supabase_mock(), _monday(), datetime, patch, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:   1 (+20 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (30): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer.  Covers two crit, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g (+22 more)

### Community 3 - "monitor_portfolio_intraday"
Cohesion: 0.18
Nodes (12): check_volume_distribution(), fetch_held_position_sentiment(), _fetch_ohlcv(), get_live_price(), _get_market_regime(), monitor_portfolio_intraday(), Fetch OHLCV rows from FMP for the last `days` calendar days.      Returns a list, Fetch live sentiment score (1-100) for a held position using FMP news + GPT-4o-m (+4 more)

### Community 4 - "Frontend Dashboard Components"
Cohesion: 0.08
Nodes (18): App(), BacktesterView(), BreakoutsView(), BreakoutTable(), sortByConviction(), daysHeld(), ExitConditionsPanel(), formatDate() (+10 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.09
Nodes (21): Exception, telegram_notifier.py — CANSLIM Trading Bot Telegram Notification Module  Fires r, Fires from ai_evaluator.py after all 5-component scores are computed.         Sh, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Distin, Sent at EOD of Day 3 when a position fails the breakout verdict.         Activat, Fires after a successful IBKR market sell order is filled and logged. (+13 more)

### Community 6 - "make_supabase_mock"
Cohesion: 0.10
Nodes (26): make_ibkr_fill(), make_ohlcv_data(), make_portfolio_item(), make_supabase_mock(), make_trigger(), mock_supabase_empty(), Factory for a daily_triggers Supabase row., Factory for an ibkr_fills Supabase row. (+18 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.09
Nodes (14): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     ot, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the two-lever, Already at 4%, crosses +14% -> should tighten to 3%., Both levers agree on 5%, current already 5% -> None., FR: +5.2% gain, 12 days. Profit lever (5%) beats time lever (6%)., +8% gain (profit->4%) vs 12 days (time->6%). Profit wins., +1% gain (profit->None) vs 15 days (time->5%). Time wins. (+6 more)

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.12
Nodes (21): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload., Within 3-6 days, even with param drift, no swap occurs if there are no fresh tri (+13 more)

### Community 9 - "execution_agent.py"
Cohesion: 0.17
Nodes (14): arm_exit(), compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), fetch_trade_confirms_for_ticker(), get_supabase_client(), handle_mock_sell(), Client (+6 more)

### Community 10 - "_run"
Cohesion: 0.13
Nodes (25): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD), Intr, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent., Day 3 EOD: price +2% but volume 0.5x avg -> FAIL. (+17 more)

### Community 11 - "main.py"
Cohesion: 0.11
Nodes (29): approve_rotation(), auto_generate_watchlist(), BacktestRequest, dismiss_rotation(), get_account_balances(), get_benchmark_returns(), get_breakouts(), get_cash_flows() (+21 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.07
Nodes (38): fetch_ibkr_delayed_price(), Fetch the current price for a contract using IBKR delayed market data (type 3)., _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell() (+30 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.07
Nodes (27): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+19 more)

### Community 14 - "make_ib_mock"
Cohesion: 0.11
Nodes (13): make_ib_mock(), Creates a mock IB instance whose portfolio() always returns the given symbols., place_trailing_stop() places exactly ONE GTC TRAIL order.     No LimitOrder (pro, TestPlaceTrailingStop, Case 4: Cash balance sync from IBKR to Supabase account_balances., Large change in cash → upsert to account_balances called., New logic: write daily snapshots for cash, positions_value, total_value., A cash jump > $500 inserts into cash_flows. (+5 more)

### Community 15 - "Trading Methodology Documentation"
Cohesion: 0.07
Nodes (26): 1.1 What Happens Every Evening, 1.2 TradingView Scanner API Call, 1.3 Fundamental Filter Thresholds, 1.4 What the Watchlist Stores, 2.1 What Happens After the Watchlist Is Built, 2.2 Breakout Detection � Three Hard Gates (all must pass), 2.3 Technical (Quality) Score � 0 to 100, 2.4 Relative Strength Score � 0 to 100 (+18 more)

### Community 16 - "database.py"
Cohesion: 0.14
Nodes (19): _bg_update_fmp_cache(), get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results(), get_setting() (+11 more)

### Community 17 - "Scoring System Enhancement Plan"
Cohesion: 0.08
Nodes (24): `ai_evaluator.py`, Component 1 — Technical Score (30%) — `technical_screener.py`, Component 2 — Liquidity Score (25%) — `technical_screener.py`, Component 3 — AI Score (25%) — `ai_evaluator.py`, Component 4 — Sentiment Score (10%) — `ai_evaluator.py`, Component 5 — Relative Strength vs S&P 500 (10%) — `technical_screener.py`, Current prompt weaknesses, `daily_triggers` table — add columns (+16 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.22
Nodes (8): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Problem, What was verified NOT dead (confirmed via manual call-site tracing, left

### Community 19 - "FMPClient"
Cohesion: 0.16
Nodes (8): FMPClient, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Gracefull, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint., DataFrame

### Community 20 - "flex_query_sync.py"
Cohesion: 0.12
Nodes (23): check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), fetch_trade_confirms_for_ticker(), main(), _parse_cash_transactions(), _parse_trade_confirms(), Client (+15 more)

### Community 21 - "date"
Cohesion: 0.14
Nodes (17): date, calculate_ema(), calculate_sma(), execute_sell(), fetch_historical_closes_with_dates(), get_ma_value(), is_market_bullish(), _nyse_holidays() (+9 more)

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

### Community 26 - "technical_screener.py"
Cohesion: 0.08
Nodes (25): increment_retention(), compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync() (+17 more)

### Community 27 - "FakeQuery"
Cohesion: 0.15
Nodes (7): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, patch, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "System Configuration Guide"
Cohesion: 0.12
Nodes (16): Buy Trigger Gating, Configuration Reference, Credentials & APIs, `daily_triggers` table, Execution Agent (`execution_agent.py`), `force_buy.py` Properties, IBKR Connection, Market Direction Filter (CANSLIM "M") (+8 more)

### Community 29 - "run_market_open_buys"
Cohesion: 0.21
Nodes (17): get_available_cash(), get_ibkr_account(), get_margin_loan(), get_own_cash(), main_loop(), _matches_account(), IB, Checks for daily breakout triggers and executes buy orders at market open. (+9 more)

### Community 30 - "Risk Management Plan"
Cohesion: 0.12
Nodes (15): 1. Gap risk — the stop doesn't protect you, 2. Higher volatility eats the trailing stop budget, 3. Wider bid-ask spreads, 4. Liquidity and market impact, 5. O'Neil's own guidance, Changes to `execution_agent.py`, Clarifying the actual risk, My recommendation (+7 more)

### Community 31 - "Buy Logic"
Cohesion: 0.12
Nodes (15): Buy Decision Flowchart, Buy Gate 1 — Portfolio Cap Check, Buy Gate 2 — Trigger Availability, Buy Gate 3 — Duplicate Position Guard, Buy Gate 4 — Cooling-Off Period, Buy Gate 5 — Re-verify Portfolio Cap (within loop), Buy Gate 6 — Cash Sufficiency, Buy Gate 7 — Pivot Extension (O'Neil Buy Zone) (+7 more)

### Community 32 - "Fundamental Filter Audit"
Cohesion: 0.13
Nodes (14): Current Filter Audit, Expected Impact, Fundamental Filter Alignment Plan, Implementation Order, Issue 1 — Volume Dead Zone (Highest Priority), Issue 2 — Price Threshold Mismatch, Issue 3 — No Market Cap Floor, Issue 4 — Annual EPS Threshold May Miss Momentum Breakouts (+6 more)

### Community 33 - "_fetch_current_rs"
Cohesion: 0.50
Nodes (4): _fetch_current_rs(), _get_entry_rs(), Return entry_rs_score for a newly opened position.      Prefers the rs_score alr, Fetch the stock's current 12-week return vs SPY and return its live RS score.

### Community 34 - "force_buy.py"
Cohesion: 0.20
Nodes (14): cancel_ticker_sell_orders(), place_trailing_stop(), Factory for IBKR TRAIL order type.     `ib_insync` 0.9.x does not export a Trail, Places a GTC Trailing Stop for an open stock position.     Trails stop_loss_pct%, Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sel, TrailingStopOrder(), get_ibkr_price(), main() (+6 more)

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.22
Nodes (8): 1. Dynamic Trailing Stop Loss (IBKR-Managed), 2. Day 3 Breakout Verdict & Intraday Loss Minimiser, 3. Moving Average Support Breach (Day 7+ EOD), Active Exit Mechanisms, Key Parameters, Legacy / Manual Scripts, Overview, Sell Logic

### Community 38 - "run_canslim_screener"
Cohesion: 0.33
Nodes (6): get_market_direction(), Evaluates a single ticker against C, A, N, S, L, I, M using FMP.     Returns a d, Scans the entire watchlist, updates scores in the SQLite database, and returns r, Analyzes ^GSPC (S&P 500) and ^IXIC (Nasdaq Composite) to determine general marke, run_canslim_screener(), scan_ticker()

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

### Community 43 - "make_position"
Cohesion: 0.12
Nodes (17): make_position(), Factory for a portfolio_positions Supabase row.      hwm_rs_score: RS score on t, patch, hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., Price below threshold near market close -> execute_sell called., Price below MA but within buffer -> no exit. (+9 more)

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
Cohesion: 0.08
Nodes (24): check_pre_breakout_coil(), compute_pre_breakout_quality_score(), Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, _coil(), _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None. (+16 more)

### Community 52 - "Build Verification Scripts"
Cohesion: 0.50
Nodes (3): DIST_DIR, failures, FEATURE_FINGERPRINTS

### Community 55 - "mock_ib"
Cohesion: 0.40
Nodes (5): mock_ib(), fixture, Default IB mock with no open positions., Auto-mock execution_agent.notifier for every test in the suite., _silence_notifier()

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

### Community 99 - "test_reconcile_detects_short_positions"
Cohesion: 0.50
Nodes (3): patch, Test that reconcile_with_ibkr detects short positions and sends alert., test_reconcile_detects_short_positions()

## Knowledge Gaps
- **237 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+232 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TelegramNotifier` connect `TelegramNotifier` to `compute_liquidity_score`, `force_buy.py`, `TeeLogger`, `execution_agent.py`, `technical_screener.py`?**
  _High betweenness centrality (0.105) - this node is a cross-community bridge._
- **Why does `make_ib_mock()` connect `make_ib_mock` to `_AV`, `make_supabase_mock`, `test_plateau_rotation.py`, `Self-Healing Order Tests`, `make_position`, `TestCancelTickerSellOrders`, `Portfolio Reconciliation Logic`, `mock_ib`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Why does `fetch_ibkr_delayed_price()` connect `fetch_ibkr_delayed_price` to `execution_agent.py`, `force_buy.py`, `run_market_open_buys`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `FMPClient` (e.g. with `BacktestRequest` and `SettingsUpdate`) actually correct?**
  _`FMPClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `name`, `private`, `version` to the rest of the system?**
  _237 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.05245901639344262 - nodes in this community are weakly interconnected._
- **Should `datetime` be split into smaller, more focused modules?**
  _Cohesion score 0.08115942028985507 - nodes in this community are weakly interconnected._