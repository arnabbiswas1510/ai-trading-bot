# Early Loss Kill-switch: tighten to 1% and restrict to the entry day

**Date:** 2026-08-20
**Status:** Accepted
**Supersedes (partially):** `decisions/2026-08-01_early-loss-killswitch-and-day2-universal-minimiser.md`

## Context

The winner-side work in `decisions/2026-08-20_hwm-profit-lock-first-leg.md` established
that profits were being given back after the high-water mark. The mirror-image question was
asked of the losers: the closed-trade record shows false breakouts failing almost
immediately, yet the book was carrying them for days.

Eight of the seventeen closed trades were losers, totalling **−$5,784.35**. The failures
are front-loaded:

| Ticker | Loss | Days held |
|---|---|---|
| HWM | −$1,463 | 2 |
| RSI | −$1,390 | 3 |
| DELL | −$1,283 | 3 |
| PTGX | −$554 | 2 |
| THC | −$363 | 4 |
| SGHC | −$326 | 9 |
| TTWO | −$236 | 3 |
| GE | −$170 | 5 |

Five of the eight were still exited by the wide IBKR GTC trailing stop, meaning the early
loss-cutting layer never engaged at all before the damage was done.

## Method

All 17 closed trades were replayed on **5-minute FMP bars** over each trade's actual
holding window. The harness reproduces the live mechanics rather than approximating them:

- rules evaluated only on 15-minute boundaries, matching `monitor_portfolio_intraday()`
- a trigger calls `arm_exit()` semantics — a 0.6% trailing stop from the arm price
  (`ARMED_EXIT_TRAIL_PCT`), resolved against every subsequent 5-minute bar
- a 3.25-hour forced-sale deadline (`ARMED_EXIT_DEADLINE_HOURS`), checked on 15-minute
  boundaries only
- gap protection: if the first bar after arming opens through the trail level, the fill is
  the open, not the level

Every result is expressed as a **delta against the exit that actually happened**, so both
loser savings and collateral damage to winners are measured on the same sample.

Roughly 400 configurations were searched across four families: percentage kill-switch,
flat dollar stop, ATR-normalised thesis stop, and stacked combinations — each in both
`arm_exit()` and immediate-market-sell modes.

## Findings

### 1. The window matters far more than the threshold

| Config | Loser savings | Winner impact | Net |
|---|---|---|---|
| 2.0%, days 0–1 *(previous default)* | +$2,348 | −$1,873 (1 winner) | +$475 |
| 1.0%, days 0–1 | +$3,637 | −$2,345 (3 winners) | +$1,292 |
| 2.0%, day 0 only | +$1,016 | $0 | +$1,016 |
| 1.25%, day 0 only | +$2,130 | $0 | +$2,130 |
| **1.0%, day 0 only** | **+$3,426** | **$0** | **+$3,426** |
| 0.75%, day 0 only | +$3,496 | $0 | +$3,496 |

> **Correction (2026-08-20, same day):** the figures in this table were produced by an
> aggregation in `research/exit_rule_replay.py` that summed only *positive* deltas on
> losing trades, silently dropping losers a configuration made worse. Corrected all-in
> figures: 2.0% days 0–1 = **−$176**; 1.0% days 0–1 = **+$893**; **1.0% day 0 = +$3,027**;
> 0.75% day 0 = +$3,097. The ranking and therefore this decision are unchanged — every
> configuration was inflated by roughly the same $400 of TTWO/SGHC regressions — but the
> absolute numbers are overstated. The harness has been fixed.
>
> **This applies to every figure in this section**, not just the table above:
> arming versus market-selling is +$3,027 vs +$2,735 (not +$3,426 vs +$3,110), and
> the combination-stack row was measured with the same bug. The conclusions each
> supported are unaffected, because the bias was in the same direction for every
> configuration compared.

Day 1 is where the harm comes from. CPAY — a **winner** — was cut for −$1,873 by the
previous days-0–1 window, which is most of what that rule earned on the losers. At a 1%
threshold the day-1 extension damages three winners for −$2,345.

No winner in the sample ever closed 1% below entry on its own entry day. The two
populations separate cleanly on day 0, which is what makes the tighter threshold free.

### 2. Arming beats selling, in every family

`arm_exit()` outperformed an immediate market sell in every configuration tested — for the
chosen rule, +$3,027 versus +$2,735 (corrected; originally reported as +$3,426 vs +$3,110). The trigger price is frequently a local trough; the
0.6% trail rides the rebound out of it. This is why a tight trigger is cheap and a loose
one is not: the rule is a *request to leave on the next bounce*, not a market order.

### 3. Nothing beat the plain percentage rule

- **Dollar stops** matched but never exceeded it, and the best variants ($200, day 0)
  produced results identical to the 1% rule — they were firing on the same bars.
- **ATR thesis-stop variants** peaked at +$2,558 with two winners harmed.
- **Combination stacks** were strictly worse (best +$2,852 on losers, −$3,036 on winners,
  net −$184): each additional layer added winner exposure without new loser coverage.

The existing dollar stop and thesis stop are unchanged — they cover days 1–5, which this
rule no longer touches.

## Decision

Change two defaults in `execution_agent.py`:

- `EARLY_LOSS_STOP_PCT`: `0.02` → `0.01`
- **new** `EARLY_LOSS_STOP_MAX_DAY`: `0` (was a hardcoded `days_held <= 1`)

The mechanism — `arm_exit()` with a 0.6% trail and a 3.25-hour deadline — is unchanged, and
the day threshold is now an env var rather than a literal, so the window can be retuned
without a code edit.

Net effect on the historical sample: **+$3,027 versus the realised exits** (all-in,
corrected), with zero winners harmed. Measured as a whole stack — kill-switch plus the
existing dollar and thesis stops — the same change moves the agent from **−$1,603 to
−$272**, an improvement of **+$1,331**. The gap between +$3,027 and −$272 is the $500
dollar stop, which is now under review as FU-004.

## Consequences

- Fresh entries are cut roughly twice as fast on their first day.
- Day 1 now falls through to the Early Dollar Stop (days 0–5) alone; the Thesis Stop opens
  on day 2 as before. This is intentional — day 1 percentage cuts were destroying winners.
- Two small losers get marginally worse (SGHC −$190, TTWO −$209) as the tighter trigger
  arms into a dip that later recovered slightly. This is swamped by RSI, HWM and DELL
  recovering roughly $1.1k each.
- More arm/disarm churn on the entry day, so more Telegram notifications.
- Day 1 now has its own dashboard phase (`D1 · Dollar cap only`), because it is genuinely a
  distinct protection regime rather than part of the kill-switch window.

## Risks and limitations

- **Small sample.** Seventeen closed trades, eight of them losers. The day-0/day-1
  asymmetry is a large and mechanically plausible effect, but it rests on one winner
  (CPAY) for most of the measured day-1 damage.
- **0.75% scored $70 better than 1.0%** — well inside noise on this sample. 1.0% was chosen
  as the more defensible round number; there is no evidence the extra tightness helps.
- The replay assumes the 0.6% trail fills at its level when the bar's low reaches it.
  Real fills will be marginally worse.
- Regime dependence is untested: all 17 trades come from a single market period.

## Follow-up

Re-run the replay once the closed-trade count reaches roughly 30 and check whether the
day-0-only restriction still dominates. If day-1 losers start slipping through to the
dollar stop at materially worse prices, revisit `EARLY_LOSS_STOP_MAX_DAY=1` paired with a
*looser* threshold rather than reverting both.
