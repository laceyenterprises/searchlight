import json
from pathlib import Path
import pytest
from sew.cli import main
from sew.gap.calibrate import CalibrationError, calibrate, recalibrate
from sew.gap.refresh import refresh
from test_gap_calibrate import setup as calibration_setup


@pytest.fixture
def setup(tmp_path):
    return calibration_setup.__wrapped__(tmp_path)


@pytest.mark.parametrize("passes", range(6))
def test_retirement_edges(setup, passes):
    kwargs, configs = setup
    old, old_path = calibrate(**kwargs)
    configs.clear()
    count = 0

    def grade(*args):
        nonlocal count
        count += 1
        return {"outcome": "pass" if count <= passes else "fail"}

    kwargs.update(model="gpt-new", grader=grade)
    record, path = recalibrate(**kwargs, previous_model="gpt-test")
    assert len(configs) == 5 and all(c.provider_id == "floor" for c in configs)
    task = record["tasks"][0]
    assert task["verdict"] == ("retired" if passes > 1 else "admitted")
    assert task["floor"]["rate"] == passes / 5
    assert task["ceiling"] == old["tasks"][0]["ceiling"]
    assert task["previous_evidence"] == old["tasks"][0]
    if passes > 1:
        assert task["retired_at"] == task["date"]
        assert "exceeds 0.2" in task["reason"] and "gpt-test -> gpt-new" in task["reason"]
    assert json.loads(old_path.read_text()) == old
    assert json.loads(path.read_text()) == record


def test_preserve_retired_without_spawn(setup):
    kwargs, configs = setup
    old, path = calibrate(**kwargs)
    old["tasks"][0].update(verdict="retired", reason="decayed", retired_at="2026-10-01")
    path.write_text(json.dumps(old))
    configs.clear()
    kwargs["model"] = "gpt-new"
    record, _ = recalibrate(**kwargs, previous_model="gpt-test")
    assert not configs and record["tasks"] == old["tasks"]


@pytest.mark.parametrize("mutation", ["same-model", "catalog", "contamination", "grading"])
def test_refuse_bad_evidence(setup, mutation):
    kwargs, configs = setup
    calibrate(**kwargs)
    kwargs["model"] = "gpt-new"
    if mutation == "same-model":
        kwargs["model"] = "gpt-test"
    elif mutation == "catalog":
        path = kwargs["root"] / "catalogs/gap/tasks.yaml"
        path.write_text(path.read_text() + "\n")
    elif mutation == "grading":
        kwargs["grader"] = lambda *a: {"outcome": "fail", "errors": ["infra"]}
    else:
        execute = kwargs["execute"]

        def contaminated(config, root):
            result = execute(config, root)
            (result.bundle_dir / "artifacts/spawn-metadata.json").write_text(
                json.dumps({"model_id": config.model_id, "arm_audit": {"contaminated": True}})
            )
            return result

        kwargs["execute"] = contaminated
    with pytest.raises(CalibrationError):
        recalibrate(**kwargs, previous_model="gpt-test")
    assert not (kwargs["state_root"] / "gap/calibration/codex@gpt-new.json").exists()


def test_refresh_output(tmp_path):
    candidates = [
        dict(
            id=f"gap-pypi-example-{i}",
            markers=markers,
            oracle=dict(source_url="https://example.org/release"),
            provenance=dict(event_date="2026-09-01"),
        )
        for i, markers in enumerate((["security"], ["default-change"], ["renamed"]))
    ]
    calls = []

    def miner(packages, *, since):
        calls.append((packages, since))
        return dict(candidates=candidates, skipped=[])

    record, path = refresh(
        since="2026-01-01", packages=["example"], state_root=tmp_path, miner=miner
    )
    assert calls == [(["example"], "2026-01-01")]
    assert json.loads(path.read_text()) == record
    assert record["dispatch_policy"] == "emit-only"
    assert len(record["tickets"]) == len(candidates)
    for ticket, candidate in zip(record["tickets"], candidates):
        assert ticket["id"] == candidate["id"]
        assert ticket["targetRepo"] == "agent-os" and ticket["targetBranch"] == "main"
        assert ticket["expectedCompletionShape"] == "pr" and ticket["workerClass"] == "codex"
        assert json.loads(Path(ticket["candidateReceipt"]).read_text()) == candidate
        prompt = Path(ticket["promptPath"]).read_text()
        assert prompt == ticket["scope"]
        assert "{candidate_id}" not in prompt and "{candidate_receipt}" not in prompt
        assert candidate["id"] in prompt and candidate["oracle"]["source_url"] in prompt
        assert "reference.diff" in prompt and "Refresh cutoff (exclusive): 2026-01-01" in prompt


def test_refresh_empty_and_guard(tmp_path):
    record, path = refresh(
        since="2026-01-01",
        state_root=tmp_path,
        miner=lambda *a, **k: {"candidates": [], "skipped": []},
    )
    assert record["tickets"] == [] and path.exists()
    (tmp_path / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(CalibrationError, match="tracked"):
        refresh(
            since="2026-01-01",
            state_root=tmp_path,
            miner=lambda *a, **k: pytest.fail("must not mine"),
        )


def test_cli_wiring(monkeypatch, setup, capsys):
    kwargs, _ = setup
    record, path = calibrate(**kwargs)
    calls = []

    def stub(**options):
        calls.append(options)
        return record, path

    monkeypatch.setattr("sew.gap.calibrate.recalibrate", stub)
    assert (
        main(
            [
                "gap",
                "recalibrate",
                "--harness",
                "codex",
                "--model",
                "new",
                "--previous-model",
                "old",
                "--reps",
                "3",
            ]
        )
        == 0
    )
    assert calls[0]["previous_model"] == "old" and calls[0]["reps"] == 3
    monkeypatch.setattr("sew.gap.refresh.refresh", lambda **k: ({"tickets": [1]}, path))
    assert main(["gap", "refresh", "--ecosystem", "pypi", "--since", "2026-01-01"]) == 0
    assert '"tickets": 1' in capsys.readouterr().out


@pytest.mark.parametrize("passes", [2, 3, 4, 5])
def test_control_floor_and_inherited_custom_thresholds(setup, passes):
    import yaml

    kwargs, configs = setup
    catalog = kwargs["root"] / "catalogs/gap/tasks.yaml"
    doc = yaml.safe_load(catalog.read_text())
    doc["tasks"][0].update(kind="control", family="control")
    catalog.write_text(yaml.safe_dump(doc))
    kwargs["grader"] = lambda *a: {"outcome": "pass"}
    old, _ = calibrate(**kwargs, max_floor=0.4, min_ceiling=0.6)
    count = 0

    def grade(*args):
        nonlocal count
        count += 1
        return {"outcome": "pass" if count <= passes else "fail"}

    kwargs.update(model="new", grader=grade)
    record, _ = recalibrate(**kwargs, previous_model="gpt-test")
    assert record["thresholds"] == old["thresholds"]
    assert record["tasks"][0]["verdict"] == ("admitted" if passes >= 3 else "rejected")
    assert "retired_at" not in record["tasks"][0]
