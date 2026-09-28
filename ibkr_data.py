"""ibkr_data.py — IBKR + FMP price and account-value access.

Extracted from execution_agent.py (2026-09-27 orchestrator split). Live-price
lookup, IBKR portfolio price maps, and cash / margin / net-liquidation reads.
Behaviour is unchanged — the same code, relocated.

SAFETY INVARIANT (see decisions/2026-09-27_execution-agent-modular-split.md):
every name the test-suite patches on `execution_agent` — get_live_price,
get_ibkr_account, get_own_cash, get_margin_loan, get_net_liquidation, FMP_API_KEY,
fmp_session, notifier — is either re-exported back into execution_agent (so
patching it there rebinds the object callers see) or, when called *from inside
this module*, referenced as `ea.<name>` so the patch is not bypassed. No patched
name is imported by value here.
"""
from __future__ import annotations

import os
import time
import datetime
from zoneinfo import ZoneInfo

from ib_insync import IB

from execution_agent_ref import ea


def get_live_price(ticker: str) -> float:
    """Fetch current price of a ticker from FMP."""
    url = f"https://financialmodelingprep.com/stable/quote?symbol={ticker}&apikey={ea.FMP_API_KEY}"
    try:
        res = ea.fmp_session.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0))
    except Exception as e:
        ea.notifier.notify_exception(f"get_live_price() — execution_agent.py", e)
        print(f"❌ Error fetching price for {ticker} from FMP: {e}")
    return 0.0


class _MarkContract:
    """Minimal stand-in for an ib_insync Contract inside a synthesized mark."""
    __slots__ = ("symbol", "secType", "conId")

    def __init__(self, symbol: str, conId=None):
        self.symbol = symbol
        self.secType = "STK"
        self.conId = conId


class _IBKRMark:
    """A PortfolioItem-shaped mark synthesized from reqPnLSingle().

    Exposes the same attributes reconcile/pricing read off a real PortfolioItem
    (marketPrice, marketValue, unrealizedPNL, position, account, contract) so the
    reqPnLSingle fallback is a drop-in for the ib.portfolio() fast path.
    """
    __slots__ = ("contract", "account", "position",
                 "marketPrice", "marketValue", "unrealizedPNL")

    def __init__(self, symbol, account, position, marketPrice,
                 marketValue, unrealizedPNL, conId=None):
        self.contract      = _MarkContract(symbol, conId)
        self.account       = account
        self.position      = position
        self.marketPrice   = marketPrice
        self.marketValue   = marketValue
        self.unrealizedPNL = unrealizedPNL


# Marks change negligibly within a monitoring cycle, and every builder below can
# fire several times per cycle (monitor, balance sync, reconcile, ad-hoc exits).
# A short TTL collapses those into a single broker round-trip without ever
# serving a stale-enough price to matter for a 15-minute loop.
_IBKR_PRICE_MAP_CACHE = {"ts": 0.0, "map": {}}
_IBKR_PRICE_MAP_TTL   = 15.0


def build_ibkr_price_map(ib: IB, force: bool = False) -> dict:
    """Return {symbol: mark} for the TARGET account's open positions only.

    The bot trades exactly one IBKR account (``IBKR_ACCOUNT`` /
    get_ibkr_account); any other account visible under the same login is ignored
    everywhere. Marks come from, in order:

    1. ``ib.portfolio()`` filtered to the target account — the non-blocking
       in-memory account-update stream. This is served only for SINGLE-account
       logins; ib_insync does not start it when several accounts are linked.
    2. ``reqPnLSingle`` per target-account position — IBKR computes value and
       unrealizedPnL server-side (like NetLiquidation), so it works for
       MULTI-account logins and needs no market-data line. marketPrice is
       derived as value / shares.

    Neither path uses the blocking ``ib.reqTickers()``. Results are TTL-cached so
    repeated calls within a cycle cost one round-trip. Pass ``force=True`` to
    bypass the cache.
    """
    now   = time.monotonic()
    cache = _IBKR_PRICE_MAP_CACHE
    if not force and cache["map"] and (now - cache["ts"]) < _IBKR_PRICE_MAP_TTL:
        return cache["map"]

    price_map = _compute_ibkr_price_map(ib)
    if price_map:
        cache["ts"]  = now
        cache["map"] = price_map
    return price_map


def _compute_ibkr_price_map(ib: IB) -> dict:
    """Build the target-account price map (see build_ibkr_price_map)."""
    try:
        target = ea.get_ibkr_account(ib)
    except Exception as e:
        print(f"   ⚠️ Could not determine IBKR account for pricing: {e}")
        target = None

    # Fast path: portfolio() marks for the target account (single-account logins).
    try:
        port = [p for p in ib.portfolio() if _matches_account(p, target)]
    except Exception as e:
        print(f"   ⚠️ Could not read IBKR portfolio for pricing: {e}")
        port = []

    fast = {}
    for p in port:
        mp = getattr(p, "marketPrice", None)
        try:
            mp = float(mp) if mp is not None else 0.0
        except (TypeError, ValueError):
            mp = 0.0
        if (getattr(p.contract, "secType", "STK") == "STK"
                and int(getattr(p, "position", 0)) > 0
                and mp == mp and mp > 0):        # NaN-safe
            fast[p.contract.symbol] = p
    if fast:
        return fast

    # Fallback: multi-account login — portfolio() is not served for this login,
    # so price the target account's positions off reqPnLSingle instead.
    if target is None:
        return {}
    return _pnl_single_price_map(ib, target)


def _pnl_single_price_map(ib: IB, account: str) -> dict:
    """Return {symbol: _IBKRMark} for the account's STK positions via reqPnLSingle.

    Used when ib.portfolio() is empty because more than one account is linked to
    the login. reqPnLSingle is server-computed and account-scoped, so it never
    reads or reports on any account other than ``account``.
    """
    out = {}
    try:
        try:
            ib.reqPositions()
            ib.sleep(1)
        except Exception:
            pass

        poss = [
            p for p in ib.positions()
            if _matches_account(p, account)
            and p.contract.secType == "STK"
            and p.position > 0
        ]

        subs = []
        for p in poss:
            try:
                s = ib.reqPnLSingle(account, "", p.contract.conId)
                subs.append((p, s))
            except Exception as e:
                print(f"   ⚠️ reqPnLSingle failed for {p.contract.symbol}: {e}")

        # Wait (bounded) for the server to push value/unrealizedPnL for every sub.
        for _ in range(6):
            ib.sleep(0.5)
            if all(s.value == s.value for _, s in subs):   # no NaNs remain
                break

        for p, s in subs:
            val  = s.value
            upnl = s.unrealizedPnL
            if val == val and val and p.position:          # NaN-safe, non-zero
                out[p.contract.symbol] = _IBKRMark(
                    p.contract.symbol, account, p.position,
                    val / float(p.position), val,
                    upnl if upnl == upnl else 0.0,
                    p.contract.conId,
                )

        for p, _ in subs:
            try:
                ib.cancelPnLSingle(account, "", p.contract.conId)
            except Exception:
                pass
    except Exception as e:
        print(f"   ⚠️ Could not build IBKR price map via reqPnLSingle: {e}")
    return out


def ibkr_target_positions(ib: IB, account: str | None = None) -> dict:
    """Return {symbol: shares} for STK holdings in the TARGET account only.

    Reads ib.positions() (primed by reqPositions), which — unlike ib.portfolio()
    — IBKR serves for multi-account logins. This is the reliable holdings source
    for sell confirmation and share-count checks, and it ignores every account
    other than the configured one.
    """
    if account is None:
        try:
            account = ea.get_ibkr_account(ib)
        except Exception:
            account = None
    try:
        ib.reqPositions()
        ib.sleep(1)
    except Exception:
        pass
    out = {}
    for p in ib.positions():
        if (p.contract.secType == "STK"
                and _matches_account(p, account)
                and int(p.position) > 0):
            out[p.contract.symbol] = int(p.position)
    return out


def get_position_price(ib: IB, ticker: str, ib_map: dict | None = None) -> tuple:
    """IBKR-first live price for an OPEN position, with FMP fallback.

    Live trades are executed against IBKR, so exit rules and account valuation
    must be decided on IBKR's own mark — the same PortfolioItem.marketPrice the
    dashboard and reconcile_with_ibkr() already use. Pricing exits off a second
    source (FMP) is what caused fill-vs-decision mismatches in the past.

    FMP is retained ONLY as a fallback for when IBKR has no usable mark (data
    farm down, or the position has not yet appeared in the account-update
    stream). This is safe because the IBKR read here is build_ibkr_price_map() —
    ib.portfolio() for single-account logins, else a reqPnLSingle snapshot —
    never the blocking ib.reqTickers() path.

    Args:
        ib:      connected IB handle.
        ticker:  symbol to price.
        ib_map:  optional precomputed {symbol: PortfolioItem} from
                 build_ibkr_price_map(ib); built on demand when omitted.

    Returns:
        (price: float, source: str) where source is 'ibkr' or 'fmp'.
        price is 0.0 only when BOTH sources fail.
    """
    if ib_map is None:
        ib_map = build_ibkr_price_map(ib)

    item = ib_map.get(ticker)
    if item is not None:
        mp = getattr(item, "marketPrice", None)
        try:
            mp = float(mp) if mp is not None else 0.0
        except (TypeError, ValueError):
            mp = 0.0
        # NaN-safe: NaN != NaN.
        if mp == mp and mp > 0:
            return mp, "ibkr"

    fmp_price = ea.get_live_price(ticker)
    if fmp_price > 0:
        print(f"   ↩️ {ticker}: IBKR mark unavailable — FMP fallback ${fmp_price:.2f}")
    return fmp_price, "fmp"


def fetch_historical_closes_with_dates(ticker: str, window: int) -> list:
    """Fetch historical daily close prices and dates from FMP (oldest first)."""
    # Fetch window * 4 + 20 calendar days to guarantee sufficient trading days
    lookback_days = window * 4 + 20
    to_date = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    from_date = to_date - datetime.timedelta(days=lookback_days)
    url = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
           f"?symbol={ticker}&from={from_date}&to={to_date}"
           f"&apikey={ea.FMP_API_KEY}")
    try:
        r = ea.fmp_session.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                # Return sorted by date ascending (oldest first)
                return sorted(data, key=lambda x: x["date"])
            else:
                print(f"⚠️ Empty historical data response for {ticker} from FMP.")
        else:
            print(f"⚠️ FMP historical API returned status code {r.status_code} for {ticker}.")
    except Exception as e:
        ea.notifier.notify_exception(f"fetch_historical_closes_with_dates() — execution_agent.py", e)
        print(f"❌ Error fetching historical prices for {ticker} from FMP: {e}")
    return []



def _matches_account(obj, target_account: str | None) -> bool:
    """Return True if obj belongs to target_account, or if obj has no account string set (e.g. test mocks)."""
    if not target_account:
        return True
    acc = getattr(obj, "account", None)
    if acc is None or not isinstance(acc, str):
        return True
    return acc == target_account


def _ibkr_avg_cost(obj) -> float | None:
    """Per-share average cost from an IBKR position object, source-agnostic.

    ib.portfolio() yields PortfolioItem (attribute ``averageCost``); the
    multi-account positions() fallback yields Position (attribute ``avgCost``).
    Both are commission-inclusive per-share cost for US stocks, and the two
    attribute sets are mutually exclusive on the real objects — so prefer
    ``averageCost`` and only consult ``avgCost`` when it is genuinely absent.
    Returns None if the resolved value is not a usable positive float (e.g. a
    zero-cost row, or a bare test mock).
    """
    raw = getattr(obj, "averageCost", None)
    if raw is None:
        raw = getattr(obj, "avgCost", None)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val or val <= 0:   # NaN-safe
        return None
    return val


def get_own_cash(ib: IB, account: str = None) -> float:
    """Return only the agent's own (non-borrowed) cash balance in USD.

    Reads ``TotalCashValue`` — IBKR's signed sum of all cash in the account.
    Filters by target account (`account` param or `get_ibkr_account(ib)`) to
    prevent reading cash balances from other sub-accounts under the same login.

    Returns:
        float: own cash in USD (>= 0.0).  Returns 0.0 if a margin loan is
               detected OR if the IBKR query fails.
    """
    try:
        target_account = account or ea.get_ibkr_account(ib)
        account_values = ib.accountValues()

        total_cash = None
        net_liq    = None

        for av in account_values:
            if not _matches_account(av, target_account):
                continue
            if av.currency != "USD":
                continue
            if av.tag == "TotalCashValue":
                total_cash = float(av.value)
            elif av.tag == "NetLiquidation":
                net_liq = float(av.value)

        if total_cash is None:
            print(f"⚠️ get_own_cash(): TotalCashValue tag not found for account {target_account}. Returning 0.")
            return 0.0

        if total_cash < 0:
            # Negative TotalCashValue = margin loan is active.
            # Hard block: return 0 so the buy loop skips all purchases.
            margin_loan = abs(total_cash)
            print(
                f"🚨 MARGIN LOAN DETECTED [{target_account}]: TotalCashValue = ${total_cash:,.2f} "
                f"(margin borrowed: ${margin_loan:,.2f}). "
                f"Returning 0 — no new buys until loan is repaid."
            )
            return 0.0

        # Cap own_cash at NetLiquidation as a sanity guard.
        if net_liq is not None and total_cash > net_liq > 0:
            print(f"⚠️ get_own_cash(): TotalCashValue (${total_cash:,.2f}) > NetLiquidation "
                  f"(${net_liq:,.2f}) for {target_account}. Capping to NetLiquidation.")
            return round(net_liq, 2)

        return round(total_cash, 2)

    except Exception as e:
        ea.notifier.notify_exception(f"get_own_cash() — execution_agent.py", e)
        print(f"❌ Error querying own cash from IBKR: {e}")
    return 0.0


def get_margin_loan(ib: IB, account: str = None) -> float:
    """Return the current margin loan amount in USD (0.0 if no loan).

    A positive return value means IBKR has lent this amount to the account.
    Filters by target account (`account` param or `get_ibkr_account(ib)`).
    """
    try:
        target_account = account or ea.get_ibkr_account(ib)
        for av in ib.accountValues():
            if not _matches_account(av, target_account):
                continue
            if av.tag == "TotalCashValue" and av.currency == "USD":
                raw = float(av.value)
                return round(abs(raw), 2) if raw < 0 else 0.0
    except Exception as e:
        print(f"⚠️ get_margin_loan(): could not fetch TotalCashValue: {e}")
    return 0.0


def get_net_liquidation(ib: IB, account: str = None) -> float:
    """Return total account equity (cash + position market value) in USD.

    Reads IBKR's ``NetLiquidation`` tag, filtered to the target account the same
    way get_own_cash() does, so a second sub-account under the same login cannot
    inflate the figure.

    Used to size the Early Dollar Stop as a share of equity rather than a fixed
    dollar amount, so the cap tracks account growth instead of silently becoming
    a tighter percentage every time the account gets larger.

    Returns:
        float: equity in USD, or 0.0 if the tag is missing or the query fails.
               Callers MUST treat 0.0 as "unknown" and skip the rule rather than
               computing a zero-dollar threshold, which would exit everything.
    """
    try:
        target_account = account or ea.get_ibkr_account(ib)
        for av in ib.accountValues():
            if not _matches_account(av, target_account):
                continue
            if av.tag == "NetLiquidation" and av.currency == "USD":
                value = float(av.value)
                return round(value, 2) if value > 0 else 0.0
        print(f"⚠️ get_net_liquidation(): NetLiquidation tag not found for {target_account}.")
    except Exception as e:
        ea.notifier.notify_exception("get_net_liquidation() — execution_agent.py", e)
        print(f"❌ Error querying net liquidation from IBKR: {e}")
    return 0.0


def get_available_cash(ib: IB) -> float:
    """Deprecated alias for get_own_cash().

    DEPRECATED: Previously read AvailableFunds (which includes margin lending).
    Now delegates to get_own_cash() which reads TotalCashValue and hard-blocks
    when a margin loan is active. Kept (with a regression test in
    tests/test_margin_safety.py) so any old call site automatically gets the
    margin-safe value without code changes. All new code should call
    get_own_cash() directly.
    """
    return ea.get_own_cash(ib)


# ─────────────────────────────────────────────────────────────────────────────
# IBKR Order Management Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_ibkr_account(ib: IB) -> str:
    """
    Returns the configured IBKR live account.
    Priority: IBKR_ACCOUNT env var → U12941651 (if present) → first live account (U...) → accounts[0].
    Raises if both live and paper (DU...) accounts are visible without
    IBKR_ACCOUNT being set — prevents accidentally trading on the wrong account.
    """
    accounts = ib.managedAccounts()
    if not accounts:
        raise ValueError("No IBKR accounts found for this login.")

    # Explicit override always wins
    env_account = os.getenv("IBKR_ACCOUNT")
    if env_account:
        if env_account not in accounts:
            raise ValueError(
                f"IBKR_ACCOUNT='{env_account}' not in managed accounts {accounts}. "
                "Check your .env file."
            )
        return env_account

    # Default to primary trading account U12941651 if available
    if "U12941651" in accounts:
        return "U12941651"

    # Prefer live accounts (U...) over paper (DU...)
    live_accounts   = [acc for acc in accounts if acc.startswith('U') and not acc.startswith('DU')]
    paper_accounts  = [acc for acc in accounts if acc.startswith('DU')]

    if paper_accounts and not live_accounts:
        # Only paper accounts visible — warn loudly but continue
        print(
            f"⚠️  WARNING: Only paper account(s) found: {paper_accounts}. "
            "Set IBKR_ACCOUNT=<live_account_id> in .env to trade live."
        )
        return paper_accounts[0]

    if paper_accounts and live_accounts:
        # Both exist — refuse to guess, require explicit config
        raise ValueError(
            f"Both live {live_accounts} and paper {paper_accounts} accounts visible. "
            "Set IBKR_ACCOUNT=<live_account_id> in .env to avoid ambiguity."
        )

    return live_accounts[0] if live_accounts else accounts[0]
