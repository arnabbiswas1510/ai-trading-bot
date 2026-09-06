# Commission accounting, and the RLS policy gap that hid it

**Date:** 2026-09-06
**Status:** Accepted

## Context

Two problems, found together, with a single root cause between them.

### 1. `ibkr_fills` had been empty since the day it was created

`ibkr_fills` is **Tier 1** of the sell-price ladder in `reconcile_with_ibkr()`.
It exists because of the RSI incident on 2026-07-17, where a sell price was
recorded incorrectly after an IB Gateway session reset wiped the `reqExecutions()`
cache. Tier 1 was built to be the one fill record that survives restarts.

It contained **zero rows**.

The production logs give the reason directly:

```
42501: new row violates row-level security policy for table "ibkr_fills"
```

16 such failures on 2026-09-03 alone; 62 in the retained window. `ibkr_fills` had
RLS **enabled** but **no policy at all**, which in Postgres denies everything.
`add_ibkr_fills.sql` left its `ENABLE` line commented out, and
`enable_rls_all_tables.sql` — the file that pairs every `ENABLE` with a
permissive policy — does not list the table.

That file's header states the assumption that made this invisible:

> the service role key bypasses RLS, so the bot is unaffected

**That assumption is false for this deployment.** The key in use is
`sb_publishable_...`, which is anon-class, and RLS applies to it in full. Every
other table works only because its policy happens to be `FOR ALL USING (true)`,
which admits anon as a side effect rather than by design.

So Tier 1 has been inert since inception, and every exit has silently fallen
through to **Tier 2 — the ephemeral session cache Tier 1 was built to replace.**
The durability guarantee documented in the 2026-07-17 remediation never actually
existed. A second table, `breakout_learnings`, was rejected the same way
(5 violations, also 0 rows).

The reason this ran for six weeks unnoticed is not the policy. It is that the
handler `print()`ed its failures and did nothing else. Both tables are
**write-only** — no screen renders either one — so an empty table is
indistinguishable from a quiet week. A write sink nobody reads cannot be
monitored by looking at it.

### 2. Reported P&L was gross, and the dashboard already knew

`trade_history.profit_loss` is `(sell - buy) x shares`. There is no fee term and
no commission column. Meanwhile `total_pnl` on the dashboard is derived from the
**live IBKR cash balance**, which is real money and therefore net of everything.

The dashboard was contradicting itself by **exactly $480.91** — the same figure
obtained independently from the account identity (expected $93,467.72 vs IBKR
actual $92,986.81). Two derivations, same number, so the gap is real and it is
fees.

Even with RLS fixed, commissions would still have been zero. IBKR sends the
execution and its commission as **separate messages**; `ib_insync` attaches a
blank `CommissionReport` at `execDetails` time and populates it later via
`commissionReportEvent`. Only `execDetailsEvent` was registered, so the old code
would have written `commission: 0` for every fill. Both bugs had to be fixed for
either fix to be worth anything.

## Decision

**1. Add the missing RLS policies** (`migrations/fix_rls_missing_policies.sql`)
for `ibkr_fills` and `breakout_learnings`, matching the `FOR ALL USING (true)`
shape every working table already uses. The migration also ships a verification
query that audits for *any* table with RLS enabled and no policy, so the next
occurrence is found by running one query rather than by reading logs.

**2. Register `commissionReportEvent`** alongside `execDetailsEvent`. The
commission handler upserts the fill first, so a fee still lands even if the
execution report was lost or arrived out of order.

**3. Escalate write-sink failures via Telegram, once per sink per process**
(`_fill_sink_failure()`). This is the part that actually matters. A per-fill
alert would flood during an outage; a per-process alert guarantees the first
failure is seen and nothing after it is noise.

**4. Keep `profit_loss` GROSS. Derive net.** New columns `buy_commission` /
`sell_commission` on `trade_history`, `buy_commission` on `portfolio_positions`,
and generated `STORED` columns for `net_profit_loss` and `commission_complete`.

Redefining `profit_loss` was rejected: `research/exit_rule_replay.py` explicitly
does not model commissions, and **every exit threshold in `decisions/` was
measured gross.** Changing the column's meaning would make every past benchmark
silently non-comparable with future runs — with no signal that the basis had
moved. The scheduled exit-parameter reviews in `AGENTS.md` depend on that
comparability.

**5. A missing commission is UNKNOWN, never zero.** `sum_fill_commission()` and
`trade_commission()` return `None` rather than a partial sum, the schema stores
NULL, and the UI marks the row provisional with `*`. IBKR always charges
something, so a stored `0` would overstate net P&L by exactly the amount we
failed to capture — while looking precise. This mirrors the labelled-FMP-fallback
principle from `2026-09-03_ibkr-sourced-position-values.md`: an unlabelled number
gets read with the same confidence as a verified one.

**6. Commissions are written by a follow-up UPDATE, never as part of an INSERT.**
If the migration has not been applied, an unknown column raises `PGRST204` and
aborts the whole statement. On the buy path that would leave a position filled at
IBKR but absent from Supabase — the phantom-fill failure the insert-before-stop
ordering exists to prevent. On the sell path it is worse: `execute_sell()`
deletes the `portfolio_positions` row *before* inserting into `trade_history`, so
a failed insert would erase the position with no closing record at all. **A cost
figure is never worth that risk.**

## Consequences

- Tier 1 of the sell-price ladder starts working for the first time. Fill
  provenance now genuinely survives restarts.
- Dashboard, Trade History and Performance all show **net** P&L, with `*` and a
  tooltip on trades whose fees IBKR has not reported.
- The $480.91 self-contradiction closes as historical trades are backfilled.
  Until then it is visible and labelled rather than silent.
- Historical trades stay NULL — correctly. They are marked provisional rather
  than being credited with a fee of zero.
- `profit_loss` remains directly comparable with every replay in `decisions/`.
- **Not yet resolved:** whether the $480.91 is all commissions or partly
  market-data subscription fees. Shipping stocks also pay dividends, which raise
  IBKR's cash and therefore *mask* fees — so true commissions may be **higher**
  than $480.91, not lower. Backfill from Flex/`reqExecutions` will settle it.

## Files

- `migrations/fix_rls_missing_policies.sql` (new)
- `migrations/add_commission_tracking.sql` (new)
- `backend/commissions.py` (new), `tests/test_commissions.py` (new)
- `frontend/src/lib/commissions.js` (new)
- `execution_agent.py` — `_fill_sink_failure`, `persist_fill`,
  `update_fill_commission`, `sum_fill_commission`, `extract_fill_commission`,
  `trade_commission`, `record_buy_commission`, `record_trade_commissions`;
  `commissionReportEvent` registration
- `backend/main.py` — `/api/trades` enrichment, `/api/portfolio` summary
- `frontend/src/components/{DashboardView,TradesView,ReturnsView}.jsx`
