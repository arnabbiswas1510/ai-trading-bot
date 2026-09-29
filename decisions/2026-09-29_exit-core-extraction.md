# Exit decisions extracted into a pure `exit_core` shared by live and backtest

- **Date:** 2026-09-29
- **Status:** Accepted. **No live behaviour changed** — this patch is purely
  additive (a new pure module plus tests). The live monitor is NOT yet rewired;
  its delegation is staged behind the orchestrator-split safety window.
- **Builds on:** `decisions/2026-09-18_execution-agent-split.md` (Stage 1, which
  moved the exit *math* into `exit_rules.py`),
  `decisions/2026-09-29_decision-core-extraction.md` (the entry twin of this change)
- **Part of:** the backtest-fidelity roadmap (Phase 2 of 6). Phase 1 shipped the
  pure entry decision (`decision_core.py`, patch 090); this is the exit half.

## Context

For a backtest to be trustworthy enough to size real money against, its **exits**
must match what the live bot does — not an approximation. Today three separate
pieces of code decide exits:

- `monitoring.monitor_portfolio_intraday` — the LIVE money path;
- `backend/backtester.py` — a research reimplementation that still models the
  RETIRED 7%-trail + EMA-21×0.99 rules and never imports `exit_rules`;
- `research/exit_rule_replay.py` — a third copy that MIRRORS the `PROVE_IT_*`
  constants in its own code.

Any profitability number the backtester produces is therefore an answer about a
strategy the bot no longer runs — the same "polish on the wrong object" problem
Phase 1 fixed for entries, now for exits.

The exit **math** was already extracted into `exit_rules.py` in Stage 1
(2026-09-18): `prove_it_stop_level`, `prove_it_trail_pct`, `hard_stop_price`,
`safe_hard_stop`, `_compute_dynamic_trail_pct`, `is_power_hold_active`,
`prove_it_is_proven`, `sell_state_code`. What remained welded to I/O inside
`monitor_portfolio_intraday` was the **orchestration**: the exact order those
primitives are called, and which single action a position takes each cycle. A
second caller (the backtester) could not reuse that verdict without dragging in a
live IBKR connection, Supabase, and the clock.

## Decision

Add `exit_core.py` — the exit twin of `decision_core.py`. A single pure function
`evaluate_exit(pos, ctx, cfg) -> ExitDecision` reproduces the live per-cycle exit
verdict by orchestrating the existing `exit_rules` primitives in
`monitor_portfolio_intraday`'s exact order:

1. **armed-exit deadline** → `SELL_DEADLINE` if held past
   `ARMED_EXIT_DEADLINE_HOURS`, else `AWAIT_ARMED` (short-circuits the rest);
2. **power-hold** active? → suppresses discretionary exits, widens the trail;
3. **Prove-It stop level** → `ARM_PROVE_IT` if price is at/through the level;
4. **partial scale-out** → `SCALE_OUT` the first time PEAK gain crosses the
   trigger — evaluated AFTER the Prove-It check, so a position through its
   give-back floor exits in full rather than being trimmed;
5. **trail + hard-stop** → `HOLD`, carrying the resolved trail % and safe hard
   price.

The module is parity-by-construction for the math (it calls the same
`exit_rules` functions the live path calls) and only reproduces the live
ORDERING, which is contractual.

### Why the live monitor is not rewired in this patch

`monitor_portfolio_intraday` is the live stop-enforcement path. The
`orchestrator-split` register entry gates any restructuring of it on a **quiet
book** (≤2 open positions, youngest ≥7 days, not before 2026-10-06), because a
mistake there could fail to sell a loser while a patched test silently no-ops.
That gate is currently closed. So this patch ships `exit_core` as **additive and
proven-equal**, exactly as Phase 0's golden tests preceded Phase 1's buy rewire.
The live delegation lands in a later patch when the gate opens; the backtester
adoption lands with the intraday-data step of Phase 2.

## Evidence (what replaces byte-identity)

- **14 tests** in `tests/test_exit_core.py`: unit coverage of every branch
  (armed-deadline force-sell/await, Phase 1 and Phase 2 firing, scale-out,
  Prove-It-before-scale-out ordering, HOLD trail/hard resolution, power-hold
  widening), plus a **parity test** that runs the SAME scripted multi-regime book
  as `tests/test_exit_path_golden.py` through both the live
  `monitor_portfolio_intraday` (via the `record_monitor` golden harness) and
  `exit_core`, asserting they take the same money-path action for every position.
- Teeth proven by mutation: perturbing the Prove-It firing condition fails 5
  tests including the parity test.
- The parity test is the **anti-drift guarantee**: `exit_core` cannot silently
  diverge from the live monitor, because a change to either that breaks agreement
  fails CI. This is the mechanism that makes the eventual delegation safe.

## Consequences

- One place now defines the exit verdict's *ordering*, and `exit_rules.py`
  already defines its *math* — so the backtester can achieve exit parity by
  calling `exit_core`, the same way it achieves entry parity by calling
  `decision_core`.
- Until the live delegation lands, there are still two orchestrations (live +
  `exit_core`), but they are held in lockstep by the parity test rather than by
  discipline.
- No constant, threshold, or behaviour changed; nothing was deleted.
