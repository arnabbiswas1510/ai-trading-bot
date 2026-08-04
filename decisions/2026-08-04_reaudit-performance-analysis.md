# Re-audit of the 2026-07-29 performance analysis

- **Date:** 2026-08-04
- **Status:** Accepted
- **Supersedes:** `docs/performance-analysis-2026-07-29.md` (annotated in place)

## Context

The user asked whether the 16 issues in `docs/performance-analysis-2026-07-29.md`
were still valid, and to revisit prior assumptions with one goal: maximise
returns as an aggressive swing trading bot.

Each item was checked against current code. Where the recommendation was a
tunable parameter it was **measured** in the 4-slot portfolio backtest rather
than applied on the document's authority — the document itself was written from
code reading alone, and this session has already shown twice that untested exit
reasoning tends to be wrong.

## Audit result

| Status | Count |
|---|---|
| Already fixed | 5 |
| Still live — fixed here | 4 |
| Recommendation rejected on evidence | 2 |
| Obsolete | 1 |
| Open, untested | 2 |

## Fixed here

### Bug 2 — Day 3 verdict compared two stale volume bars (real defect)

`_fetch_ohlcv()` returns bars ascending, but the verdict read `ohlcv[0]`
(~100 days old) against `ohlcv[1:21]` (the 20 days after it). Both sides were
stale, so the ratio was ~1.0 and `vol_pass` was almost always spuriously `True` —
the Day 3 verdict was effectively testing price only, and FAIL rarely fired.

Fixed to `ohlcv[-1]` and `ohlcv[-21:-1]`. The existing test passed against the
buggy code because the fixture also placed the newest bar at index 0, mirroring
the bug instead of the real data layout; the fixture was corrected and two
regression tests added. Both were confirmed to fail against the old code.

### Weakness 7 — Rank & Replace could not rotate FAIL-verdict positions

`if days_held_rr < 7 or verdict_rr != "PASS": continue` meant a position whose
breakout never confirmed was the one thing that could never be swapped out —
backwards. It was defensible when a FAIL verdict handed the position to the
Intraday Loss Minimiser, but that was disabled earlier today, leaving FAIL
positions with no rotation path at all.

FAIL positions now rotate on a lower score gap (`RANK_REPLACE_FAIL_THRESHOLD=5`
vs 15 for PASS): less evidence should be required to abandon a breakout that
already failed to confirm.

### Weakness 8 — cooling-off raised 1 -> 7 trading days

Measured, not assumed. 4-slot CAGR (full / worst period):

| | BROAD | GROWTH |
|---|---|---|
| 1 day | +16.6 / -1.2 | +18.0 / +9.2 |
| 7 days | +16.6 / -1.2 | **+22.5 / +13.9** |

Modest but consistent, with no downside in either universe.

### Weakness 10 — reframed: the pivot buy zone had no floor

The document blamed `TRIGGER_LOOKBACK_DAYS=3`. Sweeping entry staleness 0-3 days
produced no usable signal — non-monotonic, and the two universes disagreed
(BROAD preferred 3 days stale, GROWTH preferred 2). That parameter was left
alone.

The real defect was next to it: the pivot check was a **ceiling only**.

```python
if extension_pct > MAX_PIVOT_EXTENSION:   # too far above pivot -> skip
```

Nothing bounded it below, so a stale trigger whose price had collapsed *under*
its pivot was still a valid buy — the bot could buy a breakdown. Added
`MAX_PIVOT_BREAKDOWN=0.02`. This is a correctness fix, not a tuned parameter,
which is why it is acted on without backtest support.

## Recommendations rejected on evidence

### Weakness 9 — "tighten the ATR stop cap from 14% to 10%"

Direction is right, starting point is wrong. Sweeping the base stop, 4-slot CAGR:

| stop | BROAD full / worst | GROWTH full / worst |
|---|---|---|
| 5% | +0.8 / -14.0 | +14.1 / +3.4 |
| **7% (current)** | +16.1 / -1.2 | +20.9 / +13.7 |
| **10%** | **+29.5 / +11.9** | **+34.5 / +14.5** |
| 14% | +30.6 / +14.5 | +26.6 / +6.5 |

10% is best on both universes and both worst-periods. The document is right that
14% is not optimal, but wrong that the fix is to tighten — from the *current* 7%
the profitable move is to widen. Left unchanged pending a user decision (below).

### Weakness 11 — "tighten the trail earlier, add a (1.0, 0.065) tier"

Already measured and rejected earlier today: the wide ladder with early
tightening removed beat the tight ladder on both universes. Not re-litigated.

## Not acted on

- **W12 conviction-weighted sizing** and the **RS scoring weight (10% -> 25%)**
  are both plausible and both untestable in the current harness, which has no
  `final_score` or component scores. Applying untested changes is exactly the
  error that required two reversals earlier today, so they are left open.
- **W13** is obsolete: `_compute_param_drift()` no longer exists.

## Aggression: slot count was tested and 4 is correct

Since the stated goal is an aggressive bot, concentration was tested directly:

| slots | BROAD full / worst | GROWTH full / worst |
|---|---|---|
| 2 | +22.4 / -6.3 | +3.2 / +2.0 |
| 3 | +20.5 / -3.2 | +10.4 / +4.4 |
| **4 (current)** | +16.1 / -1.2 | **+20.9 / +13.7** |
| 5 | +13.9 / -4.7 | +23.0 / +8.7 |
| 6 | +13.6 / +0.4 | +17.0 / +5.0 |

Concentration is *not* the aggressive move here: at 2 slots GROWTH collapses to
+3.2%, because with few slots the portfolio misses most of the breakouts that
supply the right tail. 4 is kept.

The real aggression lever is the base stop, not position count.

## Consequences

- The Day 3 verdict now does what it claims, so FAIL becomes a meaningful signal
  again. Combined with the W7 fix, failed breakouts have a working rotation path
  for the first time.
- More positions will receive FAIL verdicts than before. This is correct, but it
  is a behaviour change on live capital and should be watched.
- The pivot floor will reject some buys that previously executed. If trigger
  supply is already thin this reduces it further; that is the intended trade.
- Two document recommendations were rejected. If they are re-proposed later, the
  measurements above are the reason.

## Follow-up

1. **Decide on `STOP_LOSS_PCT` 7% -> 10%** (see below).
2. Conviction-weighted sizing and RS scoring weight need a harness that models
   `final_score` before they can be judged.
3. Entry timing remains the largest open problem.

## Open decision for the user

Widening the base stop 7% -> 10% is now the single largest measured lever
(+20.9% -> +34.5% CAGR on the growth universe, +16.1% -> +29.5% on broad, better
worst-period on both).

It was declined earlier today on the grounds of not wanting to hold plateaued
stocks indefinitely. That objection has since been addressed structurally by the
plateau exit, which bounds hold time independently of stop width:

| config | CAGR | avg hold | p90 hold | max hold | worst trade |
|---|---|---|---|---|---|
| 7% stop + plateau exit | +20.9% | 9d | 23d | 60d | -7.0% |
| 10% stop + plateau exit | +34.5% | 12d | 26d | 60d | -10.0% |
| 14% stop + plateau exit | +26.6% | 14d | 26d | 60d | -14.0% |

Max hold is 60 days either way — the plateau exit, not the stop, is what bounds
it. The genuine cost of widening is +3 days average hold and a maximum per-trade
loss of 10% instead of 7%. Presented for a decision rather than applied.
