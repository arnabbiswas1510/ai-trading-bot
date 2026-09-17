# Technical Triggers

Setup detection. Runs against the fundamental watchlist after the close and writes scored
candidates for the next session's open.

**Source:** `technical_screener.py` · **Schedule:** Mon–Fri, after the fundamental screen ·
**Output:** `daily_triggers`

---

## Position in the pipeline

The fundamental screen answers *is this a quality growth business?* This stage answers *is
its price structure signalling accumulation right now?* Both must be true. Fundamentals
without a setup is a watchlist; a setup without fundamentals is a chart pattern.

```
watchlist (latest run)
    │
    ▼  FMP EOD history — 380 calendar days, ≥ 50 bars required
    │
    ▼  SMA-50 · 50-day average volume · 252-day rolling high · ATR% · RS vs SPY
    │
    ▼  Phase 1  BREAKOUT
    ▼  Phase 2  PRE_BREAKOUT            (names not already triggered)
    ▼  Phase 3  PRE_BREAKOUT_RELAXED    (only if quota unmet)
    │
    ▼  archive previous run → trigger_history, then truncate
    │
    ▼  daily_triggers  →  ai_evaluator.py scores in place
```

Only the **most recent** watchlist run is screened, so stale fundamental candidates are
never carried forward.

---

## Relative strength

RS is computed once per run and applied to every candidate:

```
excess      = 12-week stock return − 12-week SPY return   (percent)
rs_score    = 100                     if excess ≥ +10
            = 50 + (excess × 5)       if −10 ≤ excess < +10
            = 0                       if excess < −10
```

`rs_score` is **not** a percentile despite the name of the gate — it is a clipped
linear transform of excess return, bounded to 0–100. Nothing is ranked against
the rest of the field.

`RS_MIN_GATE` (50) enforces O'Neil's leadership requirement — the stock must be outperforming
the index, not merely rising with it. A stock making highs in a market making larger highs is
a laggard. A score of 50 corresponds to exactly matching SPY, so the gate admits any candidate
with non-negative excess return.

Missing history yields a neutral 50 rather than a rejection.

### The clip saturates, and that is measured but not yet acted on

Because the watchlist is already filtered to growth names near their 52-week
highs, most candidates clear +10% excess comfortably and receive an identical
score of **100** — 176 of 233 archived triggers (**76%**). A component that is the
same for three of every four candidates cannot separate them, so the 10% weight
`rs_score` carries in `final_score` does far less ranking work than the table
below implies.

Three **research-only** columns record what the clip discards. They are written to
`daily_triggers` and archived to `trigger_history`:

| Column | Meaning |
|---|---|
| `rs_12w_return` | The stock's raw 12-week return |
| `rs_excess_return` | 12-week excess vs SPY, **unclipped** |
| `rs_percentile` | 1–99 rank of `rs_excess_return` within that run's trigger cohort (ties share the average rank; a cohort of one scores a neutral 50) |

**None of these three is read by any buy gate, ranking, sort order, position size
or exit rule.** `compute_rs_score()` and `compute_final_score()` are unchanged and
the bot trades exactly as it did before they were added. They exist so the
question "would a real ranking pick better stocks?" can be answered from data
rather than assumed — the available evidence at the time of writing pointed the
*opposite* way to the obvious fix, with higher relative strength associated with
*worse* forward returns inside this already-momentum-filtered universe.

The columns are populated by `migrations/20260917_add_rs_percentile.sql`. If that migration
has not been applied, the screener detects the rejected insert, prints a warning
and retries without them — live screening is never interrupted, only the research
annotation is lost. `schema_guard` reports the absence as **advisory**, never as a
trading block.

Scheduled for review on **2026-10-19** via the
[Provisional Decision Register](../decisions/provisional_decisions.json) entry
`rs-percentile-shadow`; run `python3 research/rs_percentile_review.py --insecure`.
See `decisions/2026-09-17_rs-percentile-shadow-column.md` for why.

---

## Trigger types

Evaluated in priority order; a name that triggers in an earlier phase is not reconsidered.

### `BREAKOUT` — the primary signal

All three must hold on the latest bar:

| Condition | Test |
|---|---|
| Trend | `close > SMA-50` |
| Volume surge | `volume ≥ 1.50 × 50-day average` |
| Pivot proximity | `close ≥ 252-day rolling high × 0.95` |

Plus `RS ≥ 50`.

The volume condition is what distinguishes a breakout from a drift. Price can cross a pivot
on thin trade and fall straight back; **expanded volume is the evidence of institutional
participation** — buyers with the size to sustain a move.

`PIVOT_PROXIMITY = 0.95` admits names within 5% of the rolling high rather than requiring a
literal new high, which catches the breakout bar itself rather than only its aftermath.

### `PRE_BREAKOUT` — the coil

Anticipates the breakout rather than reacting to it:

| Condition | Test |
|---|---|
| Proximity | Within **8%** below the rolling high |
| Trend | `close > SMA-50` — a coil, not a breakdown |
| Volume | Last **3-day** average **< 1.00 ×** the 50-day average — *contracting* |
| Advance | ≥ 2 of the last 3 closes up |
| RS | ≥ 50 |

The inverted volume requirement is the point. A base that tightens on **declining** volume
means sellers are exhausted — supply has been absorbed. That is the classic pre-breakout
condition, and entering here means entering before the pivot rather than chasing it.

The trade-off is that confirmation has not arrived. This is why a pre-breakout is held to a
*higher* score floor at the buy stage (65) than a confirmed breakout (60).

### `PRE_BREAKOUT_RELAXED` — quota fill

Identical structure with widened tolerances: proximity 10%, volume ≤ 1.10×.

**Emitted only when the strict phases produce fewer than `MAX_POSITIONS` candidates.** The
target is deliberately tied to portfolio capacity — the screen aims to give the buy loop
enough options to fill the book without dropping quality gates entirely.

Relaxed triggers carry the lowest score floor at the buy stage (58) but are, by construction,
the weakest candidates of the day. If the book is routinely being filled from this phase, the
constraint is upstream: the fundamental screen is not producing enough names in a genuine
setup.

---

## Scoring

Each trigger receives a technical quality score from volume surge magnitude, distance from
pivot, and extension above SMA-50. This is one of five components later combined by
`ai_evaluator.py`:

| Component | Weight |
|---|---|
| Technical | 30% |
| Liquidity | 25% |
| AI rating | 25% |
| Sentiment | 10% |
| RS vs SPY | 10% |

Note the RS component saturates at 100 for roughly three quarters of candidates
(see [Relative strength](#relative-strength)), so its *effective* contribution to
separating one candidate from another is well below the nominal 10%.

A **failure penalty** is applied from `breakout_learnings` once enough rows exist
(`LEARNING_MIN_ROWS`). Separately, `ai_evaluator.py` applies a ticker-level
history penalty from recent `trade_history` outcomes. The buy loop uses
`adjusted_score` (not raw `final_score`) when either penalty is active.

`ATR%` and `est_days_to_target` are computed here and persisted. `entry_atr_pct` is later
copied onto the position at fill and becomes the scaling factor for the
[Prove-It Stop](sell_logic.md#2-the-prove-it-stop--always-live) — a trigger written without it forces
that rule onto a generic 3.0% fallback.

### Volatility fit (how ATR is scored)

`est_days_to_target` is the estimated trading days to reach the **+5% profit
lock** at the average ATR pace — `round(5 / ATR%)`, via `est_days_to_lock()` in
`scoring.py`. The +5% arming threshold is the only upside level the bot acts on;
there is no profit target.

ATR is scored as a **band, not a ramp**. `volatility_fit()` in `scoring.py` is
the single source of truth, mirrored for the UI in
`frontend/src/lib/volatilityFit.js`:

| Entry ATR%/day | Verdict | Effect on the AI rating |
|---|---|---|
| > **4.8%** | Too volatile | **-20 pts** |
| 1.5% – 4.8% | Good fit | none |
| < 1.5% | Quiet | -10 pts |

4.8%/day is where `2.5 x ATR` exceeds the **12% clamp** on the entry stop
([Entry Stop](sell_logic.md)). Above it a position holds under 2.5 ATR of room
and is routinely gapped out within one or two sessions for the full stop loss.

#### Why not O'Neil's 20-25% profit target?

CAN SLIM advocates taking profits at 20-25%, and the *stocks* do reach it: 22% of
breakout signals trade +25% before an 8% stop (p90 max gain +38.9%). But the
median winner takes **59 sessions**, and with only `MAX_POSITIONS` slots each
such hold occupies a quarter of the book for ~3 months. Measured on the same
signals, a +25%-target exit returns **+4.7% CAGR against the shipped ladder's
+35.2%** — not because expectancy is worse (it is marginally better, +1.53% vs
+1.33% per trade) but because turnover collapses from 295 trades to 45.

This is **not** an argument that O'Neil is wrong, and the slot count is not the
culprit — he recommends 4-5 positions too. His concentration is inseparable from
market timing and from taking few entries at proper pivots. When those are
restored (`research/oneil_full_system_bt.py`) on the screener-passing universe,
the target *wins*: **+69.9% vs +42.5%**, with per-trade expectancy +9.94% vs
+1.99%.

That result is **not actionable**, because it appears only in the
survivorship-biased today-snapshot universe; on the unbiased broad universe the
target loses at every filter level (P ≤ 1%). The standing rule is that a result
must hold in both universes. The accurate statement is therefore: *the 25%
target fails on this bot's current entry quality; its viability at O'Neil's entry
quality is unresolved.* Tracked as FU-014.

One clean finding from the same test: refusing to enter when SPY is below its
50-day MA lifts the **shipped** ladder from +35.2% to **+46.6%** CAGR on the
unbiased universe — evidence for keeping the market-direction gate strict.

Raw speed is deliberately **not** rewarded. `5 / ATR%` is under 7 sessions for
any candidate above ~0.75%/day, so time-to-lock does not separate winners from
losers; ranking on it measured **-9.5pp CAGR** against a neutral ranking.
See `decisions/2026-08-24_ai-evaluator-volatility-fit.md` for why.

---

## Trigger archive

`daily_triggers` is current-state and truncated on every run. Before truncation, the
**outgoing** rows are appended to `trigger_history`.

> **Archive the OUTGOING rows, not the incoming ones.** `ai_evaluator.py` writes
> `ai_rating`, `final_score` and `score_rationale` back into `daily_triggers` *after* the
> screener inserts. Archiving the rows being inserted would therefore store NULL scores — a
> total, silent failure of the archive's purpose. The outgoing rows are fully scored and were
> the basis of an actual trading decision. A test asserts the archive is fed from a `SELECT`.

`trigger_history` also carries forward-return outcomes, attached weekly — see below.

---

## Decision log

Every buy-loop verdict is appended to `trigger_decisions` with a stable reason code:

| Class | Codes |
|---|---|
| Bought | `BOUGHT` |
| Quality | `AI_VETO`, `NO_AI_SCORE`, `SCORE_FLOOR`, `EXTENDED_ABOVE_PIVOT`, `BELOW_PIVOT` |
| Capacity | `SLOTS_FULL`, `INSUFFICIENT_CASH`, `SHARES_ZERO` |
| Other | `ALREADY_HELD`, `COOLING_OFF`, `NO_PRICE`, `BUY_FAILED`, `LOOP_HALTED` |

`trade_history` alone cannot evaluate the buy model — it contains only candidates that passed
every gate, which is selection on the dependent variable. The skip log is the control group.

Scores are snapshotted onto the decision row rather than joined, because a trigger may be
re-scored on a later run.

All audit writes are non-fatal; research instrumentation must never interrupt live screening.

---

## Forward-return outcomes

A weekly job (`backfill_trigger_outcomes.py`, Sundays 12:00 UTC) attaches realised outcomes
to settled triggers.

| Column | Meaning |
|---|---|
| `entry_ref_price` / `entry_ref_date` | The **next session's open** — what the bot would have paid |
| `fwd_1d_pct` / `fwd_5d_pct` / `fwd_20d_pct` | Close of the Nth session **of holding**, entry counted as session 1 |
| `max_gain_20d_pct` / `max_drawdown_20d_pct` | Best and worst excursion, **including** the entry session |
| `ever_above_entry` | Empirical twin of the Prove-It Stop's `closed_above_entry` latch |
| `bench_fwd_20d_pct` / `alpha_20d_pct` | Same-window SPY return and the excess over it |
| `outcome_bars` | Sessions measured so far. Drives the partial/complete distinction below |

Two conventions are load-bearing and neither announces itself if broken:

1. **Entry is the next session's open, not the trigger close.** The bot buys at the open the
   morning after a trigger. Measuring from the trigger close would credit an overnight gap
   that was never captured.
2. **`fwd_1d` is the entry day's own close** — matching the day-numbering used by every
   day-gated exit rule.

### Each horizon is written as soon as it matures

The three horizons mature at very different rates, so they are written independently rather
than as one unit. A trigger is picked up **3 calendar days** after it fires — enough for
`fwd_1d` — and is then revisited on every subsequent run, gaining `fwd_5d` and finally
`fwd_20d` as the sessions accumulate.

`outcomes_computed_at` is the completion latch: it is stamped **only** once all 20 sessions
exist. While it is NULL the row is re-selected and topped up; once stamped the row drops out.
Partial writes never contain NULLs, so a later pass only ever adds knowledge.

`max_gain_20d_pct`, `max_drawdown_20d_pct` and `ever_above_entry` carry 20-day semantics and
are therefore withheld until all 20 sessions exist. A 5-bar drawdown is not a small 20-bar
drawdown — it is a different quantity, and storing it under the 20d name would understate
risk in every study that reads the column.

The job is idempotent, and `--force` recomputes rows that are already complete.

Manual run: `python3 backfill_trigger_outcomes.py --dry-run [--limit N]`

See `decisions/2026-09-17_per-horizon-outcomes.md` for why, and for the measurement that
motivated it: writing all three horizons together left 16 of 233 archived triggers measured
and **zero** BREAKOUT rows, while `fwd_1d` was already computable for 222 of them.

---

## Parameters

| Variable | Default | Effect |
|---|---|---|
| `SMA_WINDOW` | `50` | Trend filter |
| `VOLUME_AVG_WINDOW` | `50` | Volume baseline |
| `VOLUME_SURGE_MIN` | `1.50` | Breakout volume multiple |
| `ROLLING_HIGH_WINDOW` | `252` | Pivot lookback |
| `PIVOT_PROXIMITY` | `0.95` | Breakout proximity to the high |
| `RS_MIN_GATE` | `50` | Leadership floor |
| `MIN_PRICE_HISTORY` | `50` | Bars required |
| `FMP_HISTORY_DAYS` | `380` | History fetched |
| `PRE_BREAKOUT_PROXIMITY` | `0.08` | Coil distance below high |
| `PRE_BREAKOUT_VOL_MAX` | `1.00` | Coil volume ceiling |
| `PRE_BREAKOUT_UPTREND_MIN` | `2` | Up-closes of last 3 |
| `RELAXED_*` | see [configuration](configuration.md) | Quota-fill variants |

---

## Setup

Run `migrations/20260809_add_trigger_history.sql`, then `migrations/20260809_add_trigger_outcomes.sql`. Both
are additive; the first self-seeds from the current `daily_triggers`.
