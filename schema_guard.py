"""Startup schema assertion — fail LOUD when a risk rule's columns are missing.

WHY THIS EXISTS
---------------
On 2026-08-13 an audit found that migrations believed to be applied were not.
`portfolio_positions.closed_above_entry`, `hwm_rs_score` and `highest_rs_score`
were all absent from the live Supabase project. The consequence was not a crash
and not an alert — it was silence:

  * The Thesis Stop reads `closed_above_entry` to confine itself to breakouts
    that never followed through. A missing column reads as None, so the code took
    its conservative fallback (any INTRADAY poke above entry counts as
    follow-through) and the rule was effectively disabled. NBIX and DELL were
    both exempted this way; neither ever closed above entry.
  * Rule 1 (RS Decay) compares `live_rs_score` against `hwm_rs_score`. With no
    anchor column the rule was skipped entirely and never fired once.

Both rules degraded into a fallback path rather than failing, so nothing in the
logs or Telegram indicated that two shipped risk controls were inert. That is
the failure mode this module exists to make impossible.

DESIGN: DEGRADE, DO NOT DIE
---------------------------
The obvious response — abort at boot — is wrong for a trading daemon. Exiting
would stop position monitoring, trailing-stop maintenance and exits entirely,
which is strictly more dangerous than running with one rule impaired.

Instead this mirrors the margin-loan block in run_market_open_buys(): when a
CRITICAL column is missing the agent keeps monitoring and exiting existing
positions, but refuses to open NEW ones. Taking on fresh risk while the risk
controls are degraded is the thing worth preventing.

ADVISORY objects (analytics archives) only warn; they never block trading.

Because the check is cheap it re-runs each buy cycle, so applying the migration
clears the degraded state automatically without a container restart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Columns a live risk rule depends on. Missing => block new buys.
CRITICAL_COLUMNS: dict[str, dict[str, str]] = {
    "portfolio_positions": {
        "closed_above_entry":
            "Thesis Stop follow-through latch — without it the stop falls back to "
            "an intraday-poke test and is effectively disabled "
            "(migrations/add_closed_above_entry.sql)",
        "hwm_rs_score":
            "Rule 1 (RS Decay) anchor — without it RS breakdown never triggers an "
            "exit (migrations/add_hwm_rs_score.sql)",
        "highest_rs_score":
            "Rule 1 (RS Decay) peak tracker "
            "(migrations/add_highest_rs_score.sql)",
    },
}

# Analytics/archive objects. Missing => warn only, never block trading.
ADVISORY_TABLES: dict[str, str] = {
    "trigger_history":
        "point-in-time trigger archive — without it the screener truncates daily "
        "and the rejected-candidate control group is destroyed "
        "(migrations/add_trigger_history.sql)",
    "trigger_decisions":
        "buy/skip audit log (migrations/add_trigger_history.sql)",
    "watchlist_history":
        "point-in-time fundamental screen archive "
        "(migrations/add_watchlist_history.sql)",
    "exit_requests":
        "Smart OCA managed-exit queue — without it request_exit.py cannot queue "
        "an exit and the agent falls back to the automated ladder only "
        "(migrations/add_exit_requests.sql)",
}

REPAIR_SCRIPT = "migrations/2026-08-13_apply_missing_migrations.sql"


@dataclass
class SchemaReport:
    missing_critical: list[tuple[str, str, str]] = field(default_factory=list)
    missing_advisory: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_critical

    @property
    def degraded(self) -> bool:
        return bool(self.missing_critical)

    def summary(self) -> str:
        if self.ok and not self.missing_advisory:
            return "✅ Schema check passed — all risk-rule columns present."
        lines = []
        if self.missing_critical:
            lines.append("🚨 *SCHEMA DEGRADED — NEW BUYS BLOCKED*")
            lines.append("")
            lines.append("Missing columns that live risk rules depend on:")
            for table, col, why in self.missing_critical:
                lines.append(f"• `{table}.{col}`\n  {why}")
        if self.missing_advisory:
            if lines:
                lines.append("")
            lines.append("Missing analytics tables (not blocking):")
            for table, why in self.missing_advisory:
                lines.append(f"• `{table}` — {why}")
        lines.append("")
        lines.append(f"Fix: run `{REPAIR_SCRIPT}` in the Supabase SQL Editor.")
        lines.append("The agent keeps monitoring and exiting existing positions "
                     "and will re-check automatically each buy cycle.")
        return "\n".join(lines)


def _probe(client, table: str, column: str | None = None) -> tuple[bool, str]:
    """True if the table (and column, if given) is queryable."""
    try:
        client.table(table).select(column or "*").limit(1).execute()
        return True, ""
    except Exception as e:            # supabase-py raises APIError
        return False, str(e)[:200]


def check_schema(client) -> SchemaReport:
    """Probe every object a risk rule or archive depends on."""
    report = SchemaReport()
    if client is None:
        report.errors.append("no Supabase client — schema check skipped")
        return report

    for table, cols in CRITICAL_COLUMNS.items():
        table_ok, err = _probe(client, table)
        if not table_ok:
            # The table itself is unreachable. Report every column against it
            # rather than guessing, but do not spam a transport error as drift.
            report.errors.append(f"{table}: {err}")
            continue
        for col, why in cols.items():
            ok, _ = _probe(client, table, col)
            if not ok:
                report.missing_critical.append((table, col, why))

    for table, why in ADVISORY_TABLES.items():
        ok, _ = _probe(client, table)
        if not ok:
            report.missing_advisory.append((table, why))

    return report
