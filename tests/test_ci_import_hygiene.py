"""
Guards that the test suite stays runnable in CI's dependency environment.

The Daily Screener installs requirements-test.txt, including root runtime
dependencies and explicit test-only extras, before running the whole suite.
An undeclared import can abort collection and prevent every screener step.

That is not hypothetical: it happened on 2026-09-05, when
tests/test_dashboard_pricing.py imported backend.main for a pure function.
backend/main.py does `from fastapi import FastAPI` at module scope, FastAPI is
not in the root requirements, and the screener never ran. The fix was to move
the function to backend/pricing.py, which has no third-party imports at all.

API tests now require FastAPI itself, unlike pure pricing tests. It is explicitly
installed only for tests; undeclared web dependencies remain forbidden and the
runtime manifests stay separate.
"""
import ast
import os
import pathlib
import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
BACKEND_DIR = REPO_ROOT / "backend"

# Web packages must stay out of the agent/screener runtime manifest.
WEB_ONLY_PACKAGES = {"fastapi", "uvicorn", "yfinance"}


def _root_requirements(path=REPO_ROOT / "requirements.txt") -> set[str]:
    names = set()
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            names.update(_root_requirements(path.parent / line[3:].strip()))
            continue
        for sep in ("==", ">=", "<=", "~=", ">", "<", "["):
            line = line.split(sep)[0]
        names.add(line.strip().lower().replace("-", "_"))
    return names


def test_daily_gate_installs_complete_test_manifest():
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/daily_screener.yml").read_text()
    )
    steps = workflow["jobs"]["run-screeners"]["steps"]
    install = next(step["run"] for step in steps if step.get("name") == "Install Dependencies")
    assert "python -m pip install -r requirements-test.txt" in install
    requirements = _root_requirements(REPO_ROOT / "requirements-test.txt")
    assert {"fastapi", "pyyaml", "pyarrow", "duckdb", "exchange_calendars"} <= requirements
    test_step = next(step for step in steps if step.get("name") == "Run Unit Tests")
    assert "python -m pytest tests/" in test_step["run"]
    assert not test_step.get("continue-on-error", False)


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
    A test must not import an undeclared part of the web stack.

    Checked one level deep, which is where the real risk sits: a test importing
    a backend module whose own module-scope imports pull in FastAPI.
    """
    unavailable = WEB_ONLY_PACKAGES - _root_requirements(REPO_ROOT / "requirements-test.txt")
    for name in _module_level_imports(test_file):
        assert name not in unavailable, (
            f"{test_file.name} imports {name!r} directly, which CI does not "
            f"install (requirements-test.txt). This aborts collection and "
            f"takes the Daily Screener down with it."
        )

        backend_module = BACKEND_DIR / f"{name}.py"
        if not backend_module.exists():
            continue

        leaked = _module_level_imports(backend_module) & unavailable
        assert not leaked, (
            f"{test_file.name} imports backend/{name}.py, which imports "
            f"{sorted(leaked)} at module scope. CI installs "
            f"requirements-test.txt, so this fails at collection and the Daily "
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
