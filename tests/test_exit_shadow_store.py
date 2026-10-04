"""Private observation writes cannot borrow broker/trading authority."""
from unittest.mock import MagicMock

import pytest
import supabase

import exit_shadow_store as store
import research_diagnostics as diagnostics


@pytest.fixture(autouse=True)
def isolated_writer(monkeypatch):
    monkeypatch.setattr(store, "_client", None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "ordinary-must-not-be-used")
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", "private-synthetic-key")
    monkeypatch.setattr(diagnostics, "emit", MagicMock())
    factory = MagicMock()
    monkeypatch.setattr(supabase, "create_client", factory)
    return factory


def test_isolated_private_client_is_cached_with_bounded_timeout(isolated_writer):
    import execution_agent

    trading_client = execution_agent.supabase
    client = store.get_client()
    assert store.get_client() is client
    isolated_writer.assert_called_once()
    args, kwargs = isolated_writer.call_args
    assert args == ("https://example.supabase.co", "private-synthetic-key")
    assert kwargs["options"].postgrest_client_timeout == 5
    assert execution_agent.supabase is trading_client


@pytest.mark.parametrize("value", ["", " ", "@bws", " @bws ", "bad\nkey", "bad\rkey"])
def test_no_ordinary_key_fallback(value, monkeypatch, isolated_writer, capsys):
    monkeypatch.setenv("INTRADAY_SUPABASE_KEY", value)
    assert not store.write_observation({"ticker": "TEST"})
    isolated_writer.assert_not_called()
    diagnostics.emit.assert_called_once()
    assert "ordinary-must-not-be-used" not in capsys.readouterr().out


def test_success_inserts_only_private_shadow_row(isolated_writer):
    row = {"ticker": "TEST", "cycle_ts": "2026-10-04T15:00:00+00:00"}
    assert store.write_observation(row)
    isolated_writer.return_value.table.assert_called_once_with("exit_shadow_log")
    isolated_writer.return_value.table.return_value.insert.assert_called_once_with(row)
    diagnostics.emit.assert_not_called()


@pytest.mark.parametrize("code", ["PGRST205", "42501", "42P01"])
def test_errors_are_nonfatal_visible_and_credential_safe(code, isolated_writer, capsys):
    class PrivateTransportError(Exception):
        pass

    error = PrivateTransportError("raw JWT private-synthetic-key URL?apikey=secret")
    error.code = code
    isolated_writer.return_value.table.return_value.insert.return_value.execute.side_effect = error
    assert not store.write_observation({"ticker": "TEST"})
    args, kwargs = diagnostics.emit.call_args
    assert args == ("execution-agent", "exit_shadow_write_failed")
    assert kwargs["error"] is error
    assert kwargs["context"]["table"] == "exit_shadow_log"
    output = capsys.readouterr().out
    assert code in output
    assert "private-synthetic-key" not in output
    assert "apikey" not in output
    assert "raw JWT" not in output


def test_client_initialization_failure_is_safe_and_retried(isolated_writer, capsys):
    isolated_writer.side_effect = ValueError("private-synthetic-key")
    assert not store.write_observation({})
    assert store._client is None
    isolated_writer.side_effect = None
    assert store.write_observation({})
    assert isolated_writer.call_count == 2
    assert "private-synthetic-key" not in capsys.readouterr().out


def test_diagnostic_failure_does_not_escape(isolated_writer, monkeypatch, capsys):
    isolated_writer.side_effect = RuntimeError("private-synthetic-key")
    monkeypatch.setattr(diagnostics, "emit", MagicMock(side_effect=RuntimeError("secret")))
    assert not store.write_observation({})
    assert "not saved" in capsys.readouterr().out


def test_diagnostic_allowlist_preserves_table_name():
    assert diagnostics.query_context(
        type("Query", (), {"path": "/rest/v1/exit_shadow_log"})()
    ) == {"table": "exit_shadow_log"}


@pytest.mark.parametrize("failure", ["write", "compute"])
def test_monitor_keeps_protective_order_logic_on_research_failure(
        failure, monkeypatch, isolated_writer):
    import execution_agent
    from tests.conftest import make_ib_mock, make_position, make_supabase_mock
    from tests.test_sell_logic import _run_monitor

    position = make_position("TEST", buy_price=100.0)
    position["hard_stop_price"] = round(100 * (1 - execution_agent.MAX_LOSS_PCT), 2)
    ordinary = make_supabase_mock(portfolio=[position])
    monkeypatch.setattr(execution_agent, "EXIT_SHADOW_LOG_ENABLED", True)
    if failure == "write":
        isolated_writer.return_value.table.return_value.insert.return_value.execute.side_effect = (
            RuntimeError("private-synthetic-key"))
    else:
        monkeypatch.setattr(execution_agent, "compute_exit_shadows",
                            MagicMock(side_effect=ValueError("private-synthetic-key")))
    _, protective = _run_monitor(make_ib_mock(symbols=["TEST"]), ordinary,
                                  live_prices={"TEST": 100.0})
    protective.assert_called_once()
    assert not any(call.args == ("exit_shadow_log",) for call in ordinary.table.call_args_list)
    diagnostics.emit.assert_called_once()


def test_agent_image_includes_private_writer():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert "exit_shadow_store.py" in (root / "Dockerfile.agent").read_text()
