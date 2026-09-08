# A static broker-side hard stop that survives disconnection

**Date:** 2026-09-07
**Status:** Accepted

---

## Context

### The silent gap the disconnect incident exposed

On the day of the all-afternoon IBKR outage, every open position spent hours
protected by nothing but its **base trailing stop** — a peak-anchored GTC
`TrailingStopOrder` sitting 10–12% below the high-water mark. The bot's *tight*
protection — the Prove-It floor, which for a proven-and-armed position sits about
1% below entry — is **bot-enforced**. It is re-solved against the live price
every 15-minute cycle and expressed by re-placing the trailing order at a new
percentage. When the bot cannot reach the gateway, that re-solving stops, and the
only thing left at the broker is whatever trailing percentage was last written —
the wide base trail.

So the protection the dashboard advertised (a ~1% give-back floor on a proven
winner) was quietly **not there** during the one scenario where it mattered most.
A position could give back 10–12% while disconnected and the bot would never
know until it reconnected. This was a silent failure mode, not a loud one.

### Why the existing trailing stop cannot close the gap

The GTC `TrailingStopOrder` *is* broker-side and *does* survive a disconnect —
but it is a **trailing** order. On disconnect it freezes at its last-placed
*percentage* and keeps trailing the peak at that width. It structurally cannot
express a **fixed** price floor like "never below entry−1%", because the only
lever it has is a percentage below the running HWM. The ratcheting and arming
logic that turns that percentage into the Prove-It floor is inherently bot-side.

### Why not just make the trailing stop tighter

A tighter always-on *trailing* base (e.g. 5%) would trail the peak up and clip
winners on ordinary pullbacks. We measured this: see below.

## Decision

Add a **second, static** protective leg: a native `STP` order at a **fixed
price**, resting in the **same OCA group** (`ocaType=1`, cancel-with-block) as
the base trailing stop. A fill on either leg cancels the other, so the same
shares are never sold twice. Both legs are GTC, so both survive a gateway
restart.

The static leg's price is computed by `hard_stop_price()`:

- **Pre-proof / unarmed:** a flat **disaster floor** at `entry × (1 − MAX_LOSS_PCT)`,
  with `MAX_LOSS_PCT = 0.07` (7%).
- **Proven and armed** (position has CLOSED above entry *and* peak gain has
  reached `PROVE_IT_P2_ARM_GAIN_PCT` = +2%): it **ratchets up** to the give-back
  floor, `entry × (1 + PROVE_IT_P2_FLOOR_PCT) × (1 − PROVE_IT_BACKSTOP_SLACK_PCT)`
  ≈ `entry × 0.98` — one backstop slack wider than the bot's own Prove-It floor,
  so the broker order can never front-run the bot in normal operation but *does*
  guarantee the floor if the bot goes dark.

Two properties make this safe where a tighter trailing base would not be:

1. **It is static and entry-anchored.** It never chases the HWM upward, so it
   cannot rise into a winner and sell it on a normal pullback.
2. **It ratchets up only** (the sole exception being Power Hold, which — like the
   trailing stop — widens it back to the disaster floor so a genuine leader can
   run).

`place_protective_stops()` replaces the lone `place_trailing_stop()` on the buy
path, in the management/ratchet block, and in self-heal. `arm_exit()` still uses
the lone trailing order, because armed positions are managed separately and skip
self-heal.

## Backtest evidence

The replay harness (`research/exit_rule_replay.py`) models only *connected,
tight* behaviour — it has no disconnect scenario — so it cannot score the
disconnect benefit directly; that is a reliability property, not a P&L one. What
it *can* measure is the **normal-operation cost** of an always-on tighter base.

A `--basetrail` knob was added to model a GTC trailing base riding underneath all
Prove-It phases. Over the bot's own **30 closed trades** (18 losers, 12 winners,
−$13,864 realised):

| Always-on base trail | Net vs shipped | Winners |
|---|---|---|
| 12% / 10% / 8% / **7%** | identical (+$11,665) | +$4,973 — never binds |
| 5% | **−$1,941**, +1 harmed | +$3,032 — whipsaws winners |

So **7% is free** in normal operation (it never fires before the Prove-It floor
does), while 5% measurably clips winners. And because our hard stop is *static*,
not trailing, it is safer still than even the 7% trailing row: it cannot chase a
peak at all. Reproduce:

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
python3 research/exit_rule_replay.py --insecure --basetrail
```

## Consequences

- A new `MAX_LOSS_PCT` env var / `config.py` constant (default `0.07`).
- A new nullable `portfolio_positions.hard_stop_price` column
  (`migrations/add_hard_stop_price.sql`). All DB writes are PGRST204-tolerant so
  a lagging migration degrades gracefully (the hard-stop *order* is live at IBKR
  regardless; only the persisted mirror is skipped).
- Self-heal now expects **two** legs, not one; the `_bracket_replaced` guard
  prevents the management block and self-heal from double-placing in one cycle.
- The dashboard surfaces the hard floor as its own rule card
  (`frontend/src/lib/positionRules.js`).

## Provisional / to re-test

`MAX_LOSS_PCT = 0.07` is tuned on **30 closed trades**. It is the *widest* value
that is provably free in the replay; it is not proven to be the *best* narrow
floor for the disconnect case (the harness cannot see disconnects). Re-test it in
the scheduled exit-parameter reviews as the sample grows, and in particular check
whether a tighter static floor (5–6%) would still be free once more trades exist.

`PROVE_IT_BACKSTOP_SLACK_PCT = 0.01` remains unmeasured — set wide enough that
the resting order provably cannot front-run the bot, but no sweep supports the
exact value.
