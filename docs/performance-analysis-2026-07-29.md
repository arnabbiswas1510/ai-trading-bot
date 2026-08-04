# AI Trading Bot — Performance Analysis
**Date:** 2026-07-29
**Status:** SUPERSEDED — re-audited against the code on 2026-08-04. Most items
are fixed; two were wrong. See `decisions/2026-08-04_reaudit-performance-analysis.md`.

> ⚠️ This document was written from code reading alone, without a backtest.
> Where its recommendations were later measured, **two of them pointed the wrong
> way**: it recommended tightening the trailing stop and tightening the profit
> ladder, and measurement showed both cost money. Treat the diagnoses here as
> reliable and the prescriptions as hypotheses.

| # | Item | Status as of 2026-08-04 |
|---|---|---|
| Bug 1 | Market filter dead code | ✅ Fixed — wired at `execution_agent.py` L1488 |
| Bug 2 | Day 3 volume reads stale bar | ✅ Fixed 2026-08-04 (was still live) |
| Bug 3 | No `final_score` floor | ✅ Fixed — `MIN_TRIGGER_SCORE=60` |
| W4 | Volume surge 1.20 | ✅ Fixed — `VOLUME_SURGE_MIN=1.50` |
| W5 | RS gate 40 | ✅ Fixed — `RS_MIN_GATE=50` |
| W6 | PRE_BREAKOUT boost | ✅ Fixed — boost defaults to 0 |
| W7 | Rank & Replace skips FAIL | ✅ Fixed 2026-08-04 |
| W8 | Cooling-off 1 day | ✅ Fixed 2026-08-04 — now 7, backtest-supported |
| W9 | ATR stop cap 14% | ❌ **Recommendation rejected** — it says tighten to 10%; measurement shows widening 7%→10% roughly doubles CAGR. Cap unchanged pending a decision. |
| W10 | Trigger lookback 3 days | ⚠️ Reframed — lookback length showed no measurable effect; the real defect was that the pivot check had no lower bound. Floor added 2026-08-04. |
| W11 | No trail tightening below +3% | ❌ **Recommendation rejected** — tightening early measurably reduced returns. |
| W12 | Equal dollar sizing | ⬜ Open — untested |
| W13 | Param drift technical score | ⬜ Obsolete — `_compute_param_drift()` no longer exists |
| Scoring | RS weight 10% | ⬜ Open — untested |

---

**Original document follows.**

**Date:** 2026-07-29  
**Context:** Bot losing money; 98.8% invested ($98,816), $882 unrealized gain (~0.9%), market showing "UPTREND UNDER PRESSURE"  
**Recent losses:** OII −$770 (−1.6%), SGHC −$326 (−1.29%)

---

## 🔴 Critical Bugs (Confirmed Code Defects)

---

### Bug 1 — Market Direction Filter Is Dead Code
**File:** `execution_agent.py` | **Lines:** 1134 (def), 1199–1512 (buy loop)

`is_market_bullish()` checks SPY vs SMA-200 but is **never called anywhere**. The buy loop has zero callers for this function. `MARKET_DIRECTION_FILTER_ENABLED=true` is a config variable pointing at dead code.

**Effect:** The bot buys unconditionally in any market — including "UPTREND UNDER PRESSURE" — because there is no automatic brake. The dashboard label is computed by the backend for display only; the execution agent ignores it entirely.
≤
**Fix:**
```python
# execution_agent.py, ~line 1260 (before trigger loop in run_market_open_buys())
if MARKET_DIRECTION_FILTER_ENABLED and not is_market_bullish():
    print("📊 Market bearish (SPY < SMA-200). Standing down from new buys.")
    return
```

Also change `is_market_bullish()` to **fail closed** (return `False`) on API errors instead of the current fail-open `return True` defaults (lines 1151, 1155, 1165–1167).

---

### Bug 2 — Day 3 Breakout Verdict Volume Check Reads 97-Day-Old Data
**File:** `execution_agent.py` | **Lines:** 2484–2486

`_fetch_ohlcv()` returns data sorted **ascending** (oldest first), so `ohlcv[0]` = ~100 days ago. The Day 3 volume verdict compares a random old trading day's volume against another batch of old days — not today's volume against recent average.

```python
# WRONG (current):
day3_vol = ohlcv[0]["volume"]
avg_vol  = sum(b["volume"] for b in ohlcv[1:21]) / 20

# CORRECT:
day3_vol = ohlcv[-1]["volume"]
avg_vol  = sum(b["volume"] for b in ohlcv[-21:-1]) / 20
```

**Effect (cascading):**
- Day 3 `vol_pass` evaluates to `True` spuriously (old volumes are similar to each other)
- Positions almost never receive a `FAIL` verdict
- The **Intraday Loss Minimiser** (Day 4+, fires on 0.5% pullback from today's high) never activates because it requires `breakout_verdict == "FAIL"`
- OII and SGHC likely had failed breakouts that should have triggered the 0.5% pullback sell — instead they were held open until the trailing stop (7–14%) fired at maximum loss

---

### Bug 3 — No Minimum `final_score` Gate in the Buy Loop
**File:** `execution_agent.py` | **Lines:** 1285–1293

The only score-based veto is `ai_grade == "D"` (raw AI rating < 30). C-grade triggers (rating 30–49), which represent "low conviction / multiple signals weakened," are allowed through. There is no floor on the composite `final_score` (0–100).

**Fix:**
```python
# Add after the D-grade veto check (~line 1290):
MIN_TRIGGER_SCORE = int(os.getenv("MIN_TRIGGER_SCORE", 55))
if (trigger.get("final_score") or 0) < MIN_TRIGGER_SCORE:
    print(f"   🚫 {ticker} final_score {trigger.get('final_score')} < {MIN_TRIGGER_SCORE}. Skipping.")
    continue
```

---

## 🟠 High Severity — Structural Weaknesses

---

### Weakness 4 — Volume Surge Threshold Too Low
**File:** `technical_screener.py` | **Line:** 30

```python
VOLUME_SURGE_MIN = float(os.environ.get("VOLUME_SURGE_MIN", 1.20))  # only 20% above average
```

O'Neil's CANSLIM requires 40–50%+ above average volume for institutional buying confirmation. A 20% spike is within normal daily variance and generates many false breakout signals. Note: `compute_quality_score()` normalizes against 3× for full marks, yet the entry gate only requires 1.2×.

**Recommended:** `VOLUME_SURGE_MIN=1.50`; `1.75–2.0` for stronger confirmation.

---

### Weakness 5 — RS Gate Too Permissive
**File:** `technical_screener.py` | **Line:** 38

```python
RS_MIN_GATE = int(os.environ.get("RS_MIN_GATE", 40))
```

Stocks lagging SPY by up to 5% over 12 weeks (RS score 40–49) pass through the gate. O'Neil recommends only buying stocks in the top 50th percentile (RS ≥ 50). In a weak market, RS-40 stocks are momentum losers.

**Recommended:** `RS_MIN_GATE=50`; consider `60` for stricter entry.

---

### Weakness 6 — PRE_BREAKOUT Entries Get a Score Boost (Backwards Incentive)
**File:** `ai_evaluator.py` | **Lines:** 291–295

```python
if trigger_type == "PRE_BREAKOUT":
    boost = int(os.environ.get("PRE_BREAKOUT_SCORE_BOOST", 10))
    final_score = min(100, final_score + boost)  # +10 for unconfirmed setups
```

Stocks that have NOT confirmed a breakout (no volume surge) receive a +10pt advantage over confirmed breakouts. In a weak market, pre-breakout coiling setups frequently turn into distribution patterns. SGHC (−$326) is consistent with this failure mode.

**Recommended:** Remove the boost, or require RS ≥ 60 for PRE_BREAKOUT entries, or disable entirely when market is not "Confirmed Uptrend."

---

### Weakness 7 — Rank & Replace Can't Fire on FAIL-Verdict Positions
**File:** `execution_agent.py` | **Lines:** 2537–2543

```python
if days_held_rr < 7 or verdict_rr != "PASS":
    continue   # FAIL verdict positions excluded from rotation
```

Since Bug 2 causes FAIL verdicts to rarely fire, most losing positions show "PASS." For the rare correct FAIL verdicts, Rank & Replace is explicitly disabled — these positions then rely solely on the broken Intraday Loss Minimiser.

---

### Weakness 8 — COOLING_OFF_DAYS = 1 (Too Short)
**File:** `execution_agent.py` | **Line:** 173

A stock re-enters the eligible buy pool the next trading day after a stop-out. A stock that just hit a 7–14% trailing stop is still in a broken technical condition the next morning.

**Recommended:** `COOLING_OFF_DAYS=7` (one trading week minimum).

---

### Weakness 9 — ATR Stop Cap at 14% Creates Poor Risk/Reward
**File:** `execution_agent.py` | **Line:** 1429

```python
pos_stop_loss_pct = round(max(0.07, min(0.14, atr_derived)), 4)
```

With `MAX_PIVOT_EXTENSION=0.05` (buy up to 5% above pivot) and a 14% trailing stop, you need a 19%+ move to achieve 1:1 R:R. OII and SGHC losses suggest these fired near maximum range.

**Recommended:** Change `min(0.14, …)` → `min(0.10, …)`.

---

### Weakness 10 — TRIGGER_LOOKBACK_DAYS = 3 Allows Stale Entries
**File:** `execution_agent.py` | **Line:** 175

The bot can execute a buy on a 3-day-old breakout trigger. A trigger that has already failed below pivot is not re-screened before the buy (the 5% pivot extension check catches extended stocks but not failed ones).

**Recommended:** `TRIGGER_LOOKBACK_DAYS=1`. If the screener didn't run today, don't buy.

---

### Weakness 11 — No Trailing Stop Tightening Until +3% Gain
**File:** `execution_agent.py` | **Lines:** 155–161

```python
TRAIL_PROFIT_TIERS = [
    (20.0, 0.020),
    (14.0, 0.030),
    ( 8.0, 0.040),
    ( 3.0, 0.050),
    ( 0.0, None),    # <3% gain → no tightening (still at original 7–14% stop)
]
```

All current positions (~0.9% avg gain) are in the `(0.0, None)` tier. With ~$98K invested, this represents a potential ~$6,860–$13,720 further loss from today's prices.

**Recommended additions:**
```python
( 1.0, 0.065),  # ≥ 1% gain → 6.5% trail (start protecting early)
```
And tighten `(3.0, 0.050)` → `(3.0, 0.035)`.

---

### Weakness 12 — Equal Dollar Sizing Regardless of Conviction
**File:** `execution_agent.py` | **Lines:** 1297–1301

```python
position_size = available_cash / remaining_slots  # equal weight always
```

A trigger with `final_score=35` gets the same ~25% portfolio weighting as one with `final_score=85`. Consider conviction-weighted sizing: scale position between 15–35% of portfolio based on `final_score`.

---

### Weakness 13 — `_compute_param_drift()` Technical Score Uses Only SMA Margin
**File:** `execution_agent.py` | **Lines:** 1631–1645

The entry `technical_score` was computed with volume surge (40%) + pivot proximity (40%) + SMA margin (20%). The "current" version for drift comparison uses **only SMA margin** — the 80% majority is dropped — making technical drift detection unreliable for rotation decisions.

---

## 🟡 Scoring Formula Imbalance

**File:** `scoring.py` | **Lines:** 77–96

```python
# Current weights:
technical 30% + liquidity 25% + ai 25% + sentiment 10% + rs 10%

# Recommended weights (RS is the highest-predictive CAN SLIM factor):
technical 30% + liquidity 20% + ai 20% + sentiment  5% + rs 25%
```

RS vs SPY is the single most predictive factor in O'Neil's methodology, yet it receives only 10% of the composite score. Sentiment (news headlines) receives the same 10% despite being noisy.

---

## 📋 Priority-Ordered Action List

| Priority | Issue | File | Change |
|----------|-------|------|--------|
| 🔴 P1 | **Bug 1**: Wire market filter | `execution_agent.py` ~L1260 | Call `is_market_bullish()` before trigger loop; fail closed on API error |
| 🔴 P1 | **Bug 2**: Fix Day 3 volume index | `execution_agent.py` L2484–2486 | `ohlcv[0]` → `ohlcv[-1]`; `ohlcv[1:21]` → `ohlcv[-21:-1]` |
| 🔴 P1 | **Bug 3**: Add `final_score` floor | `execution_agent.py` ~L1290 | Skip if `final_score < 55` |
| 🟠 P2 | Volume surge too low | `.env` / `technical_screener.py` | `VOLUME_SURGE_MIN=1.50` |
| 🟠 P2 | RS gate too low | `.env` / `technical_screener.py` | `RS_MIN_GATE=50` |
| 🟠 P2 | Cooling-off too short | `.env` | `COOLING_OFF_DAYS=7` |
| 🟠 P2 | Trigger lookback too long | `.env` | `TRIGGER_LOOKBACK_DAYS=1` |
| 🟠 P2 | ATR stop cap too wide | `execution_agent.py` L1429 | `min(0.14, …)` → `min(0.10, …)` |
| 🟡 P3 | RS weight too low in scoring | `scoring.py` L89–95 | RS: 10% → 25%; reduce liquidity + AI + sentiment |
| 🟡 P3 | No early trail tightening | `execution_agent.py` L155 | Add `(1.0, 0.065)` tier |
| 🟡 P3 | PRE_BREAKOUT score boost | `ai_evaluator.py` L291–295 | Remove +10 boost or require RS ≥ 60 |
| 🟡 P3 | Equal position sizing | `execution_agent.py` L1300 | Scale by `final_score` (15–35% range) |
| 🟡 P3 | Drift tech score incomplete | `execution_agent.py` L1631–1645 | Include volume and pivot components |

---

## 🔍 Root Cause Map

| Symptom | Root Cause(s) |
|---------|--------------|
| **98.8% invested in weak market** | Bug 1 (market filter not called) + Bug 3 (no score floor) + Weakness 4 (low volume bar) + Weakness 6 (PRE_BREAKOUT boost fills slots) |
| **OII −$770, SGHC −$326 losses** | Bug 2 (FAIL verdict doesn't fire → Intraday Loss Minimiser disabled → held to full stop) + Weakness 4 (fake breakouts) + Weakness 5 (RS-40 laggards) + Weakness 9 (14% stop too wide) |
| **$882 unrealized on $98K (0.9%)** | Weakness 11 (no tightening below +3% → full 7–14% downside exposure) + Bug 1 (buying into headwinds) + Weakness 12 (equal sizing regardless of conviction) |
