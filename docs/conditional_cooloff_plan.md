# Conditional Cooling-Off — Loss-Only Plan

## The Problem with the Current Rule

The current 3-day cooling-off blocks **all** re-buys of a recently sold ticker,
regardless of why it was sold:

| Exit reason | Should cool off? | Current behaviour | Correct behaviour |
|---|---|---|---|
| Trailing stop (loss) | ✅ Yes | ✅ Blocked | ✅ Blocked |
| MA exit (small loss or profit) | ❓ Maybe | ✅ Blocked | ⬇️ See below |
| Plateau rotation (profit) | ❌ No | ❌ Incorrectly blocked | ✅ Allow |
| Force sell (profit, manual) | ❌ No | ❌ Incorrectly blocked | ✅ Allow |

**The key insight:** The cooling-off period was designed to avoid *chasing a
broken stock back in*. That logic only applies when the stock **proved itself
wrong** by hitting the stop loss. If you exited profitably (rotation,
force-sell, MA exit above water), the stock is not "broken" — a new breakout
trigger should be allowed to fire immediately.

---

## Proposed Logic

```
IF ticker was sold within the last COOLING_OFF_DAYS days:
    Fetch the profit_loss from trade_history for that sale

    IF profit_loss < 0 (a loss):
        Apply cooling-off → skip, same as today

    ELSE (break-even or profit):
        Allow the re-buy → proceed normally
```

---

## The One Code Change Required

**File:** `execution_agent.py` — the cooling-off block in `run_market_open_buys()`

**Current (line 812):**
```python
recent_sell_res = client.table("trade_history") \
    .select("ticker") \
    .eq("ticker", ticker) \
    .gte("sell_date", cooling_cutoff) \
    .execute()
if recent_sell_res.data:
    print(f"   ⏳ {ticker} sold within last {COOLING_OFF_DAYS} days — cooling-off period active. Skipping.")
    continue
```

**Proposed:**
```python
recent_sell_res = client.table("trade_history") \
    .select("ticker, profit_loss") \       # ← add profit_loss to the select
    .eq("ticker", ticker) \
    .gte("sell_date", cooling_cutoff) \
    .execute()
if recent_sell_res.data:
    # Only cool off if the most recent sale was at a loss
    last_sale = recent_sell_res.data[-1]   # most recent first (ordered by sell_date)
    last_pnl  = last_sale.get("profit_loss") or 0.0
    if last_pnl < 0:
        print(f"   ⏳ {ticker} stopped out at a loss (${last_pnl:.2f}) within "
              f"last {COOLING_OFF_DAYS} days — cooling-off active. Skipping.")
        continue
    else:
        print(f"   ✅ {ticker} sold profitably (${last_pnl:+.2f}) recently — "
              f"cooling-off bypassed. Allowing re-buy.")
```

> [!NOTE]
> **No schema change required.** `profit_loss` is already written to
> `trade_history` by every exit path (`execute_sell`, `reconcile_with_ibkr`,
> `force_sell.py`). This is purely a read-side change.

---

## Edge Cases

| Scenario | Behaviour |
|---|---|
| `profit_loss` is NULL in DB (old record) | Defaults to `0.0` → treated as break-even → **allows re-buy** (safe default) |
| Multiple sales of same ticker in window | Uses the **most recent** sale to determine P&L |
| Ticker sold at exactly $0.00 P&L | Treated as non-loss → cooling-off bypassed (edge case, acceptable) |
| MA exit just barely negative | `profit_loss < 0` → cooling-off applies (conservative but correct) |

---

## Optional Enhancement: Configurable Threshold

Instead of `profit_loss < 0`, use a configurable floor:

```python
COOLING_OFF_LOSS_THRESHOLD = float(os.getenv("COOLING_OFF_LOSS_THRESHOLD", "0.0"))
# e.g. set to -100 to only cool off on losses > $100
# default 0.0 = any loss triggers cooling-off
```

---

## What This Enables Operationally

After a **force sell** (which is always profitable or neutral by design),
the stock is immediately eligible to re-appear in tomorrow's buy scan. This
directly supports the use case you described: sell a stalled position today,
bot buys the next best breakout tomorrow — including potentially buying back
the same stock if it re-triggers.

---

## Implementation Size

| Item | Effort |
|---|---|
| Code change | ~10 lines changed in one function |
| No DB migration needed | ✅ |
| Test to add | 3-4 new unit tests in `test_buy_gates.py` |
| Risk | Very low — only affects re-buy eligibility of recently sold tickers |
