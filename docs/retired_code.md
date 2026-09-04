# Retired Code Registry

Every rule, constant or code path deliberately deleted from this repository is
recorded here **before** it is removed.

## Why this file exists

Deleted code is invisible. Once a rule is gone, the only trace left is a diff
buried in history that nobody will find, and the reasoning behind the removal
disappears entirely. Six months later someone re-derives the same idea, ships
it again, and reintroduces a bug that was already paid for once.

An ADR records *why a decision was made*. This registry records *what was
physically removed, where it lived, and how to bring it back with its original
context intact.* They are complementary — an ADR without this registry leaves
you knowing a rule was retired but not what its code actually did.

## Rules for this file

1. **Log before deleting.** The entry lands in the same commit as the removal.
2. **Link the ADR.** Every entry cites the decision that authorised it.
3. **Record the restore path.** Name the commit that still contains the code, so
   `git show <sha>:<path>` recovers it.
4. **Never delete entries.** This file only grows. A rule retired twice gets two
   entries.
5. **State what would have to be true to restore it.** "Never" is rarely the
   honest answer; "if the screener starts producing X" usually is.

---

## 2026-09-04 — Prove-It Stop consolidation

Seven exit rules retired at once, replaced by the two-phase **Prove-It Stop**.
See `decisions/2026-09-04_prove-it-stop.md` for the full reasoning and the
30-trade replay evidence.

Restore point: commit immediately preceding the Prove-It commit on `main`.

### 1. Intraday Loss Minimiser (ILM)

| | |
|---|---|
| **Constants** | `INTRADAY_MINIMISER_ENABLED`, `INTRADAY_PULLBACK_PCT`, `INTRADAY_MINIMISER_START_DAY` |
| **Location** | `execution_agent.py` — config block, monitor-loop exit block |
| **Also touched** | `telegram_notifier.py`, `frontend/src/lib/exitDetails.js`, `frontend/src/lib/positionRules.js`, `tests/` |
| **Status when retired** | Disabled by default since 2026-08-04 — dead code for a month |
| **ADR** | `decisions/2026-08-04_tune-exits-on-breakout-population.md` |

**What it did.** From Day 2 onward, sold on the first N% pullback from the day's
intraday high, provided that high was within 0.5% of entry or above.

**Why retired.** It was the single most damaging exit in the system. It sold
*developing winners* because the trigger required the high to be near entry —
exactly the condition a recovering position satisfies. Roughly halved expectancy
across two independent universes (broad 2,314 entries: +1.01% → +0.59%;
screener-passing 598 entries: +0.60% → +0.18%) and suppressed the right tail the
strategy depends on (+20% outcomes fell from 3.2% to 0.8% of entries).

It fired 3 times in live trading — GE, THC, TTWO — for a combined **−$768.72**.
All three were Day-3 verdict FAILs sold on a 0.5% wiggle.

**What would justify restoring it.** Nothing in its original form. If a
pullback-based intraday exit is ever wanted again, it must not require the high
to be near entry — that gate is what made it cut winners.

### 2. Trailing-stop time lever (`TRAIL_TIME_TIERS`)

| | |
|---|---|
| **Constants** | `TRAIL_TIME_TIERS_ENABLED`, `TRAIL_TIME_TIERS` |
| **Location** | `execution_agent.py` — config block, `_compute_dynamic_trail_pct()` |
| **Status when retired** | Disabled by default since 2026-08-04 — dead code |
| **ADR** | `decisions/2026-08-04_widen-exits-and-tighten-entries.md` |

**What it did.** Tightened the trailing stop purely as calendar time passed —
6.0% at day 8, down to 3.5% beyond day 30.

**Why retired.** It penalises a position for still working. On the 2,314-entry
breakout backtest, disabling it lifted expectancy +0.51% → +0.59% and payoff
1.44 → 1.58. Never fired in live trading.

**What would justify restoring it.** Evidence that hold duration alone predicts
reversal in the screener-passing population. The current evidence says the
opposite.

### 3. Early Loss Kill-switch

| | |
|---|---|
| **Constants** | `EARLY_LOSS_STOP_PCT`, `EARLY_LOSS_STOP_MAX_DAY` |
| **Location** | `execution_agent.py` — config block, monitor-loop "Day 0 hard loser kill-switch" |
| **Status when retired** | Active. Fired once (PSX, −$152.78) |
| **ADR** | `decisions/2026-08-20_early-loss-day0-tightening.md` |

**Why retired.** Not deleted so much as **absorbed**. Prove-It Phase 1 is the
same mechanism — an entry-anchored threshold that arms a tight trailing exit —
with the arbitrary "day 0 only" window removed. The kill-switch was correct but
stopped protecting the position at midnight on the entry day, which is why NBIX
(−$2,261) and DELL (−$1,283) ran unchecked.

### 4. Early Dollar Stop

| | |
|---|---|
| **Constants** | `EARLY_DOLLAR_STOP_PCT`, `EARLY_DOLLAR_STOP_MAX_DAY`, `EFFECTIVE_POSITION_SLOTS` |
| **Functions** | `early_dollar_stop_threshold()` |
| **Location** | `execution_agent.py`; mirrored in `frontend/src/lib/positionRules.js` |
| **Status when retired** | Active. **Never fired.** |
| **ADRs** | `decisions/2026-08-18_early-dollar-stop.md`, `decisions/2026-08-20_slot-derived-early-dollar-stop.md` |

**What it did.** Capped the unrealised dollar loss on an unconfirmed position at
`(equity / EFFECTIVE_POSITION_SLOTS) × 6%` ≈ $1,500 during days 0–5.

**Why retired.** Superseded by Prove-It Phase 1, which binds far earlier in every
case. The 2026-08-20 replay already showed the dollar stop was net harmful at its
original $500 setting; raised to a slot-derived ~$1,500 it became inert instead —
nothing ever reached that band without Phase 1 firing first. A rule that can only
fire after a better rule has already fired is not a backstop, it is dead weight.

**Bonus:** this removal retires **FU-007** in
`docs/tech_debt_and_requirements_tracker.md`. `EFFECTIVE_POSITION_SLOTS` existed
only to keep this stop's slot arithmetic honest; with the stop gone, the constant
and its pending migration to `MAX_POSITIONS` both disappear.

### 5. Thesis Stop

| | |
|---|---|
| **Constants** | `THESIS_STOP_ENABLED`, `THESIS_STOP_ATR_MULT`, `THESIS_STOP_START_DAY`, `THESIS_STOP_LAST_DAY`, `THESIS_STOP_ATR_FALLBACK` |
| **Location** | `execution_agent.py`; `telegram_notifier.notify_thesis_stop()`; `frontend/src/lib/positionRules.js` |
| **Status when retired** | Active. **Never fired.** |
| **ADRs** | `decisions/2026-08-09_thesis-stop.md`, `decisions/2026-08-17_thesis-stop-reexamination.md` |

**What it did.** Days 2–5, for positions that had never *closed* above entry: exit
once more than 1×ATR below entry.

**Why retired.** Prove-It Phase 1 asks the identical question — *has this ever
closed above entry?* — and answers it with a fixed 3% band instead of an
ATR-scaled one. Keeping both means two rules racing to cut the same population,
and the ATR version always loses the race. Its `closed_above_entry` latch is
**not** retired: it is now Prove-It's phase discriminator.

**What would justify restoring it.** Evidence that ATR-scaling the Phase 1 band
beats a fixed percentage. Genuinely untested — the rule never fired, so its
calibration is unmeasured, not validated.

### 6. EMA-21 Exit

| | |
|---|---|
| **Constants** | `EXIT_MA_TRIGGER_ENABLED`, `EXIT_MA_TYPE`, `EXIT_MA_WINDOW`, `EXIT_MA_BUFFER_PCT`, `EXIT_MA_EOD_ONLY` |
| **Functions** | `get_ma_value()` |
| **Location** | `execution_agent.py` — monitor-loop "Moving Average Exit Check" |
| **Status when retired** | Active. **Never fired.** |

**What it did.** From Day 7, sold at EOD if price closed below EMA-21 × 0.99.

**Why retired.** Dominated by Prove-It Phase 2 at every gain level. A position
above +5% is held to a 1.5% trail from its peak, which is far tighter than a 1%
undercut of a 21-day average; a proven position below +5% is held to the give-back
floor 1% under entry,
which is also tighter. There is no price path where EMA-21 fires first. It was
also suppressed during power-hold, so it could not act on the one population
where a slow-moving average might have added something.

### 7. Plateau (Stale) Exit

| | |
|---|---|
| **Constants** | `STALE_EXIT_ENABLED`, `STALE_EXIT_DAYS`, `STALE_EXIT_MIN_DAYS_HELD` |
| **Location** | `execution_agent.py` — monitor-loop "Plateau (Stale) Exit" |
| **Status when retired** | Active. **Never fired as a standalone exit.** |
| **ADR** | `decisions/2026-08-04_plateau-exit-capital-velocity.md` |

**What it did.** From Day 7, sold at EOD when no new high had been made in 10
trading days, to free the slot.

**Why retired as a standalone rule.** It sold to **cash**, which is the wrong
destination. The premise — a stalled position blocks a fresh breakout — is only
true if a fresh breakout actually exists. With Prove-It's give-back floor in
place, holding dead money costs almost nothing, so exiting to cash on a timer
gives up optionality for no gain.

**Not deleted — relocated.** The staleness signal now feeds Rank & Replace as a
threshold discount: a stale position swaps on a `RANK_REPLACE_FAIL_THRESHOLD` (5) point score gap instead of 15.
Same capital-velocity intent, but it can only fire when there is somewhere better
to put the money. `STALE_EXIT_DAYS` and `STALE_EXIT_MIN_DAYS_HELD` survive in
that role; only `STALE_EXIT_ENABLED` and the standalone exit block are gone.
