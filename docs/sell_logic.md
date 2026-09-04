# Sell Logic

Every exit rule in the live agent, in evaluation order.

**Source:** `execution_agent.py` — `monitor_portfolio_intraday()` (15-minute cycle),
`execute_sell()` (market liquidation), `arm_exit()` (armed trailing exit),
`enqueue_smart_exit()` (hands the exit to the Smart OCA queue).

## Price source: IBKR first, FMP fallback

Every exit rule below prices the position from IBKR's own mark — the same
`PortfolioItem.marketPrice` the dashboard shows and that live orders fill
against — via `get_position_price(ib, ticker, ib_map)`. The map is built once
per cycle by `build_ibkr_price_map(ib)` from the **non-blocking** `ib.portfolio()`
account-update stream, so an entire monitoring pass is decided on one consistent
broker snapshot. The per-position log line shows the source, e.g.
`Current: $150.25 (ibkr)`.

FMP (`get_live_price()`) is only a **fallback**, used when IBKR has no usable
mark for a ticker (data farm down, or the position not yet in the account
stream). This deliberately never uses `ib.reqTickers()`, which blocks
indefinitely when the ushmds data farm is down — the reason FMP was originally
the primary source. Screening and research still price non-held candidates from
FMP, where broker parity is irrelevant. See
`decisions/2026-09-04_ibkr-first-live-pricing.md` for why.

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

This matters: the Prove-It Stop's tight day-0 band applies only while this counter reads 0,
and Rank & Replace opens at the *start* of the second Tuesday, not a calendar week later.

---

## Evaluation order

Checks run per position, per cycle. **The first rule to fire pre-empts everything below it**
for that cycle — an early `continue` means later rules are unreachable until the next poll.

| # | Check | Days | Window | Action |
|---|---|---|---|---|
| 0 | **Smart OCA Managed Exit** | all | every cycle | **suspends rules 1–9 for that ticker** |
| 1 | Armed-exit deadline | all | every cycle | force market sell |
| 2 | HWM / peak metric update | all | every cycle | *(no exit)* |
| 3 | Power-hold arming | all | every cycle | *(suppresses 5 and 8)* |
| 4 | Trail tightening | all | every cycle | *(re-places broker order)* |
| 5 | **The Prove-It Stop** | all | every cycle | arm exit |
| 6 | Trailing-stop self-heal | all | every cycle | *(no exit)* |
| 7 | Day-3 breakout verdict | 3 | EOD, once | *(records verdict)* |
| 8 | Follow-through latch update | all | EOD | *(sets `closed_above_entry`)* |
| 9 | Rank & Replace | 7+ | EOD, once daily | market sell + refill |

Note that the Prove-It Stop has **no day window**. That is the point of it: the
rules it replaced each stopped looking after a few days, and a position that
never worked was left to the peak-anchored trailing stop, which is far below
entry for a stock that never rose. See
`decisions/2026-09-04_prove-it-stop.md` for why.

`process_exit_requests()` runs before `monitor_portfolio_intraday()` in the main
loop, because it decides which tickers the ladder must skip. It then runs a
**second time** afterwards, so a manually queued exit has its OCA placed on the
same cycle instead of idling `PENDING` for another 15 minutes. See
[Smart OCA Managed Exit](#smart-oca-managed-exit) below.

### Which rules sell smart, and which sell at market

Three exit mechanisms exist because urgency differs. Using the smart exit
everywhere would be actively harmful — see
`decisions/2026-08-19_smart-exit-for-discretionary-rules.md`.

| Mechanism | Used by | Why |
|---|---|---|
| **Smart OCA queue** (limit + trail, 3-day expiry) | All manual exit requests | Not urgent. A considered exit deserves a limit target, not whichever tick the cycle noticed |
| **Armed exit** (0.6% trail, ~3.25h deadline) | The Prove-It Stop, both phases | Urgent. Rides a bounce but still exits the same session |
| **Market order** | Rank & Replace, armed-exit deadline, OCA floor/expiry backstop | Must complete now — see below |

No automated rule uses the smart queue any more. The three discretionary Day 7+
rules that did — the EMA-21 breach, the plateau exit and the intraday minimiser
— are all retired (`docs/retired_code.md`). The queue remains fully supported
for manual requests via `request_exit.py`.

The market-order cases are deliberate, not oversights:

- **Rank & Replace** is a *swap*. The sell exists only to fund the replacement
  buy immediately afterwards. An OCA that fills in three days ties up the cash,
  keeps the slot occupied, and forfeits the trigger it was rotating into.
- **The Prove-It Stop** must not use the queue: a `PLACED` OCA suspends the
  whole ladder for up to `OCA_EXIT_DEFAULT_EXPIRY_DAYS`, and placement is
  deferred to `OCA_EXIT_SETTLE_MINUTE`, so a stop firing at 14:00 would wait for
  the next morning.
- **Backstops** (armed-exit deadline, OCA floor/expiry) are what catch a smart
  exit that fails to fill. A backstop that can itself fail to fill is not one.

If enqueueing fails — typically the migration has not been applied — the rule
falls back to `execute_sell()`. A triggered sell rule never executes nothing.

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
| < +5% | initial (10–12%) |
| ≥ +5% | 1.5% |

This is an explicit **first-leg profit lock**. Once a trade proves it can reach a modest gain,
the bot stops giving it 10-12% of room from the peak and instead treats any 1.5% give-back as
evidence that the breakout has stalled.

See `decisions/2026-08-22_hwm-profit-lock-arm-5pct.md` for why.

The ratchet has a second input: the **Prove-It Stop** feeds it a percentage that pins the
resting broker order onto the current Prove-It level. Because the ratchet only ever tightens,
that turns a percentage trail into a fixed price floor — see
[The Prove-It Stop](#2-the-prove-it-stop--always-live).

**Under Power Hold the ladder is bypassed** and the trail widens to
`POWER_HOLD_TRAIL_PCT` (30%). See [Power Hold](#power-hold-oneils-8-week-rule).

The agent re-places the order when a tier is crossed, and self-heals a missing stop every
cycle.

---

## 2. The Prove-It Stop — always live

One rule replaces five. It asks a single question:

> **Has this position ever CLOSED above the price we paid?**

`closed_above_entry` is latched `True` at EOD the first time a close prints above
entry, and is never cleared — a breakout confirms only once. That latch selects
the phase.

### Phase 1 — unproven. Anchored to ENTRY.

The breakout has not confirmed, so the only meaningful reference is what we paid.

| Day held | Arms an exit at |
|---|---|
| 0 | **1.0%** below entry (`PROVE_IT_P1_DAY0_PCT`) |
| 1 and after | **3.0%** below entry (`PROVE_IT_P1_LATER_PCT`) |

The band **widens** after day 0 rather than tightening, and that is deliberate.
Day 0 is the only day on which the failing and working populations separate
cleanly — a breakout that fails immediately is wrong immediately. From day 1 they
overlap, and holding the tight band costs roughly $1,500–2,000 in clipped
winners across the 30-trade sample. CPAY closed −2.24% on day 1 (low −2.88%) and
then ran to +8.95%.

**Phase 1 never expires.** Every rule it replaced was scoped to a window, which
is exactly how NBIX (−$2,261), DELL (−$1,283), RSI (−$1,390) and HWM (−$1,463)
were allowed to run: the kill-switch stopped looking after day 0 and the
peak-anchored trailing stop sits far below entry for a stock that never rose.

### Phase 2 — proven. Anchored to the PEAK.

It closed above entry, so it earned patience.

| Peak gain | Protection |
|---|---|
| below +2.0% | none of its own — the base trailing stop governs |
| at or above **+2.0%** (`PROVE_IT_P2_ARM_GAIN_PCT`) | give-back floor arms at **1.0% below entry** (`PROVE_IT_P2_FLOOR_PCT`) |
| at or above **+5.0%** | 1.5% trail from the high water mark (`TRAIL_PROFIT_TIERS`, tighter than the floor, takes over) |

**A trade that went green is never allowed to become a real loss.**

The floor sits 1% *below* entry, not at it. An exact-breakeven floor flushes any
position that pokes green and immediately retests entry — CPAY did precisely that
on day 4 (high +3.60%, low −0.41%), and an at-entry floor would have sold it for
$0 and forfeited +$1,189. That 1% of slack turns CPAY into +$1,907 while still
catching FRO and CDNA.

Below the arming gain there is no floor, because a floor inside ±2% sits inside
ordinary noise.

### How it acts

Both phases call **`arm_exit()`** — a tight 0.6% trailing exit with a 3.25h
deadline — rather than market-selling into what is usually a local trough. Across
the sample the armed exit beats an immediate market sell by roughly $600.

A resting IBKR GTC order backs this up so an overnight gap is still capped when
the agent is offline:

- **Phase 1:** the resting order sits `PROVE_IT_BACKSTOP_SLACK_PCT` (1%) **wider**
  than the trigger, so it can never front-run the bot-side exit. It is a gap
  backstop only.
- **Phase 2:** the resting order **is** the floor.

`prove_it_trail_pct()` solves `1 − (level / current_price)` and feeds it into the
existing one-way `min()` ratchet in `_compute_dynamic_trail_pct()`. Because the
ratchet only ever tightens, a rising price yields a looser required percentage
(rejected, so the stop stays put) and a falling price yields a tighter one
(accepted, so the stop is pinned on the floor). That is how a percentage trail
becomes a fixed price floor with no new order type. The percentage is solved
against the **current** price because IBKR resets the `trailingPercent` anchor on
every cancel-and-replace — which is exactly what the tightening block does.

### Why Phase 1 is enforced by the agent, not by the broker

Phase 1 is deliberately **bot-enforced**: the agent's 15-minute cycle compares
price to the band, and a breach *arms* a 0.6% trail rather than selling. The
resting IBKR order is pushed 1% further away precisely so it cannot pre-empt
that.

This is a measured choice, not an oversight. The obvious alternative — park the
resting order **on** the day-0 level so it is enforced continuously — was
replayed across all 30 closed trades and is worse on every axis:

| Day-0 enforcement | Net vs realised | Worst single loss | Losers > $300 |
|---|---|---|---|
| **Bot poll + arm (shipped)** | **+$11,665** | −$1,269 | 9 |
| Resting broker stop @ 1.0% | +$11,262 | −$1,269 | 9 |
| Resting broker stop @ 1.5% | +$10,354 | −$1,269 | 13 |
| Resting broker stop @ 2.5% | +$9,513 | −$1,269 | 13 |

It costs $403 and does not improve the worst loss at all. Widening the hard band
degrades it monotonically, because a resting stop fires on wicks that a
15-minute close ignores — and in this sample those wicks were noise. The armed
trail more than pays for the sampling gap: seven of the thirteen Phase 1 exits
came out *better* under arming (HWM −$579 → −$447, LPG −$231 → −$92,
RSI −$252 → −$202).

Reproduce with `python3 research/exit_rule_replay.py --day0`.

**Consequence to understand:** the day-0 1% is a *trigger*, not a loss cap.
Between the 15-minute sampling gap, the arm-then-trail mechanism and the
backstop sitting at ≈2% below entry, a realistic worst case on day 0 is
**2–2.5%**, and it is unbounded on a genuine fast move. That is the price of the
bounce capture.

### Fails safe

A missing `closed_above_entry` column reads as `None`, and `None` counts as
**proven**, never as unproven. Applying the tight entry-anchored band to a
working position is the more expensive error. Without the column the rule falls
back to an intraday-poke test, which treats almost everything as proven and
effectively disables Phase 1 — `schema_guard.py` warns loudly about this.

Suppressed entirely by Power Hold.

### What it replaced, and why

| Retired rule | Window | Times it fired in 30 closed trades |
|---|---|---|
| Early Loss Kill-switch | day 0 only | 1 |
| Intraday Loss Minimiser | day 2+ | 3, then disabled |
| Early Dollar Stop | days 0–5 | **0** |
| Thesis Stop | days 2–5 | **0** |
| EMA-21 support breach | day 7+ | **0** |
| Plateau exit | day 7+ | **0** |

The Early Dollar Stop and the Thesis Stop never fired once: whenever a position
was bad enough to trigger them, the kill-switch had already caught it on day 0.
They were not redundant safety, they were unreachable code that still had to be
reasoned about on every read of the monitor loop.

Full detail, including the restore conditions for each, is in
`docs/retired_code.md`.

### Evidence

A 5-minute-bar replay of all 30 closed trades, reproducing the live mechanics
(15-minute checks, `arm_exit()` 0.6% trail, 3.25h deadline):

| | Net |
|---|---|
| What actually happened | **−$6,548** |
| The rules shipped before this change | −$4,069 |
| **Prove-It** | **+$5,410** |

Zero winners cut short. Worst single loss falls from −$2,002 to −$1,140, and the
−$1,140 is APH — an overnight gap that no stop of any kind prevents. Every
intraday bleed is cut small: NBIX −$2,261 → −$230, CDNA −$1,539 → +$256,
RSI −$1,390 → −$197.

Phase 1 and Phase 2 are complementary, not additive. Phase 1 alone *harms*
winners (−$2,271); Phase 2 alone leaves the worst loss at −$2,261. Only the
combination both raises net and cuts the worst loss.

**There is no guaranteed loss cap.** No tested configuration achieves one, and
INCY gets worse under this rule (−$137 → −$429). It is a net improvement across
the distribution, not on every trade. n = 30 is a small sample and every
parameter here is provisional — see the scheduled review in `AGENTS.md`.

Reproduce with:

```bash
python3 research/exit_rule_replay.py --insecure --proveit
```

See `decisions/2026-09-04_prove-it-stop.md`.

---

## 3. Staleness — day 7+ (feeds Rank & Replace)

```
trading_days_between(hwm_date, today) ≥ STALE_EXIT_DAYS     # default 10
```

`hwm_date` advances every cycle the position prints a new high. If it stops
advancing, the clock runs. A stale position is one that has stopped making
progress even though it may sit comfortably above its trailing stop.

**Staleness does not sell to cash.** It discounts the Rank & Replace margin to
`RANK_REPLACE_FAIL_THRESHOLD`, exactly as a FAIL verdict does. The slot is
released when somewhere better to put the money actually exists — not merely
because this position stopped moving.

This changed with the Prove-It Stop. The capital-velocity argument that
justified the old plateau exit still holds — with a hard cap on slots, dead money
costs the return of the best trigger it is blocking — but with the give-back
floor in place, *holding* dead money is nearly free, while selling it to cash on
a timer is not. The plateau exit also never fired once in 30 closed trades.

Suppressed by Power Hold. Requires `STALE_EXIT_MIN_DAYS_HELD` (7) days held
before it can apply.

---

## 4. Rank & Replace — day 7+

Runs once daily at EOD, and only when the book is full and fresh triggers exist.

Each holding gets a **momentum health score** Mₜ:

```
Mₜ = 0.40 × live RS + 0.35 × volume ratio + 0.25 × sentiment
```

Rotation occurs when the best available trigger's score exceeds Mₜ by a margin that depends
on the day-3 verdict and on whether the position has gone stale:

| Condition | Required margin |
|---|---|
| `PASS` verdict, not stale | 15 points |
| `FAIL` verdict | 5 points |
| **Stale** (no new high in `STALE_EXIT_DAYS`) | 5 points |

A breakout that already failed to confirm has forfeited the benefit of the doubt, and so has
one that has stopped making progress. The staleness discount is what absorbed the retired
plateau exit — see [Staleness](#3-staleness--day-7-feeds-rank--replace). Suppressed by Power
Hold.

---

## Day-3 breakout verdict

Evaluated once, at day-3 EOD:

- **PASS** — `close ≥ entry × 1.01` **and** day-3 volume ≥ 75% of the trailing 20-day average
- **FAIL** — otherwise

The verdict never sells anything by itself. It sets the Rank & Replace threshold, and (if the
Intraday Minimiser is re-enabled) gates its day-7 fallback.

---

## Power Hold — O'Neil's 8-week rule

**Arms** when a position gains ≥ `POWER_HOLD_GAIN_PCT` (10%) within
`POWER_HOLD_TRIGGER_DAYS` (21 calendar days). **Persists** for
`POWER_HOLD_DURATION_DAYS` (56 calendar days). Note these are *calendar* days, unlike the
trading-day gates elsewhere.

While active:

| | Effect |
|---|---|
| Trailing stop | **widened** to 30%, profit ladder bypassed |
| The Prove-It Stop | suppressed, both phases |
| Rank & Replace | suppressed |
| Staleness discount | suppressed |
| Base trailing stop | **remains active** as the disaster backstop |

Widening a stop on a winner is counter-intuitive. The justification is the return
distribution: historically the top-10 trades account for the majority of total P/L, and at
4 slots on the growth universe, removing them turns the strategy unprofitable outright. A
profit lock clamping the trail to 1.5% at +5% would otherwise guarantee the biggest winners
are clipped near +5%. Power Hold exists so a genuine market leader can complete its move.

Backtested effect of the 30% power-hold trail was large, monotonic in trail width, and
consistent across both universes. See `decisions/2026-08-04_power-hold-trail-and-five-slots.md`.

The trigger was lowered from +20% to +10% alongside the Prove-It Stop. At +20% the rule was
unreachable: the realised trade distribution contains no +20% runners, so it never armed. The
+10% figure is **unvalidated** — no trade in the 30-trade replay reached +10% within 21 days,
so the harness is silent on it. It is entered into the scheduled review in `AGENTS.md`.

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
> mildly wrong, not catastrophic. `PROVE_IT_P1_DAY0_PCT` (1%) is deliberately tight
> *because* it arms rather than sells; the two must be retuned together if arming is
> removed. The 30-trade replay measured the armed exit as worth roughly +$600 over an
> immediate market sell across the sample.

Requires `exit_armed*` columns. On PGRST204 the IBKR stop is still placed, but deadline
tracking is lost.

---

## Market direction filter

`is_market_bullish()` evaluates the CANSLIM "M" gate at market open against
`MARKET_DIRECTION_TICKERS` (default `SPY,QQQ`).

The market is **bullish** only when:

- **every** benchmark closes more than `MARKET_DIRECTION_BUFFER_PCT` (1%) above its
  SMA-200, **and**
- **at least one** of those SMA-200s is non-falling over `MARKET_DIRECTION_SLOPE_DAYS`
  (20) sessions.

**It gates buying only. It never forces an exit.** A bear signal stands the system down from
new positions; existing holdings continue to be managed by the rules above.

**It fails closed.** An HTTP error, a malformed payload, fewer than
`SMA_WINDOW + SLOPE_DAYS` sessions of history, data older than
`MARKET_DIRECTION_MAX_STALE_DAYS`, an unhandled exception, or an empty benchmark list
all yield *bearish*. `MARKET_DIRECTION_FILTER_ENABLED=false` is the only bypass.

The gate is drawdown insurance, not a return enhancer: over 2007–2026 it sits out
67.8% of the worst-5% forward-20-day windows (vs 59.3% for the previous bare
SPY > SMA-200 rule) while permitting 66.9% of sessions. Its mean-return edge is
negative outside 2008. A `50-DMA > 200-DMA` requirement and an "either index"
combination were both grid-tested and rejected.
See `decisions/2026-08-22_market-direction-gate-spy-qqq.md` for why.

---

## Parameter reference

| Parameter | Default | Rule |
|---|---|---|
| `STOP_LOSS_PCT` | `0.10` | Base trailing stop |
| `ATR_STOP_MAX_PCT` | `0.12` | Cap on ATR-derived stop |
| `PROVE_IT_ENABLED` | `true` | Prove-It Stop master switch |
| `PROVE_IT_P1_DAY0_PCT` | `0.01` | Phase 1 band below entry, entry day |
| `PROVE_IT_P1_LATER_PCT` | `0.03` | Phase 1 band below entry, day 1 onward |
| `PROVE_IT_P1_DAY0_LAST_DAY` | `0` | Last day the tighter Phase 1 band applies |
| `PROVE_IT_P2_ARM_GAIN_PCT` | `0.02` | Peak gain that arms the give-back floor |
| `PROVE_IT_P2_FLOOR_PCT` | `-0.01` | Floor relative to entry (negative = below) |
| `PROVE_IT_BACKSTOP_SLACK_PCT` | `0.01` | How far wider the Phase 1 resting IBKR order sits |
| `ARMED_EXIT_TRAIL_PCT` | `0.006` | Armed trail distance |
| `ARMED_EXIT_DEADLINE_HOURS` | `3.25` | Forced-sale deadline |
| `STALE_EXIT_DAYS` | `10` | Staleness threshold (discounts the rotation bar) |
| `STALE_EXIT_MIN_DAYS_HELD` | `7` | Earliest a position may count as stale |
| `RANK_REPLACE_THRESHOLD` | `15` | Rotation margin, verdict PASS |
| `RANK_REPLACE_FAIL_THRESHOLD` | `5` | Rotation margin, verdict FAIL |
| `POWER_HOLD_GAIN_PCT` | `10.0` | Arming gain |
| `POWER_HOLD_TRIGGER_DAYS` | `21` | Arming window (calendar) |
| `POWER_HOLD_DURATION_DAYS` | `56` | Protection length (calendar) |
| `POWER_HOLD_TRAIL_PCT` | `0.30` | Trail while power-held |
| `BREAKOUT_VERDICT_MIN_GAIN` | `0.01` | Day-3 PASS gain |
| `BREAKOUT_VERDICT_MIN_VOL_PCT` | `0.75` | Day-3 PASS volume |

---

## Dashboard: the Position Journey and the Risk Rule Ladder

Every rule on this page is rendered per position in **Dashboard → Open Positions**.

### Position Journey

Expanding a position opens the **Position Journey** panel, which answers three questions
without requiring any knowledge of this document:

1. **Which phase is it in?** A track runs across the top — `Unproven`, `Proven`,
   `Floor locked`, `Rotation`. It is indexed by **proof, not by calendar day**: a position
   advances only by closing above entry and then by reaching the arming gain, and it can sit
   in `Unproven` indefinitely. Completed segments are dimmed, the current segment is
   highlighted in the phase colour, and upcoming segments are faint. `Power Hold` and
   `Exiting` override the track entirely, which the panel states explicitly rather than
   leaving a segment lit.
2. **What happens next?** The right-hand column lists the nearest price level that would
   act on the position (which rule, at what price, how far away), the next advance along the
   proof track stated as the *price* that would cause it, the day-3 verdict if it is still
   pending, the gain needed to arm the HWM profit lock, and the staleness countdown once it
   is within five stale sessions. When an exit is armed, this collapses to the single fact that
   matters: the trail level and the forced-market-sell time.
3. **What has already happened?** The left-hand column is the position's history — entry
   price and size, whether it has ever closed above entry, the day-3 verdict, the high-water mark and how far below it price now sits,
   whether the profit lock or Power Hold engaged, and whether an exit is armed and why.

The panel is derived from the same evaluated rules as the ladder below it, so the two can
never disagree.

### Compact column and ladder

The **Lifecycle / Tiers** column shows the position's current phase (`D0 · Unproven`,
`D3 · Proven`, `D4 · Floor locked`, `D9 · Rotation window`, `Power Hold`, `Exiting`), a badge for any
rule needing attention (or `✓ all rules nominal`), and a `next ▸` line naming the single
most imminent event. Hovering gives all rules plus the full history and next-step list in
one tooltip; expanding the row opens the **Risk Rule Ladder** with each rule's state,
trigger price and distance to it. The **Trail Stop** column also tells you whether the
position is still on its base/ATR stop or whether the **HWM profit lock** is active, and the
expanded **Position** card repeats that status with the live locked stop level.

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

`DEGRADED` is the state that matters. It is what the Prove-It Stop reports when
`closed_above_entry` is missing and an intraday poke above entry has promoted the position
to Phase 2 — the NBIX failure mode, which previously left no trace in the UI at all.

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

## Dashboard: the exit detail panel

Every closed trade on both the **Dashboard** and **Trade History** screens expands
to a full breakdown of how it was closed. Click the row.

The panel answers four things the Exit Reason badge alone cannot:

- **Who executed the exit.** Three attributions, and the difference is
  operationally significant:
  - **Bot (execution agent)** — the agent evaluated a rule and submitted the order.
  - **IBKR (resting order)** — a GTC order filled at the broker without the agent
    being involved; it found out at the next reconcile.
  - **Manual / human** — closed in TWS or by force-sell, bypassing the rule set.
- **Trade economics** — shares, both fills, move per share, cost basis, proceeds,
  realised P&L and return, all derived from the stored fills.
- **What the firing rule recorded** — the numbers the rule itself wrote at the
  time. A trailing stop shows the trail in force, the high-water mark and the date
  it was set, the implied trigger price, the hold day and the peak excursion. The
  A retired rule such as the Intraday Loss Minimiser still renders its own detail, because
  historical `sell_reason` strings must keep resolving forever.
  Rank & Replace shows both scores and the ticker rotated into.
- **What was never recorded**, listed by name.

### Nothing is inferred from the outcome

Exit labels are derived from the stored reason string only. They are never
inferred from the return percentage. This matters because the previous
implementation did exactly that: any reconciled exit was labelled a +25% profit
target above +24% and a −7% stop below it — so PTGX, which closed at −2.77% on a
broker trailing stop, was displayed as "Stop Loss (−7%)". Neither number is real.
The bot has **no fixed profit target**, and the stop is the dynamic 8.25–10% trail
that tightens to 1.5% after the +5% lock, not a flat 7%.
See `decisions/2026-08-23_exit-detail-panel.md` for why.

`frontend/src/lib/exitDetails.js` is the single classifier for both screens.
`classifyExit()` takes only the reason string, so it is structurally incapable of
seeing the P&L. The build fails if it regains a second parameter or if either view
reintroduces a local copy.

### "Not recorded" means the data is gone, not hidden

The 10 trades closed before 2026-08-23 by a broker trailing stop carry only the
bare label `Trailing stop (IBKR GTC TRAIL order)`. Their trail, high-water mark
and trigger price were on the `portfolio_positions` row that reconcile deleted,
and are unrecoverable. The panel names them as missing rather than leaving a gap
that reads as a zero.

From 2026-08-23 the agent captures that risk state at reconcile time and appends
it to the reason, so newer broker exits are fully explained:

```
Trailing stop (IBKR GTC TRAIL order) — trail 10.00%, HWM $52.02 set 2026-08-21,
implied trigger $46.82, day 2 of hold, peak +4.30%
```

The trigger price is labelled **implied** because it is reconstructed from the
trail and the last peak the agent observed, not read back from the broker. If the
peak moved between the final 15-minute check and the fill, it is approximate.

> **Format contract.** The agent writes this suffix in `_exit_context_suffix()`
> (`execution_agent.py`) and the dashboard parses it in `extractReasonFacts()`.
> The two are pinned from both sides — `tests/test_exit_context.py` and
> `frontend/scripts/test-exit-details.mjs` — so a drift in the agent's format
> fails a test instead of silently degrading the panel back to "not recorded".

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

Every rule in the evaluation order above ends in `execute_sell()` or
`arm_exit()`, and both cancel all open SELL orders for the ticker — which would
destroy the OCA. So the ladder skips OCA-managed tickers entirely.

This applies equally to rule-generated requests: once rule 7/9/10 has enqueued
an exit and it reaches `PLACED`, that ticker's ladder is suspended too, and the
floor and expiry below are its only protection.

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

If the resolved limit is already at or below the current market, the leg is
still placed. A **sell** limit can never fill below its limit price, so a
marketable one fills at the better prevailing bid — a $489.89 limit into a $495
market fills near $495. Dropping it would decline the exact price the request
asked for. It is also safer than a market order: if the bid collapses before
the fill, the order rests at the limit instead of chasing the drop down.

### Request modes

Requests store *intent*, not prices, because a row queued at 22:00 carrying a
literal price is stale by 09:30. The agent resolves the real price at placement.

| `limit_mode` | Resolves to |
|---|---|
| `ATR_AUTO` *(default)* | `price at placement × (1 + clamp(OCA_EXIT_UPPER_ATR_FRACTION × entry_atr_pct, OCA_EXIT_MIN_UPPER_PCT, OCA_EXIT_MAX_UPPER_PCT))` |
| `BREAKEVEN` | entry price |
| `ABS` | `limit_value` literally |
| `PCT_FROM_ENTRY` | `entry × (1 + limit_value/100)` |
| `PCT_FROM_PRICE` | `price at placement × (1 + limit_value/100)` |
| `NONE` | no upper leg — trail only |

`limit_cap` caps the resolved target in every mode.

### Why `ATR_AUTO` is the default

`BREAKEVEN` anchors the target to the **entry** price, so the bounce it needs
grows with the loss already taken — the deeper underwater, the less likely it
fills. On DELL (entry $496.04, price $468.61) breakeven required a **+5.85%**
rally, so that leg could never realistically fill and the OCA could only resolve
via the trail or the expiry.

`ATR_AUTO` anchors to the **current** price instead, so entry drops out of the
maths and the target is always about half a session's move away:

| Position | ATR | Upper leg | Bounce needed | Trail |
|---|---|---|---|---|
| LPG | 4.0% | +2.00% | reachable | 1.50% |
| DELL | 7.6% | +3.80% | reachable | 2.51% |

The upper fraction (0.50) is larger than the trail fraction (0.33), so the
optimistic leg always sits further out than the protective one at any ATR. Both
clamps matter: the floor keeps a quiet stock's target outside the spread, the
ceiling keeps a volatile stock's target reachable.

This means the minimum viable request is just a ticker — everything else is
derived from the position and its ATR:

```sql
INSERT INTO exit_requests (ticker) VALUES ('LPG');
```

```bash
python request_exit.py LPG
```

See `decisions/2026-08-19_atr-anchored-upper-leg.md` for why.

### Next-morning re-anchoring

`PCT_FROM_PRICE` is the mode for "sell tomorrow morning, priced off tomorrow".
The target is computed from the price at placement (~09:45 ET), so an overnight
gap moves the plan with the stock instead of stranding a target the open made
unreachable — or handing back a gap up.

**Always pair it with `limit_cap`.** `PCT_FROM_PRICE` is momentum-following by
construction: the better the gap, the greedier the target, so uncapped it never
takes the gift it was waiting for. Capping at the entry price converts
"sell X% above wherever it opens" into "sell X% above the open, but never hold
out for more than breakeven".

Worked example — a request for +4.54% capped at entry $496.04:

| Opens at | Uncapped target | With cap | Outcome |
|---|---|---|---|
| $455 | $475.66 | $475.66 | target follows the stock down, still sells into a bounce |
| $468 | $489.34 | $489.34 | cap inert |
| $495 | $517.50 ← above the 52w high | **$496.04** | takes breakeven instead of chasing |
| $500 | $522.65 | **$496.04** | already marketable, fills near $500 |

The trailing leg is a percentage, so it re-anchors automatically and needs no
cap.

---

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

# Next-morning sale: price off tomorrow's open, never ask above breakeven
python request_exit.py DELL --limit-pct-price 4.54 --limit-cap 496.04 --trail 2.5

# Recover-to-a-level exit (fixed price, ignores the gap)
python request_exit.py DELL --limit-abs 489.89 --trail 2.5

# Wait for breakeven, trail scaled to the stock's own ATR
python request_exit.py DELL --breakeven --trail auto

python request_exit.py --list           # in-flight requests
python request_exit.py --cancel DELL    # withdraw
```

Requires `migrations/add_exit_requests.sql`. At most one active request per
ticker is permitted — two OCA groups on the same shares would be rejected by a
cash account, and the second would cancel the first's protection.

Set `OCA_EXIT_ENABLED=false` to disable the feature; the queue is then ignored
and the automated ladder governs every position.

See `decisions/2026-08-18_smart-oca-managed-exit.md` for why.
