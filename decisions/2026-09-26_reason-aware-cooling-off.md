# Reason-aware cooling-off: loss exits block, profit exits don't

**Date:** 2026-09-26
**Status:** Accepted
**Supersedes in part:** [`2026-09-15_cooling-off-three-days.md`](2026-09-15_cooling-off-three-days.md)
(the 3-day length stands; the *blanket, reason-blind* application does not)
**Builds on:** [`2026-09-10_lot-basis-and-broker-aware-cooling-off.md`](2026-09-10_lot-basis-and-broker-aware-cooling-off.md),
[`2026-09-26_cooling-off-return-neutral.md`](2026-09-26_cooling-off-return-neutral.md)

## Context

The cooling-off gate blocked re-entry into ANY ticker sold within
`COOLING_OFF_DAYS` (3), regardless of *why* it was sold. Two findings made that
blanket rule wrong:

1. **It idled capital on proven winners.** On Friday 2026-09-25 the book was
   flat, all five slots free, market gate BULL — and **no trade was made**. Four
   of the six triggers (CDNA, HPE, TWLO, AMD) were blocked purely because they
   had been sold *at a profit* the day before. The gate treated a successful
   profit-take identically to a stop-out.

2. **The blanket rule earns nothing.**
   [`2026-09-26_cooling-off-return-neutral.md`](2026-09-26_cooling-off-return-neutral.md)
   measured it as return-neutral at best and −2.2pp CAGR at worst. Its only real
   job is **data integrity** — stopping the same-session re-buy that corrupts the
   IBKR `averageCost` basis (the NTRA 6-minute churn of 2026-08-31).

The question this ADR answers: can the buy/sell rules keep the bot profitable
without a blanket time-block — i.e. block only the re-entries that actually lose
money, and let the buy-quality gates judge the rest?

## Evidence

### Live re-entries, split by why the PRIOR sale happened (66 closed trades)

| Prior sale was… | n | net re-entry P&L | win rate |
|---|---|---|---|
| **a LOSS (falling / stop-out)** | 10 | **−$1,750** | 3/10 (30%) |
| **a PROFIT (take-profit / trail)** | 15 | **+$61** | 8/15 (53%) |

Re-buying a name sold *because it was falling* loses money and wins 30% of the
time — this is the catch-a-knife case cooling-off exists for. Re-buying a name
sold *at a profit* is a coin flip (+$61, 53%): not a money-maker, but not the
systematic loser the blanket rule assumed. Only **2** of the 25 re-entries fell
inside the 3-day blocked window at all, and **both were the same-day NTRA churn**
— so on realised history the blanket calendar block bit almost nothing except the
churn a same-session guard already stops.

### Portfolio backtest (`research/cooloff_bt.py`, 5 slots, 3.09 years)

The harness now models the reason-aware split (`cool_reason_aware=True` in
`port_sim.simulate`): a LOSS exit blocks for `cool` days, a PROFIT exit blocks
only the same session.

**Screener-pass universe (79 names — closest to the live candidate pool):**

| Rule at 3 days | CAGR | vs blanket-3 |
|---|---|---|
| No cooling-off | +29.5% | — |
| **Blanket block (old)** | +27.3% | — |
| **Reason-aware (new, LIVE)** | **+29.0%** | **+1.7pp** |

**Broad universe (256 names):** reason-aware = blanket = no-cooling, all +26.9%
(0.0pp). Where substitutes are plentiful, exempting profit exits neither helps
nor hurts.

The reason-aware rule is therefore **dominant**: never worse than the blanket
block, and materially better (+1.7pp CAGR over 3 years) on the shallow universe
that resembles the bot's real bench — while still keeping the loss-exit knife
guard and the same-session basis guard.

> **CAVEAT:** `port_sim` uses the PRE-Prove-It exit stack, so the RANKING is a
> fair read but the absolute CAGR is not the live bot's. The live-trade split is
> 25 re-entries, all inside a BULL regime, and the profit-exemption's upside is
> partly *unmeasured* because the old rule blocked those cases before they became
> trades.

## Decision

Make cooling-off **reason-aware**, with two separable jobs in
`cooling_off.compute_cooled_map()` (single source, shared by
`execution_agent.run_market_open_buys`, `force_buy.py` and `rotate_positions.py`):

- **(A) Same-session churn guard — UNCONDITIONAL.** Any name sold *today* (NY
  calendar day), profit or loss, is blocked. This protects the IBKR
  `averageCost` basis and is non-negotiable. Checked against BOTH `trade_history`
  and `ibkr_fills` SLD rows, so a broker-side exit the ledger missed still fires
  it.
- **(B) Calendar block — LOSS exits only.** A name whose most-recent sale inside
  the `COOLING_OFF_DAYS` window was a loss (realised P&L ≤ 0, or unknown) is
  blocked for the full window. A name sold at a **profit** older than today is
  **not** blocked — the buy-quality gates (extension, breakout quality, RS)
  decide whether the fresh trigger is clean.

`COOLING_OFF_DAYS` stays **3**. Only its *application* changed.

### Conservative edges

- **Unknown P&L → treated as a loss.** A sale with no recorded P&L blocks; a
  missing figure must never open a free re-entry.
- **An `ibkr_fills` SLD in the window with no ledger row → blocked.** The reason
  is unknown (most likely a resting stop that fired = a loss), so it is blocked
  conservatively — the belt-and-suspenders behaviour of the 2026-09-10 ADR is
  preserved.
- **Most-recent sale governs.** A profit sale yesterday overrides an older loss
  sale; the name's latest state is what matters.

## Consequences

- **Behaviour change:** a name sold at a profit is now eligible for re-entry the
  next session instead of being idled for 3 days. Friday-09-25-type zero-trade
  days caused by profit-sells no longer happen.
- **No change** to same-session protection, to the loss-exit block, or to
  `COOLING_OFF_DAYS = 3`.
- New module `cooling_off.py` centralises the logic; `execution_agent.py`,
  `force_buy.py` and `rotate_positions.py` all call it (removes three divergent
  copies of the query). Added to `Dockerfile.agent`'s COPY set.
- `port_sim.simulate` gains a `cool_reason_aware` flag; `cooloff_bt.py` prints
  the reason-aware sweep beside the blanket one.
- Tests: `tests/test_buy_gates.py::TestReasonAwareCoolingOff` (5 cases) covers
  loss-blocks, profit-allows, profit-today-still-blocks, most-recent-governs,
  and none-pnl-is-loss. The conftest Supabase mock gained a real query builder
  (`_RowQuery`) so the windowed `trade_history` query is exercised for real.

## Open questions

1. **Sample is 25 re-entries, all BULL.** Whether a profit-sold name is safe to
   re-buy next session in a corrective tape is untested.
2. **The profit-exemption's upside is partly unmeasured** — the old rule blocked
   those cases, so we have no realised P&L for same-week profit re-entries; the
   ≥4-day profit re-entries (+$61, break-even) are the best available proxy.
3. **P&L-sign is a proxy for "why".** A rotation-out (Rank & Replace) at a small
   loss is classed as a loss and blocked; a trailing-stop exit at a small profit
   is classed as a profit and allowed. This matches intent but was not separately
   swept.

Registered in `decisions/provisional_decisions.json` (`reason-aware-cooling-off`)
for re-measurement as more re-entries — and the first non-BULL regime — accrue.
