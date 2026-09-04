# Early Dollar Stop becomes slot-derived, not a flat dollar amount

**Date:** 2026-08-20
**Status:** Superseded by [`2026-09-04_prove-it-stop.md`](2026-09-04_prove-it-stop.md)
**Supersedes the sizing decision in:** `decisions/2026-08-18_early-dollar-stop.md`
(the rule itself, its day window and its arm-don't-sell mechanism are unchanged)

> **2026-09-04 — retired.** `EARLY_DOLLAR_STOP_PCT` and
> `EFFECTIVE_POSITION_SLOTS` are both deleted. The slot-derived cap this ADR
> introduced was never reached in 30 closed trades. Deleting it also closes
> FU-007 (retire `EFFECTIVE_POSITION_SLOTS`) without the portfolio reset that
> item was waiting on, since the constant no longer has a consumer.

## Context

`decisions/2026-08-18_early-dollar-stop.md` introduced a flat
`EARLY_DOLLAR_STOP_AMOUNT = $500` cap on unrealized loss during days 0–5. Two
days later a full-stack replay of the 17 closed trades
(`research/exit_rule_replay.py`) showed the rule was net harmful **as
configured**, and investigating why exposed a design flaw underneath the
parameter.

### 1. The measured problem

The day-0 kill-switch was tightened to 1.0% on 2026-08-20. It now reaches every
loser the dollar stop used to catch (HWM, DELL, RSI) a day earlier and more
cheaply. What remains uniquely attributable to the dollar stop is winner damage:

| Stack | All-in net vs realised exits |
|---|---|
| Previous (2.0% days 0–1 + $500 + 1×ATR) | −$1,603 |
| Shipped (1.0% day 0 + $500 + 1×ATR) | −$272 |
| 1.0% day 0 + **$1,500** + 1×ATR | **+$3,027** |
| 1.0% day 0 + 1×ATR (dollar stop removed) | +$3,027 |

At $500 the rule cut CPAY (−$1,873) and DXCM (−$1,367); both recovered and closed
as winners. The +$2,936 figure in the 2026-08-18 ADR was measured before the
kill-switch tightening, so it credited the dollar stop with saves that now
happen sooner regardless.

$1,500 and "removed" score identically because at $1,500 the rule never fires in
this sample.

### 2. The design flaw the parameter was hiding

Positions are sized `available_cash / remaining_slots`, so they cluster tightly
around one slot's worth of capital — the 21 closed trades ran $20,025 to $48,189
with a $24,415 median. A flat dollar cap is therefore a percentage in disguise,
and an unstable one:

| Position | Cost basis | $500 as % |
|---|---|---|
| OII | $48,189 | 1.0% |
| median | $24,415 | 2.0% |
| PTGX | $20,025 | 2.5% |

Same rule, materially different aggression, determined by nothing more
principled than how much cash happened to be free that morning.

A flat figure also decays silently: $500 is 2.0% of a $25K slot today and 1.0%
of a $50K slot after the account doubles. The rule becomes twice as aggressive
with no code change and no signal that anything happened.

### 3. Why the rule is kept rather than removed

The base trailing stop is the only other cover on days 1–5, and it is measured
from the **peak**, not entry. Live values are 8.25–10%; a $1,500 cap is
5.4–7.8% of the corresponding positions, so it sits strictly inside the trail on
every open position. It is not redundant — there is a real band where it is the
only thing acting, and on a position that never rises the peak-anchored trail is
far too wide to help.

## Decision

Replace the flat amount with a slot-derived threshold:

```
threshold = (NetLiquidation / EFFECTIVE_POSITION_SLOTS) x EARLY_DOLLAR_STOP_PCT
```

- `EARLY_DOLLAR_STOP_PCT = 0.06` (new) — replaces `EARLY_DOLLAR_STOP_AMOUNT`
- `EFFECTIVE_POSITION_SLOTS = 4` (new) — see below
- 6% of a ~$25K slot reproduces the ~$1,500 the replay selected

Resolved **once per monitoring cycle**, not per position: the value is identical
for every holding and each lookup is an IBKR round-trip.

### Why slot-derived rather than a percentage of the position's own cost basis

A cost-basis percentage would hand an oversized position a proportionally wider
dollar allowance. OII was booked at roughly double the median; under a
cost-basis rule it could lose twice as many dollars as everything else, when
concentrated risk is precisely what should be cut sooner. Slot-derived gives
every position the **same absolute dollar cap**, which is what the original flat
$500 was trying and failing to express.

### Why `EFFECTIVE_POSITION_SLOTS = 4` and not `MAX_POSITIONS`

`config.MAX_POSITIONS` is 5, but that is currently aspirational. The four open
positions were each bought as a quarter of capital, so no cash remains to fill a
fifth slot; sizing will not converge on 5 until the portfolio is liquidated and
rebuilt. Dividing equity by 5 today would understate the real slot size by 20%
and make the stop 20% tighter than intended.

**This constant must be replaced by `MAX_POSITIONS` once the portfolio has been
reset at 5 slots.** Tracked as FU-007 and flagged in the AGENTS.md review
schedule.

### Fail-safe

`get_net_liquidation()` returns 0.0 when equity cannot be read, and
`early_dollar_stop_threshold()` maps that to 0.0, which **disables** the rule for
that cycle. A 0.0 threshold treated as live would read as "every position has
already breached the cap" and arm an exit on the entire book on any cycle where
the IBKR account query failed. `test_rule_is_skipped_when_equity_is_unreadable`
pins this.

## Consequences

- The cap now tracks account growth instead of tightening silently.
- Every position gets the same dollar cap regardless of its own size.
- The effective cap rises from $500 to ~$1,500 at current equity, which is the
  change the replay actually asked for.
- The rule is disabled for any cycle where equity is unreadable — a monitoring
  cycle with a degraded IBKR connection loses this layer, and the kill-switch,
  thesis stop and IBKR-side trailing stop continue to cover.
- Dashboard tooltips and the phase pill now show the resolved dollar figure and
  the slot arithmetic behind it, so the number is never unexplained.

## Open caveats

- The sample cannot distinguish 6% from any larger value: nothing reached that
  band without the day-0 kill-switch having already fired. 6% is an upper bound
  justified by the base trail sitting above it, not a measured optimum.
- The winner damage that motivated the retune rests on two trades (CPAY, DXCM).
- Re-measure at the 2026-09-20 review — FU-004.
