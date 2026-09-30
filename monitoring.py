"""Intraday position monitoring orchestrator, extracted from execution_agent.py
(see decisions/2026-09-27_execution-agent-modular-split.md).

SAFETY INVARIANT: this is the money path. Every patched sibling (execute_sell,
execute_scale_out, arm_exit, place_protective_stops, get_position_price, the
prove-it/exit-shadow helpers, ...) and the frozen ``execution_agent.datetime``
clock are referenced via ``ea.<name>`` so the exit-path tests and the
tests/golden_log.py characterization harness keep observing every seam. Do NOT
bind any of these names locally.
"""
from zoneinfo import ZoneInfo
from supabase import Client
from ib_insync import IB, Stock

from execution_agent_ref import ea
import intraday_capture as capture

@capture.capture_phase("monitor")
def monitor_portfolio_intraday(ib: IB):
    """Monitors open positions: updates hwm_date, self-heals trailing stops,
    applies the MA exit, and runs EOD plateau rotation."""
    print("🔍 Running Intraday Portfolio Monitoring...")
    client = ea.get_supabase_client()

    # ── Fetch open positions ────────────────────────────────────────────────────
    try:
        portfolio_res = client.table("portfolio_positions").select("*").execute()
        positions = portfolio_res.data or []
        capture.emit("source_snapshot", table="portfolio_positions", phase="monitor",
                     positions=positions, received_at=capture.now())
        capture.observe_portfolio(ib, positions, "monitor")
    except Exception as e:
        ea.notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
        print(f"❌ Could not fetch portfolio positions: {e}")
        return

    tz = ZoneInfo("America/New_York")
    now_ny = ea.datetime.datetime.now(tz)
    today_ny = now_ny.date()
    # Track intraday prices per-ticker in memory so hwm_date comparisons are
    # relative to the last price we polled (not the stored HWM price, which
    # IBKR now owns).
    intraday_peak: dict = {}
    # Positions whose exit is owned by a Smart OCA request. Every automated rule
    # below funnels into execute_sell()/arm_exit(), and both cancel all open SELL
    # orders for the ticker — which would wipe out the OCA. Skip them entirely;
    # process_exit_requests() governs these positions.
    oca_managed = ea.get_oca_managed_tickers(client)
    capture.emit("monitor_context", positions=positions, oca_managed=list(oca_managed),
                 evaluated_at=now_ny.isoformat())

    # Single consistent IBKR price snapshot for this cycle. Every position is
    # priced from PortfolioItem.marketPrice (the broker's own mark we trade
    # against) via get_position_price(); FMP is only a per-ticker fallback when
    # a mark is missing. Read once here — ib.portfolio() is a non-blocking
    # in-memory lookup, so this does not risk the reqTickers() stall.
    ib_price_map = ea.build_ibkr_price_map(ib)

    active_positions = []
    for pos in positions:
        ticker     = pos["ticker"]
        shares     = int(pos["shares"])
        buy_price  = float(pos["buy_price"])
        buy_reason = pos.get("buy_reason", "Unknown")
        try:
            buy_date = ea.datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
            buy_date_d = buy_date.date()
        except Exception:
            buy_date_d = today_ny

        # Calculate trading days held
        days_held = ea.trading_days_between(buy_date_d, today_ny)

        # IBKR-first price: use the broker's own mark (PortfolioItem.marketPrice)
        # that we actually fill against, so exit decisions and fills agree. Falls
        # back to FMP only when IBKR has no usable mark for this ticker. The map
        # was built once above from the non-blocking ib.portfolio() cache.
        current_price, price_source = ea.get_position_price(ib, ticker, ib_price_map)
        capture.emit("monitor_observation", ticker=ticker, position=pos,
                     price=current_price, source=price_source, days_held=days_held,
                     oca_managed=ticker in oca_managed, received_at=capture.now())
        if current_price <= 0:
            print(f"   ⚠️ Could not fetch price for {ticker} — skipping this cycle.")
            active_positions.append(pos)
            continue

        pos_stop_loss_pct = float(pos.get("stop_loss_pct") or ea.STOP_LOSS_PCT)

        if ticker in oca_managed:
            print(f"   🎯 {ticker}: Smart OCA exit active — automated exit rules "
                  f"suspended (OCA + floor/expiry backstops govern this position).")
            active_positions.append(pos)
            continue

        print(f"   Monitoring {ticker}: Current: ${current_price:.2f} ({price_source}) | Entry: ${buy_price:.2f} "
              f"| Held: {days_held}d | IBKR Trail: {pos_stop_loss_pct*100:.2f}%")

        # ── Armed Trailing Exit deadline check ───────────────────────────────────
        # A Day 0-6 sell signal already fired for this position and it was armed
        # with a tight IBKR trailing stop (see arm_exit()) instead of an instant
        # market sell, so it can capture a better exit price on any bounce.
        # If that trail hasn't already fired by the deadline, force the sell now
        # — we never hold longer than this bound chasing a better price.
        if pos.get("exit_armed"):
            try:
                armed_at = ea.datetime.datetime.fromisoformat(pos["exit_armed_at"].replace('Z', '+00:00'))
            except Exception:
                armed_at = now_ny
            hours_armed = (now_ny - armed_at).total_seconds() / 3600.0
            if hours_armed >= ea.ARMED_EXIT_DEADLINE_HOURS:
                reason = (
                    f"Armed Exit Deadline — {pos.get('exit_armed_reason', 'armed exit')} "
                    f"not stopped out after {hours_armed:.2f}h, forcing sell"
                )
                print(f"🚨 {ticker}: Armed Exit Deadline firing — {reason}")
                ea.execute_sell(ib, client, ticker, shares, buy_price, buy_date, buy_reason, current_price, reason, pos_row=pos)
            else:
                print(f"   \U0001f3af {ticker}: exit armed {hours_armed:.2f}h ago "
                      f"({pos.get('exit_armed_reason')}) — awaiting trail or deadline.")
                active_positions.append(pos)
            continue

        # ── Calculate current unrealized percentage ──
        unrealized_pct = round(((current_price / buy_price) - 1.0) * 100.0, 4)

        # ── Update highest_unrealized_pct in Supabase & memory ──
        prev_highest = float(pos.get("highest_unrealized_pct") or 0.0)
        highest_unrealized_pct = max(prev_highest, unrealized_pct)

        # ── Update hwm_date, hwm_price and highest_unrealized_pct when a new intraday high is seen ────
        stored_hwm = float(pos.get("hwm_price") or buy_price)
        prev_peak = max(stored_hwm, intraday_peak.get(ticker, buy_price))
        hwm_updated = False
        if current_price > prev_peak:
            intraday_peak[ticker] = current_price
            hwm_updated = True

        if hwm_updated or highest_unrealized_pct > prev_highest:
            try:
                update_payload = {
                    "highest_unrealized_pct": round(highest_unrealized_pct, 4)
                }
                if hwm_updated:
                    update_payload["hwm_date"]  = today_ny.isoformat()
                    update_payload["hwm_price"] = round(float(current_price), 4)

                client.table("portfolio_positions").update(update_payload).eq("ticker", ticker).execute()
                pos["highest_unrealized_pct"] = highest_unrealized_pct
            except Exception as e:
                err_str = str(e)
                # PGRST204 = column missing in schema cache (migration not yet run).
                # Degrade gracefully: write only hwm_date / hwm_price which always exist.
                # Do NOT fire Telegram — this is a deploy-time setup issue, not a bug.
                if "PGRST204" in err_str or "highest_unrealized_pct" in err_str:
                    print(f"   ⚠️ {ticker}: highest_unrealized_pct column missing — run migration. "
                          f"Writing hwm only.")
                    if hwm_updated:
                        try:
                            client.table("portfolio_positions").update({
                                "hwm_date":  today_ny.isoformat(),
                                "hwm_price": round(float(current_price), 4),
                            }).eq("ticker", ticker).execute()
                        except Exception as _inner:
                            print(f"   ⚠️ Could not update hwm for {ticker}: {_inner}")
                else:
                    ea.notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", e)
                    print(f"   ⚠️ Could not update hwm/peak metrics for {ticker}: {e}")


        # ── Dynamic trailing stop tightening ─────────────────────────────────────
        # Compute calendar days (not trading days) — time lever uses calendar.
        calendar_days = (today_ny - buy_date_d).days

        # ── O'Neil 8-Week Hold Rule ──────────────────────────────────────────────
        # Evaluated before the discretionary exits below so a qualifying leader is
        # protected from being trimmed on ordinary volatility. The trailing stop
        # placed with the position is untouched and still protects the downside.
        pos["highest_unrealized_pct"] = highest_unrealized_pct
        power_held = ea.maybe_arm_power_hold(client, pos, calendar_days) or \
            ea.is_power_hold_active(pos, calendar_days)
        if power_held:
            print(f"   🏆 {ticker}: power-hold active (day {calendar_days} of "
                  f"{ea.POWER_HOLD_DURATION_DAYS}) — discretionary exits suppressed.")

        # ── The Prove-It Stop ────────────────────────────────────────────────────
        # Resolve the level this position is protected at right now, then act on
        # it two ways: the bot arms a tight trailing exit if price is already
        # through the level, and the resting IBKR order is pinned to it below.
        #
        # Suppressed while power-held, which widens the trail deliberately. There
        # is no real conflict — power-hold requires a large peak gain, so such a
        # position is always proven and far above the Phase 2 floor.
        prove_it_level, prove_it_phase = (None, "power-hold") if power_held else \
            ea.prove_it_stop_level(pos, buy_price, days_held, highest_unrealized_pct)

        # ── Exit-rule shadow log (measurement only, NEVER an order) ───────────
        # Record what the two register-tracked candidates would do this cycle.
        # Fully guarded: any failure here must never touch the live decision.
        if ea.EXIT_SHADOW_LOG_ENABLED:
            try:
                shadow = ea.compute_exit_shadows(
                    pos, buy_price, current_price,
                    float(pos.get("hwm_price") or buy_price),
                    highest_unrealized_pct, prove_it_level, prove_it_phase,
                    days_held,
                )
                shadow["cycle_ts"] = now_ny.isoformat()
                client.table("exit_shadow_log").insert(shadow).execute()
            except Exception as _shadow_err:
                # Missing table (migration not yet applied) or any transient
                # error: degrade silently, do NOT fire Telegram, do NOT spam.
                es = str(_shadow_err)
                if not ("exit_shadow_log" in es or "PGRST" in es or "42P01" in es):
                    print(f"   ⚠️ exit-shadow log skipped for {ticker}: {es}")

        if (prove_it_level is not None
                and current_price <= prove_it_level
                and not pos.get("exit_armed")):
            if prove_it_phase == "phase1":
                band_pct = ea.prove_it_p1_threshold_pct(days_held) * 100.0
                reason = (
                    f"Prove-It Stop (Phase 1 — unproven) — Day {days_held}, "
                    f"never closed above entry and price "
                    f"{unrealized_pct:.2f}% <= -{band_pct:.1f}% of entry "
                    f"(${prove_it_level:.2f})"
                )
            else:
                reason = (
                    f"Prove-It Stop (Phase 2 — give-back floor) — Day {days_held}, "
                    f"peak +{highest_unrealized_pct:.2f}% gave back to "
                    f"{unrealized_pct:.2f}%, at or below the "
                    f"{ea.PROVE_IT_P2_FLOOR_PCT * 100:+.1f}% floor "
                    f"(${prove_it_level:.2f}). A green trade does not become a loss."
                )
            print(f"🚨 {ticker}: Prove-It Stop triggered — arming exit — {reason}")
            ea.arm_exit(ib, client, ticker, shares, current_price, reason, now_ny)
            ea.notifier.notify_prove_it_stop(
                ticker, buy_price, current_price, days_held,
                prove_it_phase, prove_it_level, highest_unrealized_pct,
            )
            active_positions.append(pos)
            continue

        # ── Partial Scale-Out (winner give-back reducer) ─────────────────────────
        # Evaluated AFTER the Prove-It firing check above, so a position already
        # through its give-back floor exits in full rather than being trimmed and
        # left for another 15-minute cycle. Only a winner still holding above the
        # floor reaches here.
        #
        # The first time this position's PEAK gain reaches +SCALE_OUT_TRIGGER_PCT,
        # book SCALE_OUT_FRACTION of the shares at market and let the remainder
        # ride the unchanged Prove-It stop. Booking part of the gain is a realised
        # profit a later fade cannot erase; leaving the stop untouched means the
        # winners are never clipped. Fires exactly once (scaled_out flag).
        #
        # Suppressed for power-held leaders — an O'Neil 8-week leader is precisely
        # the position we do NOT want to trim. In practice power-hold requires a
        # far larger peak (+POWER_HOLD_GAIN_PCT) than the scale trigger, so a
        # normal winner scales long before it could ever qualify.
        # PROVISIONAL (33-trade sample) — see decisions/2026-09-08_partial-scale-out.md
        # and the register in decisions/provisional_decisions.json.
        if (ea.SCALE_OUT_ENABLED
                and "scaled_out" in pos          # migration applied — flag can persist
                and not power_held
                and not pos.get("scaled_out")
                and highest_unrealized_pct >= ea.SCALE_OUT_TRIGGER_PCT * 100.0):
            _scale_shares = int(shares * ea.SCALE_OUT_FRACTION)
            if _scale_shares >= 1 and (shares - _scale_shares) >= 1:
                _did_scale = ea.execute_scale_out(
                    ib, client, pos, ticker, shares, _scale_shares,
                    buy_price, buy_date, buy_reason, current_price,
                    highest_unrealized_pct, pos_stop_loss_pct,
                    ea.hard_stop_price(pos, buy_price, highest_unrealized_pct, False,
                                    days_held),
                )
                if _did_scale:
                    shares = pos["shares"]   # reduced remainder for the rest of the loop
                    active_positions.append(pos)
                    continue
                # On failure the position's bracket has been restored; fall
                # through to normal management this cycle.

        # While power-held the profit ladder is bypassed entirely: the HWM profit
        # lock would otherwise clamp the trail to 1.5% from the peak from +5% gain
        # onward, long before the POWER_HOLD_GAIN_PCT that arms this rule, which made the rule
        # inert (every armed position still exited on the trail). Widen to
        # POWER_HOLD_TRAIL_PCT so the leader can actually run.
        if power_held:
            new_trail_pct = (
                ea.POWER_HOLD_TRAIL_PCT
                if pos_stop_loss_pct < ea.POWER_HOLD_TRAIL_PCT
                else None
            )
        else:
            new_trail_pct = ea._compute_dynamic_trail_pct(
                unrealized_pct, calendar_days, pos_stop_loss_pct,
                prove_it_pct=ea.prove_it_trail_pct(
                    prove_it_level, current_price, prove_it_phase
                ),
            )
        # ── Static hard-stop ratchet ─────────────────────────────────────────
        # The disconnect-proof max-loss floor. Ratchets UP only (except under
        # power-hold, which widens it back to the disaster level like the trail).
        desired_hard = ea.hard_stop_price(pos, buy_price, highest_unrealized_pct,
                                       power_held, days_held)
        stored_hard  = float(pos.get("hard_stop_price") or 0.0)
        # Never place a SELL stop at or above the market — it would trigger
        # instantly and liquidate at market. See safe_hard_stop().
        desired_hard = ea.safe_hard_stop(desired_hard, current_price, stored_hard)
        # Phase 1 is now carried by THIS static leg rather than the trailing one
        # (see hard_stop_price / decisions/2026-09-18_phase1-static-backstop.md).
        # The Phase 1 band widens once, day 0 -> day 1, by design, so the floor
        # must be allowed to follow it down that one time. Restricted to the
        # unproven phase: once proven, ratchet-up-only is restored in full, so a
        # green position's floor can still never loosen.
        _unproven = not ea.prove_it_is_proven(pos, highest_unrealized_pct)
        if power_held or _unproven:
            hard_changed = abs(desired_hard - stored_hard) >= 0.01
        else:
            hard_changed = desired_hard > stored_hard + 0.005   # ratchet up only

        # Re-place BOTH legs together whenever either changes, so the OCA pair
        # stays consistent (cancel_ticker_sell_orders clears the whole group).
        _bracket_replaced = False
        if new_trail_pct is not None or hard_changed:
            prev_trail_pct = pos_stop_loss_pct
            trail_to_place = new_trail_pct if new_trail_pct is not None else pos_stop_loss_pct
            widened = new_trail_pct is not None and new_trail_pct > prev_trail_pct
            try:
                _contract_tighten = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_contract_tighten)
                ea.cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _, confirmed_trail = ea.place_protective_stops(
                    ib, _contract_tighten, shares, trail_to_place,
                    desired_hard, ea.get_ibkr_account(ib)
                )
                _bracket_replaced = True
                try:
                    client.table("portfolio_positions").update(
                        {"stop_loss_pct": confirmed_trail, "hard_stop_price": desired_hard}
                    ).eq("ticker", ticker).execute()
                except Exception as _upd_err:
                    # Migration lag: hard_stop_price column absent. Still persist
                    # the trail so profit-locking is not lost; the hard-stop ORDER
                    # is already live at IBKR regardless.
                    if "PGRST204" in str(_upd_err) or "hard_stop_price" in str(_upd_err):
                        client.table("portfolio_positions").update(
                            {"stop_loss_pct": confirmed_trail}
                        ).eq("ticker", ticker).execute()
                        print(f"   ⚠️ {ticker}: hard_stop_price column missing — "
                              f"run migrations/20260907_add_hard_stop_price.sql.")
                    else:
                        raise
                pos_stop_loss_pct = confirmed_trail   # update in-memory for self-heal below
                pos["hard_stop_price"] = desired_hard
                if new_trail_pct is not None:
                    verb = "widened (power hold)" if widened else "tightened"
                    icon = "\U0001f3c6" if widened else "\U0001f512"
                    msg = (
                        f"{icon} <b>{ticker}</b> trail {verb}: "
                        f"{prev_trail_pct * 100:.1f}% → {confirmed_trail * 100:.1f}%\n"
                        f"Gain: +{unrealized_pct:.1f}% | Days held: {calendar_days}d\n"
                        f"New stop floor: ${current_price * (1 - confirmed_trail):.2f}\n"
                        f"Hard stop: ${desired_hard:.2f}"
                    )
                    ea.notifier._send(msg)
                    print(f"   {icon} {ticker}: trail {verb} "
                          f"{prev_trail_pct * 100:.1f}% → {confirmed_trail * 100:.1f}% "
                          f"(hard ${desired_hard:.2f}, +{unrealized_pct:.1f}% gain, {calendar_days}d)")
                elif hard_changed and stored_hard > 0:
                    # A genuine ratchet on a known prior value — worth announcing.
                    # The very first write (stored_hard == 0, migration/init) is
                    # logged silently to avoid a "$0.00 → $X" notification.
                    hverb = "widened (power hold)" if desired_hard < stored_hard else "ratcheted up"
                    ea.notifier._send(
                        f"\U0001f512 <b>{ticker}</b> hard stop {hverb}: "
                        f"${stored_hard:.2f} → ${desired_hard:.2f}\n"
                        f"Give-back floor now broker-guaranteed. "
                        f"Gain: +{unrealized_pct:.1f}% | {calendar_days}d held"
                    )
                    print(f"   \U0001f512 {ticker}: hard stop {hverb} "
                          f"${stored_hard:.2f} → ${desired_hard:.2f}")
                else:
                    print(f"   \U0001f512 {ticker}: hard stop initialised @ ${desired_hard:.2f}")
            except Exception as _tighten_err:
                ea.notifier.notify_exception(
                    "monitor_portfolio_intraday() protective-stop update", _tighten_err
                )
                print(f"   ⚠️ {ticker}: protective-stop update failed: {_tighten_err}")

        # ── Self-healing: ensure the protective bracket exists ──────────────────
        # A normal-state position (armed and Smart-OCA positions already
        # `continue`d above) should carry TWO GTC sells: the base trailing stop
        # and the static hard stop. GTC orders survive gateway restarts, but may
        # be absent for positions opened before this feature or after a reset. If
        # fewer than both legs are live, re-place the pair. A fired leg cancels
        # its OCA sibling and closes the position, so a self-healed position is
        # always one that is genuinely under-protected, not one mid-exit. If the
        # management block above already re-placed a fresh pair this cycle
        # (_bracket_replaced), skip — ib.openTrades() may not yet reflect the
        # just-placed legs, and re-placing twice in one cycle is wasteful.
        _open_sells = [
            t for t in ib.openTrades()
            if t.contract.symbol == ticker
            and t.order.action == 'SELL'
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'Inactive')
        ]

        if not _bracket_replaced and len(_open_sells) < 2:
            print(f"   🔧 {ticker}: protective bracket incomplete "
                  f"({len(_open_sells)}/2 legs) — re-placing (self-healing).")
            try:
                ea.cancel_ticker_sell_orders(ib, ticker)
                ib.sleep(1)
                _heal_contract = Stock(ticker, 'SMART', 'USD')
                ib.qualifyContracts(_heal_contract)
                _heal_hard = ea.hard_stop_price(pos, buy_price, highest_unrealized_pct,
                                             power_held, days_held)
                _heal_hard = ea.safe_hard_stop(_heal_hard, current_price,
                                            float(pos.get("hard_stop_price") or 0.0))
                # Anchor the trail from current price — IBKR tracks HWM from here.
                _grp, _confirmed = ea.place_protective_stops(
                    ib, _heal_contract, shares, pos_stop_loss_pct,
                    _heal_hard, ea.get_ibkr_account(ib)
                )
                try:
                    client.table("portfolio_positions").update(
                        {"stop_loss_pct": _confirmed, "hard_stop_price": _heal_hard}
                    ).eq("ticker", ticker).execute()
                except Exception as _upd_err:
                    if "PGRST204" in str(_upd_err) or "hard_stop_price" in str(_upd_err):
                        client.table("portfolio_positions").update(
                            {"stop_loss_pct": _confirmed}
                        ).eq("ticker", ticker).execute()
                    else:
                        raise
                pos["hard_stop_price"] = _heal_hard
            except Exception as _heal_err:
                ea.notifier.notify_exception("monitor_portfolio_intraday() — execution_agent.py", _heal_err)
                print(f"   ⚠️ Self-healing failed for {ticker}: {_heal_err}")

        # Trailing stop is fully managed by IBKR. reconcile_with_ibkr() (Case 1)
        # detects when it fires and archives the position to trade_history.

        # ── Sell-state transition notice ─────────────────────────────────────────
        # Telegram a concise note whenever the governing exit regime changes
        # (Unproven → Proven, give-back floor arming, profit-lock engaging). The
        # state is latched in portfolio_positions.sell_state so each transition
        # fires exactly once and survives restarts; Exiting / Power Hold are
        # tracked but announced by their own richer messages, not re-announced
        # here. Armed and Smart-OCA positions already `continue`d above.
        _state_code = ea.sell_state_code(pos, prove_it_phase, power_held, highest_unrealized_pct)
        ea.maybe_notify_sell_state(
            client, pos, ticker, _state_code,
            prove_it_level=prove_it_level, unrealized_pct=unrealized_pct,
            peak_pct=highest_unrealized_pct, days_held=days_held,
        )

        # Position remained active
        active_positions.append(pos)

    positions = active_positions

    # ── EOD Block (3:45–4:00 PM ET) ─────────────────────────────────────────────
    # Runs once per day at 3:45 PM. Implements:
    # 1. Update EOD metrics (days_held, days_since_hwm, volume_distribution_flag, live_rs_score, Mₜ)
    # 2. Day 3 Breakout Verdict (PASS/FAIL based on price +1% + volume >= 75% avg)
    # 3. Rank & Replace Swaps (Day 7+ only: auto-swap if Mₜ gap > 15pts vs best trigger)
    #
    now_eod = ea.datetime.datetime.now(tz)
    is_eod_window = (now_eod.hour == 15 and now_eod.minute >= 45)

    if is_eod_window:
        capture.emit("eod_latch", phase="eod", stage="start",
                     complete=False, marker_only=True)
        today_eod = ea.datetime.datetime.now(tz).date()

        # Fetch today's triggers (or triggers from the last 3 days to handle weekends/holidays)
        try:
            recent_date = (ea.datetime.datetime.now(tz) - ea.datetime.timedelta(days=ea.TRIGGER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
            triggers_res = client.table("daily_triggers") \
                .select("*") \
                .gte("triggered_at", recent_date) \
                .execute()
            capture.emit("candidate_universe", phase="eod", triggers=triggers_res.data or [],
                         complete=True, lookback_start=recent_date)
            held_tickers = {p["ticker"] for p in positions}
            fresh_triggers = [
                t for t in (triggers_res.data or [])
                if t["ticker"] not in held_tickers
            ]
            fresh_triggers.sort(
                key=lambda x: x.get("final_score") or x.get("quality_score") or x.get("ai_rating") or 0,
                reverse=True
            )
            fresh_tickers = {t["ticker"] for t in fresh_triggers}
            best_trigger       = fresh_triggers[0] if fresh_triggers else None
            best_trigger_score = (best_trigger.get("final_score") or 0) if best_trigger else 0
            best_ticker        = best_trigger["ticker"] if best_trigger else None
        except Exception:
            capture.emit("candidate_universe", phase="eod", triggers=None,
                         complete=False, reason="trigger_fetch_failed")
            fresh_triggers     = []
            fresh_tickers      = set()
            best_trigger_score = 0
            best_ticker        = None

        market_regime = ea._get_market_regime()

        # 1. Update EOD metrics for all open positions
        for pos in positions:
            ticker_m = pos["ticker"]
            hwm_str  = (pos.get("hwm_date") or str(today_eod))[:10]
            hwm_d_m  = ea.datetime.date.fromisoformat(hwm_str)
            dsm      = ea.trading_days_between(hwm_d_m, today_eod)
            
            try:
                buy_date_m = ea.datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
                buy_date_d_m = buy_date_m.date()
            except Exception:
                buy_date_d_m = today_eod
            days_held_m = ea.trading_days_between(buy_date_d_m, today_eod)
            
            live_rs  = ea._fetch_current_rs(ticker_m)

            ohlcv = ea._fetch_ohlcv(ticker_m, days=100)
            vol_dist = ea.check_volume_distribution(ticker_m, ohlcv)

            # ── Live sentiment re-score for Mₜ (FMP news + GPT-4o-mini) ──────
            # Only run at EOD — ~4 calls/day, negligible cost.
            live_sentiment = ea.fetch_held_position_sentiment(ticker_m)

            # ── Compute live Momentum Health Score Mₜ ────────────────────────
            # Temporarily inject live_rs into pos dict so compute_momentum_health_score
            # can read it. The real DB write happens in update_payload below.
            pos["live_rs_score"] = live_rs
            mt_score, mt_debug = ea.compute_momentum_health_score(
                pos, ohlcv, live_sentiment, days_held=days_held_m
            )
            capture.emit("eod_observation", ticker=ticker_m, position=pos,
                         days_held=days_held_m, ohlcv=ohlcv, live_rs=live_rs,
                         live_sentiment=live_sentiment, momentum_health_score=mt_score,
                         volume_distribution=vol_dist, received_at=capture.now())

            rsi_p    = mt_debug["rsi_penalty"]
            candle_p = mt_debug["candle_penalty"]
            penalties_str = ""
            if rsi_p != 0:
                penalties_str += f", RSI div {rsi_p:+.0f}"
            if candle_p != 0:
                penalties_str += f", candle {candle_p:+.0f}"

            print(f"   📊 {ticker_m}: Mₜ={mt_score:.1f} "
                  f"(RS {pos.get('entry_rs_score')}→{live_rs}, "
                  f"vol_dist={vol_dist}, sent={live_sentiment}"
                  f"{penalties_str})")

            # ── Day 3 Breakout Verdict (evaluated once at EOD of Day 3) ──────
            # PASS: close >= entry×1.01 AND Day 3 volume >= 75% of 20-day avg
            # FAIL: either condition not met → activates Intraday Loss Minimiser
            if days_held_m == 3 and pos.get("breakout_verdict") is None:
                current_price_m, verdict_source = ea.get_position_price(ib, ticker_m)
                capture.emit("eod_quote", ticker=ticker_m, price=current_price_m,
                             source=verdict_source, purpose="breakout_verdict",
                             received_at=capture.now())
                buy_price_m     = float(pos["buy_price"])
                price_pass      = current_price_m > buy_price_m * (1 + ea.BREAKOUT_VERDICT_MIN_GAIN)

                # _fetch_ohlcv() returns bars sorted ASCENDING (oldest first), so
                # ohlcv[-1] is today and ohlcv[-21:-1] is the prior 20 sessions.
                # This previously read ohlcv[0] / ohlcv[1:21] — i.e. a bar from
                # ~100 days ago compared against the 20 days after it. Both sides
                # were stale, so the ratio was ~1.0 and vol_pass was almost always
                # spuriously True, meaning the Day 3 verdict effectively tested
                # price only and FAIL almost never fired.
                day3_vol  = ohlcv[-1]["volume"] if ohlcv else None
                avg_vol   = (sum(b["volume"] for b in ohlcv[-21:-1]) / 20) if len(ohlcv) >= 21 else None
                vol_pass  = (day3_vol >= avg_vol * ea.BREAKOUT_VERDICT_MIN_VOL_PCT) if (day3_vol and avg_vol) else True

                verdict = "PASS" if (price_pass and vol_pass) else "FAIL"
                try:
                    client.table("portfolio_positions").update(
                        {"breakout_verdict": verdict}
                    ).eq("ticker", ticker_m).execute()
                    pos["breakout_verdict"] = verdict
                except Exception as _ve:
                    print(f"   \u26a0\ufe0f Could not write breakout_verdict for {ticker_m}: {_ve}")

                icon = "\u2705" if verdict == "PASS" else "\u274c"
                price_icon = "\u2705" if price_pass else "\u274c"
                vol_icon = "\u2705" if vol_pass else "\u274c"
                vol_str = f"{day3_vol/avg_vol:.2f}\u00d7" if day3_vol and avg_vol else "N/A"
                print(
                    f"   {icon} {ticker_m}: Day 3 verdict = {verdict} "
                    f"(price {price_icon} "
                    f"{((current_price_m/buy_price_m)-1)*100:+.2f}%, "
                    f"vol {vol_icon} "
                    f"{vol_str})"
                )
                if verdict == "FAIL":
                    ea.notifier.notify_breakout_verdict_fail(
                        ticker_m, buy_price_m, current_price_m, price_pass, vol_pass
                    )

            try:
                # ── Follow-through latch: the Prove-It phase discriminator ───
                # Latches True the first time the position CLOSES above entry,
                # and is never cleared — a breakout confirms only once.
                # Phase 1 applies only while this is False, which is what
                # confines the entry-anchored band to breakouts that never
                # followed through (unlike the old Intraday Loss Minimiser,
                # which cut working positions).
                # Once True it is never cleared — a breakout confirms only once.
                closed_above = bool(pos.get("closed_above_entry"))
                if not closed_above:
                    try:
                        eod_price, eod_source = ea.get_position_price(ib, ticker_m)
                        capture.emit("eod_quote", ticker=ticker_m, price=eod_price,
                                     source=eod_source, purpose="closed_above_entry",
                                     received_at=capture.now())
                        if eod_price and eod_price > float(pos["buy_price"]):
                            closed_above = True
                            print(f"   ✅ {ticker_m}: closed above entry — thesis stop disarmed.")
                    except Exception as _cae:
                        print(f"   ⚠️ Could not evaluate follow-through for {ticker_m}: {_cae}")

                update_payload = {
                    "days_since_hwm":           dsm,
                    "days_held":                days_held_m,
                    "live_rs_score":            live_rs,
                    "volume_distribution_flag": vol_dist,
                    "top_trigger_score":        best_trigger_score if fresh_tickers else None,
                    "momentum_health_score":    mt_score,
                    "live_sentiment_score":     live_sentiment,
                    "closed_above_entry":       closed_above,
                }
                client.table("portfolio_positions").update(update_payload).eq("ticker", ticker_m).execute()
                pos["days_since_hwm"]           = dsm
                pos["days_held"]                = days_held_m
                pos["live_rs_score"]            = live_rs
                pos["volume_distribution_flag"] = vol_dist
                pos["top_trigger_score"]        = best_trigger_score if fresh_tickers else None
                pos["momentum_health_score"]    = mt_score
                pos["live_sentiment_score"]     = live_sentiment
                pos["closed_above_entry"]       = closed_above
            except Exception as _me:
                # PGRST204 = closed_above_entry column missing (migration not run).
                # Retry without it so the rest of the EOD metrics still persist.
                if "PGRST204" in str(_me) or "closed_above_entry" in str(_me):
                    print(f"   ⚠️ {ticker_m}: closed_above_entry column missing — run "
                          f"migrations/20260809_add_closed_above_entry.sql. Writing other metrics.")
                    try:
                        update_payload.pop("closed_above_entry", None)
                        client.table("portfolio_positions").update(
                            update_payload).eq("ticker", ticker_m).execute()
                    except Exception as _me2:
                        print(f"   ⚠️ Could not update EOD plateau metrics for {ticker_m}: {_me2}")
                else:
                    print(f"   ⚠️ Could not update EOD plateau metrics for {ticker_m}: {_me}")

        capture.emit("source_snapshot", table="portfolio_positions", phase="eod_metrics",
                     positions=positions, received_at=capture.now(),
                     source="live_in_memory_after_updates")
        # 2. Rank & Replace Swaps (Day 7+ only)
        # Uses live Mₜ (momentum_health_score) as the comparator.
        # Only runs for positions held >= 7 days that passed the Day 3 verdict.
        if fresh_triggers and best_ticker and len(positions) >= ea.MAX_POSITIONS:
            for pos in positions:
                ticker_m  = pos["ticker"]
                days_held_rr = pos.get("days_held") or 0
                verdict_rr   = pos.get("breakout_verdict")

                # Rotate out of stalled Day 7+ positions. A FAIL verdict marks a
                # breakout that never confirmed, which makes it a BETTER rotation
                # candidate, not a worse one — yet it was previously excluded
                # here (`verdict_rr != "PASS"`), so the weakest positions were the
                # only ones that could never be swapped out. That exclusion made
                # some sense when a FAIL verdict handed the position to the
                # Intraday Loss Minimiser; with the minimiser disabled it left
                # FAIL positions with no rotation path at all.
                #
                # FAIL positions now rotate on a LOWER score gap than PASS ones,
                # since less evidence should be needed to abandon a breakout that
                # already failed to confirm.
                if days_held_rr < 7:
                    continue
                swap_threshold = (ea.RANK_REPLACE_THRESHOLD if verdict_rr == "PASS"
                                  else ea.RANK_REPLACE_FAIL_THRESHOLD)

                # ── Staleness discount (absorbs the retired Plateau Exit) ─────
                # A position that has not made a new high in STALE_EXIT_DAYS
                # trading days has stopped working. That used to trigger a sale
                # to CASH, which was the wrong destination — it gave up the
                # position's optionality whether or not anything better existed.
                #
                # The signal is real, so it is kept; only its consequence
                # changes. Staleness now lowers the bar for ROTATION, so a
                # stalled name is abandoned readily when a genuinely better
                # breakout has appeared and held indefinitely when none has.
                # See docs/retired_code.md.
                stale_days_rr = 0
                if days_held_rr >= ea.STALE_EXIT_MIN_DAYS_HELD:
                    try:
                        _hwm_raw_rr = pos.get("hwm_date")
                        if _hwm_raw_rr:
                            stale_days_rr = ea.trading_days_between(
                                ea.datetime.date.fromisoformat(str(_hwm_raw_rr)[:10]),
                                today_eod,
                            )
                    except Exception as _stale_err:
                        print(f"   ⚠️ Could not evaluate staleness for {ticker_m}: {_stale_err}")
                is_stale_rr = stale_days_rr >= ea.STALE_EXIT_DAYS
                if is_stale_rr:
                    swap_threshold = min(swap_threshold, ea.RANK_REPLACE_FAIL_THRESHOLD)

                # Never rotate out of a position protected by the 8-week hold rule:
                # a recent 20%-in-3-weeks leader is exactly what we want to keep.
                _rr_cal_days = (today_eod - ea.datetime.datetime.fromisoformat(
                    pos["buy_date"].replace('Z', '+00:00')
                ).date()).days
                if ea.is_power_hold_active(pos, _rr_cal_days):
                    print(f"   🏆 Rank & Replace skipped for {ticker_m} — 8-week hold active.")
                    continue

                mt = pos.get("momentum_health_score")
                comparator_score = mt if mt is not None else (
                    pos.get("entry_final_score") or pos.get("entry_quality_score") or 0
                )

                if best_trigger_score > comparator_score + swap_threshold:
                    mt_label = f"M\u209c={comparator_score:.1f}" if mt is not None else f"entry={comparator_score}"
                    stale_label = (f", stale {stale_days_rr}d — swap bar lowered to "
                                   f"{swap_threshold}pts" if is_stale_rr else "")
                    reason = (
                        f"Rank & Replace Swap (Day 7+) — replaced with superior breakout {best_ticker} "
                        f"(New trigger: {best_trigger_score} vs held {mt_label}{stale_label})"
                    )
                    print(f"\U0001f504 Rank & Replace: {ticker_m} ({mt_label}) \u2192 {best_ticker} ({best_trigger_score})")

                    shares_rr    = int(pos["shares"])
                    buy_price_rr = float(pos["buy_price"])
                    buy_date_rr  = ea.datetime.datetime.fromisoformat(pos["buy_date"].replace('Z', '+00:00'))
                    buy_reason_rr = pos.get("buy_reason", "Unknown")
                    current_price_rr, _ = ea.get_position_price(ib, ticker_m)

                    # Deliberately a MARKET sell, not a Smart OCA exit, even
                    # though this is the least urgent rule in the ladder.
                    # Rank & Replace is a *swap*: the sell exists only to fund
                    # the named replacement buy on the very next line. An OCA
                    # may not fill for up to OCA_EXIT_DEFAULT_EXPIRY_DAYS, so
                    # routing it through the queue would decouple the two
                    # halves — cash stays tied up, the slot stays occupied, and
                    # the trigger being rotated into is likely gone by the time
                    # the sell completes. A better exit price is not worth
                    # losing the entry it was taken for.
                    # See decisions/2026-08-19_smart-exit-for-discretionary-rules.md.
                    sold = ea.execute_sell(ib, client, ticker_m, shares_rr, buy_price_rr,
                                        buy_date_rr, buy_reason_rr, current_price_rr, reason,
                                        pos_row=pos, market_regime=market_regime)
                    if sold:
                        print("   Slot freed. Running buy loop to fill slot...")
                        ea.run_market_open_buys(ib)
                        break
        capture.emit("eod_latch", phase="eod", stage="end",
                     complete=True, marker_only=True)




def _build_failed_params_snapshot(pos_row: dict | None, percent_return: float) -> dict:
    """
    Build a failure-parameter snapshot for breakout_learnings.

    Preferred source is `param_drift` (if present and parseable). If absent, use
    entry-time trigger parameters so the learning loop remains populated for exits
    reconciled from broker-managed stops.
    """
    import json as _json

    if not pos_row:
        return {}

    raw = pos_row.get("param_drift")
    if isinstance(raw, dict) and raw:
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict) and parsed:
                return parsed
        except Exception:
            pass

    failed = percent_return < 0
    trigger_type = str(pos_row.get("entry_trigger_type") or "BREAKOUT").upper()
    snapshot = {
        "_meta": {"trigger_type": trigger_type, "source": "entry_snapshot"},
    }
    for key, source_key in (
        ("volume_surge", "entry_volume_surge"),
        ("rs_score", "entry_rs_score"),
        ("technical_score", "entry_technical_score"),
        ("pivot_distance_pct", "entry_pivot_distance_pct"),
    ):
        val = pos_row.get(source_key)
        if val is None:
            continue
        snapshot[key] = {"entry": val, "failed": failed}
    return snapshot


def _write_breakout_learning_row(client: Client, ticker: str, buy_date, reason: str,
                                 pos_row: dict | None, market_regime: str,
                                 percent_return: float) -> None:
    """Persist a single breakout_learnings row. Non-fatal by design."""
    try:
        if not pos_row:
            return
        buy_dt_d = buy_date.date() if hasattr(buy_date, "date") else buy_date
        days_held = ea.trading_days_between(
            buy_dt_d, ea.datetime.datetime.now(ZoneInfo("America/New_York")).date()
        )
        client.table("breakout_learnings").insert({
            "ticker":            ticker,
            "buy_date":          buy_dt_d.isoformat(),
            "exit_date":         ea.datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
            "exit_type":         ea._infer_exit_type(reason),
            "entry_final_score": pos_row.get("entry_final_score"),
            "failed_params":     _build_failed_params_snapshot(pos_row, percent_return),
            "lesson_text":       pos_row.get("analysis_reason") or reason,
            "market_regime":     market_regime,
            "days_held":         days_held,
            "pnl_pct":           percent_return,
        }).execute()
        print(f"   📚 {ticker}: breakout_learnings row written (pnl={percent_return:+.1f}%)")
    except Exception as _le:
        print(f"   ⚠️ Could not write breakout_learnings for {ticker}: {_le}")
