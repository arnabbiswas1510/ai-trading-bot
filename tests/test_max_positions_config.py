"""
Guards the single-source-of-truth invariant for MAX_POSITIONS.

ADR 2026-08-04 moved the portfolio from 4 slots to 5, but the constant was
declared independently in four modules and only one was updated. The screener
uses its own MAX_POSITIONS as the target for its candidate waterfall, so it
stopped relaxing filters at 4 candidates while the agent had 5 slots to fill —
the fifth slot was starved of candidates and nothing failed loudly.

These tests exist so that divergence cannot recur silently.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Resolved in a SUBPROCESS, never by reloading modules in-process: reloading
# execution_agent rebinds objects other tests already hold references to, which
# silently breaks unrelated suites (it did — that is why this is a subprocess).
_PROBE = """
import json, os, sys
sys.path.insert(0, os.path.join(%r, "backend"))
sys.path.insert(0, %r)
import config, execution_agent, force_buy, technical_screener, backtester
import rotate_positions, force_sell
print(json.dumps({
    "max_positions": {
        "config": config.MAX_POSITIONS,
        "execution_agent": execution_agent.MAX_POSITIONS,
        "force_buy": force_buy.MAX_POSITIONS,
        "technical_screener": technical_screener.MAX_POSITIONS,
        "rotate_positions": rotate_positions.MAX_POSITIONS,
        "backtester": backtester.DEFAULT_MAX_POSITIONS,
    },
    "stop_loss_pct": {
        "config": config.STOP_LOSS_PCT,
        "execution_agent": execution_agent.STOP_LOSS_PCT,
        "force_buy": force_buy.STOP_LOSS_PCT,
        "rotate_positions": rotate_positions.STOP_LOSS_PCT,
        "force_sell": force_sell.STOP_LOSS_PCT,
    },
    "cooling_off_days": {
        "config": config.COOLING_OFF_DAYS,
        "execution_agent": execution_agent.COOLING_OFF_DAYS,
        "force_buy": force_buy.COOLING_OFF_DAYS,
        "rotate_positions": rotate_positions.COOLING_OFF_DAYS,
    },
}))
""" % (ROOT, ROOT)


def _resolve(value=None, var="MAX_POSITIONS"):
    """Return each module's view of the shared constants under a given env value."""
    env = dict(os.environ)
    for k in ("MAX_POSITIONS", "STOP_LOSS_PCT", "COOLING_OFF_DAYS"):
        env.pop(k, None)
    if value is not None:
        env[var] = str(value)
    res = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                         text=True, env=env, cwd=ROOT, timeout=120)
    assert res.returncode == 0, f"probe failed: {res.stderr[-2000:]}"
    return json.loads(res.stdout.strip().splitlines()[-1])


class TestSingleSourceOfTruth:
    @pytest.mark.parametrize("const", ["max_positions", "stop_loss_pct",
                                       "cooling_off_days"])
    def test_all_modules_agree_on_default(self, const):
        vals = _resolve()[const]
        assert len(set(vals.values())) == 1, (
            f"{const} defaults diverged across modules: {vals}. force_buy.py and "
            "rotate_positions.py place REAL orders — a divergent stop or "
            "cooling-off default there silently trades a different strategy."
        )

    def test_defaults_match_the_adrs(self):
        vals = _resolve()
        assert vals["max_positions"]["config"] == 5, (
            "ADR 2026-08-04_power-hold-trail-and-five-slots.md selected 5 slots "
            "to cut outlier dependence (top-10 trades 109% -> 92% of P/L)."
        )
        assert vals["stop_loss_pct"]["config"] == 0.10, (
            "Base trail was widened 7% -> 10%; 7% sat inside the normal daily "
            "range of the higher-ATR names the screener surfaces."
        )
        assert vals["cooling_off_days"]["config"] == 7

    @pytest.mark.parametrize("value", [3, 4, 6, 7])
    def test_max_positions_env_drives_every_module(self, value):
        """Changing slot count must need a .env edit only — never a code change."""
        vals = _resolve(value)["max_positions"]
        assert set(vals.values()) == {value}, (
            f"MAX_POSITIONS={value} did not propagate everywhere: {vals}"
        )

    @pytest.mark.parametrize("value", ["0.07", "0.12"])
    def test_stop_loss_env_drives_every_module(self, value):
        vals = _resolve(value, var="STOP_LOSS_PCT")["stop_loss_pct"]
        assert set(vals.values()) == {float(value)}, (
            f"STOP_LOSS_PCT={value} did not propagate everywhere: {vals}"
        )

    @pytest.mark.parametrize("value", [3, 14])
    def test_cooling_off_env_drives_every_module(self, value):
        vals = _resolve(value, var="COOLING_OFF_DAYS")["cooling_off_days"]
        assert set(vals.values()) == {value}, (
            f"COOLING_OFF_DAYS={value} did not propagate everywhere: {vals}"
        )


class TestNoLocalRedeclaration:
    def test_modules_import_rather_than_reread_env(self):
        """
        A module that re-reads the env itself can drift on its default. Root
        modules must import from config; only backend/ is exempt, since it
        ships as a separate image that does not contain config.py.
        """
        offenders = []
        modules = ("execution_agent.py", "force_buy.py", "technical_screener.py",
                   "rotate_positions.py", "force_sell.py")
        for name in modules:
            path = os.path.join(os.path.dirname(__file__), "..", name)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for const in ("MAX_POSITIONS", "STOP_LOSS_PCT", "COOLING_OFF_DAYS"):
                if f'getenv("{const}"' in src or f'environ.get("{const}"' in src:
                    offenders.append((name, const))
        assert not offenders, (
            f"{offenders} re-read MAX_POSITIONS from the environment instead of "
            "importing it from config.py, reintroducing the drift this guards."
        )

    def test_config_is_shipped_to_the_agent_image(self):
        """Importing config.py is useless if the image does not contain it."""
        path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile.agent")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "config.py" in src, (
            "Dockerfile.agent must COPY config.py or the agent container will "
            "crash on import at startup."
        )
