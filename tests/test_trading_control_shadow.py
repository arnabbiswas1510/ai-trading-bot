"""Real-entry permission must not pause or contaminate hypothetical trading."""
import socket

import intraday_replay as capture
import shadow_engine as shadow
import trading_control
from test_intraday_replay import DAY, records  # noqa: F401
from test_shadow_engine import seed_and_frames


def test_shadow_still_buys_with_real_entries_off(records, monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_CONTROL_PATH", str(tmp_path / "control.sqlite3"))
    trading_control.set_entries_enabled(False)
    data = capture.build_dataset(records, DAY, DAY)
    for event in data["events"]:
        for trigger in event.get("triggers", []):
            trigger["ai_grade"] = "A"
    seed, frames = seed_and_frames(data)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Shadow decisions cannot consult real permission or a network")

    monkeypatch.setattr(trading_control, "entries_allowed", forbidden)
    monkeypatch.setattr(trading_control, "entry_submission", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    state = shadow.initialize(seed, seed["config"])
    fills = []
    for frame in frames:
        state, output = shadow.advance(state, frame)
        fills.extend(output["fills"])
    assert any(fill["side"] == "BUY" for fill in fills)
    assert all(fill["execution"] == "counterfactual_sampled_full_fill" for fill in fills)
    assert trading_control.get_status()["live_entries_enabled"] is False
