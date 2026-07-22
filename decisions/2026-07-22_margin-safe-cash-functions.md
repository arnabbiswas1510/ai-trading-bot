# Decision: Replace get_available_cash with margin-safe functions

**Date:** 2026-07-22
**Commit:** `28df976`
**Status:** Implemented

## Problem

`get_available_cash()` read `AvailableFunds` from the IBKR account values. On a
margin-enabled account this includes buying power from the margin credit line —
not just actual deposited cash. This caused the TRV incident: the bot borrowed
approximately **$35,000 on margin** because it saw that "available funds"
included margin credit and happily sized a position against it.

## Decision

Split into two explicit functions:

- `get_own_cash()` — reads `TotalCashValue` from IBKR. This is the net cash
  balance after subtracting any outstanding margin loan. Always ≤ net
  liquidation value.
- `get_margin_loan()` — returns `abs(TotalCashValue)` when `TotalCashValue < 0`.
  A positive return means money is being borrowed; 0 means no loan active.

Added a **hard buy gate**: if `get_margin_loan() > 0` (any active loan), block
all new buys immediately. No position is opened while margin is in use.

## Why not just fix AvailableFunds?

`AvailableFunds` is computed by IBKR and includes unrealised P&L, margin
credit, and settlement float — all of which can evaporate. `TotalCashValue` is
the one IBKR field that directly represents settled cash on hand.

## Files changed

- `execution_agent.py` — new `get_own_cash()`, `get_margin_loan()`, hard gate
- `tests/test_buy_gates.py` — patched from `get_available_cash` → `get_own_cash` + `get_margin_loan`
- `tests/test_buy_fill_verification.py` — same patch
- `tests/test_margin_safety.py` — new suite covering the margin hard-block gate
- `tests/conftest.py` — added `make_ibkr_fill()` factory + `ibkr_fills` table mock
