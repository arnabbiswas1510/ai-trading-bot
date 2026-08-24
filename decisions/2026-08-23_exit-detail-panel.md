# Exit detail panel, and the removal of fabricated exit reasons

**Date:** 2026-08-23
**Status:** Accepted

## Context

The Exit Reason column on both the Dashboard and Trade History screens showed a
short badge and nothing else. Two problems sat behind it.

### 1. The labels were partly invented

`getCleanExitReason()` existed twice — once in `DashboardView.jsx` and once in
`TradesView.jsx` — and the copies had drifted. The Dashboard copy had been kept
current with the Thesis Stop, Early Dollar Stop, kill-switch and the plateau
tiers. The Trade History copy had not, and still contained this:

```js
if (lower.includes('order filled') || lower.includes('reconciled') || lower.includes('trail triggered')) {
  if (pctReturn >= 24.0) return 'Profit Target (+25%)';
  else                   return 'Stop Loss (-7%)';
}
```

Both outputs are false statements about the bot:

- **The fixed +25% profit target does not exist.** It was removed from the live
  bot, and `decisions/2026-07-23_backtester-accuracy-rewrite.md` records the
  backtester being changed to match ("No fixed profit target: live bot removed
  this"). No trade can exit on a rule that is not implemented.
- **The stop has not been a flat 7% for some time.** The base trail is 8.25–10%
  and tightens to 1.5% once the position locks in at +5%
  (`decisions/2026-08-22_hwm-profit-lock-arm-5pct.md`).

The mechanism of the bug is the more important part: the function took the
return percentage as an argument and inferred the *rule* from the *outcome*.
That is backwards, and it produced confidently wrong output. PTGX closed at
−2.77% on a broker trailing stop and was displayed as "Stop Loss (−7%)" — a
threshold that was never in force, on a trade that never came close to it.

The same fabrication existed in a third place found only by the doc-sync grep:
the screener's buy panel promised "Virtual stop-loss (7%) & profit target (25%)
will be set automatically", describing two rules that no longer exist.

### 2. Half the exits recorded no numbers at all

Of 21 closed trades, 10 carry the reason `Trailing stop (IBKR GTC TRAIL order)`
and nothing more. Agent-initiated exits embed their working in the reason text —
the Intraday Loss Minimiser records the intraday high and the sale price, Rank &
Replace records both scores and the replacement ticker — so they can be
reconstructed. Broker exits could not be, because the agent is not present when
the resting GTC order fills; it discovers the fill at the next reconcile and
wrote only the label.

Every number that would explain such an exit — `hwm_price`, `stop_loss_pct`,
`days_held`, `highest_unrealized_pct`, `exit_armed_price` — was sitting on the
`portfolio_positions` row that `reconcile_with_ibkr()` deletes seconds later.
Discarding them was gratuitous: once the row is gone they cannot be recovered.

## Decision

**Classification is separated from outcome, and lives in one place.**
`frontend/src/lib/exitDetails.js` is now the only exit classifier. `classifyExit()`
takes a single argument — the reason string. It structurally cannot see the P&L,
so it cannot infer a rule from it. Both views import it; the duplicated helpers
are deleted.

**Every exit gets a full breakdown.** `ExitDetailPanel.jsx` renders as an
expandable row on both screens, reusing the existing `row-expanded` idiom from
the positions table rather than introducing a modal pattern the codebase does not
have. It reports: who executed the exit (bot, broker, or human) and why that
distinction matters, the trade economics, the timeline, the numbers the firing
rule itself recorded, the entry rationale, and the reason string verbatim.

**Missing data is named, not hidden.** `unrecordedFields()` lists by name any
value a reader would expect but which was never captured. A blank space reads as
"nothing happened"; an explicit "trail percentage in force when the order filled
— not recorded" tells the operator the number is absent from the database and
cannot be recovered from that screen.

**The agent now records the risk state at exit.** `_exit_context_suffix()`
captures the trail, high-water mark and the date it was set, the implied trigger
price, hold day, peak excursion, armed state and power-hold flag, appending them
to the reason string:

```
Trailing stop (IBKR GTC TRAIL order) — trail 10.00%, HWM $52.02 set 2026-08-21,
implied trigger $46.82, day 2 of hold, peak +4.30%
```

This is appended to the existing free-text column rather than added as new
columns, so no schema migration is required and `trade_history` keeps its shape.

## Consequences

- Exit reasons are now always either recorded fact or explicitly declared
  missing. Nothing is derived from the outcome.
- The two views can no longer disagree, because there is only one classifier.
- Broker trailing-stop exits become fully explainable **from the next exit
  onward**. The 10 existing bare rows stay bare — the numbers are already gone —
  and the panel says so rather than pretending otherwise.
- The implied trigger price is labelled *implied*: it is reconstructed from the
  trail and the peak the agent last observed, not read from the broker. If the
  peak moved between the last 15-minute check and the fill, it is approximate.
- A same-day broker exit no longer reports a negative hold time. The reconcile
  path stamps `sell_date` as date-only midnight, so FROG (bought 13:45, stopped
  out the same afternoon) had an exit timestamp 13 hours *before* its entry.
  `holdDays()` compares calendar dates when the stamp carries no time.

## Guards

`verify-build.mjs` fails the build if `classifyExit` regains a second parameter,
if either view reintroduces a local `getCleanExitReason`, or if the panel
fingerprints vanish from the bundle. `frontend/scripts/test-exit-details.mjs`
(23 assertions, run by `npm run build`) and `tests/test_exit_context.py`
(18 assertions) pin both sides of the reason-string format, so a drift in the
agent's output fails a test rather than silently degrading the panel back to
"not recorded".

## Not done

`ai_evaluator.py:263` prompts the model with "Probability the stock hits +25%
WITHIN 2-6 WEEKS before -7% stop loss" — both figures describe rules the bot no
longer has, so the AI rating is being anchored to the wrong risk/reward frame.
Correcting it would change scoring behaviour on every candidate and needs its own
measurement, so it is tracked as FU-013 rather than changed here.
