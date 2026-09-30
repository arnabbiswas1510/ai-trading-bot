# Slot opportunity cost: pricing "let winners run" against the entries a longer hold blocks

Date: 2026-09-22
Status: Accepted historical tooling; current-live comparisons under erratum

> **Erratum 2026-09-30:** the capacity model fixes the historical entry stream
> and its stop-only configurations omit partial scale-out and current Phase 1
> broker protection. Do not cite its deltas as the cost of the live five-slot
> cap or a complete live-stack comparison. The historical model remains useful
> within those limits; no corrected live-strategy figures are asserted here.
> See `2026-09-30_recorded-input-replay-and-fidelity-boundaries.md`.

## Context

The goal driving the current work is to let winners run longer without loosening
the loss-cutting that is already working. The measurement tool for that question
is `research/exit_rule_replay.py`, and until now it had a hole that its own
`--runon` ADR named and left open.

`decisions/2026-09-18_runon-window-winners-run.md` fixed the first half of the
problem: the replay used to truncate each trade's price history at the *realised*
exit, so any rule that would have held **longer** ran out of bars exactly when
the live rule sold and was silently handed the live exit price for free. With a
run-on window the loosening direction could finally be scored, and the sign
reversed — a 5%/8% Phase-2 ladder beat the shipped 1.5% by roughly +$5,700.

That ADR then refused to ship the change, for one dominant reason quoted verbatim
from it:

> **Slot opportunity cost is entirely unmodelled, and it is the decisive term.**
> `MAX_POSITIONS` is 5. Every run-on figure assumes a position can be held 30
> extra days *for free*, when in reality it occupies one of five slots and blocks
> the next breakout. … **No ladder change should ship until it can.**

This ADR supplies that term. It does not change any live parameter.

## The model

`--slotcost` re-scores the same `runon_configs()` loosening sweep, but instead of
summing per-trade deltas in isolation it runs a **capacity-constrained portfolio
simulation** over the real entry stream.

1. **Merge scale-out partials into parent positions** (`merge_positions`).
   `trade_history` records a scaled-out position as several rows that share an
   entry and sell at different times (NTRA RT1/RT2/RT3, TRV's same-day partial +
   remainder). Left separate they inflate concurrency — two partials of one
   position read as two occupied slots — and the entire model turns on
   concurrency being counted correctly. A slot is keyed by **ticker**, so
   same-ticker rows whose `[buy_ts, sell_ts]` intervals overlap are one position;
   sequential re-entries (sell #1 strictly before buy #2) do not overlap and stay
   separate. The parent's shares are summed, entry/exit are share-weighted, and
   the representative used for replay is the partial with the **widest bar
   window** (the one that sold last, so its run-on reaches furthest).

2. **Validate concurrency ≤ MAX_POSITIONS** (`slot_concurrency`). After merging,
   the peak simultaneously-held count MUST be ≤ 5, because the live book never
   held more than five positions. This is the model's correctness gate: a peak
   above 5 means the merge is wrong or the data is anomalous, and the report says
   so loudly rather than producing a confidently-wrong slot charge. On the live
   data it comes out **exactly 5**.

3. **Greedy 5-slot portfolio walk** (`_run_portfolio`). Positions are processed in
   the order the bot actually bought them. At each entry, slots whose exit has
   passed are freed; if a slot is free the position is **taken**, otherwise it is
   **blocked** and contributes nothing. This mirrors how the live agent operates
   (buy when a trigger fires and a slot is open). It is deliberately greedy and
   entry-ordered, not a global optimiser — that avoids a fragile combinatorial
   cascade and matches the real mechanism.

4. **Two runs, one delta.**
   - *Baseline*: real exits, every position taken. Because the real book never
     exceeded the cap, this reproduces the realised total and anchors the A/B.
   - *Counterfactual*: `cfg` exits under the 5-slot constraint. Holding winners
     longer keeps slots busy and can block later real entries.

   ```
   slot_net  = counterfactual_total − baseline_total   (the honest value)
   naive_net = Σ per-position deltas                    (slots assumed free)
   slot_cost = naive_net − slot_net                     (net cf P&L of blocked entries)
   ```

   `slot_cost` may be **negative**: if the blocked entries were net losers,
   blocking them avoided losses and `slot_net` exceeds `naive_net`. That is not a
   bug — it is the shipped rule's result below.

### Exit-timestamp plumbing

The slot model needs to know *when* each slot frees, which the simulators did not
report. Every `return` in `simulate`, `simulate_proveit` and `simulate_scaleout`
now carries an additional `"ts"` key alongside `"price"` and `"reason"`. The
change is purely additive: `score()` and `report()` read only `price`/`reason`,
so every other mode's numbers are unchanged. For a scaled-out position the slot
frees when the **remainder** exits (the scale leg is a partial), so the blended
return propagates the remainder's timestamp.

## The first result (n = 59 positions, run-on 30 days)

Peak concurrency **5** — the gate passes. Realised baseline total **−$8,389.57**.

| configuration | naive_net | slot_cost | **slot_net** | blocked |
|---|---|---|---|---|
| RunOn: P2 ladder trail 5% | +$28,038 | +$5,761 | **+$22,277** | 28 |
| **SHIPPED (ladder 1.5%)** | +$20,034 | −$173 | **+$20,207** | 12 |
| RunOn: P2 ladder trail 3% | +$19,434 | −$87 | +$19,521 | 17 |
| RunOn: P2 ladder trail 2% | +$18,761 | +$1,130 | +$17,631 | 12 |
| RunOn: CEILING (never sell) | +$16,242 | +$1,040 | +$15,202 | 43 |
| RunOn: ladder trail 8% | +$19,511 | +$6,624 | +$12,886 | 34 |

Reading this honestly:

- **The slot charge is real and it re-ranks the sweep.** Ladder 8% loses more
  than half its naive gain to blocked winners ($6,624) and falls below shipped.
  The naive `--runon` table had 8% *above* shipped; with slots charged it is
  well below. This is exactly the correction the model exists to make.

- **Ladder 5% still leads after the charge (+$22,277 vs shipped +$20,207)** — a
  ~$2,070 edge that survives paying $5,761 of slot cost. But this is **not** a
  ship signal, for three reasons that all still stand:
  1. **It is still outlier-carried.** The naive +$28,038 is the same ECO-heavy
     distribution the run-on ADR flagged (ECO was 67% of the 5% net there). The
     slot view does not dissolve that concentration; run
     `--runon --detail "ladder trail 5%"` alongside this to see it. Per the
     standing rule, a configuration that wins on one trade has not won.
  2. **The blocked-entry model conflates a continued hold with a re-entry.** When
     the counterfactual holds ECO's 09-02 entry for 30 extra days it blocks the
     *real* ECO re-entries on 09-15 and 09-21. Treating those as "blocked" is
     defensible (you are still in the name) but it is a first-order
     approximation, not the truth.
  3. **One regime, small sample.** All 59 positions fall in a single six-week
     window that favours holding. "Hold longer" benefits most from a rising tape.

- **The shipped rule's slot_cost is −$173**: holding shipped-Prove-It slightly
  longer than the real (rotation-and-trail) exits blocks 12 entries that were net
  losers, so the block marginally *helps*. That the shipped rule barely disturbs
  the slot structure is a point in its favour.

## Decision

Add `--slotcost` as a first-class mode of the exit replay. Make **no** live
parameter change on the strength of this first run. The deliverable is the
capability the run-on ADR said had to exist before any ladder change could ship;
it now exists, and its first answer is "the 5% ladder's edge survives the slot
charge but is still carried by one trade and one regime" — measure again as the
sample grows and across a down-tape before touching `TRAIL_PROFIT_TIERS`.

## Consequences

- The winners-run question is now measurable in full: run-on removes the
  truncation bias, `--slotcost` removes the free-hold bias. The two together are
  the harness the tuning needs.
- `research/exit_rule_replay.py` gained ~230 lines (the `Position` model,
  `merge_positions`, `slot_concurrency`, the portfolio walk, `score_slotcost`,
  `report_slotcost`, the `--slotcost` flag) and an additive `"ts"` on every
  simulator return. No live trading code is touched; this is research tooling.
- The correctness gate (concurrency ≤ MAX_POSITIONS) is printed on every run, so
  a future data anomaly that breaks the partial-merge cannot silently corrupt the
  slot charge.
- `POWER_HOLD` at every trigger tested (10/7/5%) and both trail widths remains
  byte-identical to shipped or worse once the slot charge lands, consistent with
  the run-on finding that the +5% ladder rung sells first. Nothing here revives
  it.

See `decisions/2026-09-18_runon-window-winners-run.md` for the blocker this
resolves, and `research/exit_rule_replay.py` (`--slotcost`) for the code.
