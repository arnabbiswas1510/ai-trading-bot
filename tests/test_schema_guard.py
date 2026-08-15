"""
tests/test_schema_guard.py

Tests for the startup schema assertion.

Context: on 2026-08-13 an audit found `portfolio_positions.closed_above_entry`,
`hwm_rs_score` and `highest_rs_score` missing from the live Supabase project.
Neither the Thesis Stop nor Rule 1 (RS Decay) failed — both silently degraded to
a fallback path, so two shipped risk controls were inert with no alert. These
tests lock in the behaviour that makes that impossible.

The central design property under test: a degraded schema must BLOCK NEW BUYS
while leaving monitoring and exits untouched. Aborting the daemon would stop
trailing-stop maintenance and exits, which is strictly more dangerous.
"""

from unittest.mock import MagicMock

import schema_guard


class _FakeQuery:
    def __init__(self, table, missing_tables, missing_cols):
        self._t = table
        self._mt = missing_tables
        self._mc = missing_cols
        self._col = None

    def select(self, col):
        self._col = col
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._t in self._mt:
            raise Exception(
                f'{{"code":"PGRST205","message":"Could not find the table '
                f'\'public.{self._t}\' in the schema cache"}}'
            )
        if self._col and self._col != "*" and (self._t, self._col) in self._mc:
            raise Exception(
                f'{{"code":"42703","message":"column {self._t}.{self._col} '
                f'does not exist"}}'
            )
        return MagicMock(data=[])


def _client(missing_tables=(), missing_cols=()):
    c = MagicMock()
    c.table.side_effect = lambda t: _FakeQuery(t, set(missing_tables), set(missing_cols))
    return c


# ── check_schema ──────────────────────────────────────────────────────────────

class TestSchemaHealthy:
    def test_all_present_is_ok_and_not_degraded(self):
        r = schema_guard.check_schema(_client())
        assert r.ok
        assert not r.degraded
        assert r.missing_critical == []
        assert r.missing_advisory == []
        assert "passed" in r.summary()


class TestCriticalColumns:
    def test_missing_closed_above_entry_is_critical(self):
        r = schema_guard.check_schema(
            _client(missing_cols=[("portfolio_positions", "closed_above_entry")]))
        assert r.degraded
        assert not r.ok
        assert [c for _, c, _ in r.missing_critical] == ["closed_above_entry"]

    def test_missing_rs_decay_anchors_are_critical(self):
        r = schema_guard.check_schema(
            _client(missing_cols=[("portfolio_positions", "hwm_rs_score"),
                                  ("portfolio_positions", "highest_rs_score")]))
        assert r.degraded
        assert set(c for _, c, _ in r.missing_critical) == {"hwm_rs_score", "highest_rs_score"}

    def test_summary_names_column_and_repair_script(self):
        r = schema_guard.check_schema(
            _client(missing_cols=[("portfolio_positions", "closed_above_entry")]))
        s = r.summary()
        assert "closed_above_entry" in s
        assert schema_guard.REPAIR_SCRIPT in s
        assert "BUYS BLOCKED" in s

    def test_the_live_2026_08_13_drift_is_detected(self):
        """The exact drift found in production must be reported as degraded."""
        r = schema_guard.check_schema(_client(
            missing_tables=["trigger_history", "trigger_decisions", "watchlist_history"],
            missing_cols=[("portfolio_positions", "closed_above_entry"),
                          ("portfolio_positions", "highest_rs_score"),
                          ("portfolio_positions", "hwm_rs_score")]))
        assert r.degraded
        assert len(r.missing_critical) == 3
        assert len(r.missing_advisory) == 3


class TestAdvisoryTables:
    def test_missing_archive_tables_do_not_block_trading(self):
        r = schema_guard.check_schema(_client(
            missing_tables=["trigger_history", "trigger_decisions", "watchlist_history"]))
        assert r.ok, "analytics archives must never block trading"
        assert not r.degraded
        assert len(r.missing_advisory) == 3

    def test_advisory_listed_in_summary_as_non_blocking(self):
        r = schema_guard.check_schema(_client(missing_tables=["trigger_history"]))
        assert "not blocking" in r.summary()


class TestNoClient:
    def test_missing_client_does_not_report_false_drift(self):
        r = schema_guard.check_schema(None)
        assert not r.degraded
        assert r.errors


# ── assert_schema_ok / buy gate ───────────────────────────────────────────────

class TestBuyGate:
    def _agent(self):
        import execution_agent
        execution_agent._schema_alert_sent = False
        return execution_agent

    def test_healthy_schema_allows_buys(self):
        ea = self._agent()
        assert ea.assert_schema_ok(_client()) is True

    def test_degraded_schema_blocks_buys(self):
        ea = self._agent()
        assert ea.assert_schema_ok(
            _client(missing_cols=[("portfolio_positions", "closed_above_entry")])) is False

    def test_advisory_only_still_allows_buys(self):
        ea = self._agent()
        assert ea.assert_schema_ok(_client(missing_tables=["trigger_history"])) is True

    def test_alert_sent_once_not_every_cycle(self):
        ea = self._agent()
        bad = _client(missing_cols=[("portfolio_positions", "closed_above_entry")])
        ea.notifier.notify_error = MagicMock()
        for _ in range(5):
            assert ea.assert_schema_ok(bad) is False
        assert ea.notifier.notify_error.call_count == 1, "must not spam every 15-min cycle"

    def test_recovery_re_enables_buys_and_notifies(self):
        ea = self._agent()
        bad = _client(missing_cols=[("portfolio_positions", "closed_above_entry")])
        ea.notifier.notify_error = MagicMock()
        assert ea.assert_schema_ok(bad) is False
        assert ea.assert_schema_ok(_client()) is True          # migration applied
        assert ea.notifier.notify_error.call_count == 2
        assert "restored" in ea.notifier.notify_error.call_args[0][0].lower()
        # and it must not keep announcing recovery
        assert ea.assert_schema_ok(_client()) is True
        assert ea.notifier.notify_error.call_count == 2

    def test_probe_failure_does_not_stop_trading(self):
        """A monitoring concern must never become a trading outage."""
        ea = self._agent()
        broken = MagicMock()
        broken.table.side_effect = RuntimeError("network down")
        assert ea.assert_schema_ok(broken) is True

    def test_notifier_failure_is_swallowed(self):
        ea = self._agent()
        ea.notifier.notify_error = MagicMock(side_effect=RuntimeError("telegram down"))
        assert ea.assert_schema_ok(
            _client(missing_cols=[("portfolio_positions", "closed_above_entry")])) is False
