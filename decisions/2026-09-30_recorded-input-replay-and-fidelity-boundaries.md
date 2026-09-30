# Recorded-input replay and explicit execution-fidelity boundaries

Date: 2026-09-30
Status: Accepted — extended by [actual-account recording and dashboard research](2026-09-30_intraday-capture-and-approved-research.md)

> **2026-09-30 extension:** the cash-only schema-1 interface and its limits
> remain valid. A separate schema-2 actual-account mode, passive recorder and
> dashboard now accompany it. The original statement below that this change
> does not provide a production recorder describes patch 098, not the expanded
> system. Sampled execution is still not exact IBKR replay.

## Context

The profitability review used three incompatible measurements as though they
tested one current strategy. The default exit replay selected retired rules;
the cooling-off simulator used older exits and recycled a slot freed later in
the day into a purchase at that day's open; and the trigger report counted
future price peaks rather than executable portfolio profits. A daily backtester
also used future closing marks to size opening purchases.

The statements that the five-position cap was the most detrimental rule, that
the default exit replay tested the shipped stack, and that small forward-return
averages proved the AI veto beneficial are withdrawn. No live rule was changed
on those conclusions.

## Decision

Provide an offline recorded-input replay using `decision_core`, `exit_core`,
`exit_rules`, the shared cooling-off policy and the existing cost model. Keep
the live money path unchanged. Separate chronological broker price observations
from buy decisions, monitoring cycles and end-of-day observations. Recompute
cash, slots and re-entry eligibility from simulated fills, rather than carrying
the historical buy/skip label into a counterfactual portfolio.

Missing point-in-time inputs and unsupported execution paths must fail loudly.
Do not substitute current AI grades, session highs, historical exit prices, or
today's portfolio for missing history. A sampled-price fill model is an explicit
assumption, not an assertion that IBKR would have filled there. Comparisons use
the same input stream and cost model and include remaining marked positions,
not only closed trades or maximum future upside.

Fix confirmed chronology/look-ahead defects in older simulators, while retaining
their experimental models and labelling them non-equivalent. In particular,
partial proceeds cannot come from a price reached after the full exit, an
opening allocation cannot depend on that evening's close, and intraday exits
cannot fund purchases at an already-past opening price.

## Interpretation and historical errata

- `exit_rule_replay.py` remains a historical, independently implemented model.
  Its default output is not a comparison against the current live configuration.
- Prior scale-out results can include proceeds earned after a modelled full
  exit. Do not cite affected baseline/sweep results until rerun; no corrected
  numerical replacement is asserted by this change.
- The cooling-off comparison of 29.5% / 27.3% / 29.0% annualised returns used
  defective chronology and different day counting. Do not cite the -2.2 or
  +1.7 percentage-point differences as current-strategy effects.
- The full-book trigger association does not price the position cap: candidates
  may fail other gates or already be held, dates lack intraday availability,
  and maximum upside is not realised return.
- Calling current exit helpers from a daily adapter establishes code reuse,
  not equivalent broker execution, ordering or even relative profit rankings.

## Limits

Existing `trigger_decisions` rows are daily summaries, not complete per-cycle
recordings; `trigger_history` does not preserve every required earnings and
runtime input. This change cannot reconstruct facts never recorded. It provides
a strict offline input contract, not a fabricated historical profitability
verdict or a production recorder. Broker latency, partial fills, manual exits
and portfolio rotation require their own supported inputs/mechanics; unsupported
paths must be rejected rather than silently ignored.

See `docs/backtesting.md` for the runnable interface and exact supported scope.
