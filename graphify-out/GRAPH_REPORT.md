# Graph Report - ai-trading-bot  (2026-08-18)

## Corpus Check
- 158 files · ~215,313 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2359 nodes · 3679 edges · 242 communities (230 shown, 12 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 28 edges (avg confidence: 0.74)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `63cf0105`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- compute_liquidity_score
- datetime
- _AV
- Re-audit of the 2026-07-29 performance analysis
- DashboardView.jsx
- TelegramNotifier
- make_supabase_mock
- _compute_dynamic_trail_pct
- test_plateau_rotation.py
- _trigger
- _run
- main.py
- fetch_ibkr_delayed_price
- Frontend Dependencies
- make_ib_mock
- rotate_positions.py
- database.py
- telegram_notifier.py
- Decision: Repository-Wide Dead Code Cleanup
- FMPClient
- flex_query_sync.py
- rank_policy_bt.py
- _reconcile
- IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot
- Technical Triggers
- compute_rs_score
- FakeQuery
- Configuration Reference
- _resolve
- Exits
- Buy Logic
- Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py
- Backtest-corrected exit parameters; keep the entry tightening
- make_position
- Phase 1 — Extract the TOTP Base32 Secret from IBKR
- Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser
- Sell Logic
- execution_agent.py
- Fundamental Screener
- Slot count, and establishing the backtest's noise floor
- Self-Healing Order Tests
- per_symbol
- patch
- force_buy.py
- _triggers
- Decision: Breakout Quality Floor + Quota Waterfall
- Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals
- check_and_run_weekly_watchlist
- backtester.py
- docker-compose.yml
- compute_final_score
- Build Verification Scripts
- 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers
- _pos
- Plateau exit: optimise capital velocity, not per-trade expectancy
- Managed exit tool: exit at the session high rather than on impulse
- _reload
- ADR: Point-in-time trigger archive and buy/skip decision log
- Timezone Compliance Tests
- Decision
- BIRK
- Tune exits on the breakout population, not on the trades being eliminated
- ._run_at
- ai_evaluator.py
- Remove the `frontend/dist` bind mount; serve the UI from the image only
- The backtest "noise floor" was mostly a bootstrap bug
- Fix the self-defeating power-hold rule; move to 5 slots
- CMI
- MANIFEST.json
- reconcile_with_ibkr
- Commit a benchmark price dataset instead of re-fetching from FMP
- Committed benchmark price dataset
- _sb
- 2026-08-14 — Surface every risk rule's live state in the dashboard
- _run
- AAPL
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
- ABBV
- restart_6am.sh
- ABNB
- ABT
- ACN
- ADBE
- ADI
- ADP
- ADSK
- AEIS
- AEP
- AFL
- AJG
- ALAB
- ALL
- AMAT
- AMD
- AME
- AMGN
- AMKR
- AMT
- AMTM
- screener.py
- AON
- APD
- APH
- APO
- APP
- ARM
- ARW
- AS
- AVGO
- AXP
- BA
- BABA
- BAC
- BAM
- BDX
- BE
- ADR: Point-in-time watchlist history — make the fundamental screen backtestable
- BKNG
- BKR
- BMY
- BN
- BNY
- AMZN
- BX
- Make MAX_POSITIONS a single env-driven constant
- CAH
- CARR
- CAT
- _bars
- CCEP
- CDNA
- CDNS
- CEG
- CELH
- CF
- CI
- CIEN
- CL
- CMC
- CMCL
- CMCSA
- CME
- CMG
- 2026-08-13 — Reject "confirmed-breakout-first" trigger ranking
- COCO
- COF
- BSX
- COHU
- Drop stale and dead columns from `portfolio_positions`
- ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm
- CRH
- CRM
- CRWD
- CSCO
- CSX
- CTAS
- CTVA
- CVNA
- CVS
- CVX
- CXW
- D
- DAL
- DASH
- DDOG
- DE
- DELL
- DHR
- DIOD
- DIS
- DLR
- DUK
- DVN
- DXCM
- DY
- EA
- EBAY
- ECL
- ECO
- compute_pre_breakout_quality_score
- EME
- EMR
- EOG
- EPD
- ETN
- ETR
- _make_df
- EXC
- F
- FANG
- FAST
- FCX
- 2026-08-13 — Reconcile Supabase schema drift (7 unapplied migrations)
- FERG
- FITB
- FLYW
- fetch_daily.py
- to_parquet.py
- COST
- COP
- ANET
- COR
- trigger_audit.py
- Forward-return backfill for trigger_history (and the prune we did NOT build)
- increment_retention
- technical_screener.py
- COHR
- ELV
- TestPowerHoldTrailWidening
- TestPreBreakoutCoilPass
- _client
- schema_guard.py
- 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing
- Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing
- ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5
- Decision: Correct the look-ahead bias in the armed-exit backtest
- compute_momentum_health_score
- 3. Thesis Stop — days 2–5
- scoring.py
- CB
- Decision
- EW

## God Nodes (most connected - your core abstractions)
1. `per_symbol` - 124 edges
2. `make_ib_mock()` - 71 edges
3. `make_supabase_mock()` - 65 edges
4. `make_position()` - 48 edges
5. `make_trigger()` - 29 edges
6. `monitor_portfolio_intraday()` - 27 edges
7. `FMPClient` - 26 edges
8. `TelegramNotifier` - 24 edges
9. `_trigger()` - 23 edges
10. `compute_liquidity_score()` - 22 edges

## Surprising Connections (you probably didn't know these)
- `make_supabase_mock()` --indirect_call--> `_table()`  [INFERRED]
  tests/conftest.py → research/bardata.py
- `main()` --calls--> `compute_final_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `compute_liquidity_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `TelegramNotifier`  [EXTRACTED]
  ai_evaluator.py → telegram_notifier.py
- `TeeLogger` --uses--> `TelegramNotifier`  [INFERRED]
  execution_agent.py → telegram_notifier.py

## Import Cycles
- None detected.

## Communities (242 total, 12 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.13
Nodes (10): compute_liquidity_score(), Penalises low-price, low-volume, and small-cap stocks (0-100).      Price tier, NVDA-like: $750, 42M avg vol, Large -> max score, Mid-tier stock: $35, 800K vol, Mid, SGHC-like: $8, 180K vol, Small -> very low, $15 exact -> price tier = 20, $14.99 -> price tier = 10, $50 exactly -> price tier = 40 (+2 more)

### Community 1 - "datetime"
Cohesion: 0.08
Nodes (28): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _make_supabase_mock(), _monday(), datetime, patch, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:   1 (+20 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (30): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer.  Covers two crit, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g (+22 more)

### Community 3 - "Re-audit of the 2026-07-29 performance analysis"
Cohesion: 0.11
Nodes (17): Addendum — stop widened to 10% (user approved), Aggression: slot count was tested and 4 is correct, Audit result, Bug 2 — Day 3 verdict compared two stale volume bars (real defect), Consequences, Context, Fixed here, Follow-up (+9 more)

### Community 4 - "DashboardView.jsx"
Cohesion: 0.07
Nodes (30): App(), BacktesterView(), BreakoutsView(), BreakoutTable(), sortByConviction(), daysHeld(), ExitConditionsPanel(), formatDate() (+22 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.10
Nodes (16): Mirrors stdout to a daily rotating log file without touching print() calls., Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.          Uses the da, TeeLogger, Fires from ai_evaluator.py after all 5-component scores are computed.         Sh, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Distin, Sent when the Thesis Stop arms an exit.          Fires only for breakouts that h (+8 more)

### Community 6 - "make_supabase_mock"
Cohesion: 0.08
Nodes (27): make_ibkr_fill(), make_ohlcv_data(), make_portfolio_item(), make_supabase_mock(), mock_ib(), mock_supabase_empty(), fixture, Factory for an ibkr_fills Supabase row. (+19 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.08
Nodes (19): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     ot, parametrize, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the dynamic t, +20% (profit->6.5%) vs 30 days (time->3.5%) - time is tighter., +50% (profit->5%) vs 8 days (time->6%) - profit is tighter., Was at 6% trail, dipped to +22% (would suggest 6.5%). Must not loosen., Already at 6.5%, crosses +30% -> should tighten to 6%. (+11 more)

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.10
Nodes (26): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload., Within 3-6 days, even with param drift, no swap occurs if there are no fresh tri (+18 more)

### Community 9 - "_trigger"
Cohesion: 0.08
Nodes (22): Exception, _payloads(), tests/test_trigger_audit.py  Tests for the point-in-time trigger archive and the, PK includes trigger_type, so BREAKOUT and PRE_BREAKOUT coexist., Snapshotted rather than joined: the trigger row may be re-scored on a         la, A name skipped for lack of a slot says nothing about the quality         model b, A trigger can be re-evaluated on several days within the lookback         window, A failure here must never interrupt a live buy cycle. (+14 more)

### Community 10 - "_run"
Cohesion: 0.10
Nodes (34): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), fixture, tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD), Intr, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent. (+26 more)

### Community 11 - "main.py"
Cohesion: 0.11
Nodes (29): approve_rotation(), auto_generate_watchlist(), BacktestRequest, dismiss_rotation(), get_account_balances(), get_benchmark_returns(), get_breakouts(), get_cash_flows() (+21 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.09
Nodes (33): fetch_ibkr_delayed_price(), get_live_price(), Fetch the current price for a contract using IBKR delayed market data (type 3)., Fetch current price of a ticker from FMP., archive(), cancel_sells(), current_price(), main() (+25 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.07
Nodes (27): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+19 more)

### Community 14 - "make_ib_mock"
Cohesion: 0.14
Nodes (9): make_ib_mock(), Creates a mock IB instance whose portfolio() always returns the given symbols., place_trailing_stop() places exactly ONE GTC TRAIL order.     No LimitOrder (pro, TestCancelTickerSellOrders, TestPlaceTrailingStop, Case 1: In Supabase, NOT in IBKR → closed by IBKR (trailing stop / limit / TWS)., Position in Supabase but not IBKR → archived to trade_history.         IBKR port, Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill. (+1 more)

### Community 15 - "rotate_positions.py"
Cohesion: 0.16
Nodes (20): _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell(), IB, Display a numbered menu and return the chosen ticker. (+12 more)

### Community 16 - "database.py"
Cohesion: 0.16
Nodes (17): _bg_update_fmp_cache(), get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results(), get_setting() (+9 more)

### Community 17 - "telegram_notifier.py"
Cohesion: 0.20
Nodes (9): telegram_notifier.py — CANSLIM Trading Bot Telegram Notification Module  Fires r, notifier(), fixture, patch, Test that a non-200 HTTP response from the Telegram API is explicitly printed an, Test that a network exception (e.g. timeout) is explicitly printed and not swall, Returns a configured TelegramNotifier for testing., test_telegram_api_error_printed() (+1 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.20
Nodes (9): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Follow-up pass: one-off scripts not wired into the main programs, Problem (+1 more)

### Community 19 - "FMPClient"
Cohesion: 0.17
Nodes (8): FMPClient, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Gracefull, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint., DataFrame

### Community 20 - "flex_query_sync.py"
Cohesion: 0.11
Nodes (27): end, rows, start, ET, check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), fetch_trade_confirms_for_ticker() (+19 more)

### Community 21 - "rank_policy_bt.py"
Cohesion: 0.06
Nodes (69): coverage(), daily(), manifest(), Read-only loader for the committed benchmark price dataset.  The dataset lives i, Daily bars ascending by date, or None if the symbol is not in the dataset., Sorted union of dates across the given symbols (default: all)., symbols(), _table() (+61 more)

### Community 22 - "_reconcile"
Cohesion: 0.11
Nodes (15): Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).         The code, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: no absolute stop_loss price is stored.          The `stop_loss` column w, Case 3: In both, share count differs → update Supabase., IBKR has 150 shares, Supabase says 100 → update Supabase to 150., Case 3: IBKR and Supabase both have 100 shares → no update., Critical: reconcile_with_ibkr() must use ib.portfolio() everywhere.     ib.posit, The reconcile function must ONLY call ib.portfolio(), never ib.positions(). (+7 more)

### Community 23 - "IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot"
Cohesion: 0.18
Nodes (11): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Start execution agent, Step 7: Add secret to server .env, Step 8: Update docker-compose.yml (already coded — just needs to be pushed), Step 9: Restart the gateway (+3 more)

### Community 25 - "Technical Triggers"
Cohesion: 0.15
Nodes (13): `BREAKOUT` — the primary signal, Decision log, Forward-return outcomes, Parameters, Position in the pipeline, `PRE_BREAKOUT_RELAXED` — quota fill, `PRE_BREAKOUT` — the coil, Relative strength (+5 more)

### Community 26 - "compute_rs_score"
Cohesion: 0.16
Nodes (10): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, Stock +20%, SPY +5% -> excess +15% -> 100, Excess exactly 10% -> 100, Excess 5% -> 50 + 5*5 = 75, Same return as SPY -> 50, Excess -5% -> 50 + (-5)*5 = 25, Excess exactly -10% -> max(0, 50-50) = 0 (+2 more)

### Community 27 - "FakeQuery"
Cohesion: 0.15
Nodes (7): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, patch, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "Configuration Reference"
Cohesion: 0.13
Nodes (15): AI evaluator, Append-only research tables, Buy gating, Changing parameters safely, Configuration Reference, Credentials, Database schema, Deploying the dashboard (+7 more)

### Community 29 - "_resolve"
Cohesion: 0.18
Nodes (9): parametrize, Guards the single-source-of-truth invariant for MAX_POSITIONS.  ADR 2026-08-04 m, A module that re-reads the env itself can drift on its default. Root         mod, Importing config.py is useless if the image does not contain it., Return each module's view of the shared constants under a given env value., Changing slot count must need a .env edit only — never a code change., _resolve(), TestNoLocalRedeclaration (+1 more)

### Community 30 - "Exits"
Cohesion: 0.25
Nodes (8): Armed exit, Early loss and the superseded minimiser, Exits, Moving-average exit, Plateau and rotation, Power Hold, Thesis Stop, Trailing-stop ladder

### Community 31 - "Buy Logic"
Cohesion: 0.18
Nodes (11): Audit trail, Buy Logic, Design principle: fail closed, Order placement and post-fill, Parameter reference, Per-candidate gate stack, Position sizing, Pre-flight: portfolio-level blocks (+3 more)

### Community 32 - "Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py"
Cohesion: 0.29
Nodes (6): Alternatives considered, Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py, Consequences, Context, Decision, Guard

### Community 33 - "Backtest-corrected exit parameters; keep the entry tightening"
Cohesion: 0.14
Nodes (13): Backtest-corrected exit parameters; keep the entry tightening, Consequences, Context, Decision, Fidelity limits (important), Finding 1 — there was no right tail to protect, Finding 2 — ablation: only one of the four exit changes helps, Finding 3 — the early-loss reasoning was simply wrong (+5 more)

### Community 34 - "make_position"
Cohesion: 0.16
Nodes (15): make_position(), make_trigger(), Factory for a daily_triggers Supabase row.      final_score defaults to 75 (a no, Factory for a portfolio_positions Supabase row.      hwm_rs_score: RS score on t, Regression: ai_evaluator.py silently drops tickers from its batch         ("lost, adjusted_score (post-penalty) remains the primary gate input., Runs run_market_open_buys() with standard patches applied.     Returns the mock_, Gate 1: MAX_POSITIONS stock positions → portfolio full → no order placed. (+7 more)

### Community 35 - "Phase 1 — Extract the TOTP Base32 Secret from IBKR"
Cohesion: 0.29
Nodes (7): Phase 1 — Extract the TOTP Base32 Secret from IBKR, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings, Step 3: Re-enroll the Software Token (to reveal the secret), Step 4: Reveal the Base32 Secret — CRITICAL STEP, Step 5: Add to Microsoft Authenticator (same session), Step 6: Complete IBKR enrollment

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.12
Nodes (16): 1. Dynamic trailing stop (IBKR-managed), 2. Early Loss Kill-switch — days 0–1, 2b. Early Dollar Stop — days 0–5, 4. EMA-21 support breach — day 7+, 5. Plateau exit — day 7+, 6. Rank & Replace — day 7+, Armed Exit — the "smart sale" mechanism, Dashboard: the Risk Rule Ladder (+8 more)

### Community 38 - "execution_agent.py"
Cohesion: 0.09
Nodes (39): date, calculate_ema(), calculate_sma(), check_volume_distribution(), execute_sell(), _fetch_current_rs(), fetch_held_position_sentiment(), fetch_historical_closes_with_dates() (+31 more)

### Community 39 - "Fundamental Screener"
Cohesion: 0.15
Nodes (12): Filters, Fundamental Screener, Known limitation, Ordering invariant, Output, Parameters, Point-in-time archive, Purpose (+4 more)

### Community 40 - "Slot count, and establishing the backtest's noise floor"
Cohesion: 0.22
Nodes (8): Context, Follow-up, Harness defects found and fixed, Notable observations, not acted on, Re-verification: both major decisions hold under the slot constraint, Slot count, Slot count, and establishing the backtest's noise floor, The noise floor — the most important result here

### Community 41 - "Self-Healing Order Tests"
Cohesion: 0.24
Nodes (7): Even when price is below stop level, Python does NOT call execute_sell., Runs monitor_portfolio_intraday() with standard patches.     live_prices: dict o, If no open SELL orders exist for a position, monitor must re-place the     trail, No open SELL orders -> place_trailing_stop called for self-healing.         Use, Trailing stop already in IBKR -> no self-healing.         Use price=buy_price (0, _run_monitor(), TestSelfHealingTrailingStop

### Community 42 - "per_symbol"
Cohesion: 0.17
Nodes (12): end, rows, start, end, rows, start, end, rows (+4 more)

### Community 43 - "patch"
Cohesion: 0.09
Nodes (16): patch, hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., Price below threshold near market close -> execute_sell called., Price below MA but within buffer -> no exit., Outside 3:45-4:00 PM and EOD_ONLY enabled -> no exit., FMP historical fetch returns empty -> no exit and no crash. (+8 more)

### Community 44 - "force_buy.py"
Cohesion: 0.13
Nodes (19): config.py — single source of truth for cross-module trading parameters.  Every v, arm_exit(), cancel_ticker_sell_orders(), place_trailing_stop(), Client, datetime, Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sel, Factory for IBKR TRAIL order type.     `ib_insync` 0.9.x does not export a Trail (+11 more)

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

### Community 51 - "compute_final_score"
Cohesion: 0.13
Nodes (10): compute_final_score(), Weighted blend of 5 components (all 0-100) -> 0-100 final score.        Technica, TestPreBreakoutScoreBoost, NVDA-like scores -> should be around 80, SGHC-like scores -> should be around 40-50, Weighted formula: tech=100, rest=0 -> score = 30, liq=100, rest=0 -> score = 25, Score cannot exceed 100 (+2 more)

### Community 52 - "Build Verification Scripts"
Cohesion: 0.50
Nodes (3): DIST_DIR, failures, FEATURE_FINGERPRINTS

### Community 53 - "2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers"
Cohesion: 0.17
Nodes (11): 1. Fail closed on un-vetted triggers (`execution_agent.py`), 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers, 2. Batch the AI calls (`ai_evaluator.py`), 3. Demand completeness in the prompt, 4. Validate and retry, then alert, Consequences, Context, Decision (+3 more)

### Community 54 - "_pos"
Cohesion: 0.14
Nodes (15): is_power_hold_active(), maybe_arm_power_hold(), O'Neil 8-week hold rule.      True while a position is inside its protected wind, Persists the power-hold flag the first time a position qualifies.      Returns T, _client(), _pos(), test_power_hold.py - Tests for the O'Neil 8-week hold rule.  From "How to Make M, PGRST204 = migration not run yet. The rule must still apply in-memory for (+7 more)

### Community 55 - "Plateau exit: optimise capital velocity, not per-trade expectancy"
Cohesion: 0.18
Nodes (10): 5 days was an overfit trap, Consequences, Context, Decision, Findings, Follow-up, Method, Per-trade analysis says plateau exits are harmful (+2 more)

### Community 56 - "Managed exit tool: exit at the session high rather than on impulse"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Managed exit tool: exit at the session high rather than on impulse, Note on the positions that prompted this, The hard floor is what makes patience safe, Trail sizing is volatility-scaled, not fixed

### Community 57 - "_reload"
Cohesion: 0.13
Nodes (10): fixture, test_screener_filters.py - Tests for the CAN SLIM fundamental gate in tv_api_scr, Re-import the screener module with the given env overrides applied., Regression: was 0, which admitted SWK on 0.6% revenue growth., Regression: was 15. O'Neil requires ~25%., Thresholds must be adjustable without a code change, for A/B and rollback., _reload(), screener() (+2 more)

### Community 58 - "ADR: Point-in-time trigger archive and buy/skip decision log"
Cohesion: 0.20
Nodes (9): ADR: Point-in-time trigger archive and buy/skip decision log, Consequences, Context, Decision, Files, The `is_capacity` flag, `trigger_decisions` — what the bot did, and why, `trigger_history` — what the screener saw (+1 more)

### Community 60 - "Decision"
Cohesion: 0.17
Nodes (11): Cause 1 — the exits amputated winners, Cause 2 — the screener was not selecting CAN SLIM stocks, Consequences, Context, Decision, Entries — actually buy CAN SLIM stocks, Exits — stop selling on noise, Follow-up (+3 more)

### Community 61 - "BIRK"
Cohesion: 0.50
Nodes (4): end, rows, start, BIRK

### Community 62 - "Tune exits on the breakout population, not on the trades being eliminated"
Cohesion: 0.17
Nodes (11): 1. The wide profit ladder wins on the real population, 2. The Intraday Loss Minimiser is the most damaging exit in the system, 3. The 7% base trailing stop is too tight — not acted on yet, 4. The breakout timing signal has no measurable edge, Consequences, Context, Decision, Findings (+3 more)

### Community 63 - "._run_at"
Cohesion: 0.33
Nodes (4): The pivot check used to be a ceiling only: it rejected stocks extended too     f, The buy loop takes its price from fetch_ibkr_delayed_price, not         get_live, A 1% dip is noise around the pivot, not a failed breakout., TestPivotBuyZoneFloor

### Community 64 - "ai_evaluator.py"
Cohesion: 0.16
Nodes (18): ai_grade_and_bonus(), build_prompt(), call_ai_batch(), evaluate_triggers(), fetch_daily_triggers(), fetch_news_headlines(), fetch_trade_history(), fetch_watchlist_data() (+10 more)

### Community 65 - "Remove the `frontend/dist` bind mount; serve the UI from the image only"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Rejected alternatives, Related, Remove the `frontend/dist` bind mount; serve the UI from the image only, Root cause

### Community 66 - "The backtest "noise floor" was mostly a bootstrap bug"
Cohesion: 0.17
Nodes (11): Caveats, Conclusions that change, Conclusions that hold, Context, Follow-up, Results, The backtest "noise floor" was mostly a bootstrap bug, The bug (+3 more)

### Community 67 - "Fix the self-defeating power-hold rule; move to 5 slots"
Cohesion: 0.22
Nodes (8): Consequences, Context, Decision 1 — MAX_POSITIONS 4 → 5, Decision 2 — bypass the profit ladder while power-held, Expected result, Fidelity limits, Fix the self-defeating power-hold rule; move to 5 slots, Rejected

### Community 68 - "CMI"
Cohesion: 0.50
Nodes (4): end, rows, start, CMI

### Community 69 - "MANIFEST.json"
Cohesion: 0.22
Nodes (8): bar_interval, bytes, dataset, date_max, date_min, endpoint, file, generated_utc

### Community 70 - "reconcile_with_ibkr"
Cohesion: 0.16
Nodes (17): fetch_trade_confirms_for_ticker(), get_available_cash(), get_ibkr_account(), get_margin_loan(), get_own_cash(), _matches_account(), IB, Full bidirectional reconciliation between IBKR actual positions and Supabase led (+9 more)

### Community 71 - "Commit a benchmark price dataset instead of re-fetching from FMP"
Cohesion: 0.22
Nodes (8): Commit a benchmark price dataset instead of re-fetching from FMP, Decision, Format, Housekeeping, Problem, Reproducibility contract, What is deliberately excluded, Why not the alternatives

### Community 72 - "Committed benchmark price dataset"
Cohesion: 0.25
Nodes (7): Committed benchmark price dataset, Contents, Known limitations, Not yet included: 5-minute bars, Rebuilding / extending, Universe, Usage

### Community 73 - "_sb"
Cohesion: 0.12
Nodes (21): _extras(), tests/test_watchlist_history.py  Tests for the append-only point-in-time watchli, Directly testable as a buy gate: do names qualifying many runs         running o, A same-day re-run must overwrite, not duplicate., Append-only. A delete here would reintroduce the very data loss this         tab, A research feature must never be able to break live screening., THE critical invariant. `watchlist` is wiped every run; if the archive     ran a, Research extras must not leak into the `watchlist` insert — those         column (+13 more)

### Community 75 - "2026-08-14 — Surface every risk rule's live state in the dashboard"
Cohesion: 0.22
Nodes (8): 2026-08-14 — Surface every risk rule's live state in the dashboard, Consequences, Context, Correctness issues found and fixed while implementing, Decision, Files, Follow-up, Status

### Community 76 - "_run"
Cohesion: 0.16
Nodes (19): _make_ib(), _make_pos(), _make_sb(), tests/test_thesis_stop.py  Tests for the Thesis Stop — an ATR-normalised failure, Day 4, ATR 3%/day, price -5% -> beyond -3% threshold -> arm exit., The trigger price is usually a local trough.          Arming a tight trail beat, A fixed percentage is meaningless across names: DXCM moves 4%/day, so a     2% s, The failure mode that killed the Intraday Loss Minimiser. (+11 more)

### Community 77 - "AAPL"
Cohesion: 0.50
Nodes (4): end, rows, start, AAPL

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

### Community 97 - "ABBV"
Cohesion: 0.50
Nodes (4): end, rows, start, ABBV

### Community 99 - "ABNB"
Cohesion: 0.50
Nodes (4): end, rows, start, ABNB

### Community 100 - "ABT"
Cohesion: 0.50
Nodes (4): end, rows, start, ABT

### Community 101 - "ACN"
Cohesion: 0.50
Nodes (4): end, rows, start, ACN

### Community 102 - "ADBE"
Cohesion: 0.50
Nodes (4): end, rows, start, ADBE

### Community 103 - "ADI"
Cohesion: 0.50
Nodes (4): end, rows, start, ADI

### Community 104 - "ADP"
Cohesion: 0.50
Nodes (4): end, rows, start, ADP

### Community 105 - "ADSK"
Cohesion: 0.50
Nodes (4): end, rows, start, ADSK

### Community 106 - "AEIS"
Cohesion: 0.50
Nodes (4): end, rows, start, AEIS

### Community 107 - "AEP"
Cohesion: 0.50
Nodes (4): end, rows, start, AEP

### Community 108 - "AFL"
Cohesion: 0.50
Nodes (4): end, rows, start, AFL

### Community 109 - "AJG"
Cohesion: 0.50
Nodes (4): end, rows, start, AJG

### Community 110 - "ALAB"
Cohesion: 0.50
Nodes (4): end, rows, start, ALAB

### Community 111 - "ALL"
Cohesion: 0.50
Nodes (4): end, rows, start, ALL

### Community 112 - "AMAT"
Cohesion: 0.50
Nodes (4): end, rows, start, AMAT

### Community 113 - "AMD"
Cohesion: 0.50
Nodes (4): end, rows, start, AMD

### Community 114 - "AME"
Cohesion: 0.50
Nodes (4): end, rows, start, AME

### Community 115 - "AMGN"
Cohesion: 0.50
Nodes (4): end, rows, start, AMGN

### Community 116 - "AMKR"
Cohesion: 0.50
Nodes (4): end, rows, start, AMKR

### Community 117 - "AMT"
Cohesion: 0.50
Nodes (4): end, rows, start, AMT

### Community 118 - "AMTM"
Cohesion: 0.50
Nodes (4): end, rows, start, AMTM

### Community 119 - "screener.py"
Cohesion: 0.29
Nodes (9): get_watchlist(), calculate_rs_scores(), get_market_direction(), Evaluates a single ticker against C, A, N, S, L, I, M using FMP.     Returns a d, Scans the entire watchlist, updates scores in the SQLite database, and returns r, Analyzes ^GSPC (S&P 500) and ^IXIC (Nasdaq Composite) to determine general marke, Calculates Relative Strength performance weighted:     40% recent Q (last 3m), 2, run_canslim_screener() (+1 more)

### Community 120 - "AON"
Cohesion: 0.50
Nodes (4): end, rows, start, AON

### Community 121 - "APD"
Cohesion: 0.50
Nodes (4): end, rows, start, APD

### Community 122 - "APH"
Cohesion: 0.50
Nodes (4): end, rows, start, APH

### Community 123 - "APO"
Cohesion: 0.50
Nodes (4): end, rows, start, APO

### Community 124 - "APP"
Cohesion: 0.50
Nodes (4): end, rows, start, APP

### Community 125 - "ARM"
Cohesion: 0.50
Nodes (4): end, rows, start, ARM

### Community 126 - "ARW"
Cohesion: 0.50
Nodes (4): end, rows, start, ARW

### Community 127 - "AS"
Cohesion: 0.50
Nodes (4): end, rows, start, AS

### Community 128 - "AVGO"
Cohesion: 0.50
Nodes (4): end, rows, start, AVGO

### Community 129 - "AXP"
Cohesion: 0.50
Nodes (4): end, rows, start, AXP

### Community 130 - "BA"
Cohesion: 0.50
Nodes (4): end, rows, start, BA

### Community 131 - "BABA"
Cohesion: 0.50
Nodes (4): end, rows, start, BABA

### Community 132 - "BAC"
Cohesion: 0.50
Nodes (4): end, rows, start, BAC

### Community 133 - "BAM"
Cohesion: 0.50
Nodes (4): end, rows, start, BAM

### Community 134 - "BDX"
Cohesion: 0.50
Nodes (4): end, rows, start, BDX

### Community 135 - "BE"
Cohesion: 0.50
Nodes (4): end, rows, start, BE

### Community 136 - "ADR: Point-in-time watchlist history — make the fundamental screen backtestable"
Cohesion: 0.22
Nodes (8): ADR: Point-in-time watchlist history — make the fundamental screen backtestable, Consequences, Context, Decision, Files, Pre-existing issue observed, NOT addressed here, Store the raw metrics, not just the tickers, What this makes answerable

### Community 137 - "BKNG"
Cohesion: 0.50
Nodes (4): end, rows, start, BKNG

### Community 138 - "BKR"
Cohesion: 0.50
Nodes (4): end, rows, start, BKR

### Community 139 - "BMY"
Cohesion: 0.50
Nodes (4): end, rows, start, BMY

### Community 140 - "BN"
Cohesion: 0.50
Nodes (4): end, rows, start, BN

### Community 141 - "BNY"
Cohesion: 0.50
Nodes (4): end, rows, start, BNY

### Community 142 - "AMZN"
Cohesion: 0.50
Nodes (4): end, rows, start, AMZN

### Community 143 - "BX"
Cohesion: 0.50
Nodes (4): end, rows, start, BX

### Community 144 - "Make MAX_POSITIONS a single env-driven constant"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Make MAX_POSITIONS a single env-driven constant, Note on test isolation, Verification

### Community 145 - "CAH"
Cohesion: 0.50
Nodes (4): end, rows, start, CAH

### Community 146 - "CARR"
Cohesion: 0.50
Nodes (4): end, rows, start, CARR

### Community 147 - "CAT"
Cohesion: 0.50
Nodes (4): end, rows, start, CAT

### Community 148 - "_bars"
Cohesion: 0.05
Nodes (29): compute_outcomes(), fetch_pending(), fetch_prices(), main(), _pct(), backfill_trigger_outcomes.py  Weekly job that links archived breakout triggers t, Forward returns measured from the first session AFTER triggered_at.      CONVENT, Triggers whose measurement window has fully elapsed and are unmeasured. (+21 more)

### Community 149 - "CCEP"
Cohesion: 0.50
Nodes (4): end, rows, start, CCEP

### Community 150 - "CDNA"
Cohesion: 0.50
Nodes (4): end, rows, start, CDNA

### Community 151 - "CDNS"
Cohesion: 0.50
Nodes (4): end, rows, start, CDNS

### Community 152 - "CEG"
Cohesion: 0.50
Nodes (4): end, rows, start, CEG

### Community 153 - "CELH"
Cohesion: 0.50
Nodes (4): end, rows, start, CELH

### Community 154 - "CF"
Cohesion: 0.50
Nodes (4): end, rows, start, CF

### Community 155 - "CI"
Cohesion: 0.50
Nodes (4): end, rows, start, CI

### Community 156 - "CIEN"
Cohesion: 0.50
Nodes (4): end, rows, start, CIEN

### Community 157 - "CL"
Cohesion: 0.50
Nodes (4): end, rows, start, CL

### Community 158 - "CMC"
Cohesion: 0.50
Nodes (4): end, rows, start, CMC

### Community 159 - "CMCL"
Cohesion: 0.50
Nodes (4): end, rows, start, CMCL

### Community 160 - "CMCSA"
Cohesion: 0.50
Nodes (4): end, rows, start, CMCSA

### Community 161 - "CME"
Cohesion: 0.50
Nodes (4): end, rows, start, CME

### Community 162 - "CMG"
Cohesion: 0.50
Nodes (4): end, rows, start, CMG

### Community 163 - "2026-08-13 — Reject "confirmed-breakout-first" trigger ranking"
Cohesion: 0.20
Nodes (9): 2026-08-13 — Reject "confirmed-breakout-first" trigger ranking, Context, Decision, Follow-ups, Limitations (must accompany any citation of this result), Method, Reproduce, Results (+1 more)

### Community 164 - "COCO"
Cohesion: 0.50
Nodes (4): end, rows, start, COCO

### Community 165 - "COF"
Cohesion: 0.50
Nodes (4): end, rows, start, COF

### Community 166 - "BSX"
Cohesion: 0.50
Nodes (4): end, rows, start, BSX

### Community 167 - "COHU"
Cohesion: 0.50
Nodes (4): end, rows, start, COHU

### Community 168 - "Drop stale and dead columns from `portfolio_positions`"
Cohesion: 0.25
Nodes (7): Consequences, Decision, Deliberately kept, Drop stale and dead columns from `portfolio_positions`, Implementation, Problem, Verification

### Community 169 - "ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm"
Cohesion: 0.17
Nodes (11): ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm, Consequences, Context, Decision, Entry-filter improvements — REJECTED, Evidence, Files, Known limitation (+3 more)

### Community 170 - "CRH"
Cohesion: 0.50
Nodes (4): end, rows, start, CRH

### Community 171 - "CRM"
Cohesion: 0.50
Nodes (4): end, rows, start, CRM

### Community 172 - "CRWD"
Cohesion: 0.50
Nodes (4): end, rows, start, CRWD

### Community 173 - "CSCO"
Cohesion: 0.50
Nodes (4): end, rows, start, CSCO

### Community 174 - "CSX"
Cohesion: 0.50
Nodes (4): end, rows, start, CSX

### Community 175 - "CTAS"
Cohesion: 0.50
Nodes (4): end, rows, start, CTAS

### Community 176 - "CTVA"
Cohesion: 0.50
Nodes (4): end, rows, start, CTVA

### Community 177 - "CVNA"
Cohesion: 0.50
Nodes (4): end, rows, start, CVNA

### Community 178 - "CVS"
Cohesion: 0.50
Nodes (4): end, rows, start, CVS

### Community 179 - "CVX"
Cohesion: 0.50
Nodes (4): end, rows, start, CVX

### Community 180 - "CXW"
Cohesion: 0.50
Nodes (4): end, rows, start, CXW

### Community 181 - "D"
Cohesion: 0.50
Nodes (4): end, rows, start, D

### Community 182 - "DAL"
Cohesion: 0.50
Nodes (4): end, rows, start, DAL

### Community 183 - "DASH"
Cohesion: 0.50
Nodes (4): end, rows, start, DASH

### Community 184 - "DDOG"
Cohesion: 0.50
Nodes (4): end, rows, start, DDOG

### Community 185 - "DE"
Cohesion: 0.50
Nodes (4): end, rows, start, DE

### Community 186 - "DELL"
Cohesion: 0.50
Nodes (4): end, rows, start, DELL

### Community 187 - "DHR"
Cohesion: 0.50
Nodes (4): end, rows, start, DHR

### Community 188 - "DIOD"
Cohesion: 0.50
Nodes (4): end, rows, start, DIOD

### Community 189 - "DIS"
Cohesion: 0.50
Nodes (4): end, rows, start, DIS

### Community 190 - "DLR"
Cohesion: 0.50
Nodes (4): end, rows, start, DLR

### Community 191 - "DUK"
Cohesion: 0.50
Nodes (4): end, rows, start, DUK

### Community 192 - "DVN"
Cohesion: 0.50
Nodes (4): end, rows, start, DVN

### Community 193 - "DXCM"
Cohesion: 0.50
Nodes (4): end, rows, start, DXCM

### Community 194 - "DY"
Cohesion: 0.50
Nodes (4): end, rows, start, DY

### Community 195 - "EA"
Cohesion: 0.50
Nodes (4): end, rows, start, EA

### Community 196 - "EBAY"
Cohesion: 0.50
Nodes (4): end, rows, start, EBAY

### Community 197 - "ECL"
Cohesion: 0.50
Nodes (4): end, rows, start, ECL

### Community 198 - "ECO"
Cohesion: 0.50
Nodes (4): end, rows, start, ECO

### Community 199 - "compute_pre_breakout_quality_score"
Cohesion: 0.15
Nodes (12): check_pre_breakout_coil(), compute_pre_breakout_quality_score(), Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, _coil(), Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65. (+4 more)

### Community 200 - "EME"
Cohesion: 0.50
Nodes (4): end, rows, start, EME

### Community 201 - "EMR"
Cohesion: 0.50
Nodes (4): end, rows, start, EMR

### Community 202 - "EOG"
Cohesion: 0.50
Nodes (4): end, rows, start, EOG

### Community 203 - "EPD"
Cohesion: 0.50
Nodes (4): end, rows, start, EPD

### Community 204 - "ETN"
Cohesion: 0.50
Nodes (4): end, rows, start, ETN

### Community 205 - "ETR"
Cohesion: 0.50
Nodes (4): end, rows, start, ETR

### Community 206 - "_make_df"
Cohesion: 0.20
Nodes (8): _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None., Recent 3d avg vol 1.1x 50d avg -> sellers still active -> None., Strictly descending then tiny uptick: must compare vs prior row.         Use all, TestPreBreakoutCoilFail

### Community 207 - "EXC"
Cohesion: 0.50
Nodes (4): end, rows, start, EXC

### Community 208 - "F"
Cohesion: 0.50
Nodes (4): end, rows, start, F

### Community 209 - "FANG"
Cohesion: 0.50
Nodes (4): end, rows, start, FANG

### Community 210 - "FAST"
Cohesion: 0.50
Nodes (4): end, rows, start, FAST

### Community 211 - "FCX"
Cohesion: 0.50
Nodes (4): end, rows, start, FCX

### Community 212 - "2026-08-13 — Reconcile Supabase schema drift (7 unapplied migrations)"
Cohesion: 0.20
Nodes (9): 2026-08-13 — Reconcile Supabase schema drift (7 unapplied migrations), Consequences, Context, Decision, Findings, Follow-up, How to apply, Status (+1 more)

### Community 213 - "FERG"
Cohesion: 0.50
Nodes (4): end, rows, start, FERG

### Community 214 - "FITB"
Cohesion: 0.50
Nodes (4): end, rows, start, FITB

### Community 215 - "FLYW"
Cohesion: 0.50
Nodes (4): end, rows, start, FLYW

### Community 218 - "COST"
Cohesion: 0.50
Nodes (4): end, rows, start, COST

### Community 219 - "COP"
Cohesion: 0.50
Nodes (4): end, rows, start, COP

### Community 220 - "ANET"
Cohesion: 0.50
Nodes (4): end, rows, start, ANET

### Community 221 - "COR"
Cohesion: 0.50
Nodes (4): end, rows, start, COR

### Community 222 - "trigger_audit.py"
Cohesion: 0.27
Nodes (10): trigger_audit.py  Point-in-time archive of breakout triggers and the buy/skip de, Record one buy/skip verdict against one trigger.      `decision` is BOUGHT or SK, Record the same verdict against many triggers.      Used when the portfolio is a, Chunked, idempotent, non-fatal upsert., Archive `daily_triggers` rows to the append-only `trigger_history`.      MUST be, record_decisions_bulk(), record_trigger_decision(), save_trigger_history() (+2 more)

### Community 223 - "Forward-return backfill for trigger_history (and the prune we did NOT build)"
Cohesion: 0.22
Nodes (8): Consequences, Context, Conventions (the part that is easy to get silently wrong), Decision, Forward-return backfill for trigger_history (and the prune we did NOT build), Guards, Rejected: the 6-month rolling prune, Verification

### Community 224 - "increment_retention"
Cohesion: 0.43
Nodes (5): increment_retention(), get_rating_text(), Append this run's screener output to the append-only `watchlist_history`.      `, run_screener(), save_watchlist_history()

### Community 225 - "technical_screener.py"
Cohesion: 0.23
Nodes (12): check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync(), get_supabase_client(), get_watchlist_from_supabase(), Client (+4 more)

### Community 226 - "COHR"
Cohesion: 0.50
Nodes (4): end, rows, start, COHR

### Community 227 - "ELV"
Cohesion: 0.50
Nodes (4): end, rows, start, ELV

### Community 228 - "TestPowerHoldTrailWidening"
Cohesion: 0.20
Nodes (5): The rule was previously self-defeating: TRAIL_PROFIT_TIERS tightens the trail, Guards the premise: at +20% the ladder clamps to a tight trail., Never remove the stop entirely, however strong the backtest looked., The widened trail can only ever apply to a position already well in         prof, TestPowerHoldTrailWidening

### Community 229 - "TestPreBreakoutCoilPass"
Cohesion: 0.29
Nodes (4): 5% below high, vol contracting, 3/3 closes up -> PRE_BREAKOUT., 2 of 3 closes rising meets PRE_BREAKOUT_UPTREND_MIN=2., Within 1% of pivot -> quality_score >= 70 (vol 0.6x avg -> contraction pts ~16)., TestPreBreakoutCoilPass

### Community 230 - "_client"
Cohesion: 0.11
Nodes (10): _client(), _FakeQuery, tests/test_schema_guard.py  Tests for the startup schema assertion.  Context: on, A monitoring concern must never become a trading outage., The exact drift found in production must be reported as degraded., TestAdvisoryTables, TestBuyGate, TestCriticalColumns (+2 more)

### Community 231 - "schema_guard.py"
Cohesion: 0.20
Nodes (8): assert_schema_ok(), Verify risk-rule columns exist. Returns False when new buys must be blocked., check_schema(), _probe(), Startup schema assertion — fail LOUD when a risk rule's columns are missing.  WH, True if the table (and column, if given) is queryable., Probe every object a risk rule or archive depends on., SchemaReport

### Community 232 - "2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing"
Cohesion: 0.20
Nodes (9): 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing, Consequences, Context, Decision, Evidence for preferring the close latch, Files, Follow-up, Related change: the migration backfill was itself unsafe (+1 more)

### Community 233 - "Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing"
Cohesion: 0.25
Nodes (7): Consequences, Decision, Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing, Files changed, Method — decision rule fixed before looking at results, Result 1 — no configuration survives, Result 2 — why they disagree: the rule barely does anything

### Community 234 - "ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5"
Cohesion: 0.33
Nodes (5): ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5, Consequences, Context, Decision, Simulation

### Community 235 - "Decision: Correct the look-ahead bias in the armed-exit backtest"
Cohesion: 0.20
Nodes (9): Consequences — two shipped claims do not survive, Decision, Decision: Correct the look-ahead bias in the armed-exit backtest, Files changed, Problem, The 0.6% trail was never defensible on noise grounds, The armed exit is unproven, not proven, The thesis stop's headline result loses significance (+1 more)

### Community 236 - "compute_momentum_health_score"
Cohesion: 0.33
Nodes (6): compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), Wilder's smoothed RSI from a list of closing prices.      Returns a list of RSI, Detect bearish reversal candles on the last 3 bars near the plateau zone.      R, Live Momentum Health Score Mₜ (0–100) for a held position.      Returns (score,

### Community 237 - "3. Thesis Stop — days 2–5"
Cohesion: 0.50
Nodes (4): 3. Thesis Stop — days 2–5, Validation, What the rule actually does — and why it is not tuned for CAGR, Why the latch is the whole rule

### Community 239 - "CB"
Cohesion: 0.50
Nodes (4): end, rows, start, CB

### Community 240 - "Decision"
Cohesion: 0.22
Nodes (8): 1. Hard volume surge gate in `execution_agent.py`, 2. PRE_BREAKOUT 52W pivot distance gate in `execution_agent.py`, 3. AI prompt penalty rules in `ai_evaluator.py`, 4. D-veto threshold raised from 30 → 50, ADR: Buy Gate Hardening — Volume Surge Floor, PRE_BREAKOUT Pivot Distance, AI Penalty Rules, Consequences, Context, Decision

### Community 241 - "EW"
Cohesion: 0.50
Nodes (4): end, rows, start, EW

## Knowledge Gaps
- **739 isolated node(s):** `bar_interval`, `bytes`, `dataset`, `date_max`, `date_min` (+734 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `per_symbol` connect `per_symbol` to `flex_query_sync.py`, `BIRK`, `CMI`, `MANIFEST.json`, `AAPL`, `ABBV`, `ABNB`, `ABT`, `ACN`, `ADBE`, `ADI`, `ADP`, `ADSK`, `AEIS`, `AEP`, `AFL`, `AJG`, `ALAB`, `ALL`, `AMAT`, `AMD`, `AME`, `AMGN`, `AMKR`, `AMT`, `AMTM`, `AON`, `APD`, `APH`, `APO`, `APP`, `ARM`, `ARW`, `AS`, `AVGO`, `AXP`, `BA`, `BABA`, `BAC`, `BAM`, `BDX`, `BE`, `BKNG`, `BKR`, `BMY`, `BN`, `BNY`, `AMZN`, `BX`, `CAH`, `CARR`, `CAT`, `CCEP`, `CDNA`, `CDNS`, `CEG`, `CELH`, `CF`, `CI`, `CIEN`, `CL`, `CMC`, `CMCL`, `CMCSA`, `CME`, `CMG`, `COCO`, `COF`, `BSX`, `COHU`, `CRH`, `CRM`, `CRWD`, `CSCO`, `CSX`, `CTAS`, `CTVA`, `CVNA`, `CVS`, `CVX`, `CXW`, `D`, `DAL`, `DASH`, `DDOG`, `DE`, `DELL`, `DHR`, `DIOD`, `DIS`, `DLR`, `DUK`, `DVN`, `DXCM`, `DY`, `EA`, `EBAY`, `ECL`, `ECO`, `EME`, `EMR`, `EOG`, `EPD`, `ETN`, `ETR`, `EXC`, `F`, `FANG`, `FAST`, `FCX`, `FERG`, `FITB`, `FLYW`, `COST`, `COP`, `ANET`, `COR`, `COHR`, `ELV`, `CB`, `EW`?**
  _High betweenness centrality (0.270) - this node is a cross-community bridge._
- **Why does `ET` connect `flex_query_sync.py` to `per_symbol`?**
  _High betweenness centrality (0.225) - this node is a cross-community bridge._
- **Why does `fetch_trade_confirms_for_ticker()` connect `flex_query_sync.py` to `reconcile_with_ibkr`, `execution_agent.py`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **What connects `bar_interval`, `bytes`, `dataset` to the rest of the system?**
  _739 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.12666666666666668 - nodes in this community are weakly interconnected._
- **Should `datetime` be split into smaller, more focused modules?**
  _Cohesion score 0.08115942028985507 - nodes in this community are weakly interconnected._
- **Should `_AV` be split into smaller, more focused modules?**
  _Cohesion score 0.07312925170068027 - nodes in this community are weakly interconnected._