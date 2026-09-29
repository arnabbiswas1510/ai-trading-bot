# Backtests model commission and slippage (fidelity item #3)

- **Date:** 2026-09-29
- **Status:** Accepted
- **Roadmap:** backtest-fidelity gap #3 (costs/slippage), the third of six gaps.
  Follows Option A (`decisions/2026-09-29_backtester-option-a-live-exits.md`),
  which put the live exit engine in both backtesters.

## Context

Neither strategy backtester modelled trading costs. Every profitability figure
they produced was therefore an **upper bound**: it banked the full quote-to-quote
move and paid nothing to enter or exit. `docs/backtesting.md` already conceded the
tight-trail results were "an upper bound" for exactly this reason.

This matters because the measured per-trade edge is thin. The 52-trade exit-review
sample averages **−0.16%/trade** gross. An unmodelled ~0.65¢/share commission plus
a few basis points of slippage is not a rounding error against an edge that small
— it can be the entire difference between a strategy that is +EV and one that is
−EV. A backtest that cannot see costs cannot answer the only question that matters
before sizing more money into the strategy.

`backend/commissions.py` already exists, but it derives net P&L from **recorded**
per-leg IBKR fills. It has no per-share *rate* to apply to a hypothetical fill, so
it cannot cost a backtest trade that never happened. A separate, assumption-based
cost model was needed.

## Decision

1. **New root module `trade_costs.py`.** A frozen `CostModel` dataclass with
   `commission(shares, price)`, `buy_fill(quote)`, `sell_fill(quote)` and a
   `slippage_frac` property, plus `build_cost_model()`. Defaults: `$0.0035`/share
   commission (IBKR tiered), `$0.35` per-order floor, `5` bps adverse slippage per
   fill. All three are env-overridable (`BACKTEST_COMMISSION_PER_SHARE`,
   `BACKTEST_COMMISSION_MIN`, `BACKTEST_SLIPPAGE_BPS`). Import-safe: env reads
   only, no third-party imports — so it can be pulled into the web image next to
   `daily_exit_sim.py`.

2. **Wire it into both strategy backtesters identically** —
   `research/strategy_backtest.py` `simulate()` and `backend/backtester.py`
   `run_backtest()`. On a buy, size on the **slipped** fill (so a buy never
   overspends its allocation), then `cash -= shares × buy_fill + commission`. On a
   sell (full or partial scale-out), `cash += shares × sell_fill − commission`.
   Accumulate `total_commission` and `total_slippage` globally.

3. **Costs are charged to CASH only, never to the exit math.** The position's
   `buy_price` stays the **unslipped OPEN quote**, so every Prove-It level, trail
   ladder rung and hard stop is computed off the same number with costs on or off.
   Consequence, and the invariant the tests pin: **enabling costs never changes
   which exit fires** — the exit-reason counts are byte-identical. Costs move
   dollars, not decisions.

4. **Per-trade `profit_loss` stays GROSS; equity is NET.** This keeps the
   `backend/commissions.py` doctrine: `trade_history.profit_loss` is gross so it
   stays comparable with every threshold in `decisions/` and with
   `research/exit_rule_replay.py`, all of which are gross. Each trade record now
   also carries `commission`, `slippage_cost` and `net_profit_loss`; the summaries
   add `total_commission`, `total_slippage`, `total_trading_costs` and `net_pnl`
   alongside the gross figures. The equity curve — and therefore CAGR, expectancy
   and final equity — is net, because the cash it is built from was charged.

5. **`research/exit_rule_replay.py` is deliberately left gross.** It is the tool
   that ranks exit *parameters* against `decisions/`, all measured gross; adding
   costs there would break that comparability for no gain.

## Consequences

- Backtest profitability is now net of an explicit, reported cost assumption
  instead of silently costless. Over 2023-08→2024-08 on the full offline universe,
  default costs total ≈$1,669 and turn a −$3,179 gross-equivalent into −$4,809 net
  — i.e. costs are ~30% of the loss magnitude on that window, which is exactly the
  order of magnitude that makes item #3 non-optional.
- The defaults are **assumptions, not measurements**, and are flagged as such in
  the module docstring, the docs and this ADR. Real slippage on a market-open
  breakout entry is unknown without live fill data; the register carries this as a
  provisional parameter to revisit once such data exists.
- `Dockerfile` now COPYs `trade_costs.py` into the web image;
  `tests/test_web_image_completeness.py` enforces it (it failed until the COPY was
  added, which is the mechanism working as designed).

## Alternatives considered

- **Reuse `backend/commissions.py`.** Rejected: it reconstructs net from recorded
  fills and has no rate to apply to a simulated trade.
- **Subtract costs from per-trade P&L.** Rejected: it would make the backtester's
  per-trade numbers incomparable with the gross `decisions/` thresholds and the
  exit replay. Charging cash preserves gross-comparability while still making the
  bottom line net.
- **Let costs perturb the exit levels (slip the stop too).** Rejected: it would
  couple cost assumptions to strategy behaviour, so a cost sweep would silently
  become a strategy sweep. Costs must move money, not decisions.
