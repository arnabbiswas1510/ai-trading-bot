# AI evaluator: replace the velocity ladder with volatility fit

- **Date:** 2026-08-24
- **Status:** Accepted
- **Supersedes in part:** `2026-08-04_ai-evaluation-gap-fail-closed.md` (the
  slow-mover ATR rating cap it describes is removed here)
- **Closes:** FU-013 in `docs/tech_debt_and_requirements_tracker.md`

## Context

`ai_evaluator.py` asked GPT for the *"Probability the stock hits +25% WITHIN 2-6
WEEKS before -7% stop loss"* and then ranked candidates on a four-rung ladder
keyed to `EstDaysTo25%`:

```
- EstDaysTo25% <= 15 (ATR >= 1.7%/day): ideal, boost rating +10-15 pts
- EstDaysTo25% 16-30 (ATR 0.8-1.7%/day): acceptable swing horizon
- EstDaysTo25% 31-60 (ATR 0.4-0.8%/day): marginal — reduce rating 15 pts
- EstDaysTo25% > 60 (ATR < 0.4%/day): NOT a swing trade — cap rating at 35
```

Three things were wrong with this.

**1. Neither number describes the bot.** There is no +25% profit target — it was
removed — and there is no -7% stop. The live ladder is an entry stop of
`2.5 x ATR` clamped to 10%-12%, a profit lock armed at +5%, and a 1.5% trail
below the high-water mark thereafter. The best trade in the bot's live history
closed at **+4.73%**; the number of trades that have ever reached +25% is
**zero**. The model was estimating the odds of a payoff structure the bot does
not trade.

**2. `est_days_to_target` was ATR wearing a disguise.** It is defined as
`int(round(25.0 / atr_pct))` (`technical_screener.py`, two sites). Dividing a
constant by ATR is a monotonic decreasing rescaling of ATR, so the four-rung
ladder was not a horizon filter at all — it was **an unbounded preference for
volatility**, stated in units that concealed it.

**3. That preference is actively harmful**, because it selects for exactly the
names the exit ladder protects worst:

- The entry stop **caps at 12%**. Room measured in the stock's own daily range
  therefore *shrinks* as ATR rises: at ATR 2.0% a position has 5.0 ATR of room;
  at ATR 7.2% it has 1.7 ATR.
- The locked trail is a **fixed 1.5%**, not ATR-scaled, so for a high-ATR name it
  sits inside a single session's noise.

So the rubric bought upside the exits were never going to collect, in names the
stop could not hold.

## Measurement

`research/atr_rank_bt.py` (added here) varies **only the ranking score** and
holds the universe, signals, slots and exits fixed. This is the right unit of
analysis because the AI rating does not decide *whether* to trade — it decides
*which* candidate wins a scarce slot, so preferring a high-ATR name necessarily
*displaces* another. Entry stops are modelled per-position at the real
`clamp(2.5 x ATR, 10%, 12%)` rather than the flat stop `research/port_sim.py`
uses, which would have hidden the mechanism under test.

Basis: 256 symbols, 2,315 breakout signals, 775 sessions (3.09 years), 4 slots,
shipped exits. Significance is a paired block bootstrap over the same signal set.

**The live rubric, at the strength it actually ships, costs 9.5pp of CAGR:**

| Ranking policy | CAGR | vs neutral | P(better) |
|---|---|---|---|
| Neutral (no ATR term) | +35.2% | — | — |
| **Live rubric (x1.0)** | **+26.2%** | **-9.5pp** | **17%** |

And it is a clean **dose-response**, which is what rules out coincidence:

| Strength | x0.25 | x0.5 | **x1.0** | x1.5 | x2.0 |
|---|---|---|---|---|---|
| vs neutral | -2.1pp | -4.9pp | **-9.5pp** | -12.6pp | -15.3pp |
| P(better) | 39% | 30% | 17% | 8% | **2%** |

Monotone across five doses. The harm scales with the size of the tilt.

**The mechanism is visible in the trade tape.** All twelve worst trades in the
neutral portfolio had entry ATR >= 4.25%, and seven closed at exactly -12.00% —
the stop clamp — within 1-4 sessions:

```
-12.00%  held 2d  entry ATR 4.80%      -12.00%  held 1d  entry ATR 6.33%
-12.00%  held 2d  entry ATR 6.22%      -12.00%  held 1d  entry ATR 5.88%
-12.00%  held 1d  entry ATR 7.28%      -11.46%  held 4d  entry ATR 5.30%
-12.00%  held 4d  entry ATR 4.97%      -11.11%  held 4d  entry ATR 8.96%
-12.00%  held 1d  entry ATR 5.81%      -10.84%  held 5d  entry ATR 4.81%
```

4.8%/day is precisely where `2.5 x ATR` exceeds the 12% clamp.

## Decision

**1. Delete the velocity ladder. Do not replace it with the opposite tilt.**
Preferring *low* ATR scored -1.4pp (P=42%) — no better than neutral. The finding
is that the *unbounded* tilt is harmful, not that slow is good. A decile table
agrees: deciles 8-9 (ATR 3.18-4.96%) were the *best* buckets. The failure is
confined to the extreme tail.

**2. Penalise only the tail, at the mechanically-motivated cut.** Every tail-cap
tested beat neutral, and the cut is anchored to the 12% clamp rather than fitted:

| Penalise above | +3.5% | +4.0% | +4.8% | +5.5% | +6.5% |
|---|---|---|---|---|---|
| vs neutral | +4.8pp | +3.4pp | +3.0pp | +7.8pp | +4.5pp |
| P(better) | 74% | 67% | 63% | 95% | 92% |

The prompt penalises above **4.8%/day** — the clamp point — not the
best-scoring 5.5% cut. See "provisional" below.

**3. Redefine `est_days_to_target` to measure the +5% profit lock**
(`est_days_to_lock()` in `scoring.py`), because that is the only upside
threshold the bot acts on. This also demonstrates *why* velocity cannot
discriminate: `5 / atr` is under 7 sessions for any candidate above ~0.75%/day,
so it is non-binding for essentially every name the screener surfaces.

**4. One classifier, not four.** `volatility_fit()` in `scoring.py` is used by
the screener, the prompt and the Telegram digest;
`frontend/src/lib/volatilityFit.js` mirrors it for the UI. This session already
had to fix the *same* helper triplicated and drifted across three components
(`2026-08-23_exit-detail-panel.md`); the pattern is not repeated here.

## What the mandatory doc-sync grep caught

Searching for `25%` and the ladder's labels found three live sites the routing
table would not have suggested, all of which asserted a target that does not
exist:

- `ai_evaluator.py` — a **second** prompt instruction, in the rationale
  requirement, still demanding the model address "whether it can reach 25%
  within 2-6 weeks".
- `telegram_notifier.py` — the daily digest line `Est. {n} days to +25%`.
- `DashboardView.jsx` — a user-facing progress bar on open positions reading
  *"Expected at day N: +X% toward +25% goal"*, plus an `Est. +25% ~Nd` column.

The dashboard panel also read `entry_est_days_target` directly. Since rows
written before this change recorded days-to-+25%, it now derives the horizon
from `entry_atr_pct` instead, so legacy rows do not misreport pace ~5x.

## Is CAN SLIM's 20-25% target simply wrong?

No — and this is worth stating precisely, because the methodology this bot
implements *does* advocate taking profits at 20-25%, so removing that framing
needs justification beyond "the code doesn't do it".

`research/oneil_target_bt.py` (added here) separates two questions that are easy
to conflate.

**A. Can the stocks do it? Yes.** Over the same 2,315 breakout signals, walking
forward bar by bar against an 8% hard stop, resolving ambiguous bars
pessimistically as stops:

| Target | Reached before the stop | Median sessions |
|---|---|---|
| +10% | 54.3% | 27 |
| +15% | 41.0% | 42 |
| +20% | 30.7% | 52 |
| **+25%** | **22.0%** | **59** |

p90 of max gain before the stop is **+38.9%**, p99 is +134.7%. O'Neil is not
wrong about the population: roughly one breakout in five runs 25%.

**B. But "within 2-6 weeks" was fantasy.** That was the prompt's literal claim,
and it is the part the base rate destroys:

| Reached +25% within | 10 sessions | 20 | **30 (the prompt's window)** | 60 | 120 |
|---|---|---|---|---|---|
| Rate | 1.3% | 3.3% | **5.1%** | 11.3% | 22.0% |

The model was being asked to estimate the probability of a **5%-base-rate**
event and use it as the primary ranking criterion. The median winner takes 59
sessions — about three months, not two to six weeks.

**C. And with 4 slots, waiting for the target is ruinous.** Same signals, same
slots, exit policy varied:

| Exit policy | Trades | Median hold | CAGR | vs shipped | P(better) |
|---|---|---|---|---|---|
| **Shipped ladder** | **295** | **9d** | **+35.2%** | — | — |
| O'Neil +25% / 8% stop | 45 | 58d | +4.7% | -32.2pp | **0%** |
| O'Neil +20% / 8% stop | 44 | 52d | +14.6% | -22.1pp | 5% |
| O'Neil +25% / 8% + 8-week rule | 50 | 54d | +13.3% | -23.2pp | 1% |
| Take profit at +10% / 8% stop | 120 | 22d | +20.8% | -15.5pp | 4% |

Note *why* it loses. Per-trade expectancy for the 25% target is **+1.53%**,
slightly BETTER than the shipped ladder's +1.33%. It loses on **turnover**: 45
trades instead of 295, because every position occupies a quarter of the entire
portfolio for ~3 months, and 53% of them stop out anyway.

**D. The binding constraint is slot scarcity, not stock behaviour.** Varying only
the slot count:

| Slots | 4 | 6 | 10 | 20 | 40 |
|---|---|---|---|---|---|
| O'Neil +25% CAGR | +4.7% | +4.8% | +15.7% | +14.1% | **+16.8%** |
| Shipped ladder CAGR | +35.2% | +31.0% | +26.5% | +24.2% | +15.2% |

The two cross at roughly **40 slots**. O'Neil ran a wide, diversified book where
holding a dozen names for months costs nothing at the margin; this bot runs four
concentrated slots where it costs everything. Same methodology, opposite optimal
exit — the target is not wrong in general, it is wrong *at this portfolio width*.

The shipped ladder's own decline with slot count (+35.2% at 4, +15.2% at 40)
shows the mirror image: it depends on taking only the best-ranked signals, which
is exactly why the ranking retune above is worth 9.5pp.

### Correction: the slot-width framing above is too strong

The section above concluded the target "is wrong at this portfolio width". That
overstates what was measured, and the objection that exposes it is a fair one:
**O'Neil recommends 4-5 positions too.** He calls over-diversification "a hedge
against ignorance". So the concentration and the 25% target are *both* his, and
an argument that they are incompatible is an argument that his system is
internally incoherent — which is a much bigger claim than the evidence supports.

The flaw is that the test bolted O'Neil's EXIT onto this bot's entry process and
ran it always-invested. That is not CAN SLIM. His concentration is inseparable
from **M — market direction** (he insists on being in cash during corrections)
and from taking few entries at proper pivots in leading groups.
`research/oneil_full_system_bt.py` restores what the offline data allows: a SPY
> 50-day MA gate, and an entry cap. The shipped ladder is run through the
identical filter every time, so the control moves with the treatment.

**Screener-passing universe (79 names), 4 slots — the result inverts:**

| Filter | Shipped | O'Neil +25% +8wk | vs control | P(better) |
|---|---|---|---|---|
| always invested | +58.5% | +30.8% | -29.8pp | 12% |
| **+ M: SPY > 50d MA** | +59.2% | **+64.8%** | **+7.2pp** | 58% |
| **+ M + top-1/day** | +42.5% | **+69.9%** | **+31.1pp** | 89% |

And the trade profile under the last row is textbook O'Neil: per-trade
expectancy **+9.94% vs +1.99%** (5x), win rate 40% vs 61%, median hold 24d vs
5d. Fewer, larger, lower-hit-rate wins. Note also that selectivity *hurt* the
shipped ladder (+59.2 -> +42.5) while it *helped* the target (+64.8 -> +69.9) —
the two strategies monetise opposite things, turnover versus magnitude.

**So the honest verdict is not "the target is wrong at 4 slots". It is:**

> The 25% target fails on THIS BOT'S entry quality. Its viability at O'Neil's
> entry quality is unresolved, and the fair test leans in his favour.

### Why it is still not actionable

1. **It wins only in the survivorship-biased universe.** `pass_names.txt` is a
   today-snapshot replayed over history. This repo already has a standing rule
   (`research/rank_policy_bt.py`) that *a result is only actionable if it holds
   in BOTH universes*. On the broad universe the target loses at every filter
   level: -23.2pp, -40.6pp, -27.3pp, all at P<=1%.
2. **The bias is asymmetric in exactly the wrong direction.** Survivorship
   over-represents large multi-month runs. A strategy whose entire edge is
   catching those runs is inflated *more* by it than a high-turnover strategy
   is. This is precisely the artefact that would manufacture the observed flip.
3. **P=89% with CI [-11.1, +82.0] includes zero**, on n=73 trades.

Resolving it needs a **point-in-time universe** reconstructed from
`watchlist_history` rather than a today-snapshot. Tracked as FU-014.

### One result that IS clean

Market timing helps the **shipped** ladder on the unbiased broad universe:
**+35.2% -> +46.6% CAGR** just from refusing to enter when SPY is below its
50-day MA. That holds in the universe without survivorship bias and is
independent of the exit debate. The live bot already has a market-direction
gate; this is evidence for keeping it strict rather than relaxing it.

**Implication for the planned small-cap expansion.** Exit policy and slot count
are not independent choices. Widening the universe without widening the book
keeps the current ladder correct; widening the book past ~10 slots makes
longer-horizon targets progressively more competitive. Neither should be changed
without re-running this comparison.

## Consequences

- Every candidate's AI rating changes. Scores are not comparable across
  2026-08-24, and `trigger_history` rows before and after measure different
  things.
- `est_days_to_target` keeps its column name (four migrations and two history
  tables reference it; renaming buys nothing) but changes meaning. The column
  comment in the already-applied migration is left as historical record.
- `PROGRESS_DEFICIT` is fixed rather than deleted. No live code writes it, but
  legacy rows can still render it, so correcting it was cheaper than proving it
  unreachable.

## Provisional — revisit at the 2026-09-20 exit-parameter review

- **The 20-pt tail penalty is directionally supported, not tuned.** Its CI is
  wide (+0.2 to +19.5pp at the 5.5% cut) and the cut values are non-monotonic —
  4.8% scores *worse* than both 4.0% and 5.5%, which is exactly the signature of
  a noise-sensitive threshold. The cut shipped is the one with a mechanical
  justification, not the one with the best score. Do not re-tune it on this
  sample.
- **A band grid found a better-scoring cell** (2.5-3.5%, +9.9pp, P=85%) which is
  deliberately **not** shipped: its immediate neighbours swing 7pp, so it is
  fitted to noise.
- **All results come from `broad_names.txt`.** An out-of-sample confirmation on
  the 79-symbol screener-passing universe is still outstanding.
- **The real fix may be in the exits, not the prompt.** The -12% cluster is the
  stop clamp failing, not the model choosing badly. Whether `ATR_STOP_MAX_PCT`
  should scale for high-ATR names is an exit-parameter question and belongs to
  the scheduled review, not here.
