# Graph Report - ai-trading-bot  (2026-10-04)

## Corpus Check
- 463 files · ~629,125 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 7266 nodes · 13186 edges · 567 communities (476 shown, 91 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 360 edges (avg confidence: 0.69)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `7967a6d1`
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
- _coil
- ExitDetailPanel.jsx
- database.py
- _pos
- Decision: Repository-Wide Dead Code Cleanup
- FMPClient
- BrokerPositionError
- breakout_bt.py
- make_position
- test_trading_control.py
- Technical Triggers
- flex_query_sync.py
- test_buy_fill_verification.py
- Configuration Reference
- _resolve
- Tech Debt & Functional Requirements Tracker
- Buy Logic
- rank_percentiles
- Backtest-corrected exit parameters; keep the entry tightening
- make_supabase_mock
- IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot
- Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser
- Sell Logic
- per_symbol
- Fundamental Screener
- Slot count, and establishing the backtest's noise floor
- _screener
- test_calibration_api.py
- test_ci_import_hygiene.py
- _run_queue
- test_ai_evaluator_batching.py
- Decision: Breakout Quality Floor + Quota Waterfall
- Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals
- realizedPeriods.js
- strategy_backtest.py
- docker-compose.yml
- Keep the test suite importable with the root requirements alone
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
- managed_exit.py
- ai_evaluator.py
- Remove the `frontend/dist` bind mount; serve the UI from the image only
- The backtest "noise floor" was mostly a bootstrap bug
- Fix the self-defeating power-hold rule; move to 5 slots
- CMI
- MANIFEST.json
- resolve_position_price
- Commit a benchmark price dataset instead of re-fetching from FMP
- Committed benchmark price dataset
- _sb
- 2026-08-14 — Surface every risk rule's live state in the dashboard
- _make_pos
- AAPL
- FMP API Integration
- Cooling-off is 3 days — measured, not inherited
- IBKR API Integration
- OpenAI API Integration
- test_decision_review.py
- Supabase Integration
- TradingView API Integration
- Decision: Breakout Verdict + Intraday Loss Minimiser
- Decision: Separate Container Architecture (execution-agent vs trading-bot)
- Decision: Dynamic ATR Trailing Stop
- Decision: Plateau Rotation — Simplify from 3-Tier to 2-Rule
- Decision: Pre-Breakout (VCP/Handle) Detection as Second Pass
- ai_value_replay.py
- decisions/
- Local SQLite (trading_bot.db)
- Decision: Backtester Accuracy Rewrite
- restart_and_health_check.py
- ABBV
- test_scale_out.py
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
- test_shadow_worker.py
- BE
- ADR: Point-in-time watchlist history — make the fundamental screen backtestable
- BKNG
- BKR
- BMY
- BN
- test_research_diagnostics.py
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
- test_exit_rule_replay.py
- EBAY
- ECL
- ECO
- trigger_audit.py
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
- App.jsx
- Broker
- market_gate_bt.py
- 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing
- Early Loss Kill-switch: tighten to 1% and restrict to the entry day
- DashboardView.jsx
- TradesView.jsx
- _placed
- request_exit.py
- Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those
- Fall back to a labelled FMP quote on the dashboard, not to cost basis
- Decision
- Market direction gate retune: SPY-only, 0.5% buffer (drop QQQ, tighten band)
- test_exit_timestamp.py
- TestQueueMarketMode
- Decision: Scope the volume surge gate to confirmed breakouts only
- _call
- Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price
- TestManagedTickers
- _Query
- The Prove-It Stop — one loss rule replaces five
- test_oca_managed_exit.py
- HWM profit-lock after the first leg
- retired_pre_proveit_config
- Retired Code Registry
- Early Dollar Stop becomes slot-derived, not a flat dollar amount
- TestTheDetectorActuallyDetects
- TestEqualWeightCapPure
- atr_rank_bt.py
- enrich_trades
- Daily unfilled-slot alert spammed because its dedup latch could not be written (RLS)
- Closed-loop learning from realised outcomes
- Prove-It unarmed window: measured, and deliberately left open
- rs_percentile_review.py
- Decision: Replace get_available_cash with margin-safe functions
- test_strategy_validate.py
- Exit detail panel, and the removal of fabricated exit reasons
- evaluate_exit
- Relative strength is measured but not ranked — shadow `rs_percentile` column
- Backtests model commission and slippage (fidelity item #3)
- Exit-parameter review on 48 closed trades: shipped Prove-It confirmed, cliff fix rejected, nothing changed
- paired_block_bootstrap
- research_diagnostics.py
- Fail loudly on Telegram when the agent cannot reach IBKR
- Disable the breakout failure penalty — it scored every trade identically
- AI evaluator: replace the velocity ladder with volatility fit
- entry_bt.py
- compute_final_score
- Sell-state transition notifications
- NBIX sell price was reconstructed from the wrong day's FMP quote
- _client
- TeeLogger
- compute_exit_shadows
- Backtesting
- test_telegram_notifier.py
- Commission accounting, and the RLS policy gap that hid it
- compute_pre_breakout_quality_score
- test_startup_crash_shipping.py
- ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5
- TestEnqueueSmartExit
- All migrations renamed to `YYYYMMDD_slug.sql`
- test_calibration_worker.py
- TestHardStopPrice
- A static broker-side hard stop that survives disconnection
- Partial Scale-Out — book a third of a winner at +4%
- The exit-parameter review becomes cron-backed instead of a date in a markdown table
- Fail loudly when the database is unreachable
- Reconcile fill window floored at entry; honest trail-trigger display
- Archive entry-decision provenance onto closed trades (`trade_history`)
- backfill_trigger_outcomes.py
- COHU
- Telegram delivery health: make a dead alert channel observable
- test_research_diagnostics_integration.py
- Multi-account IBKR pricing: reqPnLSingle fallback + strict target-account scoping
- CRM
- insert_trade_history
- test_agent_image_completeness.py
- test_no_secrets_committed.py
- intraday_reporting.py
- Weekly-backup ship step: trust the host key on connect, not via a separate scan
- force_buy.py
- CAH
- restart_6am.sh
- Split execution_agent.py along its pure/impure seam
- CalibrationResearchView.jsx
- tee
- Migrations must be re-runnable
- Recorder
- TestHorizonThresholds
- _FakeQuery
- The replay truncated at the real exit, so "hold longer" was unmeasurable; power hold is unreachable because the ladder sells first
- Write each forward-return horizon as soon as it matures
- test_large_backlog_is_inserted_in_batches
- _bars
- test_backfill_outcomes.py
- Register preconditions: separating "is it time?" from "is it safe?"
- _emit
- Sell reasons derive the stop anchor from the fill, not the stored peak
- jackknife
- Phase 1 rests on a STATIC stop, not a ratcheting trailing order
- _RowQuery
- Reason-aware cooling-off: loss exits block, profit exits don't
- _trig
- test_position_sizing_cap.py
- Cap every position at one equal-weight share of equity
- test_log_shipping.py
- AI-value A/B replay harness (does the AI actually pick winners?)
- Earnings blackout, dead news feed fix, and AI news-disqualifier veto
- ._send_multi
- notifier
- ibkr_data.py
- _held
- Exit-parameter review on 52 closed trades: shipped stack holds, and the `--proveit` sweep is repaired
- test_sell_safety.py
- Ship noteworthy log lines to Supabase
- exit_rule_replay.py
- test_intraday_reporting.py
- test_render_env.py
- CB
- Ship the full agent log, not just the error lines
- test_intraday_observer.py
- 2026-09-27 — Secrets resolved from Bitwarden at deploy time (`@bws` sentinel)
- test_intraday_calibration.py
- The run-on truncation bias also affected the scale-out path
- test_calibration_risk.py
- test_live_rule_replay.py
- EW
- ExitConfig
- Price the lot from its own fills, and make cooling-off broker-aware
- entry_quality_review.py
- Entry quality and the missing right tail — an open question, measured not answered
- test_exit_shadow_store.py
- agent_logs was unwritable for a day, and the guard said it was fine
- test_intraday_replay.py
- ._eod_monitor
- Slot opportunity cost: pricing "let winners run" against the entries a longer hold blocks
- Cooling-off is return-neutral, not a profit rule — measured, kept at 3 days
- TestStampingContract
- Phase 1 resting broker STP sits AT the band — IBKR is the primary enforcer
- Trade lifecycle — how winners and losers are treated
- test_broker_positions.py
- Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py
- Reconcile `buy_price` against IBKR `averageCost`, and sum the dashboard headline from the rows
- rank_policy_bt.py
- run_market_open_buys
- exit_shadow_review.py
- Split execution_agent.py orchestrators into focused modules
- CalibrationDashboard.jsx
- A leave-one-out jackknife makes "carried by one trade?" arithmetic, not a judgement call
- 20261001_daily_notifications_rls.sql
- execution_agent.py
- test_retention_failure_is_reported_as_a_purge_failure
- test_markers_only_mode_still_works
- test_consecutive_repeats_are_collapsed
- test_dedup_only_collapses_ADJACENT_lines
- test_sequence_numbers_are_monotonic
- test_all_rows_carry_the_session_id
- test_severity_wins_over_trade_classification
- test_partial_batch_failure_reports_what_was_written
- test_shipping_failure_does_not_recurse
- test_flush_quietly_survives_a_dead_supabase_client
- test_retention_enforces_a_hard_row_ceiling
- MonitorRecorder
- test_redaction_keeps_the_line_useful
- test_redaction_runs_before_buffering
- calibrate_intraday.py
- test_unconfigured_send_is_no_longer_silent
- fail
- run_intraday_reporting_bws.py
- test_intraday_capture.py
- initialize
- 2026-09-28 — Bitwarden `secret list` is scoped to one project (fail-closed)
- test_portfolio_replay_chronology.py
- shadow_inputs.py
- test_web_image_completeness.py
- MemoryQuery
- live_rule_replay.py
- fake_runner
- ._place
- Entry decisions extracted into a pure `decision_core` shared by live and backtest
- CostModel
- test_treasury_reference.py
- compute_rs_score
- Exit decisions extracted into a pure `exit_core` shared by live and backtest
- .verify_delivery
- test_unconfigured_names_the_missing_variable
- test_strategy_backtest.py
- Backtester exit parity: a research backtest that calls the live exit engine
- intraday_replay.py
- Option A: the dashboard backtester exits with the LIVE engine
- test_backtester_live_exits.py
- first
- test_intraday_service.py
- Statistical-rigor harness for the strategy backtester (fidelity item #6)
- fetch_5min
- TestSmartExitRuleScoping
- thesis_reexam_bt.py
- trading_control_api.py
- Share the Bitwarden bootstrap token; resolve the project by name
- Record intraday evidence and compare strategies from the actual account
- proveit_configs
- test_research_fidelity_labels.py
- test_importing_the_module_registers_no_shutdown_hook
- test_row_ceiling_is_a_noop_when_under_the_cap
- _earnings_blackout_days_until
- test_calibration_deployment.py
- Broker-confirmed sell safety after the SHIP incident
- technical_screener.py
- BacktesterView.jsx
- 20260708_enable_rls_all_tables.sql
- 20260813_apply_missing_migrations.sql
- 20260624_migration_retention_period.sql
- 20260624_twr_schema.sql
- 20260708_add_quality_scoring.sql
- 20260712_add_5component_scoring.sql
- 20260717_add_position_analysis_columns.sql
- 20260809_add_trigger_history.sql
- 20260906_add_commission_tracking.sql
- 20260906_fix_rls_missing_policies.sql
- 20260917_add_rs_percentile.sql
- 20260708_add_hwm_date.sql
- 20260708_create_cash_flows.sql
- 20260714_patch_cash_flows_columns.sql
- 20260715_add_plateau_rotation_columns.sql
- 20260716_add_hwm_rs_score.sql
- 20260717_add_breakout_learnings.sql
- 20260717_add_hwm_price_column.sql
- 20260719_add_highest_rs_score.sql
- 20260719_add_momentum_health_score.sql
- 20260719_add_risk_optimization_columns.sql
- 20260721_add_breakout_verdict.sql
- 20260721_add_trigger_type.sql
- 20260801_add_ibkr_fills.sql
- 20260801_add_margin_tracking.sql
- 20260803_add_armed_exit_columns.sql
- 20260804_add_power_hold.sql
- 20260809_add_closed_above_entry.sql
- 20260809_add_trigger_outcomes.sql
- 20260809_add_watchlist_history.sql
- 20260818_add_exit_requests.sql
- 20260819_add_exit_requests_atr_default.sql
- 20260904_add_ibkr_position_values.sql
- 20260907_add_hard_stop_price.sql
- 20260908_add_scale_out_columns.sql
- 20260908_add_sell_state_column.sql
- 20260918_add_agent_logs.sql
- 20260918_add_telegram_health.sql
- 20260918_expand_agent_logs.sql
- 20260918_relax_agent_logs_rls.sql
- 20260918_widen_sell_reason.sql
- 20260927_add_exit_shadow_log.sql
- 20260928_add_daily_notifications.sql
- 20260928_add_entry_scores_to_trade_history.sql
- 20260928_add_next_earnings_date_to_daily_triggers.sql
- ReadOnlyBroker
- Recorded intraday research
- compute_rs_excess
- intraday_service.py
- ShadowStore
- intraday_reporting_delivery.py
- test_deploy_runtime.py
- test_auto_calibration.py
- intraday_capture.py
- exception_details
- 2026-09-27 — Startup crashes ship to Supabase; execution_agent import cycle removed
- Retune HWM profit-lock arm from +6% to +5%
- deploy_runtime.sh
- Real trading control
- TestPathMetrics
- shadow_service.py
- exit_core.py
- TestIncompleteWindowsNotWritten
- 20260930_add_intraday_shadow.sql
- test_shadow_image.py
- TestResumability
- TestFetchRobustness
- IntradayReplayView.jsx
- test_trading_control_api.py
- Query
- ReportingError
- test_shadow_activity.py
- CalibrationStore
- TradingControl.jsx
- calibration_api.py
- calibration_deployment.py
- 2026-10-04 — Load watchdog credentials from Bitwarden on the hosted runner
- test_calibration_store.py
- Evidence
- run_comparison
- treasury_reference.py
- auto_calibration.py
- _Replay
- sentiment.py
- backtester.py
- calibration_worker.py
- exit_rules.py
- test_datasource_unavailable.py
- MemoryStore
- Independent observation and approval-only calibration
- test_calibration_risk_refresh_store.py
- _LedgerQuery
- market_regime.py
- TestReconcileCase4
- Interactive self-calibration
- calibration_risk_report.py
- test-calibration-dashboard.mjs
- _run_monitor
- Automatic research with operator-approved strategy deployment
- CalibrationRiskMetrics.jsx
- test_exit_path_golden.py
- 2026-09-26 — Phase 1 backstop-slack widening (behaviour retired, constant kept)
- test_shadow_service.py
- ActivityQuery
- Evidence-bound risk ratios for calibration
- ._run_at
- capture_phase
- Unified, evidence-scoped calibration visibility
- _ExecutionAgentRef
- parametrize
- TestGetTradeHistoryProjection
- session_bounds
- AMTM
- BDX
- BNY
- EA
- telegram_notifier.py
- ._run_with_earnings
- .test_hwm_date_not_updated_when_price_falls

## God Nodes (most connected - your core abstractions)
1. `make_ib_mock()` - 152 edges
2. `per_symbol` - 124 edges
3. `make_supabase_mock()` - 117 edges
4. `make_position()` - 97 edges
5. `make_trigger()` - 59 edges
6. `_pos()` - 51 edges
7. `post()` - 45 edges
8. `now()` - 39 edges
9. `TelegramNotifier` - 39 edges
10. `CalibrationStore` - 38 edges

## Surprising Connections (you probably didn't know these)
- `SettingsInput` --uses--> `CalibrationStore`  [INFERRED]
  backend/calibration_api.py → research/calibration_store.py
- `SettingsInput` --uses--> `StoreUnavailable`  [INFERRED]
  backend/calibration_api.py → research/calibration_store.py
- `SettingsInput` --uses--> `ValidationError`  [INFERRED]
  backend/calibration_api.py → research/calibration_store.py
- `ActionInput` --uses--> `CalibrationStore`  [INFERRED]
  backend/calibration_api.py → research/calibration_store.py
- `ActionInput` --uses--> `StoreUnavailable`  [INFERRED]
  backend/calibration_api.py → research/calibration_store.py

## Import Cycles
- None detected.

## Communities (567 total, 91 thin omitted)

### Community 0 - "compute_liquidity_score"
Cohesion: 0.12
Nodes (11): compute_liquidity_score(), Penalises low-price, low-volume, and small-cap stocks (0-100).      Price tier, tests/test_score_components.py  Unit tests for the new 5-component scoring funct, NVDA-like: $750, 42M avg vol, Large -> max score, Mid-tier stock: $35, 800K vol, Mid, SGHC-like: $8, 180K vol, Small -> very low, $15 exact -> price tier = 20, $14.99 -> price tier = 10 (+3 more)

### Community 1 - "datetime"
Cohesion: 0.08
Nodes (28): _get_week_start(), datetime, Return UTC midnight of the Monday starting the ISO week containing dt., _make_supabase_mock(), _monday(), datetime, patch, Tests for watchlist weekly-snapshot logic.  Guards the following invariants:   1 (+20 more)

### Community 2 - "_AV"
Cohesion: 0.07
Nodes (30): _AV, _make_ib_with_account_values(), test_margin_safety.py — Tests for the margin-cash safety layer.  Covers two crit, When TotalCashValue > 0, there is no margin loan — get_margin_loan returns 0., Edge case: TotalCashValue cannot exceed NetLiquidation in a real account., When TotalCashValue < 0, a margin loan is active.         get_own_cash must retu, get_margin_loan() must return the absolute value of a negative         TotalCash, Stress case: large margin loan (like the TRV incident ~$35K borrowed).         g (+22 more)

### Community 3 - "Re-audit of the 2026-07-29 performance analysis"
Cohesion: 0.11
Nodes (17): Addendum — stop widened to 10% (user approved), Aggression: slot count was tested and 4 is correct, Audit result, Bug 2 — Day 3 verdict compared two stale volume bars (real defect), Consequences, Context, Fixed here, Follow-up (+9 more)

### Community 4 - "positionRules.js"
Cohesion: 0.14
Nodes (26): armed, base, config, evaluate(), journey, legacy, off, pos (+18 more)

### Community 5 - "TelegramNotifier"
Cohesion: 0.11
Nodes (17): Exception, Snapshot for persistence/display. Pure — performs no network I/O., Send message to all configured chat IDs. Never raises.          Returns True onl, Fires from technical_screener.py when breakouts are pushed to the database., Fires from ai_evaluator.py after all 5-component scores are computed.         Sh, Fires after a successful IBKR market buy order is filled and recorded., Fires when a buy order placement on IBKR fails., Fires when the buy loop is stopped after a failed order attempt.          Distin (+9 more)

### Community 6 - "make_ib_mock"
Cohesion: 0.05
Nodes (46): build_ibkr_price_map(), get_position_price(), IBKR-first live price for an OPEN position, with FMP fallback.      Live trades, Return {symbol: mark} for the TARGET account's open positions only.      The bot, isolated_entry_control(), make_ib_mock(), make_ohlcv_data(), make_portfolio_item() (+38 more)

### Community 7 - "_compute_dynamic_trail_pct"
Cohesion: 0.14
Nodes (9): _compute_dynamic_trail_pct(), Returns a tighter trailing stop % if the position has crossed a new tier,     ot, test_dynamic_trail.py - Tests for _compute_dynamic_trail_pct() and the dynamic t, The profit-lock must wait for the full +5% gain threshold. A merely green, Once locked to 1.5%, a dip in profit must not restore a wider trail., Regression for the 2026-09-07 DHT '4.9% → 4.9%' notification spam.      The Prov, TestNoSubQuantumChurn, TestOneWayOnly (+1 more)

### Community 8 - "test_plateau_rotation.py"
Cohesion: 0.11
Nodes (20): _full_portfolio(), _hwm(), test_plateau_rotation.py — Tests for the simplified 2-rule plateau rotation stra, Even with large RS decay (>15 pts from HWM), RS_DECAY is never recommended., hwm_rs_score write was removed from EOD metrics loop — column stays dormant., Tests that hwm_rs_score is NOT written to the DB in any circumstance.     The co, days_since_hwm=0 (new HWM today) → hwm_rs_score must NOT be written (column dorm, days_since_hwm=3 (stalling) → hwm_rs_score must NOT be in any update payload. (+12 more)

### Community 9 - "_trigger"
Cohesion: 0.08
Nodes (21): _payloads(), tests/test_trigger_audit.py  Tests for the point-in-time trigger archive and the, PK includes trigger_type, so BREAKOUT and PRE_BREAKOUT coexist., Snapshotted rather than joined: the trigger row may be re-scored on a         la, A name skipped for lack of a slot says nothing about the quality         model b, A trigger can be re-evaluated on several days within the lookback         window, A failure here must never interrupt a live buy cycle., THE critical invariant, plus the subtler one about WHICH rows. (+13 more)

### Community 10 - "_run"
Cohesion: 0.19
Nodes (19): _make_ib(), _make_ohlcv(), _make_pos(), _make_sb(), tests/test_breakout_verdict.py  Tests for the Breakout Verdict (Day 3 EOD), Intr, Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify., Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent., Day 3 EOD: price +2% but volume 0.5x avg -> FAIL. (+11 more)

### Community 11 - "main.py"
Cohesion: 0.08
Nodes (47): approve_rotation(), auto_generate_watchlist(), BacktestRequest, check_and_run_weekly_watchlist(), Config, dismiss_rotation(), export_intraday(), export_intraday_observations() (+39 more)

### Community 12 - "fetch_ibkr_delayed_price"
Cohesion: 0.12
Nodes (19): fetch_ibkr_delayed_price(), IB, Fetch the current price for a contract using IBKR delayed market data (type 3)., _make_ib(), _make_ticker(), tests/test_ibkr_delayed_price.py  Unit tests for fetch_ibkr_delayed_price() -- t, reqMarketDataType(1) must be the last call even on success., reqMarketDataType(1) must be called even when reqTickers raises. (+11 more)

### Community 13 - "package.json"
Cohesion: 0.07
Nodes (28): dependencies, lucide-react, react, react-dom, recharts, devDependencies, @types/react, @types/react-dom (+20 more)

### Community 14 - "_coil"
Cohesion: 0.16
Nodes (13): _coil(), _make_df(), 15% below 52w high -> beyond 8% proximity -> None., At or above 52w high -> confirmed breakout territory -> None., Close (77) below SMA-50 (~90) -> below trend -> None., Stock -5% vs SPY +15% -> low RS -> None., Recent 3d avg vol 1.1x 50d avg -> sellers still active -> None., Strictly descending then tiny uptick: must compare vs prior row.         Use all (+5 more)

### Community 15 - "ExitDetailPanel.jsx"
Cohesion: 0.17
Nodes (16): failures, EXECUTOR_COLOR, EXECUTOR_ICON, ExitDetailPanel(), formatFact(), money(), pct(), deriveTradeMath() (+8 more)

### Community 16 - "database.py"
Cohesion: 0.12
Nodes (28): _bg_update_fmp_cache(), DataSourceUnavailable, get_account_balances(), get_cash_flows(), get_daily_triggers(), get_db_connection(), get_positions(), get_screener_results() (+20 more)

### Community 17 - "_pos"
Cohesion: 0.09
Nodes (11): _pos(), PCT_FROM_PRICE is momentum-following by construction: re-anchoring to the     op, The upper leg must stay reachable however far underwater the position is.      B, The whole point: a deep loser gets the same target as a winner., On DELL, breakeven needed +5.85%; ATR_AUTO needs 3.8%., A bare `INSERT INTO exit_requests (ticker) VALUES (...)` path., Optimistic leg must be looser than the protective one, at any ATR., TestAtrAutoUpperLeg (+3 more)

### Community 18 - "Decision: Repository-Wide Dead Code Cleanup"
Cohesion: 0.20
Nodes (9): Bug found and fixed while investigating (tightly coupled — not a, Dead code removed from active files, Decision, Decision: Repository-Wide Dead Code Cleanup, Files changed, Files removed entirely (2,339 lines, zero references anywhere in the, Follow-up pass: one-off scripts not wired into the main programs, Problem (+1 more)

### Community 19 - "FMPClient"
Cohesion: 0.16
Nodes (8): FMPClient, DataFrame, Fetch annual balance sheets using stable endpoint., Calculate institutional holdings percentage.         Gracefully falls back to a, Query stable stock-screener to find active US growth equities.         Gracefull, Fetch current price, moving averages, volume, 52w range and shares outstanding u, Fetch historical daily prices and format as pandas DataFrame using stable EOD en, Fetch quarterly or annual income statements using stable endpoint.

### Community 20 - "BrokerPositionError"
Cohesion: 0.09
Nodes (33): BrokerPositionError, confirmed_long_quantity(), RuntimeError, Complete, account-scoped broker inventory for safety decisions., Broker inventory cannot safely authorize an order., Return a positive whole-share holding, never a stale ledger quantity., require_no_short_positions(), Lazy, entrypoint-safe handle to the ``execution_agent`` module.  The modular spl (+25 more)

### Community 21 - "breakout_bt.py"
Cohesion: 0.12
Nodes (27): daily(), dyn_trail(), find_breakouts(), indicators(), Breakout-population backtest.  Addresses a selection-bias problem: the exit para, Daily bars from the committed benchmark dataset — no network, no rate limit., Enter at the open the day after the signal; exit per cfg on daily bars., run() (+19 more)

### Community 22 - "make_position"
Cohesion: 0.05
Nodes (40): make_position(), Factory for a portfolio_positions Supabase row.      hwm_rs_score: RS score on t, test_reconcile.py — Tests for reconcile_with_ibkr() four reconcile cases.  Criti, Bug #5 related: PortfolioItem uses .averageCost (NOT .avgCost).         The code, Case 2: averageCost = 0 → skip insert (prevents ghost $0 positions)., Case 2: no absolute stop_loss price is stored.          The `stop_loss` column w, Case 3: Unexplained quantity changes must preserve the recorded lot., IBKR has 150 shares, Supabase says 100: do not invent lot accounting. (+32 more)

### Community 23 - "test_trading_control.py"
Cohesion: 0.07
Nodes (52): setter, Run monitor_portfolio_intraday at 3:50 PM ET (EOD window).     Returns the mock_, _run_eod(), parametrize, test_absent_store_fails_closed_and_first_heartbeat_defaults_off(), test_automatic_and_manual_off_gates_do_not_touch_broker_or_database(), test_corrupt_permission_is_never_coerced_true(), test_forced_buy_sentinel_cannot_skip_regular_protection_when_off() (+44 more)

### Community 25 - "Technical Triggers"
Cohesion: 0.12
Nodes (17): `BREAKOUT` — the primary signal, Decision log, Each horizon is written as soon as it matures, Forward-return outcomes, Parameters, Position in the pipeline, `PRE_BREAKOUT_RELAXED` — quota fill, `PRE_BREAKOUT` — the coil (+9 more)

### Community 26 - "flex_query_sync.py"
Cohesion: 0.12
Nodes (26): end, rows, start, ET, check_token_expiry(), fetch_cash_transactions(), _fetch_statement(), fetch_trade_confirms_for_ticker() (+18 more)

### Community 27 - "test_buy_fill_verification.py"
Cohesion: 0.15
Nodes (8): FakeQuery, FakeSupabaseClient, FakeTable, MockPosition, _polling_ib(), patch, test_smart_polling_fast_fill(), test_smart_polling_timeout()

### Community 28 - "Configuration Reference"
Cohesion: 0.05
Nodes (37): AI evaluator, Alert-channel health, Append-only research tables, Armed exit, Backtest cost model, Buy gating, Changing parameters safely, Commissions (+29 more)

### Community 29 - "_resolve"
Cohesion: 0.18
Nodes (9): parametrize, Guards the single-source-of-truth invariant for MAX_POSITIONS.  ADR 2026-08-04 m, A module that re-reads the env itself can drift on its default. Root         mod, Importing config.py is useless if the image does not contain it., Return each module's view of the shared constants under a given env value., Changing slot count must need a .env edit only — never a code change., _resolve(), TestNoLocalRedeclaration (+1 more)

### Community 30 - "Tech Debt & Functional Requirements Tracker"
Cohesion: 0.25
Nodes (8): Change Log, Follow-ups Raised by Recent Changes, Functional Requirements, How to use this tracker, Requirement Gaps (Current Bot vs Required Filter), Scheduled Reviews, Tech Debt Backlog, Tech Debt & Functional Requirements Tracker

### Community 31 - "Buy Logic"
Cohesion: 0.13
Nodes (15): Audit trail, Buy Logic, Cooling-off is reason-aware, and reads the broker, Daily "unfilled slots" summary, Design principle: fail closed, Gate 5 scores on `final_score` — the failure penalty is off, Order placement and post-fill, Parameter reference (+7 more)

### Community 32 - "rank_percentiles"
Cohesion: 0.13
Nodes (8): assign_rs_percentiles(), rank_percentiles(), Percentile rank (1-99) of each value within its own cohort.      Ties share the, Annotate each trigger dict in-place with `rs_percentile`.      Ranks on `rs_exce, tests/test_rs_percentile.py  Covers the shadow relative-strength ranking added 2, TestArchiveCarriesShadowColumns, TestAssignRsPercentiles, TestRankPercentiles

### Community 33 - "Backtest-corrected exit parameters; keep the entry tightening"
Cohesion: 0.14
Nodes (13): Backtest-corrected exit parameters; keep the entry tightening, Consequences, Context, Decision, Fidelity limits (important), Finding 1 — there was no right tail to protect, Finding 2 — ablation: only one of the four exit changes helps, Finding 3 — the early-loss reasoning was simply wrong (+5 more)

### Community 34 - "make_supabase_mock"
Cohesion: 0.07
Nodes (38): make_ibkr_fill(), make_supabase_mock(), make_trigger(), Factory for a daily_triggers Supabase row.      final_score defaults to 75 (a no, Factory for an ibkr_fills Supabase row.      fill_time: full ISO timestamp overr, Returns a MagicMock Supabase client where each table's queries return     realis, test_new_buys_blocked_on_short_or_unknown_inventory(), test_short_arriving_after_preflight_blocks_buy_submission() (+30 more)

### Community 35 - "IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot"
Cohesion: 0.11
Nodes (19): IBKR TOTP Setup Guide — Automated 2FA for Live Trading Bot, Overview, Phase 1 — Extract the TOTP Base32 Secret from IBKR, Phase 2 — Configure the Trading Bot, Phase 3 — Verify Unattended Operation, Step 10: Resume observation, not trading, Step 1: Log into IBKR Client Portal, Step 2: Navigate to Secure Login Settings (+11 more)

### Community 36 - "Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Early Loss Kill-switch + Day-2 Universal Intraday Minimiser, Implementation, Problem, Rationale

### Community 37 - "Sell Logic"
Cohesion: 0.04
Nodes (48): 1. Dynamic trailing stop (IBKR-managed), 1b. Static hard stop — the disconnect-proof floor, 1c. Partial scale-out — book a third of a winner at +4%, 2. The Prove-It Stop — always live, 3. Staleness — day 7+ (feeds Rank & Replace), 4. Rank & Replace — day 7+, A stale peak is labelled, not published as fact, Armed Exit — the "smart sale" mechanism (+40 more)

### Community 38 - "per_symbol"
Cohesion: 0.17
Nodes (12): end, rows, start, end, rows, start, end, rows (+4 more)

### Community 39 - "Fundamental Screener"
Cohesion: 0.15
Nodes (12): Filters, Fundamental Screener, Known limitation, Ordering invariant, Output, Parameters, Point-in-time archive, Purpose (+4 more)

### Community 40 - "Slot count, and establishing the backtest's noise floor"
Cohesion: 0.22
Nodes (8): Context, Follow-up, Harness defects found and fixed, Notable observations, not acted on, Re-verification: both major decisions hold under the slot constraint, Slot count, Slot count, and establishing the backtest's noise floor, The noise floor — the most important result here

### Community 41 - "_screener"
Cohesion: 0.14
Nodes (11): _learning(), fixture, Tests for the breakout failure penalty (technical_screener._compute_failure_pena, Executable evidence. If someone re-enables the penalty, these tests document, Reimport technical_screener with a given FAILURE_PENALTY_MAX_POINTS., _restore_env(), _screener(), TestPenaltyDisabledByDefault (+3 more)

### Community 42 - "test_calibration_api.py"
Cohesion: 0.09
Nodes (50): artifact_digest(), candidate_verification(), evidence(), post(), fixture, parametrize, Isolated research API authorization, state and exact-evidence approval contracts, rule_provenance() (+42 more)

### Community 43 - "test_ci_import_hygiene.py"
Cohesion: 0.18
Nodes (13): _module_level_imports(), parametrize, Path, Guards that the test suite stays runnable in CI's dependency environment.  The D, backend/pricing.py exists precisely so the pricing rules are testable     withou, Top-level import names only. Imports inside functions are lazy and safe., Pins the premise of the check below. If FastAPI is ever added to the root     re, A test must not import an undeclared part of the web stack.      Checked one lev (+5 more)

### Community 44 - "_run_queue"
Cohesion: 0.26
Nodes (5): _pending(), End-to-end: a request queued tonight must price off tomorrow's settled     price, _run_queue(), TestNextMorningReanchor, TestQueuePending

### Community 45 - "test_ai_evaluator_batching.py"
Cohesion: 0.09
Nodes (10): Regression tests for the AI evaluator batching / completeness logic.  Background, `_next_earnings_from_rows` picks the earliest UPCOMING earnings date from     FM, The AI was news-blind because the legacy /api/v3/stock_news endpoint now     403, Simulates the exact production failure: model drops middle entries., TestBatching, TestNewsAndEarningsEndpoints, TestNextEarningsExtraction, TestPromptCompleteness (+2 more)

### Community 46 - "Decision: Breakout Quality Floor + Quota Waterfall"
Cohesion: 0.33
Nodes (5): Consequences, Decision, Decision: Breakout Quality Floor + Quota Waterfall, Problem, Rationale

### Community 47 - "Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals"
Cohesion: 0.33
Nodes (5): Decision, Decision: Armed Trailing Exit for Day 0-6 Loss-Cutting Signals, Files changed, Problem, Why these specific numbers

### Community 48 - "realizedPeriods.js"
Cohesion: 0.22
Nodes (10): failures, trade(), addDays(), addMonths(), periodBounds(), realizedByPeriod(), sellTime(), startOfDay() (+2 more)

### Community 49 - "strategy_backtest.py"
Cohesion: 0.15
Nodes (23): build_exit_config(), DayResult, _ladder_trail_pct(), new_position(), ExitConfig, daily_exit_sim.py — the LIVE exit engine, resolved once per DAILY OHLC bar.  WHY, What resolving one position for one day produced., The trailing-stop fraction the LIVE profit ladder rests at for a given     peak (+15 more)

### Community 51 - "Keep the test suite importable with the root requirements alone"
Cohesion: 0.07
Nodes (25): Alternatives rejected, Consequences, Context, Decision, Files, Keep the test suite importable with the root requirements alone, 2026-09-27 — Exit-rule shadow logger (measure Q1 arm@+3% and Q2 5% give-back trail in production, without trading on them), Consequences (+17 more)

### Community 52 - "verify-build.mjs"
Cohesion: 0.33
Nodes (5): DIST_DIR, failures, FEATURE_FINGERPRINTS, SOURCE_GUARDS, SRC_ROOT

### Community 53 - "2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers"
Cohesion: 0.17
Nodes (11): 1. Fail closed on un-vetted triggers (`execution_agent.py`), 2026-08-04 — Fix silent AI-evaluation gap and fail closed on un-vetted triggers, 2. Batch the AI calls (`ai_evaluator.py`), 3. Demand completeness in the prompt, 4. Validate and retry, then alert, Consequences, Context, Decision (+3 more)

### Community 54 - "_pos"
Cohesion: 0.07
Nodes (22): is_power_hold_active(), O'Neil 8-week hold rule.      True while a position is inside its protected wind, patch_everywhere(), Patch `name` on EVERY loaded project module that binds it.      Python imports c, TestQueueResilience, _client(), _pos(), test_power_hold.py - Tests for the O'Neil 8-week hold rule.  From "How to Make M (+14 more)

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

### Community 63 - "managed_exit.py"
Cohesion: 0.23
Nodes (17): _confirmed_remaining(), _require_agreed_quantity(), get_live_price(), Fetch current price of a ticker from FMP., archive(), cancel_sells(), current_price(), main() (+9 more)

### Community 64 - "ai_evaluator.py"
Cohesion: 0.11
Nodes (27): ai_grade_and_bonus(), build_prompt(), build_trade_history_index(), call_ai_batch(), compute_trade_history_penalty(), evaluate_triggers(), fetch_daily_triggers(), fetch_news_headlines() (+19 more)

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

### Community 70 - "resolve_position_price"
Cohesion: 0.13
Nodes (17): Pure pricing helpers for the dashboard API.  Deliberately dependency-free: no Fa, Decide which price to display for an open position, and name the source.      IB, resolve_position_price(), make_pos(), parametrize, Price-source resolution for dashboard position values.  The dashboard cannot rea, FMP returns 0.0 for an unknown or delisted symbol. Displaying that as a, Cost basis is not a market price -- it drives unrealized P&L to exactly (+9 more)

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
Cohesion: 0.09
Nodes (25): _make_ib(), _make_pos(), _make_sb(), tests/test_prove_it_stop.py  Tests for the Prove-It Stop — the single loss rule, Unproven positions anchor to entry, and the band widens after day 0., The day-0 band must NOT still apply on day 1.          This is the regression th, Unlike every rule it replaced, Phase 1 has no day window.          The Thesis St, A proven position that reached the arming gain never becomes a real loss. (+17 more)

### Community 77 - "AAPL"
Cohesion: 0.50
Nodes (4): end, rows, start, AAPL

### Community 80 - "Cooling-off is 3 days — measured, not inherited"
Cohesion: 0.22
Nodes (9): 0 days is worse, 7 days is measurably wrong, Confound, stated plainly, Consequences, Context, Cooling-off is 3 days — measured, not inherited, Decision, Evidence (+1 more)

### Community 83 - "test_decision_review.py"
Cohesion: 0.08
Nodes (42): check_preconditions(), closed_trade_count(), _env(), is_due(), load_registry(), main(), matured_trigger_count(), _notify_telegram() (+34 more)

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

### Community 92 - "ai_value_replay.py"
Cohesion: 0.11
Nodes (30): _build_trade(), _cache_path(), _disable_tls_verification(), eligible_candidates(), _env(), _fetch_forward_bars(), _fmt_usd(), forward_outcome() (+22 more)

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

### Community 98 - "test_scale_out.py"
Cohesion: 0.14
Nodes (21): _make_fill(), _make_ib(), _make_pos(), _make_sb(), _make_scale_ib(), parametrize, tests/test_scale_out.py  Tests for the Partial Scale-Out rule — the winner->lose, A position whose peak reaches +4% is scaled out 33%. (+13 more)

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
Cohesion: 0.05
Nodes (64): _coerce_column(), fetch_table(), main(), notify_failure(), DataFrame, Path, supabase_backup.py  Weekly point-in-time export of every Supabase table to flat, Return every row of `table`, paginated and deterministically ordered.      Raise (+56 more)

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

### Community 134 - "test_shadow_worker.py"
Cohesion: 0.10
Nodes (39): fmp_symbol(), Translate dotted A/B share classes, not exchange suffixes or broker symbols., effective_config(), Same captured shape as live instrumentation, without importing its daemon., build_seed(), PublicMarketData, Expose only SELECT operations on live/source tables, with bounded pagination., ReadOnlySources (+31 more)

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

### Community 141 - "test_research_diagnostics.py"
Cohesion: 0.25
Nodes (18): emit(), Start one logger per process; repeated calls return the original handle., start(), message(), test_async_start_idempotence_and_schema(), test_at_least_once_retry_preserves_diagnostic_id(), test_bounded_close_persists_queue(), test_close_persists_coalesced_counts() (+10 more)

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
Cohesion: 0.14
Nodes (10): _bars(), tests/test_trigger_outcomes.py  Tests for the weekly forward-return backfill.  W, A raw +5% in a +5% market is not edge., Up 5% while SPY is up 10% is underperformance, and must read so., Ascending daily bars on consecutive calendar days., The single most consequential choice in this file., A missing horizon must be NULL. Zero would be read as 'flat'., TestBenchmarkAlpha (+2 more)

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

### Community 195 - "test_exit_rule_replay.py"
Cohesion: 0.29
Nodes (18): bar(), parametrize, Offline regression guards for the legacy historical exit experiments., scale_config(), test_armed_full_exit_prevents_later_scale(), test_armed_gap_after_first_bar_fills_at_open(), test_breakeven_floor_only_applies_after_scale_bar(), test_disabled_scale_does_not_tighten_floor() (+10 more)

### Community 196 - "EBAY"
Cohesion: 0.50
Nodes (4): end, rows, start, EBAY

### Community 197 - "ECL"
Cohesion: 0.50
Nodes (4): end, rows, start, ECL

### Community 198 - "ECO"
Cohesion: 0.50
Nodes (4): end, rows, start, ECO

### Community 199 - "trigger_audit.py"
Cohesion: 0.27
Nodes (10): trigger_audit.py  Point-in-time archive of breakout triggers and the buy/skip de, Record one buy/skip verdict against one trigger.      `decision` is BOUGHT or SK, Record the same verdict against many triggers.      Used when the portfolio is a, Chunked, idempotent, non-fatal upsert., Archive `daily_triggers` rows to the append-only `trigger_history`.      MUST be, record_decisions_bulk(), record_trigger_decision(), save_trigger_history() (+2 more)

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
Cohesion: 0.06
Nodes (11): _load_exit_context_suffix(), parametrize, _ratchet_pos(), Tests for the exit-context recorded against a reconciled broker exit.  When an I, Mirrors the regexes in frontend/src/lib/exitDetails.js. If the agent's         f, The helper is worthless if the reconcile path stops calling it., Import the helper without importing execution_agent itself.      execution_agent, The stored HWM is refreshed on a 15-minute cycle; a resting order is not.      A (+3 more)

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
Nodes (25): est_days_to_lock(), scoring.py — Pure scoring functions for the 5-component final_score system.  No, Trading days to reach the +5% profit lock at the average ATR pace.      Returns, Classify a candidate's ATR against the stop ladder it will actually trade., volatility_fit(), _prompt_source(), parametrize, test_volatility_fit.py — pins the volatility-fit classifier and the redefined `e (+17 more)

### Community 223 - "Forward-return backfill for trigger_history (and the prune we did NOT build)"
Cohesion: 0.22
Nodes (8): Consequences, Context, Conventions (the part that is easy to get silently wrong), Decision, Forward-return backfill for trigger_history (and the prune we did NOT build), Guards, Rejected: the 6-month rolling prune, Verification

### Community 224 - "Backups"
Cohesion: 0.14
Nodes (14): A failed export is not a retained partial backup, Adding a table, Backups, Getting a CSV, Layout, Offsite, Parquet only, Private exit-shadow observations (+6 more)

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

### Community 229 - "App.jsx"
Cohesion: 0.15
Nodes (12): App(), describeApiFailure(), BreakoutDetailPanel(), BreakoutsView(), BreakoutTable(), sortByConviction(), ScreenerView(), SettingsView() (+4 more)

### Community 230 - "Broker"
Cohesion: 0.10
Nodes (33): _confirmed_exit_fill(), _place_sell(), Price only this operation's executions, after cancelling and proving flat., Place a marketable limit SELL for the given position.      Uses IBKR delayed pri, Broker, offline(), position(), fixture (+25 more)

### Community 231 - "market_gate_bt.py"
Cohesion: 0.20
Nodes (13): _closes(), _fetch(), gate_series(), main(), market_gate_bt.py — backtest the CAN SLIM 'M' (market-direction) gate.  Replays, Return a dict of activity / profit / drawdown / insurance metrics.      Equity m, date -> close, ascending by date., Per-index (above_buffer, slope_ok) booleans aligned to `dates`. (+5 more)

### Community 232 - "2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing"
Cohesion: 0.20
Nodes (9): 2026-08-14 — Schema guard: block new buys when a risk rule's columns are missing, Consequences, Context, Decision, Evidence for preferring the close latch, Files, Follow-up, Related change: the migration backfill was itself unsafe (+1 more)

### Community 233 - "Early Loss Kill-switch: tighten to 1% and restrict to the entry day"
Cohesion: 0.18
Nodes (11): 1. The window matters far more than the threshold, 2. Arming beats selling, in every family, 3. Nothing beat the plain percentage rule, Consequences, Context, Decision, Early Loss Kill-switch: tighten to 1% and restrict to the entry day, Findings (+3 more)

### Community 234 - "DashboardView.jsx"
Cohesion: 0.17
Nodes (21): activeProfitLockTier(), DashboardView(), daysHeld(), ExitConditionsPanel(), formatDate(), _getHolidays(), _holidayCache, LifecycleCell() (+13 more)

### Community 235 - "TradesView.jsx"
Cohesion: 0.19
Nodes (18): date(), BENCH_COLORS, BenchmarkAnalyzer(), ReturnsView(), buyDateKey(), commissionKey(), exitLabelKey(), netPnLKey() (+10 more)

### Community 236 - "_placed"
Cohesion: 0.33
Nodes (4): _placed(), _queue_sb(), Supabase mock that keeps exit_requests and portfolio_positions apart., TestQueueBackstops

### Community 237 - "request_exit.py"
Cohesion: 0.48
Nodes (6): _client(), cmd_cancel(), cmd_list(), main(), Best-effort reference price for the confirmation preview only., _ref_price()

### Community 238 - "Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Route the discretionary Day 7+ exits through the Smart OCA queue — and only those, Guard, Left alone deliberately, Problem

### Community 239 - "Fall back to a labelled FMP quote on the dashboard, not to cost basis"
Cohesion: 0.25
Nodes (7): Alternatives rejected, Consequences, Context, Decision, Fall back to a labelled FMP quote on the dashboard, not to cost basis, Files, Why labelling is what makes this acceptable

### Community 240 - "Decision"
Cohesion: 0.22
Nodes (8): 1. Hard volume surge gate in `execution_agent.py`, 2. PRE_BREAKOUT 52W pivot distance gate in `execution_agent.py`, 3. AI prompt penalty rules in `ai_evaluator.py`, 4. D-veto threshold raised from 30 → 50, ADR: Buy Gate Hardening — Volume Surge Floor, PRE_BREAKOUT Pivot Distance, AI Penalty Rules, Consequences, Context, Decision

### Community 241 - "Market direction gate retune: SPY-only, 0.5% buffer (drop QQQ, tighten band)"
Cohesion: 0.12
Nodes (14): Consequences, Context, Dashboard consistency, Decision, Evidence, Follow-ups, Market direction gate: SPY+QQQ, 1% buffer, non-falling SMA-200, fail-closed, Consequences and caveats (+6 more)

### Community 242 - "test_exit_timestamp.py"
Cohesion: 0.09
Nodes (11): Tests that a closed position records WHEN it closed, to the precision available., The behaviour all of the above exists to produce., A precise timestamp is worthless if it stops reaching the row., Tier 1 reads `ibkr_fills`, written from `execution.time.isoformat()`., Tier 2 reads ib_insync executions, whose `.time` is tz-aware UTC., Tier 3 is the one path that must NOT gain a time.      Flex `dateTime` has no ti, TestFlexStaysDateOnlyOnPurpose, TestHoldingPeriodIsNoLongerNegative (+3 more)

### Community 244 - "Decision: Scope the volume surge gate to confirmed breakouts only"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Scope the volume surge gate to confirmed breakouts only, Guard, Observed damage — 2026-08-19, Problem

### Community 245 - "_call"
Cohesion: 0.16
Nodes (5): _call(), _client_ok(), tests/test_sell_state.py  Tests for the sell-state transition notifier — a conci, TestMaybeNotifySellState, TestSellStateCode

### Community 246 - "Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price"
Cohesion: 0.29
Nodes (6): Consequences, Decision, Decision: Anchor the OCA upper leg to current price × ATR, not to the entry price, Guard, Polling, not LISTEN/NOTIFY, Problem

### Community 248 - "_Query"
Cohesion: 0.07
Nodes (21): _broker_position(), _held(), _make_filling_ib(), _Query, test_buy_decision_golden.py — Phase 0 characterization ("golden") tests for the, An IB whose placeOrder returns a fully-Filled trade for the order's whole     qu, Captures the ordered (ticker, decision, reason_code) sequence the live     path, A minimal BREAKOUT trigger row good enough to clear every non-target gate     (v (+13 more)

### Community 249 - "The Prove-It Stop — one loss rule replaces five"
Cohesion: 0.13
Nodes (15): Addendum, 2026-09-04 — Phase 1 enforcement side, measured, Consequences, Context, Decision, Frontend, Kept, with a changed job, Mechanism, Phase 1 — unproven. Anchor to ENTRY. (+7 more)

### Community 250 - "test_oca_managed_exit.py"
Cohesion: 0.29
Nodes (7): _broker(), tests/test_oca_managed_exit.py — Smart OCA Managed Exit.  The dangerous property, Give a mock IB an account with readable NetLiquidation.      No live loss rule d, A buy_date exactly `n` trading days before today, in New York.      Must be comp, TestLadderSuspension, _trading_days_ago(), _with_equity()

### Community 251 - "HWM profit-lock after the first leg"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, Follow-up, HWM profit-lock after the first leg

### Community 252 - "retired_pre_proveit_config"
Cohesion: 0.17
Nodes (12): day0_configs(), headline_configs(), ladder_configs(), p1ratchet_configs(), ratchet_configs(), The RETIRED pre-2026-09-04 ruleset. NOT what the agent runs today.      ⚠️  This, The comparisons that decided the shipped parameters, plus neighbours., Day-0 mechanism test: bot-polled-then-armed vs a resting broker stop.      The s (+4 more)

### Community 253 - "Retired Code Registry"
Cohesion: 0.09
Nodes (22): 2026-09-17 — All-or-nothing outcome writing (RELOCATED, not deleted), 2026-09-18 — Marker-only log shipping (opt-in capture), 2026-09-18 — the Phase 1 trailing backstop (its ratcheting anchor), 2026-09-30 - Portfolio-only short detection (relocated), 2026-09-30 - Relocated strategy declarations and shared market-direction calculation, 2026-09-30 - Share-only reconciliation and scale-out resizing after unexplained fills, 2026-09-30 - Unconditional live-agent startup during deployment, 2026-09-30 - Unconfirmed sell replacement and independently transmitted exit legs (+14 more)

### Community 254 - "Early Dollar Stop becomes slot-derived, not a flat dollar amount"
Cohesion: 0.18
Nodes (11): 1. The measured problem, 2. The design flaw the parameter was hiding, 3. Why the rule is kept rather than removed, Consequences, Context, Decision, Early Dollar Stop becomes slot-derived, not a flat dollar amount, Fail-safe (+3 more)

### Community 255 - "TestTheDetectorActuallyDetects"
Cohesion: 0.09
Nodes (16): _migration_files(), parametrize, Path, Every migration must be safe to run twice.  WHY THIS EXISTS --------------- Noth, Guard against the scanner silently passing everything.      A test that only eve, The twr PK step is guarded in PL/pgSQL, not by IF NOT EXISTS., These files quote the failing SQL in their own explanatory headers., Pin the two files repaired on 2026-09-17 so they cannot regress. (+8 more)

### Community 256 - "TestEqualWeightCapPure"
Cohesion: 0.12
Nodes (6): parametrize, The exact numbers from the incident: $37,916 into 1 slot must be         capped, When cash-per-slot is already under the equal-weight share, the base         all, equity<=0 means NetLiquidation was unavailable. The cap must be         SKIPPED, The core invariant: whenever equity is known, no allocation may EVER         exc, TestEqualWeightCapPure

### Community 257 - "atr_rank_bt.py"
Cohesion: 0.10
Nodes (36): apply_ranking(), atr_pct_at(), build_with_atr(), describe(), entry_stop_for(), main(), rank_atr_band(), rank_atr_boost() (+28 more)

### Community 258 - "enrich_trades"
Cohesion: 0.14
Nodes (15): enrich_trades(), Commission-aware P&L.  `trade_history.profit_loss` is and remains GROSS -- (sell, (total_commission, complete) for one closed trade.      `complete` is True only, (net_profit_loss, complete) for one closed trade.      When commissions are unkn, Attach `net_profit_loss` / `total_commission` / `commission_complete`., Portfolio-level realised P&L, gross and net, with an explicit count of how     m, summarize_realized(), _to_float() (+7 more)

### Community 259 - "Daily unfilled-slot alert spammed because its dedup latch could not be written (RLS)"
Cohesion: 0.29
Nodes (6): Consequences, Context, Daily unfilled-slot alert spammed because its dedup latch could not be written (RLS), Decision, What was actually wrong, Why the backstop as well as the migration

### Community 260 - "Closed-loop learning from realised outcomes"
Cohesion: 0.33
Nodes (5): Closed-loop learning from realised outcomes, Consequences, Context, Decision, Docs sync

### Community 261 - "Prove-It unarmed window: measured, and deliberately left open"
Cohesion: 0.20
Nodes (9): Caveat on the evidence, Consequences, Context, Decision, Prove-It unarmed window: measured, and deliberately left open, The honest answer to "cap my loss at $300 per trade", The losses are gaps, not stop levels, The trade that prompted this (+1 more)

### Community 262 - "rs_percentile_review.py"
Cohesion: 0.32
Nodes (11): analyse(), _env(), fetch_rows(), _fmt(), main(), _mean(), _num(), _pearson() (+3 more)

### Community 263 - "Decision: Replace get_available_cash with margin-safe functions"
Cohesion: 0.07
Nodes (26): Decision, Decision: Replace get_available_cash with margin-safe functions, Files changed, Problem, Why not just fix AvailableFunds?, After hours, Agent side, Consequences (+18 more)

### Community 264 - "test_strategy_validate.py"
Cohesion: 0.12
Nodes (23): _closed(), drop_top_k(), _expectancy(), main(), _net(), _pctl(), _percentile_of(), random_null_distribution() (+15 more)

### Community 265 - "Exit detail panel, and the removal of fabricated exit reasons"
Cohesion: 0.22
Nodes (8): 1. The labels were partly invented, 2. Half the exits recorded no numbers at all, Consequences, Context, Decision, Exit detail panel, and the removal of fabricated exit reasons, Guards, Not done

### Community 266 - "evaluate_exit"
Cohesion: 0.18
Nodes (24): evaluate_exit(), Return the live per-cycle exit verdict for one position — pure, no I/O.      Rep, _ctx(), _monitor_kinds_by_ticker(), test_exit_core.py — unit + parity tests for the pure exit-decision module.  Two, Run the LIVE monitor over the scripted book; return {ticker: {kinds}}., The pure core's verdict must imply the SAME money-path action the live     monit, Guard the parity test is non-vacuous: the book must produce at least one     ARM (+16 more)

### Community 267 - "Relative strength is measured but not ranked — shadow `rs_percentile` column"
Cohesion: 0.20
Nodes (9): Alternatives rejected, Consequences, Context, Decision, Design notes, Every available sample says the repair might point the wrong way, Relative strength is measured but not ranked — shadow `rs_percentile` column, See also (+1 more)

### Community 268 - "Backtests model commission and slippage (fidelity item #3)"
Cohesion: 0.33
Nodes (5): Alternatives considered, Backtests model commission and slippage (fidelity item #3), Consequences, Context, Decision

### Community 269 - "Exit-parameter review on 48 closed trades: shipped Prove-It confirmed, cliff fix rejected, nothing changed"
Cohesion: 0.22
Nodes (8): A harness caveat worth recording, Consequences, Context, Decision, Exit-parameter review on 48 closed trades: shipped Prove-It confirmed, cliff fix rejected, nothing changed, Findings, against the four questions a review must answer, The owed re-run, discharged, What was measured

### Community 270 - "paired_block_bootstrap"
Cohesion: 0.27
Nodes (12): cagr_from(), paired_block_bootstrap(), Corrected bootstrap for config comparisons.  THE BUG (boot.py):     diffs = cagr, Draw circular blocks with Geometric(1/mean_len) lengths until >= ndays., Return (median diff, 5th, 95th, P(a>b)) for CAGR_a - CAGR_b., Reproduces the ORIGINAL (buggy) method, for comparison only., RNG, single_ci() (+4 more)

### Community 271 - "research_diagnostics.py"
Cohesion: 0.10
Nodes (24): close(), _context(), credential_state(), Diagnostics, _identifier(), _key_state(), _now(), _post() (+16 more)

### Community 272 - "Fail loudly on Telegram when the agent cannot reach IBKR"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, Fail loudly on Telegram when the agent cannot reach IBKR, Not addressed here

### Community 273 - "Disable the breakout failure penalty — it scored every trade identically"
Cohesion: 0.22
Nodes (8): Consequences, Context, Decision, Disable the breakout failure penalty — it scored every trade identically, Follow-ups, Measurement, Why disabling does not need more trades first, Why it fails, mechanically

### Community 274 - "AI evaluator: replace the velocity ladder with volatility fit"
Cohesion: 0.17
Nodes (11): AI evaluator: replace the velocity ladder with volatility fit, Consequences, Context, Correction: the slot-width framing above is too strong, Decision, Is CAN SLIM's 20-25% target simply wrong?, Measurement, One result that IS clean (+3 more)

### Community 275 - "entry_bt.py"
Cohesion: 0.19
Nodes (14): coverage(), daily(), manifest(), Read-only loader for the committed benchmark price dataset.  The dataset lives i, Daily bars ascending by date, or None if the symbol is not in the dataset., Sorted union of dates across the given symbols (default: all)., symbols(), _table() (+6 more)

### Community 276 - "compute_final_score"
Cohesion: 0.10
Nodes (12): compute_final_score(), Weighted blend of 5 components (all 0-100) -> 0-100 final score.        Technica, TestPreBreakoutScoreBoost, The load-bearing guarantee: this feature cannot change a trade., TestLiveScoringIsUntouched, NVDA-like scores -> should be around 80, SGHC-like scores -> should be around 40-50, Weighted formula: tech=100, rest=0 -> score = 30 (+4 more)

### Community 277 - "Sell-state transition notifications"
Cohesion: 0.25
Nodes (7): Alternatives rejected, Consequences / known limitations, Context, Decision, Sell-state transition notifications, Silent first observation, Why persist the state instead of comparing in memory

### Community 278 - "NBIX sell price was reconstructed from the wrong day's FMP quote"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, NBIX sell price was reconstructed from the wrong day's FMP quote, Open questions, Schema notes discovered while applying this, What went wrong

### Community 279 - "_client"
Cohesion: 0.05
Nodes (24): check_schema(), _probe(), _probe_writable(), Startup schema assertion — fail LOUD when a risk rule's columns are missing.  WH, True if the table (and column, if given) is queryable., True if a row can actually be INSERTed into `table`.      A read probe cannot an, Probe every object a risk rule or archive depends on., SchemaReport (+16 more)

### Community 280 - "TeeLogger"
Cohesion: 0.11
Nodes (15): flush_logs_quietly(), flush_logs_to_supabase(), _purge_agent_logs(), Persistent log tee + Supabase log shipping/purge, extracted from execution_agent, Delete execution_YYYY-MM-DD.log files older than KEEP_DAYS.          Uses the da, Buffer complete log lines. Never performs network I/O.          print() issues t, Mirrors stdout to a daily rotating log file without touching print() calls., Map a line to a retention/filter tier.          Order matters: the checks run mo (+7 more)

### Community 281 - "compute_exit_shadows"
Cohesion: 0.17
Nodes (19): prove_it_is_proven(), Has this position ever CLOSED above the price we paid?      This single question, compute_exit_shadows(), _live_would_exit(), _q1_arm3(), _q2_trail5(), exit_shadow.py — side-effect-free shadow evaluation of candidate exit rules.  Re, Would the LIVE Prove-It rule fire this cycle? (rule-level, independent of     wh (+11 more)

### Community 283 - "Backtesting"
Cohesion: 0.14
Nodes (14): 0. Daily strategy backtest using current exit-rule primitives, 1. Web strategy backtester (dashboard — daily approximation), 2. Exit replay — against your own real trades, 3. Validation harness — is a strategy backtest result REAL?, 4. Research harnesses, Backtesting, Caveats that apply to all of them, Fidelity — read before trusting a dollar figure (+6 more)

### Community 284 - "test_telegram_notifier.py"
Cohesion: 0.08
Nodes (37): patch, The disconnect alert uses its own cache key: an unrelated notify_exception     m, Ticker text must be escaped before sending HTML parse_mode Telegram., A non-200 from the Telegram API must be reported, not swallowed.      Now carrie, One blip is a warning; a run of them is an outage and must say so., Two recipients, one broken: that is a fault, not a rounding error., The dedup bug: two recipients, one broken. The operator DID see the alert     on, A TOTAL delivery failure (no recipient got it) must NOT latch — the summary (+29 more)

### Community 285 - "Commission accounting, and the RLS policy gap that hid it"
Cohesion: 0.25
Nodes (7): 1. `ibkr_fills` had been empty since the day it was created, 2. Reported P&L was gross, and the dashboard already knew, Commission accounting, and the RLS policy gap that hid it, Consequences, Context, Decision, Files

### Community 286 - "compute_pre_breakout_quality_score"
Cohesion: 0.15
Nodes (11): check_pre_breakout_coil(), compute_pre_breakout_quality_score(), Quality score 0-100 for a pre-breakout (coiling) trigger.      Weights:       Pi, Detects stocks coiling toward an imminent breakout (VCP / handle setup).      AL, Within 1%, 0 vol ratio, 3/3 closes up -> score == 100., Within 1%, 0.5x vol, 3 closes up -> 40+20+20=80., Within 3%, 0.5x vol, 2 closes up -> 35+20+10=65., Within 5%, 0.8x vol, 2 closes up -> 28+int(0.2*40)+10=28+8+10=46 (rounding gives (+3 more)

### Community 287 - "test_startup_crash_shipping.py"
Cohesion: 0.13
Nodes (13): main(), Best-effort single-row insert of a startup traceback into agent_logs.      Never, _ship_startup_crash(), _FakeClient, _FakeTable, _install_fake_supabase(), Tests for the fatal-safe entrypoint and the execution_agent_ref proxy.  Covers t, A deliberate sys.exit (e.g. missing FMP_API_KEY) is not a crash to ship. (+5 more)

### Community 288 - "ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5"
Cohesion: 0.40
Nodes (5): ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5, Consequences, Context, Decision, Simulation

### Community 290 - "All migrations renamed to `YYYYMMDD_slug.sql`"
Cohesion: 0.29
Nodes (6): All migrations renamed to `YYYYMMDD_slug.sql`, Consequences, Context, Decision, How each date was derived, Notes

### Community 291 - "test_calibration_worker.py"
Cohesion: 0.08
Nodes (30): frozen(), isolate_diagnostic_analytics(), MemoryStore, fixture, parametrize, setup(), stamp(), test_approved_rule_request_is_linked_and_not_silently_deployed() (+22 more)

### Community 292 - "TestHardStopPrice"
Cohesion: 0.09
Nodes (11): _armed_floor(), _disaster(), tests/test_hard_stop.py  Tests for the static broker-side hard stop — the discon, A SELL stop resting at or above the market triggers immediately and sells at, THE REGRESSION. Moving the Phase 1 floor from the disaster level to         the, Unproven -> the Phase 1 band ITSELF, held STATIC.          The resting STP sits, The change this locks in: the Phase 1 resting STP sits AT the band,         NOT, THE REGRESSION THIS FIXES. Whatever the position does, the Phase 1         floor (+3 more)

### Community 293 - "A static broker-side hard stop that survives disconnection"
Cohesion: 0.20
Nodes (9): A static broker-side hard stop that survives disconnection, Backtest evidence, Consequences, Context, Decision, Provisional / to re-test, The silent gap the disconnect incident exposed, Why not just make the trailing stop tighter (+1 more)

### Community 294 - "Partial Scale-Out — book a third of a winner at +4%"
Cohesion: 0.18
Nodes (10): Alternatives rejected, Consequences / known limitations, Context, Decision, Order-execution safety, Partial Scale-Out — book a third of a winner at +4%, Provisional status, The insight (+2 more)

### Community 295 - "The exit-parameter review becomes cron-backed instead of a date in a markdown table"
Cohesion: 0.33
Nodes (5): Alternatives rejected, Consequences, Context, Decision, The exit-parameter review becomes cron-backed instead of a date in a markdown table

### Community 296 - "Fail loudly when the database is unreachable"
Cohesion: 0.33
Nodes (5): Alternatives rejected, Consequences, Context, Decision, Fail loudly when the database is unreachable

### Community 297 - "Reconcile fill window floored at entry; honest trail-trigger display"
Cohesion: 0.25
Nodes (7): Bug 1 — sell-price contamination, Bug 2 — misleading trail-trigger display, Consequences, Context, Decision, Reconcile fill window floored at entry; honest trail-trigger display, Validation

### Community 298 - "Archive entry-decision provenance onto closed trades (`trade_history`)"
Cohesion: 0.33
Nodes (5): Archive entry-decision provenance onto closed trades (`trade_history`), Consequences, Context, Decision, Why this matters: the AI on/off A/B replay

### Community 299 - "backfill_trigger_outcomes.py"
Cohesion: 0.26
Nodes (11): compute_outcomes(), fetch_pending(), fetch_prices(), main(), _pct(), backfill_trigger_outcomes.py  Weekly job that links archived breakout triggers t, Daily OHLCV ascending, or [] on failure. Never raises., Forward returns measured from the first session AFTER triggered_at.      CONVENT (+3 more)

### Community 300 - "COHU"
Cohesion: 0.50
Nodes (4): end, rows, start, COHU

### Community 301 - "Telegram delivery health: make a dead alert channel observable"
Cohesion: 0.15
Nodes (12): Audit performed alongside, Consequences, Context, Decision, See also, Telegram delivery health: make a dead alert channel observable, What this does NOT establish, Why a startup self-test cannot be fatal (+4 more)

### Community 302 - "test_research_diagnostics_integration.py"
Cohesion: 0.14
Nodes (14): main(), Start cloud diagnostics before importing a production service., emissions(), PermissionFailure, Exception, fixture, parametrize, Research outages remain visible without their private database or a broker. (+6 more)

### Community 303 - "Multi-account IBKR pricing: reqPnLSingle fallback + strict target-account scoping"
Cohesion: 0.33
Nodes (5): Alternatives rejected, Consequences, Context, Decision, Multi-account IBKR pricing: reqPnLSingle fallback + strict target-account scoping

### Community 304 - "CRM"
Cohesion: 0.50
Nodes (4): end, rows, start, CRM

### Community 305 - "insert_trade_history"
Cohesion: 0.07
Nodes (17): FakeClient, The trade_history insert must never lose a trade to a long reason string.  Every, The entry-decision grade must survive the position row it was written on,     so, Mimics Supabase: raises on overflow rather than truncating., _Table, TestClampReason, TestEntryProvenance, TestInsertTradeHistory (+9 more)

### Community 306 - "test_agent_image_completeness.py"
Cohesion: 0.24
Nodes (8): _copied_modules(), _import_closure(), _local_modules(), The agent container must contain every module execution_agent.py imports.  WHY T, Every project-local module reachable from `entry` by static import., Regression: its absence silently disabled Tier 3 sell-price recovery.          N, Guards the guard: if the closure walker silently returned very little,         t, TestAgentImageCompleteness

### Community 307 - "test_no_secrets_committed.py"
Cohesion: 0.32
Nodes (7): _parse_env_template(), parametrize, Path, Guard: no real secret may live in a tracked file.  This test is the standing enf, test_env_template_secret_is_a_sentinel(), test_no_leaked_credential_literal_in_any_tracked_file(), _tracked_text_files()

### Community 308 - "intraday_reporting.py"
Cohesion: 0.12
Nodes (35): _attempt_notification(), build_report(), checkpoint_metrics(), deliver(), Persist each recipient/chunk independently; retry only missing receipts., safe_detail(), due_periods(), expected_market() (+27 more)

### Community 309 - "Weekly-backup ship step: trust the host key on connect, not via a separate scan"
Cohesion: 0.22
Nodes (8): Cause 1 — the latest run (2026-09-27): missing table, export step, Cause 2 — runs #2–#5 (2026-08-23 … 2026-09-13): the rsync ship step, Consequences, Decision, Problem, Weekly-backup ship step: trust the host key on connect, not via a separate scan, What would make this a real pin (not done here), Why this is not a security downgrade

### Community 310 - "force_buy.py"
Cohesion: 0.14
Nodes (17): config.py — single source of truth for cross-module trading parameters.  Every v, get_ibkr_price(), main(), _place_buy(), IB, force_buy.py — One-off manual buy trigger (bypasses 9:30 AM time gate).  Run thi, Place a marketable limit BUY order for a trigger.      Uses IBKR delayed ask + $, Fetch price via IBKR delayed market data (same as execution_agent buy path). (+9 more)

### Community 312 - "CAH"
Cohesion: 0.50
Nodes (4): end, rows, start, CAH

### Community 314 - "Split execution_agent.py along its pure/impure seam"
Cohesion: 0.20
Nodes (9): A latent production bug this surfaced, Consequences, Constants move with the logic they govern, Context, Cross-module patching: `patch_everywhere()`, Decision, Split execution_agent.py along its pure/impure seam, What made this dangerous (+1 more)

### Community 315 - "CalibrationResearchView.jsx"
Cohesion: 0.12
Nodes (30): artifact, download, experiment, now, policy, proposal, settings, CalibrationResearchView() (+22 more)

### Community 317 - "Migrations must be re-runnable"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Migrations must be re-runnable, Related, Verification

### Community 318 - "Recorder"
Cohesion: 0.16
Nodes (10): emit(), Producer-safe state only: never acquire a logging handler or do I/O., Worker-only, coalesced reporting; no producer/queue lock is held., Called under the short sequence lock, never during disk/network I/O., Account for every accepted event, including when the disk is unusable., Enrich durable seeds from every run, atomically marking each result., Fail only at the optional instrumentation boundary, never in trading., Recorder (+2 more)

### Community 321 - "The replay truncated at the real exit, so "hold longer" was unmeasurable; power hold is unreachable because the ladder sells first"
Cohesion: 0.20
Nodes (9): Consequence, Context, Decision, Evidence, Power hold is unreachable — and the ladder is why, The loosening direction, scored honestly, The methodological flaw, The replay truncated at the real exit, so "hold longer" was unmeasurable; power hold is unreachable because the ladder sells first (+1 more)

### Community 322 - "Write each forward-return horizon as soon as it matures"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Related, Results, The 20d-named path metrics are withheld until 20 sessions exist, Write each forward-return horizon as soon as it matures

### Community 324 - "_bars"
Cohesion: 0.36
Nodes (4): _bars(), n consecutive sessions, rising steadily so returns are easy to reason about., A 5-bar drawdown must never be stored as a 20-bar drawdown., TestPartialMeasurement

### Community 325 - "test_backfill_outcomes.py"
Cohesion: 0.21
Nodes (7): _FakeClient, patched(), fixture, Per-horizon outcome measurement in backfill_trigger_outcomes.py.  WHY THIS EXIST, Run the pipeline against synthetic prices and a fake Supabase., A row 10 days old was previously invisible; it has a valid fwd_5d., TestSelection

### Community 326 - "Register preconditions: separating "is it time?" from "is it safe?""
Cohesion: 0.20
Nodes (9): Alternatives rejected, Consequences, Context, Decision, Register preconditions: separating "is it time?" from "is it safe?", See also, Why a blocked item still opens an issue, Why `None` age is not zero (+1 more)

### Community 327 - "_emit"
Cohesion: 0.15
Nodes (13): _emit(), Mimic print(): body then newline as separate write() calls., Purging on every 15-minute flush is three wasted queries per cycle., The whole point of shipping everything: context, not just errors., Separators triple the row count and carry no information., test_blank_and_decoration_lines_are_skipped(), test_buffer_is_bounded_and_reports_drops(), test_flush_never_raises_when_supabase_is_down() (+5 more)

### Community 328 - "Sell reasons derive the stop anchor from the fill, not the stored peak"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Sell reasons derive the stop anchor from the fill, not the stored peak, Verification, What this does not fix

### Community 329 - "jackknife"
Cohesion: 0.24
Nodes (14): jackknife(), Leave-one-out fragility test of every challenger against `baseline`.      The st, report_jackknife(), _cfg(), ExitConfig, fixture, Arithmetic guards for the leave-one-out jackknife in exit_rule_replay.  The jack, Deterministic delta lookup keyed by (config label, trade ticker+hour). (+6 more)

### Community 330 - "Phase 1 rests on a STATIC stop, not a ratcheting trailing order"
Cohesion: 0.18
Nodes (10): A deployment hazard this exposed, and the guard added for it, Consequences, Context, Decision, Deliberately NOT extended to proven-but-unarmed positions, Follow-ups, Measurement, Phase 1 rests on a STATIC stop, not a ratcheting trailing order (+2 more)

### Community 332 - "Reason-aware cooling-off: loss exits block, profit exits don't"
Cohesion: 0.22
Nodes (9): Consequences, Conservative edges, Context, Decision, Evidence, Live re-entries, split by why the PRIOR sale happened (66 closed trades), Open questions, Portfolio backtest (`research/cooloff_bt.py`, 5 slots, 3.09 years) (+1 more)

### Community 333 - "_trig"
Cohesion: 0.09
Nodes (8): test_decision_core.py — unit tests for the PURE entry-decision module.  These ex, TestCapacityCash, TestEligibility, TestMarketGates, TestPriceGates, TestRanking, TestSizing, _trig()

### Community 334 - "test_position_sizing_cap.py"
Cohesion: 0.26
Nodes (8): _placed_qty(), test_position_sizing_cap.py — the equal-weight per-position ceiling.  Regression, Runs the real buy loop with cash AND NetLiquidation injected., The MPC scenario end-to-end: 4 held, 1 free slot, $37,916 free cash,         $11, Regression: the cap must not shrink a normally-sized position. 3 held,         2, If IBKR's NetLiquidation read returns 0, equity is reconstructed from         ca, _run_buys_with_equity(), TestEqualWeightCapIntegration

### Community 335 - "Cap every position at one equal-weight share of equity"
Cohesion: 0.25
Nodes (7): Alternatives considered, Cap every position at one equal-weight share of equity, Consequences, Context, Decision, Root cause, Validation

### Community 336 - "test_log_shipping.py"
Cohesion: 0.14
Nodes (12): parametrize, Tests for TeeLogger's Supabase log shipping and redaction.  This code sits in th, print() emits text and newline separately; a naive impl misses this., The durable file write must not be collateral damage from a capture bug., Unit tests and CI run without the tee; this must be a no-op, not a crash., Other tooling imports this module and parses its stdout as JSON. A     shipping, test_flush_is_safe_without_a_tee_installed(), test_lines_are_classified() (+4 more)

### Community 337 - "AI-value A/B replay harness (does the AI actually pick winners?)"
Cohesion: 0.29
Nodes (6): AI-value A/B replay harness (does the AI actually pick winners?), Alternatives considered, Consequences, Context, Decision, Preliminary reading — NOT a finding, do not cite as settled

### Community 338 - "Earnings blackout, dead news feed fix, and AI news-disqualifier veto"
Cohesion: 0.20
Nodes (9): 1. The AI's news feed had been silently dead, 2. The AI was told imminent earnings were a BONUS, 3. There was no hard guard against a broken story, Alternatives considered, Consequences, Context, Decision, Earnings blackout, dead news feed fix, and AI news-disqualifier veto (+1 more)

### Community 339 - "._send_multi"
Cohesion: 0.25
Nodes (4): Record a failed delivery and leave a greppable marker on stderr.          stderr, Deliver to a single chat. Returns True on confirmed delivery., Send to all configured chat IDs and report delivery two ways.          Returns `, Once-daily summary of why open portfolio slots were not filled.          Sent at

### Community 340 - "notifier"
Cohesion: 0.50
Nodes (4): notifier(), fixture, Returns a configured TelegramNotifier for testing., unconfigured()

### Community 341 - "ibkr_data.py"
Cohesion: 0.11
Nodes (28): _compute_ibkr_price_map(), fetch_historical_closes_with_dates(), get_available_cash(), get_ibkr_account(), get_margin_loan(), get_net_liquidation(), get_own_cash(), _ibkr_avg_cost() (+20 more)

### Community 342 - "_held"
Cohesion: 0.21
Nodes (10): _held(), RLS denies the latch UPSERT → the in-process latch still stops a resend., n stock positions in portfolio_positions (each occupies one slot)., 1 held, 4 idle, nothing sent today → exactly one summary., Dedup ledger already has today's row → no summary., MAX_POSITIONS held → zero idle slots → nothing to report., Bearish 'M' gate → single top-level reason mentioning market direction., Open market, candidates all skipped → bulleted per-reason breakdown. (+2 more)

### Community 343 - "Exit-parameter review on 52 closed trades: shipped stack holds, and the `--proveit` sweep is repaired"
Cohesion: 0.25
Nodes (7): Consequences, Context, Exit-parameter review on 52 closed trades: shipped stack holds, and the `--proveit` sweep is repaired, One candidate to watch, deliberately not shipped, The fix, The more important finding: the sweep could not answer its own question, The review, n = 52 (26 losers, 26 winners)

### Community 344 - "test_sell_safety.py"
Cohesion: 0.16
Nodes (35): _active_sells(), cancel_confirmed_sells(), Cancel this client's sells and require terminal acknowledgements., Authorize one sell or a staged OCA pair against fresh broker inventory., Refresh all clients' orders, retaining local submissions not yet echoed., submit_sell_orders(), broker(), market_sell() (+27 more)

### Community 345 - "Ship noteworthy log lines to Supabase"
Cohesion: 0.29
Nodes (6): Alternatives rejected, Consequences, Context, Decision, Follow-up, Ship noteworthy log lines to Supabase

### Community 346 - "exit_rule_replay.py"
Cohesion: 0.10
Nodes (33): basetrail_configs(), clean_qualifying_rate(), cliff_configs(), _env(), eod_configs(), _fold_cluster(), grid_configs(), load_trades() (+25 more)

### Community 347 - "test_intraday_reporting.py"
Cohesion: 0.07
Nodes (60): at(), FakeIssues, FakeTelegram, frozen_artifact(), healthy(), MemoryStore, offline_calendar(), output_events() (+52 more)

### Community 348 - "test_render_env.py"
Cohesion: 0.11
Nodes (10): Unit tests for scripts/render_env.py — the Bitwarden .env resolver.  No live Bit, Letting the last one win would silently pick a credential at random., Centralising the token is the whole point; a hard-coded app path undoes it., A project id there belongs to whichever app wrote it, not necessarily us., A same-named key in the coach's project must never win., _script(), test_conflicting_duplicates_in_one_project_are_a_hard_error(), test_secret_map_is_scoped_to_this_project() (+2 more)

### Community 349 - "CB"
Cohesion: 0.50
Nodes (4): end, rows, start, CB

### Community 350 - "Ship the full agent log, not just the error lines"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Ship the full agent log, not just the error lines, Two bugs this surfaced, What this does not change

### Community 351 - "test_intraday_observer.py"
Cohesion: 0.08
Nodes (39): Return nonzero on failed diagnostics or exhausted reconnects; retain spool., run(), arguments(), connected(), Event, FakeIB, forbidden(), fixture (+31 more)

### Community 352 - "2026-09-27 — Secrets resolved from Bitwarden at deploy time (`@bws` sentinel)"
Cohesion: 0.33
Nodes (5): 2026-09-27 — Secrets resolved from Bitwarden at deploy time (`@bws` sentinel), Consequences, Context, Decision, Why the deploy delivers the tooling but does not auto-render

### Community 353 - "test_intraday_calibration.py"
Cohesion: 0.08
Nodes (20): candidates(), data(), fixture, parametrize, Offline deterministic calibration on the existing recorded-account fixture., Move the full synthetic capture, including every observed timestamp., rising(), shift_window() (+12 more)

### Community 354 - "The run-on truncation bias also affected the scale-out path"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, The run-on truncation bias also affected the scale-out path, What this does not change

### Community 355 - "test_calibration_risk.py"
Cohesion: 0.14
Nodes (40): calculate_risk(), _daily_series(), _drawdown(), _iso(), _number(), Pure, descriptive risk statistics for frozen calibration replay results.  No net, Return finite JSON metrics and explicit unavailability, without network I/O., Validate the signed, frozen reference without trusting its availability. (+32 more)

### Community 356 - "test_live_rule_replay.py"
Cohesion: 0.11
Nodes (35): data(), eod(), event(), finish(), next_buy(), one_name(), fixture, parametrize (+27 more)

### Community 357 - "EW"
Cohesion: 0.50
Nodes (4): end, rows, start, EW

### Community 358 - "ExitConfig"
Cohesion: 0.16
Nodes (20): Any, ExitConfig, Aggregate one configuration into a comparable result.      `net` is the ALL-IN s, One candidate parameterisation of the early-exit rules., Counterfactual exit for one position under `cfg`: the fill price, the     timest, Score one configuration WITH the slot opportunity cost charged.      baseline, Replay one trade with a partial scale-out plus a rule-driven remainder.      Ret, Record a limit fill only after this bar survives the full-exit checks. (+12 more)

### Community 359 - "Price the lot from its own fills, and make cooling-off broker-aware"
Cohesion: 0.25
Nodes (8): Consequences, Context, Decision, Defect 1 — `averageCost` is not a lot cost basis after a round trip, Defect 2 — cooling-off could not see broker-side sells, Erratum — figures published in the 2026-09-09 drift-guard ADR, Open questions, Price the lot from its own fills, and make cooling-off broker-aware

### Community 360 - "entry_quality_review.py"
Cohesion: 0.28
Nodes (12): auc(), _date(), discriminate(), _env(), _get(), main(), permutation_baseline(), date (+4 more)

### Community 361 - "Entry quality and the missing right tail — an open question, measured not answered"
Cohesion: 0.13
Nodes (14): 1. Blindly holding 20 days loses money, 2. On the trades actually bought, the gain is two tanker stocks, 3. The drawdown-conditional ladder — tested, and rejected, Consequences, Context, Decision, Entry quality and the missing right tail — an open question, measured not answered, The hypothesis that was wrong (+6 more)

### Community 362 - "test_exit_shadow_store.py"
Cohesion: 0.12
Nodes (20): get_client(), Private research-only persistence; never borrows the live trading client., Use the independent diagnostic sink and a redacted console fallback., Return success without allowing research persistence to interrupt exits., report_failure(), write_observation(), query_context(), Extract only an exact known research table from query.path, never filters. (+12 more)

### Community 363 - "agent_logs was unwritable for a day, and the guard said it was fine"
Cohesion: 0.33
Nodes (5): agent_logs was unwritable for a day, and the guard said it was fine, Consequences, Context, Decision, Deployment

### Community 364 - "test_intraday_replay.py"
Cohesion: 0.09
Nodes (8): fixture, Synthetic captured inputs: actual-start semantics without network or live writes, records(), test_complete_regular_sessions_survive_overnight_gateway_restart_and_quote_gap(), test_off_hours_does_not_exempt_manual_or_external_financial_activity(), test_regular_session_gateway_gap_still_rejects(), test_unclassified_off_hours_capture_gap_is_not_silently_discarded(), two_session_raw_records()

### Community 365 - "._eod_monitor"
Cohesion: 0.32
Nodes (5): EOD plateau rotation: at 3:45-4pm, if portfolio is full AND fresh breakout     t, Helper: run monitor in EOD window., Days 3-6 position with decay is NOT swapped if no triggers exist., Days 3-6 position with decay is NOT swapped if portfolio has open slots (not ful, TestPlateauRotation

### Community 366 - "Slot opportunity cost: pricing "let winners run" against the entries a longer hold blocks"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Exit-timestamp plumbing, Slot opportunity cost: pricing "let winners run" against the entries a longer hold blocks, The first result (n = 59 positions, run-on 30 days), The model

### Community 367 - "Cooling-off is return-neutral, not a profit rule — measured, kept at 3 days"
Cohesion: 0.25
Nodes (8): 1. Portfolio simulation (`research/cooloff_bt.py`, new), 2. Live re-entry P&L (Prove-It era, 66 closed trades), Consequences, Context, Cooling-off is return-neutral, not a profit rule — measured, kept at 3 days, Decision, Open questions (carried forward from 2026-09-15, still open), What was measured

### Community 368 - "TestStampingContract"
Cohesion: 0.43
Nodes (3): The stamp is what decides whether a row is ever revisited., A later pass must ADD knowledge, never erase an earlier pass's work., TestStampingContract

### Community 369 - "Phase 1 resting broker STP sits AT the band — IBKR is the primary enforcer"
Cohesion: 0.25
Nodes (7): Consequences, Context — the loss that triggered this, Decision, Honest caveats, Phase 1 resting broker STP sits AT the band — IBKR is the primary enforcer, What was measured, Why this is safe

### Community 370 - "Trade lifecycle — how winners and losers are treated"
Cohesion: 0.22
Nodes (8): 1. Master lifecycle — entry to exit, 2. The exit ladder — evaluated in THIS order, every 15 minutes, 3. Why sells rest on the broker, not in Python — the arm/OCA sequence, Related pages, Shipped thresholds at a glance, The four scenarios, in one line each, Trade lifecycle — how winners and losers are treated, What a closed trade records

### Community 371 - "test_broker_positions.py"
Cohesion: 0.25
Nodes (14): confirmed_stock_positions(), Request a completed position snapshot, retaining zero and short quantities., position(), parametrize, test_completed_snapshot_retains_shorts_and_filters_other_accounts(), test_disconnect_during_request_does_not_authorize_orders(), test_disconnected_snapshot_is_not_empty_account(), test_invalid_snapshot_cannot_authorize_orders() (+6 more)

### Community 372 - "Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py"
Cohesion: 0.29
Nodes (6): Alternatives considered, Centralise STOP_LOSS_PCT and COOLING_OFF_DAYS in config.py, Consequences, Context, Decision, Guard

### Community 373 - "Reconcile `buy_price` against IBKR `averageCost`, and sum the dashboard headline from the rows"
Cohesion: 0.33
Nodes (5): Alternatives considered, Consequences, Context, Decision, Reconcile `buy_price` against IBKR `averageCost`, and sum the dashboard headline from the rows

### Community 374 - "rank_policy_bt.py"
Cohesion: 0.23
Nodes (14): build(), find_triggers(), _indicators(), per_type(), _rank_key(), Counterfactual replay: trigger-RANKING policy A (score-first) vs B (confirmed-fi, Point-in-time SPY 12-week (60 trading day) return, keyed by date., Replay BOTH screener detectors bar-by-bar, using production scoring.      Return (+6 more)

### Community 375 - "run_market_open_buys"
Cohesion: 0.07
Nodes (44): assert_schema_ok(), equity_capped_position_size(), maybe_report_unfilled_slots(), _print_skip(), IB, Market-open buying + schema/position-size gates, extracted from execution_agent., Operator-facing one-liner for a skipped trigger. The authoritative record     is, True when today's unfilled-slot summary has already gone out.      Fails SAFE: o (+36 more)

### Community 376 - "exit_shadow_review.py"
Cohesion: 0.60
Nodes (5): analyse(), _b(), _env(), fetch_rows(), main()

### Community 377 - "Split execution_agent.py orchestrators into focused modules"
Cohesion: 0.20
Nodes (9): 1. The `ea.`-prefix invariant — a static, greppable guarantee, 2. The golden characterization harness, 3. The suite held at its exact baseline, Consequences, Context, Decision, Register, Split execution_agent.py orchestrators into focused modules (+1 more)

### Community 379 - "CalibrationDashboard.jsx"
Cohesion: 0.14
Nodes (32): Benchmarks(), CalibrationDashboard(), Campaign(), captureValid(), count(), detailValid(), downloadEvidence(), flex (+24 more)

### Community 380 - "A leave-one-out jackknife makes "carried by one trade?" arithmetic, not a judgement call"
Cohesion: 0.25
Nodes (7): A leave-one-out jackknife makes "carried by one trade?" arithmetic, not a judgement call, Consequence, Context, Decision, Evidence — first run, 67 closed trades, Scope and non-goals, Why leave-one-out is closed-form here

### Community 382 - "execution_agent.py"
Cohesion: 0.05
Nodes (76): _count_open_positions(), _flush_logs_on_shutdown(), get_supabase_client(), install_shutdown_log_flush(), main(), main_loop(), Register the shutdown flush hooks. Called once, from main_loop()., Best-effort count of open positions for the disconnect alert.      Returns None (+68 more)

### Community 394 - "MonitorRecorder"
Cohesion: 0.15
Nodes (9): _Event, MonitorRecorder, golden_log.py — characterization recorder for execution_agent orchestrators.  WH, Drive monitor_portfolio_intraday over a scripted book and yield a     MonitorRec, One recorded money-path action, normalized to compare across runs., First clause of a sell/arm reason, before any per-run volatile detail     (price, Installs recording spies over the money-path seams and collects a     normalized, _reason_head() (+1 more)

### Community 397 - "calibrate_intraday.py"
Cohesion: 0.19
Nodes (30): CalibrationError, canonical(), console_report(), digest(), effective_settings(), engine_fingerprint(), equity_attribution(), evaluate() (+22 more)

### Community 399 - "fail"
Cohesion: 1.00
Nodes (3): fail(), log(), render_env.sh script

### Community 400 - "run_intraday_reporting_bws.py"
Cohesion: 0.15
Nodes (18): build_secret_map(), main(), Map a Bitwarden project name to its id, or raise LookupError.      The bootstrap, Index secrets by key, scoped to a project and refusing ambiguous keys.      rend, Return the rendered .env text, or raise KeyError listing unmet sentinels., render(), resolve_project_id(), _resolve_project_mode() (+10 more)

### Community 401 - "test_intraday_capture.py"
Cohesion: 0.07
Nodes (33): now(), _durable_seed(), _history_client(), parametrize, quote_response(), test_batch_entitlement_fallback_covers_45_symbols_and_records_provenance(), test_batch_quote_requests_are_at_most_100_symbols(), test_cached_broker_snapshot_is_account_scoped_and_plain() (+25 more)

### Community 402 - "initialize"
Cohesion: 0.10
Nodes (50): export_shadow_dataset(), Explicit shadow export; ordinary observer samples never enter schema2., advance(), checkpoint(), _digest(), _engine(), engine_fingerprint(), export_shadow_dataset() (+42 more)

### Community 403 - "2026-09-28 — Bitwarden `secret list` is scoped to one project (fail-closed)"
Cohesion: 0.40
Nodes (4): 2026-09-28 — Bitwarden `secret list` is scoped to one project (fail-closed), Consequences, Context, Decision

### Community 404 - "test_portfolio_replay_chronology.py"
Cohesion: 0.16
Nodes (12): compute_cooled_map(), Reason-aware cooling-off (re-entry block) — single source for all buy paths.  Th, Return {ticker: reason} for every ticker currently blocked from re-entry.      `, _historical_inputs(), _LedgerClient, parametrize, Offline regressions for opening-only information and historical replay timing., test_historical_entries_do_not_recycle_later_exit_slots() (+4 more)

### Community 405 - "shadow_inputs.py"
Cohesion: 0.08
Nodes (36): calculate_ema(), calculate_sma(), compute_momentum_health_score(), compute_rsi(), detect_candlestick_reversals(), Price-series indicators and the Momentum Health Score.  Extracted verbatim from, Live Momentum Health Score Mₜ (0–100) for a held position.      Returns (score,, Compute Simple Moving Average. (+28 more)

### Community 406 - "test_web_image_completeness.py"
Cohesion: 0.24
Nodes (11): _backend_modules(), _copied_root_modules(), The web/dashboard container must contain every module its code imports.  WHY THI, Regression: backtester's live-exit parity depends on it (Option A)., Guards the guard: if the walker returned nothing the assertion above         wou, Top-level .py modules that live at the repo root (import candidates)., Every ROOT-level module reachable from `entry` by static import, following     b, Root modules explicitly COPY'd into the image (excludes `COPY backend/`). (+3 more)

### Community 407 - "MemoryQuery"
Cohesion: 0.11
Nodes (6): MemoryQuery, MemoryStore, fixture, Real recorder + live hooks -> replay adapter/core -> persisted backend result., recorded_day(), test_real_recorder_live_hooks_to_saved_backend_result()

### Community 408 - "live_rule_replay.py"
Cohesion: 0.25
Nodes (24): _blocked_buy_reason(), _boolean(), _config(), _date(), _integer(), _keys(), _number(), _observed() (+16 more)

### Community 409 - "fake_runner"
Cohesion: 0.16
Nodes (18): fake_runner(), parametrize, CI bootstrap tests use synthetic secrets and never contact Bitwarden., secret_rows(), test_child_launch_exception_does_not_leak_credentials(), test_malformed_secret_response_is_safe(), test_missing_or_invalid_bootstrap_names_exact_actions_secret(), test_project_resolution_rejects_missing_ambiguous_or_invalid() (+10 more)

### Community 411 - "Entry decisions extracted into a pure `decision_core` shared by live and backtest"
Cohesion: 0.33
Nodes (5): Consequences, Context, Decision, Entry decisions extracted into a pure `decision_core` shared by live and backtest, Why this is safe to ship

### Community 412 - "CostModel"
Cohesion: 0.11
Nodes (16): ReplayConfig, Pins the commission + slippage model (trade_costs.py, backtest-fidelity item #3), test_commission_hits_min_floor_on_small_orders(), test_commission_uses_per_share_when_above_min(), test_commission_zero_shares_is_free(), test_costs_reduce_net_but_not_exit_reasons(), test_slippage_is_adverse_on_both_sides(), test_zero_cost_model_is_a_noop() (+8 more)

### Community 413 - "test_treasury_reference.py"
Cohesion: 0.21
Nodes (24): assert_digest(), csv_bytes(), parametrize, Offline Treasury reference transport, validation and snapshot contracts., test_extend_preserves_previous_yields_and_documents(), test_extension_over_year_boundary_keeps_all_raw_source_digests(), test_failed_extension_does_not_damage_previous(), test_failed_extension_retains_history_and_retry_only_freezes_new_dates() (+16 more)

### Community 414 - "compute_rs_score"
Cohesion: 0.16
Nodes (10): compute_rs_score(), Relative Strength score (0-100) vs S&P 500 over the last 12 weeks.      Excess r, Stock +20%, SPY +5% -> excess +15% -> 100, Excess exactly 10% -> 100, Excess 5% -> 50 + 5*5 = 75, Same return as SPY -> 50, Excess -5% -> 50 + (-5)*5 = 25, Excess exactly -10% -> max(0, 50-50) = 0 (+2 more)

### Community 415 - "Exit decisions extracted into a pure `exit_core` shared by live and backtest"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Evidence (what replaces byte-identity), Exit decisions extracted into a pure `exit_core` shared by live and backtest, Why the live monitor is not rewired in this patch

### Community 418 - "test_strategy_backtest.py"
Cohesion: 0.27
Nodes (14): _bar(), cfg(), _pos(), fixture, Tests for research/strategy_backtest.py — the daily-bar strategy backtest whose, Teeth: widen the band to 20% and the day-0 exit must vanish., test_benign_up_day_holds_and_latches_proven(), test_gap_through_open_fills_at_open_not_level() (+6 more)

### Community 419 - "Backtester exit parity: a research backtest that calls the live exit engine"
Cohesion: 0.25
Nodes (7): Alternatives considered, Backtester exit parity: a research backtest that calls the live exit engine, Consequences, Context, Decision, Fidelity — what this is and is not, The container obstacle, and why this is a *research* tool

### Community 420 - "intraday_replay.py"
Cohesion: 0.25
Nodes (16): _build_dataset(), CaptureError, _coverage(), _initial_positions(), _normalize_raw_stream(), _off_hours_technical_warning(), ValueError, Pure recorded-capture conversion and research comparison; no network or writes. (+8 more)

### Community 421 - "Option A: the dashboard backtester exits with the LIVE engine"
Cohesion: 0.25
Nodes (7): Consequences, Context, Decision, Fidelity (unchanged from Option B), Option A: the dashboard backtester exits with the LIVE engine, Verification, Why this is safe for the live dashboard

### Community 422 - "test_backtester_live_exits.py"
Cohesion: 0.36
Nodes (7): DataFrame, _range_bound_frame(), backend/backtester.py (the dashboard backtester) must exit with the LIVE rules., A ticker that oscillates in [90,100] for WARMUP days (so no 20-day-high     brea, A steadily rising index so the SPY market filter stays bullish., test_dashboard_backtester_uses_live_exit_engine(), _uptrend_frame()

### Community 423 - "first"
Cohesion: 0.10
Nodes (27): first(), The recorder's real nested snapshot/list quotes/phase marker wire format., raw_hook_records(), test_actual_recorder_phase_and_emit_events_assemble_into_buy_cycle(), test_complete_true_cannot_hide_missing_quotes(), test_config_change_requires_split_window(), test_extra_order_or_quantity_mismatch_rejected(), test_incomplete_cycle_or_missing_eod_or_outage_rejected() (+19 more)

### Community 424 - "test_intraday_service.py"
Cohesion: 0.10
Nodes (3): parametrize, Backend research jobs never depend on a live broker or mutate live settings., test_auto_reviews_only_closed_prior_week_and_deduplicates()

### Community 425 - "Statistical-rigor harness for the strategy backtester (fidelity item #6)"
Cohesion: 0.33
Nodes (5): Alternatives considered, Consequences, Context, Decision, Statistical-rigor harness for the strategy backtester (fidelity item #6)

### Community 426 - "fetch_5min"
Cohesion: 0.19
Nodes (13): _correct_split(), fetch_5min(), fetch_entry_atr_pct(), _fmp_get(), hydrate(), date, Answer the reachability question directly, before any dollar figure.      POWER_, GET with backoff on FMP rate limits.      The run-on window roughly doubles the (+5 more)

### Community 428 - "thesis_reexam_bt.py"
Cohesion: 0.35
Nodes (9): main(), Does an INTRADAY POKE above entry deserve to disarm the Thesis Stop?  THE QUESTI, load(), simulate(), stats(), main(), period_cagrs(), Re-examination of the Thesis Stop after the look-ahead correction.  WHY THIS EXI (+1 more)

### Community 429 - "trading_control_api.py"
Cohesion: 0.31
Nodes (9): _configured_token(), EntryPermission, get_trading_control(), BaseModel, get, put, Operator-authenticated live-entry control; no broker or research writes., _status() (+1 more)

### Community 430 - "Share the Bitwarden bootstrap token; resolve the project by name"
Cohesion: 0.33
Nodes (5): Alternatives considered, Consequences, Context, Decision, Share the Bitwarden bootstrap token; resolve the project by name

### Community 431 - "Record intraday evidence and compare strategies from the actual account"
Cohesion: 0.17
Nodes (10): Context, Decision, Fidelity boundaries, Record intraday evidence and compare strategies from the actual account, Retention and interpretation, Context, Decision, Interpretation and historical errata (+2 more)

### Community 432 - "proveit_configs"
Cohesion: 0.33
Nodes (6): clean_configs(), live_baseline(), proveit_configs(), The Prove-It Stop sweep.      Every row here REPLACES the kill-switch, dollar st, Historical Prove-It plus scale-out experiment; retained compatibility name., Does a DRAWDOWN-CONDITIONAL ladder rung beat one flat rung?      The question th

### Community 433 - "test_research_fidelity_labels.py"
Cohesion: 0.40
Nodes (3): parametrize, Reports must not turn descriptive legacy outcomes into causal live P&L., test_legacy_veto_pool_is_not_reported_as_realised_cost()

### Community 436 - "_earnings_blackout_days_until"
Cohesion: 0.38
Nodes (4): _earnings_blackout_days_until(), Trading days from `today` until the ticker's next earnings, or None.      None m, Unit tests for the pure trading-day distance used by the gate., TestEarningsBlackoutHelper

### Community 437 - "test_calibration_deployment.py"
Cohesion: 0.08
Nodes (21): candidate_probe(), fixture, parametrize, Offline approval deployment tests: no broker, production, or Git remote calls., refresh(), reviewed(), signed(), test_complete_candidate_probe_matches_approval_and_receives_bound_acknowledgement() (+13 more)

### Community 438 - "Broker-confirmed sell safety after the SHIP incident"
Cohesion: 0.40
Nodes (4): Broker-confirmed sell safety after the SHIP incident, Consequences and operational limits, Decision, Evidence and limits

### Community 439 - "technical_screener.py"
Cohesion: 0.18
Nodes (14): increment_retention(), check_technical_breakout(), _compute_failure_penalty(), fetch_spy_return_12w(), fetch_with_retry_sync(), get_supabase_client(), get_watchlist_from_supabase(), Fetch SPY's 12-week (approx. 60 trading days) price return as a percentage. (+6 more)

### Community 440 - "BacktesterView.jsx"
Cohesion: 0.47
Nodes (4): BacktesterView(), dollars(), muted, ShadowResearchView()

### Community 441 - "20260708_enable_rls_all_tables.sql"
Cohesion: 0.33
Nodes (5): account_balances, daily_triggers, portfolio_positions, trade_history, watchlist

### Community 442 - "20260813_apply_missing_migrations.sql"
Cohesion: 0.40
Nodes (4): portfolio_positions, trigger_decisions, trigger_history, watchlist_history

### Community 486 - "ReadOnlyBroker"
Cohesion: 0.17
Nodes (8): _deny_write(), ObservationError, RuntimeError, Only completed reads, event pumping, and connection lifecycle are exposed., ReadOnlyBroker, select_account(), test_account_ambiguity_fails(), test_explicit_account_allowed()

### Community 490 - "Recorded intraday research"
Cohesion: 0.14
Nodes (14): Automatic comparisons and data window, Decision-only worker, Deploying, Diagnose research failures without production SSH, Independent daily/weekly supervision, Independent observer, Offline calibration: select first, evaluate later, Quote endpoint compatibility and budgets (+6 more)

### Community 491 - "compute_rs_excess"
Cohesion: 0.23
Nodes (12): _env(), fetch_rows(), history(), main(), date, 12-week return as it was knowable on `as_of`. None if unavailable.      Uses the, Daily closes ascending as (date, close). Cached per symbol., return_12w() (+4 more)

### Community 492 - "intraday_service.py"
Cohesion: 0.23
Nodes (22): automatic_review(), _collector_health(), _execute_job(), export_dataset(), export_observations(), _flush_terminal_updates(), get_client(), get_run() (+14 more)

### Community 493 - "ShadowStore"
Cohesion: 0.16
Nodes (15): canonical(), fingerprint(), now(), RuntimeError, Single-writer durable shadow journal. A cycle and its outbox commit together., Ordered idempotent cloud upserts; an acknowledgement loss is safe to retry., ShadowStore, StoreError (+7 more)

### Community 495 - "intraday_reporting_delivery.py"
Cohesion: 0.22
Nodes (9): fallback_alert(), identity(), Issues, operational_issue_body(), Reporting-only transports and durable, recipient-specific delivery receipts.  At, An open issue is the durable fallback when Supabase is unreachable., Issue-body receipts allow recipient-specific retries even during DB outage., GitHub may be public: never publish caller-provided diagnostic/report text. (+1 more)

### Community 496 - "test_deploy_runtime.py"
Cohesion: 0.15
Nodes (22): deployment(), fixture, parametrize, Offline deployment contract; Docker is a recording executable, never a daemon., service_block(), test_all_modes_keep_protection_and_research_running(), test_capture_uses_host_settings_with_distinct_default_spools_and_client_ids(), test_compose_runs_protection_and_research_independent_of_runtime_label() (+14 more)

### Community 497 - "test_auto_calibration.py"
Cohesion: 0.09
Nodes (36): modeled_result(), policy(), fixture, parametrize, Pure automatic research: bounded inputs, honest risk gates, real replay artifact, test_actual_frozen_chronological_shadow_evaluation_preserves_inputs(), test_automatic_logic_fingerprint_invalidates_old_campaigns(), test_baseline_and_no_actual_settings_change_never_qualify() (+28 more)

### Community 498 - "intraday_capture.py"
Cohesion: 0.16
Nodes (18): broker_snapshot(), collector_config(), effective_config(), _fields(), observe_portfolio(), _plain(), Optional observation-only recorder. Broker objects never leave their thread.  Th, Read only IB's existing caches. Their market marks have no freshness proof. (+10 more)

### Community 499 - "exception_details"
Cohesion: 0.22
Nodes (10): exception_details(), Inspect a bounded cause/context chain, including ``raise ... from None``., test_reporting_retains_http_and_database_code_without_body(), parametrize, test_categories_use_type_not_message(), test_exception_chain_prefers_explicit_cause_and_is_bounded(), test_startup_account_and_fmp_presence_only(), test_structured_codes_never_error_text() (+2 more)

### Community 500 - "2026-09-27 — Startup crashes ship to Supabase; execution_agent import cycle removed"
Cohesion: 0.14
Nodes (12): 2026-09-27 — Startup crashes ship to Supabase; execution_agent import cycle removed, Consequences, Context, Decision, Fix 1 — one lazy, entrypoint-safe reference module, Fix 2 — a fatal-safe entrypoint that ships startup crashes, Problem 1 — a latent circular import from the modular split, Problem 2 — startup crashes were invisible to Supabase (+4 more)

### Community 501 - "Retune HWM profit-lock arm from +6% to +5%"
Cohesion: 0.29
Nodes (6): Consequences, Context, Decision, Docs sync, Evidence, Retune HWM profit-lock arm from +6% to +5%

### Community 503 - "Real trading control"
Cohesion: 0.40
Nodes (5): Hypothetical trades are not real trades, One-time setup, Persistence and failure behavior, Real trading control, What the status means

### Community 504 - "TestPathMetrics"
Cohesion: 0.22
Nodes (5): These metrics carry 20-day semantics, so the fixtures must supply a full     20-, The entry session IS held, so its range counts. The trigger session         is n, A 3-bar excursion is not a small 20-bar excursion — it is a different         qu, The failed-breakout signature the Thesis Stop targets., TestPathMetrics

### Community 505 - "shadow_service.py"
Cohesion: 0.35
Nodes (11): activity(), _activity_portfolio(), export_dataset(), _positive_integer(), _query(), Bounded read-only access to hypothetical portfolios and private research reports, Read recorded output at a fixed published watermark; never replay a strategy., reports() (+3 more)

### Community 506 - "exit_core.py"
Cohesion: 0.16
Nodes (12): _armed_deadline_reason(), config_from_module(), ExitConfig, ExitContext, ExitDecision, _prove_it_reason(), exit_core.py — the PURE per-cycle exit decision shared by the live monitor and t, True when the verdict ends this position's processing for the cycle         (mir (+4 more)

### Community 507 - "TestIncompleteWindowsNotWritten"
Cohesion: 0.43
Nodes (3): Renamed in spirit 2026-09-17: short windows ARE now written, but only the     ho, The 3-day window yields fwd_1d only. Stamping it would retire the row         fo, TestIncompleteWindowsNotWritten

### Community 508 - "20260930_add_intraday_shadow.sql"
Cohesion: 0.70
Nodes (4): public.intraday_shadow_checkpoints, public.intraday_shadow_events, public.intraday_shadow_health, public.intraday_shadow_runs

### Community 509 - "test_shadow_image.py"
Cohesion: 0.50
Nodes (3): image_sources(), The research image boots from its explicit copy list without brokerage modules., test_shadow_image_has_no_broker_sdk_or_execution_orchestration()

### Community 513 - "IntradayReplayView.jsx"
Cohesion: 0.20
Nodes (25): failures, result, sessions, Comparison(), count(), InitialSnapshot(), InitialStateSummary(), IntradayReplayView() (+17 more)

### Community 514 - "test_trading_control_api.py"
Cohesion: 0.22
Nodes (7): client(), fixture, parametrize, Trading permission API cannot write without explicit operator credentials., test_non_boolean_permission_is_rejected(), test_unauthorized_mutation_is_rejected(), test_unconfigured_token_locks_writes()

### Community 516 - "ReportingError"
Cohesion: 0.14
Nodes (16): BaseException, A candidate-level rejection handler must not swallow the worker deadline., RuntimeBudgetExceeded, CollectionAttention, deadline(), RuntimeError, Safe operator-facing error without raw transport details., Small PostgREST adapter; pagination fails rather than truncating evidence. (+8 more)

### Community 517 - "test_shadow_activity.py"
Cohesion: 0.11
Nodes (14): parametrize, Recorded shadow activity is bounded, immutable and independent of replay., seal(), test_checkpoint_seal_and_exact_provenance(), test_cursors_are_strict_positive_non_boolean_integers(), test_empty_page_with_expected_published_rows_is_an_error(), test_empty_seeded_run(), test_invalid_run() (+6 more)

### Community 518 - "CalibrationStore"
Cohesion: 0.15
Nodes (11): CalibrationStore, RuntimeError, ValueError, Private durable research inbox. Every state change and audit event commits toget, _revision(), StoreUnavailable, _text(), ValidationError (+3 more)

### Community 519 - "TradingControl.jsx"
Cohesion: 0.39
Nodes (5): now, status, readResponse(), TradingControl(), describeTradingControl()

### Community 520 - "calibration_api.py"
Cohesion: 0.15
Nodes (28): _action(), ActionInput, _approved_snapshot(), authenticate(), _call(), _configured_token(), deployment(), DeploymentVerificationInput (+20 more)

### Community 521 - "calibration_deployment.py"
Cohesion: 0.11
Nodes (40): _deployment_validation(), _approved(), build_deployment_artifact(), candidate_configuration(), canonical(), config_digest(), config_snapshot(), DeploymentError (+32 more)

### Community 522 - "2026-10-04 — Load watchdog credentials from Bitwarden on the hosted runner"
Cohesion: 0.40
Nodes (4): 2026-10-04 — Load watchdog credentials from Bitwarden on the hosted runner, Consequences, Context, Decision

### Community 523 - "test_calibration_store.py"
Cohesion: 0.12
Nodes (14): fixture, parametrize, Durable inbox never substitutes volatile memory or partial writes for database c, row(), store(), test_create_recomputes_artifact_hash_and_uses_atomic_rpc(), test_database_failure_never_reports_success(), test_database_transaction_errors_are_mapped() (+6 more)

### Community 524 - "Evidence"
Cohesion: 0.50
Nodes (4): Evidence, Phase 1 and Phase 2 are complementary, not additive, Why Phase 1 WIDENS after day 0 rather than tightening, Why the Phase 2 floor sits 1% BELOW entry, not at it

### Community 525 - "run_comparison"
Cohesion: 0.12
Nodes (16): Compare identical recorded starting books; no approval or live mutation., run_comparison(), test_actual_later_bot_fills_are_audit_only_and_manual_or_rotation_blocks(), test_backend_can_supply_same_run_configuration_from_before_window(), test_cycle_duration_is_not_misclassified_as_missing_fifteen_minute_attempt(), test_drawdown_and_closed_position_count_measure_window_not_original_cost(), test_identical_actual_start_book_cash_and_source_labelled_fills(), test_missing_unrelated_retained_symbol_warns_without_blocking_comparison() (+8 more)

### Community 526 - "treasury_reference.py"
Cohesion: 0.22
Nodes (18): HTTPRedirectHandler, _day(), _digest(), fetch_reference(), _get(), _NoRedirect, _now(), _parse() (+10 more)

### Community 527 - "auto_calibration.py"
Cohesion: 0.20
Nodes (18): assess_evaluation(), automatic_fingerprint(), evaluate_selection(), freeze_selection(), _hypotheses(), _number(), parameter_candidates(), _portfolio() (+10 more)

### Community 528 - "_Replay"
Cohesion: 0.36
Nodes (3): One shared transition, used by both batch replay and the shadow worker., Replay one immutable input, optionally with exactly one entry-veto ablation., _Replay

### Community 529 - "sentiment.py"
Cohesion: 0.16
Nodes (13): check_volume_distribution(), _fetch_current_rs(), fetch_held_position_sentiment(), _fetch_ohlcv(), _get_entry_rs(), _get_market_regime(), sentiment.py — FMP/OpenAI research-data fetchers used by the agent loop.  Extrac, Return current market regime based on SPY vs its 21-day EMA.      'uptrend'    — (+5 more)

### Community 530 - "backtester.py"
Cohesion: 0.23
Nodes (13): _cagr(), _ema(), _make_trade(), _max_consecutive_losses(), _max_underwater_days(), backend/backtester.py  Runs a historical simulation of the CAN SLIM breakout tra, One closed (or partially closed) trade record, in the shape the API/UI     and t, Historical simulation of the CAN SLIM breakout strategy.      Position sizing ma (+5 more)

### Community 531 - "calibration_worker.py"
Cohesion: 0.18
Nodes (12): future_sessions(), main(), notify(), ValueError, Scheduled, research-only campaigns using frozen future evaluation windows., Reserve unseen exchange sessions, including holidays and early closes., Read a coherent published prefix; never fill missing observations., run_once() (+4 more)

### Community 532 - "exit_rules.py"
Cohesion: 0.10
Nodes (22): hard_stop_price(), _infer_exit_type(), _position_atr_pct(), prove_it_p1_threshold_pct(), prove_it_stop_level(), prove_it_trail_pct(), Exit decision logic: WHERE a position should exit, and WHETHER it may.  Extracte, # NOTE: those figures were measured with the +20% trigger. The move to +10% wide (+14 more)

### Community 533 - "test_datasource_unavailable.py"
Cohesion: 0.15
Nodes (10): _Boom, Exception, fixture, An unreachable database must not be reported as an empty portfolio.  On 2026-09-, Stands in for the DNS / transport failures Supabase surfaces., The operator needs the underlying error to diagnose it -- here, DNS., An empty database is legitimate and must still return [] -- the fix must     not, test_original_cause_is_preserved() (+2 more)

### Community 535 - "Independent observation and approval-only calibration"
Cohesion: 0.13
Nodes (12): Consequences, Context, Decision, Independent observation and approval-only calibration, Context, Decision, Decision-only portfolio simulation and supervised research, Limits (+4 more)

### Community 536 - "test_calibration_risk_refresh_store.py"
Cohesion: 0.14
Nodes (11): migration(), fixture, parametrize, Diagnostic retries have a separate, narrow transaction; frozen writes stay block, store(), test_missing_refresh_rpc_names_the_required_migration(), test_refresh_binds_old_and_replacement_hashes_without_touching_original(), test_refresh_maps_transaction_failures_without_exposing_database_details() (+3 more)

### Community 537 - "_LedgerQuery"
Cohesion: 0.17
Nodes (3): _Ledger, _LedgerQuery, Only the query operations compute_cooled_map needs; no database or I/O.

### Community 538 - "market_regime.py"
Cohesion: 0.25
Nodes (9): index_verdict(), Pure moving-average market gate; data acquisition belongs to callers., _fetch_market_closes(), _index_is_bullish(), is_market_bullish(), market_regime.py — CANSLIM 'M' (Market Direction) filter.  Extracted from execut, Sorted (date, close) daily history for `ticker`, oldest first.      Returns an e, Per-index verdict as ``(above_sma, slope_ok)``, or None if data is unusable. (+1 more)

### Community 539 - "TestReconcileCase4"
Cohesion: 0.17
Nodes (7): Case 4: Cash balance sync from IBKR to Supabase account_balances., Large change in cash → upsert to account_balances called., New logic: write daily snapshots for cash, positions_value, total_value., A cash jump > $500 inserts into cash_flows., Regression: the stored account total must equal IBKR's NetLiquidation         ta, If IBKR's NetLiquidation tag is momentarily unavailable (returns 0),         the, TestReconcileCase4

### Community 540 - "Interactive self-calibration"
Cohesion: 0.20
Nodes (10): Approval and deployment, Follow the process in the dashboard, Interactive self-calibration, Production policy, Risk-adjusted benchmark metrics, Sampling and formulas, Visibility and failures, What it can investigate (+2 more)

### Community 541 - "calibration_risk_report.py"
Cohesion: 0.36
Nodes (8): analytics_fingerprint(), build_risk_report(), Diagnostic risk evidence, separate from frozen strategy selection and approval., Retry only cash-reference diagnostics over the exact saved replay outputs., Reproduce the exact selected pair; never search, alter a plan or infer a path., _reference(), _reference_window(), refresh_risk_reference()

### Community 542 - "test-calibration-dashboard.mjs"
Cohesion: 0.06
Nodes (29): app, baseline, { Benchmarks: RenderBenchmarks, Overview }, duplicated, errorHtml, errorResource, events, failed (+21 more)

### Community 543 - "_run_monitor"
Cohesion: 0.22
Nodes (8): test_monitor_keeps_protective_order_logic_on_research_failure(), Both protective legs already in IBKR -> no self-healing.         Use price=buy_p, Even when price is below stop level, Python does NOT call execute_sell., Runs monitor_portfolio_intraday() with standard patches.     live_prices: dict o, A healthy position carries a TWO-leg protective bracket in IBKR: the base     tr, No open SELL orders -> place_protective_stops called for self-healing.         U, _run_monitor(), TestSelfHealingTrailingStop

### Community 544 - "Automatic research with operator-approved strategy deployment"
Cohesion: 0.50
Nodes (4): Automatic research with operator-approved strategy deployment, Consequences and limits, Context, Decision

### Community 546 - "CalibrationRiskMetrics.jsx"
Cohesion: 0.38
Nodes (6): CalibrationRiskMetrics(), Measurement(), muted, rows, valueText(), riskMetricsView()

### Community 547 - "test_exit_path_golden.py"
Cohesion: 0.29
Nodes (9): test_exit_path_golden.py — locks the money-path call sequence of monitor_portfol, Two runs of the same scripted book must produce an identical sequence, or     th, A fixed multi-regime book. Prices are chosen relative to a $100 entry so     the, Guard against a golden that locks an EMPTY sequence — which would pass     forev, _record_rows(), _scripted_book(), test_golden_scenario_is_non_vacuous(), test_monitor_exit_path_matches_golden() (+1 more)

### Community 548 - "2026-09-26 — Phase 1 backstop-slack widening (behaviour retired, constant kept)"
Cohesion: 0.22
Nodes (9): 1. Intraday Loss Minimiser (ILM), 2026-09-26 — Phase 1 backstop-slack widening (behaviour retired, constant kept), 2. Trailing-stop time lever (`TRAIL_TIME_TIERS`), 3. Early Loss Kill-switch, 4. Early Dollar Stop, 5. Thesis Stop, 6. EMA-21 Exit, 7. Plateau (Stale) Exit (+1 more)

### Community 549 - "test_shadow_service.py"
Cohesion: 0.17
Nodes (5): cloud(), fixture, parametrize, Private shadow endpoints retain predecessor evidence and never mutate live state, test_export_refuses_incomplete_or_incompatible_evidence()

### Community 550 - "ActivityQuery"
Cohesion: 0.22
Nodes (5): Query, ActivityClient, ActivityQuery, cloud(), fixture

### Community 551 - "Evidence-bound risk ratios for calibration"
Cohesion: 0.40
Nodes (4): Consequences, Context, Decision, Evidence-bound risk ratios for calibration

### Community 552 - "._run_at"
Cohesion: 0.33
Nodes (4): The pivot check used to be a ceiling only: it rejected stocks extended too     f, The buy loop takes its price from fetch_ibkr_delayed_price, not         get_live, A 1% dip is noise around the pivot, not a failed breakout., TestPivotBuyZoneFloor

### Community 553 - "capture_phase"
Cohesion: 0.50
Nodes (5): capture_phase(), Markers include early returns; omitted downstream inputs stay unknown., test_disabled_phase_has_no_side_effects(), test_phase_early_return_does_not_claim_unevaluated_inputs(), cycle()

### Community 554 - "Unified, evidence-scoped calibration visibility"
Cohesion: 0.40
Nodes (4): Consequences, Context, Decision, Unified, evidence-scoped calibration visibility

### Community 557 - "parametrize"
Cohesion: 0.22
Nodes (9): parametrize, test_every_existing_position_state_input_must_be_recorded(), test_future_or_old_sample_cannot_be_used_for_entry(), test_incomplete_frame_must_not_hide_missing_held_or_variant_only_candidate(), test_initial_non_gtc_protection_is_not_carried_overnight(), test_initial_unsupported_or_unknown_oca_semantics_rejected(), test_native_proven_early_return_covers_buy_attempt_without_inventing_inputs(), test_unattributed_raw_execution_audit_is_not_silently_ignored() (+1 more)

### Community 558 - "TestGetTradeHistoryProjection"
Cohesion: 0.44
Nodes (3): The API-layer regression that made the whole commission feature inert.      `dat, Mirror of the projection in database.get_trade_history()., TestGetTradeHistoryProjection

### Community 559 - "session_bounds"
Cohesion: 0.32
Nodes (8): Return NYSE open/close in New York time, or None on a non-session.      The inst, session_bounds(), next_tick(), Never backfill an elapsed slot with a quote fetched now., session_ticks(), test_exchange_session_bounds_early_close_holiday_dst(), test_calendar_early_close_and_missed_close(), test_shadow_history_translates_share_class_and_preserves_cache_identity()

### Community 560 - "AMTM"
Cohesion: 0.50
Nodes (4): end, rows, start, AMTM

### Community 561 - "BDX"
Cohesion: 0.50
Nodes (4): end, rows, start, BDX

### Community 562 - "BNY"
Cohesion: 0.50
Nodes (4): end, rows, start, BNY

### Community 563 - "EA"
Cohesion: 0.50
Nodes (4): end, rows, start, EA

### Community 566 - ".test_hwm_date_not_updated_when_price_falls"
Cohesion: 0.33
Nodes (4): hwm_date (date of last intraday high) is the only HWM data Python tracks.     IB, New intraday high (price > buy_price) -> hwm_date written to Supabase., Price does not exceed buy_price (or last seen peak) -> no hwm_date update., TestHwmDateTracking

## Knowledge Gaps
- **1469 isolated node(s):** `bar_interval`, `bytes`, `dataset`, `date_max`, `date_min` (+1464 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **91 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `per_symbol` connect `per_symbol` to `flex_query_sync.py`, `AMTM`, `BDX`, `BNY`, `EA`, `BIRK`, `CMI`, `MANIFEST.json`, `AAPL`, `ABBV`, `ABNB`, `ABT`, `ACN`, `ADBE`, `ADI`, `ADP`, `ADSK`, `AEIS`, `AEP`, `AFL`, `AJG`, `ALAB`, `ALL`, `AMAT`, `AMD`, `AME`, `AMGN`, `AMKR`, `AMT`, `AON`, `APD`, `APH`, `APO`, `APP`, `ARM`, `ARW`, `AS`, `AVGO`, `AXP`, `BA`, `BABA`, `BAC`, `BAM`, `BE`, `BKNG`, `BKR`, `BMY`, `BN`, `AMZN`, `BX`, `CARR`, `CAT`, `CCEP`, `CDNA`, `CDNS`, `CEG`, `CELH`, `CF`, `CI`, `CIEN`, `CL`, `CMC`, `CMCL`, `CMCSA`, `CME`, `CMG`, `COCO`, `COF`, `BSX`, `CRH`, `CRWD`, `CSCO`, `CSX`, `CTAS`, `CTVA`, `CVNA`, `CVS`, `CVX`, `CXW`, `D`, `DAL`, `DASH`, `DDOG`, `DE`, `DELL`, `DHR`, `DIOD`, `DIS`, `DLR`, `DUK`, `DVN`, `DXCM`, `DY`, `EBAY`, `ECL`, `ECO`, `EME`, `EMR`, `EOG`, `EPD`, `ETN`, `ETR`, `EXC`, `F`, `FANG`, `FAST`, `FCX`, `FERG`, `FITB`, `FLYW`, `COST`, `COP`, `ANET`, `COR`, `COHR`, `ELV`, `COHU`, `CRM`, `CAH`, `CB`, `EW`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `ET` connect `flex_query_sync.py` to `per_symbol`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `fetch_trade_confirms_for_ticker()` connect `flex_query_sync.py` to `execution_agent.py`?**
  _High betweenness centrality (0.033) - this node is a cross-community bridge._
- **What connects `bar_interval`, `bytes`, `dataset` to the rest of the system?**
  _1469 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `compute_liquidity_score` be split into smaller, more focused modules?**
  _Cohesion score 0.1168091168091168 - nodes in this community are weakly interconnected._
- **Should `datetime` be split into smaller, more focused modules?**
  _Cohesion score 0.08115942028985507 - nodes in this community are weakly interconnected._
- **Should `_AV` be split into smaller, more focused modules?**
  _Cohesion score 0.07312925170068027 - nodes in this community are weakly interconnected._