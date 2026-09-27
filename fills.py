"""Fill ingestion & commission accounting, extracted from execution_agent.py
(see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: patched siblings (trade_commission, notifier) and the frozen
``execution_agent.datetime`` clock are referenced via ``ea.<name>`` so mock.patch
on execution_agent stays live. ``_FILL_SINK_ALERTED`` is this module's own state
(mutated in place) and is intentionally NOT proxied.
"""
import time
from supabase import Client

import execution_agent as ea

def extract_fill_commission(fill) -> float | None:
    """
    The commission on a Fill, or None if IBKR has not reported it yet.

    IBKR sends execution details and the commission in SEPARATE messages.
    ib_insync attaches a blank CommissionReport to the Fill when execDetails
    arrives and populates it moments later from commissionReportEvent. So at
    execDetails time this is almost always unreported.

    Unreported arrives as 0.0, an unset attribute, or NaN. None of those is a
    real fee, and all three must map to None rather than 0: a stored 0 is
    indistinguishable from a genuinely free fill and would silently overstate
    net P&L. IBKR stock commissions have a per-order minimum, so a true 0 does
    not occur on this account.
    """
    report = getattr(fill, "commissionReport", None)
    if report is None:
        return None
    raw = getattr(report, "commission", None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:   # NaN, or unreported/zero
        return None
    return round(value, 4)


_FILL_SINK_ALERTED: set[str] = set()


def _fill_sink_failure(sink: str, ticker: str, error: Exception) -> None:
    """
    Escalate a failed write to a sink nobody reads, exactly once per process.

    ibkr_fills and breakout_learnings are write-only: no screen renders them, so
    an empty table looks identical to a quiet week. Both were rejected by an RLS
    policy on every single write for six weeks, and because the handlers only
    print()ed, the sole trace was a log line nobody had reason to grep. Tier 1 of
    the sell-price ladder was inert that entire time.

    Alerting once per sink -- not once per fill -- keeps a partial outage from
    turning into a Telegram flood while still guaranteeing the first failure is
    seen.
    """
    print(f"   ⚠️  {sink}: failed to write {ticker}: {error}")
    if sink in _FILL_SINK_ALERTED:
        return
    _FILL_SINK_ALERTED.add(sink)
    try:
        ea.notifier.notify_error(
            f"🚨 <b>{sink} writes are failing</b>\n"
            f"First failure on {ticker}.\n"
            f"<code>{str(error)[:300]}</code>\n\n"
            f"This sink is not rendered anywhere, so it will look empty rather "
            f"than broken. Further failures this run are suppressed."
        )
    except Exception:
        pass


def persist_fill(client: Client, fill) -> bool:
    """
    Upsert one IBKR fill into `ibkr_fills`. Returns True if the row was written.

    This is Tier 1 of the sell-price ladder in reconcile_with_ibkr(): the only
    fill record that survives an agent restart, a container restart or an IB
    Gateway session reset. reqExecutions() (Tier 2) holds the current TWS
    session only, which is what recorded RSI's sell price incorrectly on
    2026-07-17.

    Commission is written only when IBKR has actually reported it, so a later
    commissionReportEvent can fill it in without this call clobbering it back
    to zero -- see update_fill_commission().
    """
    execution = getattr(fill, "execution", None)
    exec_id = getattr(execution, "execId", None) if execution else None
    if not exec_id:
        return False

    payload = {
        "exec_id":    exec_id,
        "ticker":     fill.contract.symbol,
        "side":       execution.side,              # 'BOT' or 'SLD'
        "shares":     float(execution.shares),
        "price":      float(execution.price),
        "fill_time":  execution.time.isoformat(),
        "order_id":   execution.orderId,
        "account_id": execution.acctNumber,
    }
    commission = extract_fill_commission(fill)
    if commission is not None:
        payload["commission"] = commission

    client.table("ibkr_fills").upsert(payload, on_conflict="exec_id").execute()
    return True


def update_fill_commission(client: Client, exec_id: str, commission: float) -> bool:
    """
    Attach a commission to an already-persisted fill.

    Called from commissionReportEvent, which is the ONLY message that carries
    the real figure. Without this handler the commission column would stay at
    its default forever, which is how every P&L number in the dashboard came to
    be gross.
    """
    if not exec_id or commission is None or commission <= 0:
        return False
    client.table("ibkr_fills") \
        .update({"commission": round(float(commission), 4)}) \
        .eq("exec_id", exec_id).execute()
    return True


def sum_fill_commission(client: Client, ticker: str, side: str,
                        since: str | None = None) -> float | None:
    """
    Total commission IBKR charged for `ticker` on `side` ('BOT' or 'SLD').

    Returns None -- never 0.0 -- when there are no matching fills, or when any
    matching fill has no commission recorded. A partial sum would understate the
    cost while looking authoritative; the caller stores NULL and the dashboard
    labels the trade as provisional instead.
    """
    try:
        query = client.table("ibkr_fills") \
            .select("commission,fill_time") \
            .eq("ticker", ticker).eq("side", side)
        if since:
            query = query.gte("fill_time", since)
        rows = query.execute().data or []
    except Exception as e:
        print(f"   ⚠️  commission lookup failed for {ticker} {side}: {e}")
        return None

    if not rows:
        return None
    total = 0.0
    for row in rows:
        value = row.get("commission")
        if value is None or float(value) <= 0:
            return None        # incomplete -> unknown, not partial
        total += float(value)
    return round(total, 4)


# Widest gap tolerated between a position's recorded buy_date and the BOT fills
# that opened it. Child fills of one market order land within seconds; a prior
# round trip's entry is separated by far more. 15 minutes cleanly admits the
# former and excludes the latter (NTRA's re-entry was 46 minutes after its
# previous buy).
LOT_FILL_LOOKBACK_MINUTES = 15


def lot_buy_basis_from_fills(client: Client, ticker: str, buy_date: str | None,
                             shares: int) -> tuple[float, str] | None:
    """
    Weighted-average BOT fill price for the lot that is currently open.

    This is the authoritative answer to "what did THIS position cost" -- the
    prices IBKR actually executed -- as opposed to ``averageCost``, which is an
    account-level figure that folds realised P&L from earlier round trips of the
    same symbol into the surviving lot (see the drift guard for the NTRA proof).

    Walks BOT fills backwards from the position's ``buy_date`` and consumes only
    as many as the position holds, so a ticker that was bought, sold and bought
    again prices off the LAST entry alone. Returns None -- never a partial
    answer -- when the fills cannot account for the full share count, so the
    caller can fall back rather than act on an under-filled average.
    """
    if not buy_date or shares <= 0:
        return None
    try:
        # +2 min of slack on the upper bound: buy_date is stamped by the DB when
        # the position row is inserted, a beat AFTER the last child fill.
        upper = _iso_shift_minutes(buy_date, +2)
        lower = _iso_shift_minutes(buy_date, -LOT_FILL_LOOKBACK_MINUTES)
        rows = client.table("ibkr_fills") \
            .select("shares,price,fill_time,exec_id") \
            .eq("ticker", ticker).eq("side", "BOT") \
            .lte("fill_time", upper).gte("fill_time", lower) \
            .order("fill_time", desc=True).execute().data or []
    except Exception as e:
        print(f"   ⚠️  BOT fill lookup failed for {ticker}: {e}")
        return None

    remaining, cost, used = float(shares), 0.0, []
    for row in rows:
        if remaining <= 0:
            break
        try:
            qty, price = float(row["shares"]), float(row["price"])
        except (TypeError, ValueError, KeyError):
            return None
        take = min(qty, remaining)
        cost += take * price
        remaining -= take
        used.append(row.get("exec_id", "?"))

    if remaining > 0 or not used:
        return None        # fills don't cover the position -> unknown, not partial
    return round(cost / float(shares), 4), f"{len(used)} BOT fill(s): {', '.join(used)}"


def has_prior_round_trip(client: Client, ticker: str, buy_date: str | None) -> bool:
    """
    True if `ticker` was sold at any point BEFORE this position was opened.

    IBKR's ``averageCost`` does not reliably reset to the new lot's price when a
    symbol is round-tripped: for NTRA it reported (total buys - total sells) /
    remaining shares, i.e. it buried two earlier realised losses inside the
    surviving lot's basis. A prior sell is therefore the signal that
    ``averageCost`` may be contaminated and must not overwrite a recorded price.
    """
    if not buy_date:
        return False
    try:
        rows = client.table("ibkr_fills") \
            .select("exec_id") \
            .eq("ticker", ticker).eq("side", "SLD") \
            .lt("fill_time", _iso_shift_minutes(buy_date, -LOT_FILL_LOOKBACK_MINUTES)) \
            .limit(1).execute().data or []
        return bool(rows)
    except Exception:
        return False       # unknown -> don't claim contamination


def _iso_shift_minutes(iso_ts: str, minutes: int) -> str:
    """Shift an ISO timestamp by `minutes`, preserving tz-awareness."""
    dt = ea.datetime.datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ea.datetime.timezone.utc)
    return (dt + ea.datetime.timedelta(minutes=minutes)).isoformat()


def trade_commission(ib, trade, wait_secs: float = 2.0) -> float | None:
    """
    Total commission across every fill of a completed Trade, or None.

    Briefly waits for commissionReportEvent, which IBKR sends after the
    execution report. Returns None unless EVERY fill has a reported commission:
    a partial sum on a multi-fill order would understate the true cost while
    looking exact.
    """
    deadline = time.time() + max(0.0, wait_secs)
    while True:
        fills = list(getattr(trade, "fills", []) or [])
        if fills:
            values = [extract_fill_commission(f) for f in fills]
            if all(v is not None for v in values):
                return round(sum(values), 4)
        if time.time() >= deadline:
            return None
        try:
            ib.sleep(0.25)
        except Exception:
            return None


def record_buy_commission(client: Client, ib, ticker: str, trade) -> float | None:
    """
    Persist the entry commission onto an already-inserted position row.

    Deliberately a SEPARATE update rather than a field on the initial insert.
    The insert is the step that makes a filled position visible to the capacity
    check; if `buy_commission` were part of it and the migration had not been
    applied, PGRST204 would abort the whole insert and leave a position live at
    IBKR but absent from Supabase -- the exact phantom-fill failure the
    insert-before-stop ordering exists to prevent. A cost figure is never worth
    that risk.
    """
    commission = ea.trade_commission(ib, trade)
    if commission is None:
        return None
    try:
        client.table("portfolio_positions") \
            .update({"buy_commission": commission}).eq("ticker", ticker).execute()
        print(f"   🧾 Entry commission recorded for {ticker}: ${commission:.2f}")
        return commission
    except Exception as e:
        print(f"   ⚠️  Could not store buy_commission for {ticker} "
              f"(run migrations/20260906_add_commission_tracking.sql): {e}")
        return None


def record_trade_commissions(client: Client, trade_row_id,
                             buy_commission, sell_commission) -> bool:
    """
    Attach both commission legs to a trade_history row after it is inserted.

    Separate from the insert for the same reason as record_buy_commission(), but
    the stakes are higher here: execute_sell() deletes the portfolio_positions
    row BEFORE inserting into trade_history, so an insert that fails on an
    unknown column would erase the position with no closing record at all. Only
    keys with a real value are sent -- a NULL commission means "not reported by
    IBKR", which is not the same as zero and must not be written as one.
    """
    payload = {}
    if buy_commission is not None:
        payload["buy_commission"] = round(float(buy_commission), 4)
    if sell_commission is not None:
        payload["sell_commission"] = round(float(sell_commission), 4)
    if not payload or trade_row_id is None:
        return False
    try:
        client.table("trade_history").update(payload).eq("id", trade_row_id).execute()
        return True
    except Exception as e:
        print(f"   ⚠️  Could not store commissions on trade_history id={trade_row_id} "
              f"(run migrations/20260906_add_commission_tracking.sql): {e}")
        return False
