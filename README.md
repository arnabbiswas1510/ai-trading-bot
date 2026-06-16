# AI Trading Bot — CANSLIM Momentum Strategy

An automated equity trading system implementing the **CANSLIM** methodology developed by William O'Neil.
The bot screens fundamentally strong stocks, detects technical breakout triggers, and executes
market orders via Interactive Brokers (IBKR), running as a fully containerized daemon.

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
│  EPS growth filter     │        │  buys() [buy logic]                 │
│  Composite scoring     │        │                                     │
│  Top 90 → Supabase     │        │  9:45 AM–4 PM → monitor_portfolio_ │
│  watchlist table       │        │  intraday() [sell logic]            │
└────────────┬───────────┘        │                                     │
             │                    │  Every cycle → reconcile_with_      │
             ▼                    │  ibkr() [sync]                      │
┌────────────────────────┐        └──────────────┬──────────────────────┘
│  TECHNICAL SCREENER    │                       │
│  technical_screener.py │                       │
│  (daily cron)          │                       │
│                        │                       │
│  Reads watchlist       │                       │
│  SMA-50 check          │                       │
│  40%+ volume surge     │    ┌──────────────────▼──────────────────────┐
│  Within 2% of 52w high │    │            SUPABASE DATABASE            │
│  → daily_triggers table│───►│  watchlist · daily_triggers             │
└────────────────────────┘    │  portfolio_positions · trade_history    │
                              │  account_balances                       │
                              └─────────────────────────────────────────┘
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
| [Buy Logic](docs/buy_logic.md) | `execution_agent.py` → `run_market_open_buys()` | Market open buys, position sizing, stop/target setup |
| [Sell Logic](docs/sell_logic.md) | `execution_agent.py` → `monitor_portfolio_intraday()` | Stop-loss, profit target, Power Hold Rule, manual close reconciliation |

---

## CANSLIM Strategy Summary

| Letter | Dimension | Pipeline Implementation |
|--------|-----------|------------------------|
| **C** | Current Earnings | Q EPS growth > 18% (pipeline) / scored 0–15 pts (full screener) |
| **A** | Annual Earnings | 3Y EPS growth > 10% (pipeline) / CAGR + ROE scored 0–15 pts |
| **N** | New Highs | Within 2% of 52-week high (technical trigger) |
| **S** | Supply & Demand | Volume surge >= 1.4x avg (technical trigger) |
| **L** | Leader vs Laggard | RS rating percentile, weighted 4-period momentum |
| **I** | Institutional Sponsorship | > 5 distinct institutional holders (FMP v3 endpoint) |
| **M** | Market Direction | S&P 500 + Nasdaq vs. SMA-50 / SMA-200 |

---

## Exit Rules

| Rule | Trigger | Default Threshold | Config Variable |
|------|---------|-------------------|-----------------|
| Trailing Stop Loss | Price falls below high-water mark | -7% from highest price reached | `STOP_LOSS_PCT` |
| Profit Target | Price rises from entry | +25% from fill price | `PROFIT_TARGET_PCT` |
| Power Hold activation | Rapid early surge | ≥20% gain in ≤21 days | `POWER_HOLD_GAIN_TRIGGER`, `POWER_HOLD_DAYS_LIMIT` |
| Power Hold duration | Profit target suspended for | 8 weeks | `POWER_HOLD_DURATION_WEEKS` |
| Stale Rotation | Sideways holder, portfolio full, fresh trigger exists | Held ≥15 days with <3% gain | `STALE_HOLD_DAYS`, `STALE_HOLD_MAX_GAIN` |
| Cooling-off | Re-buy blocked after a stop-out | 3 days | `COOLING_OFF_DAYS` |

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
| `MAX_POSITIONS` | `4` | Maximum concurrent open positions |
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

# 4. Run the execution agent daemon (during market hours)
python execution_agent.py

# 5. Manual buy trigger (connects to ib-gateway container)
python buy_triggers.py

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

---

## Supabase Schema

| Table | Key Columns | Purpose | Retention |
|-------|------------|---------|--------- |
| `watchlist` | `ticker`, `composite_score`, `q_eps_growth` | Fundamental screener top-N output | 56 days rolling |
| `daily_triggers` | `ticker`, `triggered_at`, `volume_surge`, `pivot_distance_pct` | Technical breakout signals | 56 days rolling |
| `portfolio_positions` | `ticker`, `buy_price`, `high_water_mark`, `profit_target`, `is_power_hold` | Open positions ledger | Until sell/close |
| `trade_history` | `ticker`, `buy_price`, `sell_price`, `sell_date`, `sell_reason`, `profit_loss` | Closed trade audit log | Permanent |
| `account_balances` | `key`, `value` | IBKR cash balance sync (single row: `ibkr_cash_balance`) | Live upsert |

---

## LLM Maintenance Policy

> [!IMPORTANT]
> These markdown files are **living documentation** tied directly to the source code.
> They must be updated whenever the corresponding source files change.

### How These Docs Are Maintained

The 4 component docs ([fundamental_screener.md](docs/fundamental_screener.md), [technical_triggers.md](docs/technical_triggers.md),
[buy_logic.md](docs/buy_logic.md), [sell_logic.md](docs/sell_logic.md)) are stored as
**Knowledge Items** in the AI assistant's knowledge base. This means:

1. **Auto-loaded at conversation start** — The assistant receives summaries of all knowledge items and reads
   the relevant ones before answering questions about this codebase.
2. **Triggered on code changes** — When you modify source files, ask the assistant to
   update the corresponding doc. Example: *"I changed the stop-loss to 8% — update the sell logic doc."*
3. **Accurate by design** — Docs are generated from actual code, not written by hand,
   so they reflect real thresholds, formulas, and logic.

### What Triggers a Doc Update

| Change | Doc to Update |
|--------|--------------|
| Modify `fundamental_screener.py` | `fundamental_screener.md` |
| Modify `backend/screener.py` | `fundamental_screener.md` (CANSLIM scoring section) |
| Modify `technical_screener.py` | `technical_triggers.md` |
| Modify `run_market_open_buys()` in `execution_agent.py` | `buy_logic.md` |
| Modify `monitor_portfolio_intraday()` or `execute_sell()` | `sell_logic.md` |
| Modify `backend/fmp_client.py` | Any doc using FMP endpoints |

### Limitations

The assistant **cannot** automatically detect code changes without being asked.
To ensure docs stay accurate:
- Mention doc updates when requesting code changes
- Or periodically ask: *"Are the trading bot docs still accurate?"* — the assistant will
  re-read the source files and flag any drift.
