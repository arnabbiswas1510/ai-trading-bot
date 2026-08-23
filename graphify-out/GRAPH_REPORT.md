# Graph Report - ai-trading-bot  (2026-08-22)

## Corpus Check
- 180 files · ~264,936 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2823 nodes · 4403 edges · 265 communities (250 shown, 15 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 38 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `434a63e2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- compute_liquidity_score
- patch
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
- _pos
- Decision: Repository-Wide Dead Code Cleanup
- FMPClient
- flex_query_sync.py
- breakout_bt.py
- _reconcile
- IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot
- Technical Triggers
- technical_screener.py
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
- _coil
- Commit a benchmark price dataset instead of re-fetching from FMP
- Committed benchmark price dataset
- _sb
- 2026-08-14 — Surface every risk rule's live state in the dashboard
- _make_ib
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
- _get_week_start
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
- rank_policy_bt.py
- Forward-return backfill for trigger_history (and the prune we did NOT build)
- Backups
- Decision
- COHR
- ELV
- ._run
- managed_exit.py
- _client
- schema_guard.py
- 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing
- Early Loss Kill-switch: tighten to 1% and restrict to the entry day
- CB
- TeeLogger
- test_oca_managed_exit.py
- request_exit.py
- Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those
- compute_pre_breakout_quality_score
- Decision
- Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed
- EW
- TestQueueMarketMode
- Decision: Scope the volume surge gate to confirmed breakouts only
- trading_days_between
- Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price
- TestManagedTickers
- TestPlaceOca
- Database schema
- TestSmartExitRuleScoping
- HWM profit-lock after the first leg
- exit_rule_replay.py
- C
- ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5
- ET
- monitor_portfolio_intraday
- check_and_run_weekly_watchlist
- _with_equity
- Retune HWM profit-lock arm from +6% to +5%
- Closed-loop learning from realised outcomes
- FDX
- COHU
- test_reconcile_detects_short_positions
- CRM

## God Nodes (most connected - your core abstractions)
1. `per_symbol` - 124 edges
2. `make_ib_mock()` - 72 edges
3. `make_supabase_mock()` - 66 edges
4. `_pos()` - 50 edges
5. `make_position()` - 48 edges
6. `monitor_portfolio_intraday()` - 30 edges
7. `make_trigger()` - 30 edges
8. `FMPClient` - 24 edges
9. `_run()` - 24 edges
10. `TelegramNotifier` - 23 edges

## Surprising Connections (you probably didn't know these)
- `make_supabase_mock()` --indirect_call--> `_table()`  [INFERRED]
  tests/conftest.py → research/bardata.py
- `_queue_sb()` --indirect_call--> `_table()`  [INFERRED]
  tests/test_oca_managed_exit.py → research/bardata.py
- `make_client()` --indirect_call--> `_table()`  [INFERRED]
  tests/test_supabase_backup.py → research/bardata.py
- `main()` --calls--> `compute_final_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py
- `main()` --calls--> `compute_liquidity_score()`  [EXTRACTED]
  ai_evaluator.py → scoring.py

## Import Cycles
- None detected.

## Communities (265 total, 15 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.11
Nodes (12): compute_liquidity_score(), scoring.py — Pure scoring functions for the 5-component final_score system. No…, Penalises low-price, low-volume, and small-cap stocks (0-100). Price tier (0-40…, tests/test_score_components.py Unit tests for the new 5-component scoring…, NVDA-like: $750, 42M avg vol, Large -> max score, Mid-tier stock: $35, 800K vol, Mid, SGHC-like: $8, 180K vol, Small -> very low, $15 exact -> price tier = 20 (+4 more)

### Community 1 - "patch"
Cohesion: 0.09
Nodes (23): _make_supabase_mock(), _monday(), datetime, patch, Tests for watchlist weekly-snapshot logic. Guards the following invariants: 1.…, save_screener_results must replace only the current week's rows., The delete call must use gte(week_start) and lt(week_end)., insert() must be called after delete() — ordering matters. (+15 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (30): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer. Covers two…, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account. If…, When TotalCashValue < 0, a margin loan is active. get_own_cash must return 0.0…, get_margin_loan() must return the absolute value of a negative TotalCashValue —…, Stress case: large margin loan (like the TRV incident ~$35K borrowed).… (+22 more)

### Community 3 - "Re-audit of the 2026-07-29 performance analysis"
Cohesion: 0.11
Nodes (17): Addendum — stop widened to 10% (user approved), Aggression: slot count was tested and 4 is correct, Audit result, Bug 2 — Day 3 verdict compared two stale volume bars (real defect), Consequences, Context, Fixed here, Follow-up (+9 more)

### Community 4 - "DashboardView.jsx"
Cohesion: 0.06
Nodes (38): App(), BacktesterView(), BreakoutsView(), BreakoutTable(), sortByConviction(), activeProfitLockTier(), daysHeld(), ExitConditionsPanel() (+30 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.09
Nodes (21): Fires from ai_evaluator.py after all 5-component scores are computed. Shows…, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt. Distinct from…, Sent when the Thesis Stop arms an exit. Fires only for breakouts that have…, Sent at EOD of Day 3 when a position fails the breakout verdict. Activates the…, Fires after a successful IBKR market sell order is filled and logged., Fires when reconcile_with_ibkr() detects a position closed manually in TWS. (+13 more)

### Community 6 - "make_supabase_mock"
Cohesion: 0.08
Nodes (27): make_ibkr_fill(), make_ohlcv_data(), make_portfolio_item(), make_supabase_mock(), mock_ib(), mock_supabase_empty(), fixture, Factory for an ibkr_fills Supabase row. (+19 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.09
Nodes (16): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,…, parametrize, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the dynamic…, Once locked to 1.5%, a dip in profit must not restore a wider trail., The profit-lock must wait for the full +5% gain threshold. A merely green…, Time held is not a sell signal - the time lever must not fire., Regression: the time lever must not penalize a flat position just because time… (+8 more)

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.10
Nodes (26): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation…, Even with large RS decay (>15 pts from HWM), RS_DECAY is never recommended., hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance. The…, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column…, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload. (+18 more)

### Community 9 - "_trigger"
Cohesion: 0.06
Nodes (25): Exception, The Day 7+ discretionary rules stopped market-selling and now hand the exit to…, TestEnqueueSmartExit, TestQueueResilience, _payloads(), tests/test_trigger_audit.py Tests for the point-in-time trigger archive and the…, PK includes trigger_type, so BREAKOUT and PRE_BREAKOUT coexist., Snapshotted rather than joined: the trigger row may be re-scored on a later… (+17 more)

### Community 10 - "_run"
Cohesion: 0.09
Nodes (36): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), fixture, tests/test_breakout_verdict.py Tests for the Breakout Verdict (Day 3 EOD),…, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent. (+28 more)

### Community 11 - "main.py"
Cohesion: 0.11
Nodes (29): approve_rotation(), auto_generate_watchlist(), BacktestRequest, dismiss_rotation(), get_account_balances(), get_benchmark_returns(), get_breakouts(), get_cash_flows() (+21 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.10
Nodes (25): fetch_ibkr_delayed_price(), Fetch the current price for a contract using IBKR delayed market data (type 3).…, get_ibkr_price(), main(), _place_buy(), IB, force_buy.py — One-off manual buy trigger (bypasses 9:30 AM time gate). Run…, Fetch price via IBKR delayed market data (same as execution_agent buy path).… (+17 more)

### Community 13 - "Frontend Dependencies"
Cohesion: 0.07
Nodes (27): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+19 more)

### Community 14 - "make_ib_mock"
Cohesion: 0.14
Nodes (9): make_ib_mock(), Creates a mock IB instance whose portfolio() always returns the given symbols.…, place_trailing_stop() places exactly ONE GTC TRAIL order. No LimitOrder (profit…, TestCancelTickerSellOrders, TestPlaceTrailingStop, Case 1: In Supabase, NOT in IBKR → closed by IBKR (trailing stop / limit / TWS)., Position in Supabase but not IBKR → archived to trade_history. IBKR portfolio…, Case 1 fallback: uses FMP live price when reqExecutions() has no SLD fill. (+1 more)

### Community 15 - "rotate_positions.py"
Cohesion: 0.14
Nodes (21): config.py — single source of truth for cross-module trading parameters. Every…, _cancel_existing_sells(), _get_portfolio(), main(), _notify(), _pick_from_menu(), _place_sell(), IB (+13 more)

### Community 16 - "database.py"
Cohesion: 0.18
Nodes (17): get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results(), get_setting(), get_supabase_client() (+9 more)

### Community 17 - "_pos"
Cohesion: 0.09
Nodes (11): _pos(), PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the open…, The upper leg must stay reachable however far underwater the position is.…, The whole point: a deep loser gets the same target as a winner., On DELL, breakeven needed +5.85%; ATR_AUTO needs 3.8%., A bare `INSERT INTO exit_requests (ticker) VALUES (...)` path., Optimistic leg must be looser than the protective one, at any ATR., TestAtrAutoUpperLeg (+3 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.20
Nodes (9): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Follow-up pass: one-off scripts not wired into the main programs, Problem (+1 more)

### Community 19 - "FMPClient"
Cohesion: 0.11
Nodes (17): _bg_update_fmp_cache(), FMPClient, DataFrame, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage. Gracefully falls back to a neutral…, Query stable stock-screener to find active US growth equities. Gracefully falls…, Fetch current price, moving averages, volume, 52w range and shares outstanding…, Fetch historical daily prices and format as pandas DataFrame using stable EOD… (+9 more)

### Community 20 - "flex_query_sync.py"
Cohesion: 0.13
Nodes (21): check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), main(), _parse_cash_transactions(), _parse_trade_confirms(), Client, flex_query_sync.py — IBKR Flex Query Cash Flow Sync Fetches cash deposits and… (+13 more)

### Community 21 - "breakout_bt.py"
Cohesion: 0.07
Nodes (57): coverage(), daily(), manifest(), Read-only loader for the committed benchmark price dataset. The dataset lives…, Daily bars ascending by date, or None if the symbol is not in the dataset.…, Sorted union of dates across the given symbols (default: all)., symbols(), _table() (+49 more)

### Community 22 - "_reconcile"
Cohesion: 0.11
Nodes (15): Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost). The code must…, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: no absolute stop_loss price is stored. The `stop_loss` column was a…, Case 3: In both, share count differs → update Supabase., IBKR has 150 shares, Supabase says 100 → update Supabase to 150., Case 3: IBKR and Supabase both have 100 shares → no update., Critical: reconcile_with_ibkr() must use ib.portfolio() everywhere.…, The reconcile function must ONLY call ib.portfolio(), never ib.positions(). (+7 more)

### Community 23 - "IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot"
Cohesion: 0.18
Nodes (11): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Start execution agent, Step 7: Add secret to server .env, Step 8: Update docker-compose.yml (already coded — just needs to be pushed), Step 9: Restart the gateway (+3 more)

### Community 25 - "Technical Triggers"
Cohesion: 0.15
Nodes (13): `BREAKOUT` — the primary signal, Decision log, Forward-return outcomes, Parameters, Position in the pipeline, `PRE_BREAKOUT_RELAXED` — quota fill, `PRE_BREAKOUT` — the coil, Relative strength (+5 more)

### Community 26 - "technical_screener.py"
Cohesion: 0.09
Nodes (24): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks. Excess…, check_pre_breakout_coil(), check_technical_breakout(), _compute_failure_penalty(), compute_quality_score(), fetch_spy_return_12w(), fetch_with_retry_sync() (+16 more)

### Community 27 - "FakeQuery"
Cohesion: 0.15
Nodes (7): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, patch, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "Configuration Reference"
Cohesion: 0.18
Nodes (11): AI evaluator, Buy gating, Changing parameters safely, Configuration Reference, Credentials, Deploying the dashboard, Fundamental screener, Infrastructure (+3 more)

### Community 29 - "_resolve"
Cohesion: 0.18
Nodes (9): parametrize, Guards the single-source-of-truth invariant for MAX_POSITIONS. ADR 2026-08-04…, A module that re-reads the env itself can drift on its default. Root modules…, Importing config.py is useless if the image does not contain it., Return each module's view of the shared constants under a given env value., Changing slot count must need a .env edit only — never a code change., _resolve(), TestNoLocalRedeclaration (+1 more)

### Community 30 - "Exits"
Cohesion: 0.22
Nodes (9): Armed exit, Early loss and the superseded minimiser, Exits, Moving-average exit, Plateau and rotation, Power Hold, Smart OCA managed exit, Thesis Stop (+1 more)

### Community 31 - "Buy Logic"
Cohesion: 0.17
Nodes (12): Audit trail, Buy Logic, Design principle: fail closed, Order placement and post-fill, Parameter reference, Per-candidate gate stack, Position sizing, Pre-flight: portfolio-level blocks (+4 more)

### Community 32 - "Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py"
Cohesion: 0.29
Nodes (6): Alternatives considered, Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py, Consequences, Context, Decision, Guard

### Community 33 - "Backtest-corrected exit parameters; keep the entry tightening"
Cohesion: 0.14
Nodes (13): Backtest-corrected exit parameters; keep the entry tightening, Consequences, Context, Decision, Fidelity limits (important), Finding 1 — there was no right tail to protect, Finding 2 — ablation: only one of the four exit changes helps, Finding 3 — the early-loss reasoning was simply wrong (+5 more)

### Community 34 - "make_position"
Cohesion: 0.16
Nodes (15): make_position(), make_trigger(), Factory for a daily_triggers Supabase row. final_score defaults to 75 (a…, Factory for a portfolio_positions Supabase row. hwm_rs_score: RS score on the…, Regression: ai_evaluator.py silently drops tickers from its batch ("lost in the…, adjusted_score (post-penalty) remains the primary gate input., Runs run_market_open_buys() with standard patches applied. Returns the mock_ib…, Gate 1: MAX_POSITIONS stock positions → portfolio full → no order placed. (+7 more)

### Community 35 - "Phase 1 — Extract the TOTP Base32 Secret from IBKR"
Cohesion: 0.29
Nodes (7): Phase 1 — Extract the TOTP Base32 Secret from IBKR, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings, Step 3: Re-enroll the Software Token (to reveal the secret), Step 4: Reveal the Base32 Secret — CRITICAL STEP, Step 5: Add to Microsoft Authenticator (same session), Step 6: Complete IBKR enrollment

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.06
Nodes (33): 1. Dynamic trailing stop (IBKR-managed), 2. Early Loss Kill-switch — entry day only, 2b. Early Dollar Stop — days 0–5, 3. Thesis Stop — days 2–5, 4. EMA-21 support breach — day 7+, 5. Plateau exit — day 7+, 6. Rank & Replace — day 7+, Armed Exit — the "smart sale" mechanism (+25 more)

### Community 38 - "execution_agent.py"
Cohesion: 0.05
Nodes (49): calculate_ema(), calculate_sma(), check_volume_distribution(), compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), early_dollar_stop_threshold(), fetch_held_position_sentiment() (+41 more)

### Community 39 - "Fundamental Screener"
Cohesion: 0.15
Nodes (12): Filters, Fundamental Screener, Known limitation, Ordering invariant, Output, Parameters, Point-in-time archive, Purpose (+4 more)

### Community 40 - "Slot count, and establishing the backtest's noise floor"
Cohesion: 0.22
Nodes (8): Context, Follow-up, Harness defects found and fixed, Notable observations, not acted on, Re-verification: both major decisions hold under the slot constraint, Slot count, Slot count, and establishing the backtest's noise floor, The noise floor — the most important result here

### Community 41 - "Self-Healing Order Tests"
Cohesion: 0.24
Nodes (7): Even when price is below stop level, Python does NOT call execute_sell. IBKR…, Runs monitor_portfolio_intraday() with standard patches. live_prices: dict of…, If no open SELL orders exist for a position, monitor must re-place the trailing…, No open SELL orders -> place_trailing_stop called for self-healing. Use…, Trailing stop already in IBKR -> no self-healing. Use price=buy_price (0% gain)…, _run_monitor(), TestSelfHealingTrailingStop

### Community 42 - "per_symbol"
Cohesion: 0.17
Nodes (12): end, rows, start, end, rows, start, end, rows (+4 more)

### Community 43 - "patch"
Cohesion: 0.09
Nodes (16): patch, hwm_date (date of last intraday high) is the only HWM data Python tracks. IBKR…, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., Price below threshold near market close -> execute_sell called., Price below MA but within buffer -> no exit., Outside 3:45-4:00 PM and EOD_ONLY enabled -> no exit., FMP historical fetch returns empty -> no exit and no crash. (+8 more)

### Community 44 - "_run_queue"
Cohesion: 0.26
Nodes (5): _pending(), End-to-end: a request queued tonight must price off tomorrow's settled price,…, _run_queue(), TestNextMorningReanchor, TestQueuePending

### Community 45 - "_triggers"
Cohesion: 0.16
Nodes (6): Regression tests for the AI evaluator batching / completeness logic.…, Simulates the exact production failure: model drops middle entries., TestBatching, TestPromptCompleteness, TestTradeHistoryLearning, _triggers()

### Community 46 - "Decision: Breakout Quality Floor + Quota Waterfall"
Cohesion: 0.33
Nodes (5): Consequences, Decision, Decision: Breakout Quality Floor + Quota Waterfall, Problem, Rationale

### Community 47 - "Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals"
Cohesion: 0.33
Nodes (5): Decision, Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals, Files changed, Problem, Why these specific numbers

### Community 48 - "trigger_audit.py"
Cohesion: 0.27
Nodes (10): trigger_audit.py Point-in-time archive of breakout triggers and the buy/skip…, Record one buy/skip verdict against one trigger. `decision` is BOUGHT or…, Record the same verdict against many triggers. Used when the portfolio is…, Chunked, idempotent, non-fatal upsert., Archive `daily_triggers` rows to the append-only `trigger_history`. MUST be…, record_decisions_bulk(), record_trigger_decision(), save_trigger_history() (+2 more)

### Community 49 - "backtester.py"
Cohesion: 0.31
Nodes (9): _cagr(), _ema(), _max_consecutive_losses(), _max_underwater_days(), backend/backtester.py Runs a historical simulation of the CAN SLIM breakout…, Exponential moving average (matches pandas ewm default, adjust=False)., Historical simulation of the CAN SLIM breakout strategy. Position sizing…, run_backtest() (+1 more)

### Community 51 - "compute_final_score"
Cohesion: 0.13
Nodes (10): compute_final_score(), Weighted blend of 5 components (all 0-100) -> 0-100 final score. Technical 30%…, TestPreBreakoutScoreBoost, NVDA-like scores -> should be around 80, SGHC-like scores -> should be around 40-50, Weighted formula: tech=100, rest=0 -> score = 30, liq=100, rest=0 -> score = 25, Score cannot exceed 100 (+2 more)

### Community 52 - "verify-build.mjs"
Cohesion: 0.33
Nodes (5): DIST_DIR, failures, FEATURE_FINGERPRINTS, SOURCE_GUARDS, SRC_ROOT

### Community 53 - "2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers"
Cohesion: 0.17
Nodes (11): 1. Fail closed on un-vetted triggers (`execution_agent.py`), 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers, 2. Batch the AI calls (`ai_evaluator.py`), 3. Demand completeness in the prompt, 4. Validate and retry, then alert, Consequences, Context, Decision (+3 more)

### Community 54 - "_pos"
Cohesion: 0.09
Nodes (18): is_power_hold_active(), O'Neil 8-week hold rule. True while a position is inside its protected window:…, _client(), _pos(), test_power_hold.py - Tests for the O'Neil 8-week hold rule. From "How to Make…, PGRST204 = migration not run yet. The rule must still apply in-memory for this…, The rule was previously self-defeating: TRAIL_PROFIT_TIERS tightens the trail…, Guards the premise: at +20% the ladder clamps to a tight trail. (+10 more)

### Community 55 - "Plateau exit: optimise capital velocity, not per-trade expectancy"
Cohesion: 0.18
Nodes (10): 5 days was an overfit trap, Consequences, Context, Decision, Findings, Follow-up, Method, Per-trade analysis says plateau exits are harmful (+2 more)

### Community 56 - "Managed exit tool: exit at the session high rather than on impulse"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Managed exit tool: exit at the session high rather than on impulse, Note on the positions that prompted this, The hard floor is what makes patience safe, Trail sizing is volatility-scaled, not fixed

### Community 57 - "_reload"
Cohesion: 0.13
Nodes (10): fixture, test_screener_filters.py - Tests for the CAN SLIM fundamental gate in…, Re-import the screener module with the given env overrides applied., Regression: was 0, which admitted SWK on 0.6% revenue growth., Regression: was 15. O'Neil requires ~25%., Thresholds must be adjustable without a code change, for A/B and rollback., _reload(), screener() (+2 more)

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
Nodes (4): The pivot check used to be a ceiling only: it rejected stocks extended too far…, The buy loop takes its price from fetch_ibkr_delayed_price, not get_live_price,…, A 1% dip is noise around the pivot, not a failed breakout., TestPivotBuyZoneFloor

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

### Community 70 - "_coil"
Cohesion: 0.16
Nodes (13): _coil(), _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None., Recent 3d avg vol 1.1x 50d avg -> sellers still active -> None., Strictly descending then tiny uptick: must compare vs prior row. Use all-… (+5 more)

### Community 71 - "Commit a benchmark price dataset instead of re-fetching from FMP"
Cohesion: 0.22
Nodes (8): Commit a benchmark price dataset instead of re-fetching from FMP, Decision, Format, Housekeeping, Problem, Reproducibility contract, What is deliberately excluded, Why not the alternatives

### Community 72 - "Committed benchmark price dataset"
Cohesion: 0.25
Nodes (7): Committed benchmark price dataset, Contents, Known limitations, Not yet included: 5-minute bars, Rebuilding / extending, Universe, Usage

### Community 73 - "_sb"
Cohesion: 0.12
Nodes (21): _extras(), tests/test_watchlist_history.py Tests for the append-only point-in-time…, Directly testable as a buy gate: do names qualifying many runs running…, A same-day re-run must overwrite, not duplicate., Append-only. A delete here would reintroduce the very data loss this table…, A research feature must never be able to break live screening., THE critical invariant. `watchlist` is wiped every run; if the archive ran…, Research extras must not leak into the `watchlist` insert — those columns do… (+13 more)

### Community 75 - "2026-08-14 — Surface every risk rule's live state in the dashboard"
Cohesion: 0.22
Nodes (8): 2026-08-14 — Surface every risk rule's live state in the dashboard, Consequences, Context, Correctness issues found and fixed while implementing, Decision, Files, Follow-up, Status

### Community 76 - "_make_ib"
Cohesion: 0.13
Nodes (24): _make_ib(), _make_pos(), _make_sb(), tests/test_thesis_stop.py Tests for the Thesis Stop — an ATR-normalised…, The Early Dollar Stop is evaluated before the Thesis Stop in…, The cap is a share of a slot, not a fixed dollar figure. Same position and same…, Fail safe: an unknown equity must disable the rule, not zero it. A 0.0…, Day 4, ATR 3%/day, price -5% -> beyond -3% threshold -> arm exit. (+16 more)

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
Nodes (7): main(), now_et_str(), restart_and_health_check.py — 6:00 AM IB Gateway Health Check & Telegram…, Send HTML Telegram message to all configured chat IDs., Connects to IB Gateway, verifies account U12941651, and checks own cash.…, send_telegram(), verify_health()

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
Nodes (57): Path, _coerce_column(), fetch_table(), main(), notify_failure(), DataFrame, supabase_backup.py Weekly point-in-time export of every Supabase table to flat…, Return every row of `table`, paginated and deterministically ordered. Raises if… (+49 more)

### Community 119 - "make_history"
Cohesion: 0.07
Nodes (30): _default_config(), make_history(), make_response(), patch_fmp(), date, fixture, test_market_direction.py — Real unit tests for the CANSLIM "M" gate. Before…, Both above the buffer with rising SMA-200 → BULL. (+22 more)

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
Nodes (29): compute_outcomes(), fetch_pending(), fetch_prices(), main(), _pct(), backfill_trigger_outcomes.py Weekly job that links archived breakout triggers…, Forward returns measured from the first session AFTER triggered_at. CONVENTIONS…, Triggers whose measurement window has fully elapsed and are unmeasured. (+21 more)

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
Cohesion: 0.17
Nodes (11): ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm, Consequences, Context, Decision, Entry-filter improvements — REJECTED, Evidence, Files, Known limitation (+3 more)

### Community 170 - "CRH"
Cohesion: 0.50
Nodes (4): end, rows, start, CRH

### Community 171 - "Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing"
Cohesion: 0.25
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
Nodes (5): increment_retention(), get_rating_text(), Append this run's screener output to the append-only `watchlist_history`.…, run_screener(), save_watchlist_history()

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

### Community 206 - "_get_week_start"
Cohesion: 0.29
Nodes (5): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _get_week_start always returns the Monday 00:00:00 UTC of the same ISO week., TestGetWeekStart

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

### Community 222 - "rank_policy_bt.py"
Cohesion: 0.27
Nodes (12): build(), find_triggers(), _indicators(), per_type(), _rank_key(), Counterfactual replay: trigger-RANKING policy A (score-first) vs B (confirmed-…, Point-in-time SPY 12-week (60 trading day) return, keyed by date., Replay BOTH screener detectors bar-by-bar, using production scoring. Returns… (+4 more)

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
Nodes (4): Regression guard for the volume-gate inversion. `daily_triggers.volume_surge`…, The core inversion: 0.66x is a tight coil, the signal we want., The screener's own < 1.00 gate governs looseness, not this one., TestVolumeGateRespectsTriggerType

### Community 229 - "managed_exit.py"
Cohesion: 0.26
Nodes (15): get_live_price(), Fetch current price of a ticker from FMP., archive(), cancel_sells(), current_price(), main(), market_exit(), _notify() (+7 more)

### Community 230 - "_client"
Cohesion: 0.11
Nodes (10): _client(), _FakeQuery, tests/test_schema_guard.py Tests for the startup schema assertion. Context: on…, A monitoring concern must never become a trading outage., The exact drift found in production must be reported as degraded., TestAdvisoryTables, TestBuyGate, TestCriticalColumns (+2 more)

### Community 231 - "schema_guard.py"
Cohesion: 0.20
Nodes (8): assert_schema_ok(), Verify risk-rule columns exist. Returns False when new buys must be blocked.…, check_schema(), _probe(), Startup schema assertion — fail LOUD when a risk rule's columns are missing.…, True if the table (and column, if given) is queryable., Probe every object a risk rule or archive depends on., SchemaReport

### Community 232 - "2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing"
Cohesion: 0.20
Nodes (9): 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing, Consequences, Context, Decision, Evidence for preferring the close latch, Files, Follow-up, Related change: the migration backfill was itself unsafe (+1 more)

### Community 233 - "Early Loss Kill-switch: tighten to 1% and restrict to the entry day"
Cohesion: 0.17
Nodes (11): 1. The window matters far more than the threshold, 2. Arming beats selling, in every family, 3. Nothing beat the plain percentage rule, Consequences, Context, Decision, Early Loss Kill-switch: tighten to 1% and restrict to the entry day, Findings (+3 more)

### Community 234 - "CB"
Cohesion: 0.50
Nodes (4): end, rows, start, CB

### Community 235 - "TeeLogger"
Cohesion: 0.31
Nodes (3): Mirrors stdout to a daily rotating log file without touching print() calls., Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS. Uses the date…, TeeLogger

### Community 236 - "test_oca_managed_exit.py"
Cohesion: 0.23
Nodes (7): _placed(), _queue_sb(), tests/test_oca_managed_exit.py — Smart OCA Managed Exit. The dangerous property…, A buy_date exactly `n` trading days before today, in New York. Must be…, Supabase mock that keeps exit_requests and portfolio_positions apart., TestQueueBackstops, _trading_days_ago()

### Community 237 - "request_exit.py"
Cohesion: 0.48
Nodes (6): _client(), cmd_cancel(), cmd_list(), main(), Best-effort reference price for the confirmation preview only., _ref_price()

### Community 238 - "Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those, Guard, Left alone deliberately, Problem

### Community 239 - "compute_pre_breakout_quality_score"
Cohesion: 0.18
Nodes (9): compute_pre_breakout_quality_score(), Quality score 0-100 for a pre-breakout (coiling) trigger. Weights: Pivot…, Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65., Within 5%, 0.8x vol, 2 closes up -> 28+int(0.2*40)+10=28+8+10=46 (rounding…, Within 8%, 0.9x vol, 2 closes up -> 20+4+10=34 (rounding may give 33)., 0 rising closes -> uptrend=0 -> 35+20+0=55. (+1 more)

### Community 240 - "Decision"
Cohesion: 0.22
Nodes (8): 1. Hard volume surge gate in `execution_agent.py`, 2. PRE_BREAKOUT 52W pivot distance gate in `execution_agent.py`, 3. AI prompt penalty rules in `ai_evaluator.py`, 4. D-veto threshold raised from 30 → 50, ADR: Buy Gate Hardening — Volume Surge Floor, PRE_BREAKOUT Pivot Distance, AI Penalty Rules, Consequences, Context, Decision

### Community 241 - "Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed"
Cohesion: 0.25
Nodes (7): Consequences, Context, Dashboard consistency, Decision, Evidence, Follow-ups, Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed

### Community 242 - "EW"
Cohesion: 0.50
Nodes (4): end, rows, start, EW

### Community 244 - "Decision: Scope the volume surge gate to confirmed breakouts only"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Scope the volume surge gate to confirmed breakouts only, Guard, Observed damage — 2026-08-19, Problem

### Community 245 - "trading_days_between"
Cohesion: 0.18
Nodes (11): _build_failed_params_snapshot(), _infer_exit_type(), _nyse_holidays(), date, Classify an exit reason into the breakout_learnings exit_type bucket., Build a failure-parameter snapshot for breakout_learnings. Preferred source is…, Persist a single breakout_learnings row. Non-fatal by design., Return the set of NYSE market holidays for a given year. Computed… (+3 more)

### Community 246 - "Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price, Guard, Polling, not LISTEN/NOTIFY, Problem

### Community 249 - "Database schema"
Cohesion: 0.50
Nodes (4): Append-only research tables, Database schema, Live state, Migrations

### Community 251 - "HWM profit-lock after the first leg"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, Follow-up, HWM profit-lock after the first leg

### Community 252 - "exit_rule_replay.py"
Cohesion: 0.11
Nodes (29): Any, _env(), ExitConfig, fetch_5min(), fetch_entry_atr_pct(), grid_configs(), headline_configs(), hydrate() (+21 more)

### Community 253 - "C"
Cohesion: 0.50
Nodes (4): end, rows, start, C

### Community 254 - "ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5"
Cohesion: 0.11
Nodes (16): ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5, Consequences, Context, Decision, Simulation, 1. The measured problem, 2. The design flaw the parameter was hiding, 3. Why the rule is kept rather than removed (+8 more)

### Community 255 - "ET"
Cohesion: 0.50
Nodes (4): end, rows, start, ET

### Community 256 - "monitor_portfolio_intraday"
Cohesion: 0.09
Nodes (45): arm_exit(), cancel_ticker_sell_orders(), _close_exit_request(), enqueue_smart_exit(), execute_sell(), _fetch_current_rs(), get_available_cash(), _get_entry_rs() (+37 more)

### Community 257 - "check_and_run_weekly_watchlist"
Cohesion: 0.40
Nodes (5): check_and_run_weekly_watchlist(), periodic_watchlist_scheduler(), Checks if more than 7 days have passed since the last watchlist generation, and…, startup_event(), on_event

### Community 258 - "_with_equity"
Cohesion: 0.50
Nodes (3): Give a mock IB an account with readable NetLiquidation. The Early Dollar Stop…, TestLadderSuspension, _with_equity()

### Community 259 - "Retune HWM profit-lock arm from +6% to +5%"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Docs sync, Evidence, Retune HWM profit-lock arm from +6% to +5%

### Community 260 - "Closed-loop learning from realised outcomes"
Cohesion: 0.33
Nodes (5): Closed-loop learning from realised outcomes, Consequences, Context, Decision, Docs sync

### Community 261 - "FDX"
Cohesion: 0.50
Nodes (4): end, rows, start, FDX

### Community 262 - "COHU"
Cohesion: 0.50
Nodes (4): end, rows, start, COHU

### Community 263 - "test_reconcile_detects_short_positions"
Cohesion: 0.50
Nodes (3): patch, Test that reconcile_with_ibkr detects short positions and sends alert., test_reconcile_detects_short_positions()

### Community 264 - "CRM"
Cohesion: 0.50
Nodes (4): end, rows, start, CRM

## Knowledge Gaps
- **842 isolated node(s):** `bar_interval`, `bytes`, `dataset`, `date_max`, `date_min` (+837 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `TelegramNotifier` connect `TelegramNotifier` to `ai_evaluator.py`, `technical_screener.py`, `fetch_ibkr_delayed_price`, `execution_agent.py`?**
  _High betweenness centrality (0.053) - this node is a cross-community bridge._
- **Why does `patch_fmp()` connect `make_history` to `FMPClient`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Why does `_table()` connect `breakout_bt.py` to `make_supabase_mock`, `_sb`, `_trigger`, `test_oca_managed_exit.py`, `test_supabase_backup.py`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **What connects `bar_interval`, `bytes`, `dataset` to the rest of the system?**
  _842 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.10837438423645321 - nodes in this community are weakly interconnected._
- **Should `patch` be split into smaller, more focused modules?**
  _Cohesion score 0.09365079365079365 - nodes in this community are weakly interconnected._
- **Should `_AV` be split into smaller, more focused modules?**
  _Cohesion score 0.07312925170068027 - nodes in this community are weakly interconnected._