"""Unit tests for scripts/render_env.py — the Bitwarden .env resolver.

No live Bitwarden connection: render() is exercised directly with a fake secrets
dict. Covers sentinel substitution, literal pass-through, and the fail-closed
contract (missing/empty secret raises).

See decisions/2026-09-27_bitwarden-secret-resolution.md.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("render_env", REPO / "scripts" / "render_env.py")
render_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_env)


def test_sentinel_is_resolved_and_literals_pass_through():
    template = (
        "# comment\n"
        "\n"
        "TELEGRAM_BOT_TOKEN=@bws\n"
        "MAX_POSITIONS=5\n"
        "IBKR_FLEX_EXEC_QUERY_ID=@bws\n"
    )
    secrets = {"TELEGRAM_BOT_TOKEN": "123:AA_secret", "IBKR_FLEX_EXEC_QUERY_ID": "1578858"}
    out = render_env.render(template, secrets)
    assert "TELEGRAM_BOT_TOKEN=123:AA_secret" in out
    assert "IBKR_FLEX_EXEC_QUERY_ID=1578858" in out
    assert "MAX_POSITIONS=5" in out          # literal untouched
    assert "# comment" in out                # comment preserved


def test_value_with_equals_and_punctuation_survives():
    template = "SUPABASE_KEY=@bws\n"
    jwt = "eyJhbGciOi.JIUzI1NiIsInR5.cCI6IkpXVCJ9=="  # has '=' and dots
    out = render_env.render(template, {"SUPABASE_KEY": jwt})
    assert out.strip() == f"SUPABASE_KEY={jwt}"


def test_missing_secret_fails_closed():
    with pytest.raises(KeyError) as exc:
        render_env.render("FMP_API_KEY=@bws\n", {})
    assert "FMP_API_KEY" in str(exc.value)


def test_empty_secret_is_treated_as_missing():
    with pytest.raises(KeyError):
        render_env.render("FMP_API_KEY=@bws\n", {"FMP_API_KEY": ""})


def test_blank_openai_line_is_left_blank():
    out = render_env.render("OPENAI_API_KEY=\n", {})
    assert out.strip() == "OPENAI_API_KEY="


# ── Shared bootstrap token / per-app project scoping ─────────────────────────
#
# The machine-account token is shared with other applications on this host, so
# it lives in a neutral location and carries ONLY the token. The project id is
# per-application and is resolved by name, because a project id in a shared
# file is exactly what would let one app render another app's secrets.

PROJECTS = json.dumps(
    [
        {"id": "1ec4b186", "name": "ai-trading-bot"},
        {"id": "8a03c2e4", "name": "ai-health-coach"},
    ]
)


def test_project_is_resolved_by_name():
    assert render_env.resolve_project_id(PROJECTS, "ai-trading-bot") == "1ec4b186"


def test_unknown_project_name_fails_closed_and_lists_what_is_visible():
    with pytest.raises(LookupError) as exc:
        render_env.resolve_project_id(PROJECTS, "nope")
    assert "ai-trading-bot" in str(exc.value)


def test_ambiguous_project_name_refuses_to_guess():
    dupes = json.dumps([{"id": "a", "name": "dup"}, {"id": "b", "name": "dup"}])
    with pytest.raises(LookupError):
        render_env.resolve_project_id(dupes, "dup")


def test_secret_map_is_scoped_to_this_project():
    """A same-named key in the coach's project must never win."""
    secrets = [
        {"key": "TELEGRAM_TOKEN", "value": "trade", "projectId": "1ec4b186"},
        {"key": "TELEGRAM_TOKEN", "value": "coach", "projectId": "8a03c2e4"},
    ]
    assert render_env.build_secret_map(secrets, "1ec4b186") == {"TELEGRAM_TOKEN": "trade"}


def test_conflicting_duplicates_in_one_project_are_a_hard_error():
    """Letting the last one win would silently pick a credential at random."""
    secrets = [
        {"key": "K", "value": "one", "projectId": "p"},
        {"key": "K", "value": "two", "projectId": "p"},
    ]
    with pytest.raises(ValueError):
        render_env.build_secret_map(secrets, "p")


def test_identical_duplicates_are_not_a_conflict():
    secrets = [
        {"key": "K", "value": "same", "projectId": "p"},
        {"key": "K", "value": "same", "projectId": "p"},
    ]
    assert render_env.build_secret_map(secrets, "p") == {"K": "same"}


def _script() -> str:
    return (REPO / "scripts" / "render_env.sh").read_text()


def test_shell_looks_for_the_shared_bootstrap_file():
    """Centralising the token is the whole point; a hard-coded app path undoes it."""
    assert "$HOME/.config/bws/bws.env" in _script()


def test_shell_discards_a_project_id_found_in_the_shared_file():
    """A project id there belongs to whichever app wrote it, not necessarily us."""
    script = _script()
    assert 'BWS_PROJECT_ID="$BWS_PROJECT_ID_OVERRIDE"' in script
    assert "--resolve-project" in script
