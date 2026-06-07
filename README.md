# CAN SLIM AI Trading Bot

A premium, growth-stock screening and paper-trading bot implementing the investment methodology described in William J. O'Neil's classic, *"How to Make Money in Stocks (Fourth Edition)"*.

This full-stack application scores watchlists, visualizes price breakouts with moving averages (SMAs), simulates paper trading with automated risk boundaries, and runs historical backtests.

---

## CAN SLIM Criteria Evaluated

- **C - Current Quarterly Earnings**: Growth rate >= 25% YoY, accelerating growth trends, and quarterly revenue increases.
- **A - Annual Earnings Increases**: Compound annual growth rate over 3 years >= 20%, and Return on Equity (ROE) >= 17%.
- **N - New Product/Price Highs**: Stock proximity (within 15%) to its 52-week price highs (breakout setup).
- **S - Supply and Demand**: Daily volume surges vs. 50-day average on upward price action (accumulation days).
- **L - Leader or Laggard**: Relative Strength (RS) rating comparison (percentile ranking above 80) and S&P 500 performance comparison.
- **I - Institutional Sponsorship**: Stable holdings owned by mutual funds/banks (optimal ranges between 30% and 85%).
- **M - Market Direction**: S&P 500 (`^GSPC`) and Nasdaq Composite (`^IXIC`) index price trends relative to their 50d and 200d moving averages.

---

## Technology Stack

- **Backend**: FastAPI (Python), SQLite3 Database, `yfinance` API.
- **Frontend**: React (Vite), Recharts, Lucide-React, premium Vanilla CSS with space-navy glassmorphic theme.

---

## Getting Started (Local Development)

To launch the servers concurrently and open the app in your browser, run:

```bash
python run_app.py
```

This master script automatically installs dependencies (Python & Node.js) and orchestrates the backend (port 8000) and frontend (port 5173).

---

## Docker Deployment

This application is fully dockerized. It bundles the React static build outputs directly inside the FastAPI backend, serving the entire application from a single port (`8000`).

### Using Docker Compose (Recommended)

Docker Compose automatically configures persistent storage for your paper trading portfolio database using named volumes.

To build and launch the bot, run:

```bash
docker-compose up --build -d
```

Open your browser to: **`http://localhost:8000`**

To shut down:
```bash
docker-compose down
```

### Using Standard Docker Run

To build the image manually:
```bash
docker build -t ai-trading-bot .
```

To run the container:
```bash
docker run -d -p 8000:8000 --name can-slim-bot ai-trading-bot
```
