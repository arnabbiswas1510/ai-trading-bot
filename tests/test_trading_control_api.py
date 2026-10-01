"""Trading permission API cannot write without explicit operator credentials."""
import importlib
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
api = importlib.import_module("trading_control_api")
TOKEN = "test-only-control-token-not-a-secret-123456"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_CONTROL_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_initial_permission_is_off_and_response_does_not_expose_token(client):
    response = client.get("/api/trading-control")
    assert response.status_code == 200
    assert response.json()["live_entries_enabled"] is False
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text
    assert response.json()["write_configured"] is True


@pytest.mark.parametrize("authorization", ["", "Bearer wrong", f"Basic {TOKEN}"])
def test_unauthorized_mutation_is_rejected(client, authorization):
    response = client.put("/api/trading-control",
                          json={"live_entries_enabled": True},
                          headers={"Authorization": authorization})
    assert response.status_code == 403
    assert client.get("/api/trading-control").json()["live_entries_enabled"] is False


@pytest.mark.parametrize("token", ["", "too-short"])
def test_unconfigured_token_locks_writes(client, monkeypatch, token):
    monkeypatch.setenv("TRADING_CONTROL_TOKEN", token)
    response = client.put("/api/trading-control",
                          json={"live_entries_enabled": True},
                          headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert client.get("/api/trading-control").json()["write_configured"] is False


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_non_boolean_permission_is_rejected(client, value):
    response = client.put("/api/trading-control",
                          json={"live_entries_enabled": value},
                          headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 422


def test_operator_can_enable_then_disable_without_restart(client):
    for enabled in (True, False):
        response = client.put("/api/trading-control",
                              json={"live_entries_enabled": enabled},
                              headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert response.json()["live_entries_enabled"] is enabled
        assert client.get("/api/trading-control").json()["live_entries_enabled"] is enabled


def test_storage_failure_does_not_claim_permission_was_saved(client, monkeypatch):
    def fail(_enabled):
        raise api.trading_control.ControlUnavailable("unavailable")

    monkeypatch.setattr(api.trading_control, "set_entries_enabled", fail)
    response = client.put("/api/trading-control",
                          json={"live_entries_enabled": False},
                          headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert "Do not assume it changed" in response.json()["detail"]
