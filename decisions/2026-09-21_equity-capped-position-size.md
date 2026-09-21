# Cap every position at one equal-weight share of equity

**Date:** 2026-09-21
**Status:** Accepted

## Context

On 2026-09-21 the bot took four same-day losses that were far larger in dollars
than the exit rules should have allowed: PSX −$719.90, MPC −$728.87, GNK
−$506.29, AVT −$433.80. The exits were investigated first and found to be
working: all four closed on **day 0** at −2.0% to −2.5% of entry, which is
exactly where the Prove-It Phase 1 static backstop (entry − 2%: the 1% day-0 band
plus 1% `PROVE_IT_BACKSTOP_SLACK_PCT`) is designed to sit. Nothing ran to −5% or
−10%. The stops were not circumvented.

The damage came from **position size**, not from the exits. Reconstructed from
the buy logs shipped to `agent_logs`:

```
13:32  AVT  $21,373 / 1 slot  ->  210 sh @ $97.65   = $20,506
13:47  MPC  $37,916 / 1 slot  ->   85 sh @ $425.95  = $36,206
14:18  PSX  $37,184 / 1 slot  ->  134 sh @ $267.58  = $35,856
14:48  GNK  $21,399 / 1 slot  ->  724 sh @ $28.32   = $20,504
```

MPC and PSX were each sized at **~$36k — about 1.6× the $22,306 equal-weight
share of the $111,530 account** (`NetLiquidation / MAX_POSITIONS`), and nearly 2×
the ~$19–20k the morning cohort received. A −2% stop on $36k is ~$720; the same
stop on an equal-weight $22k position is ~$440. The oversizing, not the exit
timing, is what turned two ordinary stop-outs into the two worst losses of the
day.

### Root cause

Position sizing was:

```python
remaining_slots = max(1, MAX_POSITIONS - stock_held_count)
position_size   = available_cash / remaining_slots
```

This distributes free cash across free slots, which is correct when the book is
being built from empty (the morning cohort was divided ÷4, ÷3, ÷2, ÷1 and every
name landed near $19–20k). It breaks for a **replacement** bought when the book
is nearly full. When four slots are already held, `remaining_slots` is **1**, and
the rule pours *all* currently-free cash into the single open slot. Because the
four held positions had been bought earlier for less than an equal-weight share,
the leftover equity pooled into that one slot: ~$37k of free cash against one
slot produced a ~$36k position.

The slot *count* was accounted correctly (4 held, 1 free). The flaw is that the
formula sizes against **free cash**, which is not bounded by an equal-weight
share of **total equity**.

## Decision

Add a hard ceiling. No single new position may exceed one equal-weight share of
total account equity:

```python
position_size = min(available_cash / remaining_slots,
                    NetLiquidation / MAX_POSITIONS)
```

Implemented as a pure, unit-tested helper `equity_capped_position_size(
available_cash, remaining_slots, equity, max_positions)` so the invariant can be
tested without IBKR, plus wiring in `run_market_open_buys()` that captures
`NetLiquidation` once per cycle and applies the cap.

Three properties matter:

- **The divisor is `MAX_POSITIONS`, the single source of truth for slot count.**
  The cap is therefore always exactly "one slot's worth of the whole account",
  and it tracks the account up and down automatically. No new tunable is
  introduced that could drift from `MAX_POSITIONS`.
- **`equity <= 0` skips the cap, it does not apply a $0 ceiling.** A transient
  failure to read `NetLiquidation` must never size every position to zero shares
  and freeze trading. The call site additionally reconstructs equity from
  `cash + Σ held market_value` before giving up, so the cap binds even when the
  `NetLiquidation` tag is momentarily missing — it never silently reverts to the
  uncapped formula that caused the incident.
- **The base rule is unchanged for the normal case.** When cash-per-slot is
  already at or below the equal-weight share (the usual state while building a
  book from cash), `min()` returns the base value and sizing is identical to
  before. Only the pathological "1 free slot, large free cash" case is clamped.

## Alternatives considered

**A fixed dollar cap (e.g. $20,000).** Rejected. It would need manual revision
every time the account grows or shrinks, and a stale constant is exactly the kind
of silent drift the equity-relative form avoids. `NetLiquidation / MAX_POSITIONS`
is self-adjusting.

**Cap as a share of cash rather than equity.** Rejected. Free cash is the number
that was already misleading — it is large precisely when the book is nearly full,
which is when oversizing happens. Equity is the stable base.

**Reduce `MAX_POSITIONS` or change the exit floor.** Rejected as non-responsive:
the exits worked, and the slot count is correct. The defect was purely that a
single slot could absorb more than its equal-weight share.

## Consequences

- A replacement position bought into a nearly-full book is now sized like every
  other slot. Under the 2026-09-21 conditions MPC and PSX would have been ~$22k
  (≈50 sh, ≈83 sh) instead of ~$36k, cutting each −2% stop-out from ~$720 to
  ~$440.
- Book weighting converges to equal-weight faster, since no slot can run away
  from the others on cash timing alone.
- When the account holds excess cash beyond `MAX_POSITIONS × equal-weight`, that
  surplus now stays in cash rather than concentrating into the last slot filled.
  That is the intended trade-off: capital preservation over full deployment.
- The historical backtester (`backend/backtester.py`), which explicitly mirrors
  live sizing and is used by the exit-parameter reviews, was updated to apply the
  same `min(cash / remaining_slots, equity / MAX_POSITIONS)` ceiling so its
  simulated position sizes and losses do not overstate live behaviour. Docs and
  the frontend comment mirror were updated to match.
- `NetLiquidation` is now read once per buy cycle (previously it was not read in
  this path). One extra account-values query per cycle; negligible.

## Validation

`tests/test_position_sizing_cap.py` — 37 tests. Pure-function cover includes the
exact incident numbers ($37,916 → capped to $22,305.94), the below-cap pass-
through, `equity<=0` skip, zero-slot coercion, and a parametrised invariant that
no allocation ever exceeds the equal-weight share across a wide cash/slot grid.
End-to-end cover drives `run_market_open_buys()` with 4 held + 1 free slot and
asserts the placed order's notional is within the equal-weight cap, that a normal
book is unaffected, and that the `NetLiquidation`-unavailable reconstruction path
still binds. Full suite: 916 passed.
