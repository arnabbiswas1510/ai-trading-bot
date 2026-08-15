# Sell Logic

Every exit rule in the live agent, in evaluation order.

**Source:** `execution_agent.py` — `monitor_portfolio_intraday()` (15-minute cycle),
`execute_sell()` (market liquidation), `arm_exit()` (armed trailing exit).

---

## Day counting

Every day-gated rule uses `trading_days_between(buy_date, today)`, which counts NYSE
sessions over the **half-open interval `[buy_date, today)`** — weekends and market holidays
excluded.

| Bought Monday | days_held |
|---|---|
| Monday | 0 |
| Tuesday | 1 |
| Friday | 4 |
| following Monday | 5 |

This matters: the Thesis Stop window (days 2–5) closes at the *start* of the second Tuesday,
not a calendar week later.

---

## Evaluation order

Checks run per position, per cycle. **The first rule to fire pre-empts everything below it**
for that cycle — an early `continue` means later rules are unreachable until the next poll.

| # | Check | Days | Window | Action |
|---|---|---|---|---|
| 1 | Armed-exit deadline | 0–6 | every cycle | force market sell |
| 2 | Early Loss Kill-switch | 0–1 | every cycle | arm exit |
| 3 | HWM / peak metric update | all | every cycle | *(no exit)* |
| 4 | Trail tightening + power-hold arming | all | every cycle | *(re-places broker order)* |
| 5 | **Thesis Stop** | 2–5 | every cycle | arm exit |
| 6 | Intraday Loss Minimiser | 2+ | every cycle | **disabled by default** |
| 7 | Trailing-stop self-heal | all | every cycle | *(no exit)* |
| 8 | EMA-21 support breach | 7+ | EOD 15:45–16:00 | market sell |
| 9 | Plateau exit | 7+ | EOD 15:45–16:00 | market sell |
| 10 | Day-3 breakout verdict | 3 | EOD, once | *(records verdict)* |
| 11 | Follow-through latch update | all | EOD | *(sets `closed_above_entry`)* |
| 12 | Rank & Replace | 7+ | EOD, once daily | market sell + refill |

---

## 1. Dynamic trailing stop (IBKR-managed)

A native GTC `TRAIL` order placed immediately after fill. Because it is broker-side, **it is
the only exit that survives the bot being offline.**

**Initial distance** is volatility-scaled from the trigger's ATR:

```
stop_pct = max(STOP_LOSS_PCT, min(ATR_STOP_MAX_PCT, 2.5 × entry_atr_pct))
         = max(10%,           min(12%,              2.5 × ATR%))
```

A fixed percentage is simultaneously too tight for a high-beta name and too loose for a slow
one. Scaling by ATR asks the same question of every stock: *has price moved beyond what its
own volatility explains?*

**Ratchet (one-way, tightens only):**

| Unrealised gain | Trail |
|---|---|
| < +20% | initial (10–12%) |
| ≥ +20% | 6.5% |
| ≥ +30% | 6.0% |
| ≥ +50% | 5.0% |

Time-based tightening (`TRAIL_TIME_TIERS_ENABLED`) exists but is **off by default**.

**Under Power Hold the ladder is bypassed** and the trail widens to
`POWER_HOLD_TRAIL_PCT` (30%). See [Power Hold](#power-hold-oneils-8-week-rule).

The agent re-places the order when a tier is crossed, and self-heals a missing stop every
cycle.

---

## 2. Early Loss Kill-switch — days 0–1

```
current_price ≤ buy_price × (1 − EARLY_LOSS_STOP_PCT)      # default 2%
```

Arms the exit. A breakout that reverses 2% within its first two sessions has already
falsified its premise; the base 10–12% trailing stop is far too wide to be useful this early.

---

## 3. Thesis Stop — days 2–5

The primary loss-cutting rule. Fires when **both** conditions hold:

1. The position has **never closed above its entry price** (`closed_above_entry` is false)
2. `unrealized_pct ≤ −THESIS_STOP_ATR_MULT × entry_atr_pct` (default multiplier 1.0)

With entry ATR of 2.8%/day, the trigger is −2.8%. With 4.0%/day, −4.0%. If `entry_atr_pct`
is missing, `THESIS_STOP_ATR_FALLBACK` (3.0%) is used.

Suppressed by `power_hold` and by an already-armed exit.

### Why the latch is the whole rule

The predecessor — the **Intraday Loss Minimiser** — required the day's high to reach entry
before selling on a pullback. That inverted the intended population: it fired on positions
that had rallied *back* to break-even, i.e. positions that were working. Measured effect was
roughly a halving of expectancy. It remains in the code but is **disabled by default**
(`INTRADAY_MINIMISER_ENABLED=false`).

The Thesis Stop targets the opposite set: breakouts that never followed through at all.
`closed_above_entry` is a latch — once true it is never cleared, so a position that
established follow-through is permanently exempt.

> **Migration dependency — and why an intraday poke must not exempt a position.**
> The latch requires `migrations/add_closed_above_entry.sql` (consolidated into
> `migrations/2026-08-13_apply_missing_migrations.sql`). Until it is applied, a PGRST204
> fallback treats *any* evidence of trading above entry (`highest_unrealized_pct > 0`,
> `hwm_price > buy_price`, `intraday_high_today > buy_price`) as follow-through. All three
> are **intraday** measures, so a single tick above entry permanently exempts a position
> that never *closed* there.
>
> That fallback is not equivalent to the rule and must not be relied on. On a breakout day
> the open is often near the high, so most entries poke above entry within minutes: NBIX
> printed above 168.33 intraday and closed below it on all 11 subsequent sessions; DELL
> printed 514.00 on day 0 against a 496.04 entry and closed 494.51. Both were exempted by
> the fallback and neither ever closed above entry.
>
> Measured cost of the fallback versus the close-based latch (`research/latch_bt.py`,
> paired stationary-block bootstrap): **−9.9 ΔCAGR in the screener-passing universe,
> 90% CI [+3.35, +18.40] in favour of the close latch, P=100%**; neutral in the broad
> universe (CI spans zero). The fallback also fires the stop only 21 times versus 34, so it
> is not selectively sparing winners — it broadly disables the rule.
>
> Since 2026-08-14 a missing latch column is detected by `schema_guard.py`, which **blocks
> all new buys** until the migration is applied rather than letting the degradation pass
> unnoticed. See `docs/buy_logic.md` and
> `decisions/2026-08-14_schema-guard-fail-loud.md`.

> **Backfill caution.** When applying the migration, `closed_above_entry` must **not** be
> backfilled from `highest_unrealized_pct`, which is derived from the live intraday price
> and would reproduce exactly the defect above. The repair script only pre-sets the latch
> for positions already past the thesis window (`days_held > 5`), where it cannot change
> behaviour, and leaves in-window positions `false` so the next EOD close establishes the
> truth.

### Validation

Paired stationary-block bootstrap, 2,000 resamples, two independent universes:

| Universe | ΔCAGR | 90% CI | P(improvement) |
|---|---|---|---|
| Screener-passing (n≈78) | **+18.8** | [+7.1, +33.0] | 100% |
| Broad (n=256) | −0.7 | [−15.9, +15.6] | 47% |

Average loss improved in both (−4.43% → −3.52% and −5.60% → −4.39%). Armed exit outperformed
an immediate market sell in both. Multiplier 1.0 was chosen over 0.75 for the tighter CI
lower bound. Details: `decisions/2026-08-09_thesis-stop.md`.

---

## 4. EMA-21 support breach — day 7+

```
current_price < EMA(21) × (1 − EXIT_MA_BUFFER_PCT)     # default 1% buffer
```

Evaluated **only in the 15:45–16:00 ET window** by default (`EXIT_MA_EOD_ONLY=true`), which
prevents an intraday wick from triggering a sale that the close would not have justified.

Suppressed before day 7 so that normal post-breakout consolidation is not read as failure,
and suppressed entirely by Power Hold. Fires as a market sell.

---

## 5. Plateau exit — day 7+

```
trading_days_between(hwm_date, today) ≥ STALE_EXIT_DAYS     # default 10
```

`hwm_date` advances every cycle the position prints a new high. If it stops advancing, the
clock runs.

This is a **capital velocity** rule, not a risk rule. The position may sit comfortably above
its trailing stop and its EMA — but with a hard cap of 5 slots, dead money costs the return
of the best trigger it is blocking. Suppressed by Power Hold; fires as a market sell in the
EOD window.

---

## 6. Rank & Replace — day 7+

Runs once daily at EOD, and only when the book is full and fresh triggers exist.

Each holding gets a **momentum health score** Mₜ:

```
Mₜ = 0.40 × live RS + 0.35 × volume ratio + 0.25 × sentiment
```

Rotation occurs when the best available trigger's score exceeds Mₜ by a margin that depends
on the day-3 verdict:

| Breakout verdict | Required margin |
|---|---|
| `PASS` | 15 points |
| `FAIL` | 5 points |

A breakout that already failed to confirm has forfeited the benefit of the doubt. Suppressed
by Power Hold.

---

## Day-3 breakout verdict

Evaluated once, at day-3 EOD:

- **PASS** — `close ≥ entry × 1.01` **and** day-3 volume ≥ 75% of the trailing 20-day average
- **FAIL** — otherwise

The verdict never sells anything by itself. It sets the Rank & Replace threshold, and (if the
Intraday Minimiser is re-enabled) gates its day-7 fallback.

---

## Power Hold — O'Neil's 8-week rule

**Arms** when a position gains ≥ `POWER_HOLD_GAIN_PCT` (20%) within
`POWER_HOLD_TRIGGER_DAYS` (21 calendar days). **Persists** for
`POWER_HOLD_DURATION_DAYS` (56 calendar days). Note these are *calendar* days, unlike the
trading-day gates elsewhere.

While active:

| | Effect |
|---|---|
| Trailing stop | **widened** to 30%, profit ladder bypassed |
| EMA-21 exit | suppressed |
| Plateau exit | suppressed |
| Rank & Replace | suppressed |
| Thesis Stop | suppressed *(inapplicable — window has passed)* |
| Base trailing stop | **remains active** as the disaster backstop |

Widening a stop on a winner is counter-intuitive. The justification is the return
distribution: historically the top-10 trades account for the majority of total P/L, and at
4 slots on the growth universe, removing them turns the strategy unprofitable outright. A
ladder tightening to 6.5% at +20% mathematically guarantees the biggest winners are clipped
near +20%. Power Hold exists so a genuine market leader can complete its move.

Backtested effect of the 30% power-hold trail was large, monotonic in trail width, and
consistent across both universes. See `decisions/2026-08-04_power-hold-trail-and-five-slots.md`.

---

## Armed Exit — the "smart sale" mechanism

Tiers 1 and 2 never market-sell. They call `arm_exit()`, which:

1. Places an IBKR trailing stop at `ARMED_EXIT_TRAIL_PCT` (0.6%)
2. Records `exit_armed`, `exit_armed_at`, `exit_armed_reason`, `exit_armed_price`
3. Starts a `ARMED_EXIT_DEADLINE_HOURS` (3.25 h) countdown, checked every cycle
4. Forces a market sell if the deadline expires unfilled

**Rationale.** The instant a loss threshold is breached is frequently a local trough;
market-selling there prints the low tick. A 0.6% trail follows any bounce upward while
conceding almost nothing if the decline resumes, and the deadline guarantees the position is
closed within half a session rather than drifting for days.

Backtests confirmed the armed exit beat an immediate market sell on both universes — on the
broad universe an immediate market sell was **worse than no rule at all** (−7.6 ΔCAGR).

Requires `exit_armed*` columns. On PGRST204 the IBKR stop is still placed, but deadline
tracking is lost.

---

## Market direction filter

`is_market_bullish()` compares SPY to its 200-day SMA at market open.

**It gates buying only. It never forces an exit.** A bear signal stands the system down from
new positions; existing holdings continue to be managed by the rules above. It fails closed —
if price data is unavailable, the market is treated as bearish.

---

## Parameter reference

| Parameter | Default | Rule |
|---|---|---|
| `STOP_LOSS_PCT` | `0.10` | Base trailing stop |
| `ATR_STOP_MAX_PCT` | `0.12` | Cap on ATR-derived stop |
| `EARLY_LOSS_STOP_PCT` | `0.02` | Kill-switch, days 0–1 |
| `THESIS_STOP_ENABLED` | `true` | Thesis Stop master switch |
| `THESIS_STOP_ATR_MULT` | `1.0` | ATR multiple for the threshold |
| `THESIS_STOP_START_DAY` / `LAST_DAY` | `2` / `5` | Active window |
| `THESIS_STOP_ATR_FALLBACK` | `3.0` | Used when `entry_atr_pct` is absent |
| `ARMED_EXIT_TRAIL_PCT` | `0.006` | Armed trail distance |
| `ARMED_EXIT_DEADLINE_HOURS` | `3.25` | Forced-sale deadline |
| `EXIT_MA_WINDOW` / `BUFFER_PCT` | `21` / `0.01` | EMA breach exit |
| `EXIT_MA_EOD_ONLY` | `true` | Restrict to 15:45–16:00 |
| `STALE_EXIT_DAYS` | `10` | Plateau threshold |
| `STALE_EXIT_MIN_DAYS_HELD` | `7` | Earliest plateau exit |
| `RANK_REPLACE_THRESHOLD` | `15` | Rotation margin, verdict PASS |
| `RANK_REPLACE_FAIL_THRESHOLD` | `5` | Rotation margin, verdict FAIL |
| `POWER_HOLD_GAIN_PCT` | `20.0` | Arming gain |
| `POWER_HOLD_TRIGGER_DAYS` | `21` | Arming window (calendar) |
| `POWER_HOLD_DURATION_DAYS` | `56` | Protection length (calendar) |
| `POWER_HOLD_TRAIL_PCT` | `0.30` | Trail while power-held |
| `INTRADAY_MINIMISER_ENABLED` | `false` | Superseded by the Thesis Stop |
| `BREAKOUT_VERDICT_MIN_GAIN` | `0.01` | Day-3 PASS gain |
| `BREAKOUT_VERDICT_MIN_VOL_PCT` | `0.75` | Day-3 PASS volume |

---

## Dashboard: the Risk Rule Ladder

Every rule on this page is rendered per position in **Dashboard → Open Positions**.

The **Lifecycle / Tiers** column shows the position's current phase (`D1 · Kill-switch`,
`D3 · Thesis window`, `D9 · Rotation window`, `Power Hold`, `Exiting`) plus a badge for any
rule needing attention, or `✓ all rules nominal`. Hovering gives all nine rules in one
tooltip; expanding the row opens the full **Risk Rule Ladder** with each rule's state,
trigger price and distance to it.

| State | Colour | Meaning |
|---|---|---|
| `ARMED` | red | A sell order is live at the broker right now |
| `TRIGGERED` | red | The condition is met — the agent acts on the next cycle |
| `DEGRADED` | orange | The rule exists and its window is open, but it **cannot** protect the position |
| `WATCH` | amber | Within striking distance of its trigger |
| `ACTIVE` | green | Live and protecting, comfortably clear |
| `PENDING` | grey | Its window has not opened yet |
| `SUPPRESSED` | blue | Deliberately switched off (Power Hold, or the book is not full) |
| `EXPIRED` | dim | Its window has closed |

`DEGRADED` is the state that matters. It is what the Thesis Stop reports when
`closed_above_entry` is missing and an intraday poke above entry has exempted the
position — the NBIX failure mode, which previously left no trace in the UI at all.

Two behaviours worth knowing:

- **Day counts are recomputed in the browser**, not read from `days_held`. That column is
  a snapshot from the last agent cycle and is routinely a day behind, which would put a
  position in the wrong window.
- **Rank & Replace shows `SUPPRESSED` whenever fewer than `MAX_POSITIONS` slots are
  filled**, because the agent gates the whole rule on a full book — with a free slot it
  buys the trigger rather than rotating.

> **Mirror warning.** `frontend/src/lib/positionRules.js` holds a **copy** of the
> thresholds in `execution_agent.py`. Changing a threshold in the agent without changing
> it there makes the dashboard lie. Every constant carries a comment naming its source.
> Rationale and the rejected alternatives are in
> `decisions/2026-08-14_position-lifecycle-visibility.md`.

---

## Manual tools

| Script | Purpose |
|---|---|
| `force_sell.py` | Immediate liquidation of a named position |
| `managed_exit.py` | Run an armed exit manually |
| `rotate_positions.py` | Interactive holdings-vs-triggers review; **not** part of the daemon |
