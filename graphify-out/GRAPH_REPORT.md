# Graph Report - ai-trading-bot  (2026-09-04)

## Corpus Check
- 198 files · ~291,121 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3034 nodes · 4723 edges · 289 communities (274 shown, 15 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 42 edges (avg confidence: 0.75)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `e18936da`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- compute_liquidity_score
- datetime
- _AV
- Re-audit of the 2026-07-29 performance analysis
- positionRules.js
- TelegramNotifier
- make_ib_mock
- _compute_dynamic_trail_pct
- test_plateau_rotation.py
- _trigger
- _run
- main.py
- fetch_ibkr_delayed_price
- package.json
- mock_ib
- ExitDetailPanel.jsx
- database.py
- _pos
- Decision: Repository-Wide Dead Code Cleanup
- FMPClient
- flex_query_sync.py
- breakout_bt.py
- test_reconcile.py
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
- make_supabase_mock
- Phase 1 — Extract the TOTP Base32 Secret from IBKR
- Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser
- Sell Logic
- CB
- Fundamental Screener
- Slot count, and establishing the backtest's noise floor
- test_sell_logic.py
- per_symbol
- compute_pre_breakout_quality_score
- _run_queue
- _triggers
- Decision: Breakout Quality Floor + Quota Waterfall
- Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals
- trigger_audit.py
- backtester.py
- docker-compose.yml
- compute_final_score
- verify-build.mjs
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
- EW
- Commit a benchmark price dataset instead of re-fetching from FMP
- Committed benchmark price dataset
- _sb
- 2026-08-14 — Surface every risk rule's live state in the dashboard
- _make_pos
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
- test_supabase_backup.py
- make_history
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
- Why
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
- Decision: Correct the look-ahead bias in the armed-exit backtest
- Drop stale and dead columns from `portfolio_positions`
- ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm
- CRH
- Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing
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
- increment_retention
- EME
- EMR
- EOG
- EPD
- ETN
- ETR
- TestExitContextSuffix
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
- test_volatility_fit.py
- Forward-return backfill for trigger_history (and the prune we did NOT build)
- Backups
- Decision
- COHR
- ELV
- ._run
- BreakoutsView.jsx
- _client
- schema_guard.py
- 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing
- Early Loss Kill-switch: tighten to 1% and restrict to the entry day
- DashboardView.jsx
- App.jsx
- test_oca_managed_exit.py
- request_exit.py
- Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those
- patch
- Decision
- Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed
- test_exit_timestamp.py
- TestQueueMarketMode
- Decision: Scope the volume surge gate to confirmed breakouts only
- force_buy.py
- Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price
- TestManagedTickers
- TestPlaceOca
- The Prove-It Stop — one loss rule replaces five
- TestSmartExitRuleScoping
- HWM profit-lock after the first leg
- exit_rule_replay.py
- 2026-09-04 — Prove-It Stop consolidation
- Early Dollar Stop becomes slot-derived, not a flat dollar amount
- rotate_positions.py
- monitor_portfolio_intraday
- atr_rank_bt.py
- _with_equity
- Retune HWM profit-lock arm from +6% to +5%
- Closed-loop learning from realised outcomes
- _make_df
- COHU
- Source dashboard position values from IBKR, not FMP
- CRM
- Exit detail panel, and the removal of fabricated exit reasons
- managed_exit.py
- AMTM
- CAH
- Client
- paired_block_bootstrap
- Price live-position exit logic from IBKR, not FMP
- rank_policy_bt.py
- thesis_bt.py
- AI evaluator: replace the velocity ladder with volatility fit
- bardata.py
- technical_screener.py
- 2. The Prove-It Stop — always live
- Smart OCA Managed Exit
- TestReconcileCase4
- TestPreBreakoutCoilPass
- execution_agent.py
- _position_atr_pct
- Evidence
- test_reconcile_detects_short_positions
- .test_case1_removes_from_portfolio_and_logs_trade
- ._eod_monitor
- ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5

## God Nodes (most connected - your core abstractions)
1. `per_symbol` - 124 edges
2. `make_ib_mock()` - 76 edges
3. `make_supabase_mock()` - 61 edges
4. `_pos()` - 50 edges
5. `make_position()` - 48 edges
6. `monitor_portfolio_intraday()` - 31 edges
7. `make_trigger()` - 30 edges
8. `FMPClient` - 26 edges
9. `TelegramNotifier` - 24 edges
10. `_trigger()` - 23 edges

## Surprising Connections (you probably didn't know these)
- `make_supabase_mock()` --indirect_call--> `_table()`  [INFERRED]
  tests/conftest.py → research/bardata.py
- `_queue_sb()` --indirect_call--> `_table()`  [INFERRED]
  tests/test_oca_managed_exit.py → research/bardata.py
- `make_client()` --indirect_call--> `_table()`  [INFERRED]
  tests/test_supabase_backup.py → research/bardata.py
- `_format_trigger_block()` --calls--> `volatility_fit()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `compute_final_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py

## Import Cycles
- None detected.

## Communities (289 total, 15 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.13
Nodes (10): compute_liquidity_score(), Penalises low-price, low-volume, and small-cap stocks (0-100).      Price tier, NVDA-like: $750, 42M avg vol, Large -> max score, Mid-tier stock: $35, 800K vol, Mid, SGHC-like: $8, 180K vol, Small -> very low, $15 exact -> price tier = 20, $14.99 -> price tier = 10, $50 exactly -> price tier = 40 (+2 more)

### Community 1 - "datetime"
Cohesion: 0.12
Nodes (17): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _monday(), datetime, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:   1, get_screener_results must compute NEW/RETAINED/REMOVED correctly by ISO week., Tickers in current week but absent last week must have change_status='NEW'. (+9 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (29): _AV, _make_ib_with_account_values(), When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g, If IBKR returns no TotalCashValue tag at all (e.g. data feed lag),         get_o (+21 more)

### Community 3 - "Re-audit of the 2026-07-29 performance analysis"
Cohesion: 0.11
Nodes (17): Addendum — stop widened to 10% (user approved), Aggression: slot count was tested and 4 is correct, Audit result, Bug 2 — Day 3 verdict compared two stale volume bars (real defect), Consequences, Context, Fixed here, Follow-up (+9 more)

### Community 4 - "positionRules.js"
Cohesion: 0.30
Nodes (13): approxEq(), buildLifecycle(), evaluatePositionRules(), fmtDate(), fmtTime(), LIFECYCLE_TRACK, num(), pctAway() (+5 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.07
Nodes (24): Mirrors stdout to a daily rotating log file without touching print() calls., Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.          Uses the da, TeeLogger, Fires from ai_evaluator.py after all 5-component scores are computed.         Sh, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Distin, Sent when the Prove-It Stop arms an exit.          Phase 1 fires on a breakout t (+16 more)

### Community 6 - "make_ib_mock"
Cohesion: 0.12
Nodes (12): get_position_price(), IBKR-first live price for an OPEN position, with FMP fallback.      Live trades, make_ib_mock(), make_portfolio_item(), Mimics an ib_insync PortfolioItem.     IMPORTANT: uses .averageCost (PortfolioIt, Creates a mock IB instance whose portfolio() always returns the given symbols., place_trailing_stop() places exactly ONE GTC TRAIL order.     No LimitOrder (pro, TestCancelTickerSellOrders (+4 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.18
Nodes (7): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     ot, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the dynamic t, The profit-lock must wait for the full +5% gain threshold. A merely green, Once locked to 1.5%, a dip in profit must not restore a wider trail., TestOneWayOnly, TestProfitLever

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.12
Nodes (22): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, Even with large RS decay (>15 pts from HWM), RS_DECAY is never recommended., hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload. (+14 more)

### Community 9 - "_trigger"
Cohesion: 0.06
Nodes (25): Exception, The Day 7+ discretionary rules stopped market-selling and now hand the exit, TestEnqueueSmartExit, TestQueueResilience, _payloads(), tests/test_trigger_audit.py  Tests for the point-in-time trigger archive and the, PK includes trigger_type, so BREAKOUT and PRE_BREAKOUT coexist., Snapshotted rather than joined: the trigger row may be re-scored on a         la (+17 more)

### Community 10 - "_run"
Cohesion: 0.19
Nodes (19): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD), Intr, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent., Day 3 EOD: price +2% but volume 0.5x avg -> FAIL. (+11 more)

### Community 11 - "main.py"
Cohesion: 0.09
Nodes (35): approve_rotation(), auto_generate_watchlist(), BacktestRequest, check_and_run_weekly_watchlist(), dismiss_rotation(), get_account_balances(), get_benchmark_returns(), get_breakouts() (+27 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.13
Nodes (18): fetch_ibkr_delayed_price(), Fetch the current price for a contract using IBKR delayed market data (type 3)., _make_ib(), _make_ticker(), tests/test_ibkr_delayed_price.py  Unit tests for fetch_ibkr_delayed_price() -- t, reqMarketDataType(1) must be the last call even on success., reqMarketDataType(1) must be called even when reqTickers raises., reqMarketDataType(3) must be called BEFORE reqTickers. (+10 more)

### Community 13 - "package.json"
Cohesion: 0.07
Nodes (28): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+20 more)

### Community 14 - "mock_ib"
Cohesion: 0.29
Nodes (7): mock_ib(), mock_supabase_empty(), fixture, Default IB mock with no open positions., Supabase mock with no data in any table., Auto-mock execution_agent.notifier for every test in the suite., _silence_notifier()

### Community 15 - "ExitDetailPanel.jsx"
Cohesion: 0.16
Nodes (18): failures, EXECUTOR_COLOR, EXECUTOR_ICON, ExitDetailPanel(), formatFact(), money(), pct(), classifyExit() (+10 more)

### Community 16 - "database.py"
Cohesion: 0.12
Nodes (26): _bg_update_fmp_cache(), get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results(), get_setting() (+18 more)

### Community 17 - "_pos"
Cohesion: 0.09
Nodes (11): _pos(), PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the     op, The upper leg must stay reachable however far underwater the position is.      B, The whole point: a deep loser gets the same target as a winner., On DELL, breakeven needed +5.85%; ATR_AUTO needs 3.8%., A bare `INSERT INTO exit_requests (ticker) VALUES (...)` path., Optimistic leg must be looser than the protective one, at any ATR., TestAtrAutoUpperLeg (+3 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.20
Nodes (9): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Follow-up pass: one-off scripts not wired into the main programs, Problem (+1 more)

### Community 19 - "FMPClient"
Cohesion: 0.16
Nodes (8): FMPClient, DataFrame, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Gracefull, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint.

### Community 20 - "flex_query_sync.py"
Cohesion: 0.11
Nodes (25): end, rows, start, ET, check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), main() (+17 more)

### Community 21 - "breakout_bt.py"
Cohesion: 0.16
Nodes (18): daily(), Daily bars ascending by date, or None if the symbol is not in the dataset., daily(), dyn_trail(), find_breakouts(), indicators(), Breakout-population backtest.  Addresses a selection-bias problem: the exit para, Daily bars from the committed benchmark dataset — no network, no rate limit. (+10 more)

### Community 22 - "test_reconcile.py"
Cohesion: 0.08
Nodes (23): test_reconcile.py — Tests for reconcile_with_ibkr() four reconcile cases.  Criti, Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).         The code, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: no absolute stop_loss price is stored.          The `stop_loss` column w, Case 3: In both, share count differs → update Supabase., IBKR has 150 shares, Supabase says 100 → update Supabase to 150., Case 3: IBKR and Supabase both have 100 shares → no share-count write., The IBKR valuation columns are what let the read-only web container render     t (+15 more)

### Community 23 - "IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot"
Cohesion: 0.18
Nodes (11): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Start execution agent, Step 7: Add secret to server .env, Step 8: Update docker-compose.yml (already coded — just needs to be pushed), Step 9: Restart the gateway (+3 more)

### Community 25 - "Technical Triggers"
Cohesion: 0.13
Nodes (15): `BREAKOUT` — the primary signal, Decision log, Forward-return outcomes, Parameters, Position in the pipeline, `PRE_BREAKOUT_RELAXED` — quota fill, `PRE_BREAKOUT` — the coil, Relative strength (+7 more)

### Community 26 - "compute_rs_score"
Cohesion: 0.14
Nodes (11): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, tests/test_score_components.py  Unit tests for the new 5-component scoring funct, Stock +20%, SPY +5% -> excess +15% -> 100, Excess exactly 10% -> 100, Excess 5% -> 50 + 5*5 = 75, Same return as SPY -> 50, Excess -5% -> 50 + (-5)*5 = 25 (+3 more)

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
Nodes (8): Armed exit, Exits, Power Hold, Retired exit variables, Smart OCA managed exit, Staleness and rotation, The Prove-It Stop, Trailing-stop ladder

### Community 31 - "Buy Logic"
Cohesion: 0.17
Nodes (12): Audit trail, Buy Logic, Design principle: fail closed, Order placement and post-fill, Parameter reference, Per-candidate gate stack, Position sizing, Pre-flight: portfolio-level blocks (+4 more)

### Community 32 - "Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py"
Cohesion: 0.29
Nodes (6): Alternatives considered, Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py, Consequences, Context, Decision, Guard

### Community 33 - "Backtest-corrected exit parameters; keep the entry tightening"
Cohesion: 0.14
Nodes (13): Backtest-corrected exit parameters; keep the entry tightening, Consequences, Context, Decision, Fidelity limits (important), Finding 1 — there was no right tail to protect, Finding 2 — ablation: only one of the four exit changes helps, Finding 3 — the early-loss reasoning was simply wrong (+5 more)

### Community 34 - "make_supabase_mock"
Cohesion: 0.12
Nodes (23): make_ibkr_fill(), make_ohlcv_data(), make_position(), make_supabase_mock(), make_trigger(), Factory for a portfolio_positions Supabase row.      hwm_rs_score: RS score on t, Factory for a daily_triggers Supabase row.      final_score defaults to 75 (a no, Factory for an ibkr_fills Supabase row. (+15 more)

### Community 35 - "Phase 1 — Extract the TOTP Base32 Secret from IBKR"
Cohesion: 0.29
Nodes (7): Phase 1 — Extract the TOTP Base32 Secret from IBKR, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings, Step 3: Re-enroll the Software Token (to reveal the secret), Step 4: Reveal the Base32 Secret — CRITICAL STEP, Step 5: Add to Microsoft Authenticator (same session), Step 6: Complete IBKR enrollment

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.09
Nodes (22): 1. Dynamic trailing stop (IBKR-managed), 3. Staleness — day 7+ (feeds Rank & Replace), 4. Rank & Replace — day 7+, Armed Exit — the "smart sale" mechanism, Choosing between the two queue modes, Compact column and ladder, Dashboard: the exit detail panel, Dashboard: the Position Journey and the Risk Rule Ladder (+14 more)

### Community 38 - "CB"
Cohesion: 0.50
Nodes (4): end, rows, start, CB

### Community 39 - "Fundamental Screener"
Cohesion: 0.15
Nodes (12): Filters, Fundamental Screener, Known limitation, Ordering invariant, Output, Parameters, Point-in-time archive, Purpose (+4 more)

### Community 40 - "Slot count, and establishing the backtest's noise floor"
Cohesion: 0.22
Nodes (8): Context, Follow-up, Harness defects found and fixed, Notable observations, not acted on, Re-verification: both major decisions hold under the slot constraint, Slot count, Slot count, and establishing the backtest's noise floor, The noise floor — the most important result here

### Community 41 - "test_sell_logic.py"
Cohesion: 0.10
Nodes (15): test_sell_logic.py -- Tests for monitor_portfolio_intraday() and run_market_open, Even when price is below stop level, Python does NOT call execute_sell., hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., run_market_open_buys() must NOT submit any LimitOrder (profit target).     Only, run_market_open_buys() must place exactly 1 TRAIL sell -- no LMT., Runs monitor_portfolio_intraday() with standard patches.     live_prices: dict o (+7 more)

### Community 42 - "per_symbol"
Cohesion: 0.17
Nodes (12): end, rows, start, end, rows, start, end, rows (+4 more)

### Community 43 - "compute_pre_breakout_quality_score"
Cohesion: 0.15
Nodes (12): check_pre_breakout_coil(), compute_pre_breakout_quality_score(), Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, _coil(), Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65. (+4 more)

### Community 44 - "_run_queue"
Cohesion: 0.26
Nodes (5): _pending(), End-to-end: a request queued tonight must price off tomorrow's settled     price, _run_queue(), TestNextMorningReanchor, TestQueuePending

### Community 45 - "_triggers"
Cohesion: 0.16
Nodes (6): Regression tests for the AI evaluator batching / completeness logic.  Background, Simulates the exact production failure: model drops middle entries., TestBatching, TestPromptCompleteness, TestTradeHistoryLearning, _triggers()

### Community 46 - "Decision: Breakout Quality Floor + Quota Waterfall"
Cohesion: 0.33
Nodes (5): Consequences, Decision, Decision: Breakout Quality Floor + Quota Waterfall, Problem, Rationale

### Community 47 - "Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals"
Cohesion: 0.33
Nodes (5): Decision, Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals, Files changed, Problem, Why these specific numbers

### Community 48 - "trigger_audit.py"
Cohesion: 0.27
Nodes (10): trigger_audit.py  Point-in-time archive of breakout triggers and the buy/skip de, Record one buy/skip verdict against one trigger.      `decision` is BOUGHT or SK, Record the same verdict against many triggers.      Used when the portfolio is a, Chunked, idempotent, non-fatal upsert., Archive `daily_triggers` rows to the append-only `trigger_history`.      MUST be, record_decisions_bulk(), record_trigger_decision(), save_trigger_history() (+2 more)

### Community 49 - "backtester.py"
Cohesion: 0.33
Nodes (9): _cagr(), _ema(), _max_consecutive_losses(), _max_underwater_days(), backend/backtester.py  Runs a historical simulation of the CAN SLIM breakout tra, Exponential moving average (matches pandas ewm default, adjust=False)., Historical simulation of the CAN SLIM breakout strategy.      Position sizing ma, run_backtest() (+1 more)

### Community 51 - "compute_final_score"
Cohesion: 0.13
Nodes (10): compute_final_score(), Weighted blend of 5 components (all 0-100) -> 0-100 final score.        Technica, TestPreBreakoutScoreBoost, NVDA-like scores -> should be around 80, SGHC-like scores -> should be around 40-50, Weighted formula: tech=100, rest=0 -> score = 30, liq=100, rest=0 -> score = 25, Score cannot exceed 100 (+2 more)

### Community 52 - "verify-build.mjs"
Cohesion: 0.33
Nodes (5): DIST_DIR, failures, FEATURE_FINGERPRINTS, SOURCE_GUARDS, SRC_ROOT

### Community 53 - "2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers"
Cohesion: 0.17
Nodes (11): 1. Fail closed on un-vetted triggers (`execution_agent.py`), 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers, 2. Batch the AI calls (`ai_evaluator.py`), 3. Demand completeness in the prompt, 4. Validate and retry, then alert, Consequences, Context, Decision (+3 more)

### Community 54 - "_pos"
Cohesion: 0.09
Nodes (20): is_power_hold_active(), maybe_arm_power_hold(), O'Neil 8-week hold rule.      True while a position is inside its protected wind, Persists the power-hold flag the first time a position qualifies.      Returns T, _client(), _pos(), test_power_hold.py - Tests for the O'Neil 8-week hold rule.  From "How to Make M, PGRST204 = migration not run yet. The rule must still apply in-memory for (+12 more)

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
Cohesion: 0.13
Nodes (22): ai_grade_and_bonus(), build_prompt(), build_trade_history_index(), call_ai_batch(), compute_trade_history_penalty(), evaluate_triggers(), fetch_daily_triggers(), fetch_news_headlines() (+14 more)

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

### Community 70 - "EW"
Cohesion: 0.50
Nodes (4): end, rows, start, EW

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

### Community 76 - "_make_pos"
Cohesion: 0.08
Nodes (25): _make_ib(), _make_pos(), _make_sb(), tests/test_prove_it_stop.py  Tests for the Prove-It Stop — the single loss rule, Unproven positions anchor to entry, and the band widens after day 0., The day-0 band must NOT still apply on day 1.          This is the regression th, Unlike every rule it replaced, Phase 1 has no day window.          The Thesis St, A proven position that reached the arming gain never becomes a real loss. (+17 more)

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
Cohesion: 0.33
Nodes (5): Amending an existing ADR, decisions/, Naming convention, Template, When to add a file

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

### Community 118 - "test_supabase_backup.py"
Cohesion: 0.06
Nodes (57): Path, _coerce_column(), fetch_table(), main(), notify_failure(), DataFrame, supabase_backup.py  Weekly point-in-time export of every Supabase table to flat, Return every row of `table`, paginated and deterministically ordered.      Raise (+49 more)

### Community 119 - "make_history"
Cohesion: 0.07
Nodes (30): _default_config(), make_history(), make_response(), patch_fmp(), date, fixture, test_market_direction.py — Real unit tests for the CANSLIM "M" gate.  Before 202, Both above the buffer with rising SMA-200 → BULL. (+22 more)

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

### Community 145 - "Why"
Cohesion: 0.13
Nodes (14): A mechanical guard against omission, Alternatives rejected, Decision, Export in the runner, not on the box, Failing loudly, Files changed, Full snapshots, not row-level deltas, Hive partitioning, with `table_name` rather than `table` (+6 more)

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

### Community 167 - "Decision: Correct the look-ahead bias in the armed-exit backtest"
Cohesion: 0.20
Nodes (9): Consequences — two shipped claims do not survive, Decision, Decision: Correct the look-ahead bias in the armed-exit backtest, Files changed, Problem, The 0.6% trail was never defensible on noise grounds, The armed exit is unproven, not proven, The thesis stop's headline result loses significance (+1 more)

### Community 168 - "Drop stale and dead columns from `portfolio_positions`"
Cohesion: 0.25
Nodes (7): Consequences, Decision, Deliberately kept, Drop stale and dead columns from `portfolio_positions`, Implementation, Problem, Verification

### Community 169 - "ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm"
Cohesion: 0.18
Nodes (11): ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm, Consequences, Context, Decision, Entry-filter improvements — REJECTED, Evidence, Files, Known limitation (+3 more)

### Community 170 - "CRH"
Cohesion: 0.50
Nodes (4): end, rows, start, CRH

### Community 171 - "Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing"
Cohesion: 0.29
Nodes (7): Consequences, Decision, Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing, Files changed, Method — decision rule fixed before looking at results, Result 1 — no configuration survives, Result 2 — why they disagree: the rule barely does anything

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

### Community 199 - "increment_retention"
Cohesion: 0.43
Nodes (5): increment_retention(), get_rating_text(), Append this run's screener output to the append-only `watchlist_history`.      `, run_screener(), save_watchlist_history()

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

### Community 206 - "TestExitContextSuffix"
Cohesion: 0.08
Nodes (7): _load_exit_context_suffix(), Tests for the exit-context recorded against a reconciled broker exit.  When an I, Mirrors the regexes in frontend/src/lib/exitDetails.js. If the agent's         f, The helper is worthless if the reconcile path stops calling it., Import the helper without importing execution_agent itself.      execution_agent, TestExitContextSuffix, TestReconcileUsesTheHelper

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

### Community 222 - "test_volatility_fit.py"
Cohesion: 0.10
Nodes (26): est_days_to_lock(), scoring.py — Pure scoring functions for the 5-component final_score system.  No, Trading days to reach the +5% profit lock at the average ATR pace.      Returns, Classify a candidate's ATR against the stop ladder it will actually trade., volatility_fit(), telegram_notifier.py — CANSLIM Trading Bot Telegram Notification Module  Fires r, _prompt_source(), parametrize (+18 more)

### Community 223 - "Forward-return backfill for trigger_history (and the prune we did NOT build)"
Cohesion: 0.22
Nodes (8): Consequences, Context, Conventions (the part that is easy to get silently wrong), Decision, Forward-return backfill for trigger_history (and the prune we did NOT build), Guards, Rejected: the 6-month rolling prune, Verification

### Community 224 - "Backups"
Cohesion: 0.10
Nodes (19): Adding a table, Backups, Getting a CSV, Layout, Offsite, Parquet only, Querying with SQL, Restoring (+11 more)

### Community 225 - "Decision"
Cohesion: 0.12
Nodes (15): A separate table, not columns on `portfolio_positions`, Consequences, Context, Decision, DELL — the first request, Drained every cycle, not just at the open, `ocaType=1` (CANCEL_WITH_BLOCK), Placement waits for the tape to settle (+7 more)

### Community 226 - "COHR"
Cohesion: 0.50
Nodes (4): end, rows, start, COHR

### Community 227 - "ELV"
Cohesion: 0.50
Nodes (4): end, rows, start, ELV

### Community 228 - "._run"
Cohesion: 0.29
Nodes (4): Regression guard for the volume-gate inversion.      `daily_triggers.volume_surg, The core inversion: 0.66x is a tight coil, the signal we want., The screener's own < 1.00 gate governs looseness, not this one., TestVolumeGateRespectsTriggerType

### Community 229 - "BreakoutsView.jsx"
Cohesion: 0.20
Nodes (8): BreakoutDetailPanel(), BreakoutTable(), sortByConviction(), ScreenerView(), StockChart(), getValueByPath(), useSortableTable(), volatilityFit()

### Community 230 - "_client"
Cohesion: 0.09
Nodes (12): _client(), _FakeQuery, tests/test_schema_guard.py  Tests for the startup schema assertion.  Context: on, The add_ibkr_position_values.sql migration going unrun silently turned     Inves, A monitoring concern must never become a trading outage., The exact drift found in production must be reported as degraded., TestAdvisoryColumns, TestAdvisoryTables (+4 more)

### Community 231 - "schema_guard.py"
Cohesion: 0.20
Nodes (8): assert_schema_ok(), Verify risk-rule columns exist. Returns False when new buys must be blocked., check_schema(), _probe(), Startup schema assertion — fail LOUD when a risk rule's columns are missing.  WH, True if the table (and column, if given) is queryable., Probe every object a risk rule or archive depends on., SchemaReport

### Community 232 - "2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing"
Cohesion: 0.20
Nodes (9): 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing, Consequences, Context, Decision, Evidence for preferring the close latch, Files, Follow-up, Related change: the migration backfill was itself unsafe (+1 more)

### Community 233 - "Early Loss Kill-switch: tighten to 1% and restrict to the entry day"
Cohesion: 0.18
Nodes (11): 1. The window matters far more than the threshold, 2. Arming beats selling, in every family, 3. Nothing beat the plain percentage rule, Consequences, Context, Decision, Early Loss Kill-switch: tighten to 1% and restrict to the entry day, Findings (+3 more)

### Community 234 - "DashboardView.jsx"
Cohesion: 0.19
Nodes (16): activeProfitLockTier(), daysHeld(), ExitConditionsPanel(), formatDate(), _getHolidays(), LifecycleCell(), _nyseHolidays(), PositionJourney() (+8 more)

### Community 235 - "App.jsx"
Cohesion: 0.21
Nodes (6): App(), BacktesterView(), BreakoutsView(), BENCH_COLORS, ReturnsView(), SettingsView()

### Community 236 - "test_oca_managed_exit.py"
Cohesion: 0.23
Nodes (7): _placed(), _queue_sb(), tests/test_oca_managed_exit.py — Smart OCA Managed Exit.  The dangerous property, A buy_date exactly `n` trading days before today, in New York.      Must be comp, Supabase mock that keeps exit_requests and portfolio_positions apart., TestQueueBackstops, _trading_days_ago()

### Community 237 - "request_exit.py"
Cohesion: 0.48
Nodes (6): _client(), cmd_cancel(), cmd_list(), main(), Best-effort reference price for the confirmation preview only., _ref_price()

### Community 238 - "Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those, Guard, Left alone deliberately, Problem

### Community 239 - "patch"
Cohesion: 0.19
Nodes (11): _make_supabase_mock(), patch, save_screener_results must replace only the current week's rows., The delete call must use gte(week_start) and lt(week_end)., insert() must be called after delete() — ordering matters., After insert, rows older than 56 days must be pruned., The current-week delete must NOT use neq() which would wipe all rows., Empty screener results must not insert or delete anything. (+3 more)

### Community 240 - "Decision"
Cohesion: 0.22
Nodes (8): 1. Hard volume surge gate in `execution_agent.py`, 2. PRE_BREAKOUT 52W pivot distance gate in `execution_agent.py`, 3. AI prompt penalty rules in `ai_evaluator.py`, 4. D-veto threshold raised from 30 → 50, ADR: Buy Gate Hardening — Volume Surge Floor, PRE_BREAKOUT Pivot Distance, AI Penalty Rules, Consequences, Context, Decision

### Community 241 - "Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed"
Cohesion: 0.25
Nodes (7): Consequences, Context, Dashboard consistency, Decision, Evidence, Follow-ups, Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed

### Community 242 - "test_exit_timestamp.py"
Cohesion: 0.09
Nodes (11): Tests that a closed position records WHEN it closed, to the precision available., The behaviour all of the above exists to produce., A precise timestamp is worthless if it stops reaching the row., Tier 1 reads `ibkr_fills`, written from `execution.time.isoformat()`., Tier 2 reads ib_insync executions, whose `.time` is tz-aware UTC., Tier 3 is the one path that must NOT gain a time.      Flex `dateTime` has no ti, TestFlexStaysDateOnlyOnPurpose, TestHoldingPeriodIsNoLongerNegative (+3 more)

### Community 244 - "Decision: Scope the volume surge gate to confirmed breakouts only"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Scope the volume surge gate to confirmed breakouts only, Guard, Observed damage — 2026-08-19, Problem

### Community 245 - "force_buy.py"
Cohesion: 0.20
Nodes (14): place_oca_exit(), place_trailing_stop(), Places the OCA exit pair on an open position:          upper leg  LMT  SELL @ li, Factory for IBKR TRAIL order type.     `ib_insync` 0.9.x does not export a Trail, Places a GTC Trailing Stop for an open stock position.     Trails stop_loss_pct%, TrailingStopOrder(), get_ibkr_price(), main() (+6 more)

### Community 246 - "Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price, Guard, Polling, not LISTEN/NOTIFY, Problem

### Community 249 - "The Prove-It Stop — one loss rule replaces five"
Cohesion: 0.13
Nodes (15): Addendum, 2026-09-04 — Phase 1 enforcement side, measured, Consequences, Context, Decision, Frontend, Kept, with a changed job, Mechanism, Phase 1 — unproven. Anchor to ENTRY. (+7 more)

### Community 251 - "HWM profit-lock after the first leg"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, Follow-up, HWM profit-lock after the first leg

### Community 252 - "exit_rule_replay.py"
Cohesion: 0.09
Nodes (39): Any, _correct_split(), day0_configs(), _env(), ExitConfig, fetch_5min(), fetch_entry_atr_pct(), grid_configs() (+31 more)

### Community 253 - "2026-09-04 — Prove-It Stop consolidation"
Cohesion: 0.17
Nodes (11): 1. Intraday Loss Minimiser (ILM), 2026-09-04 — Prove-It Stop consolidation, 2. Trailing-stop time lever (`TRAIL_TIME_TIERS`), 3. Early Loss Kill-switch, 4. Early Dollar Stop, 5. Thesis Stop, 6. EMA-21 Exit, 7. Plateau (Stale) Exit (+3 more)

### Community 254 - "Early Dollar Stop becomes slot-derived, not a flat dollar amount"
Cohesion: 0.18
Nodes (11): 1. The measured problem, 2. The design flaw the parameter was hiding, 3. Why the rule is kept rather than removed, Consequences, Context, Decision, Early Dollar Stop becomes slot-derived, not a flat dollar amount, Fail-safe (+3 more)

### Community 255 - "rotate_positions.py"
Cohesion: 0.14
Nodes (21): config.py — single source of truth for cross-module trading parameters.  Every v, _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell(), IB (+13 more)

### Community 256 - "monitor_portfolio_intraday"
Cohesion: 0.10
Nodes (41): build_ibkr_price_map(), cancel_ticker_sell_orders(), execute_sell(), _fetch_current_rs(), get_available_cash(), _get_entry_rs(), get_ibkr_account(), get_margin_loan() (+33 more)

### Community 257 - "atr_rank_bt.py"
Cohesion: 0.10
Nodes (36): apply_ranking(), atr_pct_at(), build_with_atr(), describe(), entry_stop_for(), main(), rank_atr_band(), rank_atr_boost() (+28 more)

### Community 258 - "_with_equity"
Cohesion: 0.50
Nodes (3): Give a mock IB an account with readable NetLiquidation.      No live loss rule d, TestLadderSuspension, _with_equity()

### Community 259 - "Retune HWM profit-lock arm from +6% to +5%"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Docs sync, Evidence, Retune HWM profit-lock arm from +6% to +5%

### Community 260 - "Closed-loop learning from realised outcomes"
Cohesion: 0.33
Nodes (5): Closed-loop learning from realised outcomes, Consequences, Context, Decision, Docs sync

### Community 261 - "_make_df"
Cohesion: 0.20
Nodes (8): _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None., Recent 3d avg vol 1.1x 50d avg -> sellers still active -> None., Strictly descending then tiny uptick: must compare vs prior row.         Use all, TestPreBreakoutCoilFail

### Community 262 - "COHU"
Cohesion: 0.50
Nodes (4): end, rows, start, COHU

### Community 263 - "Source dashboard position values from IBKR, not FMP"
Cohesion: 0.22
Nodes (8): After hours, Agent side, Consequences, Context, Decision, Follow-up, Source dashboard position values from IBKR, not FMP, Web side

### Community 264 - "CRM"
Cohesion: 0.50
Nodes (4): end, rows, start, CRM

### Community 265 - "Exit detail panel, and the removal of fabricated exit reasons"
Cohesion: 0.22
Nodes (8): 1. The labels were partly invented, 2. Half the exits recorded no numbers at all, Consequences, Context, Decision, Exit detail panel, and the removal of fabricated exit reasons, Guards, Not done

### Community 266 - "managed_exit.py"
Cohesion: 0.26
Nodes (15): get_live_price(), Fetch current price of a ticker from FMP., archive(), cancel_sells(), current_price(), main(), market_exit(), _notify() (+7 more)

### Community 267 - "AMTM"
Cohesion: 0.50
Nodes (4): end, rows, start, AMTM

### Community 268 - "CAH"
Cohesion: 0.50
Nodes (4): end, rows, start, CAH

### Community 269 - "Client"
Cohesion: 0.17
Nodes (12): arm_exit(), _close_exit_request(), enqueue_smart_exit(), get_oca_managed_tickers(), Client, datetime, Arms a Day 0-6 loss-cutting exit instead of selling immediately at the     trigg, Route an automated sell rule through the Smart OCA Exit queue.      Rather than (+4 more)

### Community 270 - "paired_block_bootstrap"
Cohesion: 0.27
Nodes (12): cagr_from(), paired_block_bootstrap(), Corrected bootstrap for config comparisons.  THE BUG (boot.py):     diffs = cagr, Draw circular blocks with Geometric(1/mean_len) lengths until >= ndays., Return (median diff, 5th, 95th, P(a>b)) for CAGR_a - CAGR_b., Reproduces the ORIGINAL (buggy) method, for comparison only., RNG, single_ci() (+4 more)

### Community 271 - "Price live-position exit logic from IBKR, not FMP"
Cohesion: 0.22
Nodes (8): Call sites moved from FMP to IBKR-first, Consequences, Context, Decision, Deliberately left on FMP, New helpers (`execution_agent.py`), Price live-position exit logic from IBKR, not FMP, Tests

### Community 272 - "rank_policy_bt.py"
Cohesion: 0.27
Nodes (12): build(), find_triggers(), _indicators(), per_type(), _rank_key(), Counterfactual replay: trigger-RANKING policy A (score-first) vs B (confirmed-fi, Point-in-time SPY 12-week (60 trading day) return, keyed by date., Replay BOTH screener detectors bar-by-bar, using production scoring.      Return (+4 more)

### Community 273 - "thesis_bt.py"
Cohesion: 0.17
Nodes (20): main(), Does an INTRADAY POKE above entry deserve to disarm the Thesis Stop?  THE QUESTI, build(), Collect all breakout signals and per-symbol bar data keyed by date., armed_fill(), atr_pct_series(), load(), Thesis stop + cooling-off backtest.  The thesis stop asks a different question f (+12 more)

### Community 274 - "AI evaluator: replace the velocity ladder with volatility fit"
Cohesion: 0.17
Nodes (11): AI evaluator: replace the velocity ladder with volatility fit, Consequences, Context, Correction: the slot-width framing above is too strong, Decision, Is CAN SLIM's 20-25% target simply wrong?, Measurement, One result that IS clean (+3 more)

### Community 275 - "bardata.py"
Cohesion: 0.36
Nodes (7): coverage(), manifest(), Read-only loader for the committed benchmark price dataset.  The dataset lives i, Sorted union of dates across the given symbols (default: all)., symbols(), _table(), trading_days()

### Community 276 - "technical_screener.py"
Cohesion: 0.23
Nodes (12): check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync(), get_supabase_client(), get_watchlist_from_supabase(), Client (+4 more)

### Community 277 - "2. The Prove-It Stop — always live"
Cohesion: 0.25
Nodes (8): 2. The Prove-It Stop — always live, Evidence, Fails safe, How it acts, Phase 1 — unproven. Anchored to ENTRY., Phase 2 — proven. Anchored to the PEAK., What it replaced, and why, Why Phase 1 is enforced by the agent, not by the broker

### Community 278 - "Smart OCA Managed Exit"
Cohesion: 0.25
Nodes (8): Next-morning re-anchoring, Placement timing, Request modes, Smart OCA Managed Exit, The automated ladder is suspended while a request is `PLACED`, Usage, Why `ATR_AUTO` is the default, Why the lower leg trails instead of sitting still

### Community 279 - "TestReconcileCase4"
Cohesion: 0.25
Nodes (5): Case 4: Cash balance sync from IBKR to Supabase account_balances., Large change in cash → upsert to account_balances called., New logic: write daily snapshots for cash, positions_value, total_value., A cash jump > $500 inserts into cash_flows., TestReconcileCase4

### Community 280 - "TestPreBreakoutCoilPass"
Cohesion: 0.29
Nodes (4): 5% below high, vol contracting, 3/3 closes up -> PRE_BREAKOUT., 2 of 3 closes rising meets PRE_BREAKOUT_UPTREND_MIN=2., Within 1% of pivot -> quality_score >= 70 (vol 0.6x avg -> contraction pts ~16)., TestPreBreakoutCoilPass

### Community 281 - "execution_agent.py"
Cohesion: 0.05
Nodes (46): _build_failed_params_snapshot(), calculate_ema(), calculate_sma(), check_volume_distribution(), compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), _exit_context_suffix() (+38 more)

### Community 283 - "_position_atr_pct"
Cohesion: 0.33
Nodes (6): _position_atr_pct(), The ATR percent both OCA legs are sized from, with its provenance.      Note thi, Resolves the OCA lower leg's trailing percent.      'ATR_AUTO' scales the trail, Resolves the OCA upper leg's limit price from stored *intent*.      Requests are, resolve_oca_limit_price(), resolve_oca_trail_pct()

### Community 284 - "Evidence"
Cohesion: 0.50
Nodes (4): Evidence, Phase 1 and Phase 2 are complementary, not additive, Why Phase 1 WIDENS after day 0 rather than tightening, Why the Phase 2 floor sits 1% BELOW entry, not at it

### Community 285 - "test_reconcile_detects_short_positions"
Cohesion: 0.50
Nodes (3): patch, Test that reconcile_with_ibkr detects short positions and sends alert., test_reconcile_detects_short_positions()

### Community 286 - ".test_case1_removes_from_portfolio_and_logs_trade"
Cohesion: 0.33
Nodes (4): Case 1: In Supabase, NOT in IBKR → closed by IBKR (trailing stop / limit / TWS)., Position in Supabase but not IBKR → archived to trade_history.         IBKR port, Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill., TestReconcileCase1

### Community 287 - "._eod_monitor"
Cohesion: 0.32
Nodes (5): EOD plateau rotation: at 3:45-4pm, if portfolio is full AND fresh breakout     t, Helper: run monitor in EOD window., Days 3-6 position with decay is NOT swapped if no triggers exist., Days 3-6 position with decay is NOT swapped if portfolio has open slots (not ful, TestPlateauRotation

### Community 288 - "ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5"
Cohesion: 0.40
Nodes (5): ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5, Consequences, Context, Decision, Simulation

## Knowledge Gaps
- **900 isolated node(s):** `bar_interval`, `bytes`, `dataset`, `date_max`, `date_min` (+895 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `per_symbol` connect `per_symbol` to `flex_query_sync.py`, `CB`, `BIRK`, `CMI`, `MANIFEST.json`, `EW`, `AAPL`, `ABBV`, `ABNB`, `ABT`, `ACN`, `ADBE`, `ADI`, `ADP`, `ADSK`, `AEIS`, `AEP`, `AFL`, `AJG`, `ALAB`, `ALL`, `AMAT`, `AMD`, `AME`, `AMGN`, `AMKR`, `AMT`, `AON`, `APD`, `APH`, `APO`, `APP`, `ARM`, `ARW`, `AS`, `AVGO`, `AXP`, `BA`, `BABA`, `BAC`, `BAM`, `BDX`, `BE`, `BKNG`, `BKR`, `BMY`, `BN`, `BNY`, `AMZN`, `BX`, `CARR`, `CAT`, `CCEP`, `CDNA`, `CDNS`, `CEG`, `CELH`, `CF`, `CI`, `CIEN`, `CL`, `CMC`, `CMCL`, `CMCSA`, `CME`, `CMG`, `COCO`, `COF`, `BSX`, `CRH`, `CRWD`, `CSCO`, `CSX`, `CTAS`, `CTVA`, `CVNA`, `CVS`, `CVX`, `CXW`, `D`, `DAL`, `DASH`, `DDOG`, `DE`, `DELL`, `DHR`, `DIOD`, `DIS`, `DLR`, `DUK`, `DVN`, `DXCM`, `DY`, `EA`, `EBAY`, `ECL`, `ECO`, `EME`, `EMR`, `EOG`, `EPD`, `ETN`, `ETR`, `EXC`, `F`, `FANG`, `FAST`, `FCX`, `FERG`, `FITB`, `FLYW`, `COST`, `COP`, `ANET`, `COR`, `COHR`, `ELV`, `COHU`, `CRM`, `AMTM`, `CAH`?**
  _High betweenness centrality (0.184) - this node is a cross-community bridge._
- **Why does `ET` connect `flex_query_sync.py` to `per_symbol`?**
  _High betweenness centrality (0.159) - this node is a cross-community bridge._
- **Why does `fetch_trade_confirms_for_ticker()` connect `execution_agent.py` to `monitor_portfolio_intraday`, `flex_query_sync.py`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **What connects `bar_interval`, `bytes`, `dataset` to the rest of the system?**
  _900 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.12666666666666668 - nodes in this community are weakly interconnected._
- **Should `datetime` be split into smaller, more focused modules?**
  _Cohesion score 0.12169312169312169 - nodes in this community are weakly interconnected._
- **Should `_AV` be split into smaller, more focused modules?**
  _Cohesion score 0.0730804810360777 - nodes in this community are weakly interconnected._