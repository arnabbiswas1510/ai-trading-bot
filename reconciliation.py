"""Broker reconciliation, extracted from execution_agent.py
(see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: every patched sibling and the frozen ``execution_agent.datetime``
clock are referenced via ``ea.<name>`` so mock.patch on execution_agent stays live.
The mutable flag ``_IBKR_VALUATION_WARNING_SHOWN`` is patched by tests directly on
the execution_agent module, so it is read AND written as ``ea._IBKR_VALUATION_WARNING_SHOWN``
(its definition stays in execution_agent) rather than declared ``global`` here.
"""
from zoneinfo import ZoneInfo
from supabase import Client
from ib_insync import IB

from execution_agent_ref import ea

def _sync_ibkr_position_values(client: Client, ib_map: dict, tickers) -> int:
    """
    Persist IBKR's own valuation of each open position onto portfolio_positions.

    The read-only web container has no brokerage access, so without these columns
    the dashboard had to value positions as shares (Supabase) x price (FMP quote).
    That never matched the broker, and it added a live third-party price to an
    IBKR cash balance refreshed only once per agent cycle — mixing two vintages
    of data in one total.

    Values come from build_ibkr_price_map(): ib.portfolio() PortfolioItem objects
    for single-account logins, or a reqPnLSingle snapshot for multi-account
    logins where portfolio() is not served. This deliberately does NOT use
    ib.reqTickers(), which blocks indefinitely when the ushmds data farm is down.

    Degrades gracefully when migrations/20260904_add_ibkr_position_values.sql has not been
    applied: PGRST204 abandons this cycle rather than failing reconciliation, but
    the next cycle retries, so applying the migration takes effect without a
    restart.

    Returns the number of positions whose valuation was written.
    """

    synced_at = ea.datetime.datetime.now(ZoneInfo("America/New_York")).isoformat()
    written = 0

    for ticker in tickers:
        item = ib_map.get(ticker)
        # The positions() fallback path yields Position objects, which carry no
        # valuation. Skip them rather than writing a price derived from our own
        # cost basis — a stale broker mark is recoverable, a fabricated one is not.
        market_price = getattr(item, "marketPrice", None)
        if market_price is None or float(market_price) <= 0:
            continue

        payload = {
            "current_price":  round(float(market_price), 4),
            "market_value":   round(float(getattr(item, "marketValue", 0) or 0), 2),
            "unrealized_pnl": round(float(getattr(item, "unrealizedPNL", 0) or 0), 2),
            "ibkr_synced_at": synced_at,
        }
        try:
            client.table("portfolio_positions").update(payload).eq("ticker", ticker).execute()
            written += 1
        except Exception as e:
            msg = str(e)
            if "PGRST204" in msg or "column" in msg.lower():
                # Abandon this cycle -- every ticker would fail identically -- but
                # do not latch. The next reconcile retries, so the migration can
                # be applied to a running agent.
                if not ea._IBKR_VALUATION_WARNING_SHOWN:
                    ea._IBKR_VALUATION_WARNING_SHOWN = True
                    print("   ⚠️  IBKR valuation columns missing — run "
                          "migrations/20260904_add_ibkr_position_values.sql. Dashboard will show "
                          "cost basis until then. Retrying each cycle; no restart needed "
                          "once applied.")
                return written
            print(f"   ⚠️  Could not write IBKR valuation for {ticker}: {e}")

    if written:
        if ea._IBKR_VALUATION_WARNING_SHOWN:
            # Recovered: say so, otherwise the only trace in the log is a stale
            # warning that now reads as if it were still true.
            ea._IBKR_VALUATION_WARNING_SHOWN = False
            print("   ✅ IBKR valuation columns are now present — resuming valuation sync.")
        print(f"   💵 Synced IBKR valuation for {written} position(s).")
    return written


def reconcile_with_ibkr(ib: IB):
    """
    Full bidirectional reconciliation between IBKR actual positions and Supabase ledger.
    Runs every monitoring cycle (every 15 min during market hours).

    Case 1 — In Supabase, NOT in IBKR:
        Position was closed manually in TWS. Log to trade_history and remove from portfolio.

    Case 2 — In IBKR, NOT in Supabase:
        Position was opened manually in TWS. Insert into portfolio with computed stop/target.

    Case 3 — In both, but share count differs:
        Partial fill or manual adjustment. Update share count in Supabase.
    """
    print("🔄 Running IBKR ↔ Supabase reconciliation...")
    client = ea.get_supabase_client()

    # ── Sync live balance to Supabase (Do this FIRST) ──────────────────────
    try:
        tz = ZoneInfo("America/New_York")
        today_str = ea.datetime.datetime.now(tz).date().strftime("%Y-%m-%d")

        target_account = ea.get_ibkr_account(ib)

        # own_cash: only our deposited money (TotalCashValue ≥ 0).
        # margin_loan: borrowed amount when TotalCashValue < 0 (0 if no loan).
        own_cash    = ea.get_own_cash(ib, target_account)
        margin_loan = ea.get_margin_loan(ib, target_account)
        # cash_balance (the displayed cash line) is finalised further down: it is
        # derived from IBKR's NetLiquidation minus position value so cash +
        # positions always reconciles to the broker's own account total. own_cash
        # (raw TotalCashValue) is preserved separately in ibkr_own_cash for
        # margin diagnostics. Initialise to own_cash as the fallback value.
        cash_balance = own_cash

        db_pos = client.table("portfolio_positions").select(
            "ticker,shares,buy_price"
        ).execute().data or []

        # Position value for the account rollup uses IBKR's own mark first
        # (PortfolioItem.marketPrice) so account_balances agrees with both the
        # dashboard and the exit logic. FMP is only a per-ticker fallback, and
        # cost basis is the final fallback. get_position_price() reads the
        # non-blocking ib.portfolio() cache — never the blocking reqTickers().
        ib_price_map = ea.build_ibkr_price_map(ib)
        pos_value = 0.0
        for p in db_pos:
            price, src = ea.get_position_price(ib, p["ticker"], ib_price_map)
            if price <= 0:
                price = float(p["buy_price"])   # final fallback: cost basis
            pos_value += int(p["shares"]) * price

        # ── Anchor the account total to IBKR's authoritative NetLiquidation ────
        # Do NOT reconstruct net_liq as own_cash + pos_value. IBKR's TotalCashValue
        # (what get_own_cash reads) still includes cash committed to an UNSETTLED
        # purchase (US equities settle T+1), while the freshly bought shares
        # already appear in ib.portfolio() with a market value. Adding the two
        # therefore double-counts that purchase — e.g. CDNA on 2026-09-28 read
        # own_cash $93,259.06 AND positions $18,219.54, giving $111,478.60 when
        # IBKR's real NetLiquidation was $94,712.78 (an overstatement of the
        # ~$16.8k tied up in the position). IBKR's NetLiquidation tag nets the
        # pending settlement, so it is the only figure that always reconciles.
        # We take it as the source of truth for the total and derive the
        # displayed cash as (net_liq - pos_value) so cash + positions == net_liq
        # exactly, whatever the settlement state.
        ibkr_net_liq = ea.get_net_liquidation(ib, target_account)
        if ibkr_net_liq > 0:
            net_liq      = round(ibkr_net_liq, 2)
            derived_cash = round(net_liq - pos_value, 2)
            if derived_cash < 0:
                # pos_value exceeds IBKR's own equity — only possible if a
                # position is valued off stale cost basis because IBKR has no
                # mark for it (it should have been reconciled away). Keep the
                # authoritative total but fall back to raw own_cash for the
                # cash line rather than displaying negative cash.
                print(f"   ⚠️ derived cash negative (net_liq ${net_liq:,.2f} - "
                      f"positions ${pos_value:,.2f}); using raw own_cash "
                      f"${own_cash:,.2f} for the cash line.")
                cash_balance = own_cash
            else:
                cash_balance = derived_cash
        else:
            # NetLiquidation tag momentarily unavailable — fall back to the old
            # reconstruction so the sync still runs (may transiently overstate
            # during unsettled purchases, but never blocks the balance write).
            print("   ⚠️ NetLiquidation unavailable — reconstructing total from "
                  "own_cash + positions (may overstate during T+1 settlement).")
            cash_balance = own_cash
            net_liq      = round(cash_balance + pos_value, 2)

        if net_liq > 0:
            upsert_payload = {
                "date":                 today_str,
                "ibkr_cash_balance":    round(cash_balance, 2),
                "ibkr_positions_value": round(pos_value, 2),
                "ibkr_total_value":     round(net_liq, 2),
                "ibkr_own_cash":        round(own_cash, 2),
                "ibkr_margin_loan":     round(margin_loan, 2),
            }
            client.table("account_balances").upsert(upsert_payload).execute()
            # Alert-channel health is written as a SEPARATE best-effort update,
            # never as part of the upsert above -- same reasoning as
            # hard_stop_price: a lagging migration must not be able to fail the
            # balance sync, which the dashboard and exit sizing both depend on.
            # Persisting it means a dead alert channel is visible in the data
            # every ~15 minutes without needing container-log access.
            try:
                _tg = ea.notifier.health()
                client.table("account_balances").update({
                    "telegram_consecutive_failures": _tg["consecutive_failures"],
                    "telegram_last_success": (
                        ea.datetime.datetime.fromtimestamp(
                            _tg["last_success_at"], tz=ea.datetime.timezone.utc
                        ).isoformat() if _tg["last_success_at"] else None
                    ),
                }).eq("date", today_str).execute()
            except Exception as _tg_err:
                if not ("PGRST204" in str(_tg_err) or "telegram_" in str(_tg_err)):
                    print(f"   ⚠️ could not persist Telegram health: {_tg_err}")
            margin_note = f" ⚠️ MARGIN LOAN: ${margin_loan:,.2f}" if margin_loan > 0 else ""
            print(f"   💰 Balance synced [{target_account}]: cash=${cash_balance:,.2f} "
                  f"positions=${pos_value:,.2f} net_liq=${net_liq:,.2f} "
                  f"(IBKR NetLiquidation; raw TotalCashValue=${own_cash:,.2f}) "
                  f"({len(db_pos)} position(s)){margin_note}")
    except Exception as e:
        ea.notifier.notify_exception("reconcile_with_ibkr() cash sync — execution_agent.py", e)
        print(f"   ❌ Could not sync cash balance: {e}")



    # ── Fetch IBKR positions via portfolio() with positions() fallback ───────
    # portfolio() reads from the in-memory account cache which may be empty
    # after a reconnect. We call reqPositions() first (unconditional TWS push)
    # to populate ib.positions(), then prefer portfolio() for richer data but
    # fall back to positions() if portfolio() is still empty.
    try:
        try:
            ib.reqPositions()
            ib.sleep(2)   # let event loop populate ib.positions()
        except Exception as _rp_err:
            print(f"   ⚠️  reqPositions() failed (non-fatal): {_rp_err}")

        target_account = ea.get_ibkr_account(ib)
        ib_raw = [
            p for p in ib.portfolio()
            if ea._matches_account(p, target_account)
        ]

        if not ib_raw:
            _pos_fallback = [
                p for p in ib.positions()
                if p.contract.secType == "STK" and p.position > 0 and ea._matches_account(p, target_account)
            ]
            if _pos_fallback:
                print(f"   ⚠️  portfolio() empty — using positions() fallback "
                      f"({len(_pos_fallback)} position(s)).")
                ib_map = {p.contract.symbol: p for p in _pos_fallback}
            else:
                ib_map = {}   # genuinely empty — guard below will handle
        else:
            for p in ib_raw:
                if p.contract.secType == "STK" and int(p.position) < 0:
                    msg = (f"🚨 SHORT POSITION DETECTED: {p.contract.symbol} "
                           f"has {int(p.position)} shares. Close this immediately in TWS!")
                    print(msg)
                    try:
                        ea.notifier.notify_error(msg)
                    except Exception:
                        pass
            ib_map = {
                p.contract.symbol: p
                for p in ib_raw
                if p.contract.secType == "STK" and int(p.position) > 0
            }
    except Exception as e:
        ea.notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
        print(f"❌ Could not fetch IBKR positions during reconciliation: {e}")
        return

    ib_tickers = set(ib_map.keys())

    # ── Fetch Supabase positions ────────────────────────────────────────────
    try:
        res = client.table("portfolio_positions").select("*").execute()
        supabase_positions = res.data or []
    except Exception as e:
        ea.notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
        print(f"❌ Could not fetch Supabase positions during reconciliation: {e}")
        return

    supabase_map = {p["ticker"]: p for p in supabase_positions}
    supabase_tickers = set(supabase_map.keys())

    # ── Safety guard: empty IBKR response while Supabase has positions ──────
    # ib.portfolio() transiently returns [] when account data hasn't finished
    # loading after a reconnect. Without this guard, Case 1 would delete every
    # Supabase position on a false "not in IBKR" signal.
    #
    # However: if portfolio() is empty but reqExecutions() shows a confirmed
    # SLD fill for one of our tickers, that fill is real and must be processed
    # regardless. We handle confirmed fills individually here, then return to
    # skip bulk reconciliation (which is still unsafe on an empty read).
    if not ib_tickers and supabase_tickers:
        print(f"   ⚠️  IBKR returned empty portfolio but Supabase has "
              f"{len(supabase_tickers)} position(s). "
              f"Checking executions for confirmed fills before skipping...")

        # Check each Supabase ticker for a confirmed SLD fill
        try:
            all_fills = ib.reqExecutions()
        except Exception as _fe:
            print(f"   ⚠️  reqExecutions() failed: {_fe}")
            all_fills = []

        confirmed_sold = set()
        for fill in all_fills:
            if (fill.contract.secType == "STK"
                    and fill.execution.side == "SLD"
                    and fill.contract.symbol in supabase_tickers):
                confirmed_sold.add(fill.contract.symbol)

        if confirmed_sold:
            print(f"   🔍 Confirmed SLD fills found for: {confirmed_sold} — processing individually.")
            # Temporarily set ib_map to empty (no live positions for these tickers)
            # so Case 1 logic below handles them. We restrict candidates_to_delete
            # to only confirmed fills to avoid false deletions on the others.
            candidates_to_delete = confirmed_sold
            changes = 0
            # Fall through to Case 1 loop with restricted candidates
        else:
            print(f"   ℹ️  No confirmed SLD fills found — skipping reconcile to prevent false deletion.")
            return

    else:
        candidates_to_delete = supabase_tickers - ib_tickers
        changes = 0

    # ── Case 1: In Supabase but NOT in IBKR ─────────────────────────────────
    # IBKR is the single source of truth: it manages trailing stops via GTC
    # TRAIL orders. Any position missing from IBKR portfolio was legitimately
    # closed (trailing stop fired or manual TWS close). Guard 1 above (empty
    # portfolio) is the only transient-glitch guard needed.
    for ticker in candidates_to_delete:
        pos = supabase_map[ticker]
        print(f"   ✅ {ticker}: position closed in IBKR — archiving to trade_history.")

        # ── Determine sell price — three-tier lookup ──────────────────────
        #
        # Tier 1: ibkr_fills Supabase table (real-time hook writes fills here
        #         the instant they happen — durable across restarts)
        # Tier 2: reqExecutions() TWS session cache (fast path for fills that
        #         arrived in the current session, < few minutes old)
        # Tier 3: Flex Query TradeConfirm (IBKR Transaction History API,
        #         on-demand, 5-10 min lag — requires IBKR_FLEX_EXEC_QUERY_ID)
        # Fallback: PRICE_UNCERTAIN alert — Telegram + flagged sell_reason
        #
        # This architecture was introduced 2026-07-21 after the RSI incident
        # where a trailing stop fill from 2026-07-17 was recorded at the wrong
        # day's FMP price because reqExecutions() lost the fill over a weekend.
        sell_price        = 0.0
        sell_price_source = "unknown"
        sell_date_fill    = None
        has_sld_fill      = False

        # If this position was partially scaled out earlier, its scale-out SLD
        # fill is in ibkr_fills too. Exclude everything up to and including that
        # fill so the CLOSE is priced only from the fills that sold the remaining
        # shares — the scale-out P&L is already booked in its own trade_history
        # row. See execute_scale_out().
        _close_since = pos.get("scaled_out_at") or pos.get("buy_date")

        # ── Tier 1: ibkr_fills (persistent Supabase table) ───────────────
        try:
            _t1_query = client.table("ibkr_fills") \
                .select("exec_id,shares,price,fill_time") \
                .eq("ticker", ticker).eq("side", "SLD")
            # Floor the fill window at the CURRENT position's entry (or the
            # scale-out instant if the runner was already trimmed). Without this,
            # a ticker that was round-tripped earlier — bought, sold, then bought
            # again — pulls the PRIOR round-trip's SLD fills into this close and
            # averages them into the exit price. FIVE (2026-09-09) is the proof:
            # a stale 84 sh @ $252.375 sell from earlier the same day was blended
            # with the real 85 sh @ $248.85 exit, yielding a fictitious $250.60
            # and understating the loss by ~$150. `_close_since` is
            # `scaled_out_at or buy_date`, both stored as full ISO timestamps, so
            # `.gt` cleanly excludes anything at or before this entry.
            if _close_since:
                _t1_query = _t1_query.gt("fill_time", _close_since)
            sb_fills_res = _t1_query.order("fill_time", desc=False).execute()
            sb_fills = sb_fills_res.data or []
            if sb_fills:
                total_qty  = sum(float(f["shares"]) for f in sb_fills)
                sell_price = (
                    sum(float(f["shares"]) * float(f["price"]) for f in sb_fills)
                    / total_qty
                ) if total_qty > 0 else 0.0
                exec_ids   = ", ".join(f["exec_id"] for f in sb_fills)
                # Full timestamp, not just the date. `ibkr_fills.fill_time` is
                # written from `fill.execution.time.isoformat()` (a tz-aware UTC
                # instant), so the precision is already there — truncating it
                # with [:10] threw it away and stamped every exit at midnight.
                # Midnight is EARLIER than the entry for any position bought and
                # closed the same session, which made same-day exits compute a
                # negative holding period.
                #
                # The LAST fill is used, not the first: this row records a
                # *closed* position, and it is not closed until the final share
                # is sold. `sb_fills` is ordered fill_time ascending by the query.
                sell_date_fill = sb_fills[-1]["fill_time"]
                sell_price_source = (
                    f"ibkr_fills (persistent DB, {len(sb_fills)} fill(s), "
                    f"execIds: {exec_ids})"
                )
                has_sld_fill = True
                print(f"        💾 Tier 1 — ibkr_fills: {len(sb_fills)} fill(s) → "
                      f"weighted avg ${sell_price:.4f} on {sell_date_fill}")
        except Exception as ex:
            print(f"        ⚠️  ibkr_fills lookup failed (non-fatal): {ex}")

        # ── Tier 2: reqExecutions() session cache ─────────────────────────
        if not has_sld_fill:
            try:
                session_fills = ib.reqExecutions()
                # Exclude any SLD fill that belongs to a PRIOR round-trip or the
                # scale-out trim — the close is priced only from fills that sold
                # the remaining shares of THIS position. `_close_since` is the
                # scale-out instant when present, otherwise this position's
                # entry, so a re-bought ticker never inherits an earlier exit's
                # price from the session cache (same defect fixed in Tier 1).
                _scaled_at = None
                if _close_since:
                    try:
                        _scaled_at = ea.datetime.datetime.fromisoformat(
                            str(_close_since).replace("Z", "+00:00")
                        )
                    except Exception:
                        _scaled_at = None
                sell_fills = [
                    f for f in session_fills
                    if f.contract.symbol == ticker and f.execution.side == "SLD"
                    and (_scaled_at is None or f.execution.time > _scaled_at)
                ]
                if sell_fills:
                    total_qty  = sum(f.execution.shares for f in sell_fills)
                    sell_price = (
                        sum(f.execution.shares * f.execution.price for f in sell_fills)
                        / total_qty
                    ) if total_qty > 0 else 0.0
                    exec_ids   = ", ".join(f.execution.execId for f in sell_fills)
                    sell_fills_sorted = sorted(sell_fills, key=lambda f: f.execution.time)
                    # Keep the time: `execution.time` is a tz-aware UTC instant,
                    # and .date() discarded it. See the Tier 1 note above — the
                    # last fill is the moment the position actually closed.
                    sell_date_fill    = sell_fills_sorted[-1].execution.time.isoformat()
                    sell_price_source = (
                        f"reqExecutions session cache weighted avg "
                        f"({len(sell_fills)} fill(s), execIds: {exec_ids})"
                    )
                    has_sld_fill = True
                    print(f"        📡 Tier 2 — reqExecutions: {len(sell_fills)} fill(s) → "
                          f"weighted avg ${sell_price:.4f} on {sell_date_fill}")
            except Exception as ex:
                ea.notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", ex)
                print(f"        ⚠️  reqExecutions() failed for {ticker}: {ex}")

        # ── Tier 3: Flex Query TradeConfirm (IBKR Transaction History) ────
        if not has_sld_fill:
            print(f"        🔍 Tier 3 — trying Flex TradeConfirm for {ticker}...")
            flex_data = ea.fetch_trade_confirms_for_ticker(ticker)
            if flex_data:
                sell_price        = flex_data["sell_price"]
                sell_date_fill    = flex_data["sell_date"]
                sell_price_source = flex_data["source"]
                has_sld_fill      = True
                print(f"        📋 Tier 3 — Flex TradeConfirm: "
                      f"weighted avg ${sell_price:.4f} on {sell_date_fill}")

        # If no SLD fill (e.g. manual TWS close or stale session), do a single
        # double-check to rule out a transient partial portfolio read.
        if not has_sld_fill:
            ib.sleep(3)
            _ib_recheck = {
                p.contract.symbol: p for p in ib.portfolio()
                if ea._matches_account(p, target_account)
                and p.contract.secType == "STK" and int(p.position) > 0
            }
            if ticker in _ib_recheck:
                print(f"        ⚠️  {ticker} reappeared on double-check — skipping (transient IBKR glitch).")
                continue
            print(f"        ℹ️  No SLD fill in current TWS session — "
                  f"fills from prior sessions are NOT in cache.")

        # Cancel any remaining SELL orders for this ticker (cleanup)
        ea.cancel_ticker_sell_orders(ib, ticker)

        # ── FIX (Bug 2 & 4): FMP fallback — flag as PRICE_UNCERTAIN ─────────
        # When fills are not in the current session (e.g. trailing stop fired
        # over a weekend), the FMP live price is the WRONG DAY's price.
        # We still record it (to avoid a zero-price row) but prefix with
        # PRICE_UNCERTAIN so the record is visibly flagged for manual correction.
        if sell_price <= 0:
            fmp_price = ea.get_live_price(ticker)
            if fmp_price > 0:
                sell_price        = fmp_price
                sell_price_source = (
                    "⚠️ PRICE_UNCERTAIN — FMP live quote used as fallback "
                    "(fill not in current TWS session; actual fill may differ — "
                    "verify against IBKR transaction history)"
                )
                ea.notifier.notify_error(
                    f"⚠️ {ticker} sell price is UNCERTAIN\n"
                    f"reqExecutions() found no fills in the current TWS session.\n"
                    f"Using FMP live price ${fmp_price:.2f} as a placeholder.\n"
                    f"Check IBKR transaction history and correct manually."
                )
                print(f"        ⚠️  PRICE_UNCERTAIN: using FMP ${fmp_price:.2f} as placeholder. "
                      f"Verify in IBKR transaction history.")

        # Fallback 2: buy_price (last resort — prevents a zero-price DB row)
        if sell_price <= 0:
            sell_price        = float(pos["buy_price"])
            sell_price_source = "buy_price (no price source available)"

        print(f"        Sell price source: {sell_price_source} → ${sell_price:.2f}")

        shares     = int(pos["shares"])
        buy_price  = float(pos["buy_price"])
        buy_date   = pos["buy_date"]
        buy_reason = pos.get("buy_reason", "Unknown")
        profit_loss    = round((sell_price - buy_price) * shares, 2)
        percent_return = round(((sell_price / buy_price) - 1.0) * 100.0, 2)

        # ── FIX (Bug 4): correct sell_reason based on whether a fill was found ─
        #
        # Record the exit CONTEXT, not just the exit label. The bare string
        # "Trailing stop (IBKR GTC TRAIL order)" is all that used to be written
        # here, which left the dashboard unable to answer the first question an
        # operator asks about a stopped-out trade: what was the trail, and what
        # peak was it anchored to? Every one of those numbers is sitting on the
        # `pos` row we are about to delete, so discarding them was gratuitous —
        # once the row is gone they are unrecoverable.
        #
        # These are appended to the free-text reason rather than added as new
        # columns so that no schema migration is required and the existing
        # `trade_history` shape is untouched; the dashboard parses them back out.
        if has_sld_fill:
            sell_reason = "Trailing stop (IBKR GTC TRAIL order)" + ea._exit_context_suffix(
                pos, sell_price, broker_trail_fill=True)
        else:
            sell_reason = "Manual close in IBKR (reconciled) — PRICE_UNCERTAIN" + ea._exit_context_suffix(pos, sell_price)

        # ── FIX (Bug 3): write explicit sell_date from fill timestamp ─────────
        trade_log = {
            "ticker":         ticker,
            "shares":         shares,
            "buy_price":      buy_price,
            "buy_date":       buy_date,
            "buy_reason":     buy_reason,
            "sell_price":     sell_price,
            "sell_reason":    sell_reason,
            "sell_date":      sell_date_fill,   # None when fill not in session (Supabase auto-stamps)
            "profit_loss":    profit_loss,
            "percent_return": percent_return,
        }
        trade_log.update(ea.entry_provenance(pos))
        try:
            # Delete from portfolio FIRST, independently of trade history
            client.table("portfolio_positions").delete().eq("ticker", ticker).execute()
            changes += 1
            print(f"        ✅ Removed {ticker} from Supabase portfolio.")
            
            try:
                # Then try to insert to trade_history
                _th_resp = ea.insert_trade_history(client, trade_log)
                # This close happened outside the agent's session (manual close or
                # an IBKR-side stop), so there is no Trade object to read. The fee
                # comes from ibkr_fills -- which only started collecting rows once
                # the RLS policy gap was fixed, so older closes stay NULL.
                _th_id = ((_th_resp.data or [{}])[0] or {}).get("id")
                ea.record_trade_commissions(
                    client, _th_id,
                    pos.get("buy_commission") if isinstance(pos, dict) else None,
                    ea.sum_fill_commission(client, ticker, "SLD", _close_since),
                )
                print(f"        ✅ Logged to history. PnL: ${profit_loss:+.2f} ({percent_return:+.2f}%)")
                ea._write_breakout_learning_row(
                    client=client,
                    ticker=ticker,
                    buy_date=ea.datetime.datetime.fromisoformat(str(buy_date).replace("Z", "+00:00")),
                    reason=sell_reason,
                    pos_row=pos,
                    market_regime="neutral",
                    percent_return=percent_return,
                )
                ea.notifier.notify_manual_close(
                    ticker=ticker, shares=shares, buy_price=buy_price,
                    sell_price=sell_price, sell_price_source=sell_price_source,
                    buy_date=buy_date
                )
            except Exception as e:
                ea.notifier.notify_exception(f"reconcile_with_ibkr() (trade_history insert) — execution_agent.py", e)
                print(f"        ❌ DB error adding {ticker} to trade_history: {e}")
        except Exception as e:
            ea.notifier.notify_exception(f"reconcile_with_ibkr() (portfolio delete) — execution_agent.py", e)
            print(f"        ❌ DB error removing {ticker} from portfolio: {e}")

    # ── Case 2: In IBKR but NOT in Supabase (manual buy / opened in TWS) ───
    for ticker in ib_tickers - supabase_tickers:
        ib_pos = ib_map[ticker]
        shares = int(ib_pos.position)
        # averageCost (PortfolioItem) or avgCost (Position, multi-account
        # fallback) — see _ibkr_avg_cost(). Reading .averageCost directly here
        # would raise on the positions() fallback objects (Bug #5).
        avg_cost = round(ea._ibkr_avg_cost(ib_pos) or 0.0, 2)

        if avg_cost <= 0:
            print(f"   ⚠️  {ticker}: in IBKR with zero avg cost — skipping.")
            continue

        print(f"   ⚠️  {ticker}: in IBKR but not in Supabase — manual buy detected.")

        stop_loss = round(avg_cost * (1 - ea.STOP_LOSS_PCT), 2)
        buy_date = ea.datetime.datetime.now(ea.datetime.timezone.utc).isoformat()

        position_data = {
            "ticker": ticker,
            "shares": shares,
            "buy_price": avg_cost,
            "buy_date": buy_date,
            "buy_reason": "Manual IBKR order (reconciled)",
            "buy_source": "daily_triggers",   # Bug fix: always set buy_source to prevent NULL
            "hwm_date":  ea.datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "hwm_price": avg_cost,   # initialised to buy price; ratchets up in monitor loop
            "entry_rs_score": ea._get_entry_rs(ticker, None),   # live-fetched so Rule 1 has a baseline
        }
        try:
            client.table("portfolio_positions").insert(position_data).execute()
            print(f"        ✅ Added to Supabase: {shares} shares @ ${avg_cost} "
                  f"| Trail: {ea.STOP_LOSS_PCT*100:.2f}% (IBKR-managed)")
            changes += 1
        except Exception as e:
            ea.notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
            print(f"        ❌ DB error adding {ticker} to Supabase: {e}")

    # ── Case 3: In both, but share count mismatch (partial fill / adjustment)
    for ticker in ib_tickers & supabase_tickers:
        ib_shares = int(ib_map[ticker].position)
        db_shares = int(supabase_map[ticker]["shares"])
        if ib_shares != db_shares:
            print(f"   ⚠️  {ticker}: share count mismatch — IBKR: {ib_shares}, Supabase: {db_shares}. Correcting.")
            try:
                client.table("portfolio_positions").update({"shares": ib_shares}).eq("ticker", ticker).execute()
                print(f"        ✅ Updated to {ib_shares} shares.")
                changes += 1
            except Exception as e:
                ea.notifier.notify_exception(f"reconcile_with_ibkr() — execution_agent.py", e)
                print(f"        ❌ DB error updating shares for {ticker}: {e}")

        # ── buy_price drift guard ────────────────────────────────────────────
        # A wrong buy_price silently corrupts BOTH the dashboard P&L and every
        # buy_price-anchored exit rule (prove-it band, give-back floor, hard
        # stop), so it is worth correcting — but only against a source that
        # actually knows what this lot cost.
        #
        # IBKR's averageCost is NOT that source after a round trip. On
        # 2026-08-31 NTRA was bought 40 @ 338.43 (8/26), sold, re-bought
        # 61 @ 320.49, sold again, and finally re-bought 61 @ 317.4295. IBKR
        # then reported averageCost = 331.70, which is exactly
        # (total buys + commissions − total sell proceeds) / 61 — the two
        # earlier realised losses folded into the surviving lot. 331.70 was
        # above NTRA's entire trading range that day, so no fill could have
        # occurred there. This guard adopted it anyway, turning a +$220 winner
        # into a phantom −$649.65 loss and mis-anchoring every exit rule for
        # six days.
        #
        # Order of trust is therefore: (1) the BOT fills that opened this lot,
        # (2) averageCost, but only when the symbol has NOT been round-tripped.
        ib_avg = ea._ibkr_avg_cost(ib_map[ticker])
        db_buy = float(supabase_map[ticker].get("buy_price") or 0.0)
        pos_buy_date = supabase_map[ticker].get("buy_date")

        truth = ea.lot_buy_basis_from_fills(client, ticker, pos_buy_date, ib_shares)
        if truth:
            true_basis, basis_source = truth
        elif ib_avg and ea.has_prior_round_trip(client, ticker, pos_buy_date):
            # averageCost is the only candidate AND it is untrustworthy here.
            if abs(ib_avg - db_buy) / ib_avg > ea.BUY_PRICE_DRIFT_TOLERANCE:
                print(f"   🛑 {ticker}: refusing buy_price 'correction' ${db_buy:.2f} → "
                      f"${ib_avg:.2f} — averageCost is contaminated by an earlier "
                      f"round trip and no BOT fills cover this lot.")
            true_basis, basis_source = None, ""
        else:
            true_basis = round(ib_avg, 4) if ib_avg else None
            basis_source = "IBKR averageCost (no round trip on record)"

        if true_basis and db_buy > 0 and abs(true_basis - db_buy) / true_basis > ea.BUY_PRICE_DRIFT_TOLERANCE:
            corrected = round(true_basis, 2)
            hwm = float(supabase_map[ticker].get("hwm_price") or corrected)
            new_peak = round(max(0.0, (hwm / corrected - 1.0) * 100.0), 4)
            new_closed_above = hwm > corrected
            drift_pct = (db_buy - true_basis) / true_basis * 100.0
            print(f"   ⚠️  {ticker}: buy_price drift — stored ${db_buy:.2f} vs "
                  f"${true_basis:.2f} ({drift_pct:+.2f}%). Source: {basis_source}.")
            try:
                client.table("portfolio_positions").update({
                    "buy_price":              corrected,
                    "highest_unrealized_pct": new_peak,
                    "closed_above_entry":     new_closed_above,
                }).eq("ticker", ticker).execute()
                supabase_map[ticker]["buy_price"] = corrected
                changes += 1
                print(f"        ✅ buy_price corrected to ${corrected:.2f} "
                      f"(peak reset to {new_peak:.2f}%, proven={new_closed_above}).")
                ea.notifier.notify_error(
                    f"🩹 {ticker}: buy_price corrected on reconcile\n"
                    f"Stored ${db_buy:.2f} → ${corrected:.2f} ({drift_pct:+.2f}%).\n"
                    f"Source: {basis_source}.\n"
                    f"Peak reset to {new_peak:.2f}%, proven={new_closed_above}. "
                    f"P&L and exit rules now price off the true cost basis."
                )
            except Exception as e:
                # Migration lag on the derived columns must not block the core
                # buy_price correction — retry with buy_price alone.
                if "PGRST204" in str(e) or "highest_unrealized_pct" in str(e) or "closed_above_entry" in str(e):
                    try:
                        client.table("portfolio_positions").update({"buy_price": corrected}) \
                            .eq("ticker", ticker).execute()
                        supabase_map[ticker]["buy_price"] = corrected
                        changes += 1
                        print(f"        ✅ buy_price corrected to ${corrected:.2f} "
                              f"(derived columns absent — run migrations).")
                    except Exception as _inner:
                        ea.notifier.notify_exception("reconcile_with_ibkr() buy_price drift — execution_agent.py", _inner)
                        print(f"        ❌ DB error correcting buy_price for {ticker}: {_inner}")
                else:
                    ea.notifier.notify_exception("reconcile_with_ibkr() buy_price drift — execution_agent.py", e)
                    print(f"        ❌ DB error correcting buy_price for {ticker}: {e}")

    # ── Persist IBKR's own valuation for every position we agree exists ──────
    # Written after the share-count correction above so market_value is stored
    # alongside a share count IBKR has already confirmed. Marks come from
    # build_ibkr_price_map (portfolio() or reqPnLSingle) so the valuation is
    # populated even on multi-account logins where the inline ib_map above was
    # built from bare positions() objects that carry no marketPrice.
    _sync_ibkr_position_values(client, ea.build_ibkr_price_map(ib),
                               ib_tickers & supabase_tickers)

    if changes == 0:
        print("   ✅ Supabase and IBKR are in sync. No changes needed.")
    else:
        print(f"   🔄 Reconciliation complete — {changes} correction(s) applied.")
