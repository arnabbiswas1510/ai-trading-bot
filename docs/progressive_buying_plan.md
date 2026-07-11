# Progressive Buying for Low-Priced Stocks — Advisory & Plan

## Clarifying the actual risk

First, an important correction to the mental model: **your dollar risk at the stop-loss is mathematically identical regardless of stock price**, given the same dollar allocation.

| Stock | Price | Shares bought ($25K) | 7% stop triggers at | Max loss |
|---|---|---|---|---|
| SGHC | $5 | 5,000 | $4.65 | **$1,750** |
| SPY | $750 | 33 | $697.50 | **$1,750** |
| NVDA | $210 | 119 | $195.30 | **$1,750** |

The *percentage* loss is capped by the trailing stop regardless of share count. So where does the extra risk actually come from on low-priced stocks?

---

## Why low-priced stocks genuinely ARE riskier (but not for the reason you think)

### 1. Gap risk — the stop doesn't protect you
A 7% trailing stop **cannot protect against overnight gaps**. A $5 stock can gap from $5.00 to $3.80 on news — a 24% drop that blows straight through your stop. The lower the price, the more a small absolute move represents a massive percentage move.

### 2. Higher volatility eats the trailing stop budget
Low-priced stocks routinely move 5-10% in a single session on no news. On SGHC, a normal day's volatility might consume 3-4% of your 7% stop, leaving almost no margin before you're stopped out. A $150 stock with the same dollar volatility would only consume 0.1% of your stop budget.

### 3. Wider bid-ask spreads
A $5 stock with a $0.05 spread costs you 1% on entry + 1% on exit = 2% frictional loss before the trade starts. That's already 28% of your 7% stop consumed at zero.

### 4. Liquidity and market impact
Selling 5,000 shares of a thinly traded $5 stock at market can move the price against you. The IBKR market sell order itself may fill 3-4% below the bid.

### 5. O'Neil's own guidance
William O'Neil, the creator of CANSLIM, explicitly recommends **avoiding stocks below $15 (often cited as $12-15 minimum)**. His reasoning: institutional investors avoid sub-$15 stocks, meaning the breakout lacks the institutional sponsorship that drives sustained moves.

---

## Your two practical options

### Option A — Minimum Price Filter (Simple, Recommended First)
> Skip any trigger where `close_price < $15` (or a configurable threshold).

**Pros:** 1 line of code, zero architectural change, consistent with O'Neil's own rules.
**Cons:** You miss some genuine small-cap breakouts.

This is the change O'Neil himself would make.

---

### Option B — Progressive Buying / Pyramiding (Complex, High Value If Done Right)

O'Neil's pyramid rule for a full $25K position:

```
Breakout day:  Buy 50% ($12,500) — the initial position
+2.5% move:    Add 25% ($6,250)  — first confirmation add-on
+5.0% move:    Add 25% ($6,250)  — second confirmation add-on
```

**Why this reduces risk on low-priced stocks:**
- You only commit 50% on day one. If the stock immediately reverses and stops you out, max loss = $875 (7% of $12,500) instead of $1,750.
- Add-ons only happen if the stock confirms strength. This filters out false breakouts, which are more common in low-priced stocks.
- Average cost basis rises with the stock (not at the bottom), so you're *adding to strength*.

**Implementation complexity — what would need to change:**

#### New Supabase columns on `portfolio_positions`
```sql
buy_phase         INT DEFAULT 1    -- 1=initial, 2=after first add, 3=full
initial_buy_price FLOAT            -- pivot for add-on calculation
addon1_target     FLOAT            -- buy_price * 1.025
addon2_target     FLOAT            -- buy_price * 1.050
addon1_done       BOOL DEFAULT false
addon2_done       BOOL DEFAULT false
initial_shares    INT              -- shares at first buy
```

#### Changes to `execution_agent.py`
| Location | Change |
|---|---|
| `run_market_open_buys()` | If `close_price < $20`, buy 50% of position. Store add-on targets. |
| `monitor_portfolio_intraday()` | New check: for positions in phase 1 or 2, fetch current price; if above add-on target, buy the next tranche. |
| `execute_buy_addon()` | New function: buy add-on shares, update position in Supabase, adjust trailing stop. |
| Position sizing | Slot reservation: reserve the full $25K in cash accounting even though only $12.5K is deployed initially. |

#### The hardest part: cash reservation
Your current sizing `available_cash / remaining_slots` assumes each slot deploys 100% at once. With pyramiding, slot 1 deploys $12.5K on day 1 but reserves $12.5K more. If slot 2 also triggers on the same day, the bot needs to know $12.5K is already earmarked — otherwise it would over-allocate.

This requires a "reserved cash" concept in the portfolio tracking.

---

## My recommendation

**Do Option A first.** Add `MIN_STOCK_PRICE = float(os.getenv("MIN_STOCK_PRICE", 15.0))` and skip triggers below it. This is the single most impactful change for avoiding the SGHC-style problem and is consistent with O'Neil's methodology.

**Then evaluate Option B separately.** Pyramiding genuinely reduces per-trade risk and is a valid CANSLIM technique, but it's a 2-3 week implementation with new DB schema, new intraday logic, and significant testing. It would be most valuable on stocks in the $10-25 range where the breakout is real but the stock needs confirmation.

> [!IMPORTANT]
> **Open question before proceeding:** What threshold makes sense for you?
> - `MIN_STOCK_PRICE = $15` — strict O'Neil compliance, misses some valid setups
> - `MIN_STOCK_PRICE = $10` — pragmatic middle ground
> - Progressive buying for stocks `$10 < price < $20`, full position for stocks `>= $20`
>
> What would you like to implement?
