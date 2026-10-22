import json

import pytest

from sew.cli import main
from sew.gap.backfill import backfill
from sew.gap.calibrate import CalibrationError, calibrate, catalog_digest
from test_gap_calibrate import setup as calibration_setup  # noqa: F401


@pytest.fixture
def legacy(calibration_setup, monkeypatch):  # noqa: F811 - pytest fixture registration
    kwargs, calls = calibration_setup
    directory = kwargs["state_root"] / "gap/calibration-runs/synthetic-legacy"
    directory.mkdir(parents=True)
    monkeypatch.setattr("sew.gap.backfill.module_root", lambda: kwargs["root"])
    monkeypatch.setattr(
        "sew.gap.backfill.default_state_root", lambda: kwargs["state_root"]
    )
    options = {
        key: kwargs[key] for key in ("harness", "model", "state_root", "root")
    } | {"catalog_hash": catalog_digest(kwargs["root"])}
    return kwargs, calls, directory, options


def bundle(
    directory,
    name="golden-api-break-floor-1",
    *,
    run=None,
    metadata=None,
    outcome="fail",
):
    attempt = directory / name
    (attempt / "artifacts").mkdir(parents=True)
    (attempt / "run.json").write_text(
        json.dumps(
            {
                "harness_id": "codex",
                "task_id": "golden-api-break",
                "provider_id": "floor",
                "status": "succeeded",
                **(run or {}),
            }
        )
    )
    (attempt / "artifacts/spawn-metadata.json").write_text(
        json.dumps(
            {
                "model_id": "gpt-test",
                "arm_audit": {"contaminated": False},
                **(metadata or {}),
            }
        )
    )
    if outcome is not None:
        (attempt / "evaluations").mkdir()
        (attempt / "evaluations/calibration-outcome.json").write_text(
            json.dumps({"outcome": outcome})
        )
    return attempt


def command(options, run_id="synthetic-legacy", *extra):
    return [
        "gap",
        "calibrate-backfill",
        run_id,
        "--harness",
        options["harness"],
        "--model",
        options["model"],
        "--catalog-hash",
        options["catalog_hash"],
        *extra,
    ]


def checkpoints(directory):
    return [
        json.loads(p.read_text()) for p in sorted(directory.glob("*.calibration.json"))
    ]


@pytest.mark.parametrize("pending", [None, "not_applicable"])
def test_backfill_resumes_and_regrades_without_execution(
    legacy, pending, monkeypatch, capsys
):
    kwargs, calls, directory, options = legacy
    floor = bundle(directory, outcome=pending)
    bundle(
        directory,
        "golden-api-break-ceiling-1",
        run={"provider_id": "ceiling"},
        outcome="pass",
    )
    writes = []
    from sew.runner import atomic_write_json

    def write(path, record):
        writes.append(path)
        atomic_write_json(path, record)

    monkeypatch.setattr("sew.gap.backfill.atomic_write_json", write)
    assert main(command(options)) == 0
    assert "Checkpoints written: 2" in capsys.readouterr().out
    assert len(writes) == 2
    assert "outcome" not in next(
        c["cell"] for c in checkpoints(directory) if c["config"]["arm"] == "floor"
    )
    grades = []
    original = kwargs["grader"]

    def grade(task, run_dir, *args):
        grades.append(run_dir)
        return original(task, run_dir, *args)

    record, path = calibrate(
        **(kwargs | {"grader": grade}), reps=1, resume=directory.name
    )
    assert len(calls) == 0
    assert grades == [floor]
    assert record["tasks"][0]["verdict"] == "admitted"
    cells = [
        cell
        for arm in ("floor", "ceiling")
        for cell in record["tasks"][0][arm]["cells"]
    ]
    assert all(c["reused"] and c["backfilled"] for c in cells)
    assert all(c["catalog_hash"] == options["catalog_hash"] for c in cells)
    assert json.loads(path.read_text())["tasks"] == record["tasks"]


def test_fully_graded_backfill_reuses_all_cells(legacy):
    kwargs, calls, directory, options = legacy
    bundle(directory)
    bundle(
        directory,
        "golden-api-break-ceiling-1",
        run={"provider_id": "ceiling"},
        outcome="pass",
    )
    assert backfill(directory.name, **options)["checkpoints_written"] == 2
    assert {c["cell"]["outcome"] for c in checkpoints(directory)} == {"pass", "fail"}

    def no_grading(*args):
        pytest.fail("graded cells must not be regraded")

    record, _ = calibrate(
        **(kwargs | {"grader": no_grading}), reps=1, resume=directory.name
    )
    assert len(calls) == 0
    assert all(
        record["tasks"][0][arm]["cells"][0]["backfilled"]
        for arm in ("floor", "ceiling")
    )


def test_all_retry_directories_and_contamination_retained(legacy):
    _, _, directory, options = legacy
    first = bundle(directory, run={"status": "contaminated"})
    failed = bundle(
        directory, "golden-api-break-floor-1-att2", run={"status": "failed"}
    )
    good = bundle(
        directory,
        "golden-api-break-floor-1-att10",
        metadata={
            "arm_audit": {
                "contaminated": False,
                "denied_network_attempts": 3,
                "config_neutralized_network_attempts": 2,
            }
        },
    )
    dirty = bundle(
        directory,
        "golden-api-break-floor-1-att11",
        metadata={"arm_audit": {"contaminated": True}},
    )
    assert backfill(directory.name, **options)["checkpoints_written"] == 1
    (checkpoint,) = checkpoints(directory)
    assert checkpoint["config"] == {
        "harness": "codex",
        "model": "gpt-test",
        "task_id": "golden-api-break",
        "arm": "floor",
        "rep": 1,
        "catalog_hash": options["catalog_hash"],
    }
    cell = checkpoint["cell"]
    assert cell["run_dir"] == str(good)
    assert cell["attempt_run_dirs"] == list(map(str, [first, failed, good, dirty]))
    assert cell["excluded_contaminated_run_dirs"] == list(map(str, [first, dirty]))
    assert cell["attempts"] == 4
    assert cell["denied_network_attempts"] == 3
    assert cell["config_neutralized_network_attempts"] == 2


@pytest.mark.parametrize("value", [None, 0, 3])
def test_backfill_normalizes_null_audit_counters(legacy, value):
    _, _, directory, options = legacy
    bundle(
        directory,
        metadata={
            "arm_audit": {
                "contaminated": False,
                "denied_network_attempts": value,
                "config_neutralized_network_attempts": value,
            }
        },
    )
    assert backfill(directory.name, **options)["checkpoints_written"] == 1
    (checkpoint,) = checkpoints(directory)
    for key in ("denied_network_attempts", "config_neutralized_network_attempts"):
        assert checkpoint["cell"][key] == (0 if value is None else value)
        assert type(checkpoint["cell"][key]) is int


@pytest.mark.parametrize(
    "artifact",
    ["run.json", "artifacts/spawn-metadata.json", "evaluations/calibration-outcome.json"],
)
@pytest.mark.parametrize("content", [b'{"interrupted":', b'\xff'])
def test_damaged_artifact_does_not_abort_backfill(legacy, artifact, content):
    _, calls, directory, options = legacy
    attempt = bundle(directory)
    (attempt / artifact).write_bytes(content)
    bundle(
        directory,
        "golden-api-break-ceiling-1",
        run={"provider_id": "ceiling"},
        outcome="pass",
    )
    result = backfill(directory.name, **options)
    cells = {c["config"]["arm"]: c["cell"] for c in checkpoints(directory)}
    assert cells["ceiling"]["outcome"] == "pass"
    if artifact.startswith("evaluations/"):
        assert result["checkpoints_written"] == 2
        assert "outcome" not in cells["floor"]
        assert result["skipped"] == []
    else:
        assert result["checkpoints_written"] == 1
        assert "floor" not in cells
        assert result["skipped"][0]["cell"] == "golden-api-break-floor-1"
    assert not calls


@pytest.mark.parametrize(
    "run,metadata,name,reason",
    [
        ({"harness_id": "claude-code"}, {}, None, "harness_id mismatch"),
        ({"task_id": "other-task"}, {}, None, "task_id mismatch"),
        ({"provider_id": "ceiling"}, {}, None, "provider_id mismatch"),
        ({}, {"model_id": "other-model"}, None, "model_id mismatch"),
        ({}, {"arm_audit": {"contaminated": True}}, None, "contaminated"),
        ({"status": "contaminated"}, {}, None, "contaminated"),
        ({"status": "failed"}, {}, None, "not succeeded"),
        ({"status": "running"}, {}, None, "not succeeded"),
        ({}, {"arm_audit": {"contaminated": 0}}, None, "exactly false"),
        ({}, {"arm_audit": {"contaminated": "false"}}, None, "exactly false"),
        ({}, {"arm_audit": None}, None, "exactly false"),
        ({"task_id": "unknown-task"}, {}, "unknown-task-floor-1", "unknown task id"),
        ({}, {}, "invalid-name", "invalid cell directory name"),
    ],
)
def test_skipped_cells_print_reason(legacy, capsys, run, metadata, name, reason):
    _, calls, directory, options = legacy
    bundle(directory, name or "golden-api-break-floor-1", run=run, metadata=metadata)
    assert main(command(options)) == 0
    output = capsys.readouterr().out
    assert "Checkpoints written: 0" in output and "Cells skipped: 1" in output
    assert reason in output
    assert not checkpoints(directory) and not calls


def test_wrong_hash_refused_before_writing(legacy, capsys):
    _, calls, directory, options = legacy
    bundle(directory)
    options["catalog_hash"] = "0" * 64
    assert main(command(options)) == 2
    assert "--catalog-hash" in capsys.readouterr().err
    assert not checkpoints(directory) and not calls


@pytest.mark.parametrize("dry_run", [False, True])
def test_existing_checkpoint_refuses_whole_run_without_overwrite(
    legacy, capsys, dry_run
):
    _, _, directory, options = legacy
    bundle(directory)
    existing = directory / "other.calibration.json"
    existing.write_text("synthetic existing checkpoint")
    assert (
        main(command(options, directory.name, *(["--dry-run"] if dry_run else []))) == 2
    )
    assert "already has a calibration checkpoint" in capsys.readouterr().err
    assert existing.read_text() == "synthetic existing checkpoint"
    assert list(directory.glob("*.calibration.json")) == [existing]


def test_dry_run_has_same_plan_without_writes(legacy, capsys):
    _, _, directory, options = legacy
    bundle(directory)
    bundle(directory, "unknown-task-floor-1", run={"task_id": "unknown-task"})
    before = sorted(directory.rglob("*"))
    assert main(command(options, directory.name, "--dry-run")) == 0
    output = capsys.readouterr().out
    assert "Checkpoints would write (dry-run): 1" in output
    assert "unknown-task-floor-1: unknown task id" in output
    assert sorted(directory.rglob("*")) == before
    assert not checkpoints(directory)
    preview = backfill(directory.name, **options, dry_run=True)
    actual = backfill(directory.name, **options)
    assert preview | {"dry_run": False} == actual


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "missing", ".."])
def test_invalid_run_id_refused(legacy, run_id):
    _, _, directory, options = legacy
    with pytest.raises(CalibrationError, match="RUN_ID|run not found"):
        backfill(run_id, **options)
    assert not checkpoints(directory)


def test_checkpoint_provenance_survives_second_resume(legacy):
    kwargs, calls, directory, options = legacy
    bundle(directory)
    bundle(
        directory,
        "golden-api-break-ceiling-1",
        run={"provider_id": "ceiling"},
        outcome="pass",
    )
    backfill(directory.name, **options)
    first, _ = calibrate(**kwargs, reps=1, resume=directory.name)
    second, _ = calibrate(**kwargs, reps=1, resume=first["run_id"])
    assert len(calls) == 0
    assert all(
        second["tasks"][0][arm]["cells"][0]["backfilled"]
        for arm in ("floor", "ceiling")
    )
