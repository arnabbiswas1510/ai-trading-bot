# Entry decisions extracted into a pure `decision_core` shared by live and backtest

- **Date:** 2026-09-29
- **Status:** Accepted. **No live behaviour changed** — this is a
  behaviour-preserving refactor, proven green by the existing buy-path test suite.
- **Builds on:** `decisions/2026-09-21_equity-capped-position-size.md`,
  `decisions/2026-09-26_reason-aware-cooling-off.md`,
  `decisions/2026-09-28_earnings-blackout-and-news-veto.md`
- **Part of:** the backtest-fidelity roadmap (Phase 1 of 6). Phase 0 shipped the
  golden characterization tests (`tests/test_buy_decision_golden.py`, patch 089).

## Context

For a backtest to be trustworthy enough to size real money against, it must make
the **same entry decisions the live bot makes** — not an approximation of them.
Before this change the live buy path (`buying.run_market_open_buys`) and the
research backtester (`backend/backtester.py`) were two independent
implementations of "which breakout do we buy and how big". They had already
drifted: the backtester still modelled a reduced set of entry gates and retired
exit rules, so any profitability number it produced answered a question about a
strategy the bot no longer runs. AGENTS.md item #1 of the roadmap names this the
single biggest source of backtest inaccuracy — "every other improvement is polish
on the wrong object" until parity exists.

The root cause is structural: the decision logic (ranking, the per-trigger gate
ladder, position sizing) was welded to I/O — the live IBKR `IB` object, Supabase
reads/writes, Telegram, and the clock — inside one 500-line function. There was no
way for a second caller to reuse the decision without also dragging in a live
brokerage connection.

## Decision

Extract the **pure** entry-decision logic into a new module, `decision_core.py`,
with no I/O and no external dependencies (it imports only the `trigger_audit`
reason-code constants, which are plain strings). It exposes:

- `rank_triggers()` / `trigger_sort_key()` — the `final_score` → `quality_score`
  → `ai_rating` → 0 ranking, including the `or`-fallthrough that treats 0 and
  None identically.
- `candidate_score_of()` / `min_score_for()` — the score the floor is applied to
  (`adjusted_score` if present, else `final_score`; None fails closed) and the
  per-trigger-type floor.
- `evaluate_eligibility()` — the price-independent gate ladder (gates 1–6:
  already-held → cooling-off → AI D-grade veto → earnings blackout →
  no-AI-score → score floor).
- `evaluate_capacity()` / `evaluate_cash()` / `evaluate_market_gates()` — the
  sizing-dependent gates (7–10: capacity halt, insufficient-cash,
  volume-surge, pre-breakout pivot distance).
- `evaluate_price_gates()` — the price-dependent gates (11–13: extension
  ceiling, breakdown floor, share count), returning a `BUY` decision with the
  exact share count or a `SKIP`.
- `equity_capped_position_size()` — the equal-weight ceiling from the
  2026-09-21 oversizing incident. `buying.equity_capped_position_size` is now a
  thin wrapper delegating here, so existing imports still resolve.
- `DecisionConfig` + `config_from_module(ea)` — an immutable threshold snapshot,
  so production uses production constants while a backtest can construct its own
  to sweep them.

`buying.run_market_open_buys` keeps everything with a side effect — fetching live
cash/price from IBKR, computing trading-days-to-earnings from the NYSE calendar,
placing the order, waiting for the fill, writing the position and the audit rows,
notifying — and now **asks `decision_core` for each verdict**, then performs the
I/O the verdict implies. The gate **order is contractual**: the live audit trail
(`trigger_decisions`) depends on which reason a trigger is rejected for, so the
ladder order is preserved exactly and pinned by tests.

## Why this is safe to ship

The extraction is behaviour-preserving by construction and proven so:

- The Phase 0 golden characterization suite (`tests/test_buy_decision_golden.py`)
  — which pins ranking, multi-buy slot depletion, cash accounting, share sizing
  and the reason-code sequence *through the live path* — stays green unchanged.
- `tests/test_buy_gates.py` and `tests/test_buy_fill_verification.py` (45 tests
  total across the buy path) stay green unchanged.
- New `tests/test_decision_core.py` (31 tests) pins each gate directly on plain
  dicts.
- **Teeth verified by mutation:** reversing the ranking sort in `decision_core`
  fails both the golden suite and the unit suite; the sizing tests are tied to
  the exact equity-cap formula.

`decision_core.py` was added to `Dockerfile.agent`'s COPY line — caught by
`tests/test_agent_image_completeness.py`, which would otherwise fail the build.

## Consequences

- Entry selection is now single-source. A gate threshold or ordering can only
  change in one place, and the backtester (Phase 2+) will import the identical
  functions — parity by construction, not by discipline.
- The next roadmap phase (exit parity on intraday bars) can proceed against a
  decision core that is already shared, testable without a broker, and locked by
  a mutation-proven suite.
- No parameter, threshold or gate behaviour changed. This ADR records a
  structural decision, not a behavioural one.
