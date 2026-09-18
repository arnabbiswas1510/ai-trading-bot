"""The trade_history insert must never lose a trade to a long reason string.

Every caller DELETEs the position from portfolio_positions before inserting the
history row, and that delete has already committed. Postgres raises on a
varchar overflow instead of truncating, so before this guard an over-long
sell_reason aborted the insert *after* the position was gone -- destroying the
position row and leaving no trade record at all.
"""
import ast
import pytest

_SRC = open("execution_agent.py").read()
_NS = {"print": print}
for _node in ast.parse(_SRC).body:
    if isinstance(_node, ast.FunctionDef) and _node.name in (
            "_clamp_reason", "insert_trade_history"):
        exec(compile(ast.Module([_node], []), "<x>", "exec"), _NS)
    if isinstance(_node, ast.Assign) and getattr(
            _node.targets[0], "id", "").startswith("SELL_REASON_"):
        exec(compile(ast.Module([_node], []), "<x>", "exec"), _NS)

clamp = _NS["_clamp_reason"]
insert_trade_history = _NS["insert_trade_history"]
LEGACY = _NS["SELL_REASON_LEGACY_LIMIT"]

LONG_REASON = (
    "Trailing stop (IBKR GTC TRAIL order) — trail 1.72%, HWM $180.99 set "
    "2026-09-18, stored HWM STALE (cycle-delayed) — fill implies peak $185.06, "
    "actual trigger $181.88, stop sat at entry +0.76%, day 0 of hold, peak "
    "+0.27% recorded but >=+2.53% implied by fill, armed at $181.00 "
    "(profit lock +5%), power hold active"
)


class _Table:
    def __init__(self, outer, limit):
        self.outer, self.limit = outer, limit

    def insert(self, row):
        self.outer.attempts.append(row)
        if self.limit is not None:
            for key in ("sell_reason", "buy_reason"):
                if len(row.get(key) or "") > self.limit:
                    raise RuntimeError(
                        f'value too long for type character varying({self.limit})')
        self.outer.stored = row
        return self

    def execute(self):
        return type("R", (), {"data": [{"id": 1}]})()


class FakeClient:
    """Mimics Supabase: raises on overflow rather than truncating."""
    def __init__(self, limit=None):
        self.limit, self.attempts, self.stored = limit, [], None

    def table(self, _name):
        return _Table(self, self.limit)


class TestClampReason:
    def test_short_strings_are_untouched(self):
        assert clamp("day 2 of hold", 200) == "day 2 of hold"

    def test_none_survives(self):
        assert clamp(None, 200) is None

    def test_result_never_exceeds_the_limit(self):
        assert len(clamp(LONG_REASON, 200)) <= 200

    def test_cut_lands_on_a_field_boundary_not_mid_number(self):
        # "trigger $18" from "$181.88" reads like a real price and is not one.
        # An obviously truncated string is safer than a plausibly wrong one.
        out = clamp(LONG_REASON, 200)
        assert out.endswith("…")
        assert not out.rstrip("…").rstrip().endswith("$")
        head = out.rstrip("…")
        assert LONG_REASON.startswith(head)
        assert LONG_REASON[len(head):len(head) + 2] == ", "

    def test_highest_value_diagnostics_survive_truncation(self):
        # If only 200 chars fit, these are the ones worth keeping: they are what
        # makes a stop that fired ABOVE entry visible at all.
        out = clamp(LONG_REASON, 200)
        assert "stored HWM STALE" in out
        assert "actual trigger $181.88" in out


class TestInsertTradeHistory:
    def test_wide_column_stores_the_full_reason(self):
        c = FakeClient(limit=None)
        insert_trade_history(c, {"ticker": "SMTC", "sell_reason": LONG_REASON})
        assert c.stored["sell_reason"] == LONG_REASON
        assert len(c.attempts) == 1, "must not retry when the insert succeeded"

    def test_narrow_column_still_records_the_trade(self):
        # The migration may not have been applied yet. Deployment ordering must
        # not be able to destroy a trade record.
        c = FakeClient(limit=LEGACY)
        insert_trade_history(c, {"ticker": "SMTC", "sell_reason": LONG_REASON})
        assert c.stored is not None, "trade record was lost"
        assert len(c.stored["sell_reason"]) <= LEGACY
        assert len(c.attempts) == 2, "should try full first, then truncated"

    def test_the_full_reason_is_always_attempted_first(self):
        c = FakeClient(limit=LEGACY)
        insert_trade_history(c, {"ticker": "SMTC", "sell_reason": LONG_REASON})
        assert c.attempts[0]["sell_reason"] == LONG_REASON

    def test_buy_reason_is_protected_too(self):
        c = FakeClient(limit=LEGACY)
        insert_trade_history(c, {"ticker": "X", "buy_reason": LONG_REASON})
        assert c.stored is not None
        assert len(c.stored["buy_reason"]) <= LEGACY

    def test_unrelated_failures_are_not_swallowed(self):
        # Retrying a truncated string would hide a real outage and report a
        # success that never happened.
        class Broken(FakeClient):
            def table(self, _n):
                raise RuntimeError("connection refused")
        with pytest.raises(RuntimeError, match="connection refused"):
            insert_trade_history(Broken(), {"ticker": "X", "sell_reason": "short"})

    def test_runaway_string_is_bounded_even_on_a_text_column(self):
        c = FakeClient(limit=None)
        insert_trade_history(c, {"ticker": "X", "sell_reason": "a, " * 5000})
        assert len(c.stored["sell_reason"]) <= _NS["SELL_REASON_RUNAWAY_LIMIT"]
