"""CI bootstrap tests use synthetic secrets and never contact Bitwarden."""
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from scripts import run_intraday_reporting_bws as bootstrap


PROJECT = "11111111-1111-4111-8111-111111111111"
ENV = {"BWS_ACCESS_TOKEN": "synthetic-bootstrap", "GITHUB_TOKEN": "synthetic-github",
       "GITHUB_REPOSITORY": "example/repo", "TRADING_RUNTIME_MODE": "observe"}


def secret_rows():
    return [{"key": key, "value": "synthetic-" + key, "projectId": PROJECT}
            for key in bootstrap.REQUIRED]


def fake_runner(rows=None, projects=None, child_code=0):
    calls = []
    replies = [
        [{"name": "ai-trading-bot", "id": PROJECT}] if projects is None else projects,
        secret_rows() if rows is None else rows,
    ]

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "bws":
            return SimpleNamespace(returncode=0, stdout=json.dumps(replies.pop(0)))
        return SimpleNamespace(returncode=child_code)

    return run, calls


@pytest.mark.parametrize("value", ["", " ", "@bws", " @bws ", None, "\0", "bad\nvalue"])
def test_missing_or_invalid_bootstrap_names_exact_actions_secret(value, capsys):
    runner, calls = fake_runner()
    assert bootstrap.main([], {**ENV, "BWS_ACCESS_TOKEN": value}, runner) == 1
    assert calls == []
    assert "GitHub Actions repository secret BWS_ACCESS_TOKEN" in capsys.readouterr().err


def test_scoped_requests_child_environment_flags_and_exit_code(capsys):
    rows = secret_rows() + [
        {"key": "UNRELATED_SECRET", "value": "never-export-this", "projectId": PROJECT},
        {"key": "SUPABASE_URL", "value": "wrong-project", "projectId": "other"},
    ]
    runner, calls = fake_runner(rows, child_code=7)
    env = {**ENV, **dict.fromkeys(bootstrap.REQUIRED, ""),
           "BWS_SERVER_URL": "https://untrusted.invalid",
           "BWS_PROFILE": "unsafe", "BWS_CONFIG_FILE": "unsafe"}
    assert bootstrap.main(["--save-calibration", "frozen.json"], env, runner) == 7
    assert len(calls) == 3
    for command, kwargs in calls[:2]:
        assert command[:5] == ["bws", "--config-file",
                               str(bootstrap.ROOT / "scripts/bws_ci.toml"),
                               "--profile", "watchdog"]
        assert kwargs["capture_output"] and kwargs["timeout"] == 60
        assert kwargs["env"]["BWS_ACCESS_TOKEN"] == ENV["BWS_ACCESS_TOKEN"]
        assert "BWS_SERVER_URL" not in kwargs["env"]
        assert ENV["BWS_ACCESS_TOKEN"] not in command
    assert calls[1][0][5:] == ["secret", "list", PROJECT, "-o", "json"]
    command, kwargs = calls[2]
    assert command[1:] == [str(bootstrap.ROOT / "research_entrypoint.py"),
                           "research-reporting", "--save-calibration", "frozen.json"]
    child = kwargs["env"]
    assert all(child[key] == "synthetic-" + key for key in bootstrap.REQUIRED)
    assert child["GITHUB_TOKEN"] == ENV["GITHUB_TOKEN"]
    assert child["TRADING_RUNTIME_MODE"] == "observe"
    assert not any(key.startswith("BWS_") for key in child)
    assert "UNRELATED_SECRET" not in child
    output = capsys.readouterr()
    assert not output.err
    assert all(line.startswith("::add-mask::") for line in output.out.splitlines())
    assert "never-export-this" not in output.out
    assert "wrong-project" not in output.out


@pytest.mark.parametrize("problem", ["missing", "empty", "whitespace", "placeholder",
                                    "duplicate", "conflict", "newline", "cr", "nul",
                                    "non-string", "wrong-project"])
def test_required_secrets_fail_closed_without_falling_back(problem, capsys):
    rows = secret_rows()
    target = next(row for row in rows if row["key"] == "INTRADAY_SUPABASE_KEY")
    if problem == "missing":
        rows.remove(target)
    elif problem in ("duplicate", "conflict"):
        rows.append({**target, "value": target["value"] if problem == "duplicate" else "other"})
    elif problem == "wrong-project":
        target["projectId"] = "other"
    else:
        target["value"] = {
            "empty": "", "whitespace": " \t", "placeholder": " @bws ",
            "newline": "secret\n::error::injected", "cr": "secret\rdata",
            "nul": "secret\0data", "non-string": 42,
        }[problem]
    runner, calls = fake_runner(rows)
    assert bootstrap.main([], ENV, runner) == 1
    assert len(calls) == 2
    output = capsys.readouterr()
    assert "secret named INTRADAY_SUPABASE_KEY" in output.err
    assert "synthetic-SUPABASE_KEY" not in output.err
    assert "::error::injected" not in output.err


@pytest.mark.parametrize("projects", [
    [], [{"name": "other", "id": PROJECT}],
    [{"name": "ai-trading-bot", "id": PROJECT}] * 2,
    [{"name": "ai-trading-bot", "id": "not-a-uuid"}],
    {"private": "never-print"}, [None],
])
def test_project_resolution_rejects_missing_ambiguous_or_invalid(projects, capsys):
    runner, calls = fake_runner(projects=projects)
    assert bootstrap.main([], ENV, runner) == 1
    assert len(calls) == 1
    assert "never-print" not in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["http", "json", "timeout", "missing-cli"])
@pytest.mark.parametrize("fail_on", [1, 2])
def test_request_failures_do_not_echo_transport_or_token(failure, fail_on, capsys):
    good, calls = fake_runner()
    counter = 0

    def run(command, **kwargs):
        nonlocal counter
        counter += 1
        if counter != fail_on:
            return good(command, **kwargs)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("never-print-raw-secret", 60,
                                            output="never-print-raw-secret")
        if failure == "missing-cli":
            raise FileNotFoundError("never-print-raw-secret")
        return SimpleNamespace(returncode=1 if failure == "http" else 0,
                               stdout="never-print-raw-secret", stderr="never-print-raw-secret")

    assert bootstrap.main([], ENV, run) == 1
    assert not any(command[0] != "bws" for command, _ in calls)
    output = capsys.readouterr()
    assert "never-print-raw-secret" not in output.out + output.err
    assert ENV["BWS_ACCESS_TOKEN"] not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("rows", [{"value": "never-print"}, [None]])
def test_malformed_secret_response_is_safe(rows, capsys):
    runner, calls = fake_runner(rows=rows)
    assert bootstrap.main([], ENV, runner) == 1
    assert len(calls) == 2
    assert "never-print" not in capsys.readouterr().err


def test_masks_escape_workflow_commands(capsys):
    bootstrap.mask("fake%token\r\n::error::injected")
    assert capsys.readouterr().out == "::add-mask::fake%25token%0D%0A::error::injected\n"


def test_child_launch_exception_does_not_leak_credentials(capsys):
    good, _ = fake_runner()

    def run(command, **kwargs):
        if command[0] == "bws":
            return good(command, **kwargs)
        raise OSError("never-print-child-environment")

    assert bootstrap.main([], ENV, run) == 1
    assert "never-print-child-environment" not in capsys.readouterr().err


def test_ci_uses_pinned_cli_and_disables_secret_cache():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/intraday_research_review.yml").read_text()
    assert "bws-v2.1.0/bws-x86_64-unknown-linux-gnu-2.1.0.zip" in workflow
    assert "ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860" in workflow
    assert workflow.index("sha256sum --check") < workflow.index("unzip")
    for key in bootstrap.REQUIRED:
        assert "secrets." + key not in workflow
    assert "GITHUB_ENV" not in workflow
    assert 'state_opt_out = "true"' in (root / "scripts/bws_ci.toml").read_text()
