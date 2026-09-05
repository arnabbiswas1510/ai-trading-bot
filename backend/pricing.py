"""
Pure pricing helpers for the dashboard API.

Deliberately dependency-free: no FastAPI, no Supabase, no HTTP client. This
module exists so the pricing rules can be imported and tested without pulling in
the web stack. backend/main.py imports FastAPI at module scope, and FastAPI is
declared only in backend/requirements.txt (the trading-bot image) -- not in the
root requirements.txt that CI installs. A test importing backend.main therefore
fails at collection on CI while passing locally, which is exactly what broke the
Daily Screener workflow on 2026-09-05: the screener steps never ran because the
pytest step failed first.

Anything here must stay importable with the standard library alone.
"""

PRICE_SOURCE_IBKR = "IBKR"
PRICE_SOURCE_FMP = "FMP"
PRICE_SOURCE_COST_BASIS = "COST_BASIS"


def resolve_position_price(pos: dict, fmp_price: float | None) -> tuple[float, str]:
    """Decide which price to display for an open position, and name the source.

    IBKR first, FMP second, cost basis last. This mirrors get_position_price()
    in execution_agent.py, which prices exits the same way: IBKR is
    authoritative because it is what orders fill against, and FMP covers only
    the window where the broker has no mark -- the agent has not reconciled the
    position yet, or its data farm is down.

    The web container cannot call ib.portfolio() itself (no brokerage access by
    design), so "no IBKR mark" here means the persisted columns are empty. Both
    fallbacks are named through the returned source so the UI can label them; an
    unlabelled third-party price mixed into a broker-sourced total is the defect
    this must not reintroduce.

    Cost basis is last because it is not a market price at all -- it drives
    unrealized P&L to exactly $0.00, which is indistinguishable from a flat book.

    Returns (display_price, price_source) where price_source is one of
    'IBKR', 'FMP' or 'COST_BASIS'.
    """
    ibkr_price = pos.get("current_price")
    ibkr_synced = pos.get("ibkr_synced_at")
    # Both are required: a price without a sync timestamp cannot be attributed,
    # and a timestamp without a price is not a mark.
    if ibkr_price is not None and ibkr_synced is not None:
        return float(ibkr_price), PRICE_SOURCE_IBKR
    if fmp_price is not None and fmp_price > 0:
        return float(fmp_price), PRICE_SOURCE_FMP
    return float(pos["buy_price"]), PRICE_SOURCE_COST_BASIS
