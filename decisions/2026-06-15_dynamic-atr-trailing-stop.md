# Decision: Dynamic ATR Trailing Stop

**Date:** 2026-06-15 (approx)
**Commit:** `816a89f`
**Status:** Implemented

## Problem

The fixed 7% trailing stop worked uniformly across all stocks regardless of
their volatility. A high-volatility growth stock (e.g. NVDA) would be stopped
out on normal intraday noise, while a low-volatility stock would give back
too much before triggering.

## Decision

Implement `_compute_dynamic_trail_pct()` — a **two-lever tightening system**:

**Profit lever:** As the position's unrealised gain grows, tighten the trail:
- < 5% gain → 7% trail (default)
- 5–14% gain → 5% trail
- ≥ 14% gain → 3% trail (lock in most of the gain)

**Time lever:** As days held increases, tighten the trail:
- < 10 days → 7% trail (default)
- 10–14 days → 6% trail
- ≥ 15 days → 5% trail

**Resolution:** The tighter of the two levers wins. If both levers agree,
no change. Returns `None` if no tightening is needed (keeps IBKR from placing
a redundant order).

The confirmed trailing stop % is shown in the Telegram buy notification.

## Why two levers?

A profitable position that's been held a long time has had ample opportunity
to demonstrate its strength. Two independent reasons to tighten the stop
provide better coverage: a position can trigger tightening via profit alone
(quick big move) or via time alone (slow grind up).

## Files changed

- `execution_agent.py` — `_compute_dynamic_trail_pct()`
- `telegram_notifier.py` — confirmed trail % in buy notification
- `tests/test_dynamic_trail.py` — 18 tests covering all lever combinations
