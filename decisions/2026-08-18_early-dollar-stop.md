# ADR: Early Dollar Stop — $500 Hard Cap on Days 0–5

**Date:** 2026-08-18  
**Status:** Accepted

---

## Context

Percentage-based stops are blind to position size. With ~$22K positions and an
ATR-derived trailing stop of 10–12%, the potential intraday loss before any
bot-side exit fires can be $2,000–$2,800. The existing early defences are:

- **Day 0-1 kill-switch**: fires at −2% from entry. On a $22K position that's
  −$440. Useful, but only covers the first two sessions.
- **Thesis Stop (days 2-5)**: fires at −1× ATR from entry. With ATR 4–8%,
  the threshold is $880–$1,760. Wide enough to let real breakouts breathe, but
  too loose for positions that are simply failing.

Observation from 19 closed trades: 8 positions were closed at a loss within
days 0-5, costing $6,205 total. The 5 worst (RSI, HWM, APH, OII, FROG) each
lost $758–$1,463 — all clearly failing from the first monitoring cycle.

## Decision

Added `EARLY_DOLLAR_STOP_AMOUNT = $500` (env: `EARLY_DOLLAR_STOP_AMOUNT`,
default 500) and `EARLY_DOLLAR_STOP_MAX_DAY = 5` (env: `EARLY_DOLLAR_STOP_MAX_DAY`).

In `monitor_portfolio_intraday()`, for `days_held <= EARLY_DOLLAR_STOP_MAX_DAY`
and no active armed exit:

```python
unrealized_dollar_loss = shares * (current_price - buy_price)
if unrealized_dollar_loss <= -EARLY_DOLLAR_STOP_AMOUNT:
    arm_exit(...)
```

The check arms an exit (0.6% tight trailing stop via `arm_exit()`) rather than
issuing an immediate market sell. This is consistent with all other early-exit
rules and allows capturing a bounce if the stock reverses.

## Simulation

Against 19 closed trades (2026-07-09 → 2026-08-18):

| Threshold | Saved on losers | Trades | Winners at risk |
|---|---|---|---|
| $250 | $4,298 | 6 | 5 at risk |
| $400 | $3,698 | 6 | 5 at risk |
| **$500** | **$2,936** | **5** | **2 at risk** |
| $600 | $2,436 | 5 | 0 at risk |
| $750 | $1,686 | 5 | 0 at risk |
| $1,000 | $907 | 3 | 0 at risk |

$500 is the optimal balance: saves $2,936 across 5 trades (RSI +$890, HWM
+$963, APH +$555, OII +$270, FROG +$258) while flagging only 2 eventual winners
as potentially at risk — and those were marginal winners (+0.45%, +0.97%)
with low-volume entries anyway.

Note: 3 of the 5 biggest early losers (HWM 0.70×, APH 0.64×, FROG 0.86×)
would also be blocked by the new volume surge gate added in the same session.
The dollar stop provides defence-in-depth for proper breakouts (RSI 5.69×, OII
3.57×) that reverse fast anyway.

## Consequences

- Maximum early-session dollar loss per position capped at ~$500 during days 0-5.
- Complements (does not replace) the Day 0-1 kill-switch and Thesis Stop.
- The rule is disabled when `EARLY_DOLLAR_STOP_AMOUNT=0`.
- Threshold is env-configurable; `$600` carries zero winner risk if desired.

See `docs/sell_logic.md` and `docs/configuration.md`.
