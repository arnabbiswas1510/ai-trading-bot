# Buy Logic

The complete gate stack a trigger must clear before an order reaches the market.

**Source:** `execution_agent.py` — `run_market_open_buys()`, executed once at 09:30 ET.
Manual equivalent: `force_buy.py`.

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
| 0c | Market direction | SPY < SMA-200 | Stand down from new buys; existing positions unaffected |
| 0d | Trigger freshness | `triggered_at` within `TRIGGER_LOOKBACK_DAYS` (3) | Stale signals discarded — covers weekends and holidays |
| 0e | Capacity | `len(holdings) ≥ MAX_POSITIONS` | All candidates recorded as `SLOTS_FULL` |

### Schema integrity block

`schema_guard.py` probes the columns each live risk rule reads. If
`portfolio_positions.closed_above_entry` (Thesis Stop), `hwm_rs_score` or
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
`migrations/2026-08-13_apply_missing_migrations.sql` clears the block automatically
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
| 2 | Cooling-off | Sold within `COOLING_OFF_DAYS` (7) | `COOLING_OFF` |
| 3 | AI veto | `ai_grade == "D"` (conviction < 50) | `AI_VETO` |
| 4 | Score present | `final_score` / `adjusted_score` is NULL | `NO_AI_SCORE` |
| 5 | Score floor | Below the trigger-type minimum | `SCORE_FLOOR` |
| 6 | Capacity (in-loop) | Slots filled by an earlier buy this cycle | `SLOTS_FULL` |
| 7 | Cash floor | `available_cash < MIN_POSITION_SIZE` ($5,000) | `INSUFFICIENT_CASH` |
| 8 | Volume surge | **`BREAKOUT` only:** `volume_surge < MIN_VOL_SURGE_GATE` (0.75×) | `SCORE_FLOOR` |
| 9 | PRE_BREAKOUT 52W distance | PRE_BREAKOUT > `MAX_PRE_BREAKOUT_PIVOT_DIST` (5%) below 52W high | `BELOW_PIVOT` |
| 10 | Contract | IBKR cannot qualify the contract | `LOOP_HALTED` *(halts loop)* |
| 11 | Price | No IBKR price and no trigger close | `NO_PRICE` |
| 12 | Buy zone — ceiling | `> pivot × (1 + MAX_PIVOT_EXTENSION)` | `EXTENDED_ABOVE_PIVOT` |
| 13 | Buy zone — floor | `< pivot × (1 − MAX_PIVOT_BREAKDOWN)` | `BELOW_PIVOT` |
| 14 | Share count | `shares ≤ 0` after safety reserve | `SHARES_ZERO` |
| 15 | Fill | Order filled 0 shares | `BUY_FAILED` *(halts loop)* |
| — | Success | Order filled | `BOUGHT` |

Capacity is re-checked **inside** the loop (gate 6) because an earlier fill in the same
cycle may have consumed the last slot.

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
position_size   = available_cash / remaining_slots
shares          = int((position_size − PRICE_SAFETY_RESERVE) / current_price)
```

Recomputed before every buy, so capital is divided among the slots that remain rather than
committed on a fixed schedule.

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
   `entry_atr_pct` is what later parameterises the [Thesis Stop](sell_logic.md#3-thesis-stop--days-25) —
   if it is not captured at entry, that rule falls back to a generic 3.0%.
6. A Telegram notification is dispatched.

---

## Parameter reference

| Parameter | Default | Effect |
|---|---|---|
| `MAX_POSITIONS` | `5` | Concurrent positions; single source in `config.py` for every module that buys. The Early Dollar Stop reads `EFFECTIVE_POSITION_SLOTS` instead — see `docs/configuration.md` |
| `MIN_POSITION_SIZE` | `5000` | Cash floor below which no buy is attempted |
| `PRICE_SAFETY_RESERVE` | `1000` | Withheld per order to absorb quote lag |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Trigger freshness window |
| `COOLING_OFF_DAYS` | `7` | Re-entry block after a sale |
| `MAX_PIVOT_EXTENSION` | `0.05` | Buy-zone ceiling above pivot |
| `MAX_PIVOT_BREAKDOWN` | `0.02` | Buy-zone floor below pivot |
| `MIN_VOL_SURGE_GATE` | `0.75` | Minimum volume surge multiple, **confirmed `BREAKOUT` triggers only** (AI-independent hard gate) |
| `MAX_PRE_BREAKOUT_PIVOT_DIST` | `0.05` | Max distance below 52W high for PRE_BREAKOUT entries |
| `MIN_TRIGGER_SCORE` | `60` | Floor for `BREAKOUT` |
| `MIN_PRE_BREAKOUT_SCORE` | `65` | Floor for `PRE_BREAKOUT` |
| `MIN_RELAXED_TRIGGER_SCORE` | `58` | Floor for `PRE_BREAKOUT_RELAXED` |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | SPY vs SMA-200 buy gate |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | Regime lookback |

---

## Audit trail

Every decision — buy and skip alike — is appended to `trigger_decisions`. Reason codes carry
an `is_capacity` flag:

| Class | Codes | Interpretation |
|---|---|---|
| Quality | `AI_VETO`, `SCORE_FLOOR`, `NO_AI_SCORE`, `EXTENDED_ABOVE_PIVOT`, `BELOW_PIVOT` | The model judged the candidate |
| Capacity | `SLOTS_FULL`, `INSUFFICIENT_CASH`, `SHARES_ZERO` | The model never got to judge |

The distinction is what allows the opportunity cost of `MAX_POSITIONS` to be measured
separately from the accuracy of the scoring model. A name skipped for want of a slot says
nothing about scoring quality — but a lot about the cost of concentration.

All audit writes are non-fatal: research instrumentation must never interrupt live trading.
