"""Synthetic account evidence and observations; never real account activity."""
import copy
import datetime as dt
import json
from itertools import groupby
import socket

import pytest

import intraday_replay as capture
import shadow_engine as shadow
from market_calendar import session_bounds
from research import live_rule_replay as core
from test_intraday_replay import DAY, records  # noqa: F401


def seed_and_frames(data):
    seed = copy.deepcopy(data["initial_state"])
    seed.update(origin="actual_account_seed", manual_requests=[], rotation=False, unsupported_orders=[])
    seed["config"] = {k: copy.deepcopy(data[k]) for k in capture.CONFIG_KEYS}
    stamp = seed["timestamp"]
    seed["source_evidence"] = {
        name: dict(received_at=stamp, filter_lte=stamp, filter_gte="2026-01-01T00:00:00-05:00")
        for name in ("broker", "portfolio_positions", "trade_history", "ibkr_fills")}
    seed["source_evidence"]["quantities"] = [
        {k: p[k] for k in ("ticker", "shares", "account", "sec_type", "currency")}
        for p in seed["broker_positions"]]
    for order in seed["orders"]:
        order.update(filled=0, remaining=order["shares"], protection_source="bot",
                     outside_rth=False, trigger_method=0)
    frames = [dict(frame_id=f"frame-{stamp}", captured_at=stamp, events=list(rows))
              for stamp, rows in groupby(copy.deepcopy(data["events"]), lambda e: e["timestamp"])]
    return seed, frames


@pytest.fixture
def shadow_inputs(records):
    return seed_and_frames(capture.build_dataset(records, DAY, DAY))


def durable_records(seed, frames):
    state = shadow.initialize(seed, seed["config"])
    rows = []
    for frame in frames:
        state, output = shadow.advance(state, frame)
        rows.append(dict(frame=frame, output=output, checkpoint=shadow.checkpoint(state)))
    return state, rows


def test_incremental_equals_batch_and_restart_is_exact(records, monkeypatch):
    data = capture.build_dataset(records, DAY, DAY)
    for event in data["events"]:
        for trigger in event.get("triggers", []):
            trigger["ai_grade"] = "A"
    seed, frames = seed_and_frames(data)
    before = copy.deepcopy((seed, frames))
    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in the pure engine")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    expected = core._Replay(data, False).run()
    state = shadow.initialize(seed, seed["config"])
    fills = []
    for frame in frames:
        state = shadow.restore(json.loads(json.dumps(shadow.checkpoint(state), sort_keys=True)))
        state, output = shadow.advance(state, frame)
        fills.extend(output["fills"])
    assert state["cash"] == expected["cash"]
    assert state["equity"] == expected["final_equity_net"]
    assert {p["ticker"]: p for p in state["positions"].values()} == {
        p["ticker"]: p for p in expected["open_positions"]}
    assert fills == expected["fills"]
    assert all(f["execution"] == "counterfactual_sampled_full_fill" for f in fills)
    assert (seed, frames) == before


def test_retry_contract_and_future_stale_inputs(shadow_inputs):
    seed, frames = shadow_inputs
    initial = shadow.initialize(seed, seed["config"])
    state, output = shadow.advance(initial, frames[0])
    assert shadow.advance(state, frames[0]) == (state, output)
    altered = copy.deepcopy(frames[0])
    altered["events"][0]["market_observations"]["HELD"]["price"] += 1
    with pytest.raises(shadow.ShadowError, match="Conflicting duplicate"):
        shadow.advance(state, altered)
    for quote_time in ("2026-09-28T09:36:00-04:00", "2026-09-28T09:20:00-04:00"):
        bad = copy.deepcopy(frames[1])
        bad["events"][0]["market_observations"]["HELD"]["observed_at"] = quote_time
        with pytest.raises(shadow.ShadowError, match="future|stale"):
            shadow.advance(state, bad)
    with pytest.raises(shadow.ShadowError, match="outage"):
        shadow.advance(state, frames[4])
    assert initial["frame_count"] == 0


def test_raw_feature_evidence_is_bound_to_input_and_duplicate_identity(shadow_inputs):
    seed, frames = shadow_inputs
    frame = frames[0]
    frame["source_evidence"] = {"daily_bars": [{"date": "2026-09-25", "close": 100}],
                                "provider_received_at": frame["captured_at"]}
    state, output = shadow.advance(shadow.initialize(seed, seed["config"]), frame)
    assert output["input_evidence"]["source_evidence_sha256"] == shadow._digest(frame["source_evidence"])
    frame["source_evidence"]["daily_bars"][0]["close"] = 99
    with pytest.raises(shadow.ShadowError, match="Conflicting duplicate"):
        shadow.advance(state, frame)


def test_history_references_require_previously_recorded_full_content(shadow_inputs):
    seed, frames = shadow_inputs
    initial = shadow.initialize(seed, seed["config"])
    history = {"available_at": frames[0]["captured_at"],
               "bars": [{"date": "2026-09-25", "close": 100}]}
    proof = {"sha256": shadow._digest(history), "available_at": history["available_at"]}
    frames[0]["source_evidence"] = {"daily_history": {
        "HELD": dict(proof, reference="earlier_committed_frame_in_same_run")}}
    with pytest.raises(shadow.ShadowError, match="hash alone"):
        shadow.advance(initial, frames[0])
    frames[0]["source_evidence"]["daily_history"]["HELD"] = dict(proof, data=history)
    state, _ = shadow.advance(initial, frames[0])
    frames[1]["source_evidence"] = {"daily_history": {
        "HELD": dict(proof, reference="earlier_committed_frame_in_same_run")}}
    state, _ = shadow.advance(shadow.restore(state), frames[1])
    assert state["history_digests"] == [proof["sha256"]]


@pytest.mark.parametrize("mutate,match", [
    (lambda s: s["positions"][0].update(shares=1.5), "integer"),
    (lambda s: s["positions"][0].update(shares=-1), "integer"),
    (lambda s: s["orders"][0].update(protection_source="manual"), "Manual"),
    (lambda s: s["orders"][0].update(remaining=9), "partial"),
    (lambda s: s["orders"][0].update(trail_stop_price=None), "finite"),
    (lambda s: s["source_evidence"]["quantities"][0].update(shares=9), "quantities disagree"),
    (lambda s: s["source_evidence"]["trade_history"].update(filter_lte=None), "cutoff"),
    (lambda s: s["source_evidence"]["broker"].update(received_at="2026-09-28T09:00:00-04:00"), "Stale"),
    (lambda s: s.update(rotation=True), "rotation"),
    (lambda s: s["positions"][0].pop("entry_fee_remaining"), "missing fields"),
])
def test_unsupported_actual_seed_blocks(shadow_inputs, mutate, match):
    seed, _ = shadow_inputs
    mutate(seed)
    with pytest.raises((shadow.ShadowError, capture.CaptureError), match=match):
        shadow.initialize(seed, seed["config"])


def test_checkpoint_version_and_corruption_fail_closed(shadow_inputs):
    seed, _ = shadow_inputs
    state = shadow.initialize(seed, seed["config"])
    bad = copy.deepcopy(state)
    bad["cash"] += 1
    with pytest.raises(shadow.ShadowError, match="integrity"):
        shadow.restore(bad)
    bad["version"] += 1
    with pytest.raises(shadow.ShadowError, match="version"):
        shadow.restore(bad)


def test_arming_is_hold_until_hypothetical_stop_fill(shadow_inputs):
    seed, frames = shadow_inputs
    seed["positions"][0].update(closed_above_entry=False, buy_date=DAY)
    for event in frames[0]["events"]:
        event["market_observations"]["HELD"]["price"] = 98.9
    state, output = shadow.advance(shadow.initialize(seed, seed["config"]), frames[0])
    arms = [d for d in output["decisions"] if d["action"] == "ARM_PROVE_IT"]
    assert arms and arms[0]["portfolio_action"] == "HOLD"
    assert not output["fills"]
    for event in frames[1]["events"]:
        event["market_observations"]["HELD"]["price"] = 98
    state, output = shadow.advance(state, frames[1])
    assert output["fills"][0]["side"] == "SELL"
    assert any(d["portfolio_action"] == "SELL" for d in output["decisions"])
    assert "HELD" not in state["positions"]


def test_exchange_session_bounds_early_close_holiday_dst():
    assert session_bounds("2026-11-27")[1].hour == 13
    assert session_bounds("2026-11-26") is None
    assert session_bounds("2026-03-06")[0].utcoffset() == dt.timedelta(hours=-5)
    assert session_bounds("2026-03-09")[0].utcoffset() == dt.timedelta(hours=-4)


def test_unsupported_rotation_blocks_without_mutating_checkpoint(shadow_inputs):
    seed, frames = shadow_inputs
    seed["positions"][0]["buy_date"] = "2026-09-10"
    seed["config"]["decision_config"]["max_positions"] = 1
    state = shadow.initialize(seed, seed["config"])
    for frame in frames:
        if any(e["type"] == "eod_latch" for e in frame["events"]):
            before = copy.deepcopy(state)
            with pytest.raises(shadow.ShadowError, match="Rank & Replace"):
                shadow.advance(state, frame)
            assert state == before
            break
        state, _ = shadow.advance(state, frame)
    else:
        pytest.fail("Fixture needs its EOD latch")


def test_genuine_mid_session_seed_can_start_without_fabricating_morning(shadow_inputs):
    seed, frames = shadow_inputs
    stamp = DAY + "T12:00:00-04:00"
    seed["timestamp"] = stamp
    for source in ("broker", "portfolio_positions", "trade_history", "ibkr_fills"):
        seed["source_evidence"][source].update(received_at=stamp, filter_lte=stamp)
    seed["broker_positions"][0]["observed_at"] = stamp
    first = next(f for f in frames if f["captured_at"] == stamp)
    state, _ = shadow.advance(shadow.initialize(seed, seed["config"]), first)
    assert state["frame_count"] == 1
    assert state["seed"]["timestamp"] == stamp
    assert state["previous"]["type"] == "monitor"


def test_quote_only_prefix_cannot_hide_missing_decision_cycles(shadow_inputs):
    seed, frames = shadow_inputs
    seed.update(positions=[], broker_positions=[], orders=[])
    seed["account"]["positions_value"] = 0
    seed["source_evidence"]["quantities"] = []
    state = shadow.initialize(seed, seed["config"])
    for index, frame in enumerate(frames[:6]):
        first = frame["events"][0]
        frame["events"] = [{k: first[k] for k in (
            "timestamp", "session", "market_observations")} | {"type": "quote"}]
        if index == 5:
            with pytest.raises(shadow.ShadowError, match="quote-only coverage"):
                shadow.advance(state, frame)
        else:
            state, _ = shadow.advance(state, frame)
