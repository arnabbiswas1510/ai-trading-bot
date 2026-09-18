# Phase 1 rests on a STATIC stop, not a ratcheting trailing order

Date: 2026-09-18
Status: Accepted

## Context

`exit_rules.py` documents the Phase 1 Prove-It stop as a **fixed floor anchored
to entry** — "a breakout that fails on day one is wrong immediately and cheaply".
The docstring for `_compute_dynamic_trail_pct()` is explicit that the one-way
rule is "what turns the Prove-It lever into a FIXED floor rather than a trail".

It was not a fixed floor. `prove_it_trail_pct()` converted the Phase 1 level into
a **percentage**, and `place_protective_stops()` submits that percentage as
`orderType='TRAIL'`. An IBKR TRAIL order's anchor **ratchets up with the high
water mark**. The bot's one-way tightening rule then refused to widen it back.

So on any position that rallied before it faded, an order written to cap a
**loss** climbed into **profit** and fired as a profit-taker.

### The worked example — every number reconciles

SMTC, bought 2026-09-18 09:31, 102 shares @ $180.50, sold 09:54 for +$138.08.

| | |
|---|---|
| Intended resting level (entry −1% band, −1% slack) | **$176.91** (entry −2.0%) |
| `trailingPercent` solved at placement | **1.72%** — matches the logged `sell_reason` |
| High water mark reached 09:45 | **$185.30** |
| Ratcheted stop: 185.30 × (1 − 0.0172) | **$182.11** (entry **+0.89%**) |
| Actual fill | **$181.88** (entry +0.76%) |

A loss cap sold a winner 23 minutes into the trade. The position's own logged
reasoning was also wrong: it recorded "peak +0.27%" and "implied trigger $177.88"
because the stored `highest_price` only refreshes on the 15-minute cycle, and
this position did not live long enough for a second cycle.

Ten of 49 closed trades carry the same signature (day 0, trail 0.2–1.9%, peak
< 5%): MPC, PSX, FIVE, LPG#52, CHRD, GEO, ECO#58, CHRD#61, CDNA#62, SMTC.
Combined they netted **−$114** across ten round trips and ten occupied slots.
Every large winner in the book — LPG +$1,168, ECO +$1,025, MPC +$980 — survived
past day 0 and exited on the genuine 1.50% profit ladder instead.

## Measurement

`research/exit_rule_replay.py` modelled Phase 1 as `entry * (1 - pct)` — the
documented intent, not the shipped mechanism — so **it had never simulated this
defect**. A `p1_broker_leg` / `p1_ratchet` pair was added to `ExitConfig` (both
default off, so every pre-existing configuration replays byte-identically) and
validated against the live outcome: the model reproduces SMTC's fill at $181.61
against the actual $181.88, a 0.15% error arising because it solves the trail at
entry where the live order was placed ~$0.50 lower. The model therefore trails
slightly **wider** and **understates** the defect.

Over all 50 closed trades, changing only whether that leg ratchets:

| | Losers | Winners | Net | Harmed |
|---|---|---|---|---|
| A — ratchet ON (live behaviour) | +5,734 | +2,320 | +8,054 | 11 |
| B — ratchet OFF (documented intent) | +5,421 | +5,222 | **+10,644** | 8 |

**B − A = +$2,590**: +$2,902 recovered on winners, −$313 paid on losers.

Two independent confirmations that the mechanism is real:

- **Monotonic dose-response.** Narrowing the backstop slack makes the stop
  ratchet closer above entry and worsens winner damage exactly as the causal
  story predicts — winners score +$948 at 0.5% slack, +$2,320 at 1.0%, +$5,222
  at 2.0%.
- **B lands within $25 of the SHIPPED row**, which does not model the broker leg
  at all. A pinned backstop is nearly inert, which is what a backstop should be.
  The ratchet was the entire difference.

### What the measurement does NOT support

**+$2,590 is not a defensible expected value.** CPAY alone contributes +$2,409
of it. Ex-CPAY the net is **+$181, which is noise.** Only 5 of 50 trades are
affected at all. What survives the outlier is the *direction*, which is
unanimous: 3 of 3 winners helped, 2 of 2 losers slightly hurt.

**The fix is not free.** The prior hypothesis was that pinning the leg would
leave losers untouched or better. It does not: INCY (−$222) and PTGX (−$91) are
both losers that the ratcheting stop exited sooner. The trade-off is real, just
heavily lopsided — roughly 9:1 in favour of winners.

The decision therefore does **not** rest on the dollar figure. It rests on
correctness: the code contradicted its own documented intent, and a stop that
drifts to +0.9% above entry is not a loss cap under any reading. That is true at
n = 1.

## Decision

**Phase 1 is carried by the static `STP` leg that already exists in the same OCA
bracket, and is removed from the trailing leg entirely.**

1. `prove_it_trail_pct()` returns `None` for `phase1`. No TRAIL order carries a
   Phase 1 level, so nothing can ratchet. Phase 2 is unchanged — its level is
   peak-anchored and one-way *by design*; it is supposed to rise.
2. `hard_stop_price()` gains a `days_held` argument and returns the Phase 1 band
   one backstop-slack wider — `entry * (1 - p1_pct(days_held)) * (1 - slack)` —
   for **unproven positions only**, never looser than the disaster floor.
3. The buy-time placement seeds this same level instead of the bare disaster
   floor, computed once so the persisted column and the placed order cannot
   disagree.
4. The caller's ratchet-up-only rule gains one narrow exception: while
   **unproven**, the floor may follow the band down when it widens from the day-0
   to the day-1+ level, because the band itself widens by design. Once proven,
   ratchet-up-only is restored in full.

### Deliberately NOT extended to proven-but-unarmed positions

Giving that window the Phase 1 band is the `p2_unarmed_keeps_p1` hypothesis,
measured on 2026-09-10 and **rejected** (−$1,691, entirely DXCM) — see
`decisions/2026-09-10_prove-it-unarmed-window-measured-not-closed.md`. A first
draft of this change covered it incidentally; that was caught by
`test_proven_but_unarmed_uses_disaster_floor` and narrowed. That window keeps the
disaster floor until the re-run owed on the post-backfill sample says otherwise.

### A deployment hazard this exposed, and the guard added for it

Raising the Phase 1 floor from −7% to −2%/−4% means any **already-open** position
trading between the two levels would have had a stop placed **above the market**
on the next monitor cycle — which triggers instantly and liquidates at market.
All five open positions happened to be safe when this shipped, but that was luck.

`safe_hard_stop(desired, current_price, stored)` now refuses any level at or
above the market and keeps the resting stop instead, letting the bot-side exit
act — the same rule `prove_it_trail_pct()` already applies when its level is
through the price.

## Consequences

- A day-0 position that rallies and fades is no longer sold at a small profit by
  its own loss cap. It is held to the bot's −1% band on a 15-minute close, or to
  the static backstop at −1.99%.
- Broker-side protection in Phase 1 **improves**: the resting floor moves from
  the disaster level (−7%) to the entry band (−1.99% day 0, −3.97% day 1+), and
  is now genuinely disconnect-proof because it is static.
- Losers that rallied before fading will be exited slightly later, costing about
  $313 across the two affected trades in the current book.
- On first deploy, every open unproven position re-places its bracket once to
  move the static leg up. This is a tightening, guarded by `safe_hard_stop()`.
- The replay harness can now model the broker leg, so `--p1ratchet` is
  reproducible and the 2026-09-20 review can re-measure it on a larger sample.

## Follow-ups

- `FU-011` in `decisions/provisional_decisions.json`: re-measure at ≥ 60 closed
  trades and confirm the direction holds **without** CPAY carrying it.
- The stale-`highest_price` logging defect this surfaced is *not* fixed here. The
  bot's recorded peak for a short-lived position is materially wrong, which
  misleads any human reading `sell_reason`. Tracked separately.
