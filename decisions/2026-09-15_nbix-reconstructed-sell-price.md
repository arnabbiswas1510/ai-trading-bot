# NBIX sell price was reconstructed from the wrong day's FMP quote

**Date:** 2026-09-15
**Status:** Accepted — applied to production `trade_history` on 2026-09-15

## Context

An operator reconciliation of the bot's ledger against IBKR TradeConfirm found a
single large disagreement:

| | Sell price | Sell date | Proceeds | P&L |
|---|---|---|---|---|
| `trade_history` id 29 | 152.7400 | 2026-08-13 | $22,147.30 | −$2,260.55 |
| IBKR actual (82 @ 158.50 + 63 @ 158.51) | **158.5043** | **2026-08-12** | $22,983.13 | **−$1,424.72** |

The share count matched exactly (82 + 63 = 145), so this was a pricing error, not
a missing or duplicated lot.

**The recorded price was not a possible fill.** NBIX traded 154.86–159.98 on
2026-08-12. $152.74 lies inside the *2026-08-13* range (150.20–155.81). The bot
recorded both the wrong price and the wrong day.

NBIX was, at the time, the single largest loss in the book.

## What went wrong

The row documented its own failure in `sell_reason`:

> "Trailing stop (IBKR GTC TRAIL) fired ~2026-08-13 — RECONSTRUCTED, NOT manual;
> SLD fill missed by all 3 tiers. … sell_price $152.74 = FMP ESTIMATE; verify via
> IBKR Flex."

All three tiers of the sell-price ladder in `reconcile_with_ibkr()` failed at
once, and the FMP fallback fired:

| Tier | Source | Why it missed |
|---|---|---|
| 1 | `ibkr_fills` table | **Silently inert.** The `execDetailsEvent` hook was being rejected by Supabase RLS — 62 consecutive denials left the table empty for six weeks with no alert. Its earliest surviving row is 2026-09-08; nothing exists for August. |
| 2 | `reqExecutions()` | Session cache only. The stop filled 2026-08-12 and reconcile observed it in a later session; prior-session fills are not cached. |
| 3 | Flex TradeConfirm | Unavailable at the time. |
| — | FMP live quote | Fired, and returned a quote from the **wrong day**. |

This is the **second** occurrence of this bug class. The first was RSI on
2026-07-17, and it is the reason the three-tier ladder exists at all — see the
comment block at `execution_agent.py` ~L2934. NBIX still slipped through, because
the ladder was designed against the case where Tier 1 is *unlucky*, not the case
where Tier 1 is *inert and not saying so*.

That silent-sink problem has since been fixed independently: `_fill_sink_failure()`
now escalates write failures instead of only printing, with the rationale stated
in-line — *"a write sink nobody reads must escalate its own failures."*

## Decision

Re-price row 29 from the authoritative fills: `sell_price = 158.5043448276`,
`sell_date = 2026-08-12`, `profit_loss = −1424.72`, `percent_return = −5.84`.

Unlike `20260910_backfill_ntra_round_trips.sql`, **this changes the book total**, because
the recorded price was simply wrong rather than misattributed:

- Book net: **−$4,722.99 → −$3,887.17** (+$835.82)
- NBIX is no longer the largest loss in the book. CDNA (−$1,539.37) takes that
  place; NBIX falls to third.

Applied via `migrations/20260915_fix_nbix_reconstructed_sell.sql`.

## Consequences

**The figure remains gross.** `commission_complete` stays `false` — the child-fill
commissions were never captured, because `ibkr_fills` was inert on the day. The
true net is a few dollars worse than −$1,424.72.

**Every exit-replay number published before 2026-09-15 is stale.** NBIX was the
largest loss in the sample feeding `research/exit_rule_replay.py`. The headline
comparison in `AGENTS.md` (realised −$6,548 vs Prove-It +$5,410 over 30 closed
trades) was computed against the −$2,260.55 figure and must be regenerated. The
scheduled exit-parameter review of **2026-09-20** must re-run the harness before
drawing any conclusion.

**No learner contamination.** `breakout_learnings` has no NBIX row, so — unlike
the NTRA incident — nothing was feeding the screener's failure penalty from this
trade.

**The failure mode is closed.** Verified on production 2026-09-15: `ibkr_fills`
holds 46 rows written on every trading day since 2026-09-08 with **100%**
commission capture and an active hook (18 events in 48h), and
`IBKR_FLEX_EXEC_QUERY_ID` is configured, arming Tier 3. NBIX is the only one of
the (then) 43 closed trades carrying a non-authoritative sell price.

## Schema notes discovered while applying this

Three `trade_history` columns cannot be written directly, and any future repair
script must account for them:

- `net_profit_loss` is **generated**: `profit_loss − buy_commission − sell_commission`.
- `commission_complete` is **generated** from whether both commissions are present.
- `sell_reason` is `varchar(200)`. The pre-existing NBIX reason was already at
  exactly 200 characters, so an append-style correction of the kind used in
  `20260910_backfill_ntra_round_trips.sql` would have silently overflowed.

## Open questions

1. **How many of the other 34 pre-2026-09-08 trades are also mispriced?** NBIX is
   the only row that *flagged itself*. Rows whose fill was captured by Tier 2 at
   the time look identical to rows that were never verified — there is no stored
   provenance field distinguishing them. The operator's IBKR reconciliation found
   this one; nothing in the system would have.
2. **Should `sell_price_source` be persisted?** It is computed in
   `reconcile_with_ibkr()` and printed, but not stored. Had it been a column,
   this error would have been a one-line query rather than a manual audit.
