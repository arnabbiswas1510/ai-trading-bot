# 2026-09-27 — Exit-rule shadow logger (measure Q1 arm@+3% and Q2 5% give-back trail in production, without trading on them)

- **Status:** Accepted; silent failures and writer credentials superseded by [2026-10-04](2026-10-04_backup-vault-and-private-exit-shadow.md)
- **Kind:** Instrumentation (no live rule reads it)
- **Register entry:** `exit-shadow-log`
- **Feeds reviews:** `exit-parameters-proveit` (Q1), `ladder-width-runon` (Q2)

> **2026-10-04:** Candidate calculations and live-order isolation still hold.
> The silent degradation described below is no longer current: research-write
> failures emit credential-safe diagnostics. Observations use an isolated
> private client rather than the ordinary anon trading client. The table's
> service-role-only policy is unchanged. Historical successful writes have not
> been established; do not assume the original deployment produced evidence.

## Context

Two exit-rule candidates keep surfacing in the scheduled exit reviews, and both
are stuck for the same reason: the only evidence for them comes from
`research/exit_rule_replay.py`, which sees **5-minute bars in a single bull-tape
regime**.

1. **Q1 — arm Phase 2 at +3% instead of the live +2%**
   (`PROVE_IT_P2_ARM_GAIN_PCT`). On the 2026-09-18 sweep it scored **+$768 over
   shipped**, helped 27 trades and harmed 10 (3 fewer harmed than shipped), and
   the direction has strengthened since n=52 (+$202). But the top 9 trades are
   100% of the net and the largest (CDNA, 18%) is a two-sided wash, so it is not
   shippable on backtest alone. Tracked by `exit-parameters-proveit`.

2. **Q2 — a 5% give-back trail from the high-water mark instead of the live 1.5%
   profit-lock** (`ladder-width-runon`). After charging slot opportunity cost it
   scored **slot_net +$25,386 vs shipped +$21,844**, but ~65% of the edge is ECO
   alone, harmed rises 12→18, and the whole sample is one regime.

The backtest cannot resolve either, and it never will from the same 5-minute,
one-regime data. What it is missing is **live, forward, out-of-regime evidence**,
and specifically the two things a 5-minute replay structurally cannot see:

- **Sub-5-minute wick behaviour** — whether a tighter rule fires on an intraday
  spike-down that immediately recovers.
- **Behaviour across different tapes**, as trades accumulate past this one bull
  window.

## Decision

Ship a **side-effect-free shadow logger**. Every 15-minute monitor cycle,
`monitor_portfolio_intraday()` records — for each open position — what the LIVE
Prove-It rule and the two candidates **would** do this cycle, into a new
`exit_shadow_log` table. **No live order is placed, cancelled or modified, and no
live rule reads the table.** The bot trades exactly as it did before.

The computation lives in a new pure module, `exit_shadow.py`
(`compute_exit_shadows()`), whose candidate constants are deliberately the
*alternatives*, not the live values:

- `Q1_SHADOW_ARM_GAIN_PCT = 0.03` (live is `0.02`)
- `Q2_SHADOW_TRAIL_PCT   = 0.05` (live profit-lock is `0.015`)

`tests/test_exit_shadow.py` asserts these differ from live, so the day someone
accidentally sets the shadow to the live values — making the log measure nothing
— a test fails. It also pins the sole Q1 divergence window (proven, peak in
[+2%, +3%), where live is armed and Q1 is not) and the Q2 hold-vs-cut boundary.

The call is gated by `EXIT_SHADOW_LOG_ENABLED` (default `true`) and fully
exception-wrapped: a missing table (migration not yet applied), a schema-cache
miss or any transient Supabase error degrades silently and never fires Telegram —
identical to the existing `highest_unrealized_pct` degrade-gracefully pattern a
few lines above it.

## The hard limit, stated honestly

A live shadow can only observe divergence **up to the real exit**. Once the live
rule sells, there are no shares left to watch, so the log captures the
**wick / timing** side of these rules (a looser trail riding out a dip the live
rule cut; an earlier or later arm) but **NOT the run-on upside** of holding a
winner past the live exit. That upside stays harness-only (`--runon`).

Concretely: **Q1 arm-timing is fully observable** live — it changes only whether
the give-back floor is engaged within the holding window, which always precedes
the exit. **Q2 5% trail is only partially observable** — its wick-avoidance is
visible, but its "hold the winner longer" thesis is not, because that plays out
after the live 1.5% lock has already sold. The register entry says this in the
same words; it is not a footnote.

## Consequences

- Zero change to live trading. `exit_rules.py`, order placement and every buy/sell
  gate are untouched.
- Two scheduled reviews (`exit-parameters-proveit`, `ladder-width-runon`) gain a
  live divergence sample to cross-check their backtest numbers against, instead of
  re-reading the same one-regime replay.
- One new append-only, retention-free table. Volume is one row per open position
  per cycle (≤5 positions × ~26 cycles/day ≈ 130 rows/day) — small enough that no
  retention sweep is added now; if it ever matters, add one like `agent_logs`.
- If the migration is not yet applied in production, the logger no-ops silently
  until it is; trading is unaffected in the interim.

See `exit_shadow.py`, `migrations/20260927_add_exit_shadow_log.sql`,
`tests/test_exit_shadow.py`, and the `exit-shadow-log` register entry.
