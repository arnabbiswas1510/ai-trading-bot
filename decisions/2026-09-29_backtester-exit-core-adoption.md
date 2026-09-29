# Backtester exit parity: a research backtest that calls the live exit engine

**Date:** 2026-09-29
**Status:** Accepted
**Scope:** backtest fidelity (roadmap item #1 — parity — for the EXIT side)

## Context

The backtest-fidelity roadmap's first and highest-priority gap is *parity*: a
backtest is only trustworthy enough to size real money against if it runs the
**same** exit code the live bot runs, not a parallel reimplementation. Today it
does not. Three separate pieces of code decide exits, and they drift:

- `monitoring.monitor_portfolio_intraday` — the LIVE money path;
- `backend/backtester.py` — a research reimplementation that still models the
  **retired** 7%-trail-from-peak + EMA-21×0.99 rules and never imports
  `exit_rules`;
- `research/exit_rule_replay.py` and the `research/*_bt.py` harnesses — each
  carry their own copy of the Prove-It / ladder constants.

Any profitability number `backend/backtester.py` produces therefore answers a
question about a strategy the bot no longer runs.

`exit_core.py` (extracted 2026-09-29, ADR `2026-09-29_exit-core-extraction.md`)
made this fixable: it is the pure, I/O-free per-cycle exit verdict, orchestrating
the `exit_rules` primitives in the exact live order. A backtest can now import and
call it.

## The container obstacle, and why this is a *research* tool

`backend/backtester.py` runs inside the web/dashboard image, which is built
`COPY backend/ ./backend/` and deliberately does **not** contain the root modules
(`config.py`, `exit_rules.py`, `exit_core.py` — see the NOTE ON CONTAINER LAYOUT
in `config.py`). Making that file import the live exit engine therefore requires
bringing those modules into the web image — a change that touches the live
dashboard build. That is deferred as a separate step ("Option A").

This ADR is "Option B": a **new** CLI tool, `research/strategy_backtest.py`, that
lives where the live modules already import cleanly, runs offline from the
committed `benchmark_data/` daily bars (no FMP key, let alone the intraday
subscription), and carries **zero deployment risk**. It is the same pattern the
repo's trustworthy harnesses already follow (`exit_rule_replay.py` is a
root-level CLI tool, not a container endpoint). The web button in
`backend/backtester.py` stays on the retired rules until Option A repoints it at
the same engine.

## Decision

Add `research/strategy_backtest.py`: a full-portfolio daily-bar strategy backtest
whose **exits are the live code**. It imports `exit_core` and `exit_rules` and
calls them; the Prove-It Stop (Phase 1 band, Phase 2 give-back floor), the
dynamic trailing ladder (off the live `TRAIL_PROFIT_TIERS`), the power-hold
widening and the partial scale-out are the live rules by construction. Change a
threshold in `exit_rules.py` and this backtest changes with it.

Entry, sizing, market filter and no-look-ahead T+1-open fills **mirror
`backend/backtester.py`** so the two tools choose the same entries (entry parity
against `decision_core` is a separate, later step). The exit engine
(`resolve_position_day`) computes every resting downside level from a live
function each day — Prove-It level, `hwm × (1 − ladder%)`, static hard stop — and
resolves them against the daily bar with a no-look-ahead convention: resting
levels use the peak/HWM as of the *start* of the day, and today's high is folded
in only *after* the low is resolved, so a wide-range day cannot trip its own
trail.

## Fidelity — what this is and is not

This achieves **rule parity** (which exit fires, and why) but **not exact
fill-price fidelity**, because daily bars cannot resolve the intraday mechanics
the live loss rules depend on:

- The live loss rules do not sell — they `arm_exit()` a tight 0.6% IBKR trail
  with a 3.25h deadline that resolves *intraday*. On daily bars that bounce is
  invisible, so a Prove-It arm is modelled as a sell at the level (or the open,
  if the bar gapped through). This is the same daily-bar limitation
  `exit_rule_replay.py` documents and is why that tool uses 5-minute bars.
- The 15-minute poll is collapsed to one sequence per day; trail-tightening and
  scale-out are resolved off the intraday high / at the close.
- Commissions and slippage are not modelled (roadmap item #3).

**Therefore:** trust it for *relative* questions — does a rule fire, how often,
does a change help or hurt, do entries and exits behave like production. Do **not**
read its absolute P&L as a precise +EV/−EV verdict on the tight Prove-It exits.
That needs 5-minute bars, which drop in with no logic change (the exit-decision
calls in `resolve_position_day` are bar-granularity agnostic). The register
work-item `intraday-fmp-exit-fidelity` tracks that upgrade, gated on live usage
showing exit-price precision is actually needed.

## Consequences

- There is now a backtest whose exits cannot silently drift from production, and
  a `tests/test_strategy_backtest.py` that pins each live rule (Phase 1 band,
  Phase 2 floor, trailing ladder, scale-out, gap-through fill) with mutation
  teeth.
- The retired-rules result in `backend/backtester.py` is now clearly labelled as
  non-authoritative in `docs/backtesting.md`, pending Option A.
- Two follow-ups are recorded: **Option A** (bring the live modules into the web
  image and repoint `backend/backtester.py`) and **entry parity** (point the
  breakout scan at `decision_core`).

## Alternatives considered

- **Upgrade `backend/backtester.py` in place first (Option A).** Rejected as the
  *first* step: it touches the live dashboard image for a feature that is
  inherently a research activity (a money-grade backtest also needs point-in-time
  fundamentals and walk-forward validation, which do not belong behind a web
  button). Option B does the hard exit logic once; Option A becomes plumbing that
  reuses it.
- **Add 5-minute FMP now.** Rejected per the operator's explicit sequencing:
  daily-bar rule parity first, intraday fidelity only when live usage shows it is
  needed. Recorded as a register work-item rather than built speculatively.
