# Configuration Reference

Every tunable parameter, its default, and what changing it does to trading behaviour.

All configuration is environment-driven (`.env` or Docker environment). **No strategy
parameter requires a code change.**

> **Shared constants.** `MAX_POSITIONS`, `STOP_LOSS_PCT` and `COOLING_OFF_DAYS` are defined
> once in `config.py` and imported by every module that trades — including the manual
> `force_buy.py`, `force_sell.py` and `rotate_positions.py` tools. They previously carried
> divergent defaults per file, which meant a manual buy attached a different stop than the
> agent would have. A regression test enforces agreement; see
> `tests/test_max_positions_config.py`.

---

## Credentials

| Variable | Required | Notes |
|---|---|---|
| `SUPABASE_URL` / `SUPABASE_KEY` | yes | Cloud state store |
| `FMP_API_KEY` | yes | Fundamental data, screening/research prices, and the **fallback** live price for held positions when IBKR has no mark (exit logic and dashboard price positions from IBKR first — see `decisions/2026-09-04_ibkr-first-live-pricing.md`) |
| `OPENAI_API_KEY` | yes | Trigger evaluation; without it every trigger is rejected `NO_AI_SCORE` |
| `IBKR_LIVE_USER` / `IBKR_LIVE_PASS` / `IBKR_TOTP_SECRET` | yes | Gateway login — see [IBKR TOTP setup](ibkr_totp_setup.md) |
| `IBKR_ACCOUNT` | conditional | **Required if both live (`U…`) and paper (`DU…`) accounts are visible.** The agent refuses to guess |
| `IBKR_FLEX_TOKEN` / `IBKR_FLEX_QUERY_ID` | optional | Cash-flow reconciliation. Token expires annually |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_IDS` | optional | Alerts |

TradingView requires no credentials.

---

## Portfolio and risk

| Variable | Default | Effect |
|---|---|---|
| `MAX_POSITIONS` | `5` | Concurrent positions. Also the technical screener's candidate-quota target |
| `MIN_POSITION_SIZE` | `5000` | Cash floor; below this no buy is attempted |
| `PRICE_SAFETY_RESERVE` | `1000` | Withheld per order to absorb IBKR's 15–20 min quote lag |
| `STOP_LOSS_PCT` | `0.10` | Base trailing stop from peak — the **floor** of the ATR band |
| `ATR_STOP_MAX_PCT` | `0.12` | Ceiling of the ATR band |
| `COOLING_OFF_DAYS` | `7` | Re-entry block after a sale |

**Sizing** is `available_cash / remaining_slots`, recomputed before each buy. The
per-position stop is `max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT, 2.5 × entry_atr_pct))` — so
in practice 10–12%, scaled to the name's own volatility.

Raising `MAX_POSITIONS` with a fully-invested book does **not** free capital. New slots fill
only as existing positions exit, and the book carries uneven weights until it fully turns
over.

No exit rule derives a threshold from the slot count any more. `EFFECTIVE_POSITION_SLOTS`
existed solely for the Early Dollar Stop's arithmetic and was deleted with it (FU-007 is
closed as a result) — see `docs/retired_code.md`.

---

## Buy gating

| Variable | Default | Effect |
|---|---|---|
| `TRIGGER_LOOKBACK_DAYS` | `3` | Trigger freshness window (covers weekends/holidays) |
| `MAX_PIVOT_EXTENSION` | `0.05` | Buy-zone ceiling above pivot |
| `MAX_PIVOT_BREAKDOWN` | `0.02` | Buy-zone floor below pivot |
| `MIN_VOL_SURGE_GATE` | `0.75` | Hard AI-independent volume surge floor. **Applies to `BREAKOUT` triggers only** — on `PRE_BREAKOUT*` rows the same `volume_surge` column holds a volume *contraction* ratio where lower is better, so a floor would invert selection. See `decisions/2026-08-19_volume-gate-inversion.md` |
| `MAX_PRE_BREAKOUT_PIVOT_DIST` | `0.05` | Max distance below 52W high for PRE_BREAKOUT entries; rejects speculative setups more than 5% away |
| `MIN_TRIGGER_SCORE` | `60` | Score floor, `BREAKOUT` |
| `MIN_PRE_BREAKOUT_SCORE` | `65` | Score floor, `PRE_BREAKOUT` |
| `MIN_RELAXED_TRIGGER_SCORE` | `58` | Score floor, `PRE_BREAKOUT_RELAXED` |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Master switch for the CANSLIM "M" buy gate. `false` is the only bypass |
| `MARKET_DIRECTION_TICKERS` | `SPY,QQQ` | Comma-separated benchmarks. **Every** one must clear the buffer for a bull verdict. Replaces the singular `MARKET_DIRECTION_TICKER`, which is no longer read |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | Regime lookback |
| `MARKET_DIRECTION_BUFFER_PCT` | `0.01` | Dead-band: price must exceed `SMA-200 × (1 + buffer)`. Prevents regime flapping on marginal crosses |
| `MARKET_DIRECTION_SLOPE_DAYS` | `20` | Sessions used for the SMA-200 slope test. **At least one** benchmark's SMA-200 must be non-falling |
| `MARKET_DIRECTION_MAX_STALE_DAYS` | `5` | Price data older than this is unusable → bearish |

The market filter gates **buying only** — it never forces an exit — and fails **closed** on
every error path: HTTP failure, malformed payload, insufficient history, stale data,
unhandled exception, or an empty benchmark list all yield bearish.
See `decisions/2026-08-22_market-direction-gate-spy-qqq.md`.

`backend/screener.py::get_market_direction()` (dashboard) reads the same three tuning
variables so its `execution_gate` field cannot drift from the agent's verdict.

---

## Exits

### The Prove-It Stop

The single loss-cutting rule. One question decides everything: has the position ever CLOSED
above what we paid? See `docs/sell_logic.md` for the full behaviour and
`decisions/2026-09-04_prove-it-stop.md` for the evidence.

| Variable | Default | Effect |
|---|---|---|
| `PROVE_IT_ENABLED` | `true` | Master switch |
| `PROVE_IT_P1_DAY0_PCT` | `0.01` | Phase 1 band below entry on the entry day |
| `PROVE_IT_P1_LATER_PCT` | `0.03` | Phase 1 band below entry from day 1 onward |
| `PROVE_IT_P1_DAY0_LAST_DAY` | `0` | Last day the tighter Phase 1 band applies |
| `PROVE_IT_P2_ARM_GAIN_PCT` | `0.02` | Peak gain that arms the Phase 2 give-back floor |
| `PROVE_IT_P2_FLOOR_PCT` | `-0.01` | Floor relative to entry; **negative = below entry** |
| `PROVE_IT_BACKSTOP_SLACK_PCT` | `0.01` | How far *wider* the Phase 1 resting IBKR order sits |

Two of these are counter-intuitive and were measured, not guessed:

- **`PROVE_IT_P1_LATER_PCT` is wider than `PROVE_IT_P1_DAY0_PCT`.** Holding the tight day-0
  band through day 1 costs roughly $1,500–2,000 in clipped winners across the 30-trade
  sample. Day 0 is the only day on which the failing and working populations separate.
- **`PROVE_IT_P2_FLOOR_PCT` must stay negative.** An exact-breakeven floor flushes any
  position that pokes green and immediately retests entry, forfeiting +$1,189 on CPAY alone.

`PROVE_IT_BACKSTOP_SLACK_PCT` keeps the resting broker order *behind* the bot-side armed
exit in Phase 1. Reducing it to 0 lets the broker fire first, which loses the ~$600 the
armed exit is worth across the sample. In Phase 2 the resting order is the floor, so the
slack does not apply.

### Armed exit

| Variable | Default | Effect |
|---|---|---|
| `ARMED_EXIT_TRAIL_PCT` | `0.006` | Trail distance once armed |
| `ARMED_EXIT_DEADLINE_HOURS` | `3.25` | Forced market sell if unfilled |

### Smart OCA managed exit

Drives `process_exit_requests()` — the queue-driven OCA exit fed by
`request_exit.py`. Requires `migrations/add_exit_requests.sql`.

| Variable | Default | Effect |
|---|---|---|
| `OCA_EXIT_ENABLED` | `true` | Master switch. When false the queue is ignored and the automated ladder governs every position |
| `OCA_EXIT_SETTLE_MINUTE` | `45` | Earliest minute past 09:00 ET to place legs. Avoids computing a limit off opening-auction noise |
| `OCA_EXIT_ATR_FRACTION` | `0.33` | Trail as a fraction of `entry_atr_pct` when `stop_mode='ATR_AUTO'` |
| `OCA_EXIT_MIN_TRAIL_PCT` | `0.015` | Lower clamp. Below this the trail sits inside ordinary noise and fires instantly, cancelling the upper leg |
| `OCA_EXIT_MAX_TRAIL_PCT` | `0.040` | Upper clamp |
| `OCA_EXIT_UPPER_ATR_FRACTION` | `0.50` | Upper leg as a fraction of `entry_atr_pct` above the placement price when `limit_mode='ATR_AUTO'` (the default). Larger than the trail fraction so the optimistic leg always sits further out than the protective one |
| `OCA_EXIT_MIN_UPPER_PCT` | `0.0075` | Lower clamp — keeps a quiet stock's target outside the bid/ask spread |
| `OCA_EXIT_MAX_UPPER_PCT` | `0.050` | Upper clamp — keeps a volatile stock's target reachable by a realistic bounce |
| `SMART_EXIT_FOR_RULES` | `true` | Retained switch. No automated rule uses the Smart OCA queue any more — the three discretionary Day 7+ rules that did are retired. The queue remains fully supported for manual requests via `request_exit.py`. Deliberately does **not** apply to the Prove-It Stop, Rank & Replace, or the backstops — see `docs/sell_logic.md` |
| `OCA_EXIT_DEFAULT_ATR_PCT` | `3.0` | Used when the position has no ATR on record |
| `OCA_EXIT_DEFAULT_FLOOR_PCT` | `0.05` | Hard floor below the placement price when the request sets none. **Not optional in effect** — the automated ladder is suspended for managed tickers, so this is the protection |
| `OCA_EXIT_DEFAULT_EXPIRY_DAYS` | `3` | Trading days before an unfilled OCA is closed at market |

Raising `OCA_EXIT_DEFAULT_FLOOR_PCT` widens the worst case on every managed
position, because no other stop is active while the OCA is placed. See
`decisions/2026-08-18_smart-oca-managed-exit.md`.

### Retired exit variables

`EARLY_LOSS_STOP_*`, `EARLY_DOLLAR_STOP_*`, `EFFECTIVE_POSITION_SLOTS`, `THESIS_STOP_*`,
`EXIT_MA_*`, `INTRADAY_*`, `STALE_EXIT_ENABLED` and `TRAIL_TIME_TIERS_*` **no longer exist**.
Setting them has no effect. The rules they configured are all folded into the Prove-It Stop
or into the staleness discount below. See `docs/retired_code.md` for each rule's code
footprint and the conditions under which it would be restored.

### Staleness and rotation

Staleness no longer sells to cash. It discounts the Rank & Replace margin to
`RANK_REPLACE_FAIL_THRESHOLD`, so a position that has stopped advancing is released when
somewhere better to put the money exists — not merely because it stopped moving.

| Variable | Default | Effect |
|---|---|---|
| `STALE_EXIT_DAYS` | `10` | Trading days without a new HWM before a position counts as stale |
| `STALE_EXIT_MIN_DAYS_HELD` | `7` | Earliest day a position may count as stale |
| `RANK_REPLACE_THRESHOLD` | `15` | Rotation margin, verdict `PASS` |
| `RANK_REPLACE_FAIL_THRESHOLD` | `5` | Rotation margin, verdict `FAIL` |
| `MOMENTUM_HEALTH_RS_WEIGHT` | `0.40` | Mₜ weight — relative strength |
| `MOMENTUM_HEALTH_VOL_WEIGHT` | `0.35` | Mₜ weight — volume |
| `MOMENTUM_HEALTH_SENT_WEIGHT` | `0.25` | Mₜ weight — sentiment |

### Power Hold

| Variable | Default | Effect |
|---|---|---|
| `POWER_HOLD_ENABLED` | `true` | Master switch |
| `POWER_HOLD_GAIN_PCT` | `10.0` | Gain required to arm |
| `POWER_HOLD_TRIGGER_DAYS` | `21` | Arming window (**calendar** days) |
| `POWER_HOLD_DURATION_DAYS` | `56` | Protection length (**calendar** days) |
| `POWER_HOLD_TRAIL_PCT` | `0.30` | Trail while power-held |

Disabling Power Hold re-imposes the profit ladder on winners, which caps them near +5%. The
strategy's returns are outlier-dependent; this switch has more effect on total return than
almost any other.

`POWER_HOLD_GAIN_PCT` was lowered from `20.0` to `10.0` alongside the Prove-It Stop: at 20%
the rule was unreachable, because the realised trade distribution contains no +20% runners.
The 10% figure is **unvalidated** — no trade in the 30-trade replay reached +10% within 21
days — and is entered into the scheduled review in `AGENTS.md`.

### Trailing-stop ladder

| Variable | Default | Effect |
|---|---|---|
| `BREAKOUT_VERDICT_MIN_GAIN` | `0.01` | Day-3 PASS gain requirement |
| `BREAKOUT_VERDICT_MIN_VOL_PCT` | `0.75` | Day-3 PASS volume requirement |

Profit tiers are code constants (`TRAIL_PROFIT_TIERS`): +5% → 1.5%.

This is a deliberate first-leg profit lock rather than a late-stage winner ladder. See
`decisions/2026-08-22_hwm-profit-lock-arm-5pct.md` for why.

The ladder has a second input: the Prove-It Stop feeds it a percentage that pins the resting
broker order onto the current Prove-It level. Because the ladder only ever tightens, that
turns a percentage trail into a fixed price floor.

---

## Fundamental screener

| Variable | Default | Effect |
|---|---|---|
| `MIN_QUARTERLY_EPS_GROWTH` | `20` | Diluted EPS QoQ % — the strongest CAN SLIM signal |
| `MIN_ANNUAL_EPS_GROWTH` | `25` | Diluted EPS YoY TTM % |
| `MIN_REVENUE_GROWTH` | `15` | Revenue YoY TTM % — blocks cost-cutting posing as growth |
| `EXCLUDED_SECTORS` | `Finance,Real Estate,Utilities` | Set empty to disable |

Hard-coded floors: price > $15, 30-day average volume > 250,000, market cap > $300M,
`is_primary` listings only.

---

## Technical screener

| Variable | Default | Effect |
|---|---|---|
| `SMA_WINDOW` | `50` | Trend filter period |
| `VOLUME_AVG_WINDOW` | `50` | Volume baseline period |
| `VOLUME_SURGE_MIN` | `1.50` | Breakout-bar volume multiple |
| `ROLLING_HIGH_WINDOW` | `252` | Pivot lookback (~52 weeks) |
| `PIVOT_PROXIMITY` | `0.95` | Close ≥ rolling high × this |
| `RS_MIN_GATE` | `50` | Minimum RS percentile vs SPY |
| `MIN_PRICE_HISTORY` | `50` | Bars required to evaluate a name |
| `FMP_HISTORY_DAYS` | `380` | Price history fetched per ticker |
| `PRE_BREAKOUT_PROXIMITY` | `0.08` | Max distance below the high |
| `PRE_BREAKOUT_VOL_MAX` | `1.00` | Volume must be **contracting** |
| `PRE_BREAKOUT_UPTREND_MIN` | `2` | Up-closes required of the last 3 |
| `RELAXED_PRE_BREAKOUT_PROXIMITY` | `0.10` | Quota-fill variant |
| `RELAXED_PRE_BREAKOUT_VOL_MAX` | `1.10` | Quota-fill variant |
| `RELAXED_PRE_BREAKOUT_UPTREND_MIN` | `2` | Quota-fill variant |
| `RELAXED_RS_MIN_GATE` | `50` | Quota-fill variant |
| `LEARNING_MIN_ROWS` | `3` | Minimum `breakout_learnings` rows before Phase-2 penalty activates |
| `LEARNING_LOOKBACK_DAYS` | `90` | Recency window for failure-penalty learnings |

---

## AI evaluator

| Variable | Default | Effect |
|---|---|---|
| `AI_BATCH_SIZE` | `8` | Triggers per prompt — small batches avoid lost-in-the-middle degradation |
| `AI_BATCH_RETRIES` | `1` | Retries for tickers missing from a response |
| `HISTORY_LEARNING_MAX_TRADES` | `3` | Recent closed trades per ticker used for history-based penalty |
| `HISTORY_LEARNING_MAX_PENALTY` | `12` | Cap for history-based adjusted-score penalty |
| `PRE_BREAKOUT_SCORE_BOOST` | `0` | Optional additive boost for coil setups |

---

## Managed exit tool

Used by `managed_exit.py` for manual liquidation.

| Variable | Default | Effect |
|---|---|---|
| `MANAGED_EXIT_ATR_FRACTION` | `0.40` | Trail as a fraction of ATR |
| `MANAGED_EXIT_MIN_TRAIL_PCT` | `0.008` | Lower bound |
| `MANAGED_EXIT_MAX_TRAIL_PCT` | `0.030` | Upper bound |
| `MANAGED_EXIT_FLOOR_PCT` | `0.020` | Hard floor below reference |
| `MANAGED_EXIT_DEADLINE` | `15:50` | Forced completion time (ET) |
| `MANAGED_EXIT_POLL_SECONDS` | `30` | Poll interval |
| `MANAGED_EXIT_DEFAULT_ATR_PCT` | `2.0` | Used when ATR is unavailable |

---

## Infrastructure

| Variable | Default | Effect |
|---|---|---|
| `IB_GATEWAY_HOST` | `ib-gateway` | Gateway hostname |
| `IB_GATEWAY_PORT` | `4000` | Gateway API port |
| `LOG_DIR` | `/app/logs` | Log destination |
| `DB_PATH` | `./trading_bot.db` | Local SQLite (UI settings only) |

`READ_ONLY_API=no` must be set in the gateway container or order submission is rejected.

### Dependency manifests

There are two, and they are **not** interchangeable:

| Manifest | Installed into | Adds |
|---|---|---|
| `requirements.txt` (root) | execution agent, screeners, **all CI workflows** | `requests`, `pandas`, `supabase`, `httpx`, `pytest`, `watchdog`, `ib_insync`, `openai` |
| `backend/requirements.txt` | `trading-bot` container only | `fastapi`, `uvicorn`, `yfinance`, `numpy` |

The Daily Screener workflow installs **only the root manifest** and runs the
full pytest suite before any screening step. A test that imports FastAPI —
directly, or by importing a `backend/` module that does — therefore aborts
collection on CI and takes the day's fundamental scan, breakout scan and AI
evaluation with it. This will not reproduce locally, where the backend
requirements are usually installed as well.

So: **tests may import a `backend/` module only if that module is importable
with the root manifest alone.** Pure logic that needs coverage belongs in a
dependency-free module such as `backend/pricing.py`, which `backend/main.py`
imports. `tests/test_ci_import_hygiene.py` enforces this and fails with the
required fix in its message. See `decisions/2026-09-05_ci-import-hygiene.md`
for why.

---

## Database schema

### Live state

| Table | Purpose |
|---|---|
| `watchlist` | Current fundamental survivors. **Truncated and rewritten daily** |
| `daily_triggers` | Today's technical triggers, enriched with scores. **Truncated daily** |
| `portfolio_positions` | Open positions and all exit-rule state. Also carries IBKR's own valuation (`current_price`, `market_value`, `unrealized_pnl`, `ibkr_synced_at`) written by `reconcile_with_ibkr()` — these are broker marks, never FMP quotes (`migrations/add_ibkr_position_values.sql`) |
| `account_balances` | IBKR cash and equity snapshots |
| `exit_requests` | Smart OCA managed-exit queue. Outlives the position it refers to, so it doubles as the exit audit trail (`migrations/add_exit_requests.sql`) |

### Append-only research tables

| Table | Purpose |
|---|---|
| `watchlist_history` | Point-in-time fundamental snapshots, with sector |
| `trigger_history` | Every trigger ever emitted, fully scored, plus forward-return outcomes |
| `trigger_decisions` | Every buy and skip with a reason code — the control group |
| `trade_history` | Closed trades |
| `cash_flows` | Deposits and withdrawals |

Key `portfolio_positions` columns driving exits: `hwm_price`, `hwm_date`, `stop_loss_pct`,
`entry_atr_pct`, `closed_above_entry`, `power_hold`, `exit_armed*`, `breakout_verdict`,
`highest_unrealized_pct`.

### Migrations

Apply the SQL in `migrations/` before first run. Several rules degrade gracefully but
**operate below design strength** until their migration is applied — most notably
`add_closed_above_entry.sql`, without which the Prove-It Stop cannot tell a proven breakout
from an unproven one. It fails safe by treating almost every position as *proven*, which
effectively disables Phase 1 — the tight entry-anchored band that is the whole point of the
rule. `schema_guard.py` warns loudly about this.

Until `add_ibkr_position_values.sql` is applied, the dashboard has no persisted broker
marks to render, so it prices every open position from a live FMP quote labelled
`FMP estimate — not broker` — or, where no quote is available, at cost basis labelled
`Cost basis — no quote`. Trading behaviour is unaffected: no exit rule reads these
columns, and the agent prices exits from `ib.portfolio()` directly.

---

## Deploying the dashboard

The compiled React bundle ships **inside** the `trading-bot` image. Stage 1 of
`Dockerfile` runs `npm run build`, which ends in `scripts/verify-build.mjs` — that
script greps the compiled output for a list of feature fingerprints and exits
non-zero if any are missing, so a partial or stale bundle fails the image build
rather than reaching production.

**Never bind-mount a host directory over `/app/frontend/dist`.** Because `dist/`
is in `.gitignore`, a host copy is never refreshed by the deploy's
`git reset --hard origin/main`. The mount shadows the freshly built assets and
the dashboard serves whatever bundle was last compiled by hand on the box —
indefinitely, and with no error anywhere. The image build, the registry push and
the container restart all report success while the UI stays frozen.

`deploy_to_server.yml` asserts against this after every deploy: it reads
`/api/version` and fails the job if the served `git_commit` does not match the
commit checked out on the server.

To confirm what is actually running:

```bash
curl -s localhost:8000/api/version
```

---

## Changing parameters safely

1. Edit `.env`
2. `docker compose up -d` to restart the affected services
3. Confirm the value took effect in the agent log at the next cycle
Strategy parameters in this repository were selected via paired stationary-block bootstrap
across two independent universes, not by single-path optimisation. Changing one on the basis
of a single backtest run — or a handful of live trades — is how a strategy gets overfitted.
The ADRs in `decisions/` record what was tested, what was rejected, and why.
