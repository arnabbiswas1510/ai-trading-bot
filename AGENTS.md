# CAN SLIM AI Trading Bot — Project Context & Memory

---

## 🖥️ New Machine Setup (REQUIRED BEFORE ANY WORK)

If `graphify-out/graph.json` exists but the `graphify` CLI is missing, install it first:

```bash
pip install -r requirements-dev.txt
# Verify:
python -m graphify --version   # should print 0.9.24 or later
```

The `graphify` CLI is mandatory for this project (see Graph-First Rule below).
The graph is pre-built and committed — no API key or rebuild needed on a fresh clone.

---

## 🔍 MANDATORY: Graph-First Rule

> **Before reading any source file or running grep for any architectural, structural,
> or cross-file question — ALWAYS query the knowledge graph first.**

`graphify-out/graph.json` is the persistent, pre-computed knowledge graph of this
entire codebase. It covers 1,210 nodes, 1,920 edges, and 97 named communities
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

A **live** growth-stock trading bot implementing the CAN SLIM methodology from William J. O'Neil's *"How to Make Money in Stocks (Fourth Edition)"*.
Executes live IBKR trades, screens watchlists for breakouts, monitors positions every 15 minutes, and runs historical backtests.

---

## ⚡ Tech Stack & Architecture

The application uses a decoupled cloud screening and local execution environment:

1. **Cloud Screener (GitHub Actions + Supabase)**:
   * Weekend fundamental scans and daily technical breakout scans run on GitHub Actions.
   * Scans write results directly to a Supabase cloud database (`watchlist` and `daily_triggers` tables).
2. **Local Self-Hosted Execution (DietPi Docker)**:
   * **`ib-gateway`**: Headless Interactive Brokers Gateway container (`ghcr.io/gnzsnz/ib-gateway`) managing the live brokerage connection (port 4000).
   * **`execution-agent`**: Python daemon (`execution_agent.py`) checking daily triggers, placing live orders at market open, and monitoring positions every 15 minutes.
   * **`trading-bot`**: FastAPI backend and React dashboard served at `http://localhost:8000`.
3. **Database Sync Split**:
   * **Supabase (Cloud)**: Stores active watchlists, daily breakout triggers, open portfolio positions (`portfolio_positions`), and trade history (`trade_history`).
   * **SQLite (Local `trading_bot.db`)**: User settings only (initial balance, stop-loss %, FMP API keys) — avoids polluting the cloud DB.

---

## ⚙️ Network & API Integrations

* **Gateway Socket Bridge**:
  The headless IB Gateway binds internally to port 4000. The `execution-agent` container connects to `ib-gateway:4000`.
* **IBKR Account Selection**:
  `get_ibkr_account()` prefers live accounts (`U...`). If both live and paper (`DU...`) accounts are visible, it raises — set `IBKR_ACCOUNT=<live_id>` in `.env` to be explicit.
* **Brokerage Write Access**:
  `READ_ONLY_API=no` is set in the `ib-gateway` container config to allow order submission.
* **FMP Pricing Integration**:
  Real-time stock prices via Financial Modeling Prep (FMP) Stable Quote API.

---

## 🚨 Timezone & Execution Rules

* **America/New_York Sync**:
  All market-hours logic uses `zoneinfo` with `America/New_York` to avoid UTC mismatches.
* **Portfolio Sizing**:
  Capped at exactly **4 concurrent active positions**. Per-trade allocation:
  `position_size = available_cash / remaining_slots`
  where `remaining_slots = MAX_POSITIONS - len(open_positions)`, recomputed at each buy.
* **Risk Boundaries**:
  * **Trailing Stop**: 7% from the position's peak price (tightens dynamically with profit and age).
  * **EMA-21 Exit**: Close below EMA-21 × 0.99 triggers EOD sell (only after day 7 — breakout consolidation phase is protected).

---

## 🧮 Cash & State Synchronization

* **Dynamic Cash Balance Formula**:
  The web app API calculates cash on-the-fly to avoid drift between SQLite and the execution agent:

  **Primary**: live `ibkr_cash_balance` synced from IBKR by the execution agent (stored in `account_balances` table).
  **Fallback** (when no synced value yet): `Initial Balance + Realized P&L − Open Position Cost`

* **Portfolio Balance Reset**:
  To reset tracked balances, clear rows from the Supabase `trade_history` table.
  ⚠️ This does **NOT** affect live IBKR positions — only local accounting state.

---

## 📐 Separate Container Architectural Rationale

We maintain strict separation between the `execution-agent` and the `trading-bot` containers:

* **Isolated Failure Domain**: Risk monitoring (trailing stop, EMA exit) must not crash if the dashboard or API goes down.
* **Security**: Only the execution agent has brokerage gateway write access.
* **Single Responsibility**: Cleaner Dockerfiles, focused dependencies, easier testing.

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
