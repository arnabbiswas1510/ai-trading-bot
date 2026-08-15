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

### Code vs. Documentation Ground-Truth Rule

> **CRITICAL**: When verifying runtime behavior or trading rules:
> 1. **Always inspect active `.py` code over `.md` docs**: The actual runtime behavior is defined strictly by the executable Python source files (`execution_agent.py`, `flex_query_sync.py`, etc.).
> 2. **Docs are reference context, not execution ground truth**: Markdown files in `docs/` or `decisions/` provide historical context. Never declare a runtime rule based solely on `.md` documentation without reading the corresponding `.py` file.
> 3. **Prune outdated docs immediately**: When refactoring code, update related `.md` files in `docs/` and run `python -m graphify update .` to prevent graph drift.
> 4. **Close the loop**: every ADR-worthy change must update the matching `docs/` page in the same commit — see the [Doc Sync Rule](#-mandatory-doc-sync-rule-adr--docs). Reading code over docs is the *workaround* for drift; keeping docs current is the *fix*.

### Step 3 — Keep the graph fresh after code changes

```bash
python -m graphify update .   # free, no API key, re-extracts changed files only
```

---

## ✏️ MANDATORY: Update Graph, Decisions & Docs After Every Code Change

> **After making any code change — before committing — you MUST:**
> 1. **Run `python -m graphify update .`** to keep `graphify-out/graph.json` current
> 2. **Write or update a `decisions/` ADR** if the change qualifies (see ADR rules below)
> 3. **Update the matching `docs/` page whenever you write an ADR** (see Doc Sync Rule below)

### What triggers each action

| Change type | Update graph? | Write ADR? | Update docs? |
|---|---|---|---|
| Core trading logic (buy/sell/stop/screen) | ✅ Always | ✅ Always | ✅ Always |
| Schema migration (new SQL file) | ✅ Always | ✅ Always | ✅ Always |
| Feature removed or replaced | ✅ Always | ✅ Always | ✅ Always |
| Significant refactor | ✅ Always | ✅ Always | ✅ Always |
| New/changed env var, threshold or default | ✅ Always | ⚠️ If behavioural | ✅ Always |
| Bug fix (obvious root cause) | ✅ Always | ❌ Skip | ⚠️ Only if docs describe the broken behaviour |
| Test added or updated | ✅ Always | ❌ Skip | ❌ Skip |
| UI tweak / dependency bump | ✅ Always | ❌ Skip | ❌ Skip |

The graph update is **always** required after any code change (it is fast and free).
The ADR is only required for meaningful architectural decisions.
**An ADR without a matching docs update is an incomplete change.**

### Commit order

```
1. Make code changes
2. Write ADR in decisions/ (if required)
3. Update matching docs/ page(s)      <-- required whenever step 2 happened
4. python -m graphify update .
5. git add decisions/ docs/ graphify-out/ <changed files>
6. git commit
7. git push
```

> ⚠️ Do NOT skip step 4. A stale graph silently gives wrong answers to future queries.
> ⚠️ Do NOT skip step 3. Stale docs are worse than no docs — they actively mislead,
> and the Code vs. Documentation Ground-Truth Rule exists only because this keeps happening.

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

1. **Update the matching `docs/` page — this is mandatory, not optional** (see below).
2. Run `python -m graphify update .` to keep the graph current.

---

## 🔄 MANDATORY: Doc Sync Rule (ADR ⇒ Docs)

> **Every ADR must be accompanied by an update to the corresponding `docs/` page
> in the same commit. An ADR records *why* a decision was made; the `docs/` page
> records *what the system now does*. Shipping one without the other is what
> creates the doc drift the Ground-Truth Rule already warns about.**

### Which doc to update

Route by what the change touched. If a change spans several areas, update **all**
matching pages.

| What changed in code | Doc page(s) that MUST be updated |
|---|---|
| Buy gates, trigger ranking/sorting, slot allocation, position sizing, cooling-off | `docs/buy_logic.md` |
| Sell rules, stops, trailing logic, kill-switch, thesis stop, EMA/plateau exits, arming | `docs/sell_logic.md` |
| Breakout / pre-breakout detection, quality & final scoring, RS gates | `docs/technical_triggers.md` |
| Fundamental screen thresholds, watchlist construction | `docs/fundamental_screener.md` |
| Any env var, threshold, default, or `config.py` constant | `docs/configuration.md` |
| Container layout, deploy pipeline, gateway/IBKR connectivity | `README.md`, `docs/ibkr_totp_setup.md` |
| New Supabase table/column that code reads or writes | The page describing the rule that consumes it |

### What the doc update must contain

- The **new** behaviour stated as current fact — not a changelog entry, and not
  phrased as "we changed X to Y".
- Any **numeric threshold, default, or env var name** that changed, so
  `docs/configuration.md` and the code never disagree.
- A link back to the ADR for the reasoning:
  `See decisions/YYYY-MM-DD_slug.md for why.`
- **Deletion of anything the change made false.** Removing a stale paragraph is
  as important as adding a new one — per the Ground-Truth Rule, prune outdated
  docs immediately.

### If no doc page covers the change

Say so explicitly in the commit message rather than silently skipping the step,
and create a new page under `docs/` if the behaviour is user- or
operator-visible. Do not invent pages for internal-only refactors.

### Self-check before committing

Ask: *"If someone read only `docs/` after this change, would they be misled about
how the bot now behaves?"* If yes, the docs update is not finished.

---

## 📦 MANDATORY: Clean Patch Files Whenever a Patch Is Requested

> **Whenever asked to create a patch file (instead of pushing directly),
> the patch must contain ONLY valid code artifacts — never noisy or
> incidental churn.**

### What belongs in the patch

- Actual source/config/test changes (`.py`, `.sql`, `.jsx`, etc.)
- The `decisions/` ADR, if one was written for this change
- The minimal graphify graph artifacts needed to keep the graph usable:
  `graphify-out/graph.json`, `graphify-out/graph.html`,
  `graphify-out/GRAPH_REPORT.md`, `graphify-out/manifest.json`, and the
  `.graphify_labels.json` / `.graphify_labels.json.sig` files.

### What must NOT be in the patch

- `graphify-out/cache/**` churn (e.g. AST cache files that changed only
  because of a graphify version bump, not because of real code changes).
  Reset this directory back to its pre-change state before generating the
  patch (e.g. `git checkout HEAD~1 -- graphify-out/cache/` or equivalent)
  so the patch shows zero diff for `graphify-out/cache/`.
- Timestamped backup/snapshot directories graphify creates
  (e.g. `graphify-out/YYYY-MM-DD/`) — these are point-in-time backups,
  not part of the working graph, and must be excluded/untracked before
  the patch is generated.
- Any other incidental artifacts not directly produced by the requested
  change (stray build output, local scratch files, etc.).

### How to generate the patch

1. Make the code change, write the ADR (if required), run
   `python -m graphify update .`.
2. Prune the commit: remove `graphify-out/cache/` churn and any backup
   snapshot dirs from the staged/committed changes so only the graph
   artifacts listed above remain diffed.
3. Verify with `git show --stat` that only intentional files are listed
   before exporting.
4. Generate the patch with `git format-patch -1 HEAD --stdout > <file>.patch`
   (or `git format-patch <range>` for multiple commits).
   Patch filenames must always be prefixed with a serial number (for example:
   `001_fix-...patch`, `002_fix-...patch`).
   Always write the patch file in the repository root (`ai-trading-bot/`), not
   in subdirectories.
5. The patch's commit message must clearly and specifically describe the
   change (what changed, why, and the key files touched) — this becomes
   the comment carried forward into the patch/commit history whenever it
   is applied and pushed. Do not use a generic or placeholder message.

### Serial numbers are never reused

The prefix is a monotonic counter over the **whole history of the project**,
not over the files currently present. Deleting applied patches (see below)
must never cause a number to be issued twice. Before creating a patch, find
the highest number ever used — check the surviving files *and*
`git log --oneline` / prior session notes — and take the next one.

---

## 🧹 MANDATORY: Delete Applied Patch Files on Pull + Hard Reset

> **Trigger.** Whenever I ask you to *pull and hard reset* (or any equivalent
> phrasing: "pull and reset", "reset to remote", "sync with origin",
> "discard local and pull"), you must — after the reset — determine which
> `NNN_*.patch` files in the repository root have already landed on the
> remote, and delete exactly those.

`git reset --hard` does not touch untracked files, so patch files survive the
reset and accumulate. Once a patch's contents are in `origin`, the file is
dead weight and actively confusing: it looks like outstanding work.

### Procedure

1. `git fetch origin` then `git reset --hard origin/<branch>`.
   Warn me first if the working tree has uncommitted changes — a hard reset
   destroys them.
2. **Verify the reset actually happened before classifying anything:**

   ```bash
   test "$(git rev-parse HEAD)" = "$(git rev-parse origin/<branch>)" || exit 1
   ```

   This guard is not optional. The classification below tests the *working
   tree*, not the remote, so running it while local commits are still
   unpushed reports those commits as "applied" and would delete the patch
   that is the only record of them.
3. For **each** `NNN_*.patch` in the repository root, classify it against the
   freshly-reset tree:

   ```bash
   # Already applied: every hunk is present, so it reverses cleanly.
   git apply --reverse --check <file>.patch   && echo APPLIED

   # Not applied: it still applies forward cleanly.
   git apply --check <file>.patch             && echo OUTSTANDING
   ```

4. If **neither** check passes, re-run the reverse check while ignoring the
   generated graph artifacts before concluding anything:

   ```bash
   git apply --reverse --check --exclude='graphify-out/*' <file>.patch \
     && echo APPLIED_SOURCE
   ```

   This step exists because `graphify-out/**` is regenerated, not authored.
   Any `graphify update` commit landing *after* the patch was applied — including
   the routine "Update knowledge graph" commit — rewrites `graph.json`,
   `manifest.json` and friends, so those hunks can never reverse cleanly even
   though the patch is fully applied. Without this step every patch that has
   been through a normal apply-then-update cycle is misreported as ambiguous
   and is never cleaned up.

   Only `graphify-out/*` may be excluded. Never exclude a source, test, doc,
   migration or `decisions/` path to force a patch into the "applied" bucket.

5. Act on the classification:

   | Result | Action |
   |---|---|
   | Reverse-check passes | **Delete** — the change is on the remote |
   | Reverse-check passes ignoring `graphify-out/*` | **Delete** — every authored hunk is on the remote; only regenerated graph output differs |
   | Forward-check passes | **Keep** — still outstanding, and say so |
   | Neither passes | **Keep** — partially applied or superseded; report it and do not guess |

6. Report what was deleted and what was kept, with the reason for each.

### Why content-based, not name-based

Do **not** decide by matching commit subjects in `git log`. Patches get
squashed, reworded, rebased and amended before they land, so the subject on
the remote frequently differs from the one in the patch file. `git apply
--reverse --check` tests the only thing that matters: whether the tree already
contains those changes. It is also correct when several patches were collapsed
into one commit before pushing.

### Safety rules

- **Never delete a patch that is not fully represented in the remote.** When
  the classification is ambiguous, keep the file and tell me.
- Delete only files matching `NNN_*.patch` in the repository root. Never touch
  `migrations/*.sql`, and never treat a `.patch` file elsewhere in the tree as
  in scope.
- This cleanup runs **only** on an explicit pull/hard-reset request. Do not
  opportunistically delete patch files during unrelated work.

---

## 🔑 Local Credentials (never committed)

Supabase and FMP credentials for this project live **outside the repository** at:

```
~/.config/ai-trading-bot/secrets.env      # chmod 600, outside git — cannot be pushed
```

It defines `SUPABASE_URL`, `SUPABASE_KEY`, and `FMP_API_KEY`.

Load it before any task that queries Supabase or FMP:

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
```

> Never copy these values into the repo, into `.env`, into docs, ADRs, or patch
> files. Reference the path only. If a credential is ever needed, read it from
> this file at runtime.
