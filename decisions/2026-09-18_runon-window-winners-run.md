# The replay truncated at the real exit, so "hold longer" was unmeasurable; power hold is unreachable because the ladder sells first

- **Date:** 2026-09-18
- **Status:** Accepted — research tooling only. **No live parameter changed.**
- **Corrects in part:** `decisions/2026-08-22_hwm-profit-lock-arm-5pct.md`,
  `decisions/2026-09-17_exit-review-48-trades.md` (loosening direction only)
- **Follow-up:** `decisions/provisional_decisions.json` → `ladder-width-runon`

## Context

The standing question is the one the book is actually built around: *cut losers
early, but let winners live until it no longer makes financial sense to hold
them.* The 2026-09-18 SMTC/LPG comparison sharpened it — SMTC realised +$138
while LPG realised +$1,168 — and the first half of that investigation found and
fixed a genuine defect (`decisions/2026-09-18_phase1-static-backstop.md`).

This ADR is the second half: whether the bot's **profit-side** rules give up on
winners too early. Two levers govern that — the Phase 2 profit ladder
(`TRAIL_PROFIT_TIERS`, +5% → 1.5%) and the power hold
(`POWER_HOLD_GAIN_PCT` = 10%, which suspends the Prove-It stack and widens the
trail to 30%).

## The methodological flaw

`research/exit_rule_replay.py` fetched 5-minute bars only up to the **real**
exit:

```python
window_end = trade.sell_ts.replace(hour=23, minute=59, second=59)
```

A configuration whose rule would have held **longer** than the live rule
therefore runs out of price history at the exact moment the live rule sold.
`simulate_proveit()` returns `None`, and `score()` books a delta of **exactly
zero** — the looser rule is handed the realised exit price for free.

This is harmless for tightening: a tighter rule always fires *before* the
truncation point, so its fill is real. It is fatal for loosening, because
holding longer can **only** pay off in the bars truncation deletes. The sweep
had no upside term in its arithmetic at all.

The symptom was visible and was misread as a result. Re-run on 2026-09-18, the
truncated `--ladder` sweep reports:

| ladder trail | net vs realised |
|---|---|
| 1.25% | +$10,766 |
| **1.50% (shipped)** | **+$10,669** |
| 2.00% | +$8,733 |
| 2.50% | +$8,083 |
| 3.00% | +$7,181 |

Perfectly monotonic decay in the loosening direction — which is what an
arithmetic that cannot score upside produces, not evidence that the shipped
width is optimal.

## Decision

Add a **run-on window** to the harness (`--runon`, `--runon-days`, default 30).
Price history is fetched past the realised exit, and `score()` marks a position
that is still open at the end of the window out at the last available close
rather than crediting it the realised sell. Gated strictly on `runon_days > 0`:
with the flag off, `runon_from` stays 0 and every other sweep replays
byte-identically (verified — the `--ladder` table above reproduces to the
dollar after the change).

Also add the power hold to `ExitConfig` and `simulate_proveit()`. It is the one
rule in the book whose *purpose* is to hold longer and the harness had never
modelled it. The model follows `execution_agent` ~L4217–4335: latch on peak gain
within the trigger window, suspend Phase 2, and let the wide trail **replace**
`stop_price` outright rather than being `max()`'d into it — the single case live
code permits a stop to loosen.

## Evidence

52 closed trades, 50 with usable history, 30-day run-on window.

### Power hold is unreachable — and the ladder is why

Peak gain within 21 calendar days of **entry**, ignoring when we sold:

| threshold | reached it | still held when it did |
|---|---|---|
| +5% | 34 / 50 | 16 |
| +7% | 26 / 50 | 8 |
| **+10%** | **13 / 50** | **1** |
| +15% | 5 / 50 | 0 |
| +20% | 1 / 50 | 0 |

**Thirteen positions reached the +10% power-hold trigger. The bot was still
holding exactly one of them.** ECO reached +28.5%, LPG +19.1%, DHT +17.1%,
MPC +16.5%, NTRA +15.6% — every one of them sold long before.

So the rule is not unreachable because the screener cannot produce +10% names.
It is unreachable because the +5% ladder rung clamps the trail to 1.5% and sells
the position at roughly half the trigger. The two rules are mutually
inconsistent, and `exit_rules.py` L212–215 *predicted* exactly this in prose
("by the time a position reaches `POWER_HOLD_GAIN_PCT` it is already on the
tightest rung"). This is the first time it has been measured.

Confirmed directly: power hold at the shipped +10% trigger is **byte-identical
to shipped** at every trail width tested (30% / 15% / 10%). It never fires.

### The loosening direction, scored honestly

| configuration | net vs realised | worst | >300 | harmed |
|---|---|---|---|---|
| CEILING — Phase 1 only, winners never sold | +$29,320 | −2,691 | 18 | 20 |
| P2 ladder 8% | +$26,922 | −1,269 | 12 | 19 |
| P2 ladder 5% | +$26,662 | −1,269 | 11 | 18 |
| power hold ≥5% @ 30% | +$22,507 | −2,691 | 16 | 21 |
| **SHIPPED (ladder 1.5%)** | **+$20,930** | −1,269 | 11 | 12 |
| power hold ≥10% (any trail) | +$20,930 | −1,269 | 11 | 12 |
| P2 ladder 3% | +$18,916 | −1,269 | 11 | 16 |
| power hold ≥7% @ 30% | +$13,544 | −1,768 | 14 | 19 |

The sign **reverses** once truncation is removed: the same widths the truncated
sweep ranked worst now rank best. That reversal is the finding. The ranking
itself is not yet a recommendation, for the reasons below.

## What this measurement does NOT support

1. **It is carried by one trade.** Ladder 5% beats shipped by +$5,685 across 24
   changed trades, but **ECO alone is +$3,794 — 67% of the net**. Ladder 8% is
   the same story (64%). Per the standing rule that a configuration winning on
   one outlier has not won, this does not clear the bar.
2. **It costs on the downside.** `harmed` rises 12 → 18, and the widest rows
   push `>300` up. TRV (−$1,354), CPAY (−$984) and NBIX (−$982) each give back
   real money at 5%.
3. **Slot opportunity cost is entirely unmodelled, and it is the decisive
   term.** `MAX_POSITIONS` is 5. Every run-on figure assumes a position can be
   held 30 extra days *for free*, when in reality it occupies one of five slots
   and blocks the next breakout. A book that held every winner 30 days longer
   would have taken a fraction of these 52 trades. The replay cannot see Rank &
   Replace and cannot price this. **No ladder change should ship until it can.**
4. **One regime.** All 52 trades fall in a single window. "Hold longer" is the
   strategy that benefits most from a rising tape and suffers most from a
   falling one, so a positive result here is the weakest possible evidence.
5. **`runon_open` is a mark, not an exit.** The CEILING row is a ceiling, not a
   proposal — it never sells anything and raises the worst single-trade loss to
   −$2,691.
6. **Power hold at ≥5% / ≥7% is non-monotonic** (+$22,507 vs +$13,544) because
   different trades arm at each level, not because 7% is structurally worse.
   Both are single-trade-carried. Neither is a candidate.

## Consequence

No live parameter changes. What changes is what we *know*:

- The claim "no trade reached +10% within 21 days" — carried in
  `exit_rules.py`, `docs/configuration.md`, `docs/sell_logic.md` and
  `AGENTS.md` — is **false**, and is corrected in all four. Thirteen did. The
  true statement is that the bot was not holding them by then.
- `POWER_HOLD_GAIN_PCT` is not merely unvalidated; at 10% it is provably
  **inert** given the current ladder. Lowering it in isolation would not fix
  that — the ladder would still sell first. The two must be retuned together or
  not at all.
- Any past conclusion that a **looser** give-back rule loses money was produced
  by the truncated harness and must be re-derived. Tightening conclusions —
  including the +6% → +5% arm decision of 2026-08-22 and the 48-trade review of
  2026-09-17 — are unaffected, because tightening fires before truncation.

The register entry `ladder-width-runon` carries this forward and will not come
due until slot opportunity cost can be modelled.
