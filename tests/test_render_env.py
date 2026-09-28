"""Unit tests for scripts/render_env.py — the Bitwarden .env resolver.

No live Bitwarden connection: render() is exercised directly with a fake secrets
dict. Covers sentinel substitution, literal pass-through, and the fail-closed
contract (missing/empty secret raises).

See decisions/2026-09-27_bitwarden-secret-resolution.md.
"""
from __future__ import annotations

import importlib.util
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
