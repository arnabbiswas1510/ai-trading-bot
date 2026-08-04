# Managed exit tool: exit at the session high rather than on impulse

**Date:** 2026-08-04
**Status:** Accepted

## Context

`force_sell.py` liquidates immediately with a marketable limit order. That is
correct for an emergency, but it guarantees selling at the current print — and
the decision to exit almost always arrives *after* a drop, so the current print
is very often a local trough. This is the same failure mode that motivated the
Armed Trailing Exit for the Day 0–6 window (see
`2026-08-04_armed-trailing-exit-day0-6`): selling at the trigger price is worse
than riding the subsequent bounce.

There was no on-demand equivalent for "exit these specific names today, but at a
sensible price".

## Decision

Add `managed_exit.py`, separate from `force_sell.py` (which stays as the
emergency path). It places an IBKR **native trailing stop** on each named ticker
and then supervises three exit conditions:

| condition | outcome |
|---|---|
| trail fires | exits near the session high-water mark |
| hard floor breached | exits immediately — caps the cost of being patient |
| deadline passes (default 15:50 NY) | forced market exit, so the position is flat today |

The native trailing stop is used rather than script-side high-water tracking for
two reasons: IBKR tracks the mark tick-by-tick, far finer than any polling loop,
and the stop survives the script being killed.

### Trail sizing is volatility-scaled, not fixed

`--trail auto` (the default) sets the trail to `MANAGED_EXIT_ATR_FRACTION` (40%)
of the position's `entry_atr_pct`, clamped to [0.8%, 3.0%].

A fixed tight trail is actively harmful here. The existing
`ARMED_EXIT_TRAIL_PCT` is 0.6%, which is appropriate for its own narrow purpose,
but applied to the current book it would fire on the first tick of noise:

| ticker | ATR | auto trail |
|---|---|---|
| NBIX | 2.88% | 1.15% |
| CPAY | 2.76% | 1.10% |
| SWK | 3.54% | 1.42% |
| DXCM | 4.04% | 1.62% |

A 0.6% trail on DXCM (4% average daily range) reproduces "sell immediately" with
extra steps.

### The hard floor is what makes patience safe

Without it, a name that gaps down and keeps sliding would sit unsold until the
deadline, which converts a small loss into a large one — the exact outcome the
tool is meant to avoid. Default 2% below the arming price, `--no-floor` to
disable.

## Consequences

- Requires `exit_armed*` columns (`migrations/add_armed_exit_columns.sql`), which
  were found to be unapplied in production during this work and have now been
  run alongside `add_power_hold.sql`.
- Inherits the `force_sell.py` constraint: sells must use `clientId=1` or IBKR
  treats them as opening a short, so the execution-agent must be stopped first.
  It would otherwise also compete with this script for control of the stops.
- On `KeyboardInterrupt` the IBKR trailing stops are deliberately left live, and
  the script says so. Cancelling them on exit would silently remove downside
  protection from a position the operator has already decided to sell.

## Note on the positions that prompted this

The tool was requested to cut losses on four holdings believed to have been
bought on faulty breakout ratings. On inspection that premise did not hold: the
book was +$139 (+0.14%), all four names carried genuine A-grade scores of 80–85
(so they were not victims of the NULL-`final_score` fail-open bug), all four sat
above their 50- and 200-day moving averages, and two were at 52-week highs. The
tool was built as a general-purpose capability; it was not applied to them.
