# Reconcile fill window floored at entry; honest trail-trigger display

- **Date:** 2026-09-09
- **Status:** Accepted
- **Area:** `reconcile_with_ibkr()` sell-price reconstruction; `_exit_context_suffix()` exit-detail display

## Context

Reviewing why FIVE peaked at $256.09 but closed below entry surfaced two
independent data bugs, both in how a broker-side exit is recorded after the fact.

FIVE was bought (85 sh @ $251.02, 9/8 15:48 ET), and earlier that same session an
*earlier* FIVE position had been round-tripped (84 sh sold @ $252.375, 9/8 15:36).
The next day the current position stopped out (31 @ $248.85 + 54 @ $248.8502,
9/9 13:08 ET).

### Bug 1 — sell-price contamination

`reconcile_with_ibkr()` prices a closed position from a weighted average of its
`ibkr_fills` SLD rows (Tier 1). That query filtered by `ticker` and `side` and
floored the fill window **only** when `scaled_out_at` was present — it had **no
`buy_date` floor**. A ticker bought, sold and re-bought in one session therefore
pulled the *prior* round-trip's sell fill into the new close:

```
(84 × 252.375 + 31 × 248.85 + 54 × 248.8502) / 169 = 250.6021
```

which is exactly the fictitious $250.60 recorded, versus the real $248.85. The
loss was understated by ~$150 (recorded −$35.52 net vs the true −$186.89). The
Tier 2 (`reqExecutions` session cache) path had the identical defect.

### Bug 2 — misleading trail-trigger display

`_exit_context_suffix()` renders `implied trigger = HWM × (1 − stop_loss_pct)`.
That is only correct when the trail is anchored on the high-water mark — true for
the base ATR trail and the profit-lock tiers. The **Prove-It floor lever**
(`prove_it_trail_pct()`) stores a trail measured from the price at the moment the
resting order was last re-placed, because IBKR's trailing anchor resets on every
cancel/re-place. FIVE's stored trail was 0.18% (the floor-pin value), so
`256.09 × (1 − 0.0018) = 255.63` was displayed as the trigger — *above* the
$248.85 the order actually filled at. A trailing stop cannot trigger above its own
fill, so the figure was not just approximate, it was impossible.

## Decision

1. **Floor the fill window at the current entry.** Both Tier 1 (`ibkr_fills`) and
   Tier 2 (`reqExecutions`) now floor at `_close_since = scaled_out_at or buy_date`,
   both stored as full ISO timestamps, so `.gt` cleanly excludes any fill at or
   before this position's entry — a prior round-trip or an earlier scale-out.

2. **Suppress physically impossible triggers.** `_exit_context_suffix()` still
   computes `HWM × (1 − trail)`, but only prints it when it is at or below the
   actual fill price (the physical invariant for a trailing stop). When it lands
   above the exit — the signature of a floor-pinned, non-HWM-anchored trail — it
   reports `floor re-anchored near $<fill> (trail not HWM-relative)` instead. The
   dashboard parser (`frontend/src/lib/exitDetails.js`) recognises the new phrase
   as the recorded stop trigger so it is not falsely reported as missing.

The live FIVE `trade_history` row was corrected in Supabase: `sell_price`
248.8501, `profit_loss` −184.44, `net_profit_loss` −186.89, and its `sell_reason`
re-rendered to the honest floor-re-anchor form.

## Consequences

- Realised P&L is now correct for any re-bought ticker; the loss-understatement
  class of error is closed at the source rather than patched per trade.
- Exit-detail panels never present a stop trigger above the fill again; a
  floor-pinned exit is labelled as such, telling the reviewer the give-back was
  bounded by the Prove-It floor rather than a runaway HWM trail.
- No schema change. The fix is a query floor plus a display guard.

## Validation

`tests/test_reconcile.py::TestReconcileSellPriceExcludesPriorRoundTrip` proves the
prior round-trip's fill is excluded (records ~$248.85, not $250.60);
`tests/test_exit_context.py` pins the trigger-suppression guard from both
directions. The `ibkr_fills` mock in `tests/conftest.py` now models the
`fill_time` floor so the regression cannot silently pass. Full suite: 630 passed.
