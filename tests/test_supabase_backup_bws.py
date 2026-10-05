"""Backup vault bootstrap uses synthetic credentials and never launches a backup."""
import pytest

from scripts import run_intraday_reporting_bws as shared
from scripts import run_supabase_backup_bws as backup
from tests.test_intraday_reporting_bws import ENV, fake_runner, secret_rows


def test_reuses_existing_vault_loader():
    assert backup.load_credentials is shared.load_credentials


@pytest.mark.parametrize("exit_code", [0, 1, 2, 7])
def test_backup_credentials_arguments_and_exit_code(exit_code, capsys):
    runner, calls = fake_runner(child_code=exit_code)
    env = {**ENV, **dict.fromkeys(shared.REQUIRED, "stale-direct-secret"),
           "BWS_PROFILE": "unsafe", "BWS_SERVER_URL": "https://unsafe.invalid"}
    args = ["--out-dir", "./backup-staging", "--dry-run",
            "--snapshot-date", "2026-10-04"]
    assert backup.main(args, env, runner) == exit_code
    command, kwargs = calls[-1]
    assert command[1:] == [str(shared.ROOT / "supabase_backup.py"), *args]
    assert kwargs["cwd"] == shared.ROOT
    assert not kwargs.get("shell", False)
    assert not any(key.startswith("BWS_") for key in kwargs["env"])
    assert all(kwargs["env"][key] == "synthetic-" + key for key in backup.REQUIRED)
    assert "INTRADAY_SUPABASE_KEY" not in kwargs["env"]
    assert "stale-direct-secret" not in str(calls[-1])
    assert not capsys.readouterr().err


def test_input_is_passed_as_a_single_argument_not_executed():
    runner, calls = fake_runner()
    value = '2026-10-04"; $(touch NEVER_CREATE); #'
    assert backup.main(["--snapshot-date", value], ENV, runner) == 0
    assert calls[-1][0][-2:] == ["--snapshot-date", value]
    assert not calls[-1][1].get("shell")


def test_backup_does_not_require_or_forward_private_research_key(capsys):
    rows = [row for row in secret_rows() if row["key"] != "INTRADAY_SUPABASE_KEY"]
    runner, calls = fake_runner(rows=rows)
    assert backup.main([], {**ENV, "INTRADAY_SUPABASE_KEY": "stale-secret"}, runner) == 0
    assert len(calls) == 3
    assert "INTRADAY_SUPABASE_KEY" not in calls[-1][1]["env"]
    assert not capsys.readouterr().err


@pytest.mark.parametrize("key", backup.REQUIRED)
def test_no_direct_secret_fallback_when_required_vault_secret_is_absent(key, capsys):
    rows = [row for row in secret_rows() if row["key"] != key]
    runner, calls = fake_runner(rows=rows)
    assert backup.main([], {**ENV, key: "stale-secret"}, runner) == 1
    assert len(calls) == 2
    error = capsys.readouterr().err
    assert key in error
    assert "stale-secret" not in error


def test_missing_bootstrap_stops_before_any_child(capsys):
    runner, calls = fake_runner()
    assert backup.main([], {}, runner) == 1
    assert not calls
    assert "BWS_ACCESS_TOKEN" in capsys.readouterr().err


def test_child_exception_does_not_print_environment(capsys):
    good, _ = fake_runner()

    def runner(command, **kwargs):
        if command[0] == "bws":
            return good(command, **kwargs)
        raise RuntimeError("SECRET-TRANSPORT " + str(kwargs["env"]))

    assert backup.main([], ENV, runner) == 1
    output = capsys.readouterr()
    assert "SECRET-TRANSPORT" not in output.out + output.err
    assert "synthetic-" not in output.err
    assert "Traceback" not in output.err


def test_workflow_bootstrap_is_pinned_and_inputs_are_not_shell_source():
    workflow = (shared.ROOT / ".github/workflows/weekly_supabase_backup.yml").read_text()
    assert "bws-v2.1.0/bws-x86_64-unknown-linux-gnu-2.1.0.zip" in workflow
    assert "ba8233c3a4aee5d43e3c73bbd04d99e9bc5aba13bbbfd06d89b073abe732b860" in workflow
    assert workflow.index("sha256sum --check") < workflow.index("unzip")
    assert "secrets.BWS_ACCESS_TOKEN" in workflow
    for key in shared.REQUIRED:
        assert "secrets." + key not in workflow
    assert "GITHUB_ENV" not in workflow
    export = workflow.split("- name: Export Supabase to Parquet", 1)[1]
    script = export.split("run: |", 1)[1].split("- name:", 1)[0]
    assert "${{ inputs." not in script
    assert 'ARGS+=(--snapshot-date "$BACKUP_SNAPSHOT_DATE")' in script
    assert 'python -m scripts.run_supabase_backup_bws "${ARGS[@]}"' in script
    assert 'state_opt_out = "true"' in (shared.ROOT / "scripts/bws_ci.toml").read_text()
