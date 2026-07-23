# CAN SLIM AI Trading Bot — Project Context & Memory

---

## 🔍 MANDATORY: Graph-First Rule

> **Before reading any source file or running grep for any architectural, structural,
> or cross-file question — ALWAYS query the knowledge graph first.**

`graphify-out/graph.json` is the persistent, pre-computed knowledge graph of this
entire codebase. It covers 1,196 nodes, 1,901 edges, and 95 named communities
including code, SQL migrations, docs, and ADRs in `decisions/`.

### Step 1 — Query the graph

```bash
# Ask a free-form question (BFS traversal across the graph)
python -m graphify query "what controls hold duration and exit timing"

# Explain a specific node and all its neighbours
python -m graphify explain "monitor_portfolio_intraday"

# Shortest path between two concepts
python -m graphify path "execute_sell" "TelegramNotifier"
```

### Step 2 — Only go to source files if the graph is insufficient

| The graph answers these directly ✅ | Go to source files for these ❌ |
|---|---|
| What calls / imports X? | Exact literal value of a constant |
| What breaks if I change X? | Live logs / runtime state |
| How do modules connect? | Syntax errors / line-level edits |
| Why was X designed this way? (→ `decisions/`) | Current portfolio / Supabase data |
| What communities / subsystems exist? | SSH / server diagnostics |

### Step 3 — Keep the graph fresh after code changes

```bash
python -m graphify update .   # free, no API key, re-extracts changed files only
```

---

## ✏️ MANDATORY: Update Graph & Decisions After Every Code Change

> **After making any code change — before committing — you MUST:**
> 1. **Run `python -m graphify update .`** to keep `graphify-out/graph.json` current
> 2. **Write or update a `decisions/` ADR** if the change qualifies (see ADR rules below)

### What triggers both actions

| Change type | Update graph? | Write ADR? |
|---|---|---|
| Core trading logic (buy/sell/stop/screen) | ✅ Always | ✅ Always |
| Schema migration (new SQL file) | ✅ Always | ✅ Always |
| Feature removed or replaced | ✅ Always | ✅ Always |
| Significant refactor | ✅ Always | ✅ Always |
| Bug fix (obvious root cause) | ✅ Always | ❌ Skip |
| Test added or updated | ✅ Always | ❌ Skip |
| UI tweak / dependency bump | ✅ Always | ❌ Skip |

The graph update is **always** required after any code change (it is fast and free).
The ADR is only required for meaningful architectural decisions.

### Commit order

```
1. Make code changes
2. Write ADR in decisions/ (if required)
3. python -m graphify update .
4. git add decisions/ graphify-out/ <changed files>
5. git commit
6. git push
```

> ⚠️ Do NOT skip step 3. A stale graph silently gives wrong answers to future queries.

---

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

---

## 📝 Architectural Decision Records (ADRs)

The `decisions/` folder contains ADR files that explain **why** design choices were made.
These are ingested by graphify so decisions are linked to the code nodes they produced.

### When I must write an ADR

After any of the following, **automatically** create or update a file in `decisions/`:

- Changes to core trading logic: buy gates, sell logic, stop-loss rules, screening thresholds
- Schema migrations that reflect a data model change
- Removal or replacement of a feature (capture what was removed and why)
- Significant refactors that solve a named problem (e.g. the TRV incident)

Do NOT write ADRs for: obvious bug fixes, test additions, UI tweaks, or dependency bumps.

### File naming

```
decisions/YYYY-MM-DD_short-slug.md
```

Use the actual date of the change. Use the commit message as a starting point for the slug.

### After writing an ADR

Run `python -m graphify update .` to keep the graph current.
