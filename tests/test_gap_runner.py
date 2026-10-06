import json
from pathlib import Path
import re
import shlex
import shutil
from types import SimpleNamespace

import pytest
import yaml

from sew.cli import build_parser, main
from sew.gap.calibrate import calibrate
from sew.gap.runner import run
from sew.runner import RunnerError
from test_bakeoff_report import PRICES


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "module"
    golden = Path(__file__).parent / "fixtures/gap"
    shutil.copytree(golden, root / "catalogs/gap")
    tasks = json.loads((golden / "tasks.json").read_text())[:1]
    (root / "catalogs/gap/tasks.yaml").write_text(
        yaml.safe_dump(
            dict(schema_version=1, catalog_id="gap-test", authoring_policy="synthetic", tasks=tasks)
        )
    )
    configs = []
    statuses = []
    usage = dict(
        input=100,
        cached_input=200,
        output=30,
        reasoning=10,
        total_billable=340,
        accounting_source="measured",
    )

    def execute(config, output):
        configs.append(config)
        directory = output / config.run_id_override
        write(
            directory / "run.json",
            dict(
                status=statuses.pop(0) if statuses else "succeeded",
                metrics_ref="metrics/metrics.json",
            ),
        )
        write(directory / "metrics/metrics.json", dict(token_usage=usage))
        write(
            directory / "artifacts/spawn-metadata.json",
            dict(
                model_id=config.model_id,
                arm_audit=dict(contaminated=False),
                process=dict(provider_calls=0),
            ),
        )
        return SimpleNamespace(bundle_dir=directory)

    def grader(task, directory, *args):
        return dict(outcome="pass" if "-ceiling-" in directory.name else "fail")

    calibration, _ = calibrate(
        harness="codex",
        model="claude-sonnet-5",
        root=root,
        state_root=tmp_path / "state",
        execute=execute,
        grader=grader,
    )
    configs.clear()
    kwargs = dict(
        harness="codex",
        model="claude-sonnet-5",
        root=root,
        state_root=tmp_path / "state",
        run_root=tmp_path / "battery",
        arms=["floor", "ceiling", "native"],
        reps=2,
        execute=execute,
        grader=grader,
        price_table=PRICES,
    )
    return kwargs, configs, statuses, calibration, usage


def test_run_and_resume_after_interruption(setup):
    kwargs, configs, _, _, _ = setup
    original = kwargs["execute"]

    def interrupted(config, output):
        if len(configs) == 2:
            raise KeyboardInterrupt
        return original(config, output)

    with pytest.raises(KeyboardInterrupt):
        run(**(kwargs | dict(execute=interrupted)))
    state, path = run(**kwargs, resume=True)
    assert len(configs) == 6
    assert len(state["entries"]) == 6
    assert configs[2].run_id_override.endswith("att2")
    assert len(json.loads((path / "run-index.json").read_text())) == 6
    assert all(
        (Path(v["run_dir"]) / "evaluations/gap-outcome.json").is_file()
        for v in state["entries"].values()
    )
    run(**kwargs, resume=True)
    assert len(configs) == 6
    with pytest.raises(RunnerError, match="use --resume"):
        run(**kwargs)


def test_streak_stops_and_resume_retries_unavailable(setup):
    kwargs, configs, statuses, _, _ = setup
    statuses[:] = ["provider_unavailable"] * 3
    state, _ = run(**kwargs)
    assert state["stopped_reason"] == "provider_unavailable_streak"
    assert len(configs) == 3
    assert not any(
        (Path(v["run_dir"]) / "evaluations/gap-outcome.json").exists()
        for v in state["entries"].values()
    )
    state, _ = run(**kwargs, resume=True)
    assert state["stopped_reason"] is None and len(configs) == 9
    assert all(v["status"] == "succeeded" for v in state["entries"].values())
    assert all(configs[i].run_id_override.endswith("att2") for i in range(3, 6))


def test_nonconsecutive_outages_do_not_stop(setup):
    kwargs, configs, statuses, _, _ = setup
    statuses[:] = ["provider_unavailable", "succeeded"] * 3
    state, _ = run(**kwargs)
    assert state["stopped_reason"] is None and len(configs) == 6


@pytest.mark.parametrize("budget", ["tokens", "calls", "wall"])
def test_budget_limits_are_passed_and_enforced(setup, budget, monkeypatch):
    kwargs, configs, _, _, usage = setup
    original = kwargs["execute"]
    if budget == "tokens":
        usage.update(input=10001, total_billable=10241)
    if budget == "wall":
        ticks = iter(range(0, 10000, 121))
        monkeypatch.setattr("sew.gap.runner.time.monotonic", lambda: next(ticks))

    def execute(config, output):
        result = original(config, output)
        if budget == "calls":
            write(
                result.bundle_dir / "artifacts/spawn-metadata.json",
                dict(
                    model_id=config.model_id,
                    arm_audit=dict(contaminated=False),
                    process=dict(provider_calls=6),
                ),
            )
        return result

    state, _ = run(
        **(kwargs | dict(execute=execute, grader=lambda *a: pytest.fail("budget cannot be graded")))
    )
    assert all(
        c.max_total_tokens == 10000 and c.max_provider_calls == 5 and c.timeout_seconds == 120
        for c in configs
    )
    assert all(v["status"] == "budget_exhausted" for v in state["entries"].values())
    run(**kwargs, resume=True)
    assert len(configs) == 6


@pytest.mark.parametrize(
    "mutation", ["rejected", "retired", "model", "harness", "catalog", "entry_hash", "missing"]
)
def test_admission_refusal_before_any_spawn(setup, mutation):
    kwargs, configs, _, record, _ = setup
    path = kwargs["state_root"] / "gap/calibration/codex@claude-sonnet-5.json"
    if mutation in {"rejected", "retired"}:
        record["tasks"][0]["verdict"] = mutation
    elif mutation in {"model", "harness"}:
        record[mutation] = "wrong"
    elif mutation == "catalog":
        record["catalog_hash"] = "wrong"
    elif mutation == "entry_hash":
        record["tasks"][0]["catalog_hash"] = "wrong"
    if mutation == "missing":
        path.unlink()
    else:
        write(path, record)
    with pytest.raises(RunnerError):
        run(**kwargs)
    assert not configs and not kwargs["run_root"].exists()


def test_projection_and_no_execution_or_state(setup):
    kwargs, configs, _, _, _ = setup
    result, path = run(**kwargs, dry_run=True)
    assert result["cells"] == 6 and result["projected_tokens"] == 2040
    assert result["projected_model_usd"] == pytest.approx(6 * 0.00064)
    assert result["pricing_lower_bound"]
    assert path is None and not configs and not kwargs["run_root"].exists()


def test_projection_uses_arm_means_and_retry_spend(setup):
    kwargs, _, _, calibration, _ = setup
    entry = calibration["tasks"][0]
    for c in entry["ceiling"]["cells"]:
        write(
            Path(c["run_dir"]) / "metrics/metrics.json",
            dict(
                token_usage=dict(
                    input=200,
                    cached_input=400,
                    output=60,
                    reasoning=20,
                    total_billable=680,
                    accounting_source="measured",
                )
            ),
        )
    # Count a prior retry as part of one reference cell's cost.
    cell = entry["floor"]["cells"][0]
    cell["attempt_run_dirs"].append(cell["run_dir"])
    write(kwargs["state_root"] / "gap/calibration/codex@claude-sonnet-5.json", calibration)
    projection, _ = run(**kwargs, dry_run=True)
    assert [r["projected_tokens"] for r in projection["rows"]] == [816, 1360, 1088]


@pytest.mark.parametrize("mutation", ["missing", "unknown", "price"])
def test_projection_missing_data_is_never_zero(setup, mutation):
    kwargs, configs, _, calibration, _ = setup
    if mutation == "price":
        from sew.cost_model import parse_price_table

        kwargs["price_table"] = parse_price_table(dict(version=1, vendors={}, models={}))
    else:
        path = (
            Path(calibration["tasks"][0]["floor"]["cells"][0]["run_dir"]) / "metrics/metrics.json"
        )
        if mutation == "missing":
            path.unlink()
        else:
            write(path, dict(token_usage=dict(accounting_source="unknown")))
    result, _ = run(**kwargs, dry_run=True)
    assert result["projected_model_usd"] is None
    if mutation != "price":
        assert result["projected_tokens"] is None
    assert not configs


def test_resume_matrix_drift_and_tree_refused(setup, tmp_path):
    kwargs, configs, _, _, _ = setup
    run(**kwargs)
    with pytest.raises(RunnerError, match="changed"):
        run(**(kwargs | dict(reps=3)), resume=True)
    assert len(configs) == 6
    forbidden = tmp_path / "forbidden"
    forbidden.mkdir()
    (forbidden / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(ValueError, match="tracked"):
        run(**(kwargs | dict(run_root=forbidden / "other")))


def test_contamination_and_wrong_identity_never_graded(setup):
    kwargs, configs, _, _, _ = setup
    original = kwargs["execute"]

    def execute(config, output):
        result = original(config, output)
        write(
            result.bundle_dir / "artifacts/spawn-metadata.json",
            dict(model_id=config.model_id, arm_audit=dict(contaminated=True)),
        )
        return result

    state, _ = run(
        **(kwargs | dict(execute=execute, grader=lambda *a: pytest.fail("contaminated")))
    )
    assert all(v["status"] == "contaminated" for v in state["entries"].values())


def test_cli_dry_run_skips_provider_key_config(monkeypatch, capsys):
    monkeypatch.setattr("sew.cli.load_provider_exposures", lambda *a: pytest.fail("keys read"))
    monkeypatch.setattr(
        "sew.gap.runner.run",
        lambda **k: (dict(cells=9, projected_tokens=100, projected_model_usd=0.2), None),
    )
    assert (
        main(
            [
                "gap",
                "run",
                "--harness",
                "codex",
                "--model",
                "m",
                "--arm",
                "brave",
                "--provider-mcp-config",
                "/missing",
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["cells"] == 9


def test_every_runbook_command_parses_and_activation_matches():
    module = Path(__file__).resolve().parents[1]
    doc = (module / "RUNBOOK-gap.md").read_text()
    commands = [line for line in doc.splitlines() if line.startswith("bin/hq-sew ")]
    assert len(commands) >= 9
    parser = build_parser()
    for command in commands:
        argv = shlex.split(command)[1:]
        if argv == ["gap", "--help"]:
            with pytest.raises(SystemExit) as exc:
                parser.parse_args(argv)
            assert exc.value.code == 0
        else:
            assert callable(parser.parse_args(argv).func)
    import subprocess

    result = subprocess.run(
        [str(module / "bin/hq-sew"), "gap", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    plan_path = module.parents[1] / "projects/search-gap-bench/plan.json"
    if not plan_path.is_file():
        return  # The wrapper/parser proof above also runs after extraction.
    plan = json.loads(plan_path.read_text())
    assertion = next(a for a in plan["activationAssertions"] if a["id"] == "gap-cli-active")
    assert assertion["command"].endswith('/bin/hq-sew" gap --help')
    assert re.search(assertion["expected_match"], result.stdout)
    assert not re.search(assertion["expected_match"], "{calibrate,report} run the references")


def test_deferral_cap_prevents_resume_starvation(setup):
    kwargs, configs, statuses, _, _ = setup
    for _ in range(2):
        statuses[:] = ["provider_unavailable"] * 3
        state, _ = run(**kwargs, resume=bool(configs))
        assert state["stopped_reason"] == "provider_unavailable_streak"
    statuses[:] = ["provider_unavailable"] * 6
    state, _ = run(**kwargs, resume=True)
    assert len(configs) == 12 and len(state["entries"]) == 6
    assert len(state["deferred"]) == 3
    statuses.clear()
    state, _ = run(**kwargs, resume=True)
    assert len(configs) == 15 and not state["deferred"]
    assert sum(e["status"] == "provider_unavailable" for e in state["entries"].values()) == 3


def test_streak_survives_interruption(setup):
    kwargs, configs, statuses, _, _ = setup
    original = kwargs["execute"]
    statuses[:] = ["provider_unavailable"] * 3

    def interrupt(config, output):
        if len(configs) == 2:
            raise KeyboardInterrupt
        return original(config, output)

    with pytest.raises(KeyboardInterrupt):
        run(**(kwargs | dict(execute=interrupt)))
    state, _ = run(**kwargs, resume=True)
    assert len(configs) == 3 and len(state["deferred"]) == 3
    assert state["stopped_reason"] == "provider_unavailable_streak"


def test_verifier_error_remains_resumable(setup):
    kwargs, configs, _, _, _ = setup
    with pytest.raises(RunnerError, match="verifier"):
        run(**(kwargs | dict(grader=lambda *a: dict(outcome="fail", errors=["setup"]))))
    state, _ = run(**kwargs, resume=True)
    assert len(configs) == 7 and len(state["entries"]) == 6


def test_concurrent_runner_refused(setup):
    import fcntl

    kwargs, configs, _, _, _ = setup
    kwargs["run_root"].mkdir()
    with (kwargs["run_root"] / ".runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RunnerError, match="active runner"):
            run(**kwargs)
    assert not configs


@pytest.mark.parametrize(
    "options", [dict(reps=0), dict(arms=[]), dict(arms=["unknown"]), dict(task_ids=["unknown"])]
)
def test_invalid_matrix_refused(setup, options):
    kwargs, configs, _, _, _ = setup
    with pytest.raises(RunnerError):
        run(**(kwargs | options))
    assert not configs


def test_provider_arm_requires_exposure(setup):
    kwargs, configs, _, _, _ = setup
    with pytest.raises(RunnerError, match="exposures"):
        run(**(kwargs | dict(arms=["brave"])))
    assert not configs


def test_provider_exposure_is_wired_without_native_search(setup):
    from sew.harness import fixture_provider_exposure

    kwargs, configs, _, _, _ = setup
    exposure = fixture_provider_exposure("brave")
    state, _ = run(**(kwargs | dict(arms=["brave"], provider_exposures={"brave": exposure})))
    assert len(state["entries"]) == 2
    assert all(c.external_provider == exposure and not c.native_search_available for c in configs)


def test_identity_refusal(setup):
    kwargs, configs, _, _, _ = setup
    original = kwargs["execute"]

    def execute(config, output):
        result = original(config, output)
        write(
            result.bundle_dir / "artifacts/spawn-metadata.json",
            dict(model_id="wrong", arm_audit=dict(contaminated=False)),
        )
        return result

    with pytest.raises(RunnerError, match="identity"):
        run(**(kwargs | dict(execute=execute)))
    assert len(configs) == 1


def test_runner_bundles_feed_paired_report(setup):
    from sew.gap.report import build_gap_report
    from test_bakeoff_report import _bundle, _usage

    kwargs, _, _, _, _ = setup

    def execute(config, output):
        _bundle(
            output.parent,
            config.run_id_override,
            config.harness_id,
            config.provider_id,
            config.task_id,
            "succeeded",
            "pass",
            _usage(100, 200, 30, 10),
            model_id=config.model_id,
        )
        return SimpleNamespace(bundle_dir=output / config.run_id_override)

    state, path = run(**(kwargs | dict(execute=execute)))
    report = build_gap_report(path, price_table=PRICES)
    assert report["calibration"] == state["calibration"]
    assert len(report["cells"]) == 6
    assert {c["arm"] for c in report["headline"]} == {"floor", "ceiling", "native"}
    assert next(c for c in report["headline"] if c["arm"] == "ceiling")["successes_n"] == 2
    assert next(c for c in report["headline"] if c["arm"] == "native")["pass_rate"] == 0


def test_cancelled_cell_stops_and_resumes_with_fresh_bundle(setup):
    kwargs, configs, statuses, _, _ = setup
    statuses[:] = ["cancelled"]
    state, _ = run(**kwargs)
    assert state["stopped_reason"] == "cancelled" and len(configs) == 1
    state, _ = run(**kwargs, resume=True)
    assert state["stopped_reason"] is None and len(state["entries"]) == 6
    assert configs[1].run_id_override.endswith("att2")


def test_resume_regenerates_report_index_from_authoritative_state(setup):
    kwargs, configs, _, _, _ = setup
    state, path = run(**kwargs)
    write(path / "run-index.json", [])
    resumed, _ = run(**kwargs, resume=True)
    assert resumed == state and len(configs) == 6
    assert sorted(
        json.loads((path / "run-index.json").read_text()), key=lambda e: e["run_id"]
    ) == sorted(state["entries"].values(), key=lambda e: e["run_id"])


def test_missing_discovery_is_unavailable_and_resume_reruns_only_unavailable(setup):
    from sew.harness import fixture_provider_exposure
    from sew.mcp_meter import CallMeter

    kwargs, configs, _, _, _ = setup
    original = kwargs["execute"]
    ready = False
    graded = []

    def execute(config, output):
        result = original(config, output)
        if config.provider_id == "brave":
            CallMeter(
                provider_id="brave",
                run_id=config.run_id_override,
                call_dir=result.bundle_dir / "provider-calls",
            )
        if config.provider_id == "brave" and (ready or "-2-" in config.run_id_override):
            meter = CallMeter(
                provider_id="brave",
                run_id=config.run_id_override,
                call_dir=result.bundle_dir / "provider-calls",
            )
            for id, method, reply in [
                (1, "initialize", {"protocolVersion": "2025-06-18"}),
                (2, "tools/list", {"tools": [{"name": "search"}]}),
            ]:
                meter.client_message({"id": id, "method": method})
                meter.server_message({"id": id, "result": reply})
        return result

    def grader(task, directory, *args):
        graded.append(directory.name)
        return {"outcome": "fail"}

    kwargs.update(
        arms=["brave", "native"],
        execute=execute,
        grader=grader,
        provider_exposures={"brave": fixture_provider_exposure("brave")},
    )
    state, _ = run(**kwargs)
    (missing,) = [e for e in state["entries"].values() if e["status"] == "provider_unavailable"]
    persisted = json.loads((Path(missing["run_dir"]) / "run.json").read_text())
    assert persisted["status"] == "provider_unavailable"
    assert persisted["failure_category"] == "provider_tools_unavailable"
    assert len(graded) == 3  # The discovered provider with zero calls is graded.
    assert not (Path(missing["run_dir"]) / "evaluations/gap-outcome.json").exists()
    run(**kwargs, resume=True)
    assert len(configs) == 4
    ready = True
    repaired, _ = run(**kwargs, resume=True, rerun_unavailable=True)
    assert len(configs) == 5 and len(graded) == 4
    assert configs[-1].run_id_override == missing["run_id"].replace("att1", "att2")
    assert all(e["status"] == "succeeded" for e in repaired["entries"].values())
    repaired_cell = next(
        e for e in repaired["entries"].values() if e["run_id"] == configs[-1].run_id_override
    )
    assert len(repaired_cell["attempt_run_dirs"]) == 2
    assert Path(missing["run_dir"]).exists()


def test_rerun_unavailable_requires_resume(setup):
    kwargs, configs, _, _, _ = setup
    with pytest.raises(RunnerError, match="requires --resume"):
        run(**kwargs, rerun_unavailable=True)
    assert not configs


def test_unobservable_provider_is_graded(setup):
    from sew.harness import fixture_provider_exposure
    from sew.mcp_meter import availability_record, write_availability

    kwargs, _, _, _, _ = setup
    original = kwargs["execute"]
    graded = []

    def execute(config, output):
        result = original(config, output)
        write_availability(
            result.bundle_dir / "provider-calls",
            availability_record(config.provider_id, config.run_id_override),
        )
        write(
            result.bundle_dir / "artifacts/transcript.json",
            [
                {
                    "harness_event": {
                        "type": "item.completed",
                        "item": {
                            "id": "c",
                            "type": "mcp_tool_call",
                            "server": "brave",
                            "tool": "search",
                            "status": "completed",
                        },
                    }
                }
            ],
        )
        return result

    def grader(task, directory, *args):
        graded.append(directory.name)
        return {"outcome": "pass"}

    kwargs.update(
        arms=["brave"],
        execute=execute,
        grader=grader,
        provider_exposures={"brave": fixture_provider_exposure("brave")},
    )
    state, _ = run(**kwargs)
    assert len(graded) == 2
    assert all(e["status"] != "provider_unavailable" for e in state["entries"].values())


@pytest.mark.parametrize(
    "status,category,ready,verdict,expected",
    [
        ("harness_boot_failed", "harness_exited_before_ready", False, None, "harness_boot_failed"),
        ("harness_boot_failed", "harness_no_first_output", False, None, "harness_boot_failed"),
        ("timeout", "timeout", False, None, "timeout"),
        ("failed", "harness_reported_error", True, None, "failed"),
        ("harness_boot_failed", "harness_auth_failed", True, None, "harness_boot_failed"),
        ("failed", "transcript_over_artifact_cap", True, "available", "failed"),
        ("failed", "harness_reported_error", True, "unknown", "failed"),
    ],
)
def test_engaged_unlaunched_wrapper_gap_gate(setup, status, category, ready, verdict, expected):
    from sew.harness import fixture_provider_exposure
    from sew.mcp_meter import availability_record, provider_available, write_availability

    kwargs, _, _, _, _ = setup
    original = kwargs["execute"]

    def execute(config, output):
        result = original(config, output)
        write(
            result.bundle_dir / "run.json",
            {
                "run_id": config.run_id_override,
                "provider_id": "brave",
                "status": status,
                "failure_category": category,
                "metrics_ref": "metrics/metrics.json",
            },
        )
        record = availability_record("brave", config.run_id_override)
        record.update(wrapper_engaged=True, observation_reason="server_not_launched")
        write_availability(result.bundle_dir / "provider-calls", record)
        write(
            result.bundle_dir / "artifacts/transcript.json",
            [{"event": "harness_ready"}] if ready else [],
        )
        if verdict is not None:
            path = result.bundle_dir / "run.json"
            run_record = json.loads(path.read_text())
            run_record["provider_availability"] = verdict
            write(path, run_record)
        return result

    kwargs.update(
        arms=["brave"],
        execute=execute,
        provider_exposures={"brave": fixture_provider_exposure("brave")},
    )
    state, _ = run(**kwargs)
    assert len(state["entries"]) == 2
    for entry in state["entries"].values():
        assert entry["status"] == expected
        directory = Path(entry["run_dir"])
        record = json.loads((directory / "run.json").read_text())
        assert record["failure_category"] == (
            "provider_tools_unavailable" if expected == "provider_unavailable" else category
        )
        if expected != "provider_unavailable":
            assert provider_available(directory, "brave", entry["run_id"]) is (
                True if verdict == "available" else None
            )
