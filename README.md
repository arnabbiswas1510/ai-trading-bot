# AI Trading Bot — CANSLIM Momentum Strategy

An automated equity trading system implementing the **CANSLIM** methodology developed by William O'Neil.
The bot finds fundamentally strong stocks, detects technical breakout triggers, and executes
market orders via Interactive Brokers (IBKR), running as a fully containerized daemon on a home server.

---

## Architecture Overview

```
+---------------------------------------------------------------+
|  DATA SOURCES                                                 |
|  TradingView Scanner  .  Financial Modeling Prep (FMP)  . IBKR|
+-------+-----------------------------+---------------------+---+
        |                             |                     |
        v                             v                     v
+------------------+ +------------------+ +------------------------------+
| FUNDAMENTAL      | | TECHNICAL        | | EXECUTION AGENT              |
| SCREENER         | | SCREENER         | | execution_agent.py           |
| tv_api_screener  | | technical_       | | (continuous daemon)          |
| .py (daily,      | | screener.py      | |                              |
|  Mon-Fri 6pm ET) | | (daily, same job)| | Market open -> buy loop      |
|                  | |                  | |   Check triggers, gate buys, |
| TradingView scan:| | FMP price data:  | |   place market orders +      |
| EPS > 20% QoQ   | | Above SMA-50     | |   GTC trailing stops         |
| EPS > 20% YoY   | | 40%+ vol surge   | |                              |
| Volume > 100K   | | Near 52w high    | | Every 15 min -> monitor loop |
| Price > $10     | | -> daily_triggers| |   Update HWM date, self-heal |
| -> watchlist    | |                  | |   MA exit, EOD rotation      |
+------------------+ +------------------+ +------------------------------+
                                                         |
                                     +-------------------v--------------+
                                     |         SUPABASE DATABASE        |
                                     |  watchlist . daily_triggers      |
                                     |  portfolio_positions             |
                                     |  trade_history . account_balances|
                                     +----------------------------------+
```

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Broker API | Interactive Brokers (`ib_insync`) |
| Fundamental Screening | TradingView Scanner API (undocumented, browser-spoofed) |
| Market Data | Financial Modeling Prep (FMP) REST API |
| Database | Supabase (PostgreSQL) |
| Containerization | Docker + Docker Compose |
| Notifications | Telegram Bot API |
| AI Ranking | OpenAI (ranks triggers by CANSLIM quality before market open) |

---

## Component Documentation

| Document | Description |
|----------|-------------|
| [Fundamental Screener](docs/fundamental_screener.md) | What makes a stock qualify for the watchlist |
| [Technical Triggers](docs/technical_triggers.md) | How breakout signals are detected daily |
| [Buy Logic](docs/buy_logic.md) | All the checks before a buy order is placed |
| [Sell Logic](docs/sell_logic.md) | How the bot exits positions |
| [Configuration](docs/configuration.md) | All settings, environment variables, and database schema |

---

## CANSLIM Strategy -- Explained Simply

Imagine you are looking for the best athlete in a school. You would not just pick someone randomly -- you would look for someone with a track record of winning (earnings growth), who is on a hot streak right now (recent breakout), who the coaches believe in (institutional money is buying them), and who is racing in a good environment (bull market).

CANSLIM is that same checklist, applied to stocks:

| Letter | What It Means | How the Bot Checks It |
|--------|---------------|----------------------|
| **C** | *Current Earnings* -- Is the company making more money this quarter than last year? | Quarterly EPS growth > 20% (TradingView filter) |
| **A** | *Annual Earnings* -- Has it been growing for a while, not just a one-hit wonder? | Annual EPS growth > 20% (TradingView filter) |
| **N** | *New Highs* -- Is the stock breaking out to new price highs on the chart? | Price within 2% of its 52-week high on a big-volume day |
| **S** | *Supply & Demand* -- Are more people rushing to buy it than usual? | Volume at least 40% above the 50-day average |
| **L** | *Leader* -- Is it one of the best in its sector, not a laggard? | Relative strength built into the TradingView screener sort |
| **I** | *Institutional Sponsorship* -- Are the big funds (Fidelity, etc.) buying it? | TradingView analyst rating used as a proxy |
| **M** | *Market Direction* -- Is the overall market going up? | SPY vs its 200-day moving average |

The bot does **not** chase every stock. It only buys when ALL these conditions line up at once. Most days, it does nothing.

---

## Buy Rules -- Explained Simply

Think of the bot as a strict bouncer at a club. Every breakout trigger has to pass **all 7 checks** before getting in:

1. **Room in the portfolio?** The bot holds a maximum of 4 stocks. If all 4 slots are full, no new buys -- full stop.
2. **Is the trigger fresh?** The breakout signal must be from the last 3 days. Stale signals from last week are ignored.
3. **Already own it?** Can't buy the same stock twice.
4. **Too soon after selling it?** If the bot sold this stock in the last 3 days (e.g., it hit the trailing stop), it waits before buying it again -- the same trade that lost once rarely wins immediately.
5. **Enough cash?** The position must be worth at least $5,000. Tiny positions aren't worth the commission risk.
6. **Still in the buy zone?** If the stock already ran up more than 5% past the breakout point by the time the bot checks it, it skips -- buying too late is a losing trade in O'Neil's system.
7. **Can we afford whole shares?** Divides the cash by the current price. If that results in 0 shares, skip.

When a buy passes all 7 gates:
- A **market order** is placed immediately (guarantees a fill at the best available price)
- A **7% trailing stop** is attached right after the fill -- this is the safety net
- The position is recorded in Supabase with `hwm_date = today` (the plateau clock starts ticking)
- Triggers are evaluated **highest AI-rated first** -- the best opportunities get filled before lesser ones

**Position sizing:** Cash is divided equally across the remaining empty slots. If you have $20,000 and 2 empty slots, each buy gets $10,000.

| # | Gate | What It Checks | Setting |
|---|------|----------------|---------|
| 1 | Portfolio cap | Must have an open slot | `MAX_POSITIONS=4` |
| 2 | Trigger freshness | Signal must be <= 3 days old | `TRIGGER_LOOKBACK_DAYS=3` |
| 3 | No duplicate | Not already held | -- |
| 4 | Cooling-off | Not sold within last 3 days | `COOLING_OFF_DAYS=3` |
| 5 | Cash floor | Available cash >= $5,000 | `MIN_POSITION_SIZE=5000` |
| 6 | Buy zone | Price <= 5% above pivot | `MAX_PIVOT_EXTENSION=0.05` |
| 7 | Share count | shares = position_size / price > 0 | -- |

---

## Sell Rules -- Explained Simply

The bot never guesses when to sell. It follows three clear rules:

### Rule 1 -- Trailing Stop (the floor that rises with you)

Every stock gets a **7% trailing stop** the moment it is bought. Think of it as a floor that follows the stock up but never comes back down.

- Stock bought at $100 -> stop starts at $93
- Stock rises to $130 -> stop rises to $120.90 (locks in a gain)
- Stock then falls to $120 -> stop fires, position sold automatically

**This is fully managed by IBKR** -- it works even when the bot is down. The bot just checks every 15 minutes that the stop order is still there; if it disappeared, it re-places it (self-healing).

### Rule 2 -- Moving Average Exit (support line check)

Once a day, near market close (3:45-4:00 PM), the bot checks if the stock price has fallen **below its 21-day EMA by more than 1%**. If yes, it sells immediately. This catches slow bleed situations where the trailing stop has not fired yet but the stock has quietly broken its trend.

### Rule 3 -- Plateau Rotation (the "get off the fence" rule)

This is the cleverest rule. Imagine holding a stock for 12 days and it just sits there going nowhere -- no new highs, no progress. Meanwhile, a fresh breakout stock is waiting to enter the portfolio but there is no room.

At 3:45 PM every day, the bot checks:
- Is the portfolio full?
- Is there a fresh breakout signal from a stock we don't own?
- Has any position gone **10+ days without making a new high?**

If all three are true, it sells the most-stalled position to make room for the fresh opportunity. The replacement buy happens the next morning.

The staleness clock (`hwm_date`) tracks the **last date each stock made a new high** during intraday monitoring. Every 15 minutes, if the current price exceeds the previous poll's high, `hwm_date` is updated to today. If it stops updating, the clock runs.

| Exit | Trigger | Who Acts |
|------|---------|----------|
| **Trailing Stop** | Price falls 7% from its peak | IBKR (automatic, always on) |
| **MA Breach** | Price drops below EMA-21 by >1% | Bot sells at 3:45 PM ET |
| **Plateau Rotation** | 10+ days no new high + full portfolio + fresh trigger | Bot sells at 3:45 PM ET |

---

## Configuration Reference

These are the strategy knobs -- the numbers that control how the bot behaves. All are set via environment variables.

### Portfolio & Risk

| Variable | Default | What It Does |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | How many stocks to hold at once |
| `MIN_POSITION_SIZE` | `5000` | Minimum dollar amount per buy -- skips if cash is too low |
| `STOP_LOSS_PCT` | `0.07` | Trailing stop distance -- 7% below the stock's highest price reached |
| `PLATEAU_DAYS` | `10` | Days without a new high before a stock qualifies for rotation |
| `COOLING_OFF_DAYS` | `3` | Days to wait before re-buying a stock that was just sold |

### Buy Gating

| Variable | Default | What It Does |
|----------|---------|-------------|
| `TRIGGER_LOOKBACK_DAYS` | `3` | How far back to look for breakout signals (covers weekends/holidays) |
| `MAX_PIVOT_EXTENSION` | `0.05` | Maximum % a stock can be above its breakout price before we skip it |

### Moving Average Exit

| Variable | Default | What It Does |
|----------|---------|-------------|
| `EXIT_MA_TRIGGER_ENABLED` | `true` | Turn the MA exit on or off |
| `EXIT_MA_TYPE` | `EMA` | Type of moving average: `EMA` or `SMA` |
| `EXIT_MA_WINDOW` | `21` | Number of trading days for the moving average |
| `EXIT_MA_BUFFER_PCT` | `0.01` | How far below the MA before selling (1% buffer prevents noise-triggered exits) |
| `EXIT_MA_EOD_ONLY` | `true` | Only check at 3:45 PM (prevents intraday whipsaw sells) |

### Technical Screener

The technical screener runs daily after market close (Mon-Fri, same GitHub Actions job as the fundamental screener). It checks each stock on the watchlist and looks for a breakout pattern. Think of it as looking for a sprinter leaving the starting blocks.

| Variable | Default | What It Does |
|----------|---------|-------------|
| `SMA_WINDOW` | `50` | Days used to calculate the trend line the stock must be above |
| `VOLUME_SURGE_MIN` | `1.40` | Volume must be at least 40% above normal to count as a real breakout |
| `ROLLING_HIGH_WINDOW` | `252` | Trading days used to define the 52-week high |
| `PIVOT_PROXIMITY` | `0.98` | Stock must be within 2% of its 52-week high to qualify |

### Fundamental Screener

The fundamental screener runs daily after market close (Mon-Fri at 6:00 PM ET). It asks TradingView to scan every US-listed stock and returns only the ones that pass all these filters simultaneously:

| Filter | Threshold | What It Means |
|--------|-----------|---------------|
| Price | > $10 | Avoids penny stocks |
| Quarterly EPS growth | > 20% QoQ | Company is earning more this quarter than a year ago -- accelerating |
| Annual EPS growth | > 20% TTM | Sustained growth over the full year, not a one-time blip |
| 30-day avg volume | > 100,000 | Enough daily trading activity to enter and exit cleanly |
| Stock type | Common or preferred only | No ETFs, no pre-IPO, no mutual funds |

These filters run inside a single TradingView API call -- results (up to 2,000 stocks) are sorted by market cap and written to the `watchlist` table. The technical screener then scans only this curated list each day.

---

## OpenAI Integration

Before market open each day, OpenAI evaluates the day's breakout signals and assigns an **AI rating** to each one. The bot sorts triggers by this rating before buying -- highest-confidence breakouts get first access to available capital.