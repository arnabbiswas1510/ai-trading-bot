"""
tests/test_breakout_verdict.py

Tests for the Breakout Verdict (Day 3 EOD), Intraday Loss Minimiser (Day 4+),
Early Loss Kill-switch (Day 0), and the Armed Trailing Exit (Day 0-6
loss-cutting: sell signals arm a tight trailing stop instead of an instant
sell, bounded by a hard deadline).

buy_date mapping for mock now=2026-06-17:
  days_held=3 -> buy_date 2026-06-12
  days_held=4 -> buy_date 2026-06-11
  days_held=5 -> buy_date 2026-06-10
  days_held=7 -> buy_date 2026-06-08
"""

import datetime
import pytest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import execution_agent

# --- Buy date constants (calibrated against mock now=2026-06-17) --------------
BD_DAY1 = "2026-06-16T12:00:00+00:00"  # 1 trading day before Jun 17
BD_DAY2 = "2026-06-15T12:00:00+00:00"  # 2 trading days
BD_DAY3 = "2026-06-12T12:00:00+00:00"  # 3 trading days
BD_DAY4 = "2026-06-11T12:00:00+00:00"  # 4 trading days
BD_DAY5 = "2026-06-10T12:00:00+00:00"  # 5 trading days
BD_DAY7 = "2026-06-08T12:00:00+00:00"  # 7 trading days
BD_DAY0 = "2026-06-17T12:00:00+00:00"  # same trading day as mock now


def _make_pos(ticker="AAPL", buy_price=100.0, buy_date=BD_DAY3,
              verdict=None, intraday_high_today=None, shares=20,
              exit_armed=False, exit_armed_at=None, exit_armed_reason=None):
    return {
        "ticker": ticker, "buy_price": buy_price,
        "buy_date": buy_date, "buy_reason": "CANSLIM breakout", "shares": shares,
        "stop_loss_pct": 0.07, "highest_unrealized_pct": 0.0,
        "hwm_price": buy_price, "hwm_date": None,
        "entry_rs_score": 90, "live_rs_score": 90,
        "breakout_verdict": verdict, "intraday_high_today": intraday_high_today,
        "momentum_health_score": None, "rotation_recommendation": None,
        "volume_distribution_flag": False,
        "exit_armed": exit_armed, "exit_armed_at": exit_armed_at,
        "exit_armed_reason": exit_armed_reason,
    }


def _make_ohlcv(length=22, day3_vol_ratio=1.0, base_vol=1_000_000):
    """Bars in ASCENDING date order, matching _fetch_ohlcv().

    The Day 3 bar is therefore the LAST element, and the 20-day average is
    taken from the 20 bars before it. This fixture previously placed the Day 3
    volume at index 0, which mirrored a bug in the production code rather than
    the real data layout, so a wrong implementation passed.
    """
    bars = []
    for i in range(length):
        v = int(base_vol * day3_vol_ratio) if i == length - 1 else base_vol
        bars.append({"open": 100, "high": 101, "low": 99, "close": 100, "volume": v})
    return bars


def _make_ib(positions):
    ib = MagicMock()
    items = []
    for pos in positions:
        item = MagicMock()
        item.contract.symbol = pos["ticker"]
        item.contract.secType = "STK"
        item.position = pos["shares"]
        item.averageCost = pos["buy_price"]
        item.marketPrice = 0.0   # no live IBKR mark -> get_position_price falls back to FMP
        items.append(item)
    ib.portfolio.return_value = items
    ib.reqPositions.return_value = None
    ib.openOrders.return_value = []
    return ib


def _make_sb(positions):
    sb = MagicMock()
    pos_res = MagicMock()
    pos_res.data = positions
    sb.table.return_value.select.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = pos_res
    sb.table.return_value.select.return_value.gte.return_value.order.return_value.execute.return_value = MagicMock(data=[])
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return sb


def _run(ib, sb, positions, live_price, hour=11, minute=30, ohlcv=None, enqueue_ok=True):
    tz = ZoneInfo("America/New_York")
    now_mock = datetime.datetime(2026, 6, 17, hour, minute, tzinfo=tz)
    ohlcv_val = ohlcv if ohlcv is not None else []
    with patch("execution_agent.supabase", sb), \
         patch("execution_agent.get_live_price", return_value=live_price), \
         patch("execution_agent._fetch_ohlcv", return_value=ohlcv_val), \
         patch("execution_agent._fetch_current_rs", return_value=90), \
         patch("execution_agent.cancel_ticker_sell_orders"), \
         patch("execution_agent.place_trailing_stop", return_value=("TS_MOCK", 0.07)), \
         patch("execution_agent.execute_sell") as mock_sell, \
         patch("execution_agent.arm_exit") as mock_arm, \
         patch("execution_agent.enqueue_smart_exit", return_value=enqueue_ok) as mock_enqueue, \
         patch("execution_agent.datetime") as mock_dt:
        mock_dt.datetime.now.side_effect = lambda *a, **kw: now_mock
        mock_dt.datetime.fromisoformat.side_effect = datetime.datetime.fromisoformat
        mock_dt.date.fromisoformat.side_effect = datetime.date.fromisoformat
        mock_dt.date.today.return_value = now_mock.date()
        mock_dt.timezone = datetime.timezone
        mock_dt.timedelta = datetime.timedelta
        execution_agent.monitor_portfolio_intraday(ib)
    # Day 7+ discretionary rules now hand the exit to the Smart OCA queue
    # instead of market-selling. Exposed as an attribute so the 16 existing
    # call sites that only care about sell/arm keep their 2-tuple unpacking.
    _run.last_enqueue = mock_enqueue
    return mock_sell, mock_arm


def _verdict_updates(sb):
    return [str(c) for c in sb.table.return_value.update.call_args_list
            if "breakout_verdict" in str(c)]


# --- Day 3 Verdict Tests -----------------------------------------------------

class TestBreakoutVerdict:

    def test_pass_price_and_volume(self):
        """Day 3 EOD: price +1.5% AND volume 1.2x avg -> PASS, no sell, no fail notify."""
        pos = _make_pos(buy_date=BD_DAY3, verdict=None)
        ohlcv = _make_ohlcv(day3_vol_ratio=1.2)
        ib, sb = _make_ib([pos]), _make_sb([pos])
        with patch.object(execution_agent.notifier, "notify_breakout_verdict_fail") as mock_fail:
            mock_sell, mock_arm = _run(ib, sb, [pos], 101.5, hour=15, minute=50, ohlcv=ohlcv)
        updates = _verdict_updates(sb)
        assert updates and "PASS" in updates[0], f"Expected PASS update, got: {updates}"
        mock_sell.assert_not_called()
        mock_arm.assert_not_called()
        mock_fail.assert_not_called()

    def test_fail_price_below_1pct(self):
        """Day 3 EOD: price only +0.5% (< 1%) -> FAIL written, notify sent."""
        pos = _make_pos(buy_date=BD_DAY3, buy_price=100.0, verdict=None)
        ohlcv = _make_ohlcv(day3_vol_ratio=1.2)
        ib, sb = _make_ib([pos]), _make_sb([pos])
        with patch.object(execution_agent.notifier, "notify_breakout_verdict_fail") as mock_fail:
            mock_sell, mock_arm = _run(ib, sb, [pos], 100.5, hour=15, minute=50, ohlcv=ohlcv)
        updates = _verdict_updates(sb)
        assert updates and "FAIL" in updates[0], f"Expected FAIL update, got: {updates}"
        mock_fail.assert_called_once()
        mock_sell.assert_not_called()

    def test_fail_volume_too_low(self):
        """Day 3 EOD: price +2% but volume 0.5x avg -> FAIL."""
        pos = _make_pos(buy_date=BD_DAY3, buy_price=100.0, verdict=None)
        ohlcv = _make_ohlcv(day3_vol_ratio=0.5)
        ib, sb = _make_ib([pos]), _make_sb([pos])
        with patch.object(execution_agent.notifier, "notify_breakout_verdict_fail") as mock_fail:
            mock_sell, mock_arm = _run(ib, sb, [pos], 102.0, hour=15, minute=50, ohlcv=ohlcv)
        updates = _verdict_updates(sb)
        assert updates and "FAIL" in updates[0], f"Expected FAIL update, got: {updates}"
        mock_fail.assert_called_once()

    def test_volume_check_reads_latest_bar_not_oldest(self):
        """Regression: the verdict must compare TODAY's volume against the prior
        20 sessions. Reading ohlcv[0] instead of ohlcv[-1] compared a ~100-day-old
        bar against the 20 days following it — both stale, ratio always ~1.0 — so
        vol_pass was spuriously True and FAIL almost never fired.

        Here the newest bar is heavy and the OLD bars are light. A correct
        implementation sees a volume surge (PASS); the buggy one sees the old
        light bar against its light neighbours and also passes on volume, so the
        discriminating case is the inverse test below."""
        bars = [{"open": 100, "high": 101, "low": 99, "close": 100,
                 "volume": 1_000_000} for _ in range(22)]
        bars[-1]["volume"] = 3_000_000          # today: heavy
        bars[0]["volume"]  = 100                # 100 days ago: near-zero
        pos = _make_pos(buy_date=BD_DAY3, buy_price=100.0, verdict=None)
        ib, sb = _make_ib([pos]), _make_sb([pos])
        _run(ib, sb, [pos], 102.0, hour=15, minute=50, ohlcv=bars)
        updates = _verdict_updates(sb)
        assert updates and "PASS" in updates[0], (
            f"Heavy volume today must PASS; reading the oldest bar gives FAIL. Got: {updates}")

    def test_volume_check_fails_when_today_is_light_despite_heavy_history(self):
        """Inverse of the above: today is light, the oldest bar is heavy. The
        buggy implementation reads the heavy old bar and returns PASS."""
        bars = [{"open": 100, "high": 101, "low": 99, "close": 100,
                 "volume": 1_000_000} for _ in range(22)]
        bars[-1]["volume"] = 100_000            # today: very light -> must FAIL
        bars[0]["volume"]  = 50_000_000         # 100 days ago: heavy
        pos = _make_pos(buy_date=BD_DAY3, buy_price=100.0, verdict=None)
        ib, sb = _make_ib([pos]), _make_sb([pos])
        with patch.object(execution_agent.notifier, "notify_breakout_verdict_fail"):
            _run(ib, sb, [pos], 102.0, hour=15, minute=50, ohlcv=bars)
        updates = _verdict_updates(sb)
        assert updates and "FAIL" in updates[0], (
            f"Light volume today must FAIL even with a heavy stale bar. Got: {updates}")

    def test_not_evaluated_before_day3(self):
        """Days 1-2: verdict must NOT be written."""
        for bd in [BD_DAY1, BD_DAY2]:
            pos = _make_pos(buy_date=bd, verdict=None)
            ohlcv = _make_ohlcv(day3_vol_ratio=1.5)
            ib, sb = _make_ib([pos]), _make_sb([pos])
            _run(ib, sb, [pos], 103.0, hour=15, minute=50, ohlcv=ohlcv)
            assert _verdict_updates(sb) == [], f"buy_date={bd}: verdict must not be written yet"

    def test_not_overwritten_once_set(self):
        """Once verdict PASS/FAIL is set, it must not be overwritten at Day 4+."""
        for v in ["PASS", "FAIL"]:
            pos = _make_pos(buy_date=BD_DAY4, verdict=v)
            ohlcv = _make_ohlcv(day3_vol_ratio=1.5)
            ib, sb = _make_ib([pos]), _make_sb([pos])
            _run(ib, sb, [pos], 105.0, hour=15, minute=50, ohlcv=ohlcv)
            assert _verdict_updates(sb) == [], \
                f"Verdict '{v}' must not be overwritten on Day 4"


# --- Intraday Loss Minimiser Tests -------------------------------------------

class TestArmedExitDeadline:

    def test_forces_sell_after_deadline(self):
        """Armed > ARMED_EXIT_DEADLINE_HOURS ago and still open -> forced market sell."""
        armed_at = "2026-06-17T07:00:00+00:00"  # ~4.5h before mock now (11:30 ET = 15:30 UTC)
        pos = _make_pos(buy_date=BD_DAY4, buy_price=100.0, verdict="FAIL",
                        exit_armed=True, exit_armed_at=armed_at,
                        exit_armed_reason="Intraday Loss Minimiser — Day 4 universal rule")
        ib, sb = _make_ib([pos]), _make_sb([pos])
        mock_sell, mock_arm = _run(ib, sb, [pos], 99.0, hour=11, minute=30)
        mock_sell.assert_called_once()
        mock_arm.assert_not_called()
        reason = mock_sell.call_args.args[8]
        assert "Armed Exit Deadline" in reason

    def test_does_not_force_sell_before_deadline(self):
        """Armed well within ARMED_EXIT_DEADLINE_HOURS -> left open, no forced sell,
        no re-evaluation of the original trigger (still just watching the trail)."""
        armed_at = "2026-06-17T14:00:00+00:00"  # ~1.5h before mock now (15:30 UTC)
        pos = _make_pos(buy_date=BD_DAY4, buy_price=100.0, verdict="FAIL",
                        exit_armed=True, exit_armed_at=armed_at,
                        exit_armed_reason="Intraday Loss Minimiser — Day 4 universal rule")
        ib, sb = _make_ib([pos]), _make_sb([pos])
        mock_sell, mock_arm = _run(ib, sb, [pos], 99.0, hour=11, minute=30)
        mock_sell.assert_not_called()
        mock_arm.assert_not_called()
