"""Sell execution: full-exit and partial scale-out, extracted from
execution_agent.py (see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: money path. Patched siblings and the frozen
``execution_agent.datetime`` clock are referenced via ``ea.<name>`` so mock.patch
on execution_agent and the golden exit-path harness stay live.
"""
from zoneinfo import ZoneInfo
from supabase import Client
from ib_insync import IB, Stock, MarketOrder

import execution_agent as ea

def execute_sell(ib: IB, client: Client, ticker: str, shares: int, buy_price: float,
                 buy_date, buy_reason: str, current_price: float, reason: str,
                 pos_row: dict | None = None,
                 market_regime: str = "neutral") -> bool:
    """Executes a market sell order on IBKR and archives the transaction in Supabase.

    CRITICAL INVARIANT: Supabase position is ONLY deleted after confirming via
    ib.portfolio() that the position is truly gone from IBKR. This prevents phantom
    deletions when market orders are cancelled/rejected (e.g. paper trading no-data).

    pos_row: the portfolio_positions dict for this ticker (used to write breakout_learnings).
    market_regime: 'uptrend' | 'correction' | 'neutral' at time of sell.
    """
    try:
        # Cancel any open trailing stop SELL orders before placing
        # explicit sell (stale rotation) to avoid duplicate fills.
        ea.cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)

        # Place sell order
        contract = Stock(ticker, 'SMART', 'USD')
        ib.qualifyContracts(contract)
        order = MarketOrder('SELL', shares)
        order.account = ea.get_ibkr_account(ib)
        trade = ib.placeOrder(contract, order)
        
        print(f"   Placing market sell order for {shares} shares of {ticker}...")
        
        # Wait up to 60 seconds for fill
        for _ in range(30):
            ib.sleep(2)
            if trade.orderStatus.status == 'Filled':
                break

        # ── CRITICAL: verify fill via ib.positions() BEFORE touching Supabase ──
        # MarketOrders can be cancelled (e.g. paper-trading no live market data)
        # without raising a Python exception. We MUST confirm the position is
        # actually gone from IBKR before removing it from Supabase. Read
        # ib.positions() (target account only) rather than ib.portfolio(): the
        # latter is empty for multi-account logins, which would wrongly read as
        # "position gone" and delete a Supabase row after a REJECTED sell.
        held_after = ea.ibkr_target_positions(ib)
        if ticker in held_after:
            print(f"   ⚠️  SELL NOT CONFIRMED: {ticker} still in IBKR portfolio after sell attempt.")
            print(f"       Order status: {trade.orderStatus.status}. Cancelling order — Supabase record PRESERVED.")
            try:
                ib.cancelOrder(trade.order)
            except Exception:
                pass
            return False  # ← EXIT WITHOUT DELETING FROM SUPABASE

        # Sell confirmed — position is gone from IBKR
        fill_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0
        if fill_price <= 0:
            fill_price = current_price
            
        profit_loss = round((fill_price - buy_price) * shares, 2)
        percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
        
        # Log to trade history
        trade_log = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": buy_price,
            "buy_date": buy_date.isoformat(),
            "buy_reason": buy_reason,
            "sell_price": fill_price,
            "sell_reason": reason,
            "profit_loss": profit_loss,
            "percent_return": percent_return
        }
        
        # Database transaction — only reached after confirmed IBKR fill
        sell_commission = ea.trade_commission(ib, trade)
        buy_commission = pos_row.get("buy_commission") if isinstance(pos_row, dict) else None
        client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
        _th_resp = ea.insert_trade_history(client, trade_log)
        _th_id = ((_th_resp.data or [{}])[0] or {}).get("id")
        ea.record_trade_commissions(client, _th_id, buy_commission, sell_commission)

        # ── Write to breakout_learnings for future screener feedback ─────────────
        ea._write_breakout_learning_row(
            client=client,
            ticker=ticker,
            buy_date=buy_date,
            reason=reason,
            pos_row=pos_row,
            market_regime=market_regime,
            percent_return=percent_return,
        )

        print(f"✅ Closed Position: Sold {shares} shares of {ticker} at ${fill_price:.2f}.")
        print(f"   PnL: ${profit_loss} ({percent_return}%) | Reason: {reason}")
        ea.notifier.notify_sell(
            ticker=ticker, shares=shares, buy_price=buy_price,
            buy_date=buy_date.isoformat(), fill_price=fill_price, reason=reason
        )
        return True
        
    except Exception as e:
        print(f"❌ Error executing sell order for {ticker}: {e}")
        ea.notifier.notify_exception(f"execute_sell({ticker}) — execution_agent.py", e)
        return False


def execute_scale_out(ib: IB, client: Client, pos: dict, ticker: str,
                      total_shares: int, scale_shares: int, buy_price: float,
                      buy_date, buy_reason: str, current_price: float,
                      highest_unrealized_pct: float, trail_pct: float,
                      hard_price: float) -> bool:
    """
    Sells a FRACTION (scale_shares) of an open position at market to lock in part
    of a winner's gain, keeping the position OPEN on the remaining shares.

    This is the winner->loser give-back reducer: booking part of the gain is a
    realised profit a later fade cannot erase, while the remainder keeps riding
    the UNCHANGED Prove-It stop so the fat winners the book depends on are never
    clipped. Freed capital stays as reserve until a full slot opens, then
    redeploys via the normal equity-capped min(cash / remaining_slots,
    NetLiquidation / MAX_POSITIONS) sizing.

    Ordering is CANCEL-FIRST, matching execute_sell(): the full-size protective
    bracket is cancelled before the market sell so the resting trailing/hard legs
    can NEVER fire alongside our order and oversell into a short. The exposure is
    a few seconds of an unprotected LONG during the market fill — a far more
    benign failure mode than an accidental short, and only reachable by an
    implausible instant move (the bracket sat ~7% away). Protection is ALWAYS
    restored: a right-sized bracket on success, the original full bracket on any
    abort.

    Sold quantity and price come from trade.fills (our order's own executions),
    never from a portfolio delta, so a concurrent fill can never be mis-booked as
    part of the scale-out. The partial is booked in its own trade_history row now;
    reconcile_with_ibkr() uses scaled_out_at to exclude it from the final close's
    price and commission when the remainder is eventually sold.

    Returns True only when the partial sell is confirmed filled at IBKR.
    """
    contract = Stock(ticker, 'SMART', 'USD')
    account = ea.get_ibkr_account(ib)

    def _ibkr_qty() -> int | None:
        # positions() (target account) — ib.portfolio() is empty on multi-account
        # logins, which would fabricate a None qty and abort every scale-out.
        return ea.ibkr_target_positions(ib, account).get(ticker)

    def _restore_full_bracket(qty: int) -> None:
        """Re-place the original full-size bracket after an aborted scale-out."""
        try:
            ea.cancel_ticker_sell_orders(ib, ticker)
            ib.sleep(1)
            ea.place_protective_stops(ib, contract, qty, trail_pct, hard_price, account)
            print(f"   🛡️ {ticker}: protective bracket restored on {qty} shares after abort.")
        except Exception as _re:
            ea.notifier.notify_exception(f"execute_scale_out({ticker}) bracket restore", _re)
            print(f"   ⚠️ {ticker}: FAILED to restore bracket after abort: {_re}. "
                  f"Self-heal will re-place next cycle.")

    try:
        ib.qualifyContracts(contract)

        pre_qty = _ibkr_qty()
        if pre_qty is None or pre_qty <= 0:
            print(f"   ⚠️ {ticker}: not in IBKR portfolio — skipping scale-out.")
            return False
        # Never oversell: if IBKR holds fewer than we thought, resize down.
        if pre_qty < total_shares:
            scale_shares = int(pre_qty * ea.SCALE_OUT_FRACTION)
        if scale_shares < 1 or (pre_qty - scale_shares) < 1:
            print(f"   ℹ️ {ticker}: too few shares ({pre_qty}) to scale out — skipping.")
            return False

        # ── Cancel the full-size bracket FIRST so it cannot oversell ───────────
        ea.cancel_ticker_sell_orders(ib, ticker)
        ib.sleep(1)

        order = MarketOrder('SELL', scale_shares)
        order.account = account
        trade = ib.placeOrder(contract, order)
        print(f"   ✂️  Scale-out: selling {scale_shares}/{pre_qty} shares of {ticker} at market...")

        for _ in range(30):
            ib.sleep(2)
            if trade.orderStatus.status == 'Filled':
                break

        # Cancel any unfilled remainder so no late fill lands after we account,
        # then settle briefly for the terminal state.
        if trade.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive'):
            try:
                ib.cancelOrder(trade.order)
                ib.sleep(1)
            except Exception:
                pass

        # ── Sold quantity/price come from OUR order's fills only ───────────────
        filled = int(sum(f.execution.shares for f in getattr(trade, "fills", []) or []))
        if filled <= 0:
            print(f"   ⚠️  SCALE-OUT NOT FILLED: {ticker} (status {trade.orderStatus.status}). "
                  f"Restoring full bracket.")
            _restore_full_bracket(pre_qty)
            return False

        # Weighted-average fill price across our fills.
        _num = sum(f.execution.shares * f.execution.price
                   for f in trade.fills if f.execution.price > 0)
        _den = sum(f.execution.shares for f in trade.fills if f.execution.price > 0)
        fill_price = (_num / _den) if _den > 0 else (
            trade.orderStatus.avgFillPrice or current_price)
        if fill_price <= 0:
            fill_price = current_price

        scale_shares = filled
        remaining = pre_qty - filled

        # ── Protection FIRST: right-size the bracket for the remainder ─────────
        # Done before any DB write so a Supabase failure below can never leave a
        # reduced, unprotected position.
        if remaining > 0:
            try:
                ea.place_protective_stops(ib, contract, remaining, trail_pct, hard_price, account)
            except Exception as _pe:
                ea.notifier.notify_exception(f"execute_scale_out({ticker}) bracket resize", _pe)
                print(f"   ⚠️ {ticker}: bracket resize failed: {_pe}. Self-heal will re-place.")

        now_ny = ea.datetime.datetime.now(ZoneInfo("America/New_York"))
        profit_loss    = round((fill_price - buy_price) * scale_shares, 2)
        percent_return = round(((fill_price / buy_price) - 1.0) * 100.0, 2)
        reason = (
            f"Partial scale-out — sold {scale_shares}/{pre_qty} sh at "
            f"+{highest_unrealized_pct:.2f}% peak (trigger +{ea.SCALE_OUT_TRIGGER_PCT*100:.0f}%); "
            f"remainder rides the Prove-It stop"
        )

        # ── Mark scaled + reduce shares FIRST (idempotency + ledger) ───────────
        # The flag write precedes the trade_history insert so that even if the
        # accounting insert fails, the rule cannot re-fire next cycle and trim
        # the position a second time. The partial P&L remains recoverable from
        # ibkr_fills, and scaled_out_at keeps it out of the final close.
        client.table("portfolio_positions").update({
            "shares":        remaining,
            "scaled_out":    True,
            "scaled_out_at": now_ny.isoformat(),
        }).eq("ticker", ticker).execute()
        pos["shares"]        = remaining
        pos["scaled_out"]    = True
        pos["scaled_out_at"] = now_ny.isoformat()

        # ── Book the partial as its own trade_history row ──────────────────────
        # The buy commission stays attributed to the FINAL close, so the total
        # commission across the partial row + close row equals the real fees
        # exactly — no double count.
        trade_log = {
            "ticker":         ticker,
            "shares":         scale_shares,
            "buy_price":      buy_price,
            "buy_date":       buy_date.isoformat(),
            "buy_reason":     buy_reason,
            "sell_price":     fill_price,
            "sell_reason":    reason,
            "profit_loss":    profit_loss,
            "percent_return": percent_return,
        }
        sell_commission = ea.trade_commission(ib, trade)
        _th_resp = ea.insert_trade_history(client, trade_log)
        _th_id = ((_th_resp.data or [{}])[0] or {}).get("id")
        ea.record_trade_commissions(client, _th_id, None, sell_commission)

        print(f"✅ Scaled out {scale_shares} of {pre_qty} {ticker} @ ${fill_price:.2f} "
              f"(+{percent_return:.2f}%, ${profit_loss:+.2f}). {remaining} shares still open.")
        ea.notifier._send(
            f"✂️ <b>{ticker}</b> partial scale-out\n"
            f"  Sold: <code>{scale_shares}/{pre_qty}</code> sh @ <code>${fill_price:,.2f}</code>\n"
            f"  Booked: <code>${profit_loss:+,.2f}  ({percent_return:+.2f}%)</code>\n"
            f"  Peak gain: +{highest_unrealized_pct:.2f}% (trigger +{ea.SCALE_OUT_TRIGGER_PCT*100:.0f}%)\n"
            f"  {remaining} sh still open on the unchanged Prove-It stop."
        )
        return True

    except Exception as e:
        print(f"❌ Error executing scale-out for {ticker}: {e}")
        ea.notifier.notify_exception(f"execute_scale_out({ticker}) — execution_agent.py", e)
        # Best-effort: make sure the position is not left without a bracket.
        _q = _ibkr_qty()
        if _q and _q > 0:
            _restore_full_bracket(_q)
        return False
