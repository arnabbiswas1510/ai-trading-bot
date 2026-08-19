# ADR: Thesis Stop — ATR-normalised early exit for breakouts that never confirm

**Date:** 2026-08-09
**Status:** Accepted
**Supersedes/relates to:** 2026-08-01 (early loss kill-switch), 2026-08-04 (intraday loss minimiser disabled)

---

## Context

The portfolio holds a maximum of **4 concurrent positions**, so each is ~25% of
capital. A 7% loss on one name is a **1.75% portfolio drawdown**. In a 20-name
book a 7% single-name loss is 0.35% — an order of magnitude less consequential.
Concentration is what makes the cost of riding a dead breakout down to the
trailing stop unacceptable here.

Observed live behaviour prompted this. Of the closed trades with Day-2 data:

| Cohort | Avg return | Loss rate |
|---|---|---|
| Weak on Day 2 | **-3.67%** | 100% |
| Not weak on Day 2 | **+1.36%** | 33% |

RSI and HWM both drifted lower from day 1 and were eventually sold by the
trailing stop at roughly the **full 7%** — meaning they never set a high-water
mark above entry at all. The trail only ratchets from the HWM, so for a
breakout that never works the trail is a *fixed* 7% loss, not a protective one.

The obvious counter is ADR 2026-08-04, which disabled the Intraday Loss
Minimiser because it roughly halved expectancy. That result is real, but it does
**not** generalise, for a reason that is easy to miss (see below).

## The distinction that makes this different

The Intraday Loss Minimiser required `today_high >= buy_price * 0.995` — the
position had to have rallied **back to or above entry** for it to fire. It
therefore cut positions that were *working*, clipping exactly the right tail the
strategy depends on.

The Thesis Stop targets the **opposite population**: breakouts that have
**never closed above entry**. Same superficial shape ("cut early losers"),
completely different trade. The `closed_above_entry` latch is the mechanism
that enforces the separation, and it is the single most important part of this
change.

## Decision

Add a **Thesis Stop** to `monitor_portfolio_intraday()`:

- Fires between **day 2 and day 5** inclusive
- Only while `closed_above_entry` is **False**
- Only when `unrealized_pct <= -1.0 x entry_atr_pct`
- **Arms an exit** via `arm_exit()` — it does not market-sell
- Skipped for power-held positions and positions already armed

Volatility normalisation is not optional. Position ATRs differ enormously —
DXCM 4.04%/day, SWK 3.54%, NBIX 2.88%, CPAY 2.76%. A fixed 2% stop on DXCM is
half a normal session and would fire on pure noise.

Configuration (all env-overridable): `THESIS_STOP_ENABLED=true`,
`THESIS_STOP_ATR_MULT=1.0`, `THESIS_STOP_START_DAY=2`,
`THESIS_STOP_LAST_DAY=5`, `THESIS_STOP_ATR_FALLBACK=3.0`.

## Evidence

> ⚠️ **CORRECTED 2026-08-17.** The figures in this section were produced by a
> backtest that anchored the armed exit's trailing stop to the trigger bar's
> *high* — a price that printed before the stop was placed. Corrected values:
> PASS **+10.29 [−1.21, +23.37] P=92%** (CI now crosses zero), BROAD **−9.43
> [−25.14, +6.96] P=15%**. The "armed exit beat an immediate market sell in
> both universes" claim below does **not** survive: corrected, it wins on PASS
> and straddles/loses on BROAD. The thesis stop remains enabled on the strength
> of its point estimate and loss-side metrics, but is no longer a demonstrated
> result. See `decisions/2026-08-17_armed-exit-backtest-lookahead.md`.
> The original numbers are retained below for the record.
>
> ⚠️ **RE-EXAMINED 2026-08-17.** A four-slice sweep on the corrected model found
> **no multiplier/start-day positive in both universes under both intra-bar path
> assumptions**, and a slot-free counterfactual showed the rule's *direct* effect
> on the trades it cuts is near zero (−0.11% mean BROAD, +0.51% PASS), with
> essentially no winners cut (0 of 89 PASS cuts would have reached +20%). The
> portfolio-level ΔCAGR swings are dominated by slot-timing luck. The rule stays
> **enabled and untuned at 1.0× from day 2**, reclassified as loss-shaping rather
> than return-enhancing. See `decisions/2026-08-17_thesis-stop-reexamination.md`.

Paired stationary-block bootstrap, 2000 reps, against baseline, in both
mandated universes (broad unselected n=256, screener-passing n=79):

| Universe | dCAGR | 90% CI | P(better) |
|---|---|---|---|
| PASS | **+18.8** | **[+7.1, +33.0]** | **100%** |
| BROAD | -0.7 | [-15.9, +15.6] | 47% |

The PASS confidence interval excludes zero. BROAD is neutral — no harm. Both
supporting metrics improve in **both** universes:

- Average loss: broad -4.43 → **-3.52**; pass -5.60 → **-4.39**
- Payoff ratio: broad 1.86 → **2.12**; pass 1.85 → **2.36**

**1.0x was chosen over 0.75x** despite a near-identical point estimate, because
its CI lower bound is materially tighter (+7.1 vs -0.7).

**Armed exit beat an immediate market sell in both universes** (market sell:
-7.6 dCAGR, P(better) 21% in broad; +8.3 vs +18.8 in pass). This is the
empirical justification for the "smart sale" requirement rather than a
market order at the trigger price.

The raw parameter sweep was **non-monotonic** across ATR multiples (1.5x scored
worse than both 1.25x and 2.0x). That is an overfitting signature, which is
precisely why the bootstrap was treated as the decision rule instead of picking
the sweep argmax.

## Negative results (deliberately not shipped)

Two other changes were investigated in the same work and **rejected**. Recording
them matters as much as the accepted change.

### Entry-filter improvements — REJECTED

Filters for pivot clearance, base tightness, RS confirmation and volume surge
were all backtested. **Not one improved both universes**; every filter that
helped one hurt the other:

| Filter | BROAD (base 22.2) | PASS (base 45.2) |
|---|---|---|
| Pivot clearance 0.98 | +25.7 | +20.4 |
| Volume surge 2.0x | +31.6 | +14.2 |
| RS > bench + 10% | +17.4 | +41.7 |
| Tight base | worse | worse |

The helps-one/hurts-other pattern is the classic signature of curve-fitting, and it
**confirms ADR 2026-08-04 finding #4**: entry *timing* carries no exploitable
edge, at least none reachable by tightening the technical trigger.
Min-score slot gating also had no effect (live scores cluster above
every tested gate). Shipping any of these would have been overfitting.

**What this does NOT establish.** It is tempting to conclude "the edge must
therefore live in the fundamental screen", and the raw baselines appear to say
so (PASS 45.2 vs BROAD 22.2 CAGR). That inference is **not supported by this
work**, for two reasons:

1. The fundamental screen was never varied. There was no counterfactual arm —
   only the technical layer was manipulated. An untested component cannot be
   credited.
2. `pass_names.txt` is the set of names passing the screen **today**, replayed
   backwards over three years (`benchmark_data/README.md`, "Known limitations").
   It carries documented **survivorship and look-ahead bias**: names that
   qualified in 2023 and later failed are absent. The 45.2-vs-22.2 gap is
   therefore inflated by an unknown amount and cannot be read as screen skill.

The honest state of knowledge is: **technical entry refinement — tested, no edge
found; fundamental screen — untested, verdict unknown.** Testing it properly
requires point-in-time fundamentals (Norgate/Sharadar/Compustat PIT) or
forward-accumulated weekly snapshots of live screener output. The current
dataset is OHLCV-only and cannot support it.

### Shortened cooling-off period — REJECTED

The hypothesis was that cutting early is safer if we can re-buy quickly. Swept
cooling-off at 0/1/2/3/5/7/10/14 days, crossed with signal cooldown 20/10/5
days. No consistent direction in either universe, all differences inside the
noise floor, and `n` barely moved — the window rarely binds in practice.
**Keep 7 days.**

## Consequences

**Positive**
- Dead breakouts are cut near -1x ATR instead of the full ~7% trail — roughly a
  1% portfolio drawdown saved per failed name at 25% sizing.
- Capital is recycled into a fresh slot days earlier, which matters most in a
  4-slot book.
- No blacklist: the name can be re-bought if it genuinely breaks out later.

**Negative / risks**
- A breakout that consolidates below entry for several days and then works will
  be cut. The BROAD-universe neutrality is the price paid for the PASS gain.
- Adds a fourth loss-cutting rule; the interaction surface with the kill-switch
  and the (disabled) minimiser is now non-trivial. Mitigated by the day windows
  being disjoint and by `tests/test_thesis_stop.py`.

**Migration dependency**
`migrations/add_closed_above_entry.sql` must be applied. Until then the code
**fails safe**: a missing column reads as `None`, and the fallback treats *any*
evidence of trading above entry (`highest_unrealized_pct > 0`, `hwm_price >
buy_price`, or `intraday_high_today > buy_price`) as follow-through. That is
deliberately more conservative than the latch it replaces — it can only cause
the stop to fire *less*, never to cut a working position.

## Known limitation

The backtest defines follow-through as a **daily close** above entry, whereas
the live latch is evaluated in the EOD window (3:45–4:00 PM ET) via
`get_live_price()`. This is a close approximation, not an identity. Separately,
the sim's proxy score is not the live `final_score`, so the null result on
min-score gating has limited external validity.

The PASS universe carries survivorship and look-ahead bias (see above). This
weakens the *absolute* CAGR figures, but the headline result is a **paired**
comparison — baseline and thesis-stop arms trade the same biased universe over
the same calendar — so the bias largely cancels in the difference. It does mean
the +18.8 dCAGR is measured on an optimistic backdrop and may not transfer at
full magnitude to a point-in-time universe.

## Files

- `execution_agent.py` — config block; Thesis Stop in `monitor_portfolio_intraday()`; `closed_above_entry` latch + PGRST204 fallback in the EOD block
- `telegram_notifier.py` — `notify_thesis_stop()`
- `migrations/add_closed_above_entry.sql` — latch column + backfill
- `tests/test_thesis_stop.py` — 15 tests
- `research/thesis_bt.py`, `research/entry_bt.py` — backtest harnesses
