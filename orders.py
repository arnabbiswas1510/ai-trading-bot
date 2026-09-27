"""Order & exit primitives extracted from execution_agent.py.

SAFETY INVARIANT (see decisions/2026-09-27_execution-agent-modular-split.md):
tests patch ``execution_agent.<name>`` at ~260 sites. Python resolves a called
name in the callee's own module globals, so any moved function that calls a
*patched* sibling MUST reference it via ``ea.<name>`` (live attribute lookup on
the execution_agent module) — never bind it locally. execution_agent re-exports
every symbol moved here via ``from orders import (...)``. This keeps all patches
live by construction. ``datetime`` is referenced as ``ea.datetime`` because the
monitor/sell-path tests (test_prove_it_stop, golden_log, ...) freeze
``execution_agent.datetime`` and the deadline math in arm_exit/place_* must see it.
"""
import sys
import time
from zoneinfo import ZoneInfo
from supabase import Client
from ib_insync import IB, Stock, Order

import execution_agent as ea

def TrailingStopOrder(action: str, totalQuantity: float,
                     trailingPercent: float = None,
                     trailStopPrice: float = None, **kwargs) -> Order:
    """
    Factory for IBKR TRAIL order type.
    `ib_insync` 0.9.x does not export a TrailingStopOrder helper,
    but the underlying Order dataclass supports it via orderType='TRAIL'.
    """
    o = Order()
    o.action = action
    o.orderType = 'TRAIL'
    o.totalQuantity = totalQuantity
    if trailingPercent is not None:
        o.trailingPercent = trailingPercent
    if trailStopPrice is not None:
        o.trailStopPrice = trailStopPrice
    for k, v in kwargs.items():
        setattr(o, k, v)
    return o

def place_trailing_stop(ib: IB, contract, shares: int, stop_loss_pct: float) -> tuple:
    """
    Places a GTC Trailing Stop for an open stock position.
    Trails stop_loss_pct% below the running peak price.

    IBKR tracks the high-water mark internally (tick-by-tick) -- no HWM
    parameter is needed. Winners run freely until the stop fires or EOD
    plateau rotation acts.

    Returns (group_label, confirmed_trail_pct) where confirmed_trail_pct
    is the trailingPercent IBKR echoed back on the Trade object (as a
    decimal, e.g. 0.091 for 9.1%). Falls back to stop_loss_pct if the
    echo is unavailable.
    """
    import time as _time
    group = f"TS_{contract.symbol}_{int(_time.time())}"

    stop = TrailingStopOrder('SELL', shares,
                             trailingPercent=round(stop_loss_pct * 100, 2))
    stop.tif = 'GTC'
    stop.account = ea.get_ibkr_account(ib)
    trade = ib.placeOrder(contract, stop)

    # Read back the confirmed trailingPercent from the echoed Trade order.
    # IBKR populates trade.order.trailingPercent synchronously after placeOrder.
    try:
        confirmed_pct_raw = getattr(trade.order, 'trailingPercent', None)
        if confirmed_pct_raw and float(confirmed_pct_raw) > 0:
            confirmed_trail_pct = float(confirmed_pct_raw) / 100.0
        else:
            confirmed_trail_pct = stop_loss_pct  # fallback to calculated
    except Exception:
        confirmed_trail_pct = stop_loss_pct

    print(f"   \U0001f6e1\ufe0f  IBKR trailing stop placed: {confirmed_trail_pct*100:.2f}% trail (confirmed)")
    return group, confirmed_trail_pct




def place_protective_stops(ib: IB, contract, shares: int, trail_pct: float,
                           hard_price: float, account: str) -> tuple:
    """
    Places the two-leg protective bracket on an open position, in one OCA group:

        leg 1  TRAIL SELL trail_pct   -- profit-locking base trail (from HWM)
        leg 2  STP   SELL hard_price  -- static disconnect-proof max-loss floor

    ocaType=1 (CANCEL_WITH_BLOCK) makes them one-cancels-the-other: a fill on
    either leg cancels the sibling, so the same shares are never sold twice and
    a partial fill reduces both legs. Both are GTC and survive a gateway restart.

    Returns (oca_group, confirmed_trail_pct) mirroring place_trailing_stop, so
    callers can persist the trailing percent IBKR echoed back.
    """
    group = f"PROT_{contract.symbol}_{int(time.time())}"

    trail = TrailingStopOrder('SELL', shares,
                              trailingPercent=round(trail_pct * 100, 2))
    trail.tif      = 'GTC'
    trail.account  = account
    trail.ocaGroup = group
    trail.ocaType  = 1
    trail.transmit = True
    trail_trade = ib.placeOrder(contract, trail)

    hard = Order()
    hard.action        = 'SELL'
    hard.orderType     = 'STP'
    hard.totalQuantity = shares
    hard.auxPrice      = round(float(hard_price), 2)
    hard.tif           = 'GTC'
    hard.account       = account
    hard.ocaGroup      = group
    hard.ocaType       = 1
    hard.transmit      = True
    ib.placeOrder(contract, hard)

    try:
        confirmed_pct_raw = getattr(trail_trade.order, 'trailingPercent', None)
        confirmed_trail_pct = (float(confirmed_pct_raw) / 100.0
                               if confirmed_pct_raw and float(confirmed_pct_raw) > 0
                               else trail_pct)
    except Exception:
        confirmed_trail_pct = trail_pct

    ib.sleep(1)
    print(f"   \U0001f6e1\ufe0f  Protective bracket placed: {confirmed_trail_pct*100:.2f}% trail "
          f"+ static hard stop @ ${round(float(hard_price), 2):.2f} (OCA)")
    return group, confirmed_trail_pct


def arm_exit(ib: IB, client: Client, ticker: str, shares: int, current_price: float,
             reason: str, now_ny: ea.datetime.datetime) -> None:
    """
    Arms a Day 0-6 loss-cutting exit instead of selling immediately at the
    trigger price (often a local trough).

    Replaces any existing sell order with a tight ARMED_EXIT_TRAIL_PCT IBKR
    native trailing stop, which tracks the price tick-by-tick and rides any
    bounce toward the best price reached since arming. A hard deadline
    (ARMED_EXIT_DEADLINE_HOURS, checked every monitoring cycle in
    monitor_portfolio_intraday) forces a market sell if the trail hasn't
    already fired — this bounds the extra hold time so we never wait
    indefinitely chasing a better exit.
    """
    try:
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        ea.cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)
        ea.place_trailing_stop(ib, contract, shares, ea.ARMED_EXIT_TRAIL_PCT)

        try:
            client.table("portfolio_positions").update({
                "exit_armed":        True,
                "exit_armed_at":     now_ny.isoformat(),
                "exit_armed_reason": reason,
                "exit_armed_price":  round(float(current_price), 4),
            }).eq("ticker", ticker).execute()
        except Exception as db_err:
            # PGRST204 = column missing in schema cache (migration not yet run).
            # The tight IBKR trailing stop is already placed and will still
            # protect the position; only the deadline bookkeeping is degraded
            # until migrations/20260803_add_armed_exit_columns.sql is applied.
            if "PGRST204" in str(db_err) or "exit_armed" in str(db_err):
                print(f"   ⚠️ {ticker}: exit_armed columns missing — run migrations/20260803_add_armed_exit_columns.sql. "
                      f"Trailing stop placed but deadline won't be tracked.")
            else:
                raise

        msg = (
            f"\U0001f3af <b>{ticker}</b> exit armed (Day 0-6 loss-cutting): {reason}\n"
            f"Tight trail: {ea.ARMED_EXIT_TRAIL_PCT*100:.2f}% from price at arm-time (${current_price:.2f})\n"
            f"Deadline: forced sell in {ea.ARMED_EXIT_DEADLINE_HOURS:.2f}h if not already stopped out."
        )
        ea.notifier._send(msg)
        print(f"   \U0001f3af {ticker}: exit armed — {reason} "
              f"(tight {ea.ARMED_EXIT_TRAIL_PCT*100:.2f}% trail, deadline {ea.ARMED_EXIT_DEADLINE_HOURS:.2f}h)")
    except Exception as arm_err:
        ea.notifier.notify_exception("arm_exit() — execution_agent.py", arm_err)
        print(f"   ⚠️ {ticker}: failed to arm exit: {arm_err}")








def place_oca_exit(ib: IB, contract, shares: int, limit_price: float | None,
                   trail_pct: float, account: str) -> tuple[str, list]:
    """
    Places the OCA exit pair on an open position:

        upper leg  LMT  SELL @ limit_price   -- the optimistic recovery target
        lower leg  TRAIL SELL trail_pct      -- ratchets up behind any bounce

    The lower leg is deliberately a TRAIL and not a static STP. With a static
    stop, a position that rallies most of the way to the limit and then fades
    still exits at the original stop, surrendering the entire move. A trail
    follows the advance and banks whatever the bounce actually delivered, which
    is the only reason waiting for the upper leg is worth the risk at all.

    ocaType=1 (CANCEL_WITH_BLOCK) is required, not cosmetic: in a cash account
    two SELL orders for the same shares are otherwise liable to be rejected as
    exceeding the position, and a partial fill on one leg must reduce the other
    rather than leave a naked short.

    Returns (oca_group, [trades]).
    """
    group = f"OCA_{contract.symbol}_{int(time.time())}"
    trades = []

    if limit_price and limit_price > 0:
        lmt = Order()
        lmt.action        = 'SELL'
        lmt.orderType     = 'LMT'
        lmt.totalQuantity = shares
        lmt.lmtPrice      = round(float(limit_price), 2)
        lmt.tif           = 'GTC'
        lmt.account       = account
        lmt.ocaGroup      = group
        lmt.ocaType       = 1
        lmt.transmit      = True
        trades.append(ib.placeOrder(contract, lmt))

    trail = TrailingStopOrder('SELL', shares,
                              trailingPercent=round(trail_pct * 100, 2))
    trail.tif      = 'GTC'
    trail.account  = account
    trail.ocaGroup = group
    trail.ocaType  = 1
    trail.transmit = True
    trades.append(ib.placeOrder(contract, trail))

    ib.sleep(1)
    return group, trades


def enqueue_smart_exit(client: Client, ticker: str, reason: str,
                       requested_by: str = "auto") -> bool:
    """
    Route an automated sell rule through the Smart OCA Exit queue.

    Rather than calling place_oca_exit() from each rule, the rule writes the
    same row a human would write with request_exit.py. One placement path, one
    audit trail, one set of backstops — and every rule automatically inherits
    ATR_AUTO sizing, the hard floor and the expiry.

    Only non-urgent Day 7+ rules should call this. Loss-cutting rules must not:
    an OCA suspends the automated ladder for up to `expires_after_days`, which
    is the wrong trade for a position that is actively failing.
    See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.

    Returns True when the exit is owned by the queue (freshly enqueued, or a
    request was already in flight) and the caller must NOT also market-sell.
    Returns False when enqueueing failed, in which case the caller must fall
    back to execute_sell() — never leave a triggered sell rule unexecuted.
    """
    try:
        client.table("exit_requests").insert({
            "ticker":       ticker.upper(),
            "limit_mode":   "ATR_AUTO",
            "stop_mode":    "ATR_AUTO",
            "note":         reason[:500],
            "requested_by": requested_by,
        }).execute()
        print(f"   🎯 {ticker}: queued Smart OCA exit ({requested_by}) — {reason}")
        return True
    except Exception as e:
        msg = str(e)
        # Unique partial index idx_exit_requests_one_active: a request is
        # already in flight for this ticker. That request owns the exit, so
        # this is success, not failure — the rule must stand down either way.
        if "23505" in msg or "idx_exit_requests_one_active" in msg or "duplicate key" in msg.lower():
            print(f"   🎯 {ticker}: exit request already in flight — leaving it to the queue.")
            return True
        if "42P01" in msg or "PGRST205" in msg:
            print(f"   ⚠️ exit_requests table missing — run migrations/20260818_add_exit_requests.sql. "
                  f"Falling back to a market sell for {ticker}.")
            return False
        ea.notifier.notify_exception(f"enqueue_smart_exit({ticker}) — execution_agent.py", e)
        return False


def process_exit_requests(ib: IB) -> None:
    """
    Drains the `exit_requests` queue — the Smart OCA Managed Exit.

    Runs on every monitoring cycle rather than only at the open, because an
    exit decision made at 11:00 should not wait until the next session. The
    settle window (OCA_EXIT_SETTLE_MINUTE) only defers requests that arrived
    before the market opened; anything queued intraday is placed at once.

    Two states are handled:
      PENDING -> cancel the existing GTC trail, place the OCA pair, mark PLACED.
      PLACED  -> detect a fill, or enforce the software backstops (hard floor
                 and expiry). An OCA left alone can sit unfilled indefinitely
                 while the position bleeds, so both bounds are mandatory.
    """
    if not ea.OCA_EXIT_ENABLED:
        return

    client = ea.get_supabase_client()
    try:
        rows = (client.table("exit_requests")
                .select("*")
                .in_("status", ["PENDING", "PLACED"])
                .execute().data) or []
    except Exception as e:
        # Table absent (migration not yet applied) must not take down the loop —
        # every other risk rule still needs to run this cycle.
        msg = str(e)
        if "42P01" in msg or "exit_requests" in msg or "PGRST205" in msg:
            print("   ⚠️ exit_requests table missing — run migrations/20260818_add_exit_requests.sql")
        else:
            ea.notifier.notify_exception("process_exit_requests() — execution_agent.py", e)
        return

    if not rows:
        return

    tz = ZoneInfo("America/New_York")
    now_ny = ea.datetime.datetime.now(tz)
    print(f"🎯 Smart OCA Exit queue: {len(rows)} request(s) in flight")

    try:
        holdings = {p["ticker"].upper(): p for p in
                    (client.table("portfolio_positions").select("*").execute().data or [])}
    except Exception as e:
        ea.notifier.notify_exception("process_exit_requests() — execution_agent.py", e)
        return

    account = ea.get_ibkr_account(ib)

    for req in rows:
        ticker = (req.get("ticker") or "").upper()
        rid    = req.get("id")
        try:
            pos = holdings.get(ticker)
            if not pos:
                # Already gone — either a leg filled and reconcile_with_ibkr()
                # archived it, or it was sold by another path.
                _close_exit_request(client, rid, "FILLED" if req["status"] == "PLACED" else "CANCELLED",
                                    outcome="LIMIT_OR_TRAIL" if req["status"] == "PLACED" else None,
                                    note="position no longer held")
                print(f"   ✅ {ticker}: position closed — exit request #{rid} resolved.")
                continue

            shares = int(pos["shares"])
            current_price, _ = ea.get_position_price(ib, ticker)
            if current_price <= 0:
                print(f"   ⚠️ {ticker}: no price this cycle — deferring exit request #{rid}.")
                continue

            # ── PENDING: place the OCA ──────────────────────────────────────
            if req["status"] == "PENDING":
                # MARKET mode is not an OCA at all — it is a force sell routed
                # through the queue so it does not require stopping the agent.
                # "Get me out now" and "get me out well" are different
                # intents; conflating them would make every urgent exit wait
                # on a bounce that may never come.
                if (req.get("stop_mode") or "").upper() == "MARKET":
                    reason = req.get("note") or "Queued market exit (request_exit.py --now)"
                    reason = f"Smart Exit Queue — {reason}"
                    print(f"🚨 {ticker}: queued MARKET exit firing — {reason}")
                    ea.cancel_ticker_sell_orders(ib, ticker)
                    ib.sleep(1)
                    buy_price  = float(pos["buy_price"])
                    buy_reason = pos.get("buy_reason", "Unknown")
                    try:
                        buy_date = ea.datetime.datetime.fromisoformat(
                            pos["buy_date"].replace('Z', '+00:00'))
                    except Exception:
                        buy_date = now_ny
                    ok = ea.execute_sell(ib, client, ticker, shares, buy_price, buy_date,
                                      buy_reason, current_price, reason, pos_row=pos)
                    if ok:
                        _close_exit_request(client, rid, "FILLED", outcome="MARKET",
                                            filled_price=current_price,
                                            note="queued market exit")
                    else:
                        # execute_sell() only returns False when it could not
                        # confirm the position left IBKR. Leave PENDING so the
                        # next cycle retries rather than orphaning the position.
                        print(f"   ⚠️ {ticker}: market exit not confirmed — retrying next cycle.")
                    continue

                if now_ny.hour == 9 and now_ny.minute < ea.OCA_EXIT_SETTLE_MINUTE:
                    print(f"   ⏳ {ticker}: waiting for the tape to settle "
                          f"(placing from 09:{ea.OCA_EXIT_SETTLE_MINUTE:02d} ET).")
                    continue

                trail_pct, trail_note = ea.resolve_oca_trail_pct(
                    pos, (req.get("stop_mode") or "ATR_AUTO").upper(), req.get("stop_value"))
                limit_price = ea.resolve_oca_limit_price(
                    pos, req.get("limit_mode"), req.get("limit_value"), current_price,
                    req.get("limit_cap"))

                if limit_price and limit_price <= current_price:
                    # The target is already met. Keep the leg: a SELL limit can
                    # never fill BELOW its limit price, so a marketable one fills
                    # at the better prevailing bid (limit 489.89 into a 495 market
                    # fills near 495). Dropping it here would decline the exact
                    # price the request asked for, and leave the position riding
                    # on the trail alone after its goal had already been reached.
                    # It is also safer than a market order: if the bid collapses
                    # before the fill, the order rests at the limit instead of
                    # chasing the drop down.
                    print(f"   ⚡ {ticker}: limit ${limit_price:.2f} is already marketable "
                          f"vs ${current_price:.2f} — target met, expect an immediate fill "
                          f"at or above the limit.")

                contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(contract)
                # The existing GTC trailing stop MUST go first. Left in place it
                # is a third competing SELL outside the OCA group, so a fill on
                # it would not cancel the OCA legs.
                ea.cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)

                group, _ = ea.place_oca_exit(ib, contract, shares, limit_price, trail_pct, account)

                floor_pct = float(req.get("hard_floor_pct") or ea.OCA_EXIT_DEFAULT_FLOOR_PCT * 100) / 100.0
                stop_ref  = current_price * (1 - trail_pct)

                client.table("exit_requests").update({
                    "status": "PLACED",
                    "oca_group": group,
                    "placed_at": now_ny.isoformat(),
                    "placed_price": round(current_price, 4),
                    "placed_limit_price": round(limit_price, 2) if limit_price else None,
                    "placed_stop_price": round(stop_ref, 2),
                    "placed_trail_pct": round(trail_pct * 100, 4),
                    "updated_at": now_ny.isoformat(),
                }).eq("id", rid).execute()

                entry = float(pos.get("buy_price") or 0)
                lim_txt = (f"${limit_price:.2f} ({(limit_price/entry-1)*100:+.2f}% vs entry)"
                           if limit_price else "none (trail only)")
                print(f"   🎯 {ticker}: OCA placed — limit {lim_txt}, "
                      f"trail {trail_pct*100:.2f}% ({trail_note}), floor {floor_pct*100:.2f}%")
                ea.notifier._send(
                    f"🎯 <b>{ticker}</b> Smart OCA exit placed\n"
                    f"Limit: {lim_txt}\n"
                    f"Trail: {trail_pct*100:.2f}% (anchor ~${stop_ref:.2f})\n"
                    f"Floor: ${current_price*(1-floor_pct):.2f} · expires in "
                    f"{int(req.get('expires_after_days') or ea.OCA_EXIT_DEFAULT_EXPIRY_DAYS)}d"
                )
                continue

            # ── PLACED: enforce the software backstops ──────────────────────
            placed_price = float(req.get("placed_price") or current_price)
            floor_pct = float(req.get("hard_floor_pct") or ea.OCA_EXIT_DEFAULT_FLOOR_PCT * 100) / 100.0
            floor_level = placed_price * (1 - floor_pct)

            try:
                placed_at = ea.datetime.datetime.fromisoformat(
                    str(req["placed_at"]).replace("Z", "+00:00")).astimezone(tz)
            except Exception:
                placed_at = now_ny
            expiry_days = int(req.get("expires_after_days") or ea.OCA_EXIT_DEFAULT_EXPIRY_DAYS)
            days_open = ea.trading_days_between(placed_at.date(), now_ny.date()) - 1

            buy_price  = float(pos["buy_price"])
            buy_reason = pos.get("buy_reason", "Unknown")
            try:
                buy_date = ea.datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
            except Exception:
                buy_date = now_ny

            breach = current_price <= floor_level
            expired = days_open >= expiry_days

            if breach or expired:
                why = (f"hard floor ${floor_level:.2f} breached" if breach
                       else f"expired after {days_open} trading day(s)")
                reason = (f"Smart OCA Exit — {why}; closing at market "
                          f"(limit ${req.get('placed_limit_price') or 0:.2f} never filled)")
                print(f"🚨 {ticker}: Smart OCA backstop firing — {why}")
                ea.cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                ok = ea.execute_sell(ib, client, ticker, shares, buy_price, buy_date,
                                  buy_reason, current_price, reason, pos_row=pos)
                if ok:
                    _close_exit_request(client, rid, "FILLED",
                                        outcome="FLOOR" if breach else "EXPIRY",
                                        filled_price=current_price, note=why)
                else:
                    print(f"   ⚠️ {ticker}: backstop sell not confirmed — retrying next cycle.")
                continue

            lim = req.get("placed_limit_price")
            lim_txt = f"limit ${float(lim):.2f}" if lim else "trail only"
            print(f"   🎯 {ticker}: OCA live — ${current_price:.2f} | {lim_txt} | "
                  f"floor ${floor_level:.2f} | day {days_open}/{expiry_days}")

        except Exception as req_err:
            ea.notifier.notify_exception(f"process_exit_requests() {ticker} — execution_agent.py", req_err)
            try:
                client.table("exit_requests").update({
                    "last_error": str(req_err)[:500],
                    "updated_at": now_ny.isoformat(),
                }).eq("id", rid).execute()
            except Exception:
                pass
            print(f"   ⚠️ {ticker}: exit request #{rid} failed this cycle: {req_err}")


def _close_exit_request(client: Client, rid, status: str, outcome: str | None = None,
                        filled_price: float | None = None, note: str | None = None) -> None:
    """Terminal-state writer for an exit_requests row."""
    payload = {"status": status, "updated_at": ea.datetime.datetime.now(
        ZoneInfo("America/New_York")).isoformat()}
    if outcome:
        payload["outcome"] = outcome
    if filled_price:
        payload["filled_price"] = round(float(filled_price), 4)
        payload["filled_at"] = payload["updated_at"]
    if note:
        payload["note"] = note
    try:
        client.table("exit_requests").update(payload).eq("id", rid).execute()
    except Exception as e:
        print(f"   ⚠️ Could not close exit request #{rid}: {e}")


def get_oca_managed_tickers(client: Client) -> set:
    """
    Tickers whose exit is currently owned by a Smart OCA request.

    The automated ladder (the Prove-It Stop and Rank & Replace) must not act on
    these. Those rules call execute_sell()/arm_exit(),
    both of which cancel every open SELL order for the ticker — which would
    silently destroy the OCA the user explicitly asked for. The OCA plus its
    floor and expiry backstops fully govern the position instead.
    """
    if not ea.OCA_EXIT_ENABLED:
        return set()
    try:
        rows = (client.table("exit_requests")
                .select("ticker,status")
                .eq("status", "PLACED")
                .execute().data) or []
        # Re-assert the predicate in Python. The server-side .eq() already
        # filters, but this makes the function total: any row that is not
        # unambiguously a PLACED exit_requests row can never suspend the
        # automated exit ladder. Wrongly suspending it would leave a position
        # with no stop at all.
        return {
            str(r["ticker"]).upper() for r in rows
            if isinstance(r, dict) and r.get("status") == "PLACED" and r.get("ticker")
        }
    except Exception:
        return set()














def maybe_arm_power_hold(client: Client, pos: dict, calendar_days: int) -> bool:
    """
    Persists the power-hold flag the first time a position qualifies.

    Returns True if the position is (now) power-held. Degrades gracefully if the
    column has not been migrated yet — the in-memory evaluation still applies.
    """
    if not ea.POWER_HOLD_ENABLED or pos.get("power_hold"):
        return bool(pos.get("power_hold")) and calendar_days <= ea.POWER_HOLD_DURATION_DAYS

    peak_gain = float(pos.get("highest_unrealized_pct") or 0.0)
    qualifies = (
        peak_gain >= ea.POWER_HOLD_GAIN_PCT
        and calendar_days <= ea.POWER_HOLD_TRIGGER_DAYS
    )
    if not qualifies:
        return False

    ticker = pos.get("ticker")
    pos["power_hold"] = True
    try:
        client.table("portfolio_positions").update({"power_hold": True}).eq("ticker", ticker).execute()
    except Exception as e:
        # PGRST204 = column missing (migration not yet run). Not a bug — the rule
        # still applies in-memory for this cycle.
        print(f"   ⚠️ {ticker}: could not persist power_hold flag ({e}). Rule still applied this cycle.")

    print(f"   🏆 {ticker}: 8-WEEK HOLD ARMED — +{peak_gain:.1f}% within {calendar_days}d. "
          f"Discretionary exits suppressed until day {ea.POWER_HOLD_DURATION_DAYS}.")
    try:
        ea.notifier._send(
            f"🏆 <b>{ticker}</b> qualified for the O'Neil 8-week hold rule\n"
            f"Peak gain +{peak_gain:.1f}% within {calendar_days} days of entry.\n"
            f"Discretionary exits suppressed until day {ea.POWER_HOLD_DURATION_DAYS}; "
            f"trailing stop widened to {ea.POWER_HOLD_TRAIL_PCT*100:.0f}% as the disaster backstop."
        )
    except Exception:
        pass
    return True


# ── Sell-state regime transitions (concise Telegram on change) ────────────────
# Every cycle a position sits under exactly one GOVERNING exit regime. When that
# regime changes we send one concise Telegram. State is latched in
# portfolio_positions.sell_state, so it fires exactly once per transition and
# survives restarts. See decisions/2026-09-08_sell-state-transitions.md.
SELL_STATE_LABELS = {
    "UNPROVEN":      "Unproven",
    "PROVEN":        "Proven",
    "PROVEN_FLOOR":  "Proven — floor armed",
    "PROFIT_LOCKED": "Profit-locked",
    "POWER_HOLD":    "Power Hold",
    "EXITING":       "Exiting",
}
# Regimes that already have their own richer dedicated Telegram (the Prove-It
# arm message and the power-hold arm message). We still LATCH these so entering
# and leaving them is tracked coherently, but we do not re-announce them here.
SELL_STATE_SUPPRESS_NOTIFY = {"EXITING", "POWER_HOLD"}




def maybe_notify_sell_state(client: Client, pos: dict, ticker: str,
                            new_code: str | None, *, prove_it_level: float | None,
                            unrealized_pct: float, peak_pct: float,
                            days_held: int) -> None:
    """Latch the governing regime and Telegram a concise note on any change.

    The first observation of a position (no prior sell_state) is recorded
    SILENTLY — it is an initial state, not a transition, and the buy itself was
    already announced. Transitions INTO a regime that has its own dedicated
    message (SELL_STATE_SUPPRESS_NOTIFY) are latched but not re-announced.
    """
    if new_code is None:
        return
    prev = pos.get("sell_state")
    if prev == new_code:
        return

    # Latch FIRST so a notify failure can never cause a re-fire next cycle.
    try:
        client.table("portfolio_positions").update(
            {"sell_state": new_code}).eq("ticker", ticker).execute()
    except Exception as e:
        # PGRST204 = column missing (migration not yet run). Without the latch we
        # cannot detect a change without spamming every cycle, so do NOT notify.
        if "PGRST204" in str(e) or "sell_state" in str(e):
            print(f"   ⚠️ {ticker}: sell_state column missing — run "
                  f"migrations/20260908_add_sell_state_column.sql. Transition notices disabled.")
            return
        raise
    pos["sell_state"] = new_code

    if prev is None:                              # initial state, not a transition
        return
    if new_code in SELL_STATE_SUPPRESS_NOTIFY:    # has its own dedicated message
        return
    ea.notifier.notify_sell_state_change(
        ticker,
        SELL_STATE_LABELS.get(prev, prev),
        SELL_STATE_LABELS.get(new_code, new_code),
        new_code,
        prove_it_level=prove_it_level,
        unrealized_pct=unrealized_pct,
        peak_pct=peak_pct,
        days_held=days_held,
    )


def cancel_ticker_sell_orders(ib: IB, ticker: str) -> int:
    """Cancels all active GTC SELL orders for *ticker* (OCA cleanup before explicit sells)."""
    cancelled = 0
    for trade in ib.openTrades():
        if (trade.contract.symbol == ticker
                and trade.order.action == 'SELL'
                and trade.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')):
            try:
                ib.cancelOrder(trade.order)
                cancelled += 1
            except Exception:
                pass
    if cancelled:
        print(f"   🗑️  Cancelled {cancelled} open SELL order(s) for {ticker}")
    return cancelled


def handle_mock_sell(ticker: str, price: float, reason: str):
    """Executes a mock sale event directly on Supabase, bypassing IBKR."""
    print(f"🧪 Initiating mock sale for {ticker} at price ${price:.2f} (Reason: {reason})...")
    client = ea.get_supabase_client()
    
    # Fetch existing position
    res = client.table("portfolio_positions").select("*").eq("ticker", ticker.upper()).execute()
    if not res.data:
        print(f"❌ No active position found in Supabase for {ticker.upper()}")
        sys.exit(1)
        
    pos = res.data[0]
    shares = int(pos["shares"])
    buy_price = float(pos["buy_price"])
    buy_date = pos["buy_date"]
    buy_reason = pos.get("buy_reason", "Unknown")
    
    # Calculate returns
    sell_price = price
    profit_loss = round((sell_price - buy_price) * shares, 2)
    percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)
    
    # Insert into trade history
    trade_log = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": buy_price,
        "buy_date": buy_date,
        "buy_reason": buy_reason,
        "sell_price": sell_price,
        "sell_reason": reason,
        "profit_loss": profit_loss,
        "percent_return": percent_return
    }
    
    try:
        # Delete from portfolio
        client.table("portfolio_positions").delete().eq("ticker", ticker.upper()).execute()
        # Insert into history
        ea.insert_trade_history(client, trade_log)
        print(f"✅ Mock sale complete! Ticker {ticker} removed and logged to trade_history.")
        print(f"   Return: {percent_return}% | PnL: ${profit_loss:.2f}")
    except Exception as e:
        ea.notifier.notify_exception(f"handle_mock_sell() — execution_agent.py", e)
        print(f"❌ Database error during mock sale execution: {e}")
        sys.exit(1)
