# HWM profit-lock after the first leg

- **Date:** 2026-08-20
- **Status:** Accepted

## Context

Recent live trades showed a repeating failure mode: the bot was correctly finding
names that could rally 4-9% from entry, but it was not monetising that first
leg. Winners were making a new high-water mark, then sliding for several
sessions, and the eventual sale was happening materially below the peak.

A review of 20 closed trades found:

- 9 winners gave back **$8,071** from their high-water marks before exit
- average give-back on winners was **$897**
- median winner exited **4.03% below its HWM**

This was not isolated to one rule. Trailing stops, rank-and-replace exits and
other discretionary sells were all arriving after a meaningful round-trip.

Several candidate rules were benchmarked against the actual closed-trade history
by reconstructing each trade's high-water mark from daily bars and comparing the
hypothetical exit against the realised exit:

| Candidate | Result vs realised exits |
|---|---|
| Arm at +3%, sell on 1.5% give-back from HWM | **+$5,311**, but one premature regression |
| **Arm at +6%, sell on 1.5% give-back from HWM** | **+$4,375**, improved 5 trades, worsened 0 |
| Arm at +5%, sell on 2.0% give-back from HWM | **+$3,504**, safer but left more on the table |
| Stall-aware rules (no new HWM for N days + weakness) | materially weaker than a pure HWM cap |

The key finding was that a **tight give-back cap from the peak** explains the
problem better than a delayed "wait for weakness confirmation" rule. The money
was being lost during the waiting.

## Decision

Replace the late-stage profit ladder with a **first-leg profit lock**:

- below **+6%** unrealised gain: keep the existing ATR-scaled base trail
- at **+6% or above**: tighten the IBKR trailing stop to **1.5%**

Expressed as `TRAIL_PROFIT_TIERS`:

| Unrealised gain | Trail |
|---|---|
| `< +6%` | base stop (`STOP_LOSS_PCT` floor, ATR-derived up to `ATR_STOP_MAX_PCT`) |
| `≥ +6%` | **1.5%** from HWM |

Power Hold remains authoritative. If a position ever qualifies as a genuine
leader, the power-hold branch still widens the trail to `POWER_HOLD_TRAIL_PCT`
and bypasses the profit-lock ladder entirely.

## Consequences

**Positive**

- Directly addresses the measured round-trip failure mode.
- Keeps the implementation simple by reusing the existing broker-managed
  trailing stop machinery.
- Uses the strongest benchmarked rule that improved realised P&L without any
  observed regressions in the current sample.

**Negative / accepted risks**

- This is intentionally aggressive and may bank ordinary winners before they can
  become exceptional ones.
- The evidence is still a small live sample, so the exact `+6% / 1.5%` pair is
  not universal truth; it is the best current operating point.
- If the tightened screener begins producing true 20%+ leaders, this setting may
  become overly defensive and should be revisited with a fresher sample.

## Follow-up

1. After several more trading sessions, re-run the same HWM benchmark on the
   expanded closed-trade set and compare `+6% / 1.5%` against a tighter
   **`+5% / 1.5%`** candidate.
2. If an automated reminder is wanted, the project's existing automation path is
   a **scheduled GitHub Actions workflow** plus **Telegram notification**. There
   is no generic reminder job today; that is the mechanism to use if we choose
   to automate this revisit.
