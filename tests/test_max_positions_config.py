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
print(json.dumps({
    "config": config.MAX_POSITIONS,
    "execution_agent": execution_agent.MAX_POSITIONS,
    "force_buy": force_buy.MAX_POSITIONS,
    "technical_screener": technical_screener.MAX_POSITIONS,
    "backtester": backtester.DEFAULT_MAX_POSITIONS,
}))
""" % (ROOT, ROOT)


def _resolve(value=None):
    """Return each module's MAX_POSITIONS under a given env value."""
    env = dict(os.environ)
    env.pop("MAX_POSITIONS", None)
    if value is not None:
        env["MAX_POSITIONS"] = str(value)
    res = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                         text=True, env=env, cwd=ROOT, timeout=120)
    assert res.returncode == 0, f"probe failed: {res.stderr[-2000:]}"
    return json.loads(res.stdout.strip().splitlines()[-1])


class TestSingleSourceOfTruth:
    def test_all_modules_agree_on_default(self):
        vals = _resolve()
        assert len(set(vals.values())) == 1, (
            f"MAX_POSITIONS defaults diverged across modules: {vals}. "
            "They must all resolve to the same number or the screener will "
            "target a different portfolio size than the agent fills."
        )

    def test_default_is_five_per_adr(self):
        vals = _resolve()
        assert vals["config"] == 5, (
            "ADR 2026-08-04_power-hold-trail-and-five-slots.md selected 5 slots "
            "to cut outlier dependence (top-10 trades 109% -> 92% of P/L)."
        )

    @pytest.mark.parametrize("value", [3, 4, 6, 7])
    def test_env_var_drives_every_module(self, value):
        """Changing slot count must need a .env edit only — never a code change."""
        vals = _resolve(value)
        assert set(vals.values()) == {value}, (
            f"MAX_POSITIONS={value} did not propagate everywhere: {vals}"
        )


class TestNoLocalRedeclaration:
    def test_modules_import_rather_than_reread_env(self):
        """
        A module that re-reads the env itself can drift on its default. Root
        modules must import from config; only backend/ is exempt, since it
        ships as a separate image that does not contain config.py.
        """
        offenders = []
        for name in ("execution_agent.py", "force_buy.py", "technical_screener.py"):
            path = os.path.join(os.path.dirname(__file__), "..", name)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            if 'getenv("MAX_POSITIONS"' in src or "environ.get(\"MAX_POSITIONS\"" in src:
                offenders.append(name)
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
