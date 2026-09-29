# Buy Logic

The complete gate stack a trigger must clear before an order reaches the market.

**Source:** `buying.py` — `run_market_open_buys()`, executed once at 09:30 ET
(re-exported through `execution_agent`).
Manual equivalent: `force_buy.py`.

**Decision core:** the *decision* itself — ranking, the per-trigger gate ladder,
and position sizing — lives in the pure, I/O-free module `decision_core.py`.
`run_market_open_buys()` fetches the live inputs (cash, price, days-to-earnings)
and performs the resulting order/DB/notify side effects, but every verdict below
is computed by `decision_core`. This is the single source the research backtester
also calls, so live and backtest entry selection are identical by construction.
See `decisions/2026-09-29_decision-core-extraction.md` for why.

---

## Design principle: fail closed

Every ambiguous condition resolves to *no trade*. Missing AI score, unavailable price,
unreachable market data — each is a rejection, not a neutral. An un-vetted trigger is an
unknown, and unknowns are not funded.

Two failures **halt the entire buy loop** rather than skipping to the next candidate:
contract qualification failure (`LOOP_HALTED`) and a zero-share fill (`BUY_FAILED`). Both
indicate the brokerage connection is not behaving as expected, and continuing to submit
orders in that state risks compounding the fault.

---

## Pre-flight: portfolio-level blocks

Checked once, before any candidate is considered.

| # | Block | Condition | Behaviour |
|---|---|---|---|
| 0a | Schema integrity | A column a live risk rule depends on is missing | **Zero buys.** Monitoring and exits continue normally |
| 0b | Margin loan | `margin_loan > 0` | **Zero buys.** The system never trades on borrowed money |
| 0c | Market direction | Benchmark (SPY) not >0.5% above its SMA-200, or every SMA-200 falling | Stand down from new buys; existing positions unaffected |
| 0d | Trigger freshness | `triggered_at` within `TRIGGER_LOOKBACK_DAYS` (3) | Stale signals discarded — covers weekends and holidays |
| 0e | Capacity | `len(holdings) ≥ MAX_POSITIONS` | All candidates recorded as `SLOTS_FULL` |

### Schema integrity block

`schema_guard.py` probes the columns each live risk rule reads. If
`portfolio_positions.closed_above_entry` (the Prove-It Stop's phase discriminator), `hwm_rs_score` or
`highest_rs_score` (Rule 1, RS Decay) is absent, the agent **refuses to open new
positions** while continuing to monitor and exit existing ones.

The asymmetry is deliberate. A missing column does not raise — the rule that reads it
quietly takes a fallback path — so a degraded risk control produces no error. Opening
fresh positions while the controls meant to protect them are impaired is the specific
mistake being prevented; aborting the daemon instead would stop trailing-stop maintenance
and exits, which is strictly worse.

Analytics archives (`trigger_history`, `trigger_decisions`, `watchlist_history`) are
**advisory** — their absence warns but never blocks trading.

The check re-runs every buy cycle, so applying
`migrations/20260813_apply_missing_migrations.sql` clears the block automatically
without restarting the container. Telegram receives one alert when the degradation is
detected and one when it is resolved — not one per 15-minute cycle.
See `decisions/2026-08-14_schema-guard-fail-loud.md` for why.

The market filter **fails closed**: if SPY data cannot be retrieved, the market is treated
as bearish. Standing down costs opportunity; buying blind into an unknown regime costs
capital.

Candidates are then sorted by score, **highest first**, so the strongest setup claims
capital before weaker ones.

---

## Per-candidate gate stack

Evaluated in this order. Every rejection is written to `trigger_decisions` with a reason
code, which is what makes the buy model auditable after the fact.

| # | Gate | Rejection condition | Reason code |
|---|---|---|---|
| 1 | Duplicate | Ticker already held | `ALREADY_HELD` |
| 2 | Cooling-off (reason-aware) | Sold **at a loss** within `COOLING_OFF_DAYS` (3), **or** sold **today** at any P&L (same-session churn guard), per `trade_history` **or** an `ibkr_fills` SLD fill. A **profit** sale older than today does **not** block. | `COOLING_OFF` |
| 3 | AI veto | `ai_grade == "D"` (conviction < 50) | `AI_VETO` |
| 4 | Earnings blackout | Next scheduled earnings within `EARNINGS_BLACKOUT_TRADING_DAYS` (3) NYSE trading days. **Deferral, not veto** — the breakout can be re-bought after the report. **Fails OPEN**: a missing/unparseable/past `next_earnings_date` allows the buy. | `EARNINGS_IMMINENT` |
| 5 | Score present | `final_score` / `adjusted_score` is NULL | `NO_AI_SCORE` |
| 6 | Score floor | Below the trigger-type minimum (`adjusted_score` when present) | `SCORE_FLOOR` |
| 7 | Capacity (in-loop) | Slots filled by an earlier buy this cycle | `SLOTS_FULL` |
| 8 | Cash floor | `available_cash < MIN_POSITION_SIZE` ($5,000) | `INSUFFICIENT_CASH` |
| 9 | Volume surge | **`BREAKOUT` only:** `volume_surge < MIN_VOL_SURGE_GATE` (0.75×) | `SCORE_FLOOR` |
| 10 | PRE_BREAKOUT 52W distance | PRE_BREAKOUT > `MAX_PRE_BREAKOUT_PIVOT_DIST` (5%) below 52W high | `BELOW_PIVOT` |
| 11 | Contract | IBKR cannot qualify the contract | `LOOP_HALTED` *(halts loop)* |
| 12 | Price | No IBKR price and no trigger close | `NO_PRICE` |
| 13 | Buy zone — ceiling | `> pivot × (1 + MAX_PIVOT_EXTENSION)` | `EXTENDED_ABOVE_PIVOT` |
| 14 | Buy zone — floor | `< pivot × (1 − MAX_PIVOT_BREAKDOWN)` | `BELOW_PIVOT` |
| 15 | Share count | `shares ≤ 0` after safety reserve | `SHARES_ZERO` |
| 16 | Fill | Order filled 0 shares | `BUY_FAILED` *(halts loop)* |
| — | Success | Order filled | `BOUGHT` |

Capacity is re-checked **inside** the loop (gate 7) because an earlier fill in the same
cycle may have consumed the last slot.

The **earnings blackout** (gate 4) exists because a fresh position sits under the Prove-It
stop's tight floor (−1% day 0, −3% day 1+), so opening just before a report is close to a
guaranteed stop-out on the post-earnings gap — plus the loss then trips the reason-aware
cooling-off lockout, compounding an avoidable, fully knowable cost. `next_earnings_date` is
fetched per trigger by `ai_evaluator.py` from FMP `/stable/earnings` and stored on
`daily_triggers`; the local buy loop enforces the deferral as a pure trading-day date
comparison, so the protection is deterministic and independent of the AI. It fails **open**
so a per-name data gap never blocks every buy (contrast the market-direction gate, which
fails **closed**). See `decisions/2026-09-28_earnings-blackout-and-news-veto.md`.

### Gate 5 scores on `final_score` — the failure penalty is off

`adjusted_score = final_score − failure_penalty − history_penalty`. The breakout
`failure_penalty` ships **disabled** (`FAILURE_PENALTY_MAX_POINTS = 0`), so
`adjusted_score` equals `final_score` for every trigger and gate 5 compares the
AI-evaluated score directly against the floor (60 for `BREAKOUT`, 65 for
`PRE_BREAKOUT`, 58 for `PRE_BREAKOUT_RELAXED`).

The penalty was measured on 2026-09-17 and found to score **every** past trade
at the cap — all 10 winners and all 6 losers identically — so it removed good
and bad candidates at the same rate. On that day it rejected six BREAKOUT
triggers, five of them AI grade **A**, including DHT (`final_score` 77 → 57).
See `decisions/2026-09-17_failure-penalty-disabled.md` for the measurement.

The `history_penalty` (per-ticker recent-loss penalty, `HISTORY_LEARNING_MAX_PENALTY`)
is unaffected and still applies.

### Cooling-off is reason-aware, and reads the broker

Gate 2 does **two separable jobs** (`cooling_off.compute_cooled_map`, shared by
the agent, `force_buy.py` and `rotate_positions.py`):

- **(A) Same-session churn guard — unconditional.** A name sold **today** (NY
  calendar day) is blocked whether it was a profit or a loss, because selling and
  re-buying the same name inside one session blends the IBKR `averageCost` basis
  and poisons every downstream stop/P&L/size calc.
- **(B) Calendar block — loss exits only.** A name whose most-recent sale within
  `COOLING_OFF_DAYS` (3) was a **loss** (realised P&L ≤ 0, or unrecorded) is
  blocked for the full window — re-buying a name sold *because it was falling*
  catches a knife (−$1,750 / 30% win across 10 real prior-loss re-entries). A
  name sold **at a profit** older than today is **not** blocked: it is a proven
  leader left to the buy-quality gates (extension, breakout quality, RS) to
  judge, not idled for days. This is why Friday 2026-09-25 went from a forced
  zero-trade day to eligible.

Both jobs check **two sources**, because `trade_history` alone is not sufficient:
that table is written by the bot's own sell path, so an exit that happened
without it — a resting IBKR stop firing between monitor cycles, or a failed write
— leaves no row and the gate goes blind. `ibkr_fills` is written by the
real-time fill hook the instant IBKR reports an execution, so it sees the sell
either way. A same-day `SLD` fill blocks on its own; an `SLD` in the window with
no ledger row is blocked conservatively (reason unknown).

NTRA on 2026-08-31 is why the same-session guard exists: the bot sold 61 shares
at 10:26 and bought 61 shares of the same ticker back at **10:32, six minutes
later**, because the 10:26 exit never reached `trade_history`. See
`decisions/2026-09-26_reason-aware-cooling-off.md` and
`decisions/2026-09-10_lot-basis-and-broker-aware-cooling-off.md`.

### ⚠️ `volume_surge` is an overloaded column

`daily_triggers.volume_surge` carries **two different metrics with opposite
polarity**, depending on `trigger_type`. Read this before writing any rule that
consumes it.

| Trigger type | What the column holds | Screener gate | Good direction |
|---|---|---|---|
| `BREAKOUT` | today's volume ÷ 50-day avg | `≥ VOLUME_SURGE_MIN` (1.50) | **higher** |
| `PRE_BREAKOUT` | 3-day avg volume ÷ 50-day avg (**contraction**) | `< PRE_BREAKOUT_VOL_MAX` (1.00) | **lower** |
| `PRE_BREAKOUT_RELAXED` | same contraction ratio | `< RELAXED_PRE_BREAKOUT_VOL_MAX` (1.10) | **lower** |

On a pre-breakout, volume drying up while the stock coils beneath its pivot is
the *constructive* signal — supply exhausting before the move. Applying a
**minimum** to that number rejects the tightest coils and admits the loosest.
Gate 8 is scoped to `BREAKOUT` for exactly this reason.
See `decisions/2026-08-19_volume-gate-inversion.md` for why.

### Score floors by trigger type

| Trigger type | Minimum | Parameter |
|---|---|---|
| `BREAKOUT` | 60 | `MIN_TRIGGER_SCORE` |
| `PRE_BREAKOUT` | 65 | `MIN_PRE_BREAKOUT_SCORE` |
| `PRE_BREAKOUT_RELAXED` | 58 | `MIN_RELAXED_TRIGGER_SCORE` |

A pre-breakout coil is held to a *higher* bar than a confirmed breakout. It has not yet
proven itself with a volume surge, so more evidence is demanded elsewhere.

### The buy zone

```
pivot × (1 − 0.02)  ≤  entry price  ≤  pivot × (1 + 0.05)
```

Bounded on **both** sides:

- **Above** — beyond +5% the risk/reward has inverted. The stop now sits far below and the
  initial thrust is spent. O'Neil is explicit that chasing extended stock is a losing game.
- **Below** — more than 2% under the pivot means price has fallen back *into* the base. The
  breakout the trigger described is no longer in effect; this is a failed breakout, not a
  discount.

---

## Position sizing

```
remaining_slots = max(1, MAX_POSITIONS − held_count)
position_size   = min(available_cash / remaining_slots,   # base: free cash across free slots
                      NetLiquidation / MAX_POSITIONS)      # HARD CEILING: one equal-weight slot
shares          = int((position_size − PRICE_SAFETY_RESERVE) / current_price)
```

Recomputed before every buy, so capital is divided among the slots that remain rather than
committed on a fixed schedule.

**No single position may exceed an equal-weight share of the account** —
`NetLiquidation / MAX_POSITIONS` (e.g. a $111,530 account with 5 slots caps each position at
$22,306). This ceiling is the important half of the formula: the base rule
`available_cash / remaining_slots` alone oversizes a *replacement* position whenever the book
is nearly full but a large cash pile is free. When only one slot is open, `remaining_slots` is
1 and the base rule pours 100% of free cash into that single name. On 2026-09-21 that sized MPC
at `$37,916 / 1 = $36,206` and PSX at `$37,184 / 1 = $35,856` — roughly 1.6× the equal-weight
share and nearly 2× the morning cohort — so a routine −2% stop lost ~$720 on each instead of
~$400. The exits fired correctly; the loss came entirely from size. The cap makes that
impossible. See `decisions/2026-09-21_equity-capped-position-size.md`.

If IBKR's `NetLiquidation` read is momentarily unavailable (returns 0), equity is reconstructed
from cash + the IBKR-synced market value of current holdings so the ceiling still binds — it
never silently falls back to the uncapped formula.

`PRICE_SAFETY_RESERVE` ($1,000) is withheld because IBKR's price feed can lag the market by
15–20 minutes. Sizing against a stale quote can produce an order that exceeds settled cash;
the reserve absorbs the discrepancy.

**Consequence of a 5-slot book:** a full portfolio is ~20% per position. With `MAX_POSITIONS`
raised mid-flight, existing positions retain their original larger sizing and the book only
converges to even weighting after full turnover.

---

## Order placement and post-fill

1. **Market order**, TIF `DAY` (explicit, to avoid IBKR error 10349), submitted at the open.
2. Fill is polled until `Filled`, `Cancelled` or `Inactive`. A partial fill is accepted; a
   zero fill halts the loop.
3. **The position is written to Supabase _before_ the trailing stop is placed.** This
   ordering is deliberate: an exception during stop placement would otherwise leave a
   position filled at IBKR but absent from the database, and the capacity check — which
   counts database rows — would authorise further buys against capital already committed.
4. A GTC `TRAIL` order is registered at
   `max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT, 2.5 × entry_atr_pct))`.
5. `entry_atr_pct`, `hwm_price`, `hwm_date` and entry-conviction fields are persisted.
   `entry_atr_pct` is what later parameterises the [base trailing stop's ATR band](sell_logic.md#1-dynamic-trailing-stop-ibkr-managed) —
   if it is not captured at entry, that rule falls back to a generic 3.0%.
6. A Telegram notification is dispatched.

---

## Parameter reference

| Parameter | Default | Effect |
|---|---|---|
| `MAX_POSITIONS` | `5` | Concurrent positions; single source in `config.py` for every module that buys. No exit rule derives a threshold from the slot count |
| `MIN_POSITION_SIZE` | `5000` | Cash floor below which no buy is attempted |
| `PRICE_SAFETY_RESERVE` | `1000` | Withheld per order to absorb quote lag |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Trigger freshness window |
| `COOLING_OFF_DAYS` | `3` | Re-entry block after a sale |
| `EARNINGS_BLACKOUT_TRADING_DAYS` | `3` | Defer opening a position when its next earnings is within this many NYSE trading days; fails open on a missing/past date |
| `MAX_PIVOT_EXTENSION` | `0.05` | Buy-zone ceiling above pivot |
| `MAX_PIVOT_BREAKDOWN` | `0.02` | Buy-zone floor below pivot |
| `MIN_VOL_SURGE_GATE` | `0.75` | Minimum volume surge multiple, **confirmed `BREAKOUT` triggers only** (AI-independent hard gate) |
| `MAX_PRE_BREAKOUT_PIVOT_DIST` | `0.05` | Max distance below 52W high for PRE_BREAKOUT entries |
| `MIN_TRIGGER_SCORE` | `60` | Floor for `BREAKOUT` |
| `MIN_PRE_BREAKOUT_SCORE` | `65` | Floor for `PRE_BREAKOUT` |
| `MIN_RELAXED_TRIGGER_SCORE` | `58` | Floor for `PRE_BREAKOUT_RELAXED` |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Master switch for the CANSLIM "M" buy gate |
| `MARKET_DIRECTION_TICKERS` | `SPY` | Benchmark(s); **every** one must clear the buffer. Retuned to SPY-only on 2026-09-28 |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | Regime lookback |
| `MARKET_DIRECTION_BUFFER_PCT` | `0.005` | Dead-band above the SMA-200 |
| `MARKET_DIRECTION_SLOPE_DAYS` | `20` | Slope lookback; **at least one** SMA-200 must be non-falling |
| `MARKET_DIRECTION_MAX_STALE_DAYS` | `5` | Older price data is treated as unusable → bearish |

---

## Audit trail

Every decision — buy and skip alike — is appended to `trigger_decisions`. Reason codes carry
an `is_capacity` flag:

| Class | Codes | Interpretation |
|---|---|---|
| Quality | `AI_VETO`, `SCORE_FLOOR`, `NO_AI_SCORE`, `EXTENDED_ABOVE_PIVOT`, `BELOW_PIVOT` | The model judged the candidate |
| Timing | `EARNINGS_IMMINENT` | A good setup deferred until after its earnings report |
| Capacity | `SLOTS_FULL`, `INSUFFICIENT_CASH`, `SHARES_ZERO` | The model never got to judge |

The distinction is what allows the opportunity cost of `MAX_POSITIONS` to be measured
separately from the accuracy of the scoring model. A name skipped for want of a slot says
nothing about scoring quality — but a lot about the cost of concentration.

All audit writes are non-fatal: research instrumentation must never interrupt live trading.

---

## Daily "unfilled slots" summary

While the market is open the buy check runs every 15 minutes. At the **first**
cycle of each ET day on which the portfolio has at least one idle slot, the agent
sends a single Telegram summary explaining why the empty slots were not filled.
It is silent when the book is full (five positions held) and never fires more than
once per day.

The reason it reports is either:

- the **single top-level cause** when the whole cycle stood down early — market
  direction bearish (CAN SLIM 'M' gate), margin loan active, schema degraded, or
  the screener produced no breakout triggers; or
- an **aggregated per-reason breakdown** of that day's `trigger_decisions` when
  candidates were evaluated but none cleared the gates, e.g. *"2 below the
  quality-score floor (XYZ, QRS); 1 extended too far above the pivot (TUV)"*.

Once-per-day delivery is deduplicated in the `daily_notifications` table
(`report_type='unfilled_slots'`, one row per ET date), so it survives container
restarts rather than re-sending after every deploy. The dedup probe fails **safe**:
if the table is missing the summary is suppressed, never spammed, until
`migrations/20260928_add_daily_notifications.sql` is applied. The day is marked
sent only after Telegram accepts the message, so a transient failure retries.

The feature is always on and has no environment variable. See
`decisions/2026-09-28_unfilled-slot-daily-alert.md` for why.

