"""Market-open buying + schema/position-size gates, extracted from
execution_agent.py (see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: patched siblings, helper modules (cooling_off, schema_guard,
trigger_audit), config constants, and the frozen ``execution_agent.datetime``
clock are referenced via ``ea.<name>`` so mock.patch on execution_agent stays
live. ``_schema_alert_sent`` is patched by tests on the execution_agent module,
so it is read AND written as ``ea._schema_alert_sent`` (definition stays in
execution_agent) instead of a local ``global``.
"""
from zoneinfo import ZoneInfo
from ib_insync import IB, Stock, MarketOrder

from execution_agent_ref import ea
import decision_core as dc
import intraday_capture as capture

def assert_schema_ok(client) -> bool:
    """Verify risk-rule columns exist. Returns False when new buys must be blocked.

    Alerts once per degradation episode rather than every 15-minute cycle, and
    sends a recovery notice when the migration is applied, so the Telegram signal
    stays meaningful.
    """
    try:
        report = ea.schema_guard.check_schema(client)
    except Exception as e:
        # A probe failure must not stop trading — that would turn a monitoring
        # concern into an outage. Log and allow the cycle to proceed.
        print(f"   ⚠️ Schema check failed to run ({e}) — continuing without it.")
        return True

    if report.degraded:
        print("🚨 SCHEMA DEGRADED — new buys blocked this cycle:")
        for table, col, why in report.missing_critical:
            print(f"   • MISSING {table}.{col} — {why}")
        print(f"   Fix: run {ea.schema_guard.REPAIR_SCRIPT} in the Supabase SQL Editor.")
        if not ea._schema_alert_sent:
            try:
                ea.notifier.notify_error(report.summary())
            except Exception:
                pass
            ea._schema_alert_sent = True
        return False

    if report.missing_advisory:
        for table, why in report.missing_advisory:
            print(f"   ⚠️ Analytics table missing: {table} — {why}")

    if ea._schema_alert_sent:
        print("✅ Schema restored — new buys re-enabled.")
        try:
            ea.notifier.notify_error(
                "✅ *Schema restored*\nAll risk-rule columns are present again. "
                "New buys are re-enabled."
            )
        except Exception:
            pass
        ea._schema_alert_sent = False
    return True


def equity_capped_position_size(available_cash: float, remaining_slots: int,
                                equity: float, max_positions: int) -> float:
    """Dollar allocation for one new position, never exceeding an equal-weight
    share of total account equity.

    The base allocation is the historical rule — free cash spread evenly across
    the still-open slots: ``available_cash / remaining_slots``. On top of that a
    HARD CEILING of ``equity / max_positions`` (one equal-weight slot of the
    whole account) is applied.

    ── Why the ceiling exists (2026-09-21 oversizing incident) ──────────────
    The base rule alone oversizes a replacement position whenever the book is
    nearly full but a large cash pile is free. On 2026-09-21 four names opened in
    the morning at ~$19k each (cash divided ÷4, ÷3, ÷2, ÷1 across empty slots),
    then as single slots reopened intraday the formula put ALL free cash into the
    one open slot: MPC was sized ``$37,916 / 1 slot = $36,206`` and PSX
    ``$37,184 / 1 slot = $35,856`` — about 1.6x the $22,306 equal-weight share of
    the $111,530 account, and nearly 2x the morning cohort. A routine −2% stop
    then lost ~$720 on each instead of the ~$400 an equal-weight position would
    have. The exits fired correctly; the dollar damage came entirely from size.
    See decisions/2026-09-21_equity-capped-position-size.md.

    Args:
        available_cash:  free (margin-free) cash available to deploy this cycle.
        remaining_slots: open position slots (MAX_POSITIONS − held). Coerced to
                         at least 1 to avoid division by zero.
        equity:          total account equity (NetLiquidation). When <= 0 it is
                         treated as UNKNOWN and the ceiling is skipped rather than
                         applied as a zero — a zero cap would block every buy.
                         Callers must pass a best-effort equity (reconstruct from
                         cash + held market value if NetLiquidation is missing) so
                         the cap is virtually always active.
        max_positions:   MAX_POSITIONS — the divisor for the equal-weight cap.

    Returns:
        The dollar allocation, guaranteed <= equity / max_positions whenever
        equity and max_positions are both positive.

    Delegates to decision_core.equity_capped_position_size so the live path and
    the backtester size positions with byte-identical logic (Phase 1 parity).
    This wrapper is kept so existing imports of ``buying.equity_capped_position_size``
    continue to resolve.
    """
    return dc.equity_capped_position_size(
        available_cash, remaining_slots, equity, max_positions)


def _print_skip(ticker: str, decision) -> None:
    """Operator-facing one-liner for a skipped trigger. The authoritative record
    is always the trigger_audit row written by the caller; this is console
    visibility only, so its wording is not asserted by any test."""
    print(f"   ⏭️  {ticker} skipped [{decision.reason_code}]: {decision.detail}")


# ── Once-daily "why are slots empty?" operator summary ────────────────────────
# The buy check runs every 15 minutes, so this must dedup to at most one message
# per ET day and survive restarts. Persistence is the daily_notifications table
# (migrations/20260928_add_daily_notifications.sql); an in-memory flag would
# resend after every deploy or crash-loop. See
# decisions/2026-09-28_unfilled-slot-daily-alert.md.

# reason_code (trigger_audit) → human phrase for the per-reason breakdown.
_SKIP_REASON_TEXT = {
    "ALREADY_HELD":         "already an open position",
    "COOLING_OFF":          "in cooling-off after a recent exit",
    "AI_VETO":              "vetoed by the AI evaluator (D-grade)",
    "EARNINGS_IMMINENT":    "earnings within the blackout window (deferred)",
    "NO_AI_SCORE":          "not scored by the AI evaluator",
    "SCORE_FLOOR":          "below the quality-score / volume floor",
    "SLOTS_FULL":           "no slot free when it was evaluated",
    "INSUFFICIENT_CASH":    "insufficient cash to size a position",
    "NO_PRICE":             "no valid live price available",
    "EXTENDED_ABOVE_PIVOT": "extended too far above the pivot (chase guard)",
    "BELOW_PIVOT":          "fallen back below the pivot (failed breakout)",
    "SHARES_ZERO":          "price too high for the position size",
    "BUY_FAILED":           "order failed at the broker",
    "LOOP_HALTED":          "buy loop halted after a broker error",
}

_SLOT_REPORT_TYPE = "unfilled_slots"


def _slot_report_already_sent(client, report_date: str) -> bool:
    """True when today's unfilled-slot summary has already gone out.

    Fails SAFE: on any probe error (including the table not existing yet) it
    returns True so the summary is suppressed rather than sent every cycle. The
    cost of a missing migration is silence for a day, not 26 duplicate alerts.
    """
    try:
        res = (client.table("daily_notifications")
               .select("report_date")
               .eq("report_type", _SLOT_REPORT_TYPE)
               .eq("report_date", report_date)
               .limit(1).execute())
        return bool(res.data)
    except Exception as e:
        print(f"   ⚠️ daily_notifications probe failed ({e}) — suppressing the "
              f"unfilled-slot summary this cycle. Apply "
              f"migrations/20260928_add_daily_notifications.sql.")
        return True


def _slot_report_mark_sent(client, report_date: str, detail: str) -> None:
    """Latch today's summary as sent. Non-fatal."""
    try:
        client.table("daily_notifications").upsert({
            "report_type": _SLOT_REPORT_TYPE,
            "report_date": report_date,
            "detail":      (detail or "")[:1000],
        }, on_conflict="report_type,report_date").execute()
    except Exception as e:
        print(f"   ⚠️ Could not persist daily_notifications (non-fatal): {e}")


def _summarize_today_skips(client, decision_date: str) -> list[str]:
    """Bulleted per-reason breakdown of today's SKIPPED trigger decisions.

    Reads the trigger_decisions rows the buy loop already writes each cycle and
    groups them by reason_code, most common first, with up to five sample
    tickers per reason. Returns [] when nothing is recorded.
    """
    try:
        res = (client.table("trigger_decisions")
               .select("ticker,reason_code,decision")
               .eq("decision_date", decision_date).execute())
        rows = res.data or []
    except Exception:
        return []

    groups: dict[str, list[str]] = {}
    for r in rows:
        if r.get("decision") == "BOUGHT":
            continue
        code = r.get("reason_code") or "OTHER"
        groups.setdefault(code, []).append(r.get("ticker"))

    lines = []
    for code, tickers in sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True):
        uniq = [t for t in dict.fromkeys(t for t in tickers if t)]
        text = _SKIP_REASON_TEXT.get(code, code.replace("_", " ").lower())
        sample = ", ".join(uniq[:5])
        more = f" +{len(uniq) - 5} more" if len(uniq) > 5 else ""
        suffix = f" ({sample}{more})" if sample else ""
        lines.append(f"• {len(uniq)} {text}{suffix}")
    return lines


def maybe_report_unfilled_slots(client, standdown_reason: str | None = None) -> None:
    """Send ONE Telegram summary per ET day when the book has idle slots.

    Called at every stand-down (`return`) inside run_market_open_buys and once
    at the end of a normal cycle. Self-contained: it re-reads holdings so it can
    run even on the early returns that fire before holdings are fetched. A no-op
    when the portfolio is full (free <= 0) or the summary already went out today.

    `standdown_reason` is a single top-level cause (market bearish, margin loan,
    schema degraded, no triggers). When None, the reasons are aggregated from
    today's per-trigger decisions instead.
    """
    tz = ZoneInfo("America/New_York")
    report_date = ea.datetime.datetime.now(tz).date().isoformat()

    try:
        holdings = client.table("portfolio_positions").select("ticker").execute().data or []
    except Exception:
        return  # cannot determine capacity → say nothing rather than guess
    held = len(holdings)
    free = ea.MAX_POSITIONS - held
    if free <= 0:
        return

    if _slot_report_already_sent(client, report_date):
        return

    if standdown_reason:
        body = standdown_reason
    else:
        lines = _summarize_today_skips(client, ea.trigger_audit._today())
        body = ("\n".join(lines) if lines
                else "No breakout candidate cleared all buy gates today.")

    delivered = False
    try:
        delivered = ea.notifier.notify_unfilled_slots(
            free, ea.MAX_POSITIONS, held, body)
    except Exception as e:
        print(f"   ⚠️ Unfilled-slot notification failed (non-fatal): {e}")
        delivered = False

    if delivered:
        _slot_report_mark_sent(client, report_date, body)


def _earnings_blackout_days_until(next_earnings_date, today) -> int | None:
    """Trading days from `today` until the ticker's next earnings, or None.

    None means "no known upcoming earnings" — missing, unparseable, or a date
    already in the past — and the caller FAILS OPEN on None: a per-name data gap
    must never block every buy. Returns 0 when earnings is today. Uses NYSE
    trading days (via ``ea.trading_days_between``) so a weekend/holiday does not
    make an imminent report look further away than it is.
    """
    if not next_earnings_date:
        return None
    try:
        edate = ea.datetime.date.fromisoformat(str(next_earnings_date)[:10])
    except (ValueError, TypeError):
        return None
    if edate < today:
        return None
    return ea.trading_days_between(today, edate)


@capture.capture_phase("buy")
def run_market_open_buys(ib: IB):
    """Checks for daily breakout triggers and executes buy orders at market open."""
    print("⏳ Running Market Open Buy checks...")
    client = ea.get_supabase_client()

    from broker_positions import BrokerPositionError, require_no_short_positions
    try:
        require_no_short_positions(ib, ea.get_ibkr_account(ib))
    except BrokerPositionError as exc:
        message = f"BUY SAFETY BLOCK: {exc}"
        print(f"🚨 {message}")
        capture.emit("capture_gap", area="broker_positions", reason=str(exc), complete=False)
        ea.notifier.notify_error(message)
        return

    # ── Schema degradation hard block ─────────────────────────────────────────
    # If a column a live risk rule depends on is missing, that rule is silently
    # inert (see schema_guard). Opening NEW positions while the controls meant to
    # protect them are impaired is the specific mistake this prevents. Existing
    # positions continue to be monitored and exited normally.
    #
    # Re-checked every cycle (it is a handful of LIMIT 1 queries), so applying the
    # migration clears this automatically without restarting the container.
    if not assert_schema_ok(client):
        capture.emit("buy_gate", gate="schema", passed=False,
                     subsequent_inputs="not_evaluated", complete=False)
        maybe_report_unfilled_slots(
            client,
            standdown_reason=("Schema degraded — a column a live risk rule depends "
                              "on is missing, so new buys are blocked until the "
                              "pending migration is applied in Supabase."))
        return

    capture.emit("buy_gate", gate="schema", passed=True)
    # ── Margin-loan hard block ────────────────────────────────────────────────
    # Before evaluating any triggers, verify we are investing only our own money.
    # If TotalCashValue is negative, IBKR has lent us money and we must not buy
    # anything until the margin balance is restored to zero.
    margin_loan = ea.get_margin_loan(ib)
    capture.emit("buy_gate", gate="margin", passed=margin_loan <= 0,
                 margin_loan=margin_loan, subsequent_inputs="not_evaluated")
    if margin_loan > 0:
        msg = (
            f"🚨 *MARGIN LOAN ACTIVE — Buys Blocked*\n"
            f"IBKR TotalCashValue is negative.\n"
            f"Margin borrowed: ${margin_loan:,.2f}\n"
            f"No new positions will be opened until the loan is fully repaid.\n"
            f"Action: check IBKR account and repay or close positions to reduce margin."
        )
        print(f"🚨 MARGIN LOAN ACTIVE (${margin_loan:,.2f} borrowed). "
              f"All buys blocked for this cycle.")
        try:
            ea.notifier.notify_error(msg)
        except Exception:
            pass
        maybe_report_unfilled_slots(
            client,
            standdown_reason=(f"Margin loan active (${margin_loan:,.2f} borrowed) — "
                              f"all new buys are blocked until the loan is repaid."))
        return

    # ── Market direction hard gate (fail-closed on data errors) ─────────────────
    if ea.MARKET_DIRECTION_FILTER_ENABLED and not ea.is_market_bullish():
        capture.emit("buy_gate", gate="market", passed=False,
                     subsequent_inputs="not_evaluated", complete=False)
        print("📊 Market bearish (benchmark below SMA-200 buffer, falling SMA-200, "
              "or data unavailable). Standing down from new buys.")
        maybe_report_unfilled_slots(
            client,
            standdown_reason=("Market direction is bearish — a benchmark index is "
                              "below its SMA-200 buffer, its SMA-200 is falling, or "
                              "the data was unavailable. The bot stands down from "
                              "new buys (CAN SLIM 'M' gate)."))
        return

    capture.emit("buy_gate", gate="market", passed=True,
                 enabled=ea.MARKET_DIRECTION_FILTER_ENABLED)
    
    # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
    tz = ZoneInfo("America/New_York")
    today_ny = ea.datetime.datetime.now(tz).date()
    today_str = today_ny.strftime("%Y-%m-%d")
    recent_date = (today_ny - ea.datetime.timedelta(days=ea.TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    
    try:
        triggers_res = client.table("daily_triggers").select("*").gte("triggered_at", recent_date).execute()
        triggers = triggers_res.data
        capture.emit("candidate_universe", phase="buy", triggers=triggers or [],
                     complete=True, lookback_start=recent_date)
        # Rank highest-conviction first (final_score, then quality_score, then
        # ai_rating). Single source shared with the backtester (Phase 1 parity).
        triggers = dc.rank_triggers(triggers)
    except Exception as e:
        ea.notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
        print(f"❌ Failed to fetch daily triggers: {e}")
        return

    if not triggers:
        print(f"😴 No primary breakouts in the last {ea.TRIGGER_LOOKBACK_DAYS} days.")
        
    # Get current holdings in portfolio_positions
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        holdings = portfolio_res.data
        capture.emit("source_snapshot", table="portfolio_positions", phase="buy",
                     positions=holdings or [], received_at=capture.now())
        capture.observe_portfolio(ib, holdings or [], "buy")
        active_tickers = [h["ticker"] for h in holdings]
    except Exception as e:
        ea.notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", e)
        print(f"❌ Failed to fetch portfolio positions: {e}")
        return


    # Check portfolio cap.
    stock_holdings = holdings
    if len(stock_holdings) >= ea.MAX_POSITIONS:
        print(f"❌ Portfolio is fully invested with {len(stock_holdings)} stock positions. Standing down.")
        # Every trigger today is foregone purely for lack of a slot. These rows
        # are what makes the opportunity cost of MAX_POSITIONS measurable.
        ea.trigger_audit.record_decisions_bulk(
            client, triggers, "SKIPPED", ea.trigger_audit.SLOTS_FULL,
            detail=f"Portfolio full at {len(stock_holdings)}/{ea.MAX_POSITIONS} before cycle",
            slots_free=0,
        )
        return

    cycle_cash_spent = 0.0
    initial_own_cash = ea.get_own_cash(ib)

    # Equity base for the per-position ceiling (equity / MAX_POSITIONS). Captured
    # once per cycle: NetLiquidation is stable as cash converts to shares within
    # the cycle. If IBKR's NetLiquidation tag is momentarily unavailable (returns
    # 0.0), reconstruct equity from cash + the IBKR-synced market value of current
    # holdings so the cap NEVER silently falls back to the uncapped formula that
    # caused the 2026-09-21 oversizing incident.
    initial_net_liq = ea.get_net_liquidation(ib)
    if initial_net_liq <= 0:
        held_value = sum(float(h.get("market_value") or 0.0) for h in holdings)
        initial_net_liq = initial_own_cash + held_value
        print(f"⚠️ NetLiquidation unavailable — reconstructed equity for position "
              f"cap: ${initial_net_liq:,.2f} (cash ${initial_own_cash:,.2f} + held "
              f"${held_value:,.2f}).")

    # Reason-aware cooling-off, computed ONCE for the cycle (single source in
    # cooling_off.py, shared with force_buy.py and rotate_positions.py). Maps a
    # blocked ticker -> the reason. A profit sale older than today is absent, so
    # it is eligible. See decisions/2026-09-26_reason-aware-cooling-off.md.
    try:
        cooled_map = ea.cooling_off.compute_cooled_map(client, today_ny, ea.COOLING_OFF_DAYS)
    except Exception as cool_err:
        ea.notifier.notify_exception("run_market_open_buys() — cooling_off", cool_err)
        print(f"   ⚠️ Cooling-off map failed: {cool_err} — allowing all buys this cycle.")
        cooled_map = {}

    # Snapshot the live thresholds once per cycle for the pure decision core.
    cfg = dc.config_from_module(ea)
    capture.emit("buy_context", cooled_map=cooled_map, initial_own_cash=initial_own_cash,
                 initial_net_liq=initial_net_liq, held_tickers=active_tickers)

    for trigger in triggers:
        ticker = trigger["ticker"]
        
        # Refresh active holdings from Supabase at top of loop
        try:
            portfolio_res = client.table("portfolio_positions").select("*").execute()
            holdings = portfolio_res.data or []
            active_tickers = [p["ticker"] for p in holdings]
        except Exception:
            pass

        # Don't buy a stock we already hold
        # ── Price-independent eligibility ladder (gates 1–6) ──────────────────
        # already-held → cooling-off → AI D-grade veto → earnings blackout →
        # no-AI-score (fail closed) → score floor. Single source shared with the
        # backtester: decision_core.evaluate_eligibility.
        #
        # next_earnings_date → trading-days-to-earnings needs the NYSE calendar,
        # so it is computed here (I/O-adjacent) and passed into the pure gate.
        # See decisions/2026-09-26_reason-aware-cooling-off.md (cooling-off) and
        # decisions/2026-09-28_earnings-blackout-and-news-veto.md (earnings).
        days_to_earnings = _earnings_blackout_days_until(
            trigger.get("next_earnings_date"), today_ny)
        elig = dc.evaluate_eligibility(
            trigger,
            held_tickers=active_tickers,
            cooled_map=cooled_map,
            days_to_earnings=days_to_earnings,
            cfg=cfg)
        capture.emit("eligibility", ticker=ticker, trigger=trigger,
                     held_tickers=active_tickers, cooled_map=cooled_map,
                     days_to_earnings=days_to_earnings, action=elig.action,
                     reason_code=elig.reason_code)
        if elig.action != dc.PROCEED:
            _print_skip(ticker, elig)
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", elig.reason_code,
                detail=elig.detail, **elig.audit)
            continue

        # Passed the eligibility ladder. Surface the AI grade for the operator and
        # keep the two values the BOUGHT audit row records downstream.
        ai_grade = trigger.get("ai_grade")
        if ai_grade:
            print(f"   🟢 {ticker} AI grade: {ai_grade} | "
                  f"quality={trigger.get('quality_score', 'N/A')} | "
                  f"final={trigger.get('final_score', 'N/A')}")
        trigger_type = str(trigger.get("trigger_type") or "BREAKOUT")
        candidate_score = dc.candidate_score_of(trigger)
        min_score = dc.min_score_for(trigger_type, cfg)

        # Size the position as an equal share of remaining capital across unfilled slots.
        # Deduct cash spent on filled orders in the current cycle from initial_own_cash.
        available_cash = max(0.0, initial_own_cash - cycle_cash_spent)
        live_own_cash = ea.get_own_cash(ib)
        available_cash = min(available_cash, live_own_cash)

        stock_held_count = len(holdings)
        remaining_slots = max(1, ea.MAX_POSITIONS - stock_held_count)
        print(f"💰 Own Cash (margin-free) in IBKR: ${available_cash:,.2f} (initial: ${initial_own_cash:,.2f}, spent this cycle: ${cycle_cash_spent:,.2f})")
        uncapped_size = available_cash / remaining_slots
        position_size = equity_capped_position_size(
            available_cash, remaining_slots, initial_net_liq, ea.MAX_POSITIONS)
        equity_cap = (initial_net_liq / ea.MAX_POSITIONS) if initial_net_liq > 0 else None
        if equity_cap is not None and uncapped_size > equity_cap + 0.005:
            print(f"   Position sizing: ${available_cash:,.2f} / {remaining_slots} slot(s) = "
                  f"${uncapped_size:,.2f}, CAPPED to equal-weight ${equity_cap:,.2f} "
                  f"(equity ${initial_net_liq:,.2f} / {ea.MAX_POSITIONS}) "
                  f"(${ea.PRICE_SAFETY_RESERVE:,.0f} safety reserve applied at share count)")
        else:
            print(f"   Position sizing: ${available_cash:,.2f} / {remaining_slots} slot(s) = ${position_size:,.2f} per position (${ea.PRICE_SAFETY_RESERVE:,.0f} safety reserve applied at share count)")

        # Double check active holdings size again (gate 7: capacity)
        cap = dc.evaluate_capacity(stock_held_count, cfg)
        if cap.action == dc.HALT_CAPACITY:
            print(f"🚫 Portfolio capacity ({ea.MAX_POSITIONS} stocks) reached during loop. Skipping further buys.")
            ea.trigger_audit.record_decisions_bulk(
                client, triggers[triggers.index(trigger):], "SKIPPED",
                cap.reason_code, detail=cap.detail, slots_free=0)
            break

        # Gate 8: insufficient cash
        cash_dec = dc.evaluate_cash(available_cash, remaining_slots, cfg)
        if cash_dec.action != dc.PROCEED:
            print(f"🚫 Insufficient cash to buy {ticker} (floor: ${ea.MIN_POSITION_SIZE:,.0f}). Skipping.")
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", cash_dec.reason_code,
                detail=cash_dec.detail, **cash_dec.audit)
            continue

        # ── Market-confirmation gates (9 volume surge, 10 pre-breakout pivot
        # distance). Single source: decision_core.evaluate_market_gates. The
        # volume_surge column is overloaded (raw surge for BREAKOUT rows, a
        # CONTRACTION ratio for PRE_BREAKOUT where lower is better), so the gate
        # applies the minimum to confirmed BREAKOUT rows only — gating the
        # contraction ratio would invert the selection. ──────────────────────────
        market_dec = dc.evaluate_market_gates(trigger, cfg)
        if market_dec.action != dc.PROCEED:
            _print_skip(ticker, market_dec)
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", market_dec.reason_code,
                detail=market_dec.detail, **market_dec.audit)
            continue
        # Buy reason tags the trigger source
        buy_reason = f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x"
        buy_source = "daily_triggers"

        print(f"🚀 Execution Trigger: Initiating purchase for {ticker}...")

        # ── Qualify contract first so we can request IBKR's live price ────────
        # Contract must be qualified before reqTickers(); done here (not inside
        # the order try block) so the price is available for share sizing.
        contract = Stock(ticker, 'SMART', 'USD')
        try:
            ib.qualifyContracts(contract)
        except Exception as _qe:
            print(f"   ⚠️ Contract qualification failed for {ticker}: {_qe}. Halting buy loop.")
            ea.notifier.notify_buy_failure(ticker=ticker, shares=0, error=_qe)
            ea.notifier.notify_buy_loop_halted(ticker=ticker, reason=str(_qe))
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", ea.trigger_audit.LOOP_HALTED,
                detail=f"Contract qualification failed: {_qe}"[:500])
            break

        # -- Get price from IBKR (delayed market data) --
        # FMP's /stable/quote returns yesterday's close at market open, lagging
        # actual prices by 5-10%+ for gap-up stocks -- the root cause of Error 201.
        #
        # IBKR delayed market data (reqMarketDataType=3) is free for all accounts
        # and returns actual IBKR traded prices with a 15-20 min lag.
        ibkr_price, price_method = ea.fetch_ibkr_delayed_price(ib, contract)

        if ibkr_price > 0:
            current_price = ibkr_price
            price_source  = f"IBKR ({price_method})"
        else:
            # IBKR delayed price unavailable — fall back to previous close from screener.
            # Do NOT use FMP here: FMP /stable/quote returns yesterday's close at market
            # open, causing the same 5-10% lag issue we're trying to avoid.
            current_price = float(trigger["close_price"])
            price_source  = "prev close (IBKR delayed unavailable)"
        capture.emit("candidate_quote", ticker=ticker, price=current_price,
                     source=price_source, received_at=capture.now(),
                     available_cash=available_cash, position_size=position_size,
                     equity=initial_net_liq, held_count=stock_held_count)
        if current_price <= 0:
            print(f"   ⚠️ No valid price for {ticker} — skipping.")
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", ea.trigger_audit.NO_PRICE,
                detail=f"no valid price (source: {price_source})")
            continue
        print(f"   📡 {ticker} price: ${current_price:.2f} (source: {price_source})")

        # ── Price-dependent gates (11 extension ceiling, 12 breakdown floor,
        # 13 share count). Single source: decision_core.evaluate_price_gates.
        # A BUY decision carries the share count; a SKIP carries its reason. ──────
        pivot_price = float(trigger["close_price"])
        price_dec = dc.evaluate_price_gates(
            trigger, current_price, pivot_price, position_size, cfg)
        if price_dec.action == dc.SKIP:
            _print_skip(ticker, price_dec)
            audit = dict(price_dec.audit)
            # SHARES_ZERO historically also recorded available_cash; preserve it.
            if price_dec.reason_code == ea.trigger_audit.SHARES_ZERO:
                audit["available_cash"] = available_cash
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "SKIPPED", price_dec.reason_code,
                detail=price_dec.detail, **audit)
            continue

        shares = price_dec.shares
        extension_pct = price_dec.audit.get("extension_pct", 0.0)
        print(f"   ✅ {ticker} within buy zone: {extension_pct*100:.1f}% above pivot ${pivot_price:.2f} "
              f"(max {ea.MAX_PIVOT_EXTENSION*100:.0f}%) → {shares} shares")

        # Place market buy order on IBKR
        try:
            require_no_short_positions(ib, ea.get_ibkr_account(ib))
        except BrokerPositionError as exc:
            message = f"BUY SAFETY BLOCK before {ticker} submission: {exc}"
            print(f"🚨 {message}")
            capture.emit("capture_gap", area="broker_positions", reason=str(exc), complete=False)
            ea.notifier.notify_error(message)
            return
        try:
            # Note: contract already qualified above
            # 1. Market Order Entry
            order = MarketOrder('BUY', shares)
            order.tif = 'DAY'   # explicit DAY prevents IBKR error 10349 (preset TIF warning)
            order.account = ea.get_ibkr_account(ib)
            
            print(f"   Submitting Market Order for {shares} shares of {ticker}...")
            trade = ib.placeOrder(contract, order)
            capture.record_order(trade, "market_buy_submitted")

            print(f"   Waiting for fill on {shares} shares of {ticker}...")
            for _ in range(60):
                ib.sleep(1)
                status = trade.orderStatus.status
                filled_so_far = int(trade.orderStatus.filled)
                if status == 'Filled':
                    break
                elif status in ('Cancelled', 'Inactive'):
                    if filled_so_far == 0:
                        # Grace period: fill confirmation may still be in-flight
                        # (race condition where IBKR warning/cancel arrives before fill ack)
                        ib.sleep(2)
                        if int(trade.orderStatus.filled) > 0:
                            print(f"   ℹ️ {ticker}: fill arrived after cancel event — proceeding with position.")
                    break

            if trade.orderStatus.status != 'Filled':
                print(f"   ⚠️ {ticker} order not fully filled or was rejected. Cancelling remaining.")
                ib.cancelOrder(order)
                ib.sleep(2)

            actual_shares = int(trade.orderStatus.filled)
            if actual_shares == 0:
                reject_msgs = [entry.message for entry in trade.log if getattr(entry, 'message', '')]
                reject_msg = " | ".join(reject_msgs) if reject_msgs else "No explicit IBKR message (Order timed out, zero liquidity, or halted)"

                print(f"   ⚠️ {ticker} order had 0 shares filled. Reason: {reject_msg}")
                ea.notifier.notify_buy_failure(ticker=ticker, shares=shares,
                    error=f"IBKR Log: {reject_msg}")
                # Stop the entire buy loop — do NOT attempt the next ranked stock.
                # Skipping to the next ticker would change portfolio construction
                # priority and is worse than halting for manual intervention.
                ea.notifier.notify_buy_loop_halted(ticker=ticker, reason=reject_msg)
                ea.trigger_audit.record_trigger_decision(
                    client, trigger, "SKIPPED", ea.trigger_audit.BUY_FAILED,
                    detail=f"0 shares filled: {reject_msg}"[:500],
                    candidate_score=candidate_score, price=current_price,
                    shares=0)
                break

            fill_price = round(trade.orderStatus.avgFillPrice, 2)
            if fill_price <= 0:
                fill_price = current_price

            actual_cost = actual_shares * fill_price
            cycle_cash_spent += actual_cost
            print(f"   💳 Cycle cash spent updated: +${actual_cost:,.2f} (total spent this cycle: ${cycle_cash_spent:,.2f})")

            # Calculate dynamic stop loss percentage (2.5x ATR, fallback to STOP_LOSS_PCT)
            # Floor: 7% (never tighter than static, protects against bad fills)
            # Cap:  14% (prevents runaway stops on extremely volatile names)
            trigger_atr_pct = trigger.get("atr_pct")
            if trigger_atr_pct and float(trigger_atr_pct) > 0:
                atr_derived = round((2.5 * float(trigger_atr_pct)) / 100.0, 4)
                # Band tracks STOP_LOSS_PCT rather than hard-coding 0.07, so
                # widening the base stop cannot be silently undone here.
                pos_stop_loss_pct = round(max(ea.STOP_LOSS_PCT, min(ea.ATR_STOP_MAX_PCT, atr_derived)), 4)
                stop_method = f"ATR-based ({float(trigger_atr_pct):.2f}% ATR × 2.5)"
            else:
                pos_stop_loss_pct = ea.STOP_LOSS_PCT
                stop_method = "static fallback"

            stop_loss_val = round(fill_price * (1 - pos_stop_loss_pct), 2)

            # ── Record position in Supabase FIRST ─────────────────────────────
            # CRITICAL: insert BEFORE place_trailing_stop() so that any exception
            # from stop placement cannot leave the position phantom-filled in IBKR
            # but absent from the DB. A missing DB entry fools the capacity check
            # into allowing extra buy orders (which IBKR then cancels for
            # insufficient buying power). Recording first makes this atomic from
            # the capacity-counting perspective.
            position_data = {
                "ticker":     ticker,
                "shares":     actual_shares,
                "buy_price":  fill_price,
                "buy_reason": f"CANSLIM Breakout [daily_triggers]: Vol Surge {trigger['volume_surge']}x",
                "buy_source": buy_source,
                "stop_loss_pct": pos_stop_loss_pct,
                "hwm_date":   ea.datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
                "highest_unrealized_pct": 0.0,
                # ── Entry conviction snapshot (all 5-component scores) ─────────
                # Copied from the daily_triggers row so the Open Positions UI and
                # future rotation analysis have the full picture at entry time.
                "entry_quality_score":    trigger.get("quality_score"),
                "entry_ai_rating":        trigger.get("ai_rating"),
                "entry_ai_grade":         trigger.get("ai_grade"),
                "entry_final_score":      trigger.get("final_score"),
                "entry_technical_score":  trigger.get("technical_score"),
                "entry_liquidity_score":  trigger.get("liquidity_score"),
                "entry_rs_score":         ea._get_entry_rs(ticker, trigger.get("rs_score")),
                "entry_sentiment_score":  trigger.get("sentiment_score"),
                "entry_atr_pct":          trigger.get("atr_pct"),
                "entry_est_days_target":  trigger.get("est_days_to_target"),
                "entry_score_rationale":  trigger.get("score_rationale"),
                # new: breakout signal baselines for PARAM_DRIFT analysis
                "entry_volume_surge":         trigger.get("volume_surge"),
                "entry_pivot_distance_pct":   trigger.get("pivot_distance_pct"),
                # hwm_price starts at fill price; ratchets up in monitor_portfolio_intraday
                "hwm_price": fill_price,
            }
            client.table("portfolio_positions").insert(position_data).execute()
            # The Phase 1 static backstop this position opens with. Computed once
            # so the persisted column and the ORDER placed below cannot disagree —
            # a mismatch would make the first monitor cycle see a phantom change
            # and needlessly re-place the bracket.
            _entry_hard = ea.hard_stop_price(
                {"closed_above_entry": False, "buy_price": fill_price},
                fill_price, 0.0, False, 0)
            # hard_stop_price is written as a best-effort follow-up (never in the
            # insert above) so a lagging migration cannot fail the insert and
            # leave a phantom-filled position. The static hard-stop ORDER is
            # placed regardless below; this column only drives ratchet/display.
            try:
                client.table("portfolio_positions").update(
                    {"hard_stop_price": _entry_hard}
                ).eq("ticker", ticker).execute()
            except Exception as _hs_err:
                if not ("PGRST204" in str(_hs_err) or "hard_stop_price" in str(_hs_err)):
                    print(f"   ⚠️ {ticker}: could not persist hard_stop_price: {_hs_err}")
            # Entry commission is written as a follow-up update, never as part of
            # the insert above -- see record_buy_commission() for why.
            ea.record_buy_commission(client, ib, ticker, trade)
            print(f"✅ Successfully bought {actual_shares} shares of {ticker} at ${fill_price:.2f}.")
            print(f"   Stop-Loss: ${stop_loss_val} | Trail: {pos_stop_loss_pct*100:.2f}% (IBKR-managed)")

            # The positive class for the counterfactual: this trigger's score is
            # paired with an actual outcome, while the SKIPPED rows are not.
            ea.trigger_audit.record_trigger_decision(
                client, trigger, "BOUGHT", ea.trigger_audit.BOUGHT,
                detail=f"Filled {actual_shares} @ ${fill_price:.2f}",
                candidate_score=candidate_score, min_score=min_score,
                price=fill_price, extension_pct=extension_pct,
                available_cash=available_cash, shares=actual_shares)

            # Update loop capacity state immediately after DB write.
            # Must happen before notify_buy so the tracker is correct even if
            # the Telegram call raises an exception.
            active_tickers.append(ticker)
            portfolio_res = client.table("portfolio_positions").select("ticker").execute()
            holdings = portfolio_res.data or []
            slot_used = len(holdings)

            # ── Attach protective bracket (isolated try/except) ───────────────
            # Trailing stop (profit-locking) + static hard stop (disconnect-proof
            # max-loss floor) in one OCA group. Wrapped separately so a placement
            # failure never prevents the position from being recorded above or
            # the loop from continuing.
            try:
                # Phase 1 static backstop (computed above, alongside the column
                # write, so order and column always agree).
                ea.place_protective_stops(ib, contract, actual_shares,
                                       pos_stop_loss_pct, _entry_hard,
                                       ea.get_ibkr_account(ib))
            except Exception as stop_err:
                print(f"   ⚠️ Protective stop placement failed for {ticker}: {stop_err} — position recorded, manual stop required.")
                ea.notifier.notify_exception("place_protective_stops() — execution_agent.py", stop_err)

            # Notify all configured Telegram recipients
            ea.notifier.notify_buy(
                ticker=ticker, shares=actual_shares, fill_price=fill_price,
                stop_loss=stop_loss_val,
                trail_pct=pos_stop_loss_pct,
                stop_method=stop_method,
                volume_surge=float(trigger.get("volume_surge", 0)),
                pivot_dist_pct=float(trigger.get("pivot_distance_pct", 0)),
                slot_used=slot_used, max_slots=ea.MAX_POSITIONS
            )

        except Exception as order_err:
            ea.notifier.notify_exception(f"run_market_open_buys() — execution_agent.py", order_err)
            print(f"❌ Failed to execute order for {ticker}: {order_err}")
            ea.notifier.notify_buy_failure(ticker=ticker, shares=shares, error=order_err)
            # Stop the entire buy loop — same reasoning as the 0-fill case above.
            ea.notifier.notify_buy_loop_halted(ticker=ticker, reason=str(order_err))
            break

    # ── End-of-cycle: explain any slots still empty (once per ET day) ──────────
    # Reached after a normal pass through the trigger loop (including the empty
    # loop when there were no triggers, and the mid-cycle capacity break). The
    # early `return`s above report their own single stand-down reason; this call
    # aggregates the per-trigger skip reasons for the day.
    if not triggers:
        maybe_report_unfilled_slots(
            client,
            standdown_reason=(f"The screener produced no breakout triggers in the "
                              f"last {ea.TRIGGER_LOOKBACK_DAYS} days, so there was "
                              f"nothing to buy."))
    else:
        maybe_report_unfilled_slots(client)
