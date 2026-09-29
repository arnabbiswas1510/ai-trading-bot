# Backtesting

There are **two different kinds of backtest** in this repo, and they answer
different questions. Picking the wrong one wastes time.

| You want to know | Use |
|---|---|
| "How would the strategy have performed, with the **live exit rules**, over a period?" | **Strategy backtest (exit-parity)** (`research/strategy_backtest.py`) |
| "How does the **dashboard** backtester score these tickers?" (⚠ still retired exits) | **Web strategy backtester** (`backend/backtester.py`) |
| "Would a different *exit rule* have made my **actual** trades better?" | **Exit replay** (`research/exit_rule_replay.py`) |
| "Does an entry/ranking/exit idea hold up across a large universe?" | **Research harnesses** (`research/*_bt.py`) |

> **Which strategy backtester?** `research/strategy_backtest.py` calls the **live**
> `exit_core` / `exit_rules` code, so its exits are the Prove-It Stop, the dynamic
> ladder, power-hold and scale-out — byte-for-byte what production runs.
> `backend/backtester.py` (the dashboard button) still models the **retired**
> 7%-trail + EMA-21 rules and is being kept only until it is repointed at the same
> engine (Option A — see `decisions/2026-09-29_backtester-exit-core-adoption.md`).
> Trust the research tool for any exit-behaviour question.

---

## 0. Strategy backtest with the LIVE exit rules (exit-parity)

`research/strategy_backtest.py` is the backtest-fidelity Phase 2 deliverable: a
full-portfolio daily-bar simulation whose **exits are the live code**. It imports
`exit_core` and `exit_rules` and calls them, so the Prove-It Phase 1 band, the
Phase 2 give-back floor, the trailing ladder (off the live `TRAIL_PROFIT_TIERS`),
the power-hold widening and the partial scale-out are the live rules by
construction — change a threshold in `exit_rules.py` and this backtest changes
with it.

```bash
python3 research/strategy_backtest.py                     # headline + exit histogram
python3 research/strategy_backtest.py --start 2024-01-01 --end 2026-06-30
python3 research/strategy_backtest.py --universe pass      # research/pass_names.txt only
python3 research/strategy_backtest.py --json out.json
```

No secrets required — it reads the committed `benchmark_data/` daily bars (313
names, 2023-07 → 2026-08), so it runs offline, free and reproducible with **no
FMP key at all**. Entries mirror `backend/backtester.py` (20-day-high breakout,
above SMA50/200, ≥1.4× volume, SPY-above-EMA21 filter) so the two agree on
entries; entry parity against `decision_core` is a separate follow-up.

### Fidelity — read before trusting a dollar figure

This achieves **rule parity** (which exit fires, and why) but **not exact
fill-price fidelity**. Daily bars cannot see the live loss rules' 0.6% arm-trail
bounce (they `arm_exit()` a tight IBKR trail that resolves *intraday*), the
15-minute poll, or slippage. A Prove-It arm is therefore modelled as a sell at
the level (or the open on a gap-through, pessimistically), and trail-tightening /
scale-out resolve off the intraday high / at the close. To avoid look-ahead,
resting levels for a day use the peak/HWM as of the previous close, and today's
high is folded in only after the low is resolved.

**Trust it for RELATIVE questions** — does a rule fire, how often, does a change
help or hurt. **Do not** read its absolute P&L as a precise +EV/−EV verdict on
the tight Prove-It exits; that needs 5-minute bars, which drop in with no logic
change (see `resolve_position_day`). The register work-item
`intraday-fmp-exit-fidelity` tracks that upgrade, gated on live usage. See
`decisions/2026-09-29_backtester-exit-core-adoption.md`.

---

## 1. Web strategy backtester (dashboard — ⚠ retired exits)

> **This still models the retired 7%-trail-from-peak + EMA-21×0.99 rules and does
> NOT import `exit_rules`.** Its exits are not what production runs. Use it for the
> dashboard/API convenience view only; for any exit-behaviour question use
> `research/strategy_backtest.py` above. It will be repointed at the live
> `exit_core` engine in a later change (Option A).

Simulates the full CAN SLIM breakout strategy over historical FMP data.
Entries are detected on day T's close and filled at day T+1's **open** (no
look-ahead). Sizing is `min(available_cash / remaining_slots, equity / MAX_POSITIONS)`,
matching the live bot — the second term caps each position at one equal-weight share of
equity (see `decisions/2026-09-21_equity-capped-position-size.md`).

### From the dashboard

Open the app (`http://192.168.1.2:8000`) → **Backtester** tab → set the date
range, capital, stop-loss % and slot count → run. Leaving the ticker list empty
uses the current watchlist.

### From the API

```bash
curl -X POST http://192.168.1.2:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{
        "tickers": ["AAPL","MSFT","NVDA"],
        "start_date": "2025-01-01",
        "end_date":   "2025-12-31",
        "initial_capital": 100000,
        "stop_loss_pct": 7,
        "max_positions": 5
      }'
```

`tickers` is optional — omit it to use the watchlist. `profit_target_pct` is
accepted for frontend compatibility but **ignored**: the live bot has no fixed
profit target.

Returns `summary`, `trades` and `equity_curve`. The summary includes CAGR,
max drawdown, Sharpe/Sortino/Calmar, win rate, expectancy, average hold days,
a breakdown of exit reasons, and `alpha_pct` against the S&P 500.

### From Python

```python
from backend.backtester import run_backtest
result = run_backtest(
    tickers=["AAPL", "MSFT"],
    start_date_str="2025-01-01",
    end_date_str="2025-12-31",
    initial_capital=100_000,
    stop_loss_pct=7.0,
    max_positions=5,
)
```

Requires a configured FMP API key (dashboard Settings, or `FMP_API_KEY`).

### Known divergence from live — read before trusting a result

The backtester's market filter is **more permissive than production**. It gates
on SPY `close > EMA-21`. The live bot uses an SMA-200 regime rule: every benchmark
in `MARKET_DIRECTION_TICKERS` (SPY) more than
`MARKET_DIRECTION_BUFFER_PCT` (0.5%) above its SMA-200, with at least one non-falling
SMA-200, failing closed. A backtest will therefore take trades the live bot would
have skipped. See `decisions/2026-09-28_market-gate-spy-only-tighter-band.md`.

`max_positions` defaults to the `MAX_POSITIONS` env var so a backtest cannot
silently simulate a different portfolio shape than production.

---

## 2. Exit replay — against your own real trades

This is the highest-signal tool in the repo, because it uses **real fills, not
simulated entries**. It replays the bot's own closed trades on 5-minute bars,
reproducing live mechanics (15-minute checks, `arm_exit()` 0.6% trail, the 3.25h
deadline) and reports every alternative as a dollar delta against the exit that
actually happened.

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
python3 research/exit_rule_replay.py --insecure          # headline comparison
python3 research/exit_rule_replay.py --insecure --grid   # full parameter sweep
python3 research/exit_rule_replay.py --insecure --ladder # profit-lock give-back sweep
python3 research/exit_rule_replay.py --insecure --eod    # EOD give-back vs intraday trail
python3 research/exit_rule_replay.py --insecure --json out.json
```

Drop `--insecure` if the local TLS trust store is working. Other flags:
`--proveit`, `--day0`, `--top N` (default 25).

`--proveit` sweeps the Prove-It parameters and, since the 2026-09-18 repair,
contains the live configuration itself. Its grid is **38 rows**, so pass
`--top 80`; the three baseline rows (`LIVE BASELINE`, `ProveIt SHIPPED`,
`RETIRED pre-ProveIt`) rank below the default cut and are otherwise invisible.
Note that `RETIRED pre-ProveIt` was labelled `SHIPPED` until that date despite
modelling rules retired on 2026-09-04 — **any "beats shipped by $N" claim taken
from a `--proveit` run before 2026-09-18 is void.** See
`decisions/2026-09-18_exit-review-52-trades-and-proveit-sweep-repair.md`.

`--ladder` sweeps the Phase 2 profit-lock give-back — the trail width in
`TRAIL_PROFIT_TIERS`, shipped at **1.5% from the high-water mark once a position
is up +5%** — and crosses it against the gain at which it arms. Every row holds
the rest of the Prove-It Stop fixed, so differences are attributable to that one
number.

Interpret it with the ladder's **engagement rate**, not the trade count. The
rung only applies to positions that have closed above entry *and* peaked at
`p2_ladder_gain`; on the 30 closed trades available on 2026-09-06 that was only
10 trades, and just 9 exited differently across the whole 0.5%–3.0% range. The
sweep therefore describes those trades rather than estimating a parameter.

Two mechanical caveats apply specifically to tight settings:

- The replay fills **exactly at the stop level with no slippage**. Real fills on
  a trail inside the spread are worse, so tight rows are an upper bound.
- Below roughly a third of a stock's own 5-minute range the level stops acting
  as a give-back cap and starts acting as "sell at the first pullback" — the
  failure `OCA_EXIT_MIN_TRAIL_PCT` guards against on the OCA path.

`--eod` answers a different question: should the give-back be checked *once a
day on the close* instead of continuously? A close-based test ignores wicks, so
a much tighter band is arguable. The sweep crosses the band (0.5%–2.0%) with the
anchor (highest close vs highest intraday high) and with the presence of a wider
intraday crash backstop.

On the 30 closed trades available on 2026-09-06 **every EOD variant lost to the
shipped intraday 1.5% trail**, the best by $1,647 and a 0.5% close-anchored band
by $2,104. Against the intraday rule head-to-head, 7 of 7 affected trades were
worse and none better. Two mechanisms, both visible in the data:

- **It fires far too often.** The median session of these names closes 0.83%
  below its own high, and 65% close more than 0.5% below it. A 0.5% EOD band is
  therefore triggered by an ordinary session, so it exits winners almost as soon
  as the rung arms rather than letting the peak keep rising.
- **The fill is unbounded.** An intraday trail fills *at* peak × (1 − band). An
  EOD rule fills at whatever the close happens to be, which is a mean 1.17%
  below the high and has a long tail. It caps when you *look*, not what you
  *give back* — so on the worst days it is the looser rule despite the tighter
  number.

Removing the intraday backstop cost a further ~$527 on losers, so an EOD-only
give-back is also strictly worse on risk.

Read the results with the four questions in `AGENTS.md` — report `n` first,
check whether the shipped config still wins, check whether any result is carried
by a single trade, and check `winners_hurt`.

> **Sample-size caution.** Every shipped exit threshold was tuned on ~17 closed
> trades. Below ~30, differences are noise. `AGENTS.md` carries a review schedule
> for re-running this as trades accumulate.

---

## 3. Research harnesses

`research/*_bt.py` are standalone studies backing specific ADRs — entry
selection (`entry_bt.py`), breakout population (`breakout_bt.py`), portfolio
slot dynamics (`port_sim.py`), O'Neil's profit target (`oneil_full_system_bt.py`),
ATR ranking, latching, thesis stops, and counterfactuals.

They read the committed offline dataset in `benchmark_data/` — 313 US equities,
daily OHLCV+VWAP, 2023-07-03 → 2026-08-04, 4.39 MB — so they run **fast, free,
reproducible, and without FMP rate limiting**. Run them directly:

```bash
python3 research/breakout_bt.py
python3 research/port_sim.py
```

`research/fetch_daily.py` refreshes that dataset from FMP; it is only needed when
extending the window or universe.

---

## Caveats that apply to all of them

- **Commissions and slippage are not modelled.** Live cost is ~0.65¢/share
  (≈1.09 bps). Realised P&L in `trade_history` is stored **gross** for exactly
  this reason — every threshold in `decisions/` was measured gross. See
  `decisions/2026-09-06_commission-accounting.md`.
- **Survivorship**: the watchlist and `benchmark_data` universe are built from
  today's tickers.
- **A backtest cannot see execution pathologies.** It assumes one clean entry and
  one clean exit per position, so it will not reproduce same-day re-entry churn
  or unrecorded stop-outs. Reconcile against IBKR for those.
