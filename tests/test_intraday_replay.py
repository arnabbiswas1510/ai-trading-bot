"""Synthetic captured inputs: actual-start semantics without network or live writes."""
import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from intraday_replay import CaptureError, build_dataset, run_comparison
from research import live_rule_replay as core


DAY = "2026-09-28"


@pytest.fixture
def records():
    template = json.loads((Path(__file__).parent / "fixtures/live_rule_replay_example.json").read_text())
    config = {key: template[key] for key in (
        "decision_config", "exit_config", "replay_config", "costs", "shared_exit_rules")}
    config["costs"] = dict(commission_min=0, commission_per_share=0, slippage_bps=0)
    start = dt.datetime.fromisoformat(DAY + "T09:30:00-04:00")
    position = dict(
        ticker="HELD", shares=10, buy_price=100, buy_date="2026-09-25",
        stop_loss_pct=0.1, highest_unrealized_pct=1, hwm_price=101,
        closed_above_entry=True, scaled_out=False, scaled_out_at=None, power_hold=False,
        exit_armed=False, exit_armed_at=None, exit_armed_reason=None,
        entry_fee_remaining=0.35, hard_stop_price=95,
    )
    orders = [
        dict(ticker="HELD", account="U_TEST", sec_type="STK", action="SELL",
             order_type=kind, shares=10, order_id=i, parent_id=0,
             tif="GTC", oca_type=1,
             status="Submitted", oca_group="protected", trailing_percent=10,
             trail_stop_price=90.9, aux_price=95)
        for i, kind in enumerate(("TRAIL", "STP"), 1)
    ]
    result = []

    def record(kind, now, payload):
        result.append(dict(id=f"event-{len(result)}", run_id="run-1", sequence=len(result),
                           session=DAY, occurred_at=now.isoformat(), kind=kind, payload=payload))

    record("portfolio_snapshot", start, dict(
        timestamp=start.isoformat(), complete=True, stock_only=True, config=config,
        account=dict(account_id="U_TEST", currency="USD", net_liquidation=10000, positions_value=1010),
        positions=[position], broker_positions=[dict(
            ticker="HELD", account="U_TEST", sec_type="STK", currency="USD", shares=10, market_price=101,
            observed_at=start.isoformat())], orders=orders, prior_trade_history=[], prior_fills=[],
    ))
    trigger = copy.deepcopy(template["events"][0]["triggers"][0])
    trigger.update(ticker="CANDIDATE", observed_at=start.isoformat(), triggered_at=start.isoformat())
    for minute in range(0, 391, 5):
        now = start + dt.timedelta(minutes=minute)
        quotes = {ticker: dict(price=price, observed_at=now.isoformat(), source="IBKR", delayed=False)
                  for ticker, price in (("HELD", 101), ("CANDIDATE", 100))}
        kind = "monitor" if minute % 15 == 0 and minute < 390 else "quote_sample"
        payload = dict(complete=True, market_observations=quotes, missing_quotes=[])
        if kind == "monitor":
            buy_id, monitor_id = f"buy-{minute}", f"monitor-{minute}"
            record("buy_cycle", now, dict(
                **copy.deepcopy(payload), triggers=[dict(trigger, observed_at=now.isoformat())],
                cycle_context=dict(cycle_id=buy_id, started_at=now.isoformat(), completed_at=now.isoformat()),
                cycle_gates=dict(observed_at=now.isoformat(), schema_ok=True, margin_loan=0, market_allowed=True),
                entry_quotes={},
            ))
            payload["cycle_context"] = dict(
                cycle_id=monitor_id, started_at=now.isoformat(), completed_at=now.isoformat(),
                preceding_buy_cycle_id=buy_id,
            )
        record(kind, now, payload)
        if minute == 375:
            record("eod_latch", now, dict(
                **copy.deepcopy(payload), observed_at=now.isoformat(), fresh_trigger_tickers=["CANDIDATE"]))
    return result


def first(records, kind):
    return next(r for r in records if r["kind"] == kind)


def test_observer_data_cannot_masquerade_as_live_decision_capture(records):
    records[0]["payload"]["capture_mode"] = "observer"
    with pytest.raises(CaptureError, match="observer-only data lacks live decision inputs"):
        build_dataset(records, DAY, DAY)


def test_identical_actual_start_book_cash_and_source_labelled_fills(records):
    original = copy.deepcopy(records)
    dataset = build_dataset(records, DAY, DAY)
    assert dataset["initial_cash"] == 8990
    assert dataset["initial_positions"][0]["shares"] == 10
    assert dataset["initial_positions"][0]["broker_anchor"] == pytest.approx(101)
    result = run_comparison(records, DAY, DAY)
    assert records == original
    assert result["recommendation"] == "research_only_manual_approval"
    assert result["initial_state_mode"] == "recorded_actual_portfolio"
    assert result["effective_initial_account"]["net_liquidation"] == 10000
    assert "Recorded 1 open positions and 2 protective orders" in result["initial_state_summary"]
    assert result["baseline"]["initial_positions"] == result["variant"]["initial_positions"]
    for run in (result["baseline"], result["variant"]):
        assert run["initial_equity"] == 10000
        assert run["net_profit"] == pytest.approx(run["final_equity_net"] - 10000)
        assert run["sessions_count"] == 1
        assert run["equity_curve"][0]["equity"] == 10000
        assert run["closed_position_count"] == 0
        assert run["max_drawdown_pct"] == 0
        assert any(p["ticker"] == "HELD" and p["shares"] == 10 for p in run["open_positions"])
    assert result["comparison_sign"] == "equal"
    buy = result["variant"]["fills"][0]
    assert buy["observation"]["source"] == "IBKR"
    assert buy["execution"] == "counterfactual_sampled_full_fill"
    assert dataset["capture_evidence"]["assumptions"][0]["kind"] == "hypothetical_entry_pricing"


def test_prior_real_loss_prefills_cooldown_and_blocks_both_variants(records):
    first(records, "portfolio_snapshot")["payload"]["prior_trade_history"] = [dict(
        ticker="CANDIDATE", sell_date="2026-09-25T11:00:00-04:00",
        net_profit_loss=-100, profit_loss=-99, sell_reason="Real prior stop")]
    result = run_comparison(records, DAY, DAY)
    assert not result["baseline"]["fills"]
    assert not result["variant"]["fills"]
    assert any("COOL" in d["reason"] for d in result["variant"]["decisions"])


def test_prior_fill_same_session_blocks_reentry_without_ledger(records):
    first(records, "portfolio_snapshot")["payload"]["prior_fills"] = [dict(
        ticker="CANDIDATE", fill_time=DAY + "T09:29:00-04:00", side="SLD")]
    assert not run_comparison(records, DAY, DAY)["variant"]["fills"]


@pytest.mark.parametrize("value", [None, 0, -1, 1.7976931348623157e308])
def test_unknown_initial_trailing_anchor_rejected(records, value):
    first(records, "portfolio_snapshot")["payload"]["orders"][0]["trail_stop_price"] = value
    with pytest.raises(CaptureError, match="trail_stop_price|trailing anchor"):
        build_dataset(records, DAY, DAY)


def test_extra_order_or_quantity_mismatch_rejected(records):
    snapshot = first(records, "portfolio_snapshot")["payload"]
    snapshot["orders"].append(dict(snapshot["orders"][0], ticker="UNKNOWN", order_id=3))
    with pytest.raises(CaptureError, match="extra order"):
        build_dataset(records, DAY, DAY)
    snapshot["orders"].pop()
    snapshot["broker_positions"][0]["shares"] = 11
    snapshot["account"].pop("positions_value")
    with pytest.raises(CaptureError, match="quantity mismatch"):
        build_dataset(records, DAY, DAY)


def test_persisted_and_broker_resting_hard_stops_remain_distinct(records):
    snapshot = first(records, "portfolio_snapshot")["payload"]
    snapshot["positions"][0]["hard_stop_price"] = 96
    assert build_dataset(records, DAY, DAY)["initial_positions"][0]["broker_hard_stop_price"] == 95


def test_mixed_quote_sources_keep_real_observation_time(records):
    buy = first(records, "buy_cycle")
    quote = buy["payload"]["market_observations"]["CANDIDATE"]
    quote.update(source="FMP", delayed=True, observed_at=DAY + "T09:25:00-04:00")
    dataset = build_dataset(records, DAY, DAY)
    entry = dataset["events"][0]["entry_quotes"]["CANDIDATE"]
    assert entry["observed_at"] == DAY + "T09:25:00-04:00"
    assert entry["source"] == "FMP"
    assert entry["hypothetical_entry_pricing"]
    assert run_comparison(records, DAY, DAY)["variant"]["fills"][0]["observation"] == quote


@pytest.mark.parametrize("time,match", [
    ("09:31:00", "future observation"), ("09:19:59", "stale observation"),
])
def test_future_or_old_sample_cannot_be_used_for_entry(records, time, match):
    first(records, "buy_cycle")["payload"]["market_observations"]["CANDIDATE"]["observed_at"] = DAY + "T" + time + "-04:00"
    with pytest.raises(CaptureError, match=match):
        build_dataset(records, DAY, DAY)


def test_no_dropping_replay_only_ticker_after_actual_veto(records):
    first(records, "quote_sample")["payload"]["market_observations"].pop("CANDIDATE")
    with pytest.raises(CaptureError, match="missing replay-universe"):
        run_comparison(records, DAY, DAY)


def test_config_change_requires_split_window(records):
    monitor = first(records, "monitor")
    monitor["payload"]["config"] = copy.deepcopy(records[0]["payload"]["config"])
    monitor["payload"]["config"]["replay_config"]["cooling_off_days"] += 1
    with pytest.raises(CaptureError, match="config changed.*split window"):
        build_dataset(records, DAY, DAY)


def test_incomplete_cycle_or_missing_eod_or_outage_rejected(records):
    first(records, "monitor")["payload"]["complete"] = False
    with pytest.raises(CaptureError, match="incomplete cycle"):
        build_dataset(records, DAY, DAY)
    first(records, "monitor")["payload"]["complete"] = True
    first(records, "eod_latch")["kind"] = "quote_sample"
    with pytest.raises(CaptureError, match="EOD"):
        build_dataset(records, DAY, DAY)


def test_sequence_hole_rejected(records):
    records.pop(4)
    with pytest.raises(CaptureError, match="missing capture sequence"):
        build_dataset(records, DAY, DAY)


def test_continuous_samples_required_even_if_sequence_is_contiguous(records):
    records[:] = [r for r in records if not ("10:00" < r["occurred_at"][11:16] < "10:30")]
    for i, record in enumerate(records):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="outage"):
        build_dataset(records, DAY, DAY)


def test_initial_cash_override_is_not_an_api(records):
    with pytest.raises(TypeError):
        build_dataset(records, DAY, DAY, initial_cash=1_000_000)


def test_actual_later_bot_fills_are_audit_only_and_manual_or_rotation_blocks(records):
    mark = first(records, "quote_sample")
    mark["kind"] = "fill"
    mark["payload"] = dict(origin="bot", ticker="HELD", shares=10, side="SLD")
    result = run_comparison(records, DAY, DAY)
    assert result["evidence"]["actual_fills_audit"]
    assert result["baseline"]["open_positions"][0]["shares"] == 10
    mark["payload"]["origin"] = "manual"
    with pytest.raises(CaptureError, match="manual/unknown"):
        build_dataset(records, DAY, DAY)
    mark["payload"].update(origin="bot", rotation=True)
    with pytest.raises(CaptureError, match="rotation"):
        build_dataset(records, DAY, DAY)


def test_empty_capture_and_incomplete_start_are_not_flat_simulations(records):
    with pytest.raises(CaptureError, match="empty capture"):
        build_dataset([], DAY, DAY)
    records[0]["payload"]["positions"][0].pop("closed_above_entry")
    with pytest.raises(CaptureError, match="missing fields"):
        build_dataset(records, DAY, DAY)


def test_scaleout_not_counted_as_closed_position(records):
    for record in records[1:]:
        quote = record["payload"].get("market_observations", {}).get("HELD")
        if quote:
            quote["price"] = 104.5
    result = run_comparison(records, DAY, DAY)["baseline"]
    assert len([f for f in result["fills"] if f["side"] == "SELL"]) == 1
    assert result["closed_position_count"] == 0
    assert result["open_positions"][0]["shares"] == 7


def test_drawdown_and_closed_position_count_measure_window_not_original_cost(records):
    for record in records[1:]:
        quote = record["payload"].get("market_observations", {}).get("HELD")
        if quote:
            quote["price"] = 90
    result = run_comparison(records, DAY, DAY)["baseline"]
    assert result["closed_position_count"] == 1
    assert result["net_profit"] == -110
    assert result["max_drawdown_pct"] == pytest.approx(1.1)
    assert result["cooldown_ledger"][-1]["buy_commission"] == 0.35


def test_direct_schema_two_cannot_tamper_with_initial_book(records):
    dataset = build_dataset(records, DAY, DAY)
    dataset["initial_positions"][0]["broker_anchor"] = 1000
    with pytest.raises(core.ReplayInputError, match="exactly match"):
        core.replay(dataset)


def test_recorded_armed_exit_keeps_original_deadline_and_no_hard_order(records):
    state = records[0]["payload"]
    state["positions"][0].update(exit_armed=True, exit_armed_at=DAY + "T08:00:00-04:00",
                                 exit_armed_reason="Recorded pre-window armed exit")
    state["orders"] = state["orders"][:1]
    state["orders"][0].update(trailing_percent=0.6, trail_stop_price=100)
    result = run_comparison(records, DAY, DAY)["baseline"]
    sale = result["fills"][0]
    assert sale["timestamp"] == DAY + "T11:15:00-04:00"
    assert "Armed Exit Deadline" in sale["reason"]
    assert result["closed_position_count"] == 1
    assert result["initial_positions"][0]["broker_hard_stop_price"] == 0
    assert result["initial_positions"][0]["hard_stop_price"] == 95


def test_future_prior_trade_cannot_poison_cooldown(records):
    records[0]["payload"]["prior_trade_history"] = [dict(
        ticker="CANDIDATE", sell_date=DAY + "T11:00:00-04:00",
        net_profit_loss=-100, profit_loss=-99, sell_reason="future")]
    with pytest.raises(CaptureError, match="future observation"):
        build_dataset(records, DAY, DAY)


def test_previous_session_snapshot_cannot_be_substituted_for_missing_today(records):
    records[0].update(session="2026-09-25", occurred_at="2026-09-25T09:30:00-04:00")
    with pytest.raises(CaptureError, match="missing recorded initial_state"):
        build_dataset(records, DAY, DAY)


@pytest.mark.parametrize("field", ["highest_unrealized_pct", "hwm_price", "entry_fee_remaining",
                                 "exit_armed_at", "scaled_out_at", "hard_stop_price"])
def test_every_existing_position_state_input_must_be_recorded(records, field):
    records[0]["payload"]["positions"][0].pop(field)
    with pytest.raises(CaptureError, match="missing fields"):
        build_dataset(records, DAY, DAY)


def test_foreign_stock_and_negative_cash_are_explicitly_unsupported(records):
    snapshot = records[0]["payload"]
    snapshot["broker_positions"][0]["currency"] = "EUR"
    with pytest.raises(CaptureError, match="unsupported asset"):
        build_dataset(records, DAY, DAY)
    snapshot["broker_positions"][0]["currency"] = "USD"
    snapshot["account"]["net_liquidation"] = 100
    with pytest.raises(CaptureError, match="negative cash"):
        build_dataset(records, DAY, DAY)


def test_multisession_missing_monitoring_cannot_be_ignored(records):
    with pytest.raises(CaptureError, match="missing NYSE session"):
        build_dataset(records, DAY, "2026-09-29")


def test_named_capture_kinds_and_unrestricted_nontrading_audit(records):
    audit = dict(records[1], id="audit", kind="diagnostic_note",
                 payload={"message": "safe diagnostic"})
    records.insert(1, audit)
    for index, record in enumerate(records):
        record["sequence"] = index
    dataset = build_dataset(records, DAY, DAY)
    assert dataset["capture_evidence"]["audit_events"][0]["kind"] == "diagnostic_note"


def test_complete_true_cannot_hide_missing_quotes(records):
    first(records, "quote_sample")["payload"]["missing_quotes"] = ["CANDIDATE"]
    with pytest.raises(CaptureError, match="missing recorded quotes"):
        build_dataset(records, DAY, DAY)


def test_comparison_limits_are_enforced_before_processing(records, monkeypatch):
    import intraday_replay
    with pytest.raises(CaptureError, match="93 calendar days"):
        build_dataset(records, "2026-01-01", DAY)
    with pytest.raises(CaptureError, match="50000 raw rows"):
        build_dataset([records[0]] * 50_001, DAY, DAY)
    monkeypatch.setattr(intraday_replay, "MAX_CAPTURE_BYTES", 10)
    with pytest.raises(CaptureError, match="64 MiB"):
        build_dataset(records, DAY, DAY)


def test_raw_nonatomic_worker_snapshot_cannot_be_relabelled_complete(records):
    records[0]["payload"]["atomic"] = False
    with pytest.raises(CaptureError, match="non-atomic.*worker-read"):
        build_dataset(records, DAY, DAY)


def test_phase_marker_cannot_be_relabelled_complete_cycle(records):
    first(records, "monitor")["payload"]["marker_only"] = True
    with pytest.raises(CaptureError, match="phase marker is not a complete replay cycle"):
        build_dataset(records, DAY, DAY)


@pytest.mark.parametrize("kind", ["fill_event", "order_event", "sell_event"])
def test_unattributed_raw_execution_audit_is_not_silently_ignored(records, kind):
    event = first(records, "quote_sample")
    event["kind"] = kind
    event["payload"] = {"ticker": "HELD"}
    with pytest.raises(CaptureError, match="manual/unknown"):
        build_dataset(records, DAY, DAY)


def test_backend_can_supply_same_run_configuration_from_before_window(records):
    config = records[0]["payload"].pop("config")
    config["runtime"] = {"OCA_EXIT_ENABLED": True}
    config["git_commit"] = "recorded-version"
    records.insert(0, dict(
        id="prior-config", run_id="run-1", sequence=0, kind="effective_config",
        session="2026-09-25", occurred_at="2026-09-25T09:00:00-04:00", payload=config))
    dataset = build_dataset(records, DAY, DAY)
    assert dataset["capture_evidence"]["recorded_configuration_metadata"]["git_commit"] == "recorded-version"
    assert run_comparison(records, DAY, DAY)["baseline"]["initial_equity"] == 10000


def test_other_run_configuration_cannot_fill_missing_snapshot_config(records):
    config = records[0]["payload"].pop("config")
    records.insert(0, dict(
        id="prior-config", run_id="different-run", sequence=0, kind="effective_config",
        session="2026-09-25", occurred_at="2026-09-25T09:00:00-04:00", payload=config))
    with pytest.raises(CaptureError, match="missing recorded runtime configuration"):
        build_dataset(records, DAY, DAY)


def raw_hook_records(records):
    """The recorder's real nested snapshot/list quotes/phase marker wire format."""
    canonical = records[0]["payload"]
    stamp = canonical["timestamp"]
    broker = dict(
        snapshot_at=stamp, account="U_TEST", connected=True, complete=True,
        freshness="cached_no_provider_timestamp",
        positions=[dict(ticker="HELD", account="U_TEST", position=10, marketPrice=101,
                        contract=dict(secType="STK", currency="USD"))],
        position_quantities=[dict(ticker="HELD", account="U_TEST", position=10,
                                  contract=dict(secType="STK", currency="USD"))],
        account_values=[dict(tag="NetLiquidation", account="U_TEST", currency="USD", value="10000")],
        open_orders=[dict(
            ticker=o["ticker"], contract=dict(secType="STK", currency="USD"),
            order=dict(account=o["account"], action=o["action"], orderType=o["order_type"],
                       totalQuantity=o["shares"], orderId=o["order_id"], parentId=o["parent_id"],
                       clientId=1,
                       tif=o["tif"], ocaType=o["oca_type"],
                       ocaGroup=o["oca_group"], trailingPercent=o["trailing_percent"],
                       trailStopPrice=o["trail_stop_price"], auxPrice=o["aux_price"]),
            status=dict(status=o["status"], filled=0, remaining=o["shares"]),
        ) for o in canonical["orders"]],
    )
    positions = copy.deepcopy(canonical["positions"])
    for pos in positions:
        pos["buy_commission"] = pos.pop("entry_fee_remaining")
    output = []

    def emit(kind, stamp, **payload):
        output.append(dict(id=f"raw-{len(output)}", run_id="run-1", sequence=len(output),
                           session=DAY, occurred_at=stamp, kind=kind, payload=payload))

    emit("effective_config", stamp, **copy.deepcopy(canonical["config"]))
    emit("portfolio_snapshot", stamp,
         snapshot_at=stamp, broker_snapshot=broker, portfolio_positions=positions,
         trade_history=canonical["prior_trade_history"], ibkr_fills=canonical["prior_fills"],
         source_times=dict(
             portfolio_positions=dict(received_at=stamp, source="live_main_thread_read"),
             trade_history=dict(filter_lte=stamp, filter_gte="2026-09-01T00:00:00-04:00"),
             ibkr_fills=dict(filter_lte=stamp, filter_gte="2026-09-01T00:00:00-04:00")),
         coherence="main_thread_adjacent_observations", complete=True, errors=[],
         effective_config=copy.deepcopy(canonical["config"]))
    for index, record in enumerate(records[1:], 1):
        kind, p, stamp = record["kind"], record["payload"], record["occurred_at"]
        if kind == "eod_latch":
            continue
        emit("quote_sample", stamp, complete=True, missing_quotes=[], errors=[],
             quotes=[dict(ticker=t, price=q["price"], source="FMP",
                          provider_timestamp=q["observed_at"], received_at=stamp)
                     for t, q in p["market_observations"].items()])
        if kind not in ("buy_cycle", "monitor"):
            continue
        cycle_id = f"cycle-{index}"
        emit(kind, stamp, marker_only=True, stage="start", complete=False, cycle_id=cycle_id)
        if kind == "buy_cycle":
            for gate in ("schema", "margin", "market"):
                emit("buy_gate", stamp, gate=gate, passed=True, margin_loan=0, cycle_id=cycle_id)
            emit("candidate_universe", stamp, phase="buy", triggers=p["triggers"],
                 complete=True, cycle_id=cycle_id)
        else:
            emit("monitor_context", stamp, positions=positions, oca_managed=[], cycle_id=cycle_id)
            emit("monitor_observation", stamp, ticker="HELD", price=101,
                 source="IBKR", position=positions[0], received_at=stamp, cycle_id=cycle_id)
            if index + 1 < len(records) and records[index + 1]["kind"] == "eod_latch":
                emit("eod_latch", stamp, marker_only=True, stage="start", complete=False, cycle_id=cycle_id)
                emit("candidate_universe", stamp, phase="eod", triggers=[{"ticker": "CANDIDATE"}],
                     complete=True, cycle_id=cycle_id)
                emit("eod_latch", stamp, marker_only=True, stage="end", complete=True, cycle_id=cycle_id)
        emit(kind, stamp, marker_only=True, stage="end", complete=True, status="returned", cycle_id=cycle_id)
    return output


def test_real_recorder_wire_format_normalizes_without_inventing_current_state(records):
    raw = raw_hook_records(records)
    result = run_comparison(raw, DAY, DAY)
    assert result["baseline"]["initial_equity"] == 10000
    assert result["effective_initial_cash"] == 8990
    assert result["baseline"]["initial_positions"][0]["shares"] == 10
    assert result["variant"]["fills"][0]["observation"]["source"] == "FMP"
    assert result["effective_initial_account"]["mark_source"] == "recorded_IBKR_portfolio_cache"
    assert result["evidence"]["records_count"] == len(raw)
    assert any(a["kind"] == "sampled_cycle_completion" for a in result["evidence"]["assumptions"])


def test_real_raw_worker_snapshot_without_coherence_is_rejected(records):
    raw = raw_hook_records(records)
    snapshot = first(raw, "portfolio_snapshot")["payload"]
    snapshot.pop("coherence")
    snapshot["atomic"] = False
    with pytest.raises(CaptureError, match="no coherent main-thread"):
        build_dataset(raw, DAY, DAY)


def test_real_raw_cycle_cannot_borrow_future_quote_or_invent_missing_gate(records):
    raw = raw_hook_records(records)
    first(raw, "buy_gate")["payload"]["gate"] = "unknown"
    with pytest.raises(CaptureError, match="missing exogenous gates"):
        build_dataset(raw, DAY, DAY)


def test_real_raw_missing_cycle_end_rejected(records):
    raw = raw_hook_records(records)
    end = next(r for r in raw if r["kind"] == "monitor" and r["payload"].get("stage") == "end")
    end["kind"] = "unrelated_audit"
    with pytest.raises(CaptureError, match="overlapping|incomplete raw cycle"):
        build_dataset(raw, DAY, DAY)


def test_real_raw_manual_smart_oca_context_rejected(records):
    raw = raw_hook_records(records)
    first(raw, "monitor_context")["payload"]["oca_managed"] = ["HELD"]
    with pytest.raises(CaptureError, match="manual Smart OCA"):
        build_dataset(raw, DAY, DAY)


def test_raw_snapshot_uploaded_later_uses_copied_preaction_state_not_upload_time(records):
    raw = raw_hook_records(records)
    snapshot = raw.pop(1)
    insertion = next(i for i, r in enumerate(raw) if r["occurred_at"][11:16] == "09:35")
    snapshot["occurred_at"] = DAY + "T09:35:00-04:00"
    raw.insert(insertion, snapshot)
    for index, r in enumerate(raw):
        r["sequence"] = index
    result = run_comparison(raw, DAY, DAY)
    assert result["initial_snapshot_at"] == DAY + "T09:30:00-04:00"
    assert result["baseline"]["initial_positions"][0]["shares"] == 10


def test_raw_received_time_is_never_substituted_for_unknown_provider_time(records):
    raw = raw_hook_records(records)
    first(raw, "quote_sample")["payload"]["quotes"][0]["provider_timestamp"] = None
    with pytest.raises(CaptureError, match="expected ISO timestamp"):
        build_dataset(raw, DAY, DAY)


def test_raw_today_only_fills_are_not_a_complete_cooldown_ledger(records):
    raw = raw_hook_records(records)
    first(raw, "portfolio_snapshot")["payload"]["source_times"]["ibkr_fills"]["filter_gte"] = DAY + "T00:00:00-04:00"
    with pytest.raises(CaptureError, match="full cooldown window"):
        build_dataset(raw, DAY, DAY)


def test_raw_unknown_actual_order_is_blocked_even_though_fills_are_audit_only(records):
    raw = raw_hook_records(records)
    sample = first(raw, "quote_sample")
    sample.update(kind="fill_event", payload={"execution": {"orderId": 9999}})
    with pytest.raises(CaptureError, match="manual/unknown order identity"):
        build_dataset(raw, DAY, DAY)


def test_actual_recorder_snapshot_function_roundtrips_into_replay(records, monkeypatch):
    import intraday_capture
    from types import SimpleNamespace

    class Query:
        def select(self, *args):
            return self

        def lte(self, *args):
            return self

        def gte(self, *args):
            return self

        def order(self, *args):
            return self

        def range(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    raw = raw_hook_records(records)
    snapshot = first(raw, "portfolio_snapshot")
    payload = snapshot["payload"]
    config = copy.deepcopy(payload["effective_config"])
    config["costs_semantics"] = "simulation_assumptions_not_measured_live_costs"
    config["runtime"] = {"OCA_EXIT_ENABLED": True}
    config["git_commit"] = "recorded-runtime-revision"
    first(raw, "effective_config")["payload"] = config
    monkeypatch.setattr(intraday_capture, "now", lambda: payload["snapshot_at"])
    recorder = intraday_capture.Recorder(config, spool="unused-capture-fixture.sqlite3")
    recorder.initial_state(
        SimpleNamespace(table=lambda name: Query()), payload["broker_snapshot"],
        portfolio_seed=dict(portfolio_positions=payload["portfolio_positions"],
                            portfolio_received_at=payload["snapshot_at"]),
    )
    generated = recorder.queue.get_nowait()
    assert generated["kind"] == "portfolio_snapshot"
    snapshot["payload"] = generated["payload"]
    result = run_comparison(raw, DAY, DAY)
    assert result["baseline"]["initial_equity"] == 10000
    assert result["evidence"]["recorded_configuration_metadata"]["costs_semantics"] == config["costs_semantics"]
    assert result["evidence"]["recorded_configuration_metadata"]["runtime"] == config["runtime"]
    assert result["evidence"]["recorded_configuration_metadata"]["git_commit"] == config["git_commit"]


def test_real_bot_scaleout_order_and_fill_are_attributed_but_not_forced(records):
    raw = raw_hook_records(records)
    start = next(r for r in raw if r["kind"] == "monitor" and r["payload"].get("stage") == "start")
    index = raw.index(start) + 1
    raw[index:index] = [
        dict(start, id="actual-scale-order", kind="order_event", payload=dict(
            stage="scale_out_submitted", cycle_id=start["payload"]["cycle_id"],
            order=dict(orderId=55, clientId=1, account="U_TEST"))),
        dict(start, id="actual-scale-fill", kind="fill_event", payload=dict(
            execution=dict(orderId=55, clientId=1, acctNumber="U_TEST", side="SLD", shares=3))),
    ]
    for i, record in enumerate(raw):
        record["sequence"] = i
    result = run_comparison(raw, DAY, DAY)
    assert len(result["evidence"]["actual_fills_audit"]) == 1
    assert result["baseline"]["open_positions"][0]["shares"] == 10
    assert result["baseline"]["closed_position_count"] == 0


def test_raw_order_id_collision_across_clients_cannot_attribute_manual_fill(records):
    raw = raw_hook_records(records)
    sample = first(raw, "quote_sample")
    sample.update(kind="fill_event", payload={"execution": {
        "orderId": 1, "clientId": 2, "acctNumber": "U_TEST", "side": "SLD", "shares": 10,
    }})
    with pytest.raises(CaptureError, match="manual/unknown order identity"):
        build_dataset(raw, DAY, DAY)


def test_actual_recorder_phase_and_emit_events_assemble_into_buy_cycle(records, monkeypatch):
    import intraday_capture

    raw = raw_hook_records(records)
    original = first(raw, "buy_cycle")
    cycle_id = original["payload"]["cycle_id"]
    start = raw.index(original)
    end = next(i for i, r in enumerate(raw)
               if r["kind"] == "buy_cycle" and r["payload"].get("stage") == "end")
    universe = next(r["payload"]["triggers"] for r in raw
                    if r["kind"] == "candidate_universe" and r["payload"].get("cycle_id") == cycle_id)
    recorder = intraday_capture.Recorder(raw[0]["payload"], spool="unused-capture-fixture.sqlite3")
    recorder.run_id = "run-1"
    monkeypatch.setattr(intraday_capture, "_recorder", recorder)
    monkeypatch.setattr(intraday_capture, "now", lambda: original["occurred_at"])
    monkeypatch.setattr(intraday_capture, "snapshot", lambda ib: None)

    @intraday_capture.capture_phase("buy")
    def captured_buy(ib):
        intraday_capture.emit("buy_gate", gate="schema", passed=True)
        intraday_capture.emit("buy_gate", gate="margin", passed=True, margin_loan=0)
        intraday_capture.emit("buy_gate", gate="market", passed=True)
        intraday_capture.emit("candidate_universe", phase="buy", triggers=universe, complete=True)

    captured_buy(None)
    emitted = []
    while not recorder.queue.empty():
        emitted.append(recorder.queue.get_nowait())
    assert len({r["payload"]["cycle_id"] for r in emitted}) == 1
    raw[start:end + 1] = emitted
    for index, record in enumerate(raw):
        record["sequence"] = index
    result = run_comparison(raw, DAY, DAY)
    assert result["baseline"]["initial_positions"][0]["shares"] == 10
    assert result["variant"]["fills"][0]["ticker"] == "CANDIDATE"


@pytest.mark.parametrize("tif", ["DAY", "", None, "IOC", "GTD"])
def test_initial_non_gtc_protection_is_not_carried_overnight(records, tif):
    raw = raw_hook_records(records)
    first(raw, "portfolio_snapshot")["payload"]["broker_snapshot"]["open_orders"][1]["order"]["tif"] = tif
    with pytest.raises(CaptureError, match="order TIF.*GTC"):
        run_comparison(raw, DAY, DAY)


@pytest.mark.parametrize("oca_type", [None, 0, 2, 3, "1", True])
def test_initial_unsupported_or_unknown_oca_semantics_rejected(records, oca_type):
    raw = raw_hook_records(records)
    first(raw, "portfolio_snapshot")["payload"]["broker_snapshot"]["open_orders"][0]["order"]["ocaType"] = oca_type
    with pytest.raises(CaptureError, match="unsupported initial OCA type"):
        build_dataset(raw, DAY, DAY)


def test_native_order_time_in_force_and_oca_type_are_preserved(records):
    dataset = build_dataset(raw_hook_records(records), DAY, DAY)
    assert all(o["tif"] == "GTC" and o["oca_type"] == 1 for o in dataset["initial_state"]["orders"])
    dataset["initial_state"]["orders"][0]["tif"] = "DAY"
    with pytest.raises(core.ReplayInputError, match="order TIF"):
        core.replay(dataset)


def test_canonical_opening_buy_does_not_prove_intraday_entry_coverage(records):
    kept_first_buy = False
    trimmed = []
    for record in records:
        if record["kind"] == "buy_cycle":
            if kept_first_buy:
                continue
            kept_first_buy = True
        trimmed.append(record)
    for i, record in enumerate(trimmed):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="missing matching preceding buy cycle"):
        build_dataset(trimmed, DAY, DAY)


def test_native_missing_later_buy_cycle_rejected_despite_continuous_samples(records):
    raw = raw_hook_records(records)
    buy = next(r for r in raw if r["kind"] == "buy_cycle" and r["occurred_at"][11:16] == "10:15")
    missing_id = buy["payload"]["cycle_id"]
    raw = [r for r in raw if r["payload"].get("cycle_id") != missing_id]
    for i, record in enumerate(raw):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="missing matching preceding buy cycle"):
        build_dataset(raw, DAY, DAY)


def test_missing_monitor_is_not_hidden_by_continuing_buy_attempts(records):
    records[:] = [r for r in records if not (
        r["kind"] == "monitor" and r["occurred_at"][11:16] == "10:15")]
    for i, record in enumerate(records):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="missing monitor cycle"):
        build_dataset(records, DAY, DAY)


def test_initial_monitor_needs_selected_buy_context_not_an_assumed_prior_attempt(records):
    raw = raw_hook_records(records)
    missing_id = first(raw, "buy_cycle")["payload"]["cycle_id"]
    raw = [r for r in raw if r["payload"].get("cycle_id") != missing_id]
    for i, record in enumerate(raw):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="missing matching preceding buy cycle.*initial snapshot"):
        build_dataset(raw, DAY, DAY)


def test_canonical_monitor_cannot_reuse_an_old_buy_cycle_id(records):
    monitors = [r for r in records if r["kind"] == "monitor"]
    monitors[1]["payload"]["cycle_context"]["preceding_buy_cycle_id"] = "buy-0"
    with pytest.raises(CaptureError, match="missing matching preceding buy cycle"):
        build_dataset(records, DAY, DAY)


def test_cycle_duration_is_not_misclassified_as_missing_fifteen_minute_attempt(records):
    for record in records:
        if record["kind"] == "buy_cycle" and record["occurred_at"][11:16] == "10:15":
            record["occurred_at"] = DAY + "T10:22:00-04:00"
            record["payload"]["cycle_context"]["completed_at"] = record["occurred_at"]
            for quote in record["payload"]["market_observations"].values():
                quote["observed_at"] = DAY + "T10:20:00-04:00"
        elif record["kind"] == "monitor" and record["occurred_at"][11:16] == "10:15":
            record["occurred_at"] = DAY + "T10:23:00-04:00"
            record["payload"]["cycle_context"].update(
                started_at=DAY + "T10:22:01-04:00", completed_at=record["occurred_at"])
            for quote in record["payload"]["market_observations"].values():
                quote["observed_at"] = DAY + "T10:20:00-04:00"
    records.sort(key=lambda r: r["occurred_at"])
    for i, record in enumerate(records):
        record["sequence"] = i
    result = run_comparison(records, DAY, DAY)
    assert result["evidence"]["continuous_session_coverage"]


def test_native_start_end_latency_preserves_real_buy_then_monitor_adjacency(records):
    raw = raw_hook_records(records)
    buy_id = next(r["payload"]["cycle_id"] for r in raw
                  if r["kind"] == "buy_cycle" and r["occurred_at"][11:16] == "10:15")
    monitor_id = next(r["payload"]["cycle_id"] for r in raw
                      if r["kind"] == "monitor" and r["occurred_at"][11:16] == "10:15")
    for record in raw:
        payload = record["payload"]
        if payload.get("cycle_id") == buy_id and record["kind"] == "buy_cycle" and payload["stage"] == "end":
            record["occurred_at"] = DAY + "T10:22:00-04:00"
        elif payload.get("cycle_id") == monitor_id:
            record["occurred_at"] = DAY + (
                "T10:23:00-04:00" if payload.get("stage") == "end" else "T10:22:01-04:00")
    raw.sort(key=lambda r: r["occurred_at"])
    for i, record in enumerate(raw):
        record["sequence"] = i
    result = run_comparison(raw, DAY, DAY)
    assert result["evidence"]["continuous_session_coverage"]


@pytest.mark.parametrize("failed_gate,reason", [
    ("schema", "SCHEMA_BLOCK"), ("margin", "MARGIN_BLOCK"), ("market", "MARKET_BLOCK"),
])
def test_native_proven_early_return_covers_buy_attempt_without_inventing_inputs(records, failed_gate, reason):
    raw = raw_hook_records(records)
    keep_gates = ("schema", "margin", "market")[:("schema", "margin", "market").index(failed_gate) + 1]
    filtered = []
    for record in raw:
        kind, payload = record["kind"], record["payload"]
        if kind == "candidate_universe" and payload.get("phase") == "buy":
            continue
        if kind == "buy_gate":
            if payload["gate"] not in keep_gates:
                continue
            payload["passed"] = payload["gate"] != failed_gate
            if payload["gate"] == "margin":
                payload["margin_loan"] = 25 if failed_gate == "margin" else 0
        filtered.append(record)
    for i, record in enumerate(filtered):
        record["sequence"] = i
    dataset = build_dataset(filtered, DAY, DAY)
    blocked = [e for e in dataset["events"] if e["type"] == "buy_blocked"]
    assert len(blocked) == len([e for e in dataset["events"] if e["type"] == "monitor"])
    assert all(e["block_reason"] == reason and len(e["gate_evidence"]) == len(keep_gates) for e in blocked)
    assert all("triggers" not in e and "entry_quotes" not in e for e in blocked)
    result = run_comparison(filtered, DAY, DAY)
    assert not result["baseline"]["fills"]
    assert not result["variant"]["fills"]
    assert any(d["reason"] == reason and d.get("cycle_wide") for d in result["baseline"]["decisions"])


def test_native_return_without_failed_gate_does_not_prove_no_entry_opportunity(records):
    raw = raw_hook_records(records)
    cycle_id = first(raw, "buy_cycle")["payload"]["cycle_id"]
    raw = [r for r in raw if not (
        r["payload"].get("cycle_id") == cycle_id and (
            r["kind"] == "candidate_universe"
            or (r["kind"] == "buy_gate" and r["payload"]["gate"] != "schema")))]
    for i, record in enumerate(raw):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="unevaluated/missing exogenous gates"):
        build_dataset(raw, DAY, DAY)


def test_missing_unrelated_retained_symbol_warns_without_blocking_comparison(records):
    raw = raw_hook_records(records)
    for record in raw:
        if record["kind"] == "quote_sample":
            payload = record["payload"]
            payload.update(complete=False, requested_symbols=["HELD", "CANDIDATE", "DELISTED"],
                           missing_quotes=["DELISTED"],
                           errors=[dict(ticker="DELISTED", reason="quote_missing")])
    result = run_comparison(raw, DAY, DAY)
    assert result["variant"]["fills"][0]["ticker"] == "CANDIDATE"
    warnings = result["evidence"]["coverage_warnings"]
    assert warnings and all(w["missing_irrelevant_symbols"] == ["DELISTED"] for w in warnings)
    assert result["evidence"]["continuous_session_coverage"]


@pytest.mark.parametrize("ticker", ["HELD", "CANDIDATE"])
def test_incomplete_frame_must_not_hide_missing_held_or_variant_only_candidate(records, ticker):
    raw = raw_hook_records(records)
    record = next(r for r in raw if r["kind"] == "quote_sample" and r["occurred_at"][11:16] == "10:00")
    payload = record["payload"]
    payload["quotes"] = [q for q in payload["quotes"] if q["ticker"] != ticker]
    payload.update(complete=False, missing_quotes=[ticker, "DELISTED"],
                   errors=[dict(ticker=ticker, reason="quote_missing"),
                           dict(ticker="DELISTED", reason="quote_missing")])
    with pytest.raises(CaptureError, match=f"missing required replay quotes.*{ticker}"):
        run_comparison(raw, DAY, DAY)


def test_valid_prices_do_not_explain_an_unscoped_incomplete_capture(records):
    raw = raw_hook_records(records)
    first(raw, "quote_sample")["payload"].update(
        complete=False, errors=[dict(reason="symbol_limit_exceeded")])
    with pytest.raises(CaptureError, match="unscoped provider failure"):
        build_dataset(raw, DAY, DAY)


def test_unrelated_bad_quote_is_not_used_and_is_reported(records):
    raw = raw_hook_records(records)
    sample = first(raw, "quote_sample")
    sample["payload"]["quotes"].append(dict(
        ticker="DELISTED", price=0, source="FMP", provider_timestamp=sample["occurred_at"]))
    result = run_comparison(raw, DAY, DAY)
    assert result["evidence"]["coverage_warnings"][0]["missing_irrelevant_symbols"] == ["DELISTED"]


def test_quote_warning_cannot_hide_a_coalesced_nonquote_capture_failure(records):
    raw = raw_hook_records(records)
    index = next(i for i, r in enumerate(raw) if r["kind"] == "quote_sample")
    sample = raw[index]
    sample["payload"].update(
        complete=False, missing_quotes=["DELISTED"],
        errors=[dict(ticker="DELISTED", reason="quote_missing")])
    gap = dict(sample, id="mixed-capture-gap", kind="capture_gap", payload=dict(
        area="recorder", complete=False, reason="quote coverage incomplete: 1 errors", error_count=2))
    raw.insert(index + 1, gap)
    for i, record in enumerate(raw):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="capture_gap"):
        build_dataset(raw, DAY, DAY)


def two_session_raw_records(records):
    raw = raw_hook_records(records)
    second = json.loads(json.dumps(raw).replace(DAY, "2026-09-29"))
    for record in second:
        record["id"] = "second-" + record["id"]
        if "cycle_id" in record["payload"]:
            record["payload"]["cycle_id"] = "second-" + record["payload"]["cycle_id"]
    initial_order = copy.deepcopy(first(raw, "portfolio_snapshot")["payload"]["broker_snapshot"]["open_orders"][0]["order"])
    overnight = [
        ("quote_sample", "23:50", dict(complete=False, quotes=[], missing_quotes=["HELD", "CANDIDATE"],
                                      errors=[dict(ticker="HELD", reason="quote_request_failed")])),
        ("capture_gap", "23:55", dict(area="broker_snapshot", complete=False, reason="ConnectionError")),
        ("order_event", "23:56", dict(stage="broker_status", order=initial_order)),
    ]
    for i, (kind, clock, payload) in enumerate(overnight):
        raw.append(dict(id=f"overnight-{i}", run_id=raw[0]["run_id"], sequence=0, session=DAY,
                        occurred_at=f"{DAY}T{clock}:00-04:00", kind=kind, payload=payload))
    raw.extend(second)
    raw.sort(key=lambda r: r["occurred_at"])
    for i, record in enumerate(raw):
        record["sequence"] = i
    return raw


def test_complete_regular_sessions_survive_overnight_gateway_restart_and_quote_gap(records):
    raw = two_session_raw_records(records)
    result = run_comparison(raw, DAY, "2026-09-29")
    assert result["evidence"]["sessions"] == [DAY, "2026-09-29"]
    warnings = result["evidence"]["coverage_warnings"]
    overnight = [w for w in warnings if w["kind"] == "off_hours_coverage_warning"]
    assert {w["source_kind"] for w in overnight} == {"quote_sample", "capture_gap"}
    assert any(r["id"] == "overnight-2:order_event" for r in result["evidence"]["audit_events"])
    dataset = build_dataset(raw, DAY, "2026-09-29")
    assert all("T23:" not in e["timestamp"] for e in dataset["events"])


def test_regular_session_gateway_gap_still_rejects(records):
    raw = two_session_raw_records(records)
    gap = next(r for r in raw if r["id"] == "overnight-1")
    gap["occurred_at"] = DAY + "T10:00:00-04:00"
    raw.sort(key=lambda r: r["occurred_at"])
    for i, record in enumerate(raw):
        record["sequence"] = i
    with pytest.raises(CaptureError, match="capture_gap"):
        build_dataset(raw, DAY, "2026-09-29")


@pytest.mark.parametrize("kind,payload", [
    ("manual", dict(reason="operator activity")),
    ("rotation", dict(reason="actual rotation")),
    ("order_event", dict(order=dict(clientId=1, orderId=999, account="U123"))),
    ("fill_event", dict(execution=dict(clientId=1, orderId=999, acctNumber="U123"))),
    ("quote_sample", dict(origin="manual", complete=False, quotes=[])),
])
def test_off_hours_does_not_exempt_manual_or_external_financial_activity(records, kind, payload):
    raw = two_session_raw_records(records)
    event = next(r for r in raw if r["id"] == "overnight-2")
    event.update(kind=kind, payload=payload)
    with pytest.raises(CaptureError, match="manual|rotation"):
        build_dataset(raw, DAY, "2026-09-29")


def test_unclassified_off_hours_capture_gap_is_not_silently_discarded(records):
    raw = two_session_raw_records(records)
    next(r for r in raw if r["id"] == "overnight-1")["payload"]["area"] = "unknown_financial_activity"
    with pytest.raises(CaptureError, match="capture_gap"):
        build_dataset(raw, DAY, "2026-09-29")
