# Decision: Correct the look-ahead bias in the armed-exit backtest

## Problem

`research/thesis_bt.py` modelled the armed trailing exit (`arm_exit()`) with a
look-ahead bug that made every armed exit appear ~3pp better than any live
order could achieve. Two independent errors, both favourable:

**1. The trail was anchored to a price that printed before it existed.**

```python
p["arm_peak"] = bar["high"]      # at the arming site, line 136
```

The thesis stop fires on the bar's CLOSE, and `arm_exit()` places the IBKR
trailing stop at that moment — so the stop can only trail from the price at
the trigger. Seeding the high-water mark with the trigger bar's HIGH
back-dates it to a price that had already passed.

The gap is not small. On the PASS universe a trigger bar's high sits a median
**+2.93%** above its close (BROAD: +1.94%). Worked example, entry $100:

| | Simulated | Reachable in production |
|---|---|---|
| Trigger bar | O 99.00 H 99.50 L 95.50 C **95.80** | same |
| Trail anchored at | **$99.50** (the high) | **$95.80** (the close) |
| Stop level @ 0.6% | **$98.90** | **$95.23** |
| Bar low 95.50 ≤ stop? | yes → exit booked at **$98.90** | — |
| Recorded outcome | **−1.10%** | **−4.77%** |

Worse, because the ratchet ran on the trigger bar itself, the position could be
booked out at $98.90 on the very bar that armed it — an exit at the day's high,
awarded for a rule that fired at the day's close.

**2. Intra-bar ordering was assumed favourable.**

The resolution ran `arm_peak = max(arm_peak, bar["high"])` *before* testing
`bar["low"] <= lvl`, i.e. it assumed the high always prints before the low, so
the trail ratcheted up before it could be hit. A stop resting below the market
does not get that courtesy. Daily bars cannot reveal the true order, so the
assumption must be stated, not silently chosen.

Gap-throughs were also mispriced: a bar opening below the stop was filled at
the stop level, which was never available.

## Decision

Anchor the trail at the trigger close, make the path assumption explicit, and
price gaps honestly.

- `armed_fill()` is now a named function with an `arm_path` parameter:
  `conservative` (low first — a resting stop is taken out before any new high
  lifts it) and `optimistic` (high first — the old implicit assumption).
- The arming site seeds `arm_peak` with the trigger **close**.
- If the first bar after arming opens through the stop, the fill is the
  **open**, not the stop level.
- `thesis_bt.py` reports CAGR under **both** paths (`CAGRc` / `CAGRo`). A
  result is only actionable if it holds under both — the same two-universe
  discipline this project already applies, extended to path risk.

`latch_bt.py` and `entry_bt.py` import this `simulate()`, so both are corrected
by the same change.

**No production trading logic is changed by this ADR.** This corrects the
measuring instrument and the claims derived from it.

## Consequences — two shipped claims do not survive

### The armed exit is unproven, not proven

`decisions/2026-08-09_thesis-stop.md` states "Armed exit beat an immediate
market sell in both universes". Corrected, it does not:

| Universe | market sell | armed (conservative) | armed (optimistic) |
|---|---|---|---|
| BROAD | **+15.9** | +14.2 | +17.6 |
| PASS | +35.7 | **+37.4** | **+40.4** |

BROAD now straddles the market sell depending on an unknowable intra-bar path;
PASS still favours arming. Split verdict ⇒ unproven by this project's own rule.

### The thesis stop's headline result loses significance

Paired stationary-block bootstrap, 2000 reps, shipped design vs baseline:

| Universe | as published | corrected |
|---|---|---|
| PASS | +18.8 [+7.1, +33.0] P=100% **SIG** | **+10.29 [−1.21, +23.37] P=92%** — CI crosses zero |
| BROAD | −0.7 [−15.9, +15.6] P=47% (neutral) | **−9.43 [−25.14, +6.96] P=15%** |

The follow-through latch comparison also splits: `close` vs `poke` is
**+6.87pp SIG on PASS but −8.43pp SIG on BROAD**, where it was positive in both.

The thesis stop is left **enabled and unchanged**. Its supporting metrics still
improve in both universes (avg loss BROAD −4.43→−4.14, PASS −5.60→−4.96;
payoff BROAD 1.86→1.81 is now flat, PASS 1.85→2.09 still up), and the point
estimate is still positive on the universe actually traded. But it is now a
*plausible* rule rather than a *demonstrated* one, and the docs must say so.

### The 0.6% trail was never defensible on noise grounds

`decisions/2026-08-03_armed-trailing-exit-day0-6.md` justifies 0.6% with
"CAN SLIM breakout names commonly show 0.3–0.6% of normal intraday
noise/spread". Measured on the committed dataset:

| | BROAD | PASS |
|---|---|---|
| Mean ATR%/day | 2.71% | **4.19%** |
| Median daily high−low range | 2.04% | **3.20%** |
| 10th-percentile range | 1.13% | **1.62%** |

0.6% is under a fifth of the *quietest* day's range on the traded universe. The
code's own `THESIS_STOP_ATR_FALLBACK` is 3.0%/day. This was already noticed in
`decisions/2026-08-04_managed-exit-tool.md` ("a 0.6% trail on DXCM reproduces
'sell immediately' with extra steps") but was not connected back to the armed
exit, because the contaminated backtest kept vouching for it.

## Why the instrument, not just the parameter

A trailing stop sells at `peak × (1 − trail)` — **structurally below a high,
never at one**. No value of `ARMED_EXIT_TRAIL_PCT` makes it capture a bounce;
tightening it only guarantees an earlier, lower fill. Capturing a bounce
requires a resting limit **above** the market. That is the subject of a
follow-up decision; this ADR only establishes that the evidence for the current
mechanism was an artifact.

## Files changed

- `research/thesis_bt.py`: added `armed_fill()` with explicit `arm_path`;
  anchored `arm_peak` at the trigger close; gap-through fills at the open;
  `__main__` reports both path bounds.
- `docs/sell_logic.md`: corrected the "Armed Exit" and Thesis Stop evidence
  sections.
- `decisions/2026-08-09_thesis-stop.md`, `2026-08-03_armed-trailing-exit-day0-6.md`:
  superseding notes pointing here.
