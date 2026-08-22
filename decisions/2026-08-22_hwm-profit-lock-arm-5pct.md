# Retune HWM profit-lock arm from +6% to +5%

- **Date:** 2026-08-22
- **Status:** Accepted
- **Supersedes in part:** `decisions/2026-08-20_hwm-profit-lock-first-leg.md`

## Context

The first-leg HWM profit-lock shipped on 2026-08-20 with `+6% -> 1.5%` to
solve winner round-trips. That ADR explicitly marked `+5% / 1.5%` as a
follow-up candidate once more real trades were available.

On 2026-08-22 we replayed closed trades on 5-minute bars using the same
intraday assumptions as the live agent (15-minute checks, armed trail mechanics)
and compared profit-lock families directly.

Usable sample: **17 closed trades** with complete bar history.

## Evidence

Same trailing cap (1.5%), varying only arm threshold:

| Config | Net delta vs realised exits |
|---|---:|
| `+5% -> 1.5%` | **+$4,720.53** |
| `+6% -> 1.5%` | **+$3,335.40** |

Difference: **+$1,385.13** in favour of +5%.

Additional check requested during this review:

| Alternative family | Best result |
|---|---:|
| No-new-high stall lock | +$1,623.28 (best variant) |

The stall family underperformed both +5% and +6% HWM arm thresholds in this
sample, so the mechanism remains a pure HWM gain-threshold arm.

## Decision

Change `TRAIL_PROFIT_TIERS` from:

- `>= +6%` unrealised gain -> `1.5%` trail from HWM

to:

- `>= +5%` unrealised gain -> `1.5%` trail from HWM

No other sell-rule ordering or mechanism changes:

- still broker-managed trailing stop
- still one-way tightening
- still bypassed by Power Hold.

## Consequences

**Positive**

- Captures first-leg gains earlier on the current live-trade sample.
- Improves net replay delta without introducing harmed-trade regressions in this run.

**Risks / limits**

- Sample size remains small (`n=17` usable), so this is still provisional.
- Outlier concentration remains possible; the next scheduled review must
  re-check stability as trade count grows.

## Docs sync

The shipped behaviour is now documented in:

- `docs/sell_logic.md`
- `docs/configuration.md`
- `README.md`

See those pages for current runtime behaviour; this ADR records why.
