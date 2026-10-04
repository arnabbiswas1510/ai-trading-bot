"""Point-in-time inputs for hypothetical decisions; no brokerage imports or writes."""
from __future__ import annotations

import copy
import datetime as dt
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from zoneinfo import ZoneInfo

from indicators import calculate_ema, calculate_sma, compute_rsi
from market_calendar import trading_days_between
from market_direction import index_verdict
from shadow_store import fingerprint
from quote_transport import fetch_quotes, quote_budget_seconds

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
SOURCE_TABLES = frozenset({
    "intraday_capture_events", "portfolio_positions", "daily_triggers",
    "trade_history", "ibkr_fills", "exit_requests",
})
MAX_ROWS = 10000
MAX_SYMBOLS = 250


class InputGap(ValueError):
    """A missing observation is not a bearish/bullish strategy decision."""


def require(condition, message):
    if not condition:
        raise InputGap(message)


def timestamp(value):
    try:
        value = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        require(value.tzinfo is not None, "Source time must include timezone.")
        return value
    except (ValueError, TypeError):
        raise InputGap("Invalid or missing source timestamp.") from None


def number(value, field, *, positive=False):
    require(not isinstance(value, bool) and value is not None, f"{field} is missing/invalid.")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise InputGap(f"{field} is not numeric.") from None
    require(math.isfinite(result) and (not positive or result > 0), f"{field} is invalid.")
    return result


def whole(value, field):
    result = number(value, field, positive=True)
    require(result.is_integer(), f"{field}: fractional quantities are unsupported.")
    return int(result)


class ReadOnlySources:
    """Expose only SELECT operations on live/source tables, with bounded pagination."""
    def __init__(self, client, clock=lambda: dt.datetime.now(UTC)):
        self._client, self.clock = client, clock

    def read(self, table, filters=(), *, order="id", descending=False, limit=None):
        require(table in SOURCE_TABLES, "Source table is outside read allowlist.")
        started = self.clock().isoformat()
        rows, offset = [], 0
        while True:
            query = self._client.table(table).select("*")
            for method, key, value in filters:
                require(method in ("eq", "gte", "lte", "lt", "in_"), "Unsupported read filter.")
                query = getattr(query, method)(key, value)
            query = query.order(order, desc=descending)
            page_size = min(500, limit - len(rows)) if limit else 500
            batch = query.range(offset, offset + page_size - 1).execute().data
            require(isinstance(batch, list), f"{table}: incomplete database response.")
            rows.extend(batch)
            require(len(rows) <= MAX_ROWS, f"{table}: input row budget exceeded.")
            if len(batch) < page_size or (limit and len(rows) == limit):
                break
            offset += len(batch)
        return {"rows": rows, "requested_at": started, "received_at": self.clock().isoformat(),
                "source": f"supabase:{table}", "filters": [list(f) for f in filters],
                "complete": True}

    def observer_pair(self, account):
        # JSON filtering is supported by PostgREST; never consume public dashboard marks.
        result = self.read("intraday_capture_events",
                           (("eq", "kind", "observer_snapshot"),
                            ("eq", "payload->>account", account)),
                           order="occurred_at", descending=True, limit=2)
        require(len(result["rows"]) == 2, "Need two adjacent completed observer snapshots.")
        return list(reversed(result["rows"]))


class PublicMarketData:
    def __init__(self, http, api_key, clock=lambda: dt.datetime.now(UTC)):
        require(bool(api_key), "FMP_API_KEY is required for hypothetical-universe prices.")
        self.http, self.api_key, self.clock = http, api_key, clock
        self.quote_endpoint = "batch-quote"
        self.quote_fallback_reason = None
        from config import INTRADAY_SAMPLE_SECONDS, INTRADAY_MAX_QUOTE_AGE_SECONDS
        self.quote_budget = quote_budget_seconds(
            INTRADAY_SAMPLE_SECONDS, INTRADAY_MAX_QUOTE_AGE_SECONDS)
        self._history_cache = {}
        from research_configuration import (
            MARKET_DIRECTION_TICKERS, MARKET_DIRECTION_SMA_WINDOW, MARKET_DIRECTION_SLOPE_DAYS,
        )
        self._benchmarks = set(MARKET_DIRECTION_TICKERS)
        self._benchmark_days = int((MARKET_DIRECTION_SMA_WINDOW + MARKET_DIRECTION_SLOPE_DAYS) * 1.6) + 60

    def _get(self, endpoint, params):
        started = self.clock().isoformat()
        try:
            response = self.http.get(
                "https://financialmodelingprep.com/stable/" + endpoint,
                params={**params, "apikey": self.api_key}, timeout=15)
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            raise InputGap(f"FMP {endpoint} request failed ({type(exc).__name__}).") from None
        require(isinstance(rows, list), f"FMP {endpoint}: unexpected response.")
        return rows, {"source": "FMP", "endpoint": endpoint, "parameters": params,
                      "requested_at": started, "received_at": self.clock().isoformat()}

    def quotes(self, symbols):
        import research_diagnostics as diagnostics
        symbols = sorted(set(symbols))
        require(len(symbols) <= MAX_SYMBOLS, "Hypothetical universe exceeds quote budget.")
        transport = fetch_quotes(
            self.http, self.api_key, symbols, budget=self.quote_budget,
            clock=lambda: self.clock().isoformat(), monotonic=time.monotonic,
            endpoint=self.quote_endpoint, fallback_reason=self.quote_fallback_reason,
            diagnostic=lambda event, **kwargs: diagnostics.emit("shadow-worker", event, **kwargs))
        self.quote_endpoint = transport["endpoint_mode"]
        self.quote_fallback_reason = transport["fallback_reason"]
        require(not transport["errors"], "FMP quote acquisition incomplete: " + ", ".join(
            sorted({e["reason"] for e in transport["errors"]})))
        result = {}
        for response in transport["responses"]:
            rows, proof = response["rows"], response["proof"]
            for row in rows:
                require(isinstance(row, dict), "Invalid FMP quote row.")
                symbol = row.get("symbol")
                require(isinstance(symbol, str) and symbol in response["symbols"],
                        "Unrequested FMP quote symbol.")
                require(symbol not in result, "Duplicate FMP quote symbol.")
                provider = row.get("timestamp")
                require(isinstance(provider, (int, float)) and not isinstance(provider, bool),
                        f"{symbol}: quote provider timestamp missing.")
                number(provider, f"{symbol}.provider_timestamp")
                try:
                    provider_time = dt.datetime.fromtimestamp(provider, UTC).isoformat()
                except (ValueError, OverflowError, OSError):
                    raise InputGap(f"{symbol}: invalid quote provider timestamp.") from None
                result[symbol] = {
                    "price": number(row.get("price"), f"{symbol}.price", positive=True),
                    "provider_timestamp": provider_time,
                    "received_at": proof["received_at"], "source": "FMP",
                    "raw": row, "endpoint": "stable/" + proof["endpoint"],
                }
        require(set(symbols) == result.keys(), "FMP quote response omitted requested symbols.")
        metadata = {k: transport[k] for k in (
            "endpoint_mode", "fallback_reason", "request_count", "request_limit", "budget_seconds")}
        evidence = [{**proof, **metadata} for proof in transport["requests"]]
        return {s: result[s] for s in symbols}, evidence

    def history(self, symbol, as_of):
        # Never request or reuse an unfinished day's EOD bar, even in the EOD latch.
        through = as_of.astimezone(NY).date() - dt.timedelta(days=1)
        key = (symbol, through.isoformat())
        if key not in self._history_cache:
            rows, evidence = self._get("historical-price-eod/full", {
                "symbol": symbol, "from": (through - dt.timedelta(
                    days=self._benchmark_days if symbol in self._benchmarks else 180)).isoformat(),
                "to": through.isoformat(),
            })
            accepted, rejected = completed_daily_bars(rows, as_of)
            require(len(accepted) >= 50, f"{symbol}: fewer than 50 completed daily bars.")
            require((through - dt.date.fromisoformat(accepted[-1]["date"])).days <= 5,
                    f"{symbol}: stale daily history.")
            self._history_cache[key] = {
                "bars": accepted, "excluded_unavailable_dates": rejected, **evidence,
                "available_at": evidence["received_at"],
            }
        return copy.deepcopy(self._history_cache[key])

    def histories(self, symbols, as_of, *, timeout=90):
        """Bound the first daily acquisition; cached completed bars need no HTTP."""
        symbols = sorted(set(symbols))
        require(len(symbols) <= MAX_SYMBOLS + 10, "Daily-history universe exceeds budget.")
        pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="shadow-history")
        futures = {pool.submit(self.history, symbol, as_of): symbol for symbol in symbols}
        result = {}
        try:
            for future in as_completed(futures, timeout=timeout):
                result[futures[future]] = future.result()
        except FuturesTimeout:
            raise InputGap("Daily-history batch exceeded 90-second acquisition budget.") from None
        finally:
            # At most eight in-flight GETs remain; each has its own 15-second
            # timeout. Cancel queued work rather than starting hundreds after a gap.
            pool.shutdown(wait=False, cancel_futures=True)
        return result


def completed_daily_bars(rows, as_of):
    cutoff = as_of.astimezone(NY).date()
    accepted, rejected, seen = [], [], set()
    for raw in rows:
        try:
            day = dt.date.fromisoformat(str(raw["date"])[:10])
        except (KeyError, ValueError):
            raise InputGap("Daily history has invalid date.") from None
        if day >= cutoff:
            rejected.append(day.isoformat())
            continue
        require(day not in seen, "Daily history has duplicate dates.")
        seen.add(day)
        require(trading_days_between(day, day + dt.timedelta(days=1)) == 1,
                "Daily history includes a non-session bar.")
        bar = {"date": day.isoformat()}
        for field in ("open", "high", "low", "close", "volume"):
            bar[field] = number(raw.get(field), f"daily.{field}", positive=field != "volume")
        require(bar["volume"] >= 0 and bar["low"] <= min(bar["open"], bar["close"])
                and bar["high"] >= max(bar["open"], bar["close"]), "Invalid daily OHLCV.")
        accepted.append(bar)
    return sorted(accepted, key=lambda b: b["date"]), rejected


def _observer(record, account, as_of, max_age=600):
    raw = record["payload"]
    require(raw.get("account") == account, "Observer account does not match selected account.")
    require(raw.get("complete") is True and raw.get("connected") is True
            and raw.get("atomic") is False, "Observer snapshot is incomplete/non-conforming.")
    stamp = timestamp(raw.get("snapshot_at"))
    require(0 <= (as_of - stamp).total_seconds() <= max_age, "Observer snapshot is stale/future.")
    start = timestamp(raw.get("started_at"))
    require(0 <= (stamp - start).total_seconds() <= 60, "Observer component acquisition took too long.")
    times = raw.get("component_times", {})
    for field in ("positions", "open_orders_all_clients", "account_download", "executions"):
        proof = times.get(field, {})
        begin, end = timestamp(proof.get("requested_at")), timestamp(proof.get("completed_at"))
        require(start <= begin <= end <= stamp, f"Observer {field} completion evidence invalid.")
    require(raw.get("commissions_complete") is True, "Observer commissions are incomplete.")
    for key in ("positions", "portfolio_marks", "open_orders", "account_values", "fills"):
        require(isinstance(raw.get(key), list), f"Observer {key} missing.")
    return raw


def build_seed(pair, before, after, history, fills, *, account, as_of, config):
    require(bool(account), "Explicit selected account is required.")
    require(len(pair) == 2, "Two completed observer snapshots required.")
    left, right = [_observer(r, account, as_of) for r in pair]
    require(0 < (timestamp(right["snapshot_at"]) - timestamp(left["snapshot_at"])).total_seconds() <= 420,
            "Adjacent observer snapshots have a long gap or duplicate timestamp.")
    def signature(raw):
        return {
            "positions": sorted(raw["positions"], key=lambda p: p["contract"]["conId"]),
            "orders": sorted(raw["open_orders"], key=lambda p: str(p["order"].get("orderId"))),
            "fills": sorted((f["execution"] for f in raw["fills"]), key=lambda f: f["execId"]),
        }
    # Receipt times vary; compare order execution state, not telemetry timestamps.
    for raw in (left, right):
        for item in raw["open_orders"]:
            require(item.get("status", {}).get("filled") is not None
                    and item.get("status", {}).get("remaining") is not None,
                    "Open-order filled/remaining status is unavailable; cannot seed protection.")
    def stable(raw):
        value = signature(raw)
        value["orders"] = [{k: row[k] for k in ("ticker", "contract", "order", "status")}
                           for row in value["orders"]]
        return value
    require(fingerprint(stable(left)) == fingerprint(stable(right)),
            "Adjacent observer quantities/orders/fills disagree; wait for concordant observations.")
    for proof in (before, after, history, fills):
        require(proof.get("complete") is True and isinstance(proof.get("rows"), list),
                "Seed source read is incomplete.")
        require(0 <= (as_of - timestamp(proof["received_at"])).total_seconds() <= 120,
                "Seed DB read is stale/future.")
    require(before["rows"] == after["rows"], "Adjacent portfolio DB reads disagree.")
    require(timestamp(before["received_at"]) <= timestamp(after["requested_at"]),
            "Seed DB reads are not ordered.")
    positions = copy.deepcopy(before["rows"])
    by_ticker = {p["ticker"]: p for p in positions}
    require(len(by_ticker) == len(positions), "Duplicate DB holdings.")
    broker_positions = []
    marks = {r["contract"]["conId"]: r for r in right["portfolio_marks"]}
    for row in right["positions"]:
        if row["position"] == 0:
            continue
        c = row["contract"]
        require(row.get("account") == account and c.get("secType") == "STK"
                and c.get("currency") == "USD", "Unsupported account/asset/currency in actual book.")
        quantity = whole(row["position"], f"{row['ticker']}.signed_actual_quantity")
        mark = marks.get(c["conId"])
        require(mark and mark.get("position") == quantity and mark.get("account") == account,
                "Completed account marks disagree with completed quantities.")
        p = by_ticker.get(row["ticker"])
        require(p and whole(p.get("shares"), "DB shares") == quantity, "DB/broker holdings disagree.")
        p["shares"] = quantity
        require(p.get("account_id", account) == account, "DB holding belongs to another account.")
        for field in ("buy_date", "buy_price", "buy_commission", "hwm_price",
                      "highest_unrealized_pct", "hwm_date", "stop_loss_pct",
                      "hard_stop_price", "scaled_out", "exit_armed",
                      "power_hold", "closed_above_entry"):
            require(field in p, f"{row['ticker']}: missing seed field {field}.")
        p["entry_fee_remaining"] = number(p["buy_commission"], "remaining buy commission")
        require(p["entry_fee_remaining"] >= 0, "Negative remaining buy commission.")
        require(p["buy_date"] and str(p["buy_date"])[:10] <= as_of.astimezone(NY).date().isoformat(),
                "Invalid seed buy date.")
        number(p["buy_price"], "seed buy price", positive=True)
        number(p["hwm_price"], "seed high water mark", positive=True)
        if p["exit_armed"]:
            require(p.get("exit_armed_at") and p.get("exit_armed_reason"), "Armed seed metadata missing.")
        broker_positions.append({
            "ticker": row["ticker"], "shares": quantity, "account": account, "sec_type": "STK",
            "currency": "USD", "market_price": number(mark["marketPrice"], "broker mark", positive=True),
            "observed_at": right["snapshot_at"],
            "observation_semantics": "completed_non_atomic_account_download_no_provider_time",
        })
    require(set(by_ticker) == {p["ticker"] for p in broker_positions}, "Extra/missing DB holding.")
    orders = []
    for row in right["open_orders"]:
        o, status, c = row["order"], row["status"], row["contract"]
        require(o.get("account") == account and c.get("secType") == "STK" and c.get("currency") == "USD",
                "Unsupported initial order account/contract.")
        qty = whole(o.get("totalQuantity"), "order totalQuantity")
        require(status.get("filled") == 0 and status.get("remaining") == qty,
                "Partially-filled or unknown remaining seed order.")
        require(o.get("action") == "SELL" and o.get("orderType") in ("STP", "TRAIL"),
                "Pending entry/manual smart OCA orders unsupported for seed.")
        require(o.get("clientId") == 1 and str(o.get("ocaGroup", "")).startswith(
            ("PROT_" + row["ticker"] + "_", "TS_" + row["ticker"] + "_")),
                "Seed order has no recognized bot-protection ownership evidence.")
        require(o.get("outsideRth") is False and type(o.get("triggerMethod")) is int
                and o["triggerMethod"] == 0,
                "Unknown/outside-RTH/non-default protection triggering is unsupported.")
        orders.append({
            "ticker": row["ticker"], "account": account, "sec_type": "STK", "currency": "USD",
            "action": o["action"], "order_type": o["orderType"], "shares": qty,
            "order_id": o["orderId"], "client_id": o["clientId"], "parent_id": o["parentId"],
            "status": status["status"], "tif": o["tif"], "oca_type": o["ocaType"],
            "oca_group": o["ocaGroup"], "trailing_percent": o["trailingPercent"],
            "trail_stop_price": o["trailStopPrice"], "aux_price": o["auxPrice"],
            "filled": 0, "remaining": qty, "protection_source": "bot",
            "outside_rth": o.get("outsideRth"), "trigger_method": o.get("triggerMethod"),
        })
    for snapshot in (left, right):
        equity_rows = []
        for row in snapshot["account_values"]:
            require(isinstance(row, dict) and row.get("account") == account,
                    "Foreign/unscoped account value in completed account download.")
            require(row.get("modelCode", "") == "",
                    "Model-scoped account values cannot seed the whole account.")
            # Ledger/settlement tags include legitimate text; only equity is consumed.
            if row.get("tag") == "NetLiquidation":
                require(row.get("currency") == "USD", "Unsupported NetLiquidation currency.")
                equity_rows.append(row)
        require(len(equity_rows) == 1,
                "Missing or ambiguous completed USD NetLiquidation.")
        equity = number(equity_rows[0].get("value"), "NetLiquidation", positive=True)
    value = sum(p["shares"] * p["market_price"] for p in broker_positions)
    require(equity > 0 and equity - value >= 0, "Unsupported negative cash/margin seed.")
    for row in fills["rows"]:
        require(row.get("account_id") == account, "Unscoped/foreign fill history cannot seed cooldowns.")
        require(timestamp(row.get("fill_time")) <= as_of, "Future fill in seed.")
        number(row.get("commission"), "historical fill commission")
    for row in history["rows"]:
        require(row.get("account_id", account) == account, "Foreign account ledger row.")
        require(str(row.get("sell_date") or "")[:10] <= as_of.astimezone(NY).date().isoformat(),
                "Future ledger row.")
    return {
        "origin": "actual_account_seed", "manual_requests": [], "rotation": False,
        "unsupported_orders": [],
        "timestamp": as_of.isoformat(), "complete": True, "stock_only": True,
        "account": {"account_id": account, "currency": "USD", "net_liquidation": equity,
                    "positions_value": value, "cash": equity - value,
                    "mark_source": "completed_IBKR_account_download",
                    "mark_freshness": "component receipt times; not atomic; provider time unavailable"},
        "positions": positions, "broker_positions": broker_positions, "orders": orders,
        "prior_trade_history": history["rows"], "prior_fills": fills["rows"], "config": config,
        "coherence": "adjacent_concordant_completed_requests_not_atomic",
        "source_evidence": {
            "observer": pair, "portfolio_before": before, "portfolio_after": after,
            "portfolio_positions": after, "broker": {"received_at": right["snapshot_at"]},
            "trade_history": {**history, "filter_lte": as_of.isoformat(),
                              "filter_gte": dt.datetime.combine(
                                  as_of.astimezone(NY).date() - dt.timedelta(
                                      days=config["replay_config"]["cooling_off_days"]),
                                  dt.time(), NY).isoformat()},
            "ibkr_fills": {**fills, "filter_lte": as_of.isoformat(),
                          "filter_gte": dt.datetime.combine(
                              as_of.astimezone(NY).date() - dt.timedelta(
                                  days=config["replay_config"]["cooling_off_days"]),
                              dt.time(), NY).isoformat()},
            "quantities": [{k: p[k] for k in ("ticker", "shares", "account", "sec_type", "currency")}
                           for p in broker_positions],
            "cutoff_semantics": "completed source reads, locally validated no future rows",
        },
    }


def derived_indicators(history):
    bars = history["bars"]
    closes = [r["close"] for r in bars]
    ranges = [max(b["high"] - b["low"], abs(b["high"] - a["close"]),
                  abs(b["low"] - a["close"])) for a, b in zip(bars, bars[1:])]
    atr = sum(ranges[-14:]) / 14
    return {
        "sma20": calculate_sma(closes, 20), "sma50": calculate_sma(closes, 50),
        "ema21": calculate_ema(closes, 21), "rsi14": compute_rsi(closes)[-1],
        "atr14": atr, "atr_pct": 100 * atr / closes[-1], "atr_pct_unit": "percentage_points",
        "previous_close": closes[-1],
        "average_volume_20": sum(b["volume"] for b in bars[-20:]) / 20,
        "source_last_date": bars[-1]["date"], "available_at": history["available_at"],
    }


class InputProducer:
    def __init__(self, sources, market, account, config, clock=lambda: dt.datetime.now(UTC)):
        self.sources, self.market, self.account = sources, market, account
        self.config, self.clock = config, clock
        self._committed_history = set()

    def committed(self, frame):
        """Only a durable frame may make later history references sufficient."""
        for item in frame.get("source_evidence", {}).get("daily_history", {}).values():
            self._committed_history.add(item["sha256"])

    def seed(self):
        self._committed_history.clear()
        before = self.sources.read("portfolio_positions", order="ticker")
        pair = self.sources.observer_pair(self.account)
        cutoff = self.clock()
        start = dt.datetime.combine(cutoff.astimezone(NY).date() - dt.timedelta(
            days=self.config["replay_config"]["cooling_off_days"]), dt.time(), NY).isoformat()
        history = self.sources.read("trade_history",
                                    (("gte", "sell_date", start),),
                                    order="sell_date")
        fills = self.sources.read("ibkr_fills",
                                  (("gte", "fill_time", start),),
                                  order="fill_time")
        after = self.sources.read("portfolio_positions", order="ticker")
        requests = self.sources.read("exit_requests",
                                     (("in_", "status", ["PENDING", "PLACED"]),), order="id")
        require(not [r for r in requests["rows"] if r.get("status") in ("PENDING", "PLACED")],
                "Actual account has pending/manual Smart OCA requests.")
        seed = build_seed(pair, before, after, history, fills, account=self.account,
                          as_of=self.clock(), config=self.config)
        seed["source_evidence"]["exit_requests"] = requests
        return seed

    def frame(self, state, cycle_key, scheduled_at, *, eod=False, decision_cycle=True, end_mark=False):
        import research_configuration as settings
        from research.live_rule_replay import trigger_date
        started = self.clock()
        lookback = (started.astimezone(NY).date() - dt.timedelta(
            days=self.config["replay_config"]["trigger_lookback_days"])).isoformat()
        candidates = self.sources.read(
            "daily_triggers", (("gte", "triggered_at", lookback),), order="id")
        # Keep every row, including NULL AI grades/scores and gate-vetoed names.
        rows = candidates["rows"]
        require(len(rows) <= MAX_SYMBOLS, "Candidate universe exceeds input budget.")
        for row in rows:
            require(row.get("ticker") and "triggered_at" in row, "Candidate identity/date missing.")
            try:
                trigger_date(row["triggered_at"], timestamp(candidates["received_at"]),
                             f"{row['ticker']}.triggered_at")
            except ValueError as exc:
                raise InputGap(str(exc)) from None
            for field in ("final_score", "ai_grade", "close_price", "next_earnings_date",
                          "volume_surge", "trigger_type"):
                require(field in row, f"{row['ticker']}: missing candidate feature {field}.")
        holdings = state.get("positions", {})
        held = set(holdings) if isinstance(holdings, dict) else {p["ticker"] for p in holdings}
        symbols = held | set(state.get("universe", [])) | {r["ticker"] for r in rows}
        require(len(symbols) <= MAX_SYMBOLS, "Hypothetical holdings + candidates exceed budget.")
        history_symbols = sorted(symbols | set(settings.MARKET_DIRECTION_TICKERS))
        if hasattr(self.market, "histories"):
            histories = self.market.histories(history_symbols, started)
        else:
            histories = {s: self.market.history(s, started) for s in history_symbols}
        indicators = {s: derived_indicators(histories[s]) for s in symbols}
        verdicts = {}
        for symbol in settings.MARKET_DIRECTION_TICKERS:
            history = histories[symbol]
            verdict = index_verdict(
                [(b["date"], b["close"]) for b in history["bars"]],
                today=started.astimezone(NY).date(), window=settings.MARKET_DIRECTION_SMA_WINDOW,
                slope_days=settings.MARKET_DIRECTION_SLOPE_DAYS,
                buffer_pct=settings.MARKET_DIRECTION_BUFFER_PCT,
                max_stale_days=settings.MARKET_DIRECTION_MAX_STALE_DAYS)
            require(verdict is not None, f"{symbol}: invalid market-direction source history.")
            verdicts[symbol] = {"above_sma": verdict[0], "slope_ok": verdict[1]}
        require(bool(verdicts) or not settings.MARKET_DIRECTION_FILTER_ENABLED,
                "Market-direction benchmarks are empty.")
        bullish = (not settings.MARKET_DIRECTION_FILTER_ENABLED or (
            all(v["above_sma"] for v in verdicts.values())
            and any(v["slope_ok"] for v in verdicts.values())))
        quotes, quote_evidence = self.market.quotes(symbols)
        finished = self.clock()
        require((finished - scheduled_at).total_seconds() <= 120,
                "Cycle acquisition missed its 120-second scheduled observation window.")
        for symbol, quote in quotes.items():
            require(0 <= (finished - timestamp(quote["provider_timestamp"])).total_seconds() <= 600,
                    f"{symbol}: stale/future provider quote.")
            require(timestamp(quote["provider_timestamp"]) <= timestamp(quote["received_at"]),
                    f"{symbol}: provider quote is later than receipt.")
        feature_rows = []
        for raw in rows:
            row = copy.deepcopy(raw)
            row["available_at"] = candidates["received_at"]
            row["feature_source"] = "supabase:daily_triggers"
            row["feature_acquisition"] = {
                "requested_at": candidates["requested_at"], "received_at": candidates["received_at"],
                "original_triggered_at": raw["triggered_at"],
                "availability_semantics": "first observed in this cycle, not assumed historical availability",
            }
            earning = raw["next_earnings_date"]
            if earning is None:
                row["days_to_earnings"] = None
            else:
                try:
                    day = dt.date.fromisoformat(str(earning)[:10])
                except ValueError:
                    raise InputGap(f"{raw['ticker']}: invalid earnings date.") from None
                today = finished.astimezone(NY).date()
                row["days_to_earnings"] = trading_days_between(today, day) if day >= today else None
            row["earnings_available_at"] = candidates["received_at"]
            feature_rows.append(row)
        history_evidence = {}
        for symbol, history in histories.items():
            digest = fingerprint(history)
            item = {"sha256": digest, "available_at": history["available_at"]}
            if digest not in self._committed_history:
                item["data"] = history
            else:
                item["reference"] = "earlier_committed_frame_in_same_run"
            history_evidence[symbol] = item
        envelope = {
            "cycle_key": cycle_key, "occurred_at": finished.isoformat(),
            "timestamp": finished.isoformat(), "scheduled_at": scheduled_at.isoformat(),
            "session": scheduled_at.astimezone(NY).date().isoformat(),
            "complete": True, "buy_cycle": True, "monitor_cycle": True, "eod": eod,
            "candidates": feature_rows, "quotes": quotes, "indicators": indicators,
            "market_bullish": bullish,
            "market_gate": {"complete": True, "bullish": bullish,
                            "benchmarks": verdicts, "available_at": finished.isoformat()},
            "source_evidence": {"candidate_universe": candidates, "quote_requests": quote_evidence,
                                "daily_history": history_evidence,
                                "derived_indicators": indicators,
                                "captured_universe": sorted(symbols)},
        }
        from research.live_rule_replay import TRIGGER_FIELDS
        normalized = []
        for row in feature_rows:
            trigger = {k: row.get(k) for k in TRIGGER_FIELDS}
            trigger["observed_at"] = candidates["received_at"]
            # Missing screener ATR keeps the same static-stop fallback as live
            # buying; independently derived indicators remain diagnostic evidence.
            normalized.append(trigger)
        observations = {
            symbol: {"price": q["price"], "observed_at": q["provider_timestamp"],
                     "source": "FMP", "delayed": False}
            for symbol, q in quotes.items()
        }
        common = {"timestamp": finished.isoformat(), "session": envelope["session"],
                  "market_observations": observations}
        events = []
        if decision_cycle:
            buy_id, monitor_id = cycle_key + ":buy", cycle_key + ":monitor"
            events.append({
                **common, "type": "buy_cycle", "triggers": normalized,
                "entry_quotes": {t["ticker"]: {**observations[t["ticker"]],
                                              "hypothetical_entry_pricing": True}
                                 for t in normalized},
                "cycle_gates": {"observed_at": finished.isoformat(), "schema_ok": True,
                                "margin_loan": max(0.0, -state["cash"]), "market_allowed": bullish},
                "cycle_context": {"cycle_id": buy_id, "started_at": started.isoformat(),
                                  "completed_at": finished.isoformat()},
            })
            events.append({
                **common, "type": "monitor",
                "cycle_context": {"cycle_id": monitor_id, "started_at": finished.isoformat(),
                                  "completed_at": finished.isoformat(), "preceding_buy_cycle_id": buy_id},
            })
            if eod:
                events.append({**common, "type": "eod_latch",
                               "observed_at": candidates["received_at"],
                               "fresh_trigger_tickers": sorted({r["ticker"] for r in rows})})
            if end_mark:
                events.append({**common, "type": "end_mark"})
        else:
            # Empty stock universes still need a clock/valuation event.
            events.append({**common, "type": "end_mark" if end_mark or not observations else "quote"})
        envelope["engine_frame"] = {"frame_id": cycle_key, "captured_at": finished.isoformat(),
                                    "events": events}
        if decision_cycle:
            try:
                envelope["source_evidence"]["actual_broker_audit_only"] = {
                    "snapshots": self.sources.observer_pair(self.account),
                    "semantics": "actual account changes never reconcile/reset the hypothetical portfolio",
                }
            except Exception as exc:
                envelope["source_evidence"]["actual_broker_audit_only"] = {
                    "unavailable": type(exc).__name__,
                    "semantics": "audit-only source failure does not replace hypothetical state",
                }
        # Full history bodies appear only on first committed acquisition; later
        # frames carry references. Export must retain both bodies and references
        # so it can prove their existence, not merely trust an external hash.
        envelope["engine_frame"]["source_evidence"] = copy.deepcopy(envelope["source_evidence"])
        return envelope
