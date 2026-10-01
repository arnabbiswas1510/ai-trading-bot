"""Synthetic chronology and shared-core delegation tests; no market data/network."""
import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

import pytest

import cooling_off
import decision_core as dc
import exit_core as ec
import exit_rules as er
from research import live_rule_replay as replay


EXAMPLE = Path(__file__).parent / "fixtures" / "live_rule_replay_example.json"


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text())


def event(kind, time, prices, **extra):
    return {
        "type": kind, "timestamp": time, "session": time[:10],
        "broker_quotes": {t: {"price": p, "observed_at": time} for t, p in prices.items()},
        **extra,
    }


def one_name(data):
    """Single, fully invested synthetic name at exactly $100 without costs."""
    data = copy.deepcopy(data)
    data["costs"] = dict(commission_per_share=0, commission_min=0, slippage_bps=0)
    data["decision_config"]["max_positions"] = 1
    data["events"] = data["events"][:1]
    buy = data["events"][0]
    buy["triggers"] = [buy["triggers"][1]]
    buy["entry_quotes"].pop("SYNTH_A")
    buy["broker_quotes"].pop("SYNTH_A")
    return data


def finish(data, price=100, time="2026-09-28T12:00:00-04:00"):
    data["events"].append(event("end_mark", time, {"SYNTH_B": price}))
    return replay.replay(data)["baseline"]


def next_buy(data, time, **trigger_changes):
    buy = copy.deepcopy(data["events"][0])
    buy["timestamp"], buy["session"] = time, time[:10]
    buy["cycle_gates"]["observed_at"] = time
    for quotes in (buy["entry_quotes"], buy["broker_quotes"]):
        for value in quotes.values():
            value["observed_at"] = time
    for trigger in buy["triggers"]:
        trigger["observed_at"] = time
        trigger["triggered_at"] = time
        trigger.update(trigger_changes)
    return buy


def eod(data, day, price, fresh=()):
    data["events"].append(event("monitor", f"{day}T15:45:00-04:00", {"SYNTH_B": price}))
    data["events"].append(event(
        "eod_latch", f"{day}T15:45:01-04:00", {"SYNTH_B": price},
        observed_at=f"{day}T15:45:01-04:00", fresh_trigger_tickers=list(fresh)))


def test_example_compare_net_equity_open_marks_costs_and_input_immutable(data):
    before = copy.deepcopy(data)
    result = replay.replay(data, compare_without_ai_veto=True)
    assert data == before
    baseline, variant = result["baseline"], result["variant"]
    assert result["simulation"] == replay.LABEL
    assert "SYNTHETIC" in result["dataset_label"]
    assert any("monitoring.py does NOT call exit_core" in note for note in result["caveats"])
    assert result["effective_config"]["shared_exit_rules"] == replay.shared_rule_snapshot()
    assert result["net_final_equity_difference"] == pytest.approx(
        variant["final_equity_net"] - baseline["final_equity_net"])
    assert {f["ticker"] for f in baseline["fills"]} == {"SYNTH_B"}
    assert {f["ticker"] for f in variant["fills"]} == {"SYNTH_A", "SYNTH_B"}
    for run in (baseline, variant):
        assert run["open_positions"]
        assert run["final_equity_net"] == pytest.approx(run["cash"] + run["open_market_value"])
        assert run["commission"] == pytest.approx(sum(f["commission"] for f in run["fills"]))
        assert run["cash"] == pytest.approx(10000 + sum(
            (1 if f["side"] == "SELL" else -1) * f["shares"] * f["price"] - f["commission"]
            for f in run["fills"]))
        pos = run["open_positions"][0]
        assert pos["buy_price"] == pytest.approx(100.05)
        assert run["brackets"][0]["hard_price"] == round(pos["buy_price"] * (1-er.PROVE_IT_P1_DAY0_PCT), 2)
        assert pos["closed_above_entry"]
    assert not any(d["action"] == ec.SCALE_OUT and d["timestamp"][11:16] == "10:00"
                   for d in variant["decisions"])


def test_hypothetical_replay_never_reads_or_changes_live_entry_permission(data, monkeypatch):
    import trading_control

    def forbidden(*args, **kwargs):
        pytest.fail("Hypothetical replay must not consult or acquire the real entry gate")

    monkeypatch.setattr(trading_control, "entries_allowed", forbidden)
    monkeypatch.setattr(trading_control, "entry_submission", forbidden)
    result = replay.replay(data, compare_without_ai_veto=True)
    assert any(fill["side"] == "BUY" for fill in result["baseline"]["fills"])
    assert any(fill["side"] == "BUY" for fill in result["variant"]["fills"])


def test_ranking_is_final_score_not_adjusted(data):
    data["decision_config"]["max_positions"] = 1
    first, second = data["events"][0]["triggers"]
    first.update(ai_grade="B", adjusted_score=65)
    second["adjusted_score"] = 99
    result = replay.replay(data)["baseline"]
    assert result["fills"][0]["ticker"] == "SYNTH_A"


def test_ai_ablation_keeps_score_floor_and_earnings(data):
    data["events"][0]["triggers"][0]["adjusted_score"] = 1
    result = replay.replay(data, compare_without_ai_veto=True)
    assert result["variant"]["decisions"][0]["reason"] == "SCORE_FLOOR"
    assert result["baseline"]["decisions"][0]["reason"] == "AI_VETO"
    assert result["net_final_equity_difference"] == 0
    data["events"][0]["triggers"][0].update(adjusted_score=85, next_earnings_date="2026-09-29")
    result = replay.replay(data, compare_without_ai_veto=True)
    assert result["variant"]["decisions"][0]["reason"] == "EARNINGS_IMMINENT"


def test_missing_ai_score_is_not_backfilled(data):
    data["events"][0]["triggers"][1].update(final_score=None, adjusted_score=None)
    result = replay.replay(data)["baseline"]
    assert not result["fills"]
    assert any(d["reason"] == "NO_AI_SCORE" for d in result["decisions"])


@pytest.mark.parametrize("gate,value,reason", [
    ("schema_ok", False, "SCHEMA_BLOCK"),
    ("margin_loan", 1, "MARGIN_BLOCK"),
    ("market_allowed", False, "MARKET_BLOCK"),
])
def test_cycle_wide_recorded_gates_precede_candidates(data, gate, value, reason):
    data["events"][0]["cycle_gates"][gate] = value
    run = replay.replay(data)["baseline"]
    assert not run["fills"]
    assert {d["reason"] for d in run["decisions"]} == {reason}


def test_precycle_full_book_precedes_already_held_and_ai_veto(data):
    data = one_name(data)
    data["events"].append(next_buy(data, "2026-09-28T10:00:00-04:00", ai_grade="D"))
    run = finish(data)
    assert run["decisions"][1]["reason"] == "SLOTS_FULL"


def test_midcycle_eligibility_precedes_capacity(data):
    data["decision_config"]["max_positions"] = 1
    first, second = data["events"][0]["triggers"]
    first["ai_grade"] = "B"
    second["ai_grade"] = "D"
    run = replay.replay(data)["baseline"]
    assert run["decisions"][1]["reason"] == "AI_VETO"


def test_delayed_entry_quote_sizes_but_actual_fill_sets_basis(data):
    data = one_name(data)
    data["events"][0]["entry_quotes"]["SYNTH_B"]["price"] = 101
    data["events"][0]["broker_quotes"]["SYNTH_B"]["price"] = 100
    run = finish(data)
    fill = run["fills"][0]
    assert fill["shares"] == int((10000-100)/101)
    assert run["open_positions"][0]["buy_price"] == 100


def test_subcent_fill_uses_live_rounded_rule_basis_but_raw_cash(data):
    data = one_name(data)
    data["costs"]["slippage_bps"] = 3.14159
    run = finish(data)
    pos, fill = run["open_positions"][0], run["fills"][0]
    raw_fill = 100 * (1 + 3.14159 / 10000)
    assert fill["price"] == raw_fill
    assert pos["execution_buy_price"] == raw_fill
    assert pos["buy_price"] == round(raw_fill, 2)
    assert pos["buy_price"] != raw_fill
    assert pos["hwm_price"] == pos["buy_price"]
    assert run["cash"] == pytest.approx(10000 - raw_fill * fill["shares"])
    assert run["brackets"][0]["hard_price"] == er.hard_stop_price(
        pos, round(raw_fill, 2), 0, False, 0)
    assert run["net_profit"] == pytest.approx((100 - raw_fill) * fill["shares"])


def test_do_not_resize_or_borrow_when_delayed_price_fill_exceeds_cash(data):
    data = one_name(data)
    data["events"][0]["broker_quotes"]["SYNTH_B"]["price"] = 120
    with pytest.raises(replay.ReplayInputError, match="do not silently resize"):
        finish(data)


def test_static_hard_stop_gap_fills_at_sample_not_perfect_level(data):
    data = one_name(data)
    data["events"].append(event("quote", "2026-09-28T09:40:00-04:00", {"SYNTH_B": 97}))
    run = finish(data, 97)
    sell = run["fills"][1]
    assert sell["reason"] == "BROKER_HARD_STOP"
    assert sell["price"] == 97
    assert run["brackets"][0]["hard_price"] == 99


def test_broker_ratchets_between_monitors_without_bot_peak_lookahead(data):
    data = one_name(data)
    data["events"].extend([
        event("quote", "2026-09-28T10:01:00-04:00", {"SYNTH_B": 112}),
        event("quote", "2026-09-28T10:02:00-04:00", {"SYNTH_B": 100.5}),
    ])
    run = finish(data)
    assert run["fills"][1]["reason"] == "BROKER_TRAIL"
    assert run["fills"][1]["price"] == 100.5
    assert len(run["decisions"]) == 1  # No synthetic monitor at 10:00 or bot power latch.


def test_scale_out_whole_shares_once_and_still_one_slot(data):
    data = one_name(data)
    data["events"].extend([
        event("monitor", "2026-09-28T10:07:00-04:00", {"SYNTH_B": 104.5}),
        event("monitor", "2026-09-28T10:17:00-04:00", {"SYNTH_B": 104.5}),
        next_buy(data, "2026-09-28T10:18:00-04:00"),
    ])
    # Keep the candidate/broker quote above the still-resting hard stop.
    data["events"][-1]["broker_quotes"]["SYNTH_B"]["price"] = 104.5
    run = finish(data, 104.5)
    assert run["fills"][0]["shares"] == 99
    assert run["fills"][1]["shares"] == int(99 * data["exit_config"]["scale_out_fraction"])
    assert len(run["fills"]) == 2
    assert run["open_positions"][0]["shares"] == 67
    assert run["decisions"][-1]["reason"] == "SLOTS_FULL"
    assert run["brackets"][1]["anchor"] == 104.5
    assert run["brackets"][1]["shares"] == 67
    assert run["brackets"][1]["trail_pct"] == run["brackets"][0]["trail_pct"]
    assert run["brackets"][1]["hard_price"] == er.hard_stop_price(
        {"closed_above_entry": False}, 100, 4.5, False, 0)


def test_replacement_resets_anchor_not_historical_high(data):
    data = one_name(data)
    data["exit_config"]["scale_out_enabled"] = False
    data["events"].append(event("quote", "2026-09-28T10:00:00-04:00", {"SYNTH_B": 110}))
    eod(data, "2026-09-28", 104)
    data["events"].append(event("monitor", "2026-09-29T09:30:00-04:00", {"SYNTH_B": 102}))
    run = finish(data, 100, "2026-09-29T10:00:00-04:00")
    assert run["open_positions"]
    assert run["brackets"][-1]["anchor"] == 102
    assert run["open_positions"][0]["highest_unrealized_pct"] == 4
    assert run["brackets"][-1]["trail_pct"] == pytest.approx(
        round(er.prove_it_trail_pct(99, 102, "phase2") * 100, 2) / 100)


def test_scale_out_changes_broker_hard_but_not_persisted_hard(data):
    data = one_name(data)
    eod(data, "2026-09-28", 100)  # Unproven; stored day-0 hard stop remains $99.
    data["events"].extend([
        event("monitor", "2026-09-29T09:30:00-04:00", {"SYNTH_B": 104.5}),
        event("quote", "2026-09-29T09:31:00-04:00", {"SYNTH_B": 110}),
        event("monitor", "2026-09-29T09:32:00-04:00", {"SYNTH_B": 104}),
    ])
    run = finish(data, 98.5, "2026-09-29T09:33:00-04:00")
    partial_bracket, refreshed = run["brackets"][-2:]
    assert partial_bracket["hard_price"] == 97
    assert partial_bracket["stored_hard_price"] == 99
    assert refreshed["hard_price"] == refreshed["stored_hard_price"] == 97
    assert refreshed["anchor"] == 104
    assert run["open_positions"][0]["broker_anchor"] == 104
    assert len(run["fills"]) == 2  # No false $98.50 sale from the old $110 anchor.


def test_final_close_bears_entire_entry_fee_and_cools_next_day(data):
    data = one_name(data)
    data["initial_cash"] = 500  # $100 reserve leaves exactly four $100 shares.
    data["costs"]["commission_min"] = 0.35
    data["events"].extend([
        event("monitor", "2026-09-28T10:00:00-04:00", {"SYNTH_B": 104.5}),  # Sell one.
        event("quote", "2026-09-28T10:01:00-04:00", {"SYNTH_B": 112}),
        event("quote", "2026-09-28T10:02:00-04:00", {"SYNTH_B": 100.22}),  # Trail sells three.
        next_buy(data, "2026-09-29T09:30:00-04:00"),
    ])
    run = finish(data, 100, "2026-09-29T10:00:00-04:00")
    assert [fill["shares"] for fill in run["fills"]] == [4, 1, 3]
    partial, final = run["cooldown_ledger"]
    assert partial["profit_loss"] == 4.5
    assert partial["buy_commission"] is None
    assert partial["net_profit_loss"] == 4.15
    assert final["profit_loss"] == 0.66
    assert final["buy_commission"] == final["sell_commission"] == 0.35
    assert final["net_profit_loss"] == -0.04
    assert run["decisions"][-1]["reason"] == "COOLING_OFF"
    assert run["cash"] == pytest.approx(500 + 4.5 + 0.66 - 3 * 0.35)
    assert run["commission"] == pytest.approx(3 * 0.35)  # Attribution is not another cash debit.


def test_cooldown_ledger_rounds_gross_not_sell_fill_or_net_again(data):
    data = one_name(data)
    data["initial_cash"] = 500
    data["costs"]["commission_min"] = 0.3501
    data["events"].extend([
        event("quote", "2026-09-28T10:01:00-04:00", {"SYNTH_B": 112}),
        event("quote", "2026-09-28T10:02:00-04:00", {"SYNTH_B": 100.1749}),
    ])
    run = finish(data)
    sale = run["cooldown_ledger"][0]
    assert sale["sell_price"] == 100.1749
    assert sale["profit_loss"] == 0.7
    assert sale["net_profit_loss"] == -0.0002
    assert run["cash"] == pytest.approx(500 + (100.1749 - 100) * 4 - 0.7002)


def test_proven_latch_only_after_intraday_monitor(data):
    data = one_name(data)
    eod(data, "2026-09-28", 101)
    data["events"].append(event("monitor", "2026-09-29T09:30:00-04:00", {"SYNTH_B": 101}))
    run = finish(data, 101, "2026-09-29T10:00:00-04:00")
    phases = [d["exit_decision"]["prove_it_phase"] for d in run["decisions"] if "exit_decision" in d]
    assert phases == ["phase1", "phase2-unarmed"]


def test_trading_age_calendar_age_and_phase1_widening(data):
    data = one_name(data)
    buy = next_buy(data, "2026-10-02T09:30:00-04:00")
    data["events"] = [buy]
    eod(data, "2026-10-02", 99.5)
    data["events"].append(event("monitor", "2026-10-05T09:30:00-04:00", {"SYNTH_B": 100}))
    run = finish(data, 100, "2026-10-05T10:00:00-04:00")
    assert run["decisions"][-1]["days_held"] == 1
    assert run["decisions"][-1]["calendar_days"] == 3
    assert run["brackets"][-1]["hard_price"] == 97


def test_old_day0_resting_band_still_applies_before_next_day_monitor(data):
    data = one_name(data)
    eod(data, "2026-09-28", 99.5)
    data["events"].append(event("monitor", "2026-09-29T09:30:00-04:00", {"SYNTH_B": 98}))
    run = finish(data, 98, "2026-09-29T10:00:00-04:00")
    assert run["fills"][-1]["reason"] == "BROKER_HARD_STOP"
    assert run["fills"][-1]["price"] == 98


def test_cooldown_recomputed_from_simulated_loss_and_same_session_profit(data, monkeypatch):
    original = cooling_off.compute_cooled_map
    observed = []

    def capture(client, today, days):
        result = original(client, today, days)
        observed.append((copy.deepcopy(client.sales), result))
        return result

    monkeypatch.setattr(cooling_off, "compute_cooled_map", capture)
    data = one_name(data)
    data["events"].append(event("quote", "2026-09-28T10:00:00-04:00", {"SYNTH_B": 98}))
    data["events"].append(next_buy(data, "2026-09-28T10:10:00-04:00"))
    run = finish(data)
    assert run["decisions"][-1]["reason"] == "COOLING_OFF"
    assert observed[-1][0][0]["profit_loss"] < 0
    assert "sold today" in observed[-1][1]["SYNTH_B"]
    ledger = replay._Ledger()
    ledger.sales = [dict(ticker="SYNTH_B", sell_date="2026-09-28T10:00:00-04:00",
                        profit_loss=10, net_profit_loss=9, sell_reason="Partial scale-out")]
    assert "SYNTH_B" in original(ledger, dt.date(2026, 9, 28), 5)
    assert "SYNTH_B" not in original(ledger, dt.date(2026, 9, 29), 5)
    ledger.sales[0]["net_profit_loss"] = -1
    assert "SYNTH_B" in original(ledger, dt.date(2026, 9, 29), 5)


def test_partial_sale_is_recorded_in_shared_cooldown_ledger(data, monkeypatch):
    calls = []
    original = cooling_off.compute_cooled_map

    def capture(client, today, days):
        calls.append(original(client, today, days))
        return calls[-1]

    monkeypatch.setattr(cooling_off, "compute_cooled_map", capture)
    data = one_name(data)
    data["decision_config"]["max_positions"] = 2
    data["events"].append(event("monitor", "2026-09-28T10:07:00-04:00", {"SYNTH_B": 104.5}))
    data["events"].append(next_buy(data, "2026-09-28T10:08:00-04:00"))
    finish(data, 104.5)
    assert "SYNTH_B" in calls[-1]  # Held gate precedes cooldown, but sale is still recorded.


def test_delegates_every_entry_core_and_exit_core(data, monkeypatch):
    calls = {}
    names = ("rank_triggers", "evaluate_eligibility", "equity_capped_position_size",
             "evaluate_capacity", "evaluate_cash", "evaluate_market_gates", "evaluate_price_gates")
    for name in names:
        original = getattr(dc, name)

        def wrapper(*args, _original=original, _name=name, **kwargs):
            calls[_name] = calls.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(dc, name, wrapper)
    original_exit = ec.evaluate_exit
    verdicts = []

    def capture_exit(pos, ctx, cfg):
        result = original_exit(pos, ctx, cfg)
        verdicts.append(result)
        return result

    monkeypatch.setattr(ec, "evaluate_exit", capture_exit)
    run = replay.replay(data)["baseline"]
    assert all(calls[name] for name in names)
    assert [d["action"] for d in run["decisions"] if "exit_decision" in d] == [
        v.action for v in verdicts]


def test_shared_protective_math_is_used_not_mirrored(data, monkeypatch):
    data = one_name(data)
    calls = []
    original = er.hard_stop_price

    def capture(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(er, "hard_stop_price", capture)
    data["events"].append(event("monitor", "2026-09-28T10:07:00-04:00", {"SYNTH_B": 100}))
    run = finish(data)
    assert len(calls) >= 2
    assert run["brackets"][0]["hard_price"] == original(
        {"closed_above_entry": False}, 100, 0, False, 0)


def test_power_hold_latches_and_suppresses_partial(data):
    data = one_name(data)
    data["events"].append(event("monitor", "2026-09-28T10:07:00-04:00", {"SYNTH_B": 111}))
    data["events"].append(event("monitor", "2026-09-28T10:08:00-04:00", {"SYNTH_B": 103}))
    run = finish(data, 103)
    pos = run["open_positions"][0]
    assert pos["power_hold"]
    assert not pos["scaled_out"]
    assert pos["stop_loss_pct"] == er.POWER_HOLD_TRAIL_PCT
    assert pos["hard_stop_price"] == 100 * (1 - er.MAX_LOSS_PCT)


def test_armed_trail_and_deadline_delegate_to_exit_core(data):
    # Explicitly initialize a scoped unit harness in a reachable Phase-2 state:
    # current loss floor (99) sits above static broker backstop (98.01), base trail
    # has not yet been tightened. The high was observed at this monitor, not a bar.
    data = one_name(data)
    data["events"].append(event("end_mark", "2026-09-28T10:00:00-04:00", {"SYNTH_B": 100}))
    engine = replay._Replay(data, False)
    engine.run()
    pos = engine.positions["SYNTH_B"]
    pos.update(closed_above_entry=True, highest_unrealized_pct=3, hard_stop_price=98.01)
    engine.now = replay._timestamp("2026-09-28T11:00:00-04:00", "test")
    engine.quotes = {"SYNTH_B": {"price": 98.5}}
    engine._monitor()
    assert pos["exit_armed"]
    assert pos["broker_trail_pct"] == data["replay_config"]["armed_exit_trail_pct"]
    assert pos["broker_hard_stop_price"] == 0  # arm_exit cancels both old bracket legs.
    assert pos["hard_stop_price"] == 98.01  # It does not persist a new hard-stop column.
    assert engine.decisions[-1]["action"] == ec.ARM_PROVE_IT
    engine.now += dt.timedelta(hours=3)
    engine._monitor()
    assert engine.decisions[-1]["action"] == ec.AWAIT_ARMED
    engine.now += dt.timedelta(minutes=15)
    engine._monitor()
    assert engine.decisions[-1]["action"] == ec.SELL_DEADLINE
    assert not engine.positions


def test_power_hold_expiry_is_shared_core_behavior(data):
    data = one_name(data)
    data["events"].append(event("end_mark", "2026-09-28T10:00:00-04:00", {"SYNTH_B": 100}))
    engine = replay._Replay(data, False)
    engine.run()
    pos = engine.positions["SYNTH_B"]
    pos.update(power_hold=True, highest_unrealized_pct=11, closed_above_entry=True)
    engine.today += dt.timedelta(days=er.POWER_HOLD_DURATION_DAYS + 1)
    engine.now += dt.timedelta(days=er.POWER_HOLD_DURATION_DAYS + 1)
    engine.quotes = {"SYNTH_B": {"price": 104}}
    engine._monitor()
    assert not engine.decisions[-1]["exit_decision"]["power_held"]
    assert engine.decisions[-1]["action"] == ec.SCALE_OUT


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.update(events=[]), "empty dataset"),
    (lambda d: d.update(initial_cash=0), "initial_cash"),
    (lambda d: d.update(initial_positions=[{"ticker": "OLD"}]), "Start flat"),
    (lambda d: d["events"][0].pop("cycle_gates"), "missing"),
    (lambda d: d["events"][0]["triggers"][0].pop("ai_grade"), "missing"),
    (lambda d: d["events"][0]["triggers"][0].pop("next_earnings_date"), "missing"),
    (lambda d: d["decision_config"].pop("max_positions"), "missing"),
    (lambda d: d["costs"].update(commission_min=-1), "commission_min"),
    (lambda d: d["costs"].update(commission_min=float("nan")), "finite"),
    (lambda d: d["events"][0]["broker_quotes"]["SYNTH_B"].update(price=float("inf")), "finite"),
    (lambda d: d["costs"].update(slippage_bps=10000), "slippage_bps"),
    (lambda d: d["exit_config"].update(power_hold_trail_pct=0.2), "must match"),
    (lambda d: d["shared_exit_rules"].update(PROVE_IT_P1_DAY0_PCT=0.02), "snapshot differs"),
    (lambda d: d["events"][0]["triggers"][0].update(observed_at="2027-01-01T09:00:00-05:00"), "future"),
    (lambda d: d["events"][1].update(timestamp=d["events"][0]["timestamp"]), "strictly increasing"),
    (lambda d: d["events"][0].update(timestamp="2026-09-28T09:30:00"), "timezone"),
    (lambda d: d["events"][0].update(session="2026-09-29"), "New York date"),
    (lambda d: d["events"][0]["broker_quotes"]["SYNTH_B"].update(price=0), "positive"),
    (lambda d: d["events"][0]["broker_quotes"]["SYNTH_B"].update(observed_at="2026-09-28T09:00:00-04:00"), "current observation"),
    (lambda d: d["events"][0]["triggers"][0].update(next_earnings_date="unknown"), "YYYY-MM-DD"),
    (lambda d: d["events"][-1].update(type="manual_exit"), "unsupported event"),
    (lambda d: d["scope"].update(external_activity="manual exit"), "unsupported conditions"),
    (lambda d: d["events"][-1]["broker_quotes"].pop("SYNTH_B"), "missing current held marks"),
    (lambda d: d["events"].pop(), "finish with end_mark"),
    (lambda d: d["events"].pop(-2), "must be followed immediately"),
])
def test_missing_inconsistent_and_unsupported_inputs_fail(data, mutate, match):
    mutate(data)
    with pytest.raises(replay.ReplayInputError, match=match):
        replay.replay(data)


def test_eod_before_monitor_rejected(data):
    data["events"].pop(3)
    with pytest.raises(replay.ReplayInputError, match="preceding"):
        replay.replay(data)


def test_missing_overnight_eod_rejected(data):
    data = one_name(data)
    with pytest.raises(replay.ReplayInputError, match="Held overnight"):
        finish(data, 100, "2026-09-29T10:00:00-04:00")


def test_empty_or_nonobject_dataset_rejected():
    for invalid in ({}, None, []):
        with pytest.raises(replay.ReplayInputError, match="dataset"):
            replay.replay(invalid)


def test_old_audit_rows_are_not_accepted_as_historical_capture():
    with pytest.raises(replay.ReplayInputError, match="trade_history alone are insufficient"):
        replay.replay({"trigger_decisions": [], "trade_history": []})


def test_comparison_requires_marks_for_variant_only_holdings(data):
    data["events"][-1]["broker_quotes"].pop("SYNTH_A")
    replay.replay(data)  # Baseline never held the D-grade candidate.
    with pytest.raises(replay.ReplayInputError, match="variant-only"):
        replay.replay(data, compare_without_ai_veto=True)


def test_reject_potential_eod_rotation_not_silent_parity(data):
    data = one_name(data)
    for day in ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01",
                "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"):
        eod(data, day, 100, fresh=["SYNTH_OTHER"])
    with pytest.raises(replay.ReplayInputError, match="Rank & Replace could apply"):
        finish(data, 100, "2026-10-07T15:46:00-04:00")


def test_cli_help_and_comparison_and_no_execution_agent_import():
    help_run = subprocess.run([sys.executable, "-m", "research.live_rule_replay", "--help"],
                              capture_output=True, text=True, check=True)
    assert "--compare-without-ai-veto" in help_run.stdout
    run = subprocess.run([sys.executable, "research/live_rule_replay.py", str(EXAMPLE),
                          "--compare-without-ai-veto"], capture_output=True, text=True, check=True)
    assert "net_final_equity_difference" in json.loads(run.stdout)
    isolated = subprocess.run([sys.executable, "-c",
        "import research.live_rule_replay; import sys; assert 'execution_agent' not in sys.modules"],
        capture_output=True, text=True)
    assert isolated.returncode == 0, isolated.stderr
