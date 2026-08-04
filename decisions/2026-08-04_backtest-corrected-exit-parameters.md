# Backtest-corrected exit parameters; keep the entry tightening

- **Date:** 2026-08-04
- **Status:** Accepted
- **Amends:** `2026-08-04_widen-exits-and-tighten-entries.md` (same day — that ADR's
  exit conclusions were largely wrong and are corrected here)

## Context

The previous ADR diagnosed the bot's losses from trade arithmetic and code
reading alone, because every attempt at an empirical backtest had been blocked
(Stooq is behind bot-protection; the local network runs a TLS-intercepting proxy
that breaks Python's HTTPS clients). It concluded that the exits were amputating
winners and widened four separate parameters on that theory.

A working FMP API key then became available, which made a real replay possible
(curl works even though Python HTTPS does not). **The replay contradicted most of
that ADR.** This document records what the data actually showed and what was
reverted as a result.

## Method

- 5-minute bars for all 12 traded symbols over the full trade window, fetched
  from FMP `/stable/historical-chart/5min`. The API caps each response at ~468
  bars, so requests are paginated in ~6-day chunks; without that you silently get
  only the last 6 sessions.
- The simulator mirrors `execution_agent.py`: agent-level checks (early-loss
  kill-switch, Intraday Loss Minimiser, EMA-21 exit) fire only on a 15-minute
  poll grid, because the agent sleeps 900s between cycles. The IBKR native
  trailing stop is a resting order, so it is evaluated on every 5-minute bar and
  can fill intra-bar. `intraday_high_today` is modelled as a running maximum of
  polled prices that is never reset, matching the code.
- Where a choice existed, it was made against the new rules (the trailing-stop
  high-water mark is updated from the bar high before the bar low is tested;
  still-open positions are marked to the last close), so the figures are a floor.

### Fidelity limits (important)

The agent's `get_live_price()` reads FMP quotes with a known 15-20 minute lag,
and those exact prices are not recoverable — the prices in the real THC sell log
($258.12 high, $255.17 current) do not appear as 5-minute bar closes at all. For
a **0.5%** rule, that sampling error is the same order as the signal, so the
absolute P&L of the old configuration is not trustworthy (the simulator produced
-$1,923 against an actual -$2,821). The replay is reliable for **ranking
configurations**, not for predicting absolute returns. The trades were also
executed by an older revision of the agent, so the simulated "old" arm is a
reconstruction, not a reproduction.

## What the data showed

### Finding 1 — there was no right tail to protect

Maximum favourable excursion from each real entry:

| | value |
|---|---|
| Median MFE | **+4.28%** |
| Best MFE of any trade (NBIX) | **+9.55%** |
| Trades reaching +20% (the power-hold trigger) | **0 of 12** |
| Trades reaching +8% | 2 of 12 |
| Buy-and-hold to last close, mean | **-2.62%** |

This is the finding that overturns the previous ADR. Holding these positions
*longer with no exits at all* loses money. The premise that wider stops would
let winners run assumed winners existed; in this sample none did. The exits were
indeed leaving ~4% on the table, but the prize was 4%, not the 20-50% CAN SLIM
expects.

### Finding 2 — ablation: only one of the four exit changes helps

Each row changes exactly one thing from the old baseline:

| Config | P&L | expectancy | payoff |
|---|---|---|---|
| A. Old baseline | -$1,923 | -0.62% | 1.61 |
| **B. Intraday pullback 0.5% → 3% only** | **+$1,968** | **+0.53%** | 1.28 |
| C. Early-loss stop 2% → 7% only | -$2,184 | -0.73% | 1.48 |
| D. Wide profit tiers + time tiers off only | -$2,596 | -0.73% | 1.47 |
| E. B + D | +$1,179 | +0.41% | 1.15 |
| **F. All four (what was shipped)** | **-$486** | -0.07% | 0.82 |

The single-parameter fix (B) beats the full change set (F) by **$2,454**. The
shipped configuration was worse than doing just one thing.

A 2-D sweep confirms the pullback effect is the dominant one and is robust:
every value in the 2-5% range beats 0.5% by more than $4,000, while the surface
within 2-5% is flat and non-monotonic (2%: +$3,418, 3%: +$1,968, 4%: +$2,706),
i.e. the precise value is inside the noise. The early-loss stop is monotonic in
the other direction — tighter is better at every pullback setting tested.

### Finding 3 — the early-loss reasoning was simply wrong

The previous ADR argued a 2% Day 0-1 stop "sits inside the band a legitimate
breakout undercuts". That overlooked what the code does: the trigger calls
`arm_exit()`, which places a tight 0.6% *trailing* stop and rides any bounce
back up. It does not sell at the trigger price. A tight trigger is therefore
cheap, and loosening it to 7% simply converted small losses into large ones
(HWM -2.73% → -6.12%, RSI -2.77% → -5.14%, OII -1.48% → -3.55%).

### Finding 4 — the entry filter is the best-supported change

Of the 12 symbols actually traded, **11 would be rejected by the new screener**;
only TTWO passes. Four are rejected on sector alone (EGP, FR, WSFS, TRV — REITs,
a bank and an insurer). This is the strongest single result in the analysis and
the entry changes are kept unaltered.

It is *not* however proof of improved returns. Measured over the same calendar
window, the 79-name pool that passes the new screener returned a mean of -1.21%,
slightly worse than the 12 traded names (+2.92%). That comparison measures
buy-and-hold from a fixed date rather than entries on breakouts, so it does not
model what the bot does — but it is honest evidence that the filter is not a
demonstrated performance improvement on this window.

### Finding 5 — not a market-regime problem

SPY was above its 200-day SMA on every trade date and rose +0.79% over the
window, so `MARKET_DIRECTION_FILTER` behaved correctly and correctly permitted
trading. Growth simply underperformed. The losses are not explained by regime.

## Decision

| Parameter | Previous ADR shipped | Now | Basis |
|---|---|---|---|
| `INTRADAY_PULLBACK_PCT` | 0.03 | **0.02** | Validated; dominant effect |
| `EARLY_LOSS_STOP_PCT` | 0.07 | **0.02 (reverted)** | Contradicted; -$2,454 |
| `TRAIL_PROFIT_TIERS` | wide (5-6.5% from +20%) | **reverted to 2-5% ladder** | Contradicted; -$790 |
| `TRAIL_TIME_TIERS_ENABLED` | false | **true (reverted)** | No measurable effect |
| Power-hold rule | added | **kept** | Never fired; inert, risk-free |
| Screener thresholds + sector exclusion | added | **kept** | Rejects 11 of 12 losers |

Two further points recorded for whoever touches this next:

- **The profit ladder cannot be both.** `_compute_dynamic_trail_pct()` ratchets
  one-way, so the ladder must be monotonically tightening; you cannot have
  tight early profit-taking *and* a wide late trail. Choosing the tight ladder is
  a deliberate bet that entries produce 3-9% moves rather than 20%+ runs.
- **That bet is in tension with the power-hold rule**, which clamps to a 2% trail
  at exactly the +20% point where power-hold would engage. The rule is kept
  because it cannot increase risk (the trailing stop is never suspended) and is
  inert unless a genuine leader appears, but the two must be revisited together
  if the tightened screener starts producing 20%+ moves.

## Consequences

**Positive**

- The one change with strong evidence is retained and tuned to its measured
  optimum; the sample swing from the old value is roughly +$5,300.
- Three unvalidated changes that the data showed to be harmful are reverted.
- The project now has a working FMP-based replay harness, so future parameter
  changes can be tested rather than argued.

**Negative / accepted risks**

- **Sample is 13 trades over four weeks.** The ranking of configurations is
  consistent and large in magnitude, but the exact optima are certainly
  overfitted. Values were chosen off plateaus rather than peaks for this reason.
- Absolute P&L from the simulator is not trustworthy (see fidelity limits); only
  relative comparisons are used for decisions.
- The strategy is now explicitly tuned to bank 3-9% moves. If the entry filter
  works as intended and starts producing real leaders, this tuning becomes
  actively wrong and must be revisited.
- The entry filter remains theoretically motivated rather than empirically
  proven.

## Follow-up

1. Apply `migrations/add_power_hold.sql`.
2. Re-run the replay after ~20 further closed trades, this time against trades
   the current code actually produced.
3. Watch specifically for any position reaching +20%. The first time that
   happens, the profit ladder and power-hold tension above must be resolved.
