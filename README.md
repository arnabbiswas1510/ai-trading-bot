# AI Trading Bot — CANSLIM Momentum Strategy

An automated equity trading system implementing the **CANSLIM** methodology developed by William O'Neil.
The bot screens fundamentally strong stocks, detects technical breakout triggers, and executes
market orders via Interactive Brokers (IBKR), running as a fully containerized daemon.
Idle portfolio slots are parked in QQQ during bull markets and held as cash during bear markets.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  DATA SOURCES                                                        │
│  Financial Modeling Prep (FMP API)  ←→  Interactive Brokers (IBKR)  │
└────────────┬──────────────────────────────────┬─────────────────────┘
             │                                  │
             ▼                                  ▼
┌────────────────────────┐        ┌─────────────────────────────────────┐
│  FUNDAMENTAL SCREENER  │        │         EXECUTION AGENT             │
│  fundamental_screener  │        │         execution_agent.py          │
│  .py (weekly cron)     │        │         (continuous daemon)         │
│                        │        │                                     │
│  S&P 500 universe      │        │  9:30–9:45 AM → run_market_open_   │
│  EPS growth filter     │        │  buys() — 5-step cascade:           │
│  Composite scoring     │        │   ① Sell ETF slots for triggers     │
│  Top 90 → Supabase     │        │   ② Buy daily_triggers (CANSLIM)   │
│  watchlist table       │        │   ③ Sell ETF for momentum triggers  │
└────────────┬───────────┘        │   ④ Buy momentum_triggers           │
             │                    │   ⑤ Park idle slots → QQQ / cash   │
             ▼                    │                                     │
┌────────────────────────┐        │  9:45 AM–4 PM → monitor_portfolio_ │
│  TECHNICAL SCREENER    │        │  intraday() — 4-phase sell logic    │
│  technical_screener.py │        │                                     │
│  (daily cron)          │        │  Every cycle → reconcile_with_      │
│                        │        │  ibkr() [uses ib.portfolio()]       │
│  SMA-50 check          │        └──────────────┬──────────────────────┘
│  40%+ volume surge     │                       │
│  Within 2% of 52w high │                       │
│  → daily_triggers      │    ┌──────────────────▼──────────────────────┐
└────────────────────────┘    │            SUPABASE DATABASE            │
             │                │  watchlist · daily_triggers             │
             ▼                │  momentum_triggers                      │
┌────────────────────────┐    │  portfolio_positions · trade_history    │
│  MOMENTUM SCREENER     │───►│  account_balances                       │
│  momentum_screener.py  │    └─────────────────────────────────────────┘
│  (daily cron, 2-pass)  │
│                        │
│  Relaxed fundamentals  │
│  Pass 1: standard tech │
│  Pass 2: relaxed tech  │
│  → momentum_triggers   │
└────────────────────────┘
```

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Broker API | Interactive Brokers (`ib_insync`) |
| Market Data | Financial Modeling Prep (FMP) REST API |
| Database | Supabase (PostgreSQL) |
| Containerization | Docker + Docker Compose |
| HTTP Client | `httpx` (async), `requests` (sync) |
| Data Processing | `pandas` |

---

## Component Documentation

> These documents are derived directly from source code and must be kept in sync
> with any code changes. See [Maintenance Policy](#llm-maintenance-policy) below.

| Document | Source File(s) | Description |
|----------|---------------|-------------|
| [Fundamental Screener](docs/fundamental_screener.md) | `fundamental_screener.py`, `backend/screener.py` | CANSLIM 7-dimension scoring, watchlist pipeline, EPS thresholds |
| [Technical Triggers](docs/technical_triggers.md) | `technical_screener.py` | Breakout detection: SMA-50, volume surge, 52-week high proximity |
| [Momentum Screener](docs/momentum_screener.md) | `momentum_screener.py` | Secondary relaxed screener, two-pass logic, momentum_triggers table |
| [Buy Logic](docs/buy_logic.md) | `execution_agent.py` → `run_market_open_buys()` | 5-step cascade: primary → momentum → ETF parking; position sizing |
| [Sell Logic](docs/sell_logic.md) | `execution_agent.py` → `monitor_portfolio_intraday()` | Bear market exit, stale rotation, stop-loss, profit target, Power Hold |

---

## CANSLIM Strategy Summary

| Letter | Dimension | Pipeline Implementation | Config Variable |
|--------|-----------|------------------------|----------------|
| **C** | Current Earnings | Q EPS growth > 18% | `CANSLIM_MIN_Q_EPS_GROWTH` |
| **A** | Annual Earnings | 3Y EPS growth > 10% | `CANSLIM_MIN_A_EPS_GROWTH` |
| **N** | New Highs | Within 2% of 52-week high (technical trigger) | `PIVOT_PROXIMITY` |
| **S** | Supply & Demand | Volume surge >= 1.4x avg (technical trigger) | `VOLUME_SURGE_MIN` |
| **L** | Leader vs Laggard | RS rating percentile, weighted 4-period momentum | — |
| **I** | Institutional Sponsorship | > 5 distinct institutional holders | `CANSLIM_MIN_INST_HOLDERS` |
| **M** | Market Direction | **Actively enforced:** SPY vs SMA-200. Bull → buy/park. Bear → liquidate ETF, hold cash. | `MARKET_DIRECTION_TICKER`, `MARKET_DIRECTION_SMA_WINDOW` |

---

## Buy Rules

Buys execute at **9:30–9:45 AM ET** via `run_market_open_buys()`. The function runs a **5-step cascade**: primary CANSLIM triggers → momentum triggers → ETF cash parking. Every trigger passes these gates — all must pass for an order to be placed:

| # | Gate | Logic | Config |
|---|------|-------|--------|
| 1 | **Stock capacity** | Skip if **stock-only** count ≥ MAX_POSITIONS (ETF parking slots are displaceable) | `MAX_POSITIONS=4` |
| 2 | **Trigger freshness** | Only consider triggers from last N days | `TRIGGER_LOOKBACK_DAYS=3` |
| 3 | **Not already held** | Skip if ticker already in portfolio | — |
| 4 | **Cooling-off period** | Skip if ticker was sold within last N days | `COOLING_OFF_DAYS=3` |
| 5 | **Sufficient cash** | Skip if available cash < minimum position floor | `MIN_POSITION_SIZE=5000` |
| 6 | **Pivot extension** | Skip if live price > N% above breakout pivot | `MAX_PIVOT_EXTENSION=0.05` |
| 7 | **Share count** | Compute shares = position_size / live_price; skip if 0 | — |

**Position sizing:** `available_cash ÷ remaining_stock_slots` — equal-weight across unfilled stock slots only.

> [!IMPORTANT]
> Gate 6 (pivot extension) enforces O'Neil's buy zone rule: a stock that has already moved >5% beyond
> its breakout pivot is considered "extended" and skipped. Critical when the bot recovers
> from downtime and evaluates triggers that are 1–2 days old.

> [!IMPORTANT]
> Fill verification uses `ib.portfolio()` (not `trade.orderStatus`) to prevent ghost positions
> from IBKR paper trading's "Cancelled and Resubmitted" order status (Error 10349).

---

## Sell Rules

| Rule | Trigger | Default Threshold | Config Variable |
|------|---------|-------------------|-----------------| 
| Bear market exit | SPY below SMA-200 | Liquidate ETF parking → hold cash | `MARKET_DIRECTION_TICKER`, `MARKET_DIRECTION_SMA_WINDOW` |
| Trailing Stop Loss | Price falls below high-water mark | -7% from highest price reached | `STOP_LOSS_PCT` |
| Profit Target | Price rises from entry | +25% from fill price | `PROFIT_TARGET_PCT` |
| Power Hold activation | Rapid early surge | ≥20% gain in ≤21 days | `POWER_HOLD_GAIN_TRIGGER`, `POWER_HOLD_DAYS_LIMIT` |
| Power Hold duration | Profit target suspended for | 8 weeks | `POWER_HOLD_DURATION_WEEKS` |
| Stale Rotation | Sideways holder, portfolio full, fresh trigger exists | Held ≥15 days with <3% gain | `STALE_HOLD_DAYS`, `STALE_HOLD_MAX_GAIN` |
| Cooling-off | Re-buy blocked after a stop-out | 3 days | `COOLING_OFF_DAYS` |

After every sell, `run_etf_parking()` immediately re-parks the freed slot (QQQ if bull, cash if bear).

---

## Configuration Reference

All strategy parameters are set in `.env`. Defaults are shown — override any value without touching code.

### Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `FMP_API_KEY` | — | Financial Modeling Prep API key |
| `SUPABASE_URL` | — | Supabase project URL |
| `SUPABASE_KEY` | — | Supabase service role key |
| `IB_GATEWAY_HOST` | `localhost` | IB Gateway hostname (`ib-gateway` in Docker) |
| `IB_GATEWAY_PORT` | `7497` | IB Gateway API port |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather (leave empty to disable) |
| `TELEGRAM_CHAT_IDS` | — | Comma-separated recipient chat IDs |

### Portfolio Management

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITIONS` | `4` | Maximum concurrent positions (stocks + ETF parking combined) |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum USD position size — skip if position would be smaller |

### Exit & Hold Parameters (`execution_agent.py`)

| Variable | Default | Description |
|----------|---------|-------------|
| `STOP_LOSS_PCT` | `0.07` | Trailing stop distance — sell if price falls this % below high-water mark |
| `PROFIT_TARGET_PCT` | `0.25` | Take-profit — sell when position gains this % from entry |
| `POWER_HOLD_GAIN_TRIGGER` | `0.20` | Surge required to activate Power Hold (20%) |
| `POWER_HOLD_DAYS_LIMIT` | `21` | Power Hold only activates if surge occurs within this many days of purchase |
| `POWER_HOLD_DURATION_WEEKS` | `8` | Weeks the profit target is suspended after Power Hold activates |
| `COOLING_OFF_DAYS` | `3` | Days before a stopped-out ticker can be re-bought |
| `TRIGGER_LOOKBACK_DAYS` | `3` | Days back to look for valid breakout triggers (covers weekends/holidays) |
| `MAX_PIVOT_EXTENSION` | `0.05` | Skip buy if live price is already >5% above the trigger's pivot close |
| `STALE_HOLD_DAYS` | `15` | Min days held before a sideways position qualifies for rotation |
| `STALE_HOLD_MAX_GAIN` | `0.03` | Max gain (decimal) that qualifies as "sideways" — 0.03 = within 3% of entry |

### Fundamental Screener (`fundamental_screener.py`)

| Variable | Default | Description |
|----------|---------|-------------|
| `CANSLIM_MIN_Q_EPS_GROWTH` | `0.18` | Minimum quarterly EPS growth rate (CANSLIM "C") |
| `CANSLIM_MIN_A_EPS_GROWTH` | `0.10` | Minimum annual EPS growth rate (CANSLIM "A") |
| `CANSLIM_MIN_INST_HOLDERS` | `5` | Minimum distinct institutional holders (CANSLIM "I") |
| `CANSLIM_WATCHLIST_SIZE` | `90` | Max candidates written to watchlist per screening run |
| `WATCHLIST_PRUNE_DAYS` | `56` | Days to retain watchlist rows in Supabase |
| `API_CONCURRENCY` | `10` | Max parallel FMP API calls during screening |

### Technical Screener (`technical_screener.py`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SMA_WINDOW` | `50` | Moving average period for trend filter |
| `VOLUME_AVG_WINDOW` | `50` | Period for computing the volume baseline |
| `VOLUME_SURGE_MIN` | `1.40` | Min volume surge ratio to qualify as a breakout (1.40 = 40% above avg) |
| `ROLLING_HIGH_WINDOW` | `252` | Trading days used to compute the rolling high |
| `PIVOT_PROXIMITY` | `0.98` | Price must be within this fraction of rolling high (0.98 = within 2%) |
| `MIN_PRICE_HISTORY` | `50` | Min days of price history required to analyze a ticker |
| `FMP_HISTORY_DAYS` | `380` | Calendar days of EOD data fetched from FMP per ticker |
| `TRIGGER_PRUNE_DAYS` | `56` | Days to retain daily_trigger rows in Supabase |

### Momentum Secondary Screener (`momentum_screener.py`)

| Variable | Default | Description |
|----------|---------|-------------|
| `MOMENTUM_MIN_Q_EPS_GROWTH` | `0.10` | Relaxed quarterly EPS threshold (vs 18% CANSLIM) |
| `MOMENTUM_MIN_INST_HOLDERS` | `3` | Relaxed institutional holder minimum (vs 5 CANSLIM) |
| `MOMENTUM_VOLUME_SURGE_MIN` | `1.20` | Pass 2 relaxed volume surge (vs 1.40 primary) |
| `MOMENTUM_PIVOT_PROXIMITY` | `0.95` | Pass 2 relaxed proximity — within 5% of 52w high |
| `MOMENTUM_TRIGGER_PRUNE_DAYS` | `56` | Days to retain momentum_trigger rows in Supabase |

### ETF Cash Parking / Market Direction Filter

| Variable | Default | Description |
|----------|---------|-------------|
| `ETF_PARKING_ENABLED` | `true` | Master switch — disable to always hold cash |
| `ETF_PARKING_TICKER` | `QQQ` | Ticker to park idle cash in (NASDAQ-100, CANSLIM growth bias) |
| `ETF_PARKING_MAX_SLOTS` | `4` | Max slots eligible for parking (SMA200 is the safety valve) |
| `MARKET_DIRECTION_FILTER_ENABLED` | `true` | Enable bear market detection and ETF liquidation |
| `MARKET_DIRECTION_SMA_WINDOW` | `200` | SMA period for market direction (O'Neil standard: 200) |
| `MARKET_DIRECTION_TICKER` | `SPY` | Market direction gauge (S&P 500) |

---

## Running the System

### Local (Development)

```bash
# 1. Copy and fill environment variables
cp .env.example .env

# 2. Run the fundamental screener (weekly)
python fundamental_screener.py

# 3. Run the technical screener (daily, after market close)
python technical_screener.py

# 4. Run the momentum screener (daily, after market close — after technical_screener)
python momentum_screener.py

# 5. Run the execution agent daemon (during market hours)
python execution_agent.py

# 6. Mock-sell a position for testing (no IBKR connection needed)
python execution_agent.py --mock-sell AAPL --price 195.50 --reason "Test exit"
```

### Docker (Production)

```bash
docker compose up -d
```

Key services in `docker-compose.yml`:
- `ib-gateway` — Interactive Brokers Gateway (API port 4004)
- `execution-agent` — The main daemon
- `backend` — FastAPI dashboard backend

### Manual Buy Trigger (one-off)

```bash
# Run inside the running container — interactive prompt per ticker
docker exec -it execution-agent python force_buy.py
```

Runs the full cascade (daily_triggers → momentum_triggers → ETF parking) with Y/N confirmation per buy.

---

## Supabase Schema

| Table | Key Columns | Purpose | Retention |
|-------|------------|---------|---------|
| `watchlist` | `ticker`, `composite_score`, `q_eps_growth` | Fundamental screener top-N output | 56 days rolling |
| `daily_triggers` | `ticker`, `triggered_at`, `volume_surge`, `pivot_distance_pct` | CANSLIM breakout signals | 56 days rolling |
| `momentum_triggers` | `ticker`, `triggered_at`, `volume_surge`, `pivot_distance_pct` | Secondary relaxed breakout signals | 56 days rolling |
| `portfolio_positions` | `ticker`, `buy_price`, `high_water_mark`, `profit_target`, `is_power_hold`, **`buy_source`** | Open positions ledger | Until sell/close |
| `trade_history` | `ticker`, `buy_price`, `sell_price`, `sell_date`, `sell_reason`, `profit_loss` | Closed trade audit log | Permanent |
| `account_balances` | `key`, `value` | IBKR cash balance sync (single row: `ibkr_cash_balance`) | Live upsert |

**`portfolio_positions.buy_source` values:**
- `"daily_triggers"` — CANSLIM primary breakout
- `"momentum_triggers"` — secondary relaxed screener
- `"etf_parking"` — QQQ idle-slot position (displaceable, not monitored for stop/profit)

---

## LLM Maintenance Policy

> [!IMPORTANT]
> These markdown files are **living documentation** tied directly to the source code.
> They must be updated whenever the corresponding source files change.

### How These Docs Are Maintained

The component docs are stored as **Knowledge Items** in the AI assistant's knowledge base. This means:

1. **Auto-loaded at conversation start** — The assistant receives summaries of all knowledge items and reads
   the relevant ones before answering questions about this codebase.
2. **Triggered on code changes** — When you modify source files, ask the assistant to
   update the corresponding doc.
3. **Accurate by design** — Docs are generated from actual code, not written by hand,
   so they reflect real thresholds, formulas, and logic.

### What Triggers a Doc Update

| Change | Doc to Update |
|--------|--------------|
| Modify `fundamental_screener.py` | `fundamental_screener.md` |
| Modify `technical_screener.py` | `technical_triggers.md` |
| Modify `momentum_screener.py` | `momentum_screener.md` |
| Modify `run_market_open_buys()` in `execution_agent.py` | `buy_logic.md` |
| Modify `monitor_portfolio_intraday()` or `execute_sell()` | `sell_logic.md` |
| Add `.env` variables | `README.md` Configuration Reference section |

### Limitations

The assistant **cannot** automatically detect code changes without being asked.
To ensure docs stay accurate:
- Mention doc updates when requesting code changes
- Or periodically ask: *"Are the trading bot docs still accurate?"* — the assistant will
  re-read the source files and flag any drift.
