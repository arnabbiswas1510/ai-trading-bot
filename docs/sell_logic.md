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
| 0 | **Smart OCA Managed Exit** | all | every cycle | **suspends rules 1–13 for that ticker** |
| 1 | Armed-exit deadline | 0–6 | every cycle | force market sell |
| 2 | Early Loss Kill-switch | 0–1 | every cycle | arm exit |
| 3 | **Early Dollar Stop** | 0–5 | every cycle | arm exit |
| 4 | HWM / peak metric update | all | every cycle | *(no exit)* |
| 5 | Trail tightening + power-hold arming | all | every cycle | *(re-places broker order)* |
| 6 | **Thesis Stop** | 2–5 | every cycle | arm exit |
| 7 | Intraday Loss Minimiser | 2+ | every cycle | **disabled by default** |
| 8 | Trailing-stop self-heal | all | every cycle | *(no exit)* |
| 9 | EMA-21 support breach | 7+ | EOD 15:45–16:00 | market sell |
| 10 | Plateau exit | 7+ | EOD 15:45–16:00 | market sell |
| 11 | Day-3 breakout verdict | 3 | EOD, once | *(records verdict)* |
| 12 | Follow-through latch update | all | EOD | *(sets `closed_above_entry`)* |
| 13 | Rank & Replace | 7+ | EOD, once daily | market sell + refill |

`process_exit_requests()` runs before `monitor_portfolio_intraday()` in the main
loop, because it decides which tickers the ladder must skip. See
[Smart OCA Managed Exit](#smart-oca-managed-exit) below.

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

## 2b. Early Dollar Stop — days 0–5

```
shares × (current_price − buy_price) ≤ −EARLY_DOLLAR_STOP_AMOUNT    # default $500
```

A flat dollar cap on unrealized loss during the first `EARLY_DOLLAR_STOP_MAX_DAY` trading
days (default 5). Percentage-based stops are blind to position size; this provides a uniform
floor regardless of ATR or share price.

Checked on the 15-minute monitoring cycle. Arms the exit (0.6% tight trail via `arm_exit()`)
rather than immediately market-selling, so a bounce can still be captured.

**Simulation result (19 trades, 2026-07-09 → 2026-08-18):**

| Threshold | Total saved | Trades helped |
|---|---|---|
| $400 | $3,698 | 6 trades (5 winners at risk) |
| **$500** | **$2,936** | **5 trades (2 winners at risk)** |
| $600 | $2,436 | 5 trades (0 winners at risk) |
| $750 | $1,686 | 5 trades (0 winners at risk) |

$500 is the default — best savings/risk ratio in the historical sample.
Set `EARLY_DOLLAR_STOP_AMOUNT=0` to disable. See `decisions/2026-08-18_early-dollar-stop.md`.

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
| Screener-passing (n≈78) | +10.3 | [−1.2, +23.4] | 92% |
| Broad (n=256) | −9.4 | [−25.1, +7.0] | 15% |

**Neither universe reaches significance: both confidence intervals cross zero.**

Earlier revisions of this page reported +18.8 [+7.1, +33.0] at P=100%. That figure came
from a backtest that anchored the armed exit's trailing stop to the trigger bar's *high* —
a price that printed before the stop was placed. See
`decisions/2026-08-17_armed-exit-backtest-lookahead.md` for the correction, and
`decisions/2026-08-09_thesis-stop.md` for the original reasoning.

#### What the rule actually does — and why it is not tuned for CAGR

A follow-up re-examination (`research/thesis_reexam_bt.py`, 5 ATR multipliers × 2 start
days, scored in four slices: both universes × both intra-bar path assumptions) found
**no configuration positive in all four slices**. `0.75×` from day 3 is *significantly
harmful* on the broad universe (−11.0 [−21.7, −1.4]) and *significantly helpful* on the
screener-passing universe (+10.7 [+1.1, +22.8]) — significant in opposite directions.

`research/thesis_counterfactual_bt.py` explains why. Removing the 4-slot constraint makes
every signal a trade in both arms, which isolates the rule's effect on the positions it
actually cuts from the knock-on effect of freeing a slot early:

| At the shipped 1.0× from day 2 | Broad | Screener-passing |
|---|---|---|
| Positions cut | 387 of 2315 (16.7%) | 89 of 598 (14.9%) |
| Cut at | −3.54% mean | −4.06% mean |
| Would have ended at, if held | −3.43% mean | −4.56% mean |
| **Direct effect** | **−0.11% mean / +0.62% median** | **+0.51% mean / +0.81% median** |
| Cuts that would have reached ≥ +20% | 3 of 387 | 0 of 89 |

Per-trade expectancy across *all* signals never moves more than 0.05pp at any multiplier
in the grid. **The Thesis Stop exits positions that were going to lose about the same
amount anyway, and it does not cut winners.** The large portfolio-level ΔCAGR swings in
either direction come from which slot happened to be free on which day — path-dependent
luck, not edge. Selecting a multiplier by that number would be fitting noise, so the
multiplier is deliberately left at `1.0` and is not tuned.

The Thesis Stop is therefore justified as **loss-shaping and capital recycling on an
invalidated entry** — a smaller average loss (−4.43% → −4.14% broad, −5.60% → −4.96%
pass), a shorter left tail on the traded universe (worst-period CAGR −0.5 → +19.9), and
capital returned from a breakout that never broke out — **not** as a CAGR improvement.
See `decisions/2026-08-17_thesis-stop-reexamination.md` for why.

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

**Intent.** The instant a loss threshold is breached is frequently a local trough;
market-selling there prints the low tick. The trail is meant to follow a bounce upward and
concede little if the decline resumes, with the deadline bounding the wait.

> ⚠️ **This mechanism does not currently achieve that intent, and the 0.6% default is
> known to be too tight.** Two independent problems:
>
> **The trail is inside the noise band.** Traded names average 4.19% ATR/day with a 3.20%
> median daily range; even the quietest decile ranges 1.62%. A 0.6% trail is under a fifth
> of the calmest normal session, so it typically fills on the first chop — roughly 0.6%
> *below* the trigger price, i.e. worse than the market sell it replaced. The same
> observation is recorded independently in `decisions/2026-08-04_managed-exit-tool.md`.
>
> **A trailing stop cannot sell at a high.** It fills at `peak × (1 − trail)` by
> construction — always below the peak, never at it. No value of `ARMED_EXIT_TRAIL_PCT`
> makes it capture a bounce; tightening it only produces an earlier, lower fill. Capturing
> a bounce requires a resting limit *above* the market.
>
> The claim that backtests confirmed the armed exit beats an immediate market sell **in
> both universes was an artifact** of a look-ahead bug. Corrected, the comparison is split:
> the armed exit wins on the screener-passing universe (+37.4 vs +35.7 CAGR) and straddles
> or loses on the broad universe (+14.2 conservative / +17.6 optimistic vs +15.9), where
> the outcome depends on unknowable intra-bar ordering. Per this project's two-universe
> rule that is **unproven**. See
> `decisions/2026-08-17_armed-exit-backtest-lookahead.md`.
>
> The mechanism is retained pending a replacement, because the alternatives were measured
> to be within roughly ±0.15% of one another per exit — the current design is
> mildly wrong, not catastrophic. `EARLY_LOSS_STOP_PCT` (2%) is deliberately tight
> *because* it arms rather than sells; the two must be retuned together if arming is
> removed.

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

| Script | Purpose | Agent must be stopped? | Speed |
|---|---|---|---|
| `request_exit.py --now` | **Force sell via the queue** | **No** | next cycle (≤15 min) |
| `request_exit.py` | **Smart OCA managed exit** | **No** | next cycle (≤15 min) |
| `force_sell.py` | Immediate liquidation | **Yes** | immediate |
| `managed_exit.py` | Run an armed exit manually | **Yes** | immediate |
| `rotate_positions.py` | Interactive holdings-vs-triggers review; **not** part of the daemon | — | — |

`force_sell.py` and `managed_exit.py` connect to IBKR directly as `clientId=1`,
so the execution-agent must be stopped before running either — which leaves
every other position unmonitored. Reserve them for the case where a delay of up
to 15 minutes is itself the risk. For everything else use `request_exit.py`.

### Choosing between the two queue modes

| | `--now` | OCA (default) |
|---|---|---|
| Intent | "get me out" | "get me out well" |
| Order | market sell | `LMT` upper + `TRAIL` lower |
| Waits for a bounce? | no | yes, bounded by floor + expiry |
| Suspends the automated ladder? | no — it exits immediately | **yes**, while `PLACED` |
| Use when | thesis is broken, news is bad, you want it gone | the drop looks like an overreaction and you want a better price |

`--now` is the direct replacement for `force_sell.py` in normal use: same
outcome, but the agent keeps running so the rest of the portfolio keeps its
stops. It does not wait for the 09:45 settle window — an urgent exit deferred
to 09:45 would silently become a wait.

---

## Smart OCA Managed Exit

A queue-driven exit that lets you leave a position with a defined optimistic
target and a defined floor, without taking the agent offline.

You write a row to `exit_requests`; the execution-agent drains the queue on its
normal 15-minute cycle and places an IBKR **OCA pair** on the position:

| Leg | Order type | Purpose |
|---|---|---|
| Upper | `LMT` SELL at a recovery target | the optimistic exit |
| Lower | `TRAIL` SELL | ratchets up behind any bounce |

One fills, the other is cancelled (`ocaType=1`).

### Why the lower leg trails instead of sitting still

With a static stop, a position that rallies most of the way to your limit and
then fades still exits at the original stop price — the whole move is handed
back. The trail follows the advance and banks whatever the bounce actually
delivered. That retained upside is the only reason waiting for the upper leg
beats selling now.

### The automated ladder is suspended while a request is `PLACED`

Every rule in the evaluation order above calls `execute_sell()` or `arm_exit()`,
and both cancel all open SELL orders for the ticker — which would destroy the
OCA. So the ladder skips OCA-managed tickers entirely.

Because that removes the normal safety net, two backstops are enforced in
software every cycle and are **not optional**:

| Backstop | Default | Behaviour |
|---|---|---|
| Hard floor | `OCA_EXIT_DEFAULT_FLOOR_PCT` = 5% | market-exit if price falls this far below the placement price |
| Expiry | `OCA_EXIT_DEFAULT_EXPIRY_DAYS` = 3 | market-exit after this many trading days if neither leg filled |

Without the floor, a name that gaps down and keeps sliding sits unsold behind an
unreachable limit. Without the expiry, an OCA can sit unfilled indefinitely
while the position bleeds.

`get_oca_managed_tickers()` re-checks `status == 'PLACED'` in Python even though
the query filters server-side: wrongly suspending the ladder is the one failure
mode that leaves a position with no stop at all, so it fails closed.

### Placement timing

Requests are drained every cycle, so an exit queued at 11:00 is placed at 11:00.
Requests queued before the open wait until `OCA_EXIT_SETTLE_MINUTE` (09:45 ET) —
the opening auction has the widest spreads of the session, and a limit computed
off a 09:30 tick is computed off noise.

Before placing, the agent cancels the position's existing GTC trailing stop.
Left in place it would be a third SELL order outside the OCA group, so filling
it would not cancel the OCA legs.

If the resolved limit is at or below the current market it is dropped and the
trail is placed alone — such a limit would fill instantly at a worse price than
a plain sell.

### Request modes

Requests store *intent*, not prices, because a row queued at 22:00 carrying a
literal price is stale by 09:30. The agent resolves the real price at placement.

| `limit_mode` | Resolves to |
|---|---|
| `BREAKEVEN` *(default)* | entry price |
| `ABS` | `limit_value` literally |
| `PCT_FROM_ENTRY` | `entry × (1 + limit_value/100)` |
| `PCT_FROM_PRICE` | `price at placement × (1 + limit_value/100)` |
| `NONE` | no upper leg — trail only |

| `stop_mode` | Resolves to |
|---|---|
| `ATR_AUTO` *(default)* | `OCA_EXIT_ATR_FRACTION × entry_atr_pct`, clamped to `[OCA_EXIT_MIN_TRAIL_PCT, OCA_EXIT_MAX_TRAIL_PCT]` |
| `TRAIL_PCT` | `stop_value` percent |
| `MARKET` | not an OCA — sell at market on the next cycle (`--now`). `limit_mode` is ignored |

`ATR_AUTO` exists because a trail tighter than the stock's own noise fires on
the first random wiggle, cancels the upper leg, and reproduces "sell now" with
extra steps. A 1% trail on a name with a 7% average true range is not a tight
stop, it is an immediate one.

### Usage

```bash
# Force sell — market exit, agent stays running
python request_exit.py DELL --now

# Recover-to-a-level exit
python request_exit.py DELL --limit-abs 489.89 --trail 2.5

# Wait for breakeven, trail scaled to the stock's own ATR
python request_exit.py DELL --breakeven --trail auto

# Target +3% above entry, 4% floor, 5 trading days
python request_exit.py NVDA --limit-pct-entry 3 --trail 2 --floor 4 --expires 5

python request_exit.py --list           # in-flight requests
python request_exit.py --cancel DELL    # withdraw
```

Requires `migrations/add_exit_requests.sql`. At most one active request per
ticker is permitted — two OCA groups on the same shares would be rejected by a
cash account, and the second would cancel the first's protection.

Set `OCA_EXIT_ENABLED=false` to disable the feature; the queue is then ignored
and the automated ladder governs every position.

See `decisions/2026-08-18_smart-oca-managed-exit.md` for why.
