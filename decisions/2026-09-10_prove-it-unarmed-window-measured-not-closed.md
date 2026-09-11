# Prove-It unarmed window: measured, and deliberately left open

- **Date:** 2026-09-10
- **Status:** Accepted
- **Supersedes:** nothing. Records a hypothesis that was tested and **rejected**.

## Context

`prove_it_stop_level()` has a branch that looks wrong on inspection:

```python
if prove_it_is_proven(pos, highest_unrealized_pct):
    if highest_unrealized_pct < PROVE_IT_P2_ARM_GAIN_PCT * 100.0:
        return None, "phase2-unarmed"      # no Prove-It level at all
    return buy_price * (1.0 + PROVE_IT_P2_FLOOR_PCT), "phase2"
return buy_price * (1.0 - prove_it_p1_threshold_pct(days_held)), "phase1"
```

Becoming "proven" **removes** the Phase 1 entry-anchored band but grants no Phase 2
floor until the peak reaches +2%. So a position that closes one cent above entry
is left with only the static hard stop at −7% (`MAX_LOSS_PCT`), where a moment
earlier it had a 3% band. Protection gets **worse** as a result of good news.
That is non-monotonic, which is normally a defect rather than a tuning choice.

### The trade that prompted this

NTRA, 26–31 Aug 2026, 40 shares at 338.43 — round trip 1 of three, the one that
lost **−$706.66**. (It is invisible in `trade_history`; it was recovered from the
IBKR TradeConfirm Flex statement. See
`decisions/2026-09-10_lot-basis-and-broker-aware-cooling-off.md`.)

| Date | Event |
|---|---|
| 8/26 | buy 338.43; high 343.18 → peak gain **+1.40%** |
| 8/27 | close **338.70** — $0.27 above entry → latched proven |
| 8/28 | close −3.60%, low 325.66 |
| 8/31 | gap open 320.95; sold 09:35 at 320.82 (−5.2%) |

It was proven under *either* reading of the predicate — the close-based latch and
the touch-based fallback both fire. Peak gain +1.40% never reached the +2% arm.
So it spent its whole life in the unarmed window. Its Phase 1 band would have
been **328.28**; its Phase 2 floor would have been **335.05**; what it actually
had was the −7% hard stop at **314.74**. Both the 8/28 low (325.66) and the 8/31
low (316.00) breached 328.28 and neither breached 314.74.

The obvious fix is to floor the unarmed window at the Phase 1 band, so protection
can never loosen.

## Decision

**Do not close the window.** Keep `phase2-unarmed` exactly as it is.

The fix was implemented in the replay harness and measured against all 39 closed
trades. It loses money, and the loss discipline of this project is that an exit
change ships only if it reduces `worst`/`>300` without increasing `harmed`.

Reproduce with:

```bash
python3 research/exit_rule_replay.py --insecure --cliff
```

| Configuration | losers | winners | NET | worst | >300 | harmed |
|---|---|---|---|---|---|---|
| **Prove-It SHIPPED** | 6,687 | 5,111 | **11,798** | −1,269 | 10 | 8 |
| Unarmed keeps P1 band (the fix) | 6,687 | 3,420 | 10,107 | −1,269 | **11** | 9 |
| P2 arms at +1.5% instead of +2% | 6,658 | 5,111 | 11,769 | −1,269 | 9 | 9 |
| P2 arms at +1.0% | 6,658 | 3,917 | 10,575 | −1,269 | 10 | 10 |
| P2 arms at +0.5% | 6,658 | 3,332 | 9,990 | −1,269 | 10 | 11 |

It fails on every column: **−$1,691** net, `>300` rises 10 → 11, `harmed` rises
8 → 9, and `worst` does not move.

### Why it fails

Two findings, and the second is the more important one.

1. **The cost is one trade.** The entire −$1,691 is DXCM: delta **−$1,475**,
   turning a realised **+$729 winner into a −$746 loser**. Every other per-trade
   delta is identical to shipped. DXCM closed marginally green, dipped through
   the 3% band, and the restored floor armed it out before it ran.

2. **The benefit is zero.** The `losers` column is **6,687 under both** — not
   close, identical. Across 23 losing trades, restoring the Phase 1 band in the
   unarmed window rescues **not one dollar**. The window is not where this book
   loses money.

Arming earlier is the other way to close the same hole — it shortens the
unprotected window rather than flooring it — and it behaves the same way: +1.5%
is break-even but harms one more trade, and anything tighter gives back winners.

### The losses are gaps, not stop levels

`losers = 6,687` recurs across every loss-cutting variant tested, in this sweep
and in the `--eod` sweep run the same day. No tested configuration extracts more
than $6,687 from the 23 losers; several extract less. Flushing armed positions at
the close to dodge overnight gaps — the mechanism that actually converted NTRA's
−3.0% band into a −5.2% realised loss — scores **−$1,962 to −$3,321** against
shipped, again entirely out of winners:

| Configuration | losers | winners | NET |
|---|---|---|---|
| Prove-It SHIPPED | 6,687 | 5,111 | **11,798** |
| EOD 0.75% [intraday anchor, backstop 3%] | 6,687 | 3,150 | 9,836 |
| EOD 0.75% [intraday anchor, no backstop] | **6,159** | 2,593 | 8,752 |

NTRA's extra 2.2% of loss was a gap from a 326.20 close to a 320.95 open. A
bot polled every 15 minutes cannot be inside that move, and the configurations
that try to pre-empt it pay more for the attempt than it costs.

`worst` is −$1,269 (APH) under **every** configuration in both sweeps. It is a
gap too, and nothing tested moves it.

## Consequences

- `prove_it_stop_level()` is **unchanged**. No production behaviour changes.
- `research/exit_rule_replay.py` gains an `ExitConfig.p2_unarmed_keeps_p1` knob
  and a `--cliff` sweep, so the negative result stays reproducible rather than
  being re-derived from scratch the next time someone reads that branch.
- `docs/sell_logic.md` now states that the window is a known, measured and
  deliberately accepted gap, so the code no longer reads as an oversight.
- This is the third loss-cutting rule this book has rejected for the same reason,
  after the Early Dollar Stop and the Thesis Stop (`docs/retired_code.md`).
  Stop-level tightening trades winner upside at roughly 1:1 and this book does
  not have the loss profile to pay for it.

## The honest answer to "cap my loss at $300 per trade"

Not reachable through exit rules. A −5.2% gap on a $13,537 position is −$704, and
every attempt to cut sooner measured worse. The lever that does work is
**position size**: the same 3% band on a $10,000 position is a $300 loss. That
scales losses and gains down together — it lowers risk, it does not improve
expectancy. Raising `MAX_POSITIONS` from 4 is how that would be done, and it is a
separate decision that should be measured on its own.

## Caveat on the evidence

The replay cannot see NTRA round trip 1. That exit never reached `trade_history`
— the data-integrity defect fixed the same day — so the row labelled `NTRA` in
the sweep is the contaminated composite, not the −$706.66 trade under discussion.
The trade that motivated the fix is therefore **absent from the sample that
rejected it**. Crediting the fix generously with saving the full difference
between a −3% exit and the −5.2% realised (~$300) still leaves it net −$1,391, so
the conclusion holds; but this should be re-tested once round trips are recorded
individually and the sample has grown. Added to the scheduled exit-parameter
review.
