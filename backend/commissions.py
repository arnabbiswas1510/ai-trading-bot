"""
Commission-aware P&L.

`trade_history.profit_loss` is and remains GROSS -- (sell - buy) x shares, with
no fee term. That is deliberate: every exit threshold in `decisions/` was
measured gross, and `research/exit_rule_replay.py` does not model commissions,
so redefining the column would silently make all past benchmarks
non-comparable with future runs. Net is derived here instead.

The one rule that matters in this module: a missing commission is UNKNOWN, not
zero. IBKR always charges something, so treating NULL as 0.0 would overstate
net P&L by exactly the amount we failed to record -- and it would look precise
while doing it. Every function here reports completeness alongside the number so
the UI can label a figure as provisional rather than presenting a guess with the
same confidence as a verified value.

Kept free of fastapi/supabase imports so it is unit-testable and safe to import
from CI-only code paths (see decisions/2026-09-05_ci-import-hygiene.md).
"""

from __future__ import annotations


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trade_commission_total(trade: dict) -> tuple[float, bool]:
    """
    (total_commission, complete) for one closed trade.

    `complete` is True only when BOTH legs were reported. A trade with a buy fee
    but no sell fee returns the partial total with complete=False -- useful for
    a lower bound, never safe to present as the cost.
    """
    buy = _to_float(trade.get("buy_commission"))
    sell = _to_float(trade.get("sell_commission"))
    total = (buy or 0.0) + (sell or 0.0)
    return round(total, 4), (buy is not None and sell is not None)


def trade_net_pnl(trade: dict) -> tuple[float, bool]:
    """
    (net_profit_loss, complete) for one closed trade.

    When commissions are unknown this returns the GROSS figure with
    complete=False -- the honest answer is "this is the best we know", flagged,
    rather than an invented fee.
    """
    gross = _to_float(trade.get("profit_loss")) or 0.0
    commission, complete = trade_commission_total(trade)
    return round(gross - commission, 2), complete


def enrich_trades(trades: list[dict]) -> list[dict]:
    """Attach `net_profit_loss` / `total_commission` / `commission_complete`."""
    enriched = []
    for trade in trades:
        row = dict(trade)
        net, complete = trade_net_pnl(trade)
        commission, _ = trade_commission_total(trade)
        row["total_commission"] = commission
        row["net_profit_loss"] = net
        row["commission_complete"] = complete
        enriched.append(row)
    return enriched


def summarize_realized(trades: list[dict]) -> dict:
    """
    Portfolio-level realised P&L, gross and net, with an explicit count of how
    many closed trades still lack commission data.

    `commission_complete` is False if even ONE trade is missing a leg. Partial
    coverage cannot be aggregated into a trustworthy total, and a total that is
    quietly short by an unknown amount is worse than one labelled incomplete.
    """
    gross = 0.0
    commission = 0.0
    incomplete = 0
    for trade in trades:
        gross += _to_float(trade.get("profit_loss")) or 0.0
        trade_commission, complete = trade_commission_total(trade)
        commission += trade_commission
        if not complete:
            incomplete += 1
    return {
        "realized_pnl_gross": round(gross, 2),
        "total_commission": round(commission, 2),
        "realized_pnl_net": round(gross - commission, 2),
        "commission_complete": incomplete == 0 and len(trades) > 0,
        "trades_missing_commission": incomplete,
    }
