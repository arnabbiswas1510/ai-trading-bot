# CAN SLIM AI Trading Bot — Project Context & Memory

## Project Overview
A premium growth-stock screening and paper-trading bot implementing the methodology described in William J. O'Neil's classic, *"How to Make Money in Stocks (Fourth Edition)"*.
This full-stack application scores watchlists, visualizes price breakouts with moving averages, simulates paper trading with automated risk boundaries, and runs historical backtests.

---

## ⚡ Tech Stack & Architecture

The application has been migrated from a monolithic SQLite setup to a modern, decoupled cloud screening and local execution environment:

1. **Cloud Screener (GitHub Actions + Supabase)**:
   * Weekend fundamental scans and daily technical breakout scans run on GitHub Actions.
   * Scans write results directly to a Supabase cloud database (`watchlist` and `daily_triggers` tables).
2. **Local Self-Hosted Execution (WSL Docker Setup)**:
   * **`ib-gateway`**: Headless Interactive Brokers Gateway container (`ghcr.io/gnzsnz/ib-gateway`) managing the paper trading connection.
   * **`execution-agent`**: Python daemon (`execution_agent.py`) checking daily triggers, placing orders at market open, and checking positions every 15 minutes.
   * **`trading-bot`**: FastAPI backend and static React user interface serving the dashboard at `http://localhost:8000`.
3. **Database Sync Split**:
   * **Supabase (Cloud)**: Stores active watchlists, daily breakout triggers, open portfolio positions (`portfolio_positions`), and trade history (`trade_history`).
   * **SQLite (Local `trading_bot.db`)**: Kept inside the web application container to store user settings (initial balance, stop-loss and profit target percentages, FMP API keys) to avoid polluting the cloud DB.

---

## ⚙️ Network & API Integrations

* **Gateway Socket Bridge**:
  The headless IB Gateway binds internally to loopback (`127.0.0.1:4002`) inside its container. To allow the `execution-agent` container to connect, we mapped the container's external `socat` TCP tunnel port (`4004`) to host port `4002` in `docker-compose.yml`, and configured the agent to connect to `ib-gateway:4004`.
* **Brokerage Write Access**:
  To resolve the `The API interface is currently in Read-Only mode` order error, we set the environment variable `READ_ONLY_API=no` inside the `ib-gateway` container config, enabling automated setting reconfiguration.
* **FMP Pricing Integration**:
  The system queries current real-time stock prices from the Financial Modeling Prep (FMP) Stable Quote API.

---

## 🚨 Timezone & Execution Rules

* **America/New_York Sync**:
  Because Docker containers run in UTC by default, using raw `datetime.datetime.now()` caused a critical timezone mismatch (checking for market open at 9:30 AM UTC / 5:30 AM EST). The execution agent was updated to explicitly compute dates and times using the `America/New_York` timezone (`zoneinfo`), ensuring proper alignment with US trading hours.
* **Portfolio Sizing**:
  Capped at exactly **5 concurrent active positions**, allocation size is a flat **`$20,000 USD`** block per position.
* **Risk Boundaries**:
  * **Absolute Stop-Loss**: 7% below average fill price.
  * **Profit Target**: 25% above average fill price.
  * **8-Week Power Holding Rule**: If a stock gains 20%+ in less than 21 days from purchase, the execution agent activates a power hold flag in Supabase. The stock is exempt from the 25% target until the 8-week hold period expires.

---

## 🧮 Cash & State Synchronization

* **Dynamic Cash Balance Formula**:
  To prevent data drift between the local SQLite database and the background execution agent, the web app's API calculates cash balance on-the-fly:
  $$\text{Cash Balance} = \text{Initial Balance (SQLite settings)} + \text{Realized P\&L (Supabase trade history)} - \text{Open Positions Cost (Supabase portfolio positions)}$$
* **Paper Trading Slate Reset**:
  To reset the paper trading account cash to a clean `$100,000.00` balance, clear all test rows from the Supabase `trade_history` table.

---

## 📐 Separate Container Architectural Rationale

We maintain a strict separation between the `execution-agent` and the `trading-bot` containers:
* **Pros**:
  * **Isolated Failure Domain**: Risk monitoring (7% stop-loss and 25% profit targets) is mission-critical and must not crash if the web dashboard, API endpoints, or database clients experience downtime.
  * **Security**: Only the isolated execution agent has brokerage gateway write access, keeping the public/dashboard web app credentials footprint clean.
  * **Single Responsibility**: Cleaner Dockerfiles, focused dependencies, and easier testing.
