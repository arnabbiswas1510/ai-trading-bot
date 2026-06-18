# Momentum Screener

## Overview

**File:** `momentum_screener.py`

A **secondary daily screener** that runs after `technical_screener.py` as part of the same GitHub Actions cron job. It applies **relaxed fundamental and technical thresholds** to surface high-momentum stocks that pass the primary CANSLIM quality bar but miss one or more of the stricter primary screener thresholds.

Results are written to the `momentum_triggers` Supabase table and consumed by the `execution_agent.py` momentum cascade — filling any portfolio slots not filled by `daily_triggers`.

---

## When It Runs

```yaml
# .github/workflows/daily_technical.yml
steps:
  - run: python technical_screener.py
  - run: python momentum_screener.py   # runs after primary screener
```

Executes **daily after market close** (Monday–Friday). Only populates `momentum_triggers` when the primary `daily_triggers` count falls below `MAX_POSITIONS`, ensuring momentum picks only fill genuine gaps.

---

## Two-Pass Logic

The screener uses two passes with progressively relaxed thresholds to maximize the chance of finding quality momentum candidates when the primary screener yields insufficient signals:

### Pass 1 — Standard Technicals, Relaxed Fundamentals

| Dimension | Primary Threshold | Pass 1 Threshold | Config Variable |
|-----------|------------------|-----------------|----------------|
| Q EPS Growth | ≥ 18% | ≥ 10% | `MOMENTUM_MIN_Q_EPS_GROWTH` |
| Inst. Holders | ≥ 5 | ≥ 3 | `MOMENTUM_MIN_INST_HOLDERS` |
| Volume Surge | ≥ 1.40x | ≥ 1.40x (unchanged) | `VOLUME_SURGE_MIN` |
| Pivot Proximity | within 2% of 52w high | within 2% (unchanged) | `PIVOT_PROXIMITY` |
| SMA-50 | price above | price above (unchanged) | `SMA_WINDOW` |

Pass 1 relaxes only the fundamental criteria. Technical quality remains identical to the primary screener.

### Pass 2 — Relaxed Technicals (runs only if Pass 1 yields insufficient results)

Activates when `len(pass1_results) < MAX_POSITIONS - len(daily_triggers)`:

| Dimension | Pass 2 Threshold | Config Variable |
|-----------|-----------------|----------------|
| Q EPS Growth | ≥ 10% (same as Pass 1) | `MOMENTUM_MIN_Q_EPS_GROWTH` |
| Inst. Holders | ≥ 3 (same as Pass 1) | `MOMENTUM_MIN_INST_HOLDERS` |
| Volume Surge | ≥ **1.20x** (relaxed) | `MOMENTUM_VOLUME_SURGE_MIN` |
| Pivot Proximity | within **5%** of 52w high (relaxed) | `MOMENTUM_PIVOT_PROXIMITY` |
| SMA-50 | price above (unchanged) | `SMA_WINDOW` |

Pass 2 results are merged with Pass 1 (deduped). Combined results are ranked by `volume_surge` descending and written to `momentum_triggers`.

---

## Output — `momentum_triggers` Table

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | text | Stock symbol |
| `triggered_at` | date | Date of the triggering session |
| `close_price` | float | Closing price at trigger date (pivot reference for buy zone gate) |
| `volume_surge` | float | Volume as multiple of 50-day average |
| `pivot_distance_pct` | float | Distance from 52-week high (lower = closer to breakout pivot) |

Rows older than `MOMENTUM_TRIGGER_PRUNE_DAYS` (default: 56) are deleted each run.

---

## How Momentum Triggers Are Consumed

At market open the `execution_agent.py` momentum cascade:

1. Checks if any stock slots remain after `daily_triggers` buys
2. If yes — queries `momentum_triggers` for the last `TRIGGER_LOOKBACK_DAYS` (default: 3) days
3. Runs a **momentum pre-flight**: sells ETF parking positions to free cash
4. Evaluates each trigger through the same 7 buy gates as primary triggers (incl. pivot extension check)
5. Records buys with `buy_source='momentum_triggers'`

`momentum_triggers` positions receive **lower priority than `daily_triggers` in stale rotation** — they are the first stocks sold when a fresh CANSLIM primary breakout arrives and the portfolio is full.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MOMENTUM_MIN_Q_EPS_GROWTH` | `0.10` | Minimum quarterly EPS growth (both passes) |
| `MOMENTUM_MIN_INST_HOLDERS` | `3` | Minimum institutional holders (both passes) |
| `MOMENTUM_VOLUME_SURGE_MIN` | `1.20` | Pass 2 volume surge threshold |
| `MOMENTUM_PIVOT_PROXIMITY` | `0.95` | Pass 2 proximity to 52w high (0.95 = within 5%) |
| `MOMENTUM_TRIGGER_PRUNE_DAYS` | `56` | Days to retain momentum_trigger rows in Supabase |

Pass 1 technical thresholds reuse the primary screener variables (`VOLUME_SURGE_MIN`, `PIVOT_PROXIMITY`, `SMA_WINDOW`).

---

## Screener Pipeline

```
Run after technical_screener.py (same cron job)
    │
    ▼
Fetch watchlist from Supabase (CANSLIM-screened universe)
    │
    ▼
Apply relaxed fundamental filter:
  Q EPS growth >= MOMENTUM_MIN_Q_EPS_GROWTH (10%)
  Inst. holders >= MOMENTUM_MIN_INST_HOLDERS (3)
    │
    ▼
PASS 1: Standard technical breakout check
  price > SMA50
  volume_surge >= VOLUME_SURGE_MIN (1.40x)
  close >= 52w_high * PIVOT_PROXIMITY (0.98 = within 2%)
    │
    ├─ Pass 1 results >= needed slots? → write to momentum_triggers
    │
    ▼
PASS 2 (if Pass 1 insufficient): Relaxed technical check
  price > SMA50
  volume_surge >= MOMENTUM_VOLUME_SURGE_MIN (1.20x)
  close >= 52w_high * MOMENTUM_PIVOT_PROXIMITY (0.95 = within 5%)
    │
    ▼
Merge Pass 1 + Pass 2 (dedupe) → rank by volume_surge desc
    │
    ▼
Prune stale rows (> MOMENTUM_TRIGGER_PRUNE_DAYS)
    │
    ▼
Write to Supabase momentum_triggers table
```
