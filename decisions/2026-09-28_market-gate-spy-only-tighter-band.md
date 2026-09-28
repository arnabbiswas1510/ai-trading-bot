# Market direction gate retune: SPY-only, 0.5% buffer (drop QQQ, tighten band)

- **Date:** 2026-09-28
- **Status:** Accepted
- **Supersedes in part:** [decisions/2026-08-22_market-direction-gate-spy-qqq.md](2026-08-22_market-direction-gate-spy-qqq.md) — parameter values only; the gate's structure and fail-closed semantics are unchanged.

## Context

The operator's goal for the CANSLIM "M" gate is explicit: **keep the bot active
in most markets.** The gate is drawdown insurance, not an entry signal, so an
over-strict gate costs opportunity while buying little extra protection. The
shipped configuration since 2026-08-22 was **SPY+QQQ, 1% buffer, 20-session
slope**. The operator asked whether a more active setting would serve them
better, and — separately — decided to keep `MARKET_DIRECTION_FILTER_ENABLED=true`
and drop QQQ.

## What was measured

A new reusable harness, `research/market_gate_bt.py`, replays the exact live gate
logic (`is_market_bullish()`: **every** benchmark closes above `SMA-200 × (1 +
buffer)` **and at least one** SMA-200 is non-falling over `slope` sessions) over
**4,965 SPY sessions (2007–2026)** from FMP. It sweeps `buffer ∈ {0, 0.5, 1, 1.5,
2, 3}%` × `slope ∈ {5, 10, 20, 30, 50}` and scores each cell on activity (% of
sessions the gate is open), total return / CAGR of a SPY-long-when-open equity
curve, and max drawdown. It also measures the SPY+QQQ pair to price what QQQ adds.

### Findings

1. **Activity and total return are strongly correlated: corr = +0.878** across
   the 30 SPY-only cells. On this sample, a *more active* gate made *more* money —
   because it kept capital invested through up-markets, not because it timed
   anything. The forward-return edge of the gate is ~0.1% (noise).

2. **0.5% buffer dominates 1%.** At slope=20, dropping the band from 1% → 0.5%
   raises activity (69.6% → 70.8%), raises total return (238% → 264%) **and**
   lowers max drawdown (−21.4% → −20.5%). There is no tradeoff here — the tighter
   band is better on all three axes.

3. **Widening the band is strictly worse.** 2% buffer cuts return to ~199% and
   deepens drawdown to −26%; 3% cuts return to ~146%. A wider band buys only
   6–8pp of extra insurance while surrendering large amounts of return. **Never
   widen past 1%.**

4. **slope=20 is fine; slope=30 is the one to avoid.** slope ∈ {5,10,20,50} all
   produce ~−19 to −21% drawdowns; slope=30 consistently produces the worst
   (−24 to −28%). The operator chose to keep slope at **20**.

5. **Dropping QQQ is negligible.** Removing QQQ from the pair moved activity by
   +0.6pp and drawdown by ~−1pp — inside noise. QQQ was not earning its place as
   a second required benchmark, and a single-benchmark gate is simpler to reason
   about.

## Decision

Retune the shipped defaults:

| Parameter | Old (2026-08-22) | New (2026-09-28) |
|---|---|---|
| `MARKET_DIRECTION_TICKERS` | `SPY,QQQ` | **`SPY`** |
| `MARKET_DIRECTION_BUFFER_PCT` | `0.01` | **`0.005`** |
| `MARKET_DIRECTION_SLOPE_DAYS` | `20` | `20` (unchanged) |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | `true` (unchanged) |

The gate **structure** — every benchmark above the SMA-200 buffer AND at least
one non-falling SMA-200, uniformly fail-closed — is unchanged from the 2026-08-22
ADR. Only the parameter values move.

The dashboard's independent gate mirror (`backend/screener.py`) already read
`BUFFER_PCT` / `SLOPE_DAYS` from the same env vars; its default buffer was updated
to `0.005` and it now derives its **benchmark index set** from
`MARKET_DIRECTION_TICKERS` (SPY→^GSPC, QQQ→^IXIC) instead of hardcoding both
indices — otherwise dropping QQQ from the agent would have left the dashboard gate
*stricter* than the live agent (a silent drift the mirror exists to prevent).

## Consequences and caveats

- **This is drawdown insurance, not alpha.** The +0.878 activity/return
  correlation is a property of staying invested in a secular bull tape; it is not
  a claim that the gate predicts returns. Its measured mean-return edge is
  negative outside 2008.
- **The equity model is SPY-long-when-open, not this bot's breakouts.** Breakouts
  fail worse in downtrends than SPY's own mean implies, so the real insurance
  value to this strategy is a *lower bound* on the numbers above, and the
  "activity helps" conclusion is *overstated* for the actual strategy. This is why
  the gate is retained at all rather than loosened further.
- **The parameters remain tuned on index data, not this bot's trades** (tracker
  FU-011). All closed trades to date fall inside one market window where every
  candidate config returns BULL, so the trade-history replay cannot discriminate.
  Re-validate once the closed-trade sample spans more than one regime.
- Config-only change: no migration, no schema change. Fully env-overridable.

## Reproduce

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
python3 research/market_gate_bt.py --insecure
```
