"""Complete, account-scoped broker inventory for safety decisions."""
import math
import time


class BrokerPositionError(RuntimeError):
    """Broker inventory cannot safely authorize an order."""


def confirmed_stock_positions(ib, account):
    """Request a completed position snapshot, retaining zero and short quantities."""
    if not isinstance(account, str) or not account.strip():
        raise BrokerPositionError("An explicit broker account is required.")
    if ib.isConnected() is not True:
        raise BrokerPositionError("Broker disconnected; position inventory unavailable.")
    if account not in ib.managedAccounts():
        raise BrokerPositionError("Selected account is not managed by this broker connection.")
    previous_timeout = ib.RequestTimeout
    try:
        ib.RequestTimeout = 10
        rows = ib.reqPositions()
    except Exception as exc:
        raise BrokerPositionError(
            f"Broker position snapshot failed ({type(exc).__name__})."
        ) from exc
    finally:
        ib.RequestTimeout = previous_timeout
    if not isinstance(rows, (list, tuple)):
        raise BrokerPositionError("Broker did not return a completed position snapshot.")
    if ib.isConnected() is not True:
        raise BrokerPositionError("Broker disconnected while reading positions.")
    latest = {}
    symbols = {}
    for row in rows:
        row_account = getattr(row, "account", None)
        if not isinstance(row_account, str) or not row_account:
            raise BrokerPositionError("Broker position is missing its account.")
        if row_account != account:
            continue
        contract = getattr(row, "contract", None)
        security_type = getattr(contract, "secType", None)
        if not isinstance(security_type, str) or not security_type:
            raise BrokerPositionError("Broker position has no valid security type.")
        if security_type != "STK":
            continue
        symbol = getattr(contract, "symbol", None)
        contract_id = getattr(contract, "conId", None)
        quantity = getattr(row, "position", None)
        if (not isinstance(symbol, str) or not symbol
                or isinstance(contract_id, bool) or not isinstance(contract_id, int)
                or contract_id <= 0
                or isinstance(quantity, bool)
                or not isinstance(quantity, (int, float))
                or not math.isfinite(quantity)):
            raise BrokerPositionError("Broker stock position has invalid symbol or quantity.")
        if symbol in symbols and symbols[symbol] != contract_id:
            raise BrokerPositionError(f"{symbol}: ambiguous broker stock contracts.")
        if contract_id in latest and latest[contract_id].contract.symbol != symbol:
            raise BrokerPositionError("Broker contract changed symbol within a snapshot.")
        symbols[symbol] = contract_id
        # positionEnd completes a stream, not necessarily one row per contract.
        # A fill during the request can append a zero/short after an earlier long.
        latest[contract_id] = row
    return list(latest.values())


def require_no_short_positions(ib, account):
    positions = confirmed_stock_positions(ib, account)
    shorts = [p for p in positions if p.position < 0]
    if shorts:
        detail = ", ".join(f"{p.contract.symbol} {p.position:g}" for p in shorts)
        raise BrokerPositionError(
            f"Unexpected short position(s): {detail}. New orders are blocked; "
            "review the account in IBKR. No automatic buy-to-cover will be placed."
        )
    return positions


def confirmed_long_quantity(ib, account, ticker):
    """Return a positive whole-share holding, never a stale ledger quantity."""
    rows = [p for p in require_no_short_positions(ib, account)
            if p.contract.symbol == ticker]
    if len(rows) != 1 or rows[0].position <= 0:
        raise BrokerPositionError(f"{ticker}: no unambiguous broker-confirmed long position.")
    quantity = rows[0].position
    if not float(quantity).is_integer():
        raise BrokerPositionError(f"{ticker}: fractional holding requires manual review.")
    return int(quantity)


def _active_sells(ib, account, ticker):
    """Refresh all clients' orders, retaining local submissions not yet echoed."""
    if ib.isConnected() is not True:
        raise BrokerPositionError("Broker disconnected; open orders unavailable.")
    previous_timeout = ib.RequestTimeout
    try:
        ib.RequestTimeout = 10
        trades = ib.reqAllOpenOrders()
    except Exception as exc:
        raise BrokerPositionError(
            f"{ticker}: open-order snapshot failed ({type(exc).__name__})."
        ) from exc
    finally:
        ib.RequestTimeout = previous_timeout
    if not isinstance(trades, (list, tuple)) or ib.isConnected() is not True:
        raise BrokerPositionError(f"{ticker}: incomplete open-order snapshot.")
    active = []
    seen = set()
    local_pending = [t for t in ib.openTrades()
                     if t.order.clientId == ib.client.clientId]
    for trade, broker_reported in (
        [(t, True) for t in trades] + [(t, False) for t in local_pending]
    ):
        if id(trade) in seen:
            continue
        seen.add(id(trade))
        if (trade.contract.symbol != ticker or trade.contract.secType != "STK"
                or trade.order.action != "SELL"):
            continue
        order_account = trade.order.account
        if not isinstance(order_account, str) or not order_account:
            raise BrokerPositionError(f"{ticker}: sell order has no explicit account.")
        if order_account != account:
            continue
        # Inactive can be a held/rejected order, not a cancellation acknowledgement.
        # cancelOrder() can synthesize Cancelled locally for Inactive/staged
        # orders. A fresh broker response still listing the order wins over it.
        if broker_reported or trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled"):
            active.append(trade)
    return active


def cancel_confirmed_sells(ib, account, ticker):
    """Cancel this client's sells and require terminal acknowledgements."""
    trades = _active_sells(ib, account, ticker)
    client_id = ib.client.clientId
    if any(t.order.clientId != client_id for t in trades):
        raise BrokerPositionError(
            f"{ticker}: another API client owns a sell order; replacement blocked."
        )
    for trade in trades:
        try:
            ib.cancelOrder(trade.order)
        except Exception as exc:
            raise BrokerPositionError(
                f"{ticker}: cancellation failed ({type(exc).__name__}); replacement blocked."
            ) from exc
    deadline = time.monotonic() + 10
    while any(t.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled")
              for t in trades):
        if ib.isConnected() is not True or time.monotonic() >= deadline:
            raise BrokerPositionError(
                f"{ticker}: sell cancellation not confirmed; replacement blocked."
            )
        ib.sleep(0.1)
    if _active_sells(ib, account, ticker):
        raise BrokerPositionError(f"{ticker}: live SELL orders remain; replacement blocked.")
    return len(trades)


def submit_sell_orders(ib, contract, orders, account):
    """Authorize one sell or a staged OCA pair against fresh broker inventory."""
    if contract.secType != "STK":
        raise BrokerPositionError("Stock sell authorization requires a stock contract.")
    if (isinstance(contract.conId, bool) or not isinstance(contract.conId, int)
            or contract.conId <= 0):
        raise BrokerPositionError("Sell contract must be qualified by the broker.")
    if not orders:
        raise BrokerPositionError("No sell orders supplied.")
    quantities = [o.totalQuantity for o in orders]
    if any(isinstance(q, bool) or not isinstance(q, (int, float))
           or not math.isfinite(q) or q <= 0 or not float(q).is_integer()
           for q in quantities):
        raise BrokerPositionError(f"{contract.symbol}: invalid sell quantity.")
    if any(o.action != "SELL" or o.account != account for o in orders):
        raise BrokerPositionError("Sell orders must name the selected account.")
    if len(orders) > 1 and (
        not orders[0].ocaGroup
        or any(o.ocaGroup != orders[0].ocaGroup or o.ocaType != 1
               or o.totalQuantity != quantities[0] for o in orders)
    ):
        raise BrokerPositionError("Multiple sells require one equal-size blocking OCA group.")
    if _active_sells(ib, account, contract.symbol):
        raise BrokerPositionError(f"{contract.symbol}: existing SELL orders block submission.")
    rows = [p for p in require_no_short_positions(ib, account)
            if p.contract.conId == contract.conId and p.contract.symbol == contract.symbol]
    if len(rows) != 1 or rows[0].position <= 0:
        raise BrokerPositionError(f"{contract.symbol}: no broker-confirmed long position in this contract.")
    quantity = rows[0].position
    if not float(quantity).is_integer():
        raise BrokerPositionError(f"{contract.symbol}: fractional holding requires manual review.")
    if max(quantities) > quantity:
        raise BrokerPositionError(
            f"{contract.symbol}: requested {max(quantities):g} shares, "
            f"but broker holds only {quantity}; sell blocked."
        )
    trades = []
    try:
        for index, order in enumerate(orders):
            # Stage every sibling before the final transmission. A marketable
            # first leg must not fill before its cancelling sibling exists.
            order.transmit = index == len(orders) - 1
            trades.append(ib.placeOrder(contract, order))
    except Exception:
        # Never leave a partially staged group to be transmitted by a later call.
        cancel_confirmed_sells(ib, account, contract.symbol)
        raise
    return trades
