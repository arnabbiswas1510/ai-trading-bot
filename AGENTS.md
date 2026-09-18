# CAN SLIM AI Trading Bot — Project Context & Memory

# Formatting Requirements
- You must always preserve, expose, and display your full internal reasoning and chain-of-thought "thinking lines" in your chat responses.
- Do not collapse, hide, or strip your analytical steps.
- Format your raw thinking process inside standard markdown code blocks or `<think>` tags so they remain permanently visible within the IntelliJ chat screen.

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
2. **Local Self-Hosted Execution (DietPi Docker at `192.168.1.2`)**:
   * **Host Server**: Production DietPi host at `192.168.1.2` (SSH port 2222).
   * **`ib-gateway`**: Headless Interactive Brokers Gateway container (`ghcr.io/gnzsnz/ib-gateway`) managing the live brokerage connection (port 4000).
   * **`execution-agent`**: Python daemon (`execution_agent.py`) checking daily triggers, placing live orders at market open, and monitoring positions every 15 minutes.
   * **`trading-bot`**: FastAPI backend and React dashboard served at `http://localhost:8000` (or `http://192.168.1.2:8000`).
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
  Real-time stock prices via Financial Modeling Prep (FMP) Stable Quote API, used
  by the screener/research for non-held candidates, and as the **fallback** live
  price for held positions when IBKR has no mark (`get_live_price()`).
  **Live-position pricing is IBKR-first** — the execution agent's exit rules and
  account rollup price open positions from IBKR's own `marketPrice` via
  `get_position_price()` / `build_ibkr_price_map()` (non-blocking `ib.portfolio()`,
  never the blocking `reqTickers()`), falling back to FMP only when no mark is
  available. **Dashboard position values** are likewise IBKR-first — IBKR's own
  `marketPrice` / `marketValue` / `unrealizedPNL`, persisted onto
  `portfolio_positions` by `reconcile_with_ibkr()` and rendered with an "as of"
  timestamp — falling back to a **labelled** FMP quote when the broker has no
  mark, and to cost basis only when there is no quote either. The web container
  has no brokerage access, so "no IBKR mark" there means the persisted columns
  are empty. Every fallback is named on screen via `price_source`
  (`IBKR` / `FMP` / `COST_BASIS`); an *unlabelled* third-party price mixed into a
  broker-sourced total remains forbidden. FMP is never written into the
  `portfolio_positions` columns — the fallback is applied at render time only.
  See `decisions/2026-09-04_ibkr-first-live-pricing.md`,
  `decisions/2026-09-03_ibkr-sourced-position-values.md` and
  `decisions/2026-09-04_labelled-fmp-dashboard-fallback.md`.

---

## 🚨 Timezone & Execution Rules

* **America/New_York Sync**:
  All market-hours logic uses `zoneinfo` with `America/New_York` to avoid UTC mismatches.
* **Portfolio Sizing**:
  Capped at exactly **5 concurrent active positions** (`MAX_POSITIONS`, config.py,
  env-overridable; the book was migrated from 4 slots to 5 — see
  `decisions/2026-08-04_backtest-noise-floor-and-slot-count.md` and
  `decisions/2026-08-09_max-positions-single-source.md`). Per-trade allocation:
  `position_size = available_cash / remaining_slots`
  where `remaining_slots = MAX_POSITIONS - len(open_positions)`, recomputed at each buy.
  A slot is counted by ticker existence (`len(holdings)`), not dollar size, so a
  partially scaled-out position still occupies exactly one of the five slots.
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
| Buy gates, trigger ranking/sorting, slot allocation, position sizing, cooling-off | `docs/buy_logic.md`, `README.md` |
| Sell rules, stops, trailing logic, kill-switch, thesis stop, EMA/plateau exits, arming | `docs/sell_logic.md`, `README.md` |
| Breakout / pre-breakout detection, quality & final scoring, RS gates | `docs/technical_triggers.md` |
| Fundamental screen thresholds, watchlist construction | `docs/fundamental_screener.md` |
| Any env var, threshold, default, or `config.py` constant | `docs/configuration.md`, `.env.template` |
| Container layout, deploy pipeline, gateway/IBKR connectivity | `README.md`, `docs/ibkr_totp_setup.md` |
| New Supabase table/column that code reads or writes | The page describing the rule that consumes it |
| **Any rule, constant, function or feature DELETED** | **`docs/retired_code.md`** (see the deletion-logging rule below) |

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

**Step 1 — the grep. This is mandatory and not a judgement call.**

The routing table above is a *starting point*, not an exhaustive list. It tells you
where documentation for a subsystem is *supposed* to live; it cannot tell you where
a stale copy of the value you just changed actually *is*. Only a search can.

For every numeric value, threshold, default and env var name you changed, grep the
whole repository — not just `docs/`:

```bash
# Example: after changing EARLY_LOSS_STOP_PCT from 0.02 to 0.01 and the
# trail ladder from 6.5% to 1.5%
grep -rn "6\.5%\|days 0–1\|days 0-1\|EARLY_LOSS_STOP_PCT" \
  --include=*.md --include=*.template --include=*.py --include=*.js --include=*.jsx \
  . | grep -v node_modules | grep -v graphify-out | grep -v "/dist/"
```

Search for the **distinctive** terms — the env var name, the old percentage as it
is written in prose (`6.5%`), the old day range. Do not grep a bare number like
`0.02`; it matches unrelated code and buries the real hits. Always exclude
`node_modules`, `graphify-out` and `frontend/dist` — the built bundle contains a
copy of every string and will swamp the output.

Every hit showing the **old** value is part of this change. In particular this
catches the three places the routing table alone will miss:

- **`README.md`** — it carries the full tier-by-tier sell-rule spec with live
  numbers, not just deploy instructions.
- **`.env.template`** — drift here is *functional*, not cosmetic: a fresh
  deployment silently inherits the old threshold. This is the highest-severity
  miss on this list and the easiest to overlook.
- **Frontend mirrors** (`frontend/src/lib/positionRules.js`) — these are
  deliberate copies of backend constants and go stale silently.
- **Code comments in unrelated functions** — a threshold is often quoted in the
  rationale for a *different* rule (e.g. the power-hold comments explaining why
  they bypass the profit ladder). These mislead the next reader of that code.
- **`decisions/` ADRs** — see below. Do **not** filter these out of the grep.

**Step 1b — hits inside `decisions/` are not exempt.**

The instinct to skip them is wrong, and it is the specific mistake made on
2026-08-20: the grep surfaced `decisions/2026-08-18_early-dollar-stop.md` and it
was excluded with `grep -v decisions/…` on the reasoning that ADRs are immutable
historical records.

Half of that is right. An ADR's **body** *is* immutable — never rewrite the
decision, the context or the numbers as if they had always said something else.
That destroys the record of what was believed at the time, which is the only
thing an ADR is for.

But the **header is not history, it is a claim about the present.** `Status:
Accepted` on a decision that has since been replaced is simply false, and it is
false in the most damaging way available: the document reads perfectly, so
nothing signals that it is wrong. A future reader — or a future session doing
exactly this grep — will cite a superseded threshold in good faith.

So when a hit lands in `decisions/`:

| Do | Do not |
|---|---|
| Update the `Status:` field — `Superseded by <link>`, `Superseded in part by <link>`, or `Accepted — with one documented exception` | Edit the decision, context or rationale text |
| Add a dated block quote at the top saying what is now false and what still holds | Delete or reword the original numbers |
| Mark any measurement that is now known to be wrong with an explicit **do not cite**, and give the corrected figure | Silently leave a superseded benchmark quotable |

Three distinct triggers, all of which count:

1. **Supersession** — the change replaces a decision an ADR recorded.
2. **Exception** — the change violates an invariant an ADR established. Defend it
   in that ADR or retract the change; do not leave the contradiction unremarked.
3. **Erratum** — the change proves a number a previous ADR published was wrong.
   This one has no code footprint at all, so grep will not find it. It is caught
   only by asking, after any measurement fix: *what did I previously publish
   using this?*

**Step 2 — the read-through.**

Ask: *"If someone read only `README.md`, `docs/` and the `Status:` line of each
ADR after this change, would they be misled about how the bot now behaves?"* If
yes, the update is not finished.

> ⚠️ Do not reason about which documents *ought* to mention the rule and stop
> there. That reasoning is what produces stale docs: it fails precisely when a
> value has been duplicated somewhere the routing table never anticipated. Run
> the grep and let the result — not the mental model — define the file list.

---

## 🗓️ MANDATORY: Name Every Migration `YYYYMMDD_slug.sql`

> **Every file in `migrations/` MUST be named `YYYYMMDD_short_slug.sql`, using the
> date the migration is written. No exceptions, no other prefix, no bare
> `add_thing.sql`.**

```
migrations/20260917_add_rs_percentile.sql     ✅
migrations/add_rs_percentile.sql              ❌ undated
migrations/2026-09-17_add_rs_percentile.sql   ❌ dashes break the sort alignment
migrations/09172026_add_rs_percentile.sql     ❌ not sortable
```

### Why this exact format

`YYYYMMDD` is the only common date format where **lexical order equals
chronological order**. That single property is what makes `ls migrations/`,
`ls -r migrations/`, a file-tree pane and a `git diff --stat` all agree on what
happened when, with no tooling and no metadata.

It matters here specifically because nothing in this repo *runs* migrations.
They are applied by hand in the Supabase SQL Editor, so the filename is the only
record of sequence that exists. When the order is invisible, the question "has
this one been applied yet, and what did it assume was already there?" becomes
unanswerable — which is exactly the condition that produced
`20260813_apply_missing_migrations.sql`, a whole consolidated patch written to
recover from schema drift after migrations were applied out of order or not at
all.

### Which date to use

Use the date you **write** the migration — the same date as the ADR and the
commit. Do not back-date to when the underlying feature was conceived, and do not
forward-date to a planned deployment. If a migration is written on one day and
applied weeks later, the filename still records the former; the latter belongs in
the commit history.

Same-day migrations simply sort alphabetically within the day. That is fine —
never invent a fake time or a sequence number to break the tie.

### When renaming an existing migration

The filename is referenced from code, docs and ADRs — `schema_guard.py` names
migrations in the repair messages it prints to the operator, and
`REPAIR_SCRIPT` hard-codes one. So a rename is a **cross-cutting change**:

1. `git mv` the file so history follows it.
2. Grep the **whole repo** for the old basename and update every hit — `.py`,
   `.md`, `.sql`, `.yml`, `.json`, `.template`, `.jsx`. Exclude `node_modules`,
   `graphify-out`, `frontend/dist` and `*.patch`.
3. Guard against double-prefixing. Match with a negative lookbehind
   (`(?<!\d{8}_)add_thing\.sql`) so a name that is *already* dated is not
   prefixed a second time.
4. **Do not hand-edit `.graphify_ast.json` / `.graphify_detect.json`.** They are
   generated; revert any incidental edits and let `graphify update .` rebuild
   them.
5. Hits inside `decisions/` **are** in scope — an ADR that points at a filename
   which no longer exists is a broken instruction, not a historical record. This
   is a path correction, not a rewrite of the decision, so the
   "never edit an ADR body" rule does not apply.

All 39 existing migrations were normalised to this convention on 2026-09-17; see
`decisions/2026-09-17_migration-naming-convention.md`.

---

## ♻️ MANDATORY: Every Migration Must Be Re-Runnable

> **A migration must succeed, and change nothing, when run a second time.
> Never use a bare `ADD COLUMN`, `DROP COLUMN`, `CREATE TABLE`, `CREATE INDEX`
> or `ADD CONSTRAINT`.**

```sql
ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;     ✅
ALTER TABLE t DROP COLUMN IF EXISTS c;             ✅
CREATE TABLE IF NOT EXISTS t (...);                ✅
ALTER TABLE t ADD COLUMN c TEXT;                   ❌ fails on re-run
```

### Why

Nothing in this repo runs migrations — they are applied by hand in the Supabase
SQL Editor. The operator has no ledger of what has been applied, so the *only*
protection against running one twice is the file being safe to run twice.

This is not hypothetical. On 2026-09-17 `20260624_migration_retention_period.sql`
was re-run by mistake and aborted with `column "retention_period" of relation
"watchlist" already exists`. Nothing was damaged — the SQL Editor wraps a script
in a transaction, so it rolled back — but the error named a migration the
operator had not intended to touch, which is alarming and costs real time to
diagnose. The date-prefix convention above makes this *more* likely, not less:
every migration now sorts together in one list, so picking a long-applied file
out of it is easy.

### Guards must match the *current* schema, not the original intent

`IF NOT EXISTS` is necessary but often not sufficient. A migration written years
ago may reference a column that has since been dropped, and no amount of
`IF NOT EXISTS` saves a statement whose *referenced* object is gone —
`20260624_twr_schema.sql` ends in `ADD PRIMARY KEY (date, key)`, and
`account_balances.key` no longer exists, so that statement now fails outright.

When that happens, wrap the step in a `DO $$ ... $$` block that tests its own
preconditions and `RAISE NOTICE` on skip:

```sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='t' AND column_name='c') THEN
        EXECUTE 'ALTER TABLE t ADD PRIMARY KEY (a, c)';
    ELSE
        RAISE NOTICE 't: step skipped (column "c" no longer exists).';
    END IF;
END $$;
```

Make the historical step **inert**; do not rewrite it to impose the modern
schema. An old migration's job is to record what was done at the time, not to
reshape a table it no longer describes.

### Enforcement

`tests/test_migrations_idempotent.py` scans every file in `migrations/` and fails
on unguarded DDL. It runs in the normal `pytest` suite, so a non-idempotent
migration cannot be committed. Add an end-of-file verification `SELECT` that
reports `OK`/`FAIL` per object, so the operator sees confirmation rather than
silence. See `decisions/2026-09-17_idempotent-migrations.md`.

---

## 🗄️ MANDATORY: Log Every Deletion in `docs/retired_code.md`

> **Before deleting any rule, constant, function or code path, record it in
> `docs/retired_code.md` — in the same commit as the deletion.**

Deleted code is invisible. Once a rule is gone the only trace is a diff nobody
will find, and the reasoning evaporates with it. The predictable result is that
someone re-derives the same idea months later, ships it again, and reintroduces a
bug that was already paid for once.

This is not the same job as an ADR. An ADR says *why a decision was made*;
`docs/retired_code.md` says *what was physically removed, where it lived, and how
to restore it with its context intact*. Shipping one without the other leaves you
knowing a rule was retired but not what its code actually did.

### What triggers an entry

| Deletion | Log it? |
|---|---|
| A trading rule or exit rule | ✅ Always |
| A tunable constant or env var | ✅ Always |
| A function other code called | ✅ Always |
| A schema column the code read | ✅ Always |
| A whole feature or subsystem | ✅ Always |
| Dead code already disabled by default | ✅ Always — *especially* this |
| Renaming a local variable | ❌ Skip |
| Removing a stale comment | ❌ Skip |
| Deleting a test for code that still exists | ❌ Skip |

The "already disabled" row is the one that gets skipped and shouldn't. A rule
that has been off for a month still encodes a measured result, and that
measurement is exactly what a future session needs in order not to re-enable it.

### What each entry must contain

- **The identifiers removed** — constant names, function names, env vars. These
  are what a future `grep` will be searching for.
- **Where the code lived** — every file, including frontend mirrors and tests.
- **Its status when retired** — active or already disabled, and *whether it ever
  fired in live trading*. A rule that never fired is a very different thing from
  one that fired and lost money.
- **What it did**, in two or three sentences.
- **Why it was retired**, with the evidence. Cite the ADR.
- **A restore path** — the commit that still contains the code, so
  `git show <sha>:<path>` recovers it.
- **What would have to be true to bring it back.** "Never" is rarely honest;
  *"if the screener starts producing +20% leaders"* usually is.

### Rules for the file

1. **Log before deleting**, never after.
2. **Never remove an entry.** The file only grows. A rule retired twice gets two
   entries — the second one is evidence the first removal was wrong.
3. **Distinguish *retired* from *relocated*.** If a rule's logic survives
   somewhere else in another form, say so explicitly and name its new home.
   Silently listing it as deleted sends a future reader looking for behaviour
   that is still live.

### Self-check

Run the same grep the Doc Sync Rule mandates. Every identifier you removed should
now appear in exactly two places: the git history, and `docs/retired_code.md`. If
a deleted constant name returns **zero** hits repo-wide, the entry is missing.

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

### Do NOT run `graphify update .` after APPLYING a patch

The patch already contains the regenerated `graphify-out/` artifacts — that is
why they are in the permitted file list above. Re-running the update on the
machine that applied it re-stats every file and rewrites **only the `mtime`
fields in `manifest.json`**; every `ast_hash` is byte-identical, so the commit
records nothing but checkout timestamps.

That empty commit is actively harmful:

- It becomes the head commit, so the GitHub Actions run is titled
  *"Update knowledge graph after applying patch NNN"* instead of the change
  that was actually deployed.
- It triggers a redundant image build and production redeploy.
- It guarantees the patch can never reverse cleanly, which is the entire reason
  the `APPLIED_SOURCE` escape hatch below has to exist.

Run `graphify update .` when you **author** a change, never when you apply one.
If a graph refresh is ever genuinely needed post-apply, fold it into the work
commit with `git commit --amend` rather than adding a trailing commit.

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

## ⏰ SCHEDULED: Exit-Parameter Review Against Real Trade History

> **At the start of any session on or after a due date below, tell me the review
> is due before doing anything else.** Do not wait to be asked. If several dates
> have passed, run the most recent one — they supersede each other.

Every exit threshold currently shipped was tuned on **17 closed trades**. That is
not enough to trust, and each of the ADRs behind them says so explicitly. These
reviews exist to re-run the same measurement as real trades accumulate, so the
parameters are corrected by evidence rather than left to ossify.

### The command

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a

# START HERE. --cliff is the only mode whose table contains the CURRENTLY
# SHIPPED Prove-It stack, so it is the one that answers "does shipped still win?"
python3 research/exit_rule_replay.py --insecure --cliff

python3 research/exit_rule_replay.py --insecure            # headline comparison
python3 research/exit_rule_replay.py --insecure --grid     # full sweep
python3 research/exit_rule_replay.py --insecure --proveit  # Prove-It parameter sweep
python3 research/exit_rule_replay.py --insecure --day0     # Phase 1: bot-enforced vs broker-resting
```

> ⚠️ **Do not use `--proveit` to ask "is the shipped config still best?"** Every
> row in that sweep uses a **breakeven** Phase 2 floor and a 1.5%/2.0% Phase 1
> band, whereas the live rule floors at **−1%** with a **3.0%** later band — so
> none of its rows is the shipped configuration. It also seeds the table with the
> *pre-2026-09-04* stack and then hides that row behind the default `--top 25`.
> `--cliff` puts the real shipped rule first. This tripped up the 2026-09-17
> review; see `decisions/2026-09-17_exit-review-48-trades.md`.

`research/exit_rule_replay.py` replays the bot's **own** closed trades on
5-minute bars, reproducing the live mechanics (15-minute checks, `arm_exit()`
0.6% trail, 3.25h deadline), and reports every result as a dollar delta against
the exit that actually happened. Drop `--insecure` if the local TLS trust store
is working.

### Schedule

| Due | Trades needed to be meaningful | Status |
|---|---|---|
| **2026-09-20** (+1 month) | ~22 | ☑ **run 2026-09-17**, n=48 — SHIPPED wins at **+$10,673**, no challenger beats it, **0 winners harmed**. Cliff fix rejected again (−$1,393). No change made. |
| **2026-10-20** (+2 months) | ~28 | ☐ not run |
| **2026-11-20** (+3 months) | ~35 | ☐ not run |
| **2026-12-20** (+4 months) | ~42 | ☐ not run |
| **2027-01-20** (+5 months) | ~48 | ☐ not run |
| **2027-02-20** (+6 months) | ~55 | ☐ not run |

Tick the box and record the date, trade count and headline result when a review
is run. If the trade count has barely moved since the last review, say so and
skip rather than re-reading noise — the dates are a prompt, not an obligation to
change something.

### What each review must answer

1. **Has the sample grown enough to matter?** Below ~30 closed trades the
   differences are noise. Report `n` first, every time.
2. **Does the shipped configuration still win?** It is the `SHIPPED` row in the
   output. If something now beats it by more than ~$500 *and* the improvement is
   spread over 3+ trades, it is a real candidate.
3. **Is any result carried by a single trade?** The per-trade deltas are printed
   for exactly this reason. A configuration that wins on one outlier has not won.
4. **Has anything started harming winners?** `winners_hurt` is the column that
   matters most. Loss-cutting rules that clip winners destroy far more value than
   they save — this is what sank the days-0-1 kill-switch window.

### Parameters under review, and what to re-test

| Parameter | Shipped value | Why it is provisional |
|---|---|---|
| `PROVE_IT_P1_DAY0_PCT` | `0.01` | 0.75% scored $70 better on the earlier 17-trade sample — inside noise. Either could be right. |
| `PROVE_IT_P1_LATER_PCT` | `0.03` | Chosen because CPAY's day-1 close of −2.24% (low −2.88%) sits just inside it. That is **one trade** defining a threshold. |
| `PROVE_IT_P1_DAY0_LAST_DAY` | `0` | The day-1 damage rests largely on that same winner. |
| `PROVE_IT_P2_ARM_GAIN_PCT` | `0.02` | Re-swept **2026-09-17 via `--cliff` on 48 trades** (was 39): arming at +1.5% is still break-even (−$29) and still harms one more trade; +1.0% (−$655) and +0.5% (−$1,240) give back winners. 2% stands, now on a larger and corrected sample. |
| `phase2-unarmed` (no floor below the arm gain) | open by design | Flooring it at the Phase 1 band is **rejected, and as of 2026-09-17 the rejection is settled.** Re-run via `--cliff` on the corrected 48-trade sample that now DOES contain NTRA RT1 (−$706.66) — the trade that motivated the hypothesis and whose absence made the earlier 2026-09-10 rejection provisional. The fix scores **−$1,393 vs shipped** (+$9,280 vs +$10,673) and raises `>300` from 11 to 12, so it is worse on the larger sample than it was on the smaller one. The owed re-run is **done**; no caveat remains. See `decisions/2026-09-10_prove-it-unarmed-window-measured-not-closed.md` and `decisions/2026-09-17_exit-review-48-trades.md`. |
| `PROVE_IT_P2_FLOOR_PCT` | `-0.01` | The 1% of slack is worth +$1,189 on CPAY alone. Whether 1% is the *right* slack, or merely enough for CPAY, is unresolved. |
| `PROVE_IT_BACKSTOP_SLACK_PCT` | `0.01` | Not measured. Set wide enough that the resting order provably cannot front-run the bot; no sweep supports the exact value. **Re-test with `--day0`:** a broker-hard Phase 1 wins by +$187 on the current sample, but the entire net is APH alone — recheck once more overnight-gap trades exist. |
| `TRAIL_PROFIT_TIERS` | `+5% → 1.5%` | 2026-08-22 replay on 17 trades outperformed +6% by +$1,385 with no harmed trades; still under review due to sample size. |
| `POWER_HOLD_GAIN_PCT` | `10.0` | **Entirely unvalidated.** No trade in the 30-trade replay reached +10% within 21 days, so the harness is silent on it. Lowered from 20% only because 20% was unreachable. |
| `STALE_EXIT_DAYS` (as a rotation discount) | `10` | The staleness discount is **unmodelled by the harness** — the replay cannot see Rank & Replace at all. |
| `MARKET_DIRECTION_TICKERS` | `SPY,QQQ` | Chosen on a 4,940-session **index** grid. The trade-history replay could not discriminate — all 21 closed trades fall in one six-week window where every config says BULL. |
| `MARKET_DIRECTION_BUFFER_PCT` | `0.01` | Same caveat. 1% and 2% scored within noise of each other on index data. |
| `MARKET_DIRECTION_SLOPE_DAYS` | `20` | Same caveat. Note the gate's mean-return edge is **negative outside 2008** — it is justified as drawdown insurance, not as a return enhancer. |

### Resolved: `EFFECTIVE_POSITION_SLOTS` is gone

Deleted 2026-09-04 along with the Early Dollar Stop that was its only consumer.
FU-007 is closed without the portfolio reset it was waiting on.

### What each review must answer first, from 2026-09-20

Everything in the table above was re-tuned on **2026-09-04**, when five exit
rules were replaced by the Prove-It Stop
(`decisions/2026-09-04_prove-it-stop.md`). The earlier open questions about the
Early Dollar Stop and the Thesis Stop are moot — both rules are retired, having
fired **zero** times in 30 closed trades.

The measurement that replaced them, **re-run 2026-09-17 on all 48 closed trades**
(26 losers, 22 winners) after the 2026-09-15 NBIX/NTRA repairs:

| Configuration | NET vs the exits that actually happened | winners | harmed | >300 |
|---|---|---|---|---|
| **Prove-It (SHIPPED)** | **+$10,673** | +$5,222 | 8 | 11 |
| + P2 arms at +1.5% peak | +$10,644 (−$29) | +$5,222 | 9 | 11 |
| + P2 arms at +1.0% peak | +$10,018 (−$655) | +$4,028 | 10 | 10 |
| Cliff fix (unarmed keeps P1 band) | +$9,280 (−$1,393) | +$3,531 | 9 | 12 |

Deltas, not absolute P&L: a positive net means the configuration would have made
that much more than the bot actually did. For reference the 48 trades contain
**−$15,054 of realised losses** across the 26 losers.

**The shipped stack ranks first and nothing beats it.** The result is not carried
by one trade: 23 trades improve against 8 harmed, and **seven** contribute more
than $1,000 each (CDNA +$1,795, FR +$1,269, NBIX +$1,216, RSI +$1,188, NBIX
+$1,180, FRO +$1,030, HWM +$1,016). Dropping the single largest contributor still
leaves **+$8,878**.

**Zero winners were harmed.** All 8 harmed trades (TTWO, CHRD, INCY, SGHC, APH,
DXCM, GE, LPG) were already losers; the rule makes a few losses somewhat larger
while rescuing far more. This is the column that matters most and it is clean.

> **Erratum — the pre-2026-09-17 version of this table is superseded.** It read
> −$6,548 actual / −$4,069 old rules / +$5,410 Prove-It on **30** trades, and was
> already marked stale because of two `trade_history` repairs: **NBIX** id 29 was
> re-priced from a wrong-day FMP estimate of $152.74 to the real fill of
> $158.5043 (−$2,260.55 → −$1,424.72), and the **NTRA** backfill split one
> contaminated composite row into RT1 −$706.66, RT2 −$162.84 and RT3 **+$218.43**.
> Do not cite the old figures. See
> `decisions/2026-09-15_nbix-reconstructed-sell-price.md`.

Reproduce the shipped baseline with
`python3 research/exit_rule_replay.py --insecure --cliff` — **not** `--proveit`.
`--proveit` is a parameter *sweep*: its rows all use a breakeven Phase 2 floor
(`floor+0.0`) and none of them is the live configuration, whose floor is −1%. Its
top-25 cut also hides the baseline row entirely. `--cliff` places the true shipped
config first, which is why it is the right command for "does shipped still win?".

**The three questions this leaves open:**

1. **Is `PROVE_IT_P1_LATER_PCT = 3%` right, or is it CPAY-shaped?** The 3% band
   was chosen because CPAY's day-1 low of −2.88% sits just inside it. Sweep it
   again with more trades and check whether the winner-damage cliff is still at
   the same place, or whether it moved because one trade left the sample.

2. **Does the Phase 2 floor need 1% of slack, or less?** Same problem: the slack
   exists because CPAY retested entry on day 4. Test 0.5% and 1.5% and report
   whether the difference is carried by more than one trade.

3. **Is `POWER_HOLD_GAIN_PCT = 10%` reachable?** It was lowered from 20%
   because no realised trade ever hit 20% within 21 days. Check whether anything
   has now hit 10% — if not, the rule is still dormant and the number is still a
   guess.

**A second erratum (2026-09-15).** The replay's *input* changed, not its code:
NBIX was re-priced (+$835.82) and NTRA was split into its three real round trips.
Any figure produced by the harness before 2026-09-15 is stale regardless of which
flag produced it. The `--cliff` result in particular was measured on a sample that
did not contain NTRA RT1 (−$706.66) — the very trade that motivated the
hypothesis it rejected.

**An erratum to carry forward.** Two bugs in `research/exit_rule_replay.py` were
found and fixed on 2026-09-04, and **any figure quoted from a replay run before
that date is unreliable**: (a) same-day round trips produced a zero-width
price-fetch window and were silently dropped — this removed OII, FROG and APH,
**all losers, −$2,583**, flattering every result; (b) APH's 2-for-1 split was not
corrected for, producing a fictitious −$11,650. The fixed harness reproduces the
realised total exactly (−$6,547.59, matching the dashboard to the cent). Before
citing any older number, re-run it.

> **Note on mechanism — this schedule is now ACTIVE, not passive.** Until
> 2026-09-17 the table above was a *passive* reminder: it only fired when a
> session happened to read this file, so a review could be missed in silence.
> That gap is closed. The exit review is now registered in
> `decisions/provisional_decisions.json` as **`exit-parameters-proveit`**, so the
> monthly `decision_review.yml` cron opens a **persistent GitHub issue** and pings
> Telegram the moment it comes due — the same mechanism that guards every other
> provisional decision.
>
> Its `revisit.min_closed_trades` is deliberately **null**: this review is
> *date*-driven, and gating it on a trade count would let a slow month stall it
> indefinitely, recreating the very failure mode being removed. It fires on the
> date even if not a single new trade has closed; the review itself reports `n`
> first and may legitimately conclude "sample barely moved, skip".
>
> **When a review is completed, update BOTH:** tick the box in the table above,
> *and* append to the register entry's `history` / bump its `revisit.not_before`
> to the next checkpoint. Ticking only the table leaves the cron firing on a date
> that has already been handled.
>
> New forward-looking parameter decisions go in the register, not in this table;
> this table remains the record of the original 17-trade tuning and the log of
> each checkpoint.

---

## 🧭 MANDATORY: Provisional Decision Register (revisit-when-more-trades)

> **Whenever we take a decision on a small trade sample, or defer a piece of
> engineering work until the bot is in a safer state, add an entry to
> `decisions/provisional_decisions.json` in the same commit — never track it in
> prose.** This is the single source of truth for "things to come back to", and
> it is wired to an active alarm.

### Why this exists

The exit-review schedule above only fires when a human opens this file. That is
fine as a backstop but it is not bulletproof — a quiet month and a parked
decision is forgotten, and an untested parameter ossifies into a "fact". The
register removes that failure mode.

### The three parts (all committed, all in the repo)

1. **`decisions/provisional_decisions.json`** — the register. Each entry records
   the decision, its rationale and *baseline numbers at decision time*, the ADR
   link, the exact `review_command` that reproduces the measurement, the
   `review_questions` the review must answer, a `kind`, a `revisit` **trigger**
   (`min_closed_trades` and/or `not_before`) and optional `preconditions`.
   `history` is an append-only log of past reviews. The file's own
   `_README`/`_schema` document every field.
2. **`research/decision_review.py`** — reads the register, queries the live
   closed-trade count **and live portfolio state** from Supabase, and reports
   which active entries are DUE and which of those are actually **actionable**.
   Exit `10` = something due, `0` = nothing due, `1` = error (fails LOUD; a
   malformed register or unreachable Supabase must never be silently skipped).
   Run locally: `python3 research/decision_review.py --insecure`.
3. **`.github/workflows/decision_review.yml`** — a monthly cron (20th, 12:00 UTC)
   that runs the script and, the instant an entry is due, opens a **persistent
   GitHub issue** (idempotent by an `id` marker in the title, so it never
   double-opens) and pings Telegram. The issue states whether the entry is ready
   to action or blocked, and by what. It stays open until a human reviews the
   entry and closes it — a scrolled-past Telegram message can be missed, an open
   issue cannot.

### Trigger vs. precondition — they are not the same question

`revisit` asks **"is it time?"** — a date floor and/or a closed-trade count.
`preconditions` asks **"and is it safe to act today?"** — evaluated against live
portfolio state at review time:

| Field | Meaning |
|---|---|
| `max_open_positions` | Actionable only when open positions ≤ this. `0` requires a flat book. |
| `min_position_age_days` | Actionable only when the **youngest** open position is at least this old. A flat book always satisfies it. |

Youngest is the binding figure, not average or oldest: a single day-0 position
sits in the tight Prove-It Phase 1 band, so the book is not quiet no matter how
long the others have been held.

An entry that is due but blocked is **still reported and still opens an issue** —
it is never silently skipped. The issue says what is blocking it and stays open
until it clears. Omit the `preconditions` block entirely when there is no
constraint; absent means always actionable.

### The two kinds of entry

| `kind` | Use for | Typically gated by |
|---|---|---|
| `parameter` (default) | A tuned number chosen on too small a sample | `min_closed_trades` |
| `work-item` | Engineering work deliberately deferred until it is safe | `not_before` + `preconditions` |

`kind` defaults to `parameter` when absent, so pre-existing entries are unchanged.

### When you must add an entry

Any time a shipped number was chosen on a sample too small to trust and the honest
statement is "this is provisional until we have more trades" — new exit trigger,
fraction, threshold, gate, or default. If you would write "revisit when we have N
trades" anywhere, that sentence belongs in the register instead.

**Also add one whenever work is deferred for safety** rather than abandoned. If
you would write "let's do this once the book is flat" or "revisit after the next
deploy settles", that is a `work-item` entry with `preconditions` — not a note in
a commit message, and not a line in a chat log that scrolls away.

### When a review comes due (the loop that closes)

1. Run the entry's `review_command` and answer its `review_questions`.
2. Append a `{date, closed_trades, verdict, note}` row to that entry's `history`
   and set `last_reviewed`.
3. If the decision **still holds**, bump `revisit.min_closed_trades` to the next
   milestone. If it **does not**, set `status` to `superseded`, refresh the ADR
   and the docs (Doc Sync Rule), and log any deletion (`docs/retired_code.md`).
   For a `work-item` that is now **done**, set `status` to `resolved`.
4. If it was **blocked**, do not close the issue and do not weaken the
   precondition to make it pass. Leave it open; it is doing its job.
5. Close the GitHub issue.

> The register is the machine-readable companion to the ADRs: an ADR says *why*
> a provisional decision was made; the register guarantees we come back and test
> whether it was right.

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
