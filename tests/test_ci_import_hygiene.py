"""
Guards that the test suite stays runnable in CI's dependency environment.

The Daily Screener workflow installs ONLY the root requirements.txt, then runs
the whole suite before executing any screener step. Anything the tests import
that lives solely in backend/requirements.txt (the trading-bot image) fails at
*collection*, which aborts the entire run -- so a test-only mistake silently
takes out the day's fundamental scan, technical scan and AI evaluation.

That is not hypothetical: it happened on 2026-09-05, when
tests/test_dashboard_pricing.py imported backend.main for a pure function.
backend/main.py does `from fastapi import FastAPI` at module scope, FastAPI is
not in the root requirements, and the screener never ran. The fix was to move
the function to backend/pricing.py, which has no third-party imports at all.

The rule these tests enforce: tests may import from backend/ only where the
module is importable with the root requirements alone.
"""
import ast
import os
import pathlib
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
BACKEND_DIR = REPO_ROOT / "backend"

# Declared in backend/requirements.txt but NOT in the root requirements.txt that
# the Daily Screener workflow installs. Importing any of these at module scope
# in a file the tests reach will break collection on CI while passing locally.
WEB_ONLY_PACKAGES = {"fastapi", "uvicorn", "yfinance"}


def _root_requirements() -> set[str]:
    names = set()
    for line in (REPO_ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        for sep in ("==", ">=", "<=", "~=", ">", "<", "["):
            line = line.split(sep)[0]
        names.add(line.strip().lower().replace("-", "_"))
    return names


def _module_level_imports(path: pathlib.Path) -> set[str]:
    """Top-level import names only. Imports inside functions are lazy and safe."""
    tree = ast.parse(path.read_text())
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def _test_files():
    return sorted(p for p in TESTS_DIR.glob("test_*.py"))


def test_web_only_packages_are_absent_from_root_requirements():
    """
    Pins the premise of the check below. If FastAPI is ever added to the root
    requirements the constraint genuinely relaxes -- but that should be a
    deliberate, visible change rather than something this file silently ignores.
    """
    overlap = WEB_ONLY_PACKAGES & _root_requirements()
    assert not overlap, (
        f"{sorted(overlap)} are now in the root requirements.txt. Update "
        "WEB_ONLY_PACKAGES here; the import restriction may no longer apply."
    )


@pytest.mark.parametrize("test_file", _test_files(), ids=lambda p: p.name)
def test_no_test_imports_a_web_only_dependency_transitively(test_file):
    """
    A test must not import a backend module that needs the web stack.

    Checked one level deep, which is where the real risk sits: a test importing
    a backend module whose own module-scope imports pull in FastAPI.
    """
    for name in _module_level_imports(test_file):
        assert name not in WEB_ONLY_PACKAGES, (
            f"{test_file.name} imports {name!r} directly, which CI does not "
            f"install (root requirements.txt only). This aborts collection and "
            f"takes the Daily Screener down with it."
        )

        backend_module = BACKEND_DIR / f"{name}.py"
        if not backend_module.exists():
            continue

        leaked = _module_level_imports(backend_module) & WEB_ONLY_PACKAGES
        assert not leaked, (
            f"{test_file.name} imports backend/{name}.py, which imports "
            f"{sorted(leaked)} at module scope. CI installs only the root "
            f"requirements.txt, so this fails at collection and the Daily "
            f"Screener never runs. Move the code under test into a module with "
            f"no web-stack imports (see backend/pricing.py)."
        )


def test_pricing_module_stays_dependency_free():
    """
    backend/pricing.py exists precisely so the pricing rules are testable
    without the web stack. A third-party import here would defeat that and
    reintroduce the 2026-09-05 breakage.
    """
    allowed = {"typing", "decimal", "math", "datetime", "zoneinfo"}
    imports = _module_level_imports(BACKEND_DIR / "pricing.py")
    assert imports <= allowed, (
        f"backend/pricing.py must stay importable with the standard library "
        f"alone; found {sorted(imports - allowed)}."
    )
