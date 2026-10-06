"""CLI wiring for native-only live suite auth selection."""

from sew import cli


def test_native_only_live_run_forwards_auth_without_provider_config(monkeypatch, capsys) -> None:
    seen = {}

    class Executor:
        def __init__(self, *, provider_exposures, harness_auth):
            seen["provider_exposures"] = provider_exposures
            seen["harness_auth"] = harness_auth

    class Runner:
        def __init__(self, *, state_root, live_executor):
            seen["live_executor"] = live_executor

        def dry_run(self, suite, *, repetitions, seed, mode):
            seen["mode"] = mode
            return {"suite": suite, "mode": mode}

    monkeypatch.setattr(cli, "LiveCellExecutor", Executor)
    monkeypatch.setattr(cli, "SuiteRunner", Runner)
    assert cli.main(["run", "--mode", "live", "--dry-run", "--harness-auth", "account"]) == 0
    assert seen["provider_exposures"] is None
    assert seen["harness_auth"] == "account"
    assert seen["mode"] == "live"
    assert "harness auth source: account" in capsys.readouterr().err
