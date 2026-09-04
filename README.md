# CAN SLIM Breakout Trading System

An automated equity momentum system implementing William J. O'Neil's CAN SLIM methodology.
It screens for fundamentally-qualified growth names, detects technical breakout setups,
scores them, and executes live orders through Interactive Brokers — running unattended as a
containerised daemon.

The system is opinionated: it holds a **concentrated book of 5 positions**, cuts losers on
evidence rather than hope, and spends most days doing nothing. That last property is a
feature. O'Neil's edge comes from participating in a small number of genuine breakouts, not
from constant activity.

> **This trades real money.** Every parameter below is live. Read the
> [Risk Model](#risk-model) before deploying.

---

## Table of Contents

- [Strategy Primer](#strategy-primer) — the concepts, for readers new to CAN SLIM
- [Signal Pipeline](#signal-pipeline) — how a stock becomes a position
- [Risk Model](#risk-model) — the exit hierarchy
- [Architecture](#architecture)
- [Research Infrastructure](#research-infrastructure)
- [Deployment](#deployment)
- [Backups](#backups)
- [Glossary](#glossary)

---

## Strategy Primer

CAN SLIM is a growth methodology: buy institutional-quality companies at the precise moment
their price structure signals accumulation, and exit fast when that signal fails.

| | Criterion | Implementation |
|---|---|---|
| **C** | Current quarterly earnings | Diluted EPS QoQ growth > 20% |
| **A** | Annual earnings | Diluted EPS YoY TTM growth > 25%, revenue growth > 15% |
| **N** | New highs / new products | Price within 5% of the 252-day rolling high |
| **S** | Supply and demand | Volume ≥ 1.50× the 50-day average on the breakout bar |
| **L** | Leader, not laggard | 12-week relative strength vs SPY, ≥ 50th percentile |
| **I** | Institutional sponsorship | $300M market-cap floor, 250K average daily volume |
| **M** | Market direction | Buys suspended unless **both** SPY and QQQ close >1% above their 200-day SMA with at least one 200-DMA non-falling; fails closed on any data error |

The revenue-growth filter deserves note: EPS growth alone can be manufactured through
cost-cutting and buybacks. Requiring **top-line growth alongside it** distinguishes a
genuine growth business from financial engineering.

### The setup being traded

A **base** is a consolidation — a period where a stock digests prior gains and trades
sideways while institutions accumulate. The **pivot** is the high of that base; the price
level where supply has repeatedly overwhelmed demand. A **breakout** is the pivot being
taken out decisively on expanded volume.

The volume requirement is what separates a breakout from noise. Price alone can drift
through a pivot on thin trade and fall straight back. Price *plus* a volume surge implies
institutional participation — the buyers who can sustain a move.

Two properties of real breakouts drive the entire risk model:

1. **They work almost immediately.** A valid breakout does not spend a week underwater.
   Failure to advance is itself information.
2. **Entry proximity is critical.** O'Neil's buy zone extends roughly 5% above the pivot.
   Beyond that, the risk/reward inverts: the stop is now far below and the easy move is
   already gone.

Both are enforced mechanically — see [The Prove-It Stop](#tier-1--the-prove-it-stop-always-live)
and the [pivot extension gate](docs/buy_logic.md).

---

## Signal Pipeline

A name must survive four independent stages to become a position. Each runs as a separate
scheduled job, so a failure in one stage cannot silently corrupt another.

```
  TradingView Scanner            FMP EOD prices              OpenAI              IBKR
          |                            |                        |                  |
          v                            v                        v                  v
  +---------------+          +------------------+      +---------------+   +--------------+
  | 1. FUNDAMENTAL|          | 2. TECHNICAL     |      | 3. SCORING    |   | 4. EXECUTION |
  |    SCREEN     |--------->|    TRIGGERS      |----->|               |-->|              |
  |               | watchlist|                  |daily_|  composite +  |   | buy gates,   |
  | CAN SLIM      |          | breakout / coil  |trig- |  AI rating    |   | market order,|
  | fundamentals  |          | detection, RS    |gers  |  + veto       |   | trailing stop|
  +---------------+          +------------------+      +---------------+   +--------------+
   Mon-Fri 21:00 UTC          same job, after           same job, after      09:30 ET next
                              the fundamental           the technical        session
```

### 1. Fundamental screen → `watchlist`

A single TradingView Scanner query applies the CAN SLIM fundamental filters plus liquidity
floors, excluding Finance, Real Estate and Utilities — sectors driven by interest rates
rather than earnings acceleration, where the methodology's growth signals do not translate.

Full detail: [docs/fundamental_screener.md](docs/fundamental_screener.md)

### 2. Technical screen → `daily_triggers`

Each watchlist name is evaluated against three setup types, in priority order:

| Trigger type | Setup |
|---|---|
| `BREAKOUT` | Pivot cleared on ≥ 1.50× average volume — the primary signal |
| `PRE_BREAKOUT` | Coiling within 8% of the high on *contracting* volume (< 1.00× average) |
| `PRE_BREAKOUT_RELAXED` | Same structure, widened tolerances; emitted only to fill the daily quota |

Volume **contraction** in a pre-breakout coil is the point, not a compromise: a base that
tightens on declining volume indicates sellers are exhausted, which is precisely the
condition that precedes a clean breakout.

Full detail: [docs/technical_triggers.md](docs/technical_triggers.md)

### 3. Scoring → `final_score`

Every trigger receives a 0–100 composite:

| Component | Weight | Measures |
|---|---|---|
| Technical | 30% | Breakout mechanics — volume surge, pivot proximity, SMA structure |
| Liquidity | 25% | Price, average volume, market cap |
| AI rating | 25% | GPT-assessed fundamental quality in full context |
| Sentiment | 10% | Recent news tone |
| RS vs SPY | 10% | 12-week relative strength |

The AI layer assigns a conviction grade; **grade D (conviction < 30) is a hard veto**
regardless of composite score. A missing AI score is *also* a rejection — the system fails
closed rather than falling back to technicals alone, because an un-vetted trigger is an
unknown, not a neutral.

### 4. Execution

At 09:30 ET the agent walks the scored triggers highest-first through a sequential gate
stack — capacity, freshness, duplicate, cooling-off, cash floor, score floor, AI veto,
buy-zone bounds, share count. A trigger must clear **every** gate.

On fill, a GTC trailing stop is registered with IBKR immediately, and the entry ATR is
persisted — that value later parameterises the base trailing stop's ATR band.

Full detail: [docs/buy_logic.md](docs/buy_logic.md)

---

## Risk Model

Position sizing is `available_cash / remaining_slots`, recomputed before each buy. With 5
slots, a full book is roughly 20% per name. **Concentration is deliberate** — but it means
a single-name loss is 4–5× more damaging to the portfolio than in a 20-stock book. That
asymmetry is why exits are aggressive and layered.

Exits form a hierarchy from mechanical to discretionary. Every rule below is evaluated on
**trading days held**, counted over the half-open interval `[buy_date, today)` — so a
position bought Monday is day 0 that day, day 1 on Tuesday.

### Tier 0 — Dynamic trailing stop (always on)

A native IBKR `TRAIL` order placed at fill. Initial distance is
`max(10%, min(12%, 2.5 × entry ATR%))` — volatility-scaled, so a name that routinely swings
4% a day is not stopped out by ordinary noise.

It **ratchets one way only**, tightening as unrealised gain builds. There is a single rung:

| Unrealised gain | Trail tightens to |
|---|---|
| ≥ +5% | 1.5% from the high-water mark |

This is the **HWM profit lock**. It replaced a 20/30/50% → 6.5/6.0/5.0% ladder that was far
too loose to matter: across nine closed winners, $8,071 of open profit was given back from
peak before the exit actually fired. A later replay on 5-minute bars over 17 closed trades
improved net further by arming at +5% instead of +6% (+$4,720.53 vs +$3,335.40, same 1.5%
trail), so +5% is now shipped.
See [decisions/2026-08-22_hwm-profit-lock-arm-5pct.md](decisions/2026-08-22_hwm-profit-lock-arm-5pct.md).

Because it lives at the broker, this is the one protection that survives the bot being
offline. Treat it as the disaster backstop, not the primary exit.

### Tier 1 — The Prove-It Stop (always live)

The central rule, and the one most worth understanding. It replaced five separate exits and
asks a single question:

> **Has this position ever *closed* above the price we paid?**

`closed_above_entry` is latched `True` at EOD the first time a close prints above entry, and
is never cleared — a breakout confirms only once. That latch selects the phase.

**Phase 1 — unproven. Anchored to entry.**

| Day held | Arms an exit at |
|---|---|
| 0 | **1%** below entry |
| 1 and after | **3%** below entry |

The band **widens** after day 0 rather than tightening, which is counter-intuitive and was
measured. A breakout that fails on day one is wrong immediately and cheaply; from day 1 the
failing and working populations overlap, and holding the tight band costs roughly
$1,500–2,000 in clipped winners across the 30-trade sample. CPAY closed −2.24% on day 1 and
then ran to +8.95%.

**Phase 1 never expires**, and that is the entire point. Every rule it replaced was scoped
to a window, which is precisely how NBIX (−$2,261), DELL (−$1,283), RSI (−$1,390) and HWM
(−$1,463) were allowed to run: the kill-switch stopped looking after day 0, and the
peak-anchored trailing stop sits far below entry for a stock that never rose.

**Phase 2 — proven. Anchored to the peak.**

It closed above entry, so it earned patience. Once the peak gain reaches **+2%**, a
give-back floor arms at **1% below entry**: a trade that went green is never allowed to
become a real loss. Above +5% the profit ladder (1.5% from the high-water mark) is tighter
and takes over.

The floor sits 1% *below* entry, not at it. An exact-breakeven floor flushes any position
that pokes green and immediately retests entry — CPAY did exactly that on day 4 and an
at-entry floor would have forfeited +$1,189.

**Evidence.** A 5-minute replay of all 30 closed trades, reproducing the live mechanics:

| | Net |
|---|---|
| What actually happened | **−$6,548** |
| The rules shipped before this change | −$4,069 |
| **Prove-It** | **+$5,410** |

Zero winners cut short. Worst single loss falls from −$2,002 to −$1,140 — and that −$1,140
is an overnight gap no stop of any kind prevents. There is **no guaranteed loss cap**, and
INCY is worse under this rule (−$137 → −$429): it is a net improvement across the
distribution, not on every trade. n = 30 is small and every parameter is provisional.

See [decisions/2026-09-04_prove-it-stop.md](decisions/2026-09-04_prove-it-stop.md).

### Tier 2 — Staleness (day 7+, EOD)

No new high-water mark in 10 trading days → the position counts as **stale**. This is a
**capital velocity** concern, not a risk one: a position going nowhere still occupies one of
the book's slots.

Staleness does **not** sell to cash. It discounts the Rank & Replace margin below, so the
slot is released when somewhere better to put the money exists — not merely because this
position stopped moving. With the give-back floor in place, holding dead money is nearly
free; selling it on a timer is not.

### Tier 3 — Rank & Replace (day 7+, EOD)

When the book is full and a fresh trigger materially outranks the weakest holding's
momentum-health score, the system rotates. The margin required depends on the day-3
breakout verdict:

- Verdict `PASS` → new trigger must beat it by **15 points**
- Verdict `FAIL` → **5 points** suffices
- **Stale** (no new high in 10 trading days) → **5 points** suffices

A breakout that already failed to confirm has forfeited the benefit of the doubt, and so has
one that has stopped making progress.

### Override — Power Hold

O'Neil's 8-week rule: a stock that gains **+10% within 21 calendar days** is exhibiting
institutional-grade demand and is granted 56 calendar days of protection. During that
window the profit ladder is bypassed and the trail *widens* to 30%, while the Prove-It Stop
and the rotation exits are suppressed entirely.

The trigger was lowered from +20% to +10% because at 20% the rule was unreachable — the
realised trade distribution contains no +20% runners. The +10% figure is **unvalidated**: no
trade in the 30-trade replay reached it within 21 days.

This is counter-intuitive and it is the point. The strategy's returns are outlier-dependent
— historically the top-10 trades have accounted for the majority of total P/L. A profit lock
that clamps the trail to 1.5% at +5% would otherwise guarantee you clip your biggest winners
at +5%. Power Hold exists to let a genuine leader run. The base trailing stop remains active
throughout as the disaster backstop.

### Armed Exit — how the system sells

The Prove-It Stop does not market-sell in either phase. It **arms** a 0.6% trailing stop
with a 3.25-hour deadline.

The reasoning: the moment a loss threshold is breached is frequently a local trough — a
market order there sells the low tick. Arming a tight trail lets an intraday bounce be
captured while guaranteeing the position is gone within half a session. If the trail has
not filled by the deadline, a market order is forced.

This is what "cut it smartly" means operationally: exit on the system's terms, not the
worst print of the day. Across the 30-trade replay the armed exit is worth roughly **+$600**
over selling at market on the trigger tick.

A resting IBKR GTC order backs it up so an overnight gap is capped even when the agent is
offline. In Phase 1 that order sits 1% *wider* than the trigger so it can never front-run
the bot; in Phase 2 the resting order **is** the floor.

### Exit hierarchy at a glance

| Tier | Rule | Days | Evaluated | Fires as | Suppressed by |
|---|---|---|---|---|---|
| 0 | Dynamic trailing stop | all | continuous (IBKR) | broker trail | — (widened by power hold) |
| 1 | **The Prove-It Stop** | **all** | every 15 min | armed exit | power hold, exit armed |
| 2 | Staleness | 7+ | EOD, once daily | *(discounts tier 3)* | power hold |
| 3 | Rank & Replace | 7+ | EOD, once daily | market | power hold |

Checks are ordered, and the first to fire pre-empts the rest for that cycle.

Full detail: [docs/sell_logic.md](docs/sell_logic.md)

---

## Architecture

Screening runs in the cloud; execution runs on hardware you control.

```
GitHub Actions (screening)          Supabase (Postgres)         Local host (Docker)
+------------------------+          +-----------------+         +--------------------+
| tv_api_screener.py     |--------->| watchlist       |<--------| execution-agent    |
| technical_screener.py  |          | daily_triggers  |         |  buy loop 09:30 ET |
| ai_evaluator.py        |          | portfolio_...   |         |  monitor / 15 min  |
| backfill_outcomes.py   |          | trade_history   |         +--------+-----------+
+------------------------+          | *_history       |                  | :4000
                                    +-----------------+         +--------v-----------+
                                                                | ib-gateway (IBKR)  |
                                                                +--------------------+
                                                                | trading-bot :8000  |
                                                                |  FastAPI + React   |
                                                                +--------------------+
```

**The execution agent and the dashboard are deliberately separate containers.** Risk
monitoring must not share a failure domain with a web UI — a dashboard crash cannot be
allowed to stop trailing-stop maintenance. Only the agent holds brokerage write access.

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Broker | Interactive Brokers via `ib_insync`, headless IB Gateway |
| Fundamentals | TradingView Scanner API |
| Prices | Financial Modeling Prep (FMP) for screening and agent exit rules; **open-position values come from IBKR** (`decisions/2026-09-03_ibkr-sourced-position-values.md`) |
| Database | Supabase (Postgres) — cloud state; SQLite for local settings only |
| AI | OpenAI (trigger evaluation) |
| Alerts | Telegram |

### Scheduled jobs

| Job | Cron (UTC) | Purpose |
|---|---|---|
| Daily screener | `0 21 * * 1-5` | Fundamental → technical → AI scoring chain |
| IBKR cash-flow sync | `0 6 * * 2-6` | Reconcile deposits/withdrawals via Flex Query |
| Trigger outcome backfill | `0 12 * * 0` | Attach forward returns to archived triggers |
| Supabase backup | `0 14 * * 0` | Full snapshot of every table to Parquet on the prod server |

---

## Research Infrastructure

Most trading systems cannot answer *"is my screen actually any good?"* — they overwrite
their own history. This one is instrumented to answer it.

| Table | Retention | Records |
|---|---|---|
| `watchlist_history` | append-only | Every fundamental snapshot, with sector and market cap |
| `trigger_history` | append-only | Every breakout trigger, fully scored |
| `trigger_decisions` | append-only | Every buy **and skip**, with a reason code |
| `trade_history` | append-only | Closed trades |

`trigger_decisions` is the important one. `trade_history` alone is selection on the
dependent variable — it contains only candidates that passed every gate, so it cannot tell
you whether your gates are rejecting winners. Logging the skips, with reasons, creates the
control group.

Reason codes are split into **quality** rejections (`AI_VETO`, `SCORE_FLOOR`,
`EXTENDED_ABOVE_PIVOT`) and **capacity** rejections (`SLOTS_FULL`, `INSUFFICIENT_CASH`). A
name skipped for lack of a slot says nothing about the quality model but everything about
the cost of running 5 positions.

A weekly job attaches forward returns (1/5/20-day, path metrics, SPY alpha) to each
archived trigger. Entry is measured at the **next session's open** — what the bot would
actually have paid — not the trigger close, which would credit an overnight gap that was
never captured.

### Exit-parameter replay

`research/exit_rule_replay.py` replays the bot's **own closed trades** on 5-minute bars
against alternative exit parameters, reporting each candidate as a dollar delta versus the
exit that actually happened. Every entry is real and every baseline is a real fill, so it
carries no simulation-universe bias.

```bash
set -a && . ~/.config/ai-trading-bot/secrets.env && set +a
python3 research/exit_rule_replay.py           # headline comparison
python3 research/exit_rule_replay.py --grid    # full parameter sweep
```

Two rules govern its use, both learned the hard way:

- **Compare whole stacks, never single rules.** A rule measured in isolation is credited
  with saves that a faster rule running alongside it would have reached first. Measuring
  the day-0 kill-switch alone scored it +$3,027; the flat $500 dollar stop it ran next to
  dragged the real stack to −$272.
- **Check `n` before believing anything.** The current sample is 17 trades from one market
  regime. Differences under ~$500, or carried by fewer than three trades, are noise — the
  report prints per-trade deltas so this is visible rather than averaged away.

This harness backs the scheduled exit-parameter reviews defined in `AGENTS.md`.

---

## Deployment

### Prerequisites

IBKR account with API access · Supabase project · FMP and OpenAI keys · Docker · TradingView
needs no key.

### Setup

```bash
git clone <repo> && cd ai-trading-bot
cp .env.template .env        # populate credentials and strategy parameters
```

Apply the SQL in `migrations/` to your Supabase project, then:

```bash
docker compose up -d
```

Dashboard at `http://localhost:8000`. Verify the gateway is connected and reporting the
expected account before the next market open.

Each open position expands into a **Position Journey** panel that states, without needing
this document: which lifecycle phase the position is in (`Unproven` → `Proven` →
`Floor locked` → `Rotation`, with Power Hold and Exiting shown as overrides). That track is
indexed by **proof, not by calendar day** — a position advances only by closing above entry
and then by reaching the arming gain, and it can sit in `Unproven` indefinitely. The panel
also states what has already happened to it (entry, follow-through latch, day-3 verdict,
high-water mark and give-back, profit lock, armed exits), and what happens next — the
nearest price trigger and its level, the next phase change, and any pending scheduled
event. Below it, the **Risk Rule Ladder** shows every rule's live state and trigger price.

Each **closed** trade expands too, on both the Dashboard and Trade History screens, into an
**exit detail panel**: who actually executed the exit (the agent, a resting IBKR order, or a
human), the trade economics, and the numbers the firing rule recorded — for a trailing stop
that is the trail in force, the high-water mark it was anchored to and the implied trigger
price. Anything the record does not contain is listed by name as *not recorded* rather than
left blank. Exit labels are read from the stored reason and are never inferred from the
return: the bot has no fixed profit target and no flat 7% stop, so it never claims one.
See `docs/sell_logic.md` and `decisions/2026-08-23_exit-detail-panel.md`.

```bash
docker compose logs -f execution-agent
```

### Operational notes

- **`READ_ONLY_API=no`** must be set on the gateway or orders will be silently rejected.
- If both live (`U…`) and paper (`DU…`) accounts are visible, set `IBKR_ACCOUNT` explicitly
  — the agent refuses to guess.
- All market-hours logic uses `America/New_York`. Never rely on the host clock.
- Changing slot count is a `.env` edit plus a restart — but on a fully-invested book it does
  not take effect immediately: existing positions keep their original sizing and the book
  only converges after full turnover. `MAX_POSITIONS` is read from a single source
  (`config.py`) by every module that *buys*. No exit rule derives a threshold from the slot
  count any more — `EFFECTIVE_POSITION_SLOTS` was deleted with the Early Dollar Stop that
  was its only consumer.

### Manual tools

| Script | Purpose |
|---|---|
| `force_buy.py` | Place a gated buy outside the market-open loop |
| `force_sell.py` | Liquidate a named position immediately |
| `rotate_positions.py` | Interactive review of holdings against fresh triggers |
| `managed_exit.py` | Run an armed exit manually |
| `supabase_backup.py` | Export every Supabase table to Parquet (see [Backups](#backups)) |

---

## Backups

Supabase holds all trading state, and `trade_history` is the only record of what the
strategy did with real money — every scheduled parameter review replays it. It is backed
up weekly.

A GitHub Action (`weekly_supabase_backup.yml`, Sundays 14:00 UTC) exports all 12 tables and
rsyncs them to `/home/pom/docker/ai-trading-bot/backups/` on the production server:

```
backups/
  parquet/table_name=<table>/snapshot_date=<YYYY-MM-DD>/data.parquet
  manifest/<YYYY-MM-DD>.json                                          <- counts + checksums
```

Each run writes a **complete snapshot into a new dated partition** and never overwrites an
earlier one, so the archive grows incrementally while every week stays independently
restorable. There is no row-level delta: the whole database is ~700 rows (~180KB/week), and
`portfolio_positions` is mutated in place every 15 minutes, so an append-only delta would
miss most of what changes.

Parquet is the only format written — typed, compressed and verified by read-back. A CSV can
be produced whenever one is wanted:

```bash
duckdb -c "COPY (SELECT * FROM 'parquet/table_name=trade_history/snapshot_date=2026-08-21/data.parquet') TO 'trade_history.csv' (HEADER)"
```

Query the archive with [DuckDB](https://duckdb.org) (one binary, no server, free, all
platforms):

```sql
SELECT * FROM read_parquet('parquet/table_name=trade_history/**/*.parquet',
                           hive_partitioning = true, union_by_name = true);
```

`union_by_name = true` is required — the schema has changed 30+ times, so older snapshots
have fewer columns than newer ones. The partition key is `table_name`, not `table`, because
`table` is a DuckDB reserved word.

Full operator guide: **[docs/backups.md](docs/backups.md)**. Rationale:
[decisions/2026-08-21_weekly-supabase-backup.md](decisions/2026-08-21_weekly-supabase-backup.md).

---

## Glossary

| Term | Definition |
|---|---|
| **Armed exit** | A 0.6% trailing stop with a 3.25-hour deadline, used instead of a market sell so an intraday bounce can be captured rather than selling the low tick |
| **ATR** | Average True Range — mean daily price range; the unit the base trailing stop is scaled in |
| **Base** | Sideways consolidation where institutions accumulate before a move |
| **Breakout verdict** | Day-3 PASS/FAIL assessment (close ≥ entry +1% on ≥ 75% of average volume). Governs how easily a position can later be rotated out |
| **Buy zone** | Pivot to pivot +5%. Above it, `EXTENDED_ABOVE_PIVOT`; more than 2% below, `BELOW_PIVOT` |
| **Cooling-off** | 7-day block on re-buying a name after it was sold |
| **Hive partitioning** | Encoding column values in directory names (`table_name=…/snapshot_date=…`) so a query engine reads them as real columns without them being stored in the files |
| **HWM** | High-water mark — the position's peak price. Anchors the trailing stop, the profit lock and the staleness clock |
| **Give-back floor** | Prove-It Phase 2: once a proven position's peak reaches +2%, a floor arms 1% below entry so a green trade never becomes a real loss |
| **Pivot** | The high of the base; the breakout reference price |
| **Power Hold** | +10% within 21 days grants 56 days of protection, widening the trail to 30% so a leader can run |
| **RS** | Relative strength vs SPY over 12 weeks, percentile-ranked |
| **Slot** | One position's share of capital. `MAX_POSITIONS` is the configured target; a fully-invested book only converges on it after full turnover |
| **Prove-It Stop** | The single loss rule. Phase 1 (never closed above entry) anchors to entry and never expires; Phase 2 (proven) anchors to the peak via the give-back floor |
| **Trailing stop** | Broker-side stop that ratchets up with price and never loosens |
| **Volume surge** | Breakout-bar volume ≥ 1.50× the 50-day average — the evidence of institutional participation |

---

## Further Reading

| Document | Contents |
|---|---|
| [Fundamental Screener](docs/fundamental_screener.md) | Filters, thresholds, watchlist archival |
| [Technical Triggers](docs/technical_triggers.md) | Setup detection, quota waterfall, trigger archive |
| [Buy Logic](docs/buy_logic.md) | The complete gate stack and sizing |
| [Sell Logic](docs/sell_logic.md) | Every exit rule in evaluation order |
| [Configuration](docs/configuration.md) | Every environment variable and schema |
| [IBKR TOTP Setup](docs/ibkr_totp_setup.md) | Headless gateway authentication |
| [Backups](docs/backups.md) | Weekly Supabase export, archive layout, and querying it with SQL |
| [Tech Debt & Requirements Tracker](docs/tech_debt_and_requirements_tracker.md) | Open follow-ups, requirement gaps, scheduled parameter reviews |
| `decisions/` | ADRs — why each rule exists, including the ones that were rejected |

---

## Disclaimer

This software executes live orders with real capital. Trading involves substantial risk of
loss. Backtested performance is not indicative of future results, and every backtest in
this repository carries documented limitations — see `benchmark_data/README.md` for the
survivorship and look-ahead biases affecting the screener-passing universe. Nothing here is
financial advice. You are responsible for every order this system places on your behalf.
