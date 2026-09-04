# The Prove-It Stop — one loss rule replaces five

**Date:** 2026-09-04
**Status:** Accepted
**Supersedes:**
`decisions/2026-08-01_early-loss-killswitch-and-day2-universal-minimiser.md`,
`decisions/2026-08-04_plateau-exit-capital-velocity.md`,
`decisions/2026-08-09_thesis-stop.md`,
`decisions/2026-08-17_thesis-stop-reexamination.md`,
`decisions/2026-08-18_early-dollar-stop.md`,
`decisions/2026-08-20_early-loss-day0-tightening.md`,
`decisions/2026-08-20_slot-derived-early-dollar-stop.md`

---

## Context

### The loss pathway

Four of the bot's worst closed trades lost four figures each: NBIX −$2,261,
HWM −$1,463, RSI −$1,390, DELL −$1,283. All four failed the same way. They never
worked, the Early Loss Kill-switch stopped looking after day 0, and from day 1
the only thing left holding them was a **peak-anchored** trailing stop of
8.25–12%. A position that never rose has no peak worth anchoring to, so that
stop sat far below entry and the loss was allowed to compound for days.

Every rule written to close that gap was scoped to a *window*:

| Rule | Window | Anchor | Times it fired in 30 closed trades |
|---|---|---|---|
| Early Loss Kill-switch | day 0 only | entry | 1 |
| Intraday Loss Minimiser | day 2+ | intraday high | 3 (then disabled) |
| Early Dollar Stop | days 0–5 | dollars at risk | **0** |
| Thesis Stop | days 2–5 | entry ± ATR | **0** |
| EMA-21 exit | day 7+ | EMA-21 | **0** |
| Plateau exit | day 7+ | time | **0** |

The Early Dollar Stop and the Thesis Stop never fired *once*, because whenever a
position was bad enough to trigger them the kill-switch had already caught it on
day 0. They were not redundant safety — they were unreachable code that still
had to be reasoned about on every read of the monitor loop.

Meanwhile the actual gap — day 1 onward, for a position that never confirmed —
had no owner at all.

### The insight

Five rules were five different answers to two questions:

1. **Has this breakout confirmed?** If it never even closed above what we paid,
   there is nothing to be patient about.
2. **Did it go green and then give it back?** If so, it must not be allowed to
   become a real loss.

One latch already in the schema — `closed_above_entry`, built for the Thesis
Stop — answers the first question definitively. Everything else follows from it.

---

## Decision

Replace the Early Loss Kill-switch, Intraday Loss Minimiser, Early Dollar Stop,
Thesis Stop, EMA-21 exit and Plateau exit with **one rule in two phases**, keyed
on whether the position has ever CLOSED above entry.

### Phase 1 — unproven. Anchor to ENTRY.

| Day | Band below entry |
|---|---|
| 0 | 1.0% (`PROVE_IT_P1_DAY0_PCT`) |
| 1+ | 3.0% (`PROVE_IT_P1_LATER_PCT`) |

**No expiry.** This is the whole point: the gap that produced the four-figure
losses was a rule set that stopped looking.

### Phase 2 — proven. Anchor to the PEAK.

Once the position has closed above entry it earns patience. Once its peak gain
reaches **+2.0%** (`PROVE_IT_P2_ARM_GAIN_PCT`) a give-back floor arms at **1.0%
below entry** (`PROVE_IT_P2_FLOOR_PCT`). A trade that went green never becomes a
real loss.

Above +5% the existing profit ladder (`TRAIL_PROFIT_TIERS`, 1.5% from the high
water mark) is tighter than the floor and takes over. It is unchanged.

### Mechanism

Both phases call `arm_exit()` — a tight 0.6% trailing exit with a 3.25h deadline
— rather than market-selling into what is usually a local trough. In the replay
the armed exit beats an immediate market sell by roughly **$600** across the
sample.

A resting IBKR GTC order backs it up so an overnight gap is capped even when the
agent is offline. In Phase 1 that order sits `PROVE_IT_BACKSTOP_SLACK_PCT` (1%)
**wider** than the trigger so it can never front-run the bot. In Phase 2 the
resting order *is* the floor.

`prove_it_trail_pct()` solves `1 − (level / current_price)` and feeds the result
into the existing one-way `min()` ratchet in `_compute_dynamic_trail_pct()`.
Because the ratchet only ever tightens, a rising price produces a looser required
percentage (rejected — the stop stays put) while a falling price produces a
tighter one (accepted — the stop is pinned on the floor). That is what turns a
percentage trail into a **fixed price floor** with no new order type.

Note that IBKR resets the `trailingPercent` anchor on cancel-and-replace — which
is exactly what the tightening block does — so the percentage must be solved
against the *current* price, never a historical peak.

---

## Evidence

A 5-minute-bar replay of **all 30 closed trades**, reproducing the live
mechanics: 15-minute check cadence, `arm_exit()` 0.6% trail, 3.25h deadline.
`research/exit_rule_replay.py --proveit`.

| | Net |
|---|---|
| What actually happened | **−$6,548** |
| The rules shipped before this change | −$4,069 |
| **Prove-It** | **+$5,410** |

- **Zero winners cut short.** CPAY *improves*, to +$1,907.
- Worst single loss falls from **−$2,002 to −$1,140**.
- Every intraday bleed is cut small: NBIX −$2,261 → −$230, CDNA −$1,539 → +$256,
  RSI −$1,390 → −$197.

### Phase 1 and Phase 2 are complementary, not additive

Neither half works alone, which is why both shipped together:

| Configuration | Effect |
|---|---|
| Phase 1 only | **Harms winners** (−$2,271) |
| Phase 2 only | Worst loss still **−$2,261** |
| Both | Raises net **and** cuts the worst loss |

### Why Phase 1 WIDENS after day 0 rather than tightening

Counter-intuitive, and measured. Holding the tight 1.0% band through day 1 costs
roughly **$1,500–2,000** in winner damage. CPAY closed −2.24% on day 1 with a low
of −2.88%, then ran to +8.95%. Day 0 is the only day on which the failing and the
working populations separate cleanly; from day 1 they overlap.

### Why the Phase 2 floor sits 1% BELOW entry, not at it

An exact-breakeven floor flushes any position that pokes green and immediately
retests entry. CPAY did exactly that on day 4 (high +3.60%, low −0.41%); an
at-entry floor sold it for $0 and forfeited +$1,189. One percent of slack is the
difference between the floor protecting winners and clipping them — it turns CPAY
into +$1,907 while still catching FRO and CDNA.

---

## Two harness bugs found and fixed

The first set of results was wrong, and was caught only because the numbers were
compared against the live dashboard. Both bugs are fixed in
`research/exit_rule_replay.py`; recording them here because either one would
silently corrupt any future replay.

1. **Zero-width fetch windows.** `hydrate()` requested bars between `buy_ts` and
   `sell_ts`. For a same-day round trip those are the same timestamp, so the
   request returned nothing and the trade was silently dropped. It dropped OII,
   FROG and APH — **all losers, −$2,583 in total** — which flattered every
   result. Fixed by widening the window to whole-day boundaries.

2. **Split-adjusted bars.** APH did a 2-for-1 split. The bar feed is
   split-adjusted; the recorded entry price is not. The mismatch produced a
   fictitious **−$11,650** loss. Fixed with an explicit `_SPLIT_RATIOS` table
   and `_correct_split()`.

After both fixes the replay reproduces the realised total **exactly**: −$6,547.59,
matching the dashboard to the cent. That agreement is the only reason the rest of
these numbers should be believed.

---

## What is explicitly NOT claimed

- **There is no $300 loss cap.** No configuration tested achieves one. The
  −$1,140 APH loss is an overnight gap and no stop of any kind prevents it. An
  earlier promise in this session that four-figure losses were eliminated was
  withdrawn once the corrected data landed.
- **INCY gets worse**, −$137 → −$429. The rule is a net improvement across the
  distribution, not a Pareto improvement on every trade.
- **n = 30.** This is a small sample. Every parameter here is provisional and is
  entered into the scheduled exit-parameter review in `AGENTS.md`.
- **Power Hold's new +10% trigger is entirely unvalidated.** No trade in the
  sample reached +10% within 21 days, so the replay is silent on it.

---

## Addendum, 2026-09-04 — Phase 1 enforcement side, measured

The question was raised whether Phase 1 (and the day-0 1% band in particular)
should be enforced by a **resting IBKR stop sitting on the level** rather than by
the agent's 15-minute cycle. The concern is legitimate: bot-side enforcement
samples price 26 times a session instead of watching it continuously, and a
breach *arms* a 0.6% trail rather than selling, so the day-0 1% is a trigger and
not a loss cap. Realistic worst case on day 0 is 2–2.5%, unbounded on a fast
move.

`research/exit_rule_replay.py` gained a `p1_hard_max_day` mechanism and a
`--day0` config set to settle it against all 30 closed trades.

| Configuration | Net vs realised | Worst loss | > $300 |
|---|---|---|---|
| **Shipped (bot poll + arm)** | **+$11,665** | −$1,269 | 9 |
| Broker-hard day 0 @ 1.0% | +$11,262 | −$1,269 | 9 |
| Broker-hard day 0 @ 1.5% | +$10,354 | −$1,269 | 13 |
| Broker-hard day 0 @ 2.5% | +$9,513 | −$1,269 | 13 |
| Broker-hard *all* Phase 1 days | +$11,852 | −$770 | 9 |

**Decision: keep Phase 1 bot-enforced. No code change.**

- Hardening **day 0 only** costs $403 and does not move the worst loss by a cent.
  Widening the hard band degrades it monotonically: a resting stop fires on wicks
  a 15-minute close ignores, and in this sample those wicks were noise.
- The armed trail more than pays for the sampling gap. Seven of the thirteen
  Phase 1 exits were *better* under arming — HWM −$579 → −$447, LPG −$231 → −$92,
  RSI −$252 → −$202.
- Hardening **all** of Phase 1 appears to win (+$187, worst loss −$770) but is
  **rejected as a single-trade artefact**. Per-trade, it hurts 9 trades and helps
  4; the entire net is APH alone (−$1,269 → −$665), the overnight-gap trade where
  a resting order fills on the gap open before the agent's first poll. Excluding
  APH the configuration *loses* $418. This is exactly the "carried by one trade"
  failure mode the scheduled review in `AGENTS.md` warns about.
- **Winners are untouched by this choice.** Every Phase 2 exit reason is
  byte-identical across all variants; the whole difference is confined to losers.

APH does point at a real gap-risk gap, but on n=30 that is one trade of evidence.
If it is ever revisited, the targeted fix is a wider always-resting backstop, not
a tighter day 0. Added to the scheduled exit-parameter review.

Reproduce: `python3 research/exit_rule_replay.py --day0`

---

## Consequences

### Retired

Six rules and their configuration, all logged with restore conditions in
`docs/retired_code.md`: Intraday Loss Minimiser, `TRAIL_TIME_TIERS`, Early Loss
Kill-switch, Early Dollar Stop, Thesis Stop, EMA-21 exit, Plateau exit.
`execution_agent.py` drops from ~4,670 to ~4,100 lines.

`EFFECTIVE_POSITION_SLOTS` is deleted with the Early Dollar Stop that was its
only consumer. This closes FU-007 without the portfolio reset it was waiting for.

### Kept, with a changed job

- **`closed_above_entry`** survives as the phase discriminator. It fails SAFE: a
  missing column counts as *proven*, never as unproven, because applying the
  tight entry-anchored band to a working position is the more expensive error.
- **Staleness** (`STALE_EXIT_DAYS`) no longer sells to cash. With the give-back
  floor in place, holding dead money is nearly free, so staleness now only
  **discounts the Rank & Replace margin** to `RANK_REPLACE_FAIL_THRESHOLD`. The
  slot is released when somewhere better to put the money exists — not merely
  because this position stopped moving. This discount is unmodelled by the
  harness.
- **Power Hold** stays and still suppresses the Prove-It Stop, but its trigger
  drops from +20% to **+10%**: the realised distribution contains no +20%
  runners, so at 20% the rule was unreachable.

### Frontend

`frontend/src/lib/positionRules.js` mirrors the new constants and exposes one
`prove_it` rule in place of three. The lifecycle track is no longer indexed by
calendar day — it is indexed by **proof**, because a position can now sit in
Phase 1 indefinitely. Component code was not changed; it iterates whatever the
library returns.

`frontend/src/lib/exitDetails.js` **keeps** every retired rule's matcher, marked
as retired. Trade history still contains those `sell_reason` strings and must
keep rendering correctly forever.
