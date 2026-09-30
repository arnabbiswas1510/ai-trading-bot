"""The web/dashboard container must contain every module its code imports.

WHY THIS EXISTS
---------------
The web image (root Dockerfile) is built `COPY backend/ ./backend/` plus an
EXPLICIT list of root modules the backend needs at runtime. A backend module that
imports a root module NOT on that COPY line does not fail at build time -- the gap
only surfaces when the container starts and the import raises, i.e. in production,
on the live dashboard.

This became load-bearing with Option A
(decisions/2026-09-29_backtester-option-a-live-exits.md): backend/backtester.py now
`import daily_exit_sim`, which pulls in config/exit_rules/exit_core. If any of those
is dropped from the Dockerfile the dashboard crashes on boot.

This test walks the real import closure of every backend module and asserts each
root-level dependency is COPY'd into the image -- so it fails the moment a new root
import is added without extending the COPY line, including transitive imports.
"""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")


def _root_modules() -> set:
    """Top-level .py modules that live at the repo root (import candidates)."""
    return {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}


def _backend_modules() -> set:
    return {f[:-3] for f in os.listdir(BACKEND) if f.endswith(".py")}


def _root_imports_of(entry_dir: str, entry: str, roots: set, backends: set) -> set:
    """Every ROOT-level module reachable from `entry` by static import, following
    backend-internal imports transitively."""
    seen_backend: set = set()
    needed_roots: set = set()

    def walk(mod: str):
        # A module name can resolve to a backend file OR a root file. Prefer
        # backend (that is where the entry point lives and shadows root in-image).
        backend_path = os.path.join(BACKEND, mod + ".py")
        root_path = os.path.join(ROOT, mod + ".py")
        if mod in backends and os.path.exists(backend_path):
            if mod in seen_backend:
                return
            seen_backend.add(mod)
            path = backend_path
        elif mod in roots and os.path.exists(root_path):
            needed_roots.add(mod)
            # Follow the root module's own imports (they must be copied too).
            path = root_path
            if mod in seen_backend:
                return
            seen_backend.add(mod)
        else:
            return
        for node in ast.walk(ast.parse(open(path).read())):
            if isinstance(node, ast.Import):
                for a in node.names:
                    walk(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                walk(node.module.split(".")[0])

    walk(entry)
    return needed_roots


def _copied_root_modules() -> set:
    """Root modules explicitly COPY'd into the image (excludes `COPY backend/`)."""
    df = open(os.path.join(ROOT, "Dockerfile")).read()
    flat = re.sub(r"\\\s*\n", " ", df)
    copied = set()
    for m in re.finditer(r"^COPY\s+(.+?)\s+\./backend/?\s*$", flat, re.M):
        arg = m.group(1)
        if arg.strip().startswith("backend/"):
            continue  # the whole-backend copy, not a root-module list
        copied |= set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.py", arg))
    return copied


class TestWebImageCompleteness:

    def test_intraday_api_import_closure_is_copied(self):
        roots, backends = _root_modules(), _backend_modules()
        needed = _root_imports_of(BACKEND, "intraday_service", roots, backends)
        assert not needed - _copied_root_modules(), (
            f"Missing intraday research dependencies: {needed - _copied_root_modules()}")

    def test_every_root_module_backtester_imports_is_copied(self):
        roots, backends = _root_modules(), _backend_modules()
        needed = _root_imports_of(BACKEND, "backtester", roots, backends)
        missing = sorted(needed - _copied_root_modules())
        assert not missing, (
            "The web Dockerfile does not COPY these root modules that "
            f"backend/backtester.py imports: {missing}. The dashboard container "
            "will crash on startup at import time. Add them to the "
            "`COPY <modules> ./backend/` line."
        )

    def test_daily_exit_sim_is_deployed(self):
        """Regression: backtester's live-exit parity depends on it (Option A)."""
        assert "daily_exit_sim" in _copied_root_modules()

    def test_closure_finds_the_live_exit_engine(self):
        """Guards the guard: if the walker returned nothing the assertion above
        would pass while checking nothing."""
        roots, backends = _root_modules(), _backend_modules()
        needed = _root_imports_of(BACKEND, "backtester", roots, backends)
        for m in ("daily_exit_sim", "exit_core", "exit_rules", "config"):
            assert m in needed, f"{m} missing from computed import closure"
