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

| Rule | Trigger | Threshold |
|------|---------|-----------|
| Hard Stop-Loss | Price falls | -7% from fill price |
| Profit Target | Price rises | +25% from fill price |
| Power Hold | Surge > 20% in ≤ 21 days | Suspend profit target for 8 weeks |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FMP_API_KEY` | — | Financial Modeling Prep API key |
| `SUPABASE_URL` | — | Supabase project URL |
| `SUPABASE_KEY` | — | Supabase service role key |
| `IB_GATEWAY_HOST` | `localhost` | IB Gateway hostname (use `ib-gateway` in Docker) |
| `IB_GATEWAY_PORT` | `7497` | IB Gateway API port (4004 in Docker via `buy_triggers.py`) |
| `MAX_POSITIONS` | `4` | Maximum concurrent open positions |
| `MIN_POSITION_SIZE` | `5000.0` | Minimum USD position size floor |

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

| Table | Purpose | Retention |
|-------|---------|-----------|
| `watchlist` | Fundamental screener top-90 output | 56 days rolling |
| `daily_triggers` | Technical breakout signals | 56 days rolling |
| `portfolio_positions` | Open positions ledger | Until sell/close |
| `trade_history` | Closed trade audit log | Permanent |
| `account_balances` | IBKR cash balance sync | Live upsert |

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
