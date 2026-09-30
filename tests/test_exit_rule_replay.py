"""Offline regression guards for the legacy historical exit experiments."""

import datetime as dt
import io
import json
from contextlib import nullcontext
from dataclasses import replace

import pytest

from research import exit_rule_replay as replay


def bar(timestamp, open_, high, low, close):
    ts = dt.datetime.fromisoformat(timestamp)
    return {"ts": ts, "date": ts.date().isoformat(), "open": open_,
            "high": high, "low": low, "close": close}


def trade(bars):
    return replay.Trade(
        ticker="TEST", buy_price=100.0, buy_ts=bars[0]["ts"],
        sell_price=100.0, sell_ts=bars[-1]["ts"], shares=100,
        profit_loss=0.0, sell_reason="test", bars=bars,
        trade_days=sorted({b["date"] for b in bars}),
    )


def scale_config(**changes):
    return replace(
        replay.ExitConfig(
            "historical scale", proveit=True, p1_tiers=((99, 1.0),),
            p1_touch=True, mode="market", p2_floor_pct=-1.0,
            scale_frac=0.33,
        ),
        **changes,
    )


@pytest.mark.parametrize("same_bar", [False, True])
def test_full_stop_prevents_scale_at_or_after_exit(same_bar):
    bars = [
        bar("2026-09-21T10:00", 100, 105 if same_bar else 100, 98, 98),
        bar("2026-09-21T10:05", 101, 105, 101, 104),
    ]
    result = replay.simulate_scaleout(trade(bars), scale_config())
    assert result["price"] == pytest.approx(99.0)
    assert result["reason"] == "p1_market"
    assert result["ts"] == bars[0]["ts"]
    assert "scale_ts" not in result


@pytest.mark.parametrize("scale_open,scale_price", [(100, 104), (105, 105)])
def test_valid_scale_precedes_remainder_exit(scale_open, scale_price):
    bars = [
        bar("2026-09-21T10:00", scale_open, max(scale_open, 104), 100, 103),
        bar("2026-09-22T10:00", 102, 103, 98, 99),
    ]
    # Keep this test below the ladder: it isolates the Phase-2 floor.
    cfg = scale_config(p2_ladder_gain=10.0)
    result = replay.simulate_scaleout(trade(bars), cfg)
    assert result["price"] == pytest.approx(0.33 * scale_price + 0.67 * 99)
    assert result["scale_ts"] == bars[0]["ts"]
    assert result["ts"] == bars[1]["ts"]
    assert result["reason"] == "scale33@4%+p2_floor"


def test_breakeven_floor_only_applies_after_scale_bar():
    bars = [
        bar("2026-09-21T10:00", 100, 102, 100, 102),
        bar("2026-09-22T09:30", 101, 101, 99.5, 100.5),
        # Both this bar and the prior one survive the original $99 floor.
        bar("2026-09-22T09:35", 100.5, 104, 99.5, 103),
        bar("2026-09-22T09:40", 101, 102, 99.5, 100.5),
    ]
    cfg = scale_config(scale_be_remainder=True)
    result = replay.simulate_scaleout(trade(bars), cfg)
    assert result["price"] == pytest.approx(101.32)
    assert result["scale_ts"] == bars[2]["ts"]
    assert result["ts"] == bars[3]["ts"]
    assert cfg.p2_floor_pct == -1.0


def test_disabled_scale_does_not_tighten_floor():
    bars = [
        bar("2026-09-21T10:00", 100, 104, 100, 103),
        bar("2026-09-22T10:00", 102, 103, 99.5, 100.5),
    ]
    cfg = scale_config(scale_frac=0.0, scale_be_remainder=True)
    result = replay.simulate_scaleout(trade(bars), cfg)
    assert result["price"] == 100.0
    assert result["reason"] == "held"
    assert "scale_ts" not in result


def test_scale_remainder_marks_at_runon_close_if_still_open():
    bars = [
        bar("2026-09-21T10:00", 100, 104, 100, 103),
        bar("2026-09-22T10:00", 105, 106, 105, 106),
    ]
    position = trade(bars)
    position.sell_ts = bars[0]["ts"]
    position.runon_from = 1
    result = replay.simulate_scaleout(position, scale_config(p2_enabled=False))
    assert result["price"] == pytest.approx(0.33 * 104 + 0.67 * 106)
    assert result["ts"] == bars[-1]["ts"] + dt.timedelta(minutes=5)
    assert result["reason"] == "scale33@4%+runon_open"


def test_final_runon_bar_high_touch_scales_before_closing_valuation():
    bars = [
        bar("2026-09-21T10:00", 100, 101, 100, 101),
        bar("2026-09-22T10:00", 101, 104, 101, 103),
    ]
    position = trade(bars)
    position.sell_ts = bars[0]["ts"]
    position.runon_from = 1
    result = replay.simulate_scaleout(position, scale_config(p2_enabled=False))
    assert result["price"] == pytest.approx(103.33)
    assert result["reason"] == "scale33@4%+runon_open"
    assert result["ts"] == bars[-1]["ts"] + dt.timedelta(minutes=5)
    assert result["scale_ts"] < result["scale_latest_ts"] == result["ts"]


@pytest.mark.parametrize("scale_before_actual_exit", [False, True])
def test_fallback_exit_only_credits_scale_at_or_before_actual_sell(scale_before_actual_exit):
    bars = [
        bar("2026-09-21T09:30", 100, 104 if scale_before_actual_exit else 101, 100, 101),
        bar("2026-09-21T10:00", 101, 102, 100, 101),
        bar("2026-09-21T11:00", 102, 104, 102, 103),
    ]
    position = trade(bars)
    position.sell_ts = bars[1]["ts"]
    result = replay.simulate_scaleout(position, scale_config(p2_enabled=False))
    assert result["ts"] == position.sell_ts
    if scale_before_actual_exit:
        assert result["price"] == pytest.approx(101.32)
        assert result["reason"] == "scale33@4%+held"
        assert result["scale_ts"] < result["ts"]
    else:
        assert result["price"] == position.sell_price
        assert result["reason"] == "held"
        assert "scale_ts" not in result


@pytest.mark.parametrize("sale_minute", [0, 2, 5])
@pytest.mark.parametrize("target_at_open", [False, True])
def test_fallback_requires_scale_fill_before_sale_not_just_bar_timestamp(
        sale_minute, target_at_open):
    bars = [
        bar("2026-09-21T09:30", 100, 101, 100, 101),
        bar("2026-09-21T10:00", 104 if target_at_open else 101, 104, 101, 103),
    ]
    position = trade(bars)
    position.sell_ts = bars[1]["ts"] + dt.timedelta(minutes=sale_minute)
    position.sell_price = 101.0
    result = replay.simulate_scaleout(position, scale_config(p2_enabled=False))
    assert result["ts"] == position.sell_ts
    if target_at_open or sale_minute == 5:
        assert result["price"] == pytest.approx(101.99)
        assert result["reason"] == "scale33@4%+held"
        expected_latest = bars[1]["ts"] + dt.timedelta(minutes=0 if target_at_open else 5)
        assert result["scale_latest_ts"] == expected_latest
        assert result["scale_ts"] <= result["scale_latest_ts"] <= result["ts"]
    else:
        assert result["price"] == 101.0
        assert result["reason"] == "held"
        assert "scale_ts" not in result


def test_model_exit_after_actual_sell_can_credit_prior_counterfactual_scale():
    bars = [
        bar("2026-09-21T09:30", 100, 101, 100, 101),
        bar("2026-09-21T10:00", 101, 102, 100, 101),
        bar("2026-09-21T11:00", 102, 104, 102, 103),
        bar("2026-09-21T12:00", 100, 101, 98, 99),
    ]
    position = trade(bars)
    position.sell_ts = bars[1]["ts"]
    result = replay.simulate_scaleout(position, scale_config())
    assert result["price"] == pytest.approx(100.65)
    assert result["reason"] == "scale33@4%+p1_market"
    assert result["ts"] == bars[3]["ts"]
    assert position.sell_ts < result["scale_ts"] < result["ts"]


def test_armed_full_exit_prevents_later_scale():
    bars = [
        bar("2026-09-21T10:00", 100, 100, 98, 98),
        bar("2026-09-21T10:05", 96, 97, 95, 96),
        bar("2026-09-21T10:10", 101, 105, 101, 104),
    ]
    result = replay.simulate_scaleout(
        trade(bars), scale_config(p1_touch=False, mode="armed"))
    assert result["price"] == 96
    assert result["reason"] == "p1_gap"
    assert result["ts"] == bars[1]["ts"]
    assert "scale_ts" not in result


@pytest.mark.parametrize("simulate", [replay.simulate, replay.simulate_proveit])
@pytest.mark.parametrize("gap_at", ["2026-09-21T10:10", "2026-09-22T09:30"])
def test_armed_gap_after_first_bar_fills_at_open(simulate, gap_at):
    bars = [
        bar("2026-09-21T10:00", 100, 100, 98, 98),
        bar("2026-09-21T10:05", 98, 99, 97.5, 98.5),
        bar(gap_at, 96, 96.5, 95, 96),
    ]
    cfg = replay.ExitConfig(
        "historical armed", pct=1.0, proveit=True,
        p1_tiers=((99, 1.0),),
    )
    result = simulate(trade(bars), cfg)
    assert result["price"] == 96
    assert result["reason"].endswith("_gap")
    assert result["ts"] == bars[2]["ts"]


@pytest.mark.parametrize("mode", [
    None, "--grid", "--proveit", "--cliff", "--day0", "--ladder", "--ratchet",
    "--scale", "--eod", "--basetrail", "--p1ratchet", "--clean", "--runon",
    "--slotcost", "--jackknife",
])
def test_every_cli_mode_warns_before_credentials_or_network(mode, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["exit_rule_replay.py"] + ([mode] if mode else []))

    def no_credentials(_name):
        raise RuntimeError("offline test: stop before credentials")

    monkeypatch.setattr(replay, "_env", no_credentials)
    with pytest.raises(RuntimeError, match="offline test"):
        replay.main()
    output = capsys.readouterr().out
    assert "HISTORICAL COUNTERFACTUAL" in output
    assert "NOT the current strategy" in output
    assert "retired rules" in output
    assert "manually mirrored" in output
    assert "stop-only (no scale-out)" in output


def test_cli_help_explains_historical_scope(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["exit_rule_replay.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        replay.main()
    assert exc.value.code == 0
    output = " ".join(capsys.readouterr().out.split())
    assert "HISTORICAL COUNTERFACTUAL" in output
    assert "retired rules" in output
    assert "not live parity" in output


@pytest.mark.parametrize("mode", [None, "--runon", "--slotcost", "--jackknife"])
def test_json_exports_carry_scope_without_network_or_files(mode, monkeypatch, capsys):
    sample = trade([
        bar("2026-09-21T10:00", 100, 104, 100, 103),
        bar("2026-09-22T10:00", 102, 103, 98, 99),
    ])
    monkeypatch.setattr(replay, "_env", lambda name: "unused-offline")
    monkeypatch.setattr(replay, "load_trades", lambda **kw: [sample])
    monkeypatch.setattr(replay, "hydrate", lambda trades, key, **kw: trades)
    output = io.StringIO()
    monkeypatch.setattr(replay, "open", lambda *a, **kw: nullcontext(output), raising=False)
    monkeypatch.setattr("sys.argv", [
        "exit_rule_replay.py", "--json", "unused-offline.json",
    ] + ([mode] if mode else []))
    replay.main()
    exported = json.loads(output.getvalue())
    assert exported["research_scope"] == replay.REPLAY_SCOPE
    assert exported["results"]
    assert "HISTORICAL COUNTERFACTUAL" in capsys.readouterr().out


def test_historical_configurations_are_not_silently_replaced():
    assert all(not cfg.proveit for cfg in replay.headline_configs())
    assert all(cfg.scale_frac is None for cfg in replay.runon_configs())
    assert replay.live_baseline().scale_frac == 0.33
    assert replay.shipped_proveit().p2_floor_pct == -1.0
