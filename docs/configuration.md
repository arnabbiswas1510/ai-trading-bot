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
| `FMP_API_KEY` | yes | Price and fundamental data |
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
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Suspend buys when SPY < SMA-200 |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | Regime lookback |
| `MARKET_DIRECTION_TICKER` | `SPY` | Regime benchmark |

The market filter gates **buying only** — it never forces an exit — and fails closed on
missing data.

---

## Exits

### Thesis Stop

| Variable | Default | Effect |
|---|---|---|
| `THESIS_STOP_ENABLED` | `true` | Master switch |
| `THESIS_STOP_ATR_MULT` | `1.0` | Threshold in units of entry ATR |
| `THESIS_STOP_START_DAY` | `2` | First eligible trading day |
| `THESIS_STOP_LAST_DAY` | `5` | Last eligible trading day |
| `THESIS_STOP_ATR_FALLBACK` | `3.0` | Used when `entry_atr_pct` is missing |

Lowering `THESIS_STOP_ATR_MULT` cuts sooner and more often; 0.75 was tested and produced a
wider confidence interval than 1.0.

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
| `OCA_EXIT_DEFAULT_ATR_PCT` | `3.0` | Used when the position has no ATR on record |
| `OCA_EXIT_DEFAULT_FLOOR_PCT` | `0.05` | Hard floor below the placement price when the request sets none. **Not optional in effect** — the automated ladder is suspended for managed tickers, so this is the protection |
| `OCA_EXIT_DEFAULT_EXPIRY_DAYS` | `3` | Trading days before an unfilled OCA is closed at market |

Raising `OCA_EXIT_DEFAULT_FLOOR_PCT` widens the worst case on every managed
position, because no other stop is active while the OCA is placed. See
`decisions/2026-08-18_smart-oca-managed-exit.md`.

### Early loss and the superseded minimiser

| Variable | Default | Effect |
|---|---|---|
| `EARLY_LOSS_STOP_PCT` | `0.02` | Kill-switch threshold, days 0–1 |
| `EARLY_DOLLAR_STOP_AMOUNT` | `500` | Max dollar loss per position on days 0–`EARLY_DOLLAR_STOP_MAX_DAY`; 0 to disable |
| `EARLY_DOLLAR_STOP_MAX_DAY` | `5` | Last trading day (inclusive) the dollar stop is active |
| `INTRADAY_MINIMISER_ENABLED` | `false` | **Superseded by the Thesis Stop** |
| `INTRADAY_PULLBACK_PCT` | `0.02` | Only used if the minimiser is re-enabled |
| `INTRADAY_MINIMISER_START_DAY` | `2` | Only used if the minimiser is re-enabled |

Re-enabling the minimiser is not recommended: it fired on positions that had rallied back to
break-even — i.e. positions that were working — and roughly halved expectancy.

### Moving-average exit

| Variable | Default | Effect |
|---|---|---|
| `EXIT_MA_TRIGGER_ENABLED` | `true` | Master switch |
| `EXIT_MA_TYPE` | `EMA` | `EMA` or `SMA` |
| `EXIT_MA_WINDOW` | `21` | Period |
| `EXIT_MA_BUFFER_PCT` | `0.01` | Breach tolerance below the MA |
| `EXIT_MA_EOD_ONLY` | `true` | Restrict to 15:45–16:00 ET |

Setting `EXIT_MA_EOD_ONLY=false` allows an intraday wick to force a sale the close would not
have justified.

### Plateau and rotation

| Variable | Default | Effect |
|---|---|---|
| `STALE_EXIT_ENABLED` | `true` | Master switch |
| `STALE_EXIT_DAYS` | `10` | Trading days without a new HWM |
| `STALE_EXIT_MIN_DAYS_HELD` | `7` | Earliest eligible day |
| `RANK_REPLACE_THRESHOLD` | `15` | Rotation margin, verdict `PASS` |
| `RANK_REPLACE_FAIL_THRESHOLD` | `5` | Rotation margin, verdict `FAIL` |
| `MOMENTUM_HEALTH_RS_WEIGHT` | `0.40` | Mₜ weight — relative strength |
| `MOMENTUM_HEALTH_VOL_WEIGHT` | `0.35` | Mₜ weight — volume |
| `MOMENTUM_HEALTH_SENT_WEIGHT` | `0.25` | Mₜ weight — sentiment |

### Power Hold

| Variable | Default | Effect |
|---|---|---|
| `POWER_HOLD_ENABLED` | `true` | Master switch |
| `POWER_HOLD_GAIN_PCT` | `20.0` | Gain required to arm |
| `POWER_HOLD_TRIGGER_DAYS` | `21` | Arming window (**calendar** days) |
| `POWER_HOLD_DURATION_DAYS` | `56` | Protection length (**calendar** days) |
| `POWER_HOLD_TRAIL_PCT` | `0.30` | Trail while power-held |

Disabling Power Hold re-imposes the profit ladder on winners, which caps them near +20%. The
strategy's returns are outlier-dependent; this switch has more effect on total return than
almost any other.

### Trailing-stop ladder

| Variable | Default | Effect |
|---|---|---|
| `TRAIL_TIME_TIERS_ENABLED` | `false` | Age-based tightening (off) |
| `BREAKOUT_VERDICT_MIN_GAIN` | `0.01` | Day-3 PASS gain requirement |
| `BREAKOUT_VERDICT_MIN_VOL_PCT` | `0.75` | Day-3 PASS volume requirement |

Profit tiers are code constants (`TRAIL_PROFIT_TIERS`): +20% → 6.5%, +30% → 6.0%,
+50% → 5.0%.

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

---

## AI evaluator

| Variable | Default | Effect |
|---|---|---|
| `AI_BATCH_SIZE` | `8` | Triggers per prompt — small batches avoid lost-in-the-middle degradation |
| `AI_BATCH_RETRIES` | `1` | Retries for tickers missing from a response |
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

---

## Database schema

### Live state

| Table | Purpose |
|---|---|
| `watchlist` | Current fundamental survivors. **Truncated and rewritten daily** |
| `daily_triggers` | Today's technical triggers, enriched with scores. **Truncated daily** |
| `portfolio_positions` | Open positions and all exit-rule state |
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
`add_closed_above_entry.sql`, without which the Thesis Stop uses a conservative fallback and
fires less often than intended.

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
