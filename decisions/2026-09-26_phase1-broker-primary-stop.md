# Phase 1 resting broker STP sits AT the band — IBKR is the primary enforcer

- **Date:** 2026-09-26
- **Status:** Accepted (provisional — 66-trade sample; registered for revisit)
- **Supersedes in part:** the Phase 1 half of
  `decisions/2026-09-18_phase1-static-backstop.md` (the static-STP shape is kept;
  only its *resting level* changes) and the "keep the broker 1% behind the bot"
  reasoning in `docs/configuration.md`.

## Context — the loss that triggered this

On 2026-09-22 ECO and TNK gapped **down** at the open and were sold for roughly
−$1,250 and −$1,100. Both were unproven Phase 1 positions. The tight Prove-It
Phase 1 stop that should have capped them is enforced by the execution agent's
**15-minute poll**, which only runs during market hours and only reacts to a bar
*close*. Overnight and at the opening print it is blind. The only order actually
resting at the broker overnight was the Phase 1 static `STP` — but it was
deliberately parked one `PROVE_IT_BACKSTOP_SLACK_PCT` (1%) **below** the band, as
a pure outage/gap backstop, so the bot-side armed exit would fire first in normal
operation. On a hard gap-down the position blew through the poll's reach and the
backstop only caught it 1% lower than the band it was supposed to defend.

## What was measured

`research/exit_rule_replay.py` replays the bot's own 66 closed trades on 5-minute
bars, reproducing the live mechanics. Two runs are relevant:

- `--day0` **"ProveIt + BROKER-HARD all Phase 1 days"** (resting STP AT the band,
  touch/gap sensitive, no arming): **NET +$10,609** vs the exits that actually
  happened, **worst single loss −$1,150**, >300 losses 17.
- `--p1ratchet` **"P1 broker leg ratchet OFF (documented intent)"** — the faithful
  model of the *current* live Phase 1 (static resting STP at `band × (1 − slack)`
  **plus** the bot poll+arm): **NET +$9,083**, worst single loss **−$1,418**.

The honest incremental gain — broker-primary-at-band **minus** the faithful model
of what is live today — is **+$1,526**, and the worst single-trade loss improves
from **−$1,418 to −$1,150**. It is not carried by one trade: on the `--day0` run
30 trades are helped and 16 harmed, the largest single contributor is CDNA at 17%
of the net, and the net excluding the largest is still +$8,814.

> **Why not quote the +$1,418 vs the `SHIPPED` baseline?** `shipped_proveit()` in
> the harness models Phase 1 as **bot poll + arm only** — it gives shipped no
> credit for its *existing* resting backstop STP, so measured against it the gain
> looks like +$1,418 but part of that is the baseline ignoring a leg that is
> really there. Cross-checking against `--p1ratchet`, which *does* model the
> resting leg, gives the honest +$1,526. Both point the same way.

## Decision

In `hard_stop_price()` (`exit_rules.py`), the **Phase 1 (unproven)** resting level
changes from

```
band × (1 − PROVE_IT_BACKSTOP_SLACK_PCT)      # 1% below the band
```

to the band itself:

```
band = entry × (1 − prove_it_p1_threshold_pct(days_held))
return round(max(disaster, band), 2)
```

IBKR is now the **primary** enforcer of the Phase 1 stop: the resting `STP` sits at
`entry − 1%` (day 0) / `entry − 3%` (day 1+) and fills at the band on a gap-open or
intraday touch. The bot's 15-minute poll + `arm_exit()` remains in place as a
**fallback** — it still fires when a level cannot be placed at the broker (e.g.
`safe_hard_stop()` blocks a stop that would land above the market). No change to
Phase 2: the armed give-back floor still sits one `PROVE_IT_BACKSTOP_SLACK_PCT`
below the floor.

## Why this is safe

- The leg is still a **static `STP`**, entry-anchored — it cannot chase the HWM up
  and clip a winner. The 2026-09-18 SMTC regression (a trailing anchor turning a
  loss cap into a profit-taker) does not recur.
- It is never placed at or above the market (`safe_hard_stop()`), and never looser
  than the entry − `MAX_LOSS_PCT` disaster floor.
- Proven-but-unarmed positions are unchanged (still the disaster floor; the
  `p2_unarmed_keeps_p1` hypothesis stays rejected).

## Honest caveats

- **Wick sensitivity.** A static `STP` at the band fills on *any* touch, including a
  fast intraday spike-down-and-recover that a 15-minute *close* poll would have
  ridden through. The replay measures this only to 5-minute granularity, so a
  sub-5-minute wick shakeout is **not** fully captured — the harness may be
  slightly optimistic. This is the deliberate cost of broker-side enforcement, and
  it is the direction the operator has repeatedly asked for ("all sell orders on
  the broker side, not the Python side"). The gap losses being defended against
  ($1,000+) dwarf a typical wick-shakeout cost.
- **Sample.** 66 closed trades, all in a BULL-tape window. Registered for revisit
  as more trades and more gap events accumulate.

## Consequences

- Phase 1 losses are capped ~1% tighter and gap-protected at the band.
- Expect marginally more intraday stop-outs on noisy names (the wick cost above).
- `PROVE_IT_BACKSTOP_SLACK_PCT` now governs **only** the Phase 2 armed floor.
