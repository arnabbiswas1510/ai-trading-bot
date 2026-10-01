from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import sqlite3
import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

import trading_control as control
from tests.conftest import make_ib_mock, make_supabase_mock, make_trigger


def test_absent_store_fails_closed_and_first_heartbeat_defaults_off(isolated_entry_control, caplog):
    isolated_entry_control.unlink()
    status = control.get_status()
    assert status["live_entries_enabled"] is False
    assert status["error"]
    assert not control.entries_allowed()
    with pytest.raises(control.EntryDisabled):
        with control.entry_submission():
            pytest.fail("must not submit")
    status = control.report_agent_status(False)
    assert "error" not in status
    assert status["live_entries_enabled"] is False
    assert status["revision"] == 0
    assert status["agent"]["broker_connected"] is False
    assert "blocked" in caplog.text


def test_revision_changes_only_on_permission_change_and_survives_restart():
    on = control.get_status()
    assert control.set_entries_enabled(True)["revision"] == on["revision"]
    off = control.set_entries_enabled(False)
    assert off["revision"] == on["revision"] + 1
    assert control.report_agent_status(True)["live_entries_enabled"] is False
    output = subprocess.check_output(
        [sys.executable, "-c",
         "import json,trading_control;print(json.dumps(trading_control.get_status()))"],
        text=True,
    )
    restarted = json.loads(output)
    assert restarted == control.get_status()
    assert restarted["live_entries_enabled"] is False
    assert restarted["agent"]["observed_revision"] == off["revision"]
    assert datetime.fromisoformat(restarted["agent"]["last_seen"]).utcoffset().total_seconds() == 0
    assert control.set_entries_enabled(True)["revision"] == off["revision"] + 1


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None, [], {}])
def test_permission_setter_rejects_non_boolean(invalid):
    before = control.get_status()
    with pytest.raises(ValueError):
        control.set_entries_enabled(invalid)
    assert control.get_status() == before


@pytest.mark.parametrize("invalid", ["false", "true", 2, -1, 0.5])
def test_corrupt_permission_is_never_coerced_true(isolated_entry_control, invalid):
    with sqlite3.connect(isolated_entry_control) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("UPDATE entry_control SET live_entries_enabled = ?", (invalid,))
    assert not control.entries_allowed()
    assert control.get_status()["error"]
    with pytest.raises(control.ControlUnavailable):
        control.set_entries_enabled(True)
    with pytest.raises(control.EntryDisabled):
        with control.entry_submission():
            pytest.fail("must not submit")


def test_unavailable_directory_and_corrupt_database_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_CONTROL_PATH", str(tmp_path / "absent" / "control.sqlite3"))
    for operation in (control.get_status, lambda: control.report_agent_status(False)):
        status = operation()
        assert status["error"]
        assert status["live_entries_enabled"] is False
    with pytest.raises(control.ControlUnavailable):
        control.set_entries_enabled(True)
    path = tmp_path / "broken.sqlite3"
    path.write_bytes(b"not a SQLite database")
    monkeypatch.setenv("TRADING_CONTROL_PATH", str(path))
    assert control.get_status()["error"]
    assert not control.entries_allowed()


def test_toggle_waits_for_submission_and_then_blocks_next_submission():
    entered = threading.Event()
    release = threading.Event()
    toggle_started = threading.Event()

    def submit():
        with control.entry_submission():
            entered.set()
            assert release.wait(5)

    def disable():
        toggle_started.set()
        return control.set_entries_enabled(False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        submission = pool.submit(submit)
        assert entered.wait(5)
        toggle = pool.submit(disable)
        assert toggle_started.wait(5)
        try:
            assert not toggle.done()
            assert control.get_status()["live_entries_enabled"] is True
        finally:
            release.set()
        submission.result(timeout=5)
        result = toggle.result(timeout=5)
    assert "error" not in result
    assert result["live_entries_enabled"] is False
    with pytest.raises(control.EntryDisabled):
        with control.entry_submission():
            pytest.fail("next submission must not run")


def test_locked_store_blocks_submission_and_toggle_without_claiming_success(
        isolated_entry_control, monkeypatch):
    monkeypatch.setattr(control, "LOCK_TIMEOUT_SECONDS", 0.01)
    with sqlite3.connect(isolated_entry_control) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(control.ControlUnavailable):
            control.set_entries_enabled(False)
        with pytest.raises(control.EntryDisabled):
            with control.entry_submission():
                pytest.fail("locked permission must block")
    assert control.get_status()["live_entries_enabled"] is True


def test_submission_body_error_releases_lock_without_reclassifying_it():
    with pytest.raises(RuntimeError, match="broker failure"):
        with control.entry_submission():
            raise RuntimeError("broker failure")
    assert "error" not in control.set_entries_enabled(False)


def test_rotation_does_not_lock_quotes_and_blocks_before_cancelling_protection():
    ib = make_ib_mock()
    with control.rotation_submission(ib) as rotation_ib:
        rotation_ib.qualifyContracts()
        assert "error" not in control.set_entries_enabled(False)
        with pytest.raises(control.EntryDisabled):
            rotation_ib.cancelOrder(MagicMock())
    ib.cancelOrder.assert_not_called()
    ib.placeOrder.assert_not_called()


def test_rotation_reserves_cancellation_through_submission_but_not_fill_wait(monkeypatch):
    from ib_insync import MarketOrder, Stock
    monkeypatch.setattr(control, "LOCK_TIMEOUT_SECONDS", 0.01)
    ib = make_ib_mock()
    with control.rotation_submission(ib) as rotation_ib:
        rotation_ib.cancelOrder(MagicMock())
        with pytest.raises(control.ControlUnavailable):
            control.set_entries_enabled(False)
        rotation_ib.placeOrder(Stock("TEST", "SMART", "USD"), MarketOrder("SELL", 10))
        # Still inside the sell helper, before a potentially long fill wait.
        assert control.set_entries_enabled(False)["live_entries_enabled"] is False
        rotation_ib.sleep(60)
        rotation_ib.cancelOrder(MagicMock())  # post-submission cleanup stays allowed
    assert ib.cancelOrder.call_count == 2
    ib.placeOrder.assert_called_once()


def test_rotation_failure_before_submission_releases_permission_lock():
    ib = make_ib_mock()
    with pytest.raises(RuntimeError):
        with control.rotation_submission(ib) as rotation_ib:
            rotation_ib.cancelOrder(MagicMock())
            raise RuntimeError("preparation failed")
    assert "error" not in control.set_entries_enabled(False)

@pytest.mark.parametrize("query", ["positions", "orders"])
@pytest.mark.parametrize("fails", [False, True])
def test_rotation_preserves_broker_request_timeout_and_restores_it(query, fails):
    import broker_positions
    ib = make_ib_mock()
    ib.RequestTimeout = 0
    observed = []

    def request():
        observed.append(ib.RequestTimeout)
        if fails:
            raise TimeoutError("broker did not reply")
        return []

    method = ib.reqPositions if query == "positions" else ib.reqAllOpenOrders
    method.side_effect = request
    account = ib.managedAccounts()[0]
    with control.rotation_submission(ib) as rotation_ib:
        operation = (
            lambda: broker_positions.confirmed_stock_positions(rotation_ib, account)
        ) if query == "positions" else (
            lambda: broker_positions._active_sells(rotation_ib, account, "TEST")
        )
        if fails:
            with pytest.raises(broker_positions.BrokerPositionError):
                operation()
        else:
            assert operation() == []
        assert rotation_ib.RequestTimeout == 0
    assert observed == [10]
    assert ib.RequestTimeout == 0


def test_manual_rotation_inactive_never_connects_or_sells():
    import rotate_positions
    control.set_entries_enabled(False)
    with patch.object(rotate_positions, "create_client") as client, \
         patch.object(rotate_positions, "IB") as broker, \
         patch.object(rotate_positions, "_place_sell") as sell:
        rotate_positions.main()
    client.assert_not_called()
    broker.assert_not_called()
    sell.assert_not_called()


@pytest.mark.parametrize("disable_at", ["confirmation", "submission"])
def test_manual_rotation_rechecks_before_connect_and_before_sell_submission(disable_at):
    import rotate_positions
    from ib_insync import MarketOrder, Stock
    from tests.conftest import make_position
    client = make_supabase_mock(portfolio=[make_position("HELD")])
    ib = make_ib_mock(["HELD"])

    def confirm(_):
        if disable_at == "confirmation":
            control.set_entries_enabled(False)
        return "yes"

    def sell(rotation_ib, *_):
        control.set_entries_enabled(False)
        rotation_ib.placeOrder(Stock("HELD", "SMART", "USD"), MarketOrder("SELL", 100))
        pytest.fail("inactive rotation must not submit")

    with patch.object(rotate_positions, "SUPABASE_URL", "test"), \
         patch.object(rotate_positions, "SUPABASE_KEY", "test"), \
         patch.object(rotate_positions.sys, "argv", ["rotate_positions.py", "HELD"]), \
         patch.object(rotate_positions, "create_client", return_value=client), \
         patch.object(rotate_positions, "_select_buy_triggers", return_value=[make_trigger("NEW")]), \
         patch("builtins.input", side_effect=confirm), \
         patch.object(rotate_positions, "IB", return_value=ib) as connect, \
         patch.object(rotate_positions, "_place_sell", side_effect=sell), \
         patch.object(rotate_positions, "_place_buy") as buy:
        rotate_positions.main()
    if disable_at == "confirmation":
        connect.assert_not_called()
    ib.placeOrder.assert_not_called()
    ib.cancelOrder.assert_not_called()
    buy.assert_not_called()


@pytest.mark.parametrize("use_ib", [False, True])
def test_heartbeat_runs_cooperatively_during_long_wait_and_preserves_permission(monkeypatch, use_ib):
    ib = make_ib_mock()
    clock = [0.0]
    waits = []
    caller_threads = []
    monkeypatch.setattr(control.time, "monotonic", lambda: clock[0])

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds
        return True

    ib.sleep.side_effect = wait
    monkeypatch.setattr(control.time, "sleep", wait)
    original = control.report_agent_status

    def report(connected):
        caller_threads.append(threading.get_ident())
        return original(connected)

    monkeypatch.setattr(control, "report_agent_status", report)
    stop = control.start_agent_heartbeat(ib)
    try:
        control.set_entries_enabled(False)
        ib.isConnected.return_value = False
        stop.sleep(65, use_ib=use_ib)
        assert waits == [20, 20, 20, 5]
        assert len(caller_threads) == 4
        assert set(caller_threads) == {threading.get_ident()}
        status = control.get_status()
        assert status["live_entries_enabled"] is False
        assert status["agent"]["broker_connected"] is False
        assert status["agent"]["observed_revision"] == status["revision"]
    finally:
        stop.set()


def test_automatic_and_manual_off_gates_do_not_touch_broker_or_database():
    import buying
    import force_buy
    control.set_entries_enabled(False)
    ib = make_ib_mock()
    client = MagicMock()
    with patch("execution_agent.get_supabase_client") as get_client:
        buying.run_market_open_buys(ib)
        force_buy._place_buy(ib, client, {}, 10000, set(), "U_TEST", None, False)
        force_buy.main()
    get_client.assert_not_called()
    ib.placeOrder.assert_not_called()
    ib.positions.assert_not_called()
    client.table.assert_not_called()


def test_automatic_final_submit_rechecks_permission():
    from tests.test_buy_gates import _run_buys
    import execution_agent
    ib = make_ib_mock()
    client = make_supabase_mock(daily_triggers=[make_trigger("TEST")], portfolio=[])

    def qualified(*contracts):
        control.set_entries_enabled(False)
        return list(contracts)

    ib.qualifyContracts.side_effect = qualified
    with patch.object(execution_agent, "assert_schema_ok", return_value=True):
        _run_buys(ib, client, ibkr_price=100)
    assert control.get_status()["live_entries_enabled"] is False
    ib.placeOrder.assert_not_called()


def test_manual_final_submit_rechecks_permission():
    import force_buy
    ib = make_ib_mock()

    def qualified(*contracts):
        control.set_entries_enabled(False)
        return list(contracts)

    ib.qualifyContracts.side_effect = qualified
    with patch.object(force_buy, "get_ibkr_price", return_value=100):
        assert force_buy._place_buy(
            ib, MagicMock(), make_trigger("TEST"), 10000, set(),
            ib.managedAccounts()[0], None, False
        ) is None
    assert control.get_status()["live_entries_enabled"] is False
    ib.placeOrder.assert_not_called()


def test_manual_entry_submits_when_enabled_without_touching_existing_orders():
    import force_buy
    ib = make_ib_mock()
    ib.placeOrder.return_value.orderStatus.status = "Cancelled"
    ib.placeOrder.return_value.orderStatus.filled = 0
    with patch.object(force_buy, "get_ibkr_price", return_value=100):
        result = force_buy._place_buy(
            ib, MagicMock(), make_trigger("TEST"), 10000, set(),
            ib.managedAccounts()[0], None, False,
        )
    assert result is None
    ib.placeOrder.assert_called_once()
    assert ib.placeOrder.call_args.args[1].action == "BUY"
    ib.cancelOrder.assert_not_called()


@pytest.mark.parametrize("unavailable", [False, True])
def test_protective_stop_still_arms_when_entries_are_off_or_unavailable(
        isolated_entry_control, unavailable):
    from tests.test_prove_it_stop import _make_pos, _make_ib, _make_sb, _run, BD_DAY0
    import execution_agent
    control.set_entries_enabled(False)
    if unavailable:
        isolated_entry_control.unlink()
    pos = _make_pos(buy_date=BD_DAY0, closed_above_entry=False)
    price = 100 * (1 - execution_agent.PROVE_IT_P1_DAY0_PCT) - 0.01
    _, arm = _run(_make_ib([pos]), _make_sb([pos]), price)
    arm.assert_called_once()
    assert "Prove-It Stop (Phase 1" in arm.call_args.args[5]


@pytest.mark.parametrize("enabled, disable_during_quote", [(False, False), (True, False), (True, True)])
def test_rotation_requires_entry_permission_but_metrics_continue(enabled, disable_during_quote):
    from tests.test_plateau_rotation import _run_eod
    from tests.conftest import make_position
    import execution_agent
    control.set_entries_enabled(enabled)
    pos = make_position("HELD")
    pos.update(buy_date="2026-06-01T12:00:00+00:00", closed_above_entry=True,
               highest_unrealized_pct=1, hwm_price=101, breakout_verdict="PASS")
    client = make_supabase_mock(
        portfolio=[pos], daily_triggers=[make_trigger("NEW", final_score=99)])

    def quote(*args):
        # The rotation's final quote occurs after ranking, with no price map.
        if disable_during_quote and len(args) == 2:
            control.set_entries_enabled(False)
        return 101, "IBKR"

    with patch.object(execution_agent, "MAX_POSITIONS", 1), \
         patch.object(execution_agent, "get_position_price", side_effect=quote), \
         patch.object(execution_agent, "_fetch_ohlcv", return_value=[]), \
         patch.object(execution_agent, "_get_market_regime", return_value="BULL"), \
         patch.object(execution_agent, "fetch_held_position_sentiment", return_value=50), \
         patch.object(execution_agent, "notifier"), \
         patch.object(execution_agent, "run_market_open_buys") as buy:
        sell = _run_eod(make_ib_mock(["HELD"]), client, live_rs_return=20, live_price=101)
    metrics = client.table("portfolio_positions").update.call_args_list
    assert any("momentum_health_score" in call.args[0] for call in metrics)
    assert sell.called is (enabled and not disable_during_quote)
    assert buy.called is (enabled and not disable_during_quote)


def test_forced_buy_sentinel_cannot_skip_regular_protection_when_off():
    from zoneinfo import ZoneInfo
    import execution_agent as ea
    import intraday_capture
    control.set_entries_enabled(False)
    ib = make_ib_mock()
    now = datetime(2026, 10, 1, 11, 0, tzinfo=ZoneInfo("America/New_York"))

    def sleep(seconds):
        if seconds == 900:
            raise KeyboardInterrupt()

    ib.sleep.side_effect = sleep
    notifier = MagicMock()
    notifier.verify_delivery.return_value = (True, "test")
    with patch.object(ea, "IB", return_value=ib), \
         patch.object(ea, "notifier", notifier), \
         patch.object(ea, "schema_guard"), \
         patch.object(ea, "get_supabase_client", return_value=MagicMock()), \
         patch.object(ea, "install_shutdown_log_flush"), \
         patch.object(ea, "flush_logs_quietly"), \
         patch.object(ea, "reconcile_with_ibkr") as reconcile, \
         patch.object(ea, "process_exit_requests") as process, \
         patch.object(ea, "monitor_portfolio_intraday") as monitor, \
         patch.object(ea.os.path, "exists", return_value=True), \
         patch.object(ea.os, "remove"), \
         patch.object(ea, "datetime") as clock, \
         patch.object(control, "start_agent_heartbeat", return_value=MagicMock()), \
         patch.object(intraday_capture, "start"), \
         patch.object(intraday_capture, "stop"):
        clock.datetime.now.return_value = now
        ea.main_loop()
    assert reconcile.call_count == 2
    assert process.call_count == 2
    monitor.assert_called_once_with(ib)
    ib.placeOrder.assert_not_called()
