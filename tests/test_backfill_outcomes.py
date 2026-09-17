"""Per-horizon outcome measurement in backfill_trigger_outcomes.py.

WHY THIS EXISTS
---------------
`fwd_1d`, `fwd_5d` and `fwd_20d` used to be written as one all-or-nothing unit: a
row was discarded entirely unless all 20 sessions existed. Because the three
horizons mature at very different rates, the two short ones were withheld for a
month by the long one.

Measured 2026-09-17 over the 233-row archive:

    fwd_1d   222/233 rows measurable   34/37 BREAKOUT
    fwd_5d   185/233 rows measurable   24/37 BREAKOUT
    fwd_20d   16/233 rows measurable    0/37 BREAKOUT
    ACTUAL    16/233 rows written       0/37 BREAKOUT

Zero measured BREAKOUT rows is what blocked refitting the breakout failure
penalty -- whose "failures" are day-0/day-1 stop-outs, precisely what fwd_1d and
fwd_5d measure.

The fix introduces two hazards that these tests exist to pin:

1. A partial row must not be stamped complete, or it would never be revisited and
   every row would silently cap at its first measurement.
2. A short window must not be written under a "20d" name. A 5-bar max drawdown is
   not a small 20-bar drawdown; it is a different quantity, and storing it would
   understate risk in every study that reads the column.

See decisions/2026-09-17_per-horizon-outcomes.md.
"""
from __future__ import annotations

import datetime

import pytest

import backfill_trigger_outcomes as b


def _bars(n, start_day=1, base=100.0):
    """n consecutive sessions, rising steadily so returns are easy to reason about."""
    out = []
    for i in range(n):
        d = datetime.date(2026, 8, 3) + datetime.timedelta(days=start_day + i)
        p = base + i
        out.append({"date": d.isoformat(), "open": p, "close": p + 1,
                    "high": p + 2, "low": p - 1})
    return out


class TestHorizonThresholds:
    def test_a_single_session_is_enough_to_write_something(self):
        assert b.MIN_BARS_REQUIRED == min(b.HORIZONS) == 1

    def test_completeness_still_requires_the_longest_horizon(self):
        assert b.COMPLETE_BARS_REQUIRED == max(b.HORIZONS) == 20

    def test_selection_window_is_shorter_than_the_completion_window(self):
        """Selection must admit rows the completion threshold would still reject."""
        assert b.MIN_SETTLE_DAYS < b.SETTLE_DAYS


class TestPartialMeasurement:
    def test_short_window_yields_the_short_horizons(self):
        res = b.compute_outcomes(_bars(8), "2026-08-04")
        assert res["fwd_1d_pct"] is not None
        assert res["fwd_5d_pct"] is not None
        assert res["fwd_20d_pct"] is None

    def test_short_window_omits_20d_named_path_metrics(self):
        """A 5-bar drawdown must never be stored as a 20-bar drawdown."""
        res = b.compute_outcomes(_bars(8), "2026-08-04")
        for k in ("max_gain_20d_pct", "max_drawdown_20d_pct", "ever_above_entry"):
            assert k not in res, f"{k} must not be written before 20 sessions exist"

    def test_full_window_includes_20d_path_metrics(self):
        res = b.compute_outcomes(_bars(30), "2026-08-04")
        assert res["outcome_bars"] == 20
        assert res["fwd_20d_pct"] is not None
        for k in ("max_gain_20d_pct", "max_drawdown_20d_pct", "ever_above_entry"):
            assert k in res

    def test_no_sessions_after_trigger_is_unmeasurable(self):
        assert b.compute_outcomes(_bars(3), "2099-01-01") is None


class _FakeQuery:
    def __init__(self, store, table):
        self._store, self._table, self._filters = store, table, {}

    def select(self, *_a, **_k):
        return self

    def lte(self, col, val):
        self._filters["lte"] = (col, val); return self

    def is_(self, col, _val):
        self._filters["isnull"] = col; return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._filters["limit"] = n; return self

    def eq(self, col, val):
        self._filters.setdefault("eq", {})[col] = val; return self

    def update(self, payload):
        self._store["updates"].append((dict(self._filters.get("eq", {})), payload))
        self._payload = payload
        return self

    def execute(self):
        if hasattr(self, "_payload"):
            eqs = self._store["updates"][-1][0]
            for r in self._store["rows"]:
                if all(r.get(k) == v for k, v in eqs.items()):
                    r.update(self._store["updates"][-1][1])
            return type("R", (), {"data": []})()
        rows = self._store["rows"]
        col, val = self._filters.get("lte", (None, None))
        if col:
            rows = [r for r in rows if str(r.get(col, "")) <= val]
        if "isnull" in self._filters:
            rows = [r for r in rows if r.get(self._filters["isnull"]) is None]
        return type("R", (), {"data": list(rows)})()


class _FakeClient:
    def __init__(self, rows):
        self.store = {"rows": rows, "updates": []}

    def table(self, name):
        return _FakeQuery(self.store, name)


@pytest.fixture
def patched(monkeypatch):
    """Run the pipeline against synthetic prices and a fake Supabase."""
    def fake_prices(ticker, start, end, session=None):
        return _bars(40) if ticker != "NOPRICE" else []
    monkeypatch.setattr(b, "fetch_prices", fake_prices)
    monkeypatch.setattr(b, "_today_ny", lambda: datetime.date(2026, 9, 17))
    return fake_prices


class TestStampingContract:
    """The stamp is what decides whether a row is ever revisited."""

    def _run(self, monkeypatch, trigger_date, nbars):
        rows = [{"triggered_at": trigger_date, "ticker": "AAA",
                 "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        client = _FakeClient(rows)
        monkeypatch.setattr(b, "fetch_prices",
                            lambda t, s, e, sess=None: _bars(nbars))
        monkeypatch.setattr(b, "_today_ny", lambda: datetime.date(2026, 9, 17))
        monkeypatch.setattr(b, "SUPABASE_URL", "http://x")
        monkeypatch.setattr(b, "SUPABASE_KEY", "k")
        monkeypatch.setattr(b, "FMP_API_KEY", "k")
        monkeypatch.setattr(b, "create_client", lambda *_a, **_k: client)
        b.run(dry_run=False)
        return client.store["updates"]

    def test_partial_row_is_not_stamped(self, monkeypatch):
        updates = self._run(monkeypatch, "2026-08-04", nbars=8)
        assert updates, "a partial row should still be written"
        _eq, payload = updates[0]
        assert "outcomes_computed_at" not in payload, (
            "stamping a partial row would stop it ever being revisited"
        )
        assert payload["fwd_1d_pct"] is not None

    def test_complete_row_is_stamped(self, monkeypatch):
        updates = self._run(monkeypatch, "2026-08-04", nbars=40)
        _eq, payload = updates[0]
        assert "outcomes_computed_at" in payload
        assert payload["fwd_20d_pct"] is not None

    def test_payload_never_contains_nulls(self, monkeypatch):
        """A later pass must ADD knowledge, never erase an earlier pass's work."""
        updates = self._run(monkeypatch, "2026-08-04", nbars=8)
        _eq, payload = updates[0]
        assert all(v is not None for v in payload.values())
        assert "fwd_20d_pct" not in payload


class TestSelection:
    def test_recent_rows_are_now_selected(self, patched):
        """A row 10 days old was previously invisible; it has a valid fwd_5d."""
        rows = [{"triggered_at": "2026-09-07", "ticker": "AAA",
                 "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        got = b.fetch_pending(_FakeClient(rows))
        assert len(got) == 1, "rows past MIN_SETTLE_DAYS must be selected"

    def test_rows_inside_the_settle_window_are_excluded(self, patched):
        rows = [{"triggered_at": "2026-09-16", "ticker": "AAA",
                 "trigger_type": "BREAKOUT", "outcomes_computed_at": None}]
        assert b.fetch_pending(_FakeClient(rows)) == []

    def test_completed_rows_drop_out(self, patched):
        rows = [{"triggered_at": "2026-08-14", "ticker": "AAA",
                 "trigger_type": "BREAKOUT",
                 "outcomes_computed_at": "2026-09-17T00:00:00Z"}]
        assert b.fetch_pending(_FakeClient(rows)) == []

    def test_force_reselects_completed_rows(self, patched):
        rows = [{"triggered_at": "2026-08-14", "ticker": "AAA",
                 "trigger_type": "BREAKOUT",
                 "outcomes_computed_at": "2026-09-17T00:00:00Z"}]
        assert len(b.fetch_pending(_FakeClient(rows), force=True)) == 1
