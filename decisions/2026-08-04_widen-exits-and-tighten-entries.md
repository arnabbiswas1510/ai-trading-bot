# Widen exits and tighten entries to restore positive expectancy

- **Date:** 2026-08-04
- **Status:** Accepted
- **Supersedes (in part):** the trailing-stop tiers introduced with the dynamic trail system

## Context

The bot was losing money. Analysis of the 13 closed trades in the Supabase
`trade_history` table (a realised loss of **-$2,821** over roughly three weeks)
showed the problem was not bad luck or a bad win rate — it was structural.

| Metric | Actual | Needed |
|---|---|---|
| Win rate | 46.2% (6W / 7L) | — |
| Average win | **+1.27%** | — |
| Average loss | **-2.58%** | — |
| Payoff ratio | **0.49 : 1** | > 1.5 : 1 |
| Breakeven win rate at that payoff | **67.1%** | we have 46.2% |
| Expectancy | **-0.80% per trade** | positive |
| Trades ever exceeding +2% | **1 of 13** | — |
| Median hold | **5 days** | 8-12 weeks |

The win rate was actually respectable. Losses were simply twice the size of
wins, so the strategy needed a 67% win rate just to break even.

CAN SLIM is not a high-win-rate method. Its entire edge is a fat right tail: a
minority of positions run 20-100%+ and pay for a majority of small losses. The
code had systematically removed that right tail while leaving the left tail
intact. **One of 13 trades ever exceeded +2%.** That is not a strategy with a
bad month; that is a strategy whose winners are amputated by design.

Two independent causes were identified.

### Cause 1 — the exits amputated winners

Four separate mechanisms each independently capped upside:

- `INTRADAY_PULLBACK_PCT = 0.005` — sold on a **0.5% pullback** from the
  intraday high, from Day 2 onward. That is spread and tick noise, not a
  reversal. A $250 stock wiggling $1.25 tripped it. It closed GE, THC and TTWO
  for losses.
- `EARLY_LOSS_STOP_PCT = 0.02` — a 2% stop on Days 0-1, well inside the band a
  legitimate breakout routinely undercuts before working.
- `TRAIL_PROFIT_TIERS` — began tightening at a **+3%** gain and clamped to a 2%
  trail at +20%. A growth stock that has run 20% routinely pulls back 5-8%
  before continuing, so this guaranteed an exit at +18% instead of letting the
  position develop.
- `TRAIL_TIME_TIERS` — tightened the stop purely because time had passed (6% at
  day 8 down to 3.5% at day 30) **regardless of how the position was
  performing**. Any position still open at day 30 was near-certain to be stopped
  out on noise. This made a large winner structurally impossible.

### Cause 2 — the screener was not selecting CAN SLIM stocks

`tv_api_screener.py` required revenue growth only to be `> 0`. O'Neil requires
roughly 25% sales growth. The annual EPS filter was `> 15` (should be 25). There
was **no sector exclusion at all**.

The consequence was concrete: SWK was bought at a score of 81 with **0.6%
revenue growth** against 496% EPS growth — an easy-comparison accounting
artifact, not a growth business. Its own AI rationale flagged the 0.57% revenue
figure and it was bought anyway. The bot also bought REITs (EGP, FR), an insurer
(TRV) and a bank (WSFS): rate-driven, book-value businesses structurally
incapable of the earnings acceleration CAN SLIM looks for. Meanwhile NVDA, with
70% revenue growth, sat unused in the watchlist.

(A third cause — a silent AI-evaluation gap that let un-vetted triggers bypass
every guardrail — was diagnosed and fixed separately in
`2026-08-04_ai-evaluation-gap-fail-closed.md`.)

## Decision

### Exits — stop selling on noise

| Parameter | Was | Now |
|---|---|---|
| `INTRADAY_PULLBACK_PCT` | 0.005 (0.5%) | **0.03 (3.0%)** |
| `EARLY_LOSS_STOP_PCT` | 0.02 (2%) | **0.07 (7%)** |
| `TRAIL_PROFIT_TIERS` first tier | +3% gain → 5% trail | **+20% gain → 6.5% trail** |
| `TRAIL_PROFIT_TIERS` tightest | 2% trail | **5% trail** |
| `TRAIL_TIME_TIERS` | active | **disabled by default** |

`EARLY_LOSS_STOP_PCT` is set to 7% to match O'Neil's own maximum-loss
discipline, and now coincides with the base `STOP_LOSS_PCT`.

The profit tiers deserve a specific note. They were initially widened to 8-12%,
which was wrong: the base stop is 7% and `_compute_dynamic_trail_pct()` only
ever *tightens*, so tiers wider than 7% would have silently made the entire
mechanism a no-op. The tiers are instead set slightly *inside* the base stop and
only begin at a +20% gain — below that the 7% base stop is already appropriate
and is left alone.

`TRAIL_TIME_TIERS` is retained behind `TRAIL_TIME_TIERS_ENABLED=false` rather
than deleted, so the legacy behaviour can be restored for comparison. Time held
is not a sell signal: a position that is still working should not be penalised
for still working.

### O'Neil's 8-week hold rule

Widening the stops is necessary but not sufficient — several *discretionary*
exits (EMA-21 exit, Rank & Replace, the Intraday Loss Minimiser) would still
close a strong position early. We therefore implement the rule from *How to Make
Money in Stocks* directly:

> A stock that gains 20%+ within 3 weeks of a proper breakout is behaving like a
> genuine market leader and should be held at least 8 weeks.

New constants: `POWER_HOLD_ENABLED` (true), `POWER_HOLD_GAIN_PCT` (20),
`POWER_HOLD_TRIGGER_DAYS` (21), `POWER_HOLD_DURATION_DAYS` (56), plus
`is_power_hold_active()` and `maybe_arm_power_hold()`.

While a position is power-held, the three discretionary exits are suppressed.
**The IBKR trailing stop is deliberately not suspended.** This is the key safety
property: the rule can only ever cost us opportunity (holding something that
later fades back to the trailing stop), never uncapped risk.

The flag is *persisted* to a new `portfolio_positions.power_hold` column rather
than recomputed each cycle. `highest_unrealized_pct` keeps climbing after the
21-day trigger window closes, so without a sticky flag a position that qualified
on day 12 would silently lose its protection on day 22 — the exact opposite of
the intent. Persistence degrades gracefully on `PGRST204` (migration not yet
run), so the code is safe to deploy before the migration.

### Entries — actually buy CAN SLIM stocks

| Filter | Was | Now |
|---|---|---|
| `MIN_REVENUE_GROWTH` | `> 0` | **`> 15`** |
| `MIN_ANNUAL_EPS_GROWTH` | `> 15` | **`> 25`** |
| `MIN_QUARTERLY_EPS_GROWTH` | `> 20` | unchanged |
| Sector exclusion | none | **Finance, Real Estate, Utilities** |

All four are env-tunable so they can be A/B tested and rolled back without a
code change.

Revenue growth defaults to 15 rather than the textbook 25 as a deliberate first
phase, to avoid changing too many variables at once while the exit changes are
being observed.

### On the risk of too few triggers

The obvious objection is that stricter entries plus longer holds will starve the
bot of trades. This was measured against live data rather than assumed:

- Against the live TradingView API, the new filter set returns **80 names**
  (102 before the sector exclusion — it removes 22, of which 21 are Finance).
  NVDA is now the top-ranked name.
- Of the 33 triggers fired on the day of analysis, 5 survive the strict filters
  and 4 of those score >= 60, i.e. enough to fill all four slots in a single day.
- Crucially, **longer holds cut entry demand by roughly 7x**: 4 slots over 6-day
  holds is 0.67 entries/day; over 40-day holds it is 0.10/day.

Supply therefore exceeds demand by a wide margin. Quiet periods are a feature,
not a bug: at an expectancy of -0.80% per trade, every additional trade was
costing money.

## Consequences

**Positive**

- Winners are given room to become the outsized winners the method depends on.
- The 7% early stop matches the documented maximum-loss discipline instead of
  converting normal volatility into realised losses.
- A large winner is now structurally *possible*, which it previously was not.
- The watchlist is dominated by genuine growth names rather than rate-driven
  financials.
- Every threshold is env-tunable, so this is reversible without a deploy.

**Negative / accepted risks**

- Individual losses will be larger (up to 7% rather than 2%). This is the
  intended trade: the payoff ratio matters more than the size of any single loss.
- Fewer trades. Accepted, and desirable while expectancy is negative.
- Positions will be held far longer, so drawdowns will look worse intraday.
- The `power_hold` migration must be applied for the flag to persist across
  restarts; until then the rule is evaluated per-cycle only.

**Not validated by simulation.** A counterfactual backtest was attempted but
could not be run: Stooq now sits behind JS bot-protection and the local network
has a TLS-intercepting proxy that breaks Python HTTPS clients. These changes
rest on code analysis plus the arithmetic of the 13 real trades. Thirteen trades
is a small sample — the *direction* of each defect is unambiguous from the code,
but the effect sizes are uncertain and the new thresholds should be reviewed
against live results after a meaningful number of trades.

## Follow-up

1. Apply `migrations/add_power_hold.sql`.
2. Re-measure win rate, average win, average loss and payoff ratio after ~20
   further closed trades.
3. If the payoff ratio clears 1.5:1, consider raising `MIN_REVENUE_GROWTH`
   toward the textbook 25.
