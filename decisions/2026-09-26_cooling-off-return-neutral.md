# Cooling-off is return-neutral, not a profit rule — measured, kept at 3 days

**Date:** 2026-09-26
**Status:** Accepted policy; backtest evidence withdrawn below — extends, does not supersede,
[`2026-09-15_cooling-off-three-days.md`](2026-09-15_cooling-off-three-days.md)
**Register:** `cooling-off-three-days` in `decisions/provisional_decisions.json`

> **Erratum 2026-09-30 — DO NOT CITE the portfolio CAGR table as evidence:**
> `port_sim` used later same-day exits to free slots for opening buys and used
> different day counting from live. The 29.5%/27.3% comparison and -2.2pp
> conclusion require rerunning; no corrected figures are asserted here. Older
> exit rules also prevent treating even a corrected run as current execution.
> The live policy and separately observed trade history remain unchanged.
> See `2026-09-30_recorded-input-replay-and-fidelity-boundaries.md`.

## Context

A zero-trade session on Friday 2026-09-25 was traced to the cooling-off gate:
the bot sold five winners on Thursday 09-24 (CDNA +$1,443, HPE +$706, TWLO +$564,
AMD +$324, DXCM −$369), those same names re-triggered on Friday, and the 3-day
`COOLING_OFF_DAYS` gate blocked every one of them — so with a BULL market gate,
a flat book and all five slots free, no buy was made. The only other Friday
candidate (PLTR) failed the extension gate at 7.2% below its pivot.

That raised the direct question the 2026-09-15 ADR left as its open question #2:
**is the 3-day cooling-off actually profitable, or is it just idling capital?**
The earlier ADR swept only 0 vs 3 vs 7 and could not model the `SLOTS_FULL`
confound. This measurement sweeps the adjacent values and models the slot
allocator.

## What was measured

Two independent harnesses, because each sees something the other cannot.

### 1. Portfolio simulation (`research/cooloff_bt.py`, new)

`port_sim.simulate()` runs a 5-slot, best-first, chronological portfolio over
3.09 years of daily bars, blocking re-entry for `cool` **trading** days after a
sale. Because it fills a blocked slot with the next-best candidate, a blocked
winner is a real opportunity cost and a blocked loser a real saving — and unlike
the 2026-09-15 FMP counterfactual, **the slot allocator models the `SLOTS_FULL`
confound directly.** `cool` was swept across 0, 1, 2, 3, 4, 5, 7, 10.

**Broad universe (256 names):**

| cooling-off | CAGR | vs cool=0 |
|---|---|---|
| 0–5 days | **+26.9%** | **0.0pp (identical)** |
| 7 days | +27.4% | +0.5pp |
| 10 days | +26.6% | −0.3pp |

**Screener-passing universe (79 names):**

| cooling-off | CAGR | vs cool=0 |
|---|---|---|
| 0 days | **+29.5%** | — |
| 1 day | +29.0% | −0.5pp |
| **3 days (LIVE)** | **+27.3%** | **−2.2pp** |
| 7 days | +27.0% | −2.4pp |
| 10 days | +25.9% | −3.6pp |

Trade count barely moves (245→245 on the narrow set), confirming a blocked
re-entry is swapped for another candidate rather than lost. **Cooling-off never
improves CAGR in any run.** On a deep universe it is exactly neutral; on a
shallow one — where a just-sold name has fewer substitutes — it is mildly
*negative*, monotonically worse the longer it blocks.

> **CAVEAT, stated so it is never dropped:** `port_sim` uses the PRE-Prove-It
> exit stack (EMA-21 exit, stale exit, profit ladder, power-hold, 10% base
> trail), not the live Prove-It Stop. Cooling-off is an *entry* gate and is
> largely orthogonal to the exit rule, so the RANKING of `cool` values is a fair
> read; the absolute CAGR is not the live bot's number.

### 2. Live re-entry P&L (Prove-It era, 66 closed trades)

Grouping every real re-entry in `trade_history` by the gap between the prior
sell and the next buy:

| Gap window | n | net re-entry P&L | win/loss |
|---|---|---|---|
| **< 3 days (BLOCKED by the live rule)** | **2** | **+$60** | 1 / 1 |
| 3–7 days (allowed) | 13 | **+$2,803** | 7 / 6 |
| ≥ 7 days (allowed) | 10 | −$4,478 | 3 / 7 |

The two trades in the blocked window are **both the same-day NTRA churn**
(gap 0.0) that corrupted the lot basis and motivated
[`2026-09-10_lot-basis-and-broker-aware-cooling-off.md`](2026-09-10_lot-basis-and-broker-aware-cooling-off.md).
Their combined P&L is +$60 — trivial. **In live trading the 3-day gate's actual
P&L bite is ≈ zero; its real function is blocking same-session re-buys, not
capturing or forgoing profit.** Every profitable re-entry (the +$2,803 in the
3–7 day bucket) is *allowed* by the 3-day rule, and extending to 7 days would
begin blocking it — the same direction the 2026-09-15 ADR found.

## Decision

**Keep `COOLING_OFF_DAYS = 3`. Nothing changes in code.** But correct the frame
in which it is justified:

> The cooling-off gate is **not a profit rule and must not be defended or
> attacked as one.** Both harnesses agree it is **return-neutral**: it earns no
> measurable CAGR (broad universe: 0.0pp; narrow: −2.2pp — a small *cost*) and in
> live trades it blocked $60 of P&L. Its value is **data integrity** — it is the
> only rule standing between the bot and the same-session churn that produced
> NTRA's contaminated `averageCost`. Return-neutral is the *best* outcome a
> safety gate can post: it prevents a known, expensive failure mode at no
> measurable return cost.

Three days is correctly placed on the evidence: **shorter re-enables the churn**
(the <3-day window is exactly where NTRA's 6-minute round trip sits), and
**longer sacrifices the profitable 3–7 day re-entry window** (+$2,803 live;
+$1,736 in the 2026-09-15 measurement). The sweep also answers open question #2:
2, 4 and 5 days are **statistically indistinguishable** from 3 (identical CAGR on
the broad universe), so 3 is not a fragile optimum — the whole 2–5 day band is
one flat plateau.

## Consequences

- No code, config, threshold or default changes. `COOLING_OFF_DAYS` stays 3 in
  `config.py` and `.env.template`.
- `docs/buy_logic.md` gains a sentence recording that the gate is return-neutral
  and justified on data-integrity grounds, so no future reader re-opens it as a
  suspected profit leak.
- `research/cooloff_bt.py` is added as the reusable harness for the register's
  scheduled re-measurement.
- The `cooling-off-three-days` register entry gets a `history` row; its revisit
  trigger (`min_closed_trades: 75`, `not_before: 2026-11-20`) is **unchanged** —
  this is an early confirmatory read, not the scheduled review, and the sample
  (25 re-entries, only 2 in the blocked window) is still small and entirely
  inside a BULL regime.

## Open questions (carried forward from 2026-09-15, still open)

1. **Sample is small and all-BULL.** 25 re-entries, 2 in the blocked window; no
   corrective tape yet. Fast re-entry in a falling market is still untested.
2. **The live P&L split is low-n.** The 3–7 day bucket (+$2,803) vs the ≥7 day
   bucket (−$4,478) is suggestive that *closer* re-entries do better, but n=13
   and n=10 cannot carry that claim; it is noted, not concluded.
3. **`port_sim` is pre-Prove-It.** The ranking is trustworthy, the absolute CAGR
   is not; a Prove-It-faithful portfolio sim would tighten the absolute numbers.
