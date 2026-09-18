# The run-on truncation bias also affected the scale-out path

- **Date:** 2026-09-18
- **Status:** Accepted
- **Area:** `research/exit_rule_replay.py` — `simulate_scaleout()`,
  `fetch_entry_atr_pct()`, `report()`
- **Scope:** **Measurement harness only. No trading code, no parameter changes.**

## Context

`decisions/2026-09-18_runon-window-winners-run.md` fixed a methodological flaw:
the replay fetched bars only up to the realised exit, so any rule that would
have held *longer* ran out of price history exactly when the live rule sold,
booked a delta of zero, and was silently handed the realised exit price for
free. Loosening was unmeasurable by construction.

That fix was applied in `score()`, which covers the Prove-It path. **It was not
applied to `simulate_scaleout()`**, which has its own `rem is None` branch:

```python
rem = simulate_proveit(trade, rem_cfg)
remainder_exit = rem["price"] if rem is not None else trade.sell_price
```

`rem is None` means the remainder's rule never fired. Falling back to
`trade.sell_price` is the identical bias in a second location — it hands the
remainder the live exit for free, so a **wider trail on the remainder can never
show its upside**. Since the whole point of combining scale-out with a loosened
ladder is to let the remainder run, the one configuration family most worth
testing was the one the harness could not score.

This was found while answering "does the bot let winners run?" on 2026-09-18.
It did not corrupt any previously published figure: `--scale` predates the
run-on window, so `runon_from` was always `None` on those runs and the branch
was unreachable. The bug was latent, not historical.

Two further defects surfaced in the same session:

1. **`fetch_entry_atr_pct()` used a bare `requests.get`.** A 52-trade run-on
   sweep issues two FMP calls per trade and reliably trips the rate limiter. The
   backoff helper `_fmp_get()` added by the run-on work was wired into
   `fetch_5min()` but not here, so the sweep aborted with an unhandled 429 after
   ~40 trades of completed work — roughly ten minutes of fetching discarded.

2. **`report()` only printed the per-trade breakdown for the top-scoring row.**
   `AGENTS.md` requires every review to answer *"is any result carried by a
   single trade?"*, and that question is about the configuration being
   **considered**, which is rarely the top row — the top row is usually an
   unshippable ceiling such as `CEILING — winners never sold`. The concentration
   check therefore either got skipped or got answered from the wrong
   configuration's numbers.

## Decision

1. Mark the scale-out remainder to the last available close when its rule never
   fires and a run-on window exists, exactly as `score()` does.
2. Route `fetch_entry_atr_pct()` through `_fmp_get()`.
3. Add `--detail SUBSTRING`, selecting which configuration gets the per-trade
   breakdown, and print a concentration summary with it: net, largest single
   contributor as a share of net, net excluding the largest, and helped/harmed
   counts.

## Consequences

The concentration summary immediately changed a conclusion. Widening the Phase 2
ladder measured **+$6,429 against a no-scale-out baseline** and looked
shippable. With the correct baseline — scale-out is **already live**
(`SCALE_OUT_ENABLED` defaults true), so the honest comparison is ladder 1.5% +
scale 33%@+4% — it becomes:

| remainder trail | net vs live | largest contributor | net ex-top-3 |
|---|---|---|---|
| 3% | **−$1,350** | — | −$2,945 |
| 5% | +$4,308 | ECO **59%** | **−$1,014** |
| 8% | +$4,495 | ECO **56%** | **−$1,801** |

Every positive result is carried by ECO and turns **negative** once the top three
trades are removed. Per `AGENTS.md` — *"a configuration that wins on one outlier
has not won"* — this is not a signal, and the `ladder-width-runon` deferral in
`decisions/provisional_decisions.json` stands unchanged.

Without `--detail` the +$6,429 figure would have been quoted against the wrong
baseline and read as a shippable improvement.

## What this does not change

No trading parameter. `TRAIL_PROFIT_TIERS`, `POWER_HOLD_GAIN_PCT`,
`SCALE_OUT_TRIGGER_PCT` and `SCALE_OUT_FRACTION` are all untouched.
