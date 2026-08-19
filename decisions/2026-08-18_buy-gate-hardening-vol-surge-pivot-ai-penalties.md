# ADR: Buy Gate Hardening — Volume Surge Floor, PRE_BREAKOUT Pivot Distance, AI Penalty Rules

**Date:** 2026-08-18  
**Status:** Accepted

---

## Context

FROG and APH were purchased on 2026-08-18 and 2026-08-17 respectively as
`PRE_BREAKOUT` triggers and both were stopped out the same day/next day for
combined losses of ~$1,813. Post-mortem identified three systemic failures:

1. **No volume surge floor.** APH had a 0.64× surge and FROG 0.86× — both
   below the 50-day average. CAN SLIM requires rising institutional volume to
   confirm a breakout. The bot had no code-level gate enforcing this.

2. **PRE_BREAKOUT entries too far from 52W high.** The existing pivot check
   (`MAX_PIVOT_BREAKDOWN`) only compares current price vs *yesterday's close*,
   not vs the 52-week high that the stock must breach to confirm a breakout.
   APH was -4.29% and FROG -4.62% below their 52W highs — speculative
   positioning, not confirmed breakout entries.

3. **AI grade inflation rendering the veto inert.** In 30 days of operation the
   AI evaluator gave 80% A grades, 20% B grades, and zero D-grade vetoes. The
   prompt lacked explicit penalty rules for the exact red flags that caused
   these losses (sub-par volume surge, below-pivot, negative ROE, Sell analyst,
   large institutional float). The D-veto threshold (rating < 30) was never
   reachable given the AI's rating floor of ~58.

---

## Decision

### 1. Hard volume surge gate in `execution_agent.py`

Added `MIN_VOL_SURGE_GATE = float(os.getenv("MIN_VOL_SURGE_GATE", 0.75))`.

In `run_market_open_buys()`, after the cash floor check, a trigger whose
`volume_surge < MIN_VOL_SURGE_GATE` is skipped with reason `SCORE_FLOOR`.
This gate is AI-independent — no score can override it.

Both APH (0.64×) and FROG (0.86×) would have been blocked.

### 2. PRE_BREAKOUT 52W pivot distance gate in `execution_agent.py`

Added `MAX_PRE_BREAKOUT_PIVOT_DIST = float(os.getenv("MAX_PRE_BREAKOUT_PIVOT_DIST", 0.05))`.

For `PRE_BREAKOUT` and `PRE_BREAKOUT_RELAXED` triggers, if
`pivot_distance_pct < -(MAX_PRE_BREAKOUT_PIVOT_DIST × 100)` the trigger is
skipped with reason `BELOW_PIVOT`. The `pivot_distance_pct` field stored by
the screener measures distance from the stock's 52-week high.

Both APH (-4.29%) and FROG (-4.62%) would have been blocked at 5% gate.

### 3. AI prompt penalty rules in `ai_evaluator.py`

Added a `MANDATORY QUALITY PENALTIES` block to `build_prompt()` with explicit
reductions for:

| Condition | Penalty |
|---|---|
| `volume_surge < 0.75×` | −25 pts |
| `volume_surge 0.75–1.0×` | −10 pts |
| `DistFromPivot < −3%` | −20 pts |
| Negative ROE | −15 pts |
| Analyst = "Sell" | −20 pts |
| Float > 1 billion shares | −20 pts |

These are additive. A stock like APH (0.64× surge −25, Sell analyst −20,
1.23B float −20) would have received a rating ~25–35 points lower.

### 4. D-veto threshold raised from 30 → 50

Changed `_GRADE_BOUNDARIES` from `[(70,"A",15),(50,"B",5),(30,"C",0)]` to
`[(70,"A",15),(55,"B",5),(50,"C",0)]`.

D-grade (veto) now triggers at `rating < 50` instead of `< 30`. With prompt
penalties applied, genuinely weak setups will score 35–45 and be vetoed.

---

## Consequences

- Both FROG and APH would have been blocked by gates 1 and 2 before reaching
  the AI score check.
- PRE_BREAKOUT entries are now substantially harder to pass — they require both
  adequate volume AND proximity to the 52W high.
- The AI veto is now calibrated to the actual rating distribution and will fire
  on the bottom ~15% of candidates rather than being theoretically inert.
- `MIN_VOL_SURGE_GATE` and `MAX_PRE_BREAKOUT_PIVOT_DIST` are env-configurable
  for tuning without code changes.

See `docs/buy_logic.md` and `docs/configuration.md` for updated parameter tables.
