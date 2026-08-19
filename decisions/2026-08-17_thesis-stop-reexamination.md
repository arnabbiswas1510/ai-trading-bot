# Decision: Keep the Thesis Stop at 1.0×ATR from day 2, reclassified as risk-shaping rather than return-enhancing

Follow-up to `decisions/2026-08-17_armed-exit-backtest-lookahead.md`, which
removed the look-ahead bias from `research/thesis_bt.py` and in doing so cost
the thesis stop its headline result (+18.8pp PASS, significant → +10.3pp,
CI crossing zero). This ADR answers the question that raised: *does the thesis
stop still earn its place, and at what parameters?*

## Method — decision rule fixed before looking at results

`research/thesis_reexam_bt.py` sweeps 5 ATR multipliers × 2 start days and
evaluates each in **four slices**: {BROAD, PASS} × {conservative, optimistic
intra-bar path}. Paired stationary-block bootstrap, 2000 reps.

A configuration is actionable only if median ΔCAGR is positive in **all four**.
This is the project's existing two-universe rule extended to path risk, and it
was written down before the sweep ran so it could not be tuned to the answer.

## Result 1 — no configuration survives

All 10 configs fail. The two most interesting failures:

| Config | BROAD/cons | BROAD/opt | PASS/cons | PASS/opt |
|---|---|---|---|---|
| 0.75× d3 | **−11.0 [−21.7, −1.4]** sig. HARMFUL | −8.7 | **+10.7 [+1.1, +22.8]** sig. HELPFUL | +9.1 |
| 1.5× d2 | **−17.6** sig. | **−15.0** sig. | +3.1 | +4.4 |

`0.75× d3` is statistically significant in three of four slices — in
*contradictory directions*. That is the signature of a result driven by
something other than the rule itself.

The sweeps disagree in shape, too:

- **PASS is cleanly monotonic** (tighter = better): 37.2, 37.4, 31.0, 24.2, 25.9 vs OFF 27.7
- **BROAD is wildly non-monotonic**: 14.7, 14.2, 20.9, 6.9, 22.1 vs OFF 23.2 — every config below OFF, with no ordering

## Result 2 — why they disagree: the rule barely does anything

The portfolio sim cannot isolate cause, because cutting a position frees a slot
and changes which *later* entries are taken. `thesis_counterfactual.py` removes
the 4-slot constraint so every signal becomes a trade in both arms, restoring
1:1 pairing and separating the rule's **direct** effect (on the trades it cuts)
from its **indirect** effect (capital velocity / slot reshuffling).

At the shipped 1.0× from day 2:

| | BROAD | PASS |
|---|---|---|
| Signals / cut by the rule | 2315 / 387 (16.7%) | 598 / 89 (14.9%) |
| Cut positions: thesis exit | −3.54% mean | −4.06% mean |
| Cut positions: if HELD instead | −3.43% mean | −4.56% mean |
| **Direct effect** | **−0.11% mean, +0.62% median** | **+0.51% mean, +0.81% median** |
| Cuts where the rule helped | 59% | 63% |
| Cut positions that would have ended profitable | 14% (mean +5.3%) | 9% (mean +5.4%) |
| Cut positions that would have reached ≥ +20% | **3 of 387** | **0 of 89** |

And this holds across the whole grid — per-trade expectancy over *all* signals
never moves more than 0.05pp in either universe:

| Config | BROAD OFF → ON | PASS OFF → ON |
|---|---|---|
| 0.75× d2 | +1.16% → +1.12% | +1.03% → +1.04% |
| 1.0× d2 | +1.16% → +1.14% | +1.03% → +1.10% |
| 1.25× d3 | +1.16% → +1.11% | +1.03% → +0.98% |
| 2.0× d3 | +1.16% → +1.14% | +1.03% → +1.00% |

**The thesis stop cuts positions that were going to lose about the same amount
anyway.** It exits at −3.5% to −4.1% trades that would have ended at −3.4% to
−4.6%. It is not rescuing capital from disaster, and — importantly — it is not
destroying the right tail either: essentially none of the positions it cuts
would have become big winners.

Therefore the ±10–18pp portfolio-level CAGR swings are **almost entirely the
indirect effect**: which slot happened to be free on which day, and which
subsequent breakout that let the portfolio catch. That is path-dependent luck,
not edge. It explains both the BROAD non-monotonicity and the universe
disagreement, and it means *no* multiplier in this grid is genuinely better
than any other.

## Decision

**Keep the thesis stop enabled at `THESIS_STOP_ATR_MULT = 1.0` from day 2 —
unchanged — and stop describing it as a return-enhancing rule.**

Do not retune the multiplier. The sweep that would justify a change is measuring
slot-timing luck, so picking its argmax would be fitting noise. `0.75× d2` looks
best on PASS; it is −8.7pp on BROAD and significantly harmful at `d3`. That is
not a tuning opportunity.

Keep rather than remove, because:

- Direct effect is **median-positive in 5 of 6** measured configs, and mean-positive on the traded universe.
- It **does not cut winners** (0 of 89 PASS cuts would have reached +20%), so the usual cost of an early-exit rule is absent here.
- It reduces average loss (BROAD −4.43 → −4.14, PASS −5.60 → −4.96) and recycles capital out of a position whose entry premise has been invalidated.
- On PASS it materially improves the **worst period** (OFF worst −0.5 → +19.9 at 0.75× d2), i.e. it shortens the left tail even where it does not lift the mean.
- Turning it off is itself a live-trading change and there is no evidence for that direction either. Neutral evidence favours the status quo.

What changes is the **claim**, not the code: the thesis stop is justified as
loss-shaping and capital recycling on an invalidated entry, not as a CAGR
improvement. `docs/sell_logic.md` is updated to say exactly that.

## Consequences

- No production code changes. `execution_agent.py` constants are untouched.
- The +18.82pp figure is fully retired from the docs; any future citation of it
  is a bug.
- Future evaluation of *any* exit rule in this repo must report the direct
  (per-signal, slot-free) effect alongside portfolio CAGR. A portfolio-level
  ΔCAGR on a 4-slot sim is not by itself evidence that a rule works — as this
  re-examination shows, it can be dominated by slot-timing noise.

## Files changed

- `research/thesis_reexam_bt.py` (new): four-slice sweep with pre-registered decision rule.
- `docs/sell_logic.md`: thesis stop section reframed; validation table corrected.
- `decisions/2026-08-09_thesis-stop.md`: pointer to this re-examination.
