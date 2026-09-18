"""The agent container must contain every module execution_agent.py imports.

WHY THIS EXISTS
---------------
Dockerfile.agent copies source files INDIVIDUALLY rather than `COPY . .`, so a
new module is not deployed unless someone remembers to extend that line. There is
no error at build time -- the gap only appears when the container starts and the
import fails, i.e. in production, on the live trading agent.

Worse, an import guarded by `try/except ImportError` does not fail at all: it
silently degrades. That is exactly what happened to flex_query_sync.py, which was
absent from the image for its entire life. Its import is wrapped in a no-op stub
commented "not available in test environments" -- but it was equally unavailable
in PRODUCTION, so Tier 3 of the sell-price reconstruction ladder (authoritative
IBKR TradeConfirm fills) always returned None and reconciliation fell through to
an FMP estimate. That is the likely origin of the NBIX mis-price repaired on
2026-09-15 (see decisions/2026-09-15_nbix-reconstructed-sell-price.md).

This test walks the real import closure, so it fails the moment a module is added
without extending the COPY line -- including modules imported only indirectly.

See decisions/2026-09-18_execution-agent-split.md.
"""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _local_modules():
    return {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}


def _import_closure(entry: str) -> set:
    """Every project-local module reachable from `entry` by static import."""
    local, seen = _local_modules(), set()

    def walk(mod):
        path = os.path.join(ROOT, mod + ".py")
        if mod in seen or not os.path.exists(path):
            return
        seen.add(mod)
        for node in ast.walk(ast.parse(open(path).read())):
            if isinstance(node, ast.Import):
                for a in node.names:
                    head = a.name.split(".")[0]
                    if head in local:
                        walk(head)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                head = node.module.split(".")[0]
                if head in local:
                    walk(head)

    walk(entry)
    return seen


def _copied_modules() -> set:
    df = open(os.path.join(ROOT, "Dockerfile.agent")).read()
    copied = set()
    # Line continuations make COPY multi-line; flatten first.
    flat = re.sub(r"\\\s*\n", " ", df)
    for m in re.finditer(r"^COPY\s+(.+?)\s+\./\s*$", flat, re.M):
        copied |= set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.py", m.group(1)))
    return copied


class TestAgentImageCompleteness:

    def test_every_imported_module_is_copied_into_the_image(self):
        missing = sorted(_import_closure("execution_agent") - _copied_modules())
        assert not missing, (
            "Dockerfile.agent does not COPY these modules that execution_agent.py "
            f"imports: {missing}. The container will crash on startup -- or, if the "
            "import is wrapped in try/except ImportError, will silently run with a "
            "no-op stub in production. Add them to the COPY line."
        )

    def test_flex_query_sync_is_deployed(self):
        """Regression: its absence silently disabled Tier 3 sell-price recovery.

        Named explicitly because the failure mode is invisible -- the module is
        optional at import time, so nothing breaks loudly when it is missing.
        """
        assert "flex_query_sync" in _copied_modules()

    def test_closure_finds_the_extracted_modules(self):
        """Guards the guard: if the closure walker silently returned very little,
        the assertions above would pass while checking nothing."""
        closure = _import_closure("execution_agent")
        for m in ("exit_rules", "indicators", "market_calendar", "config"):
            assert m in closure, f"{m} missing from computed import closure"
