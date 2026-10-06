"""Validity acceptance uses the real offline verifier and Seatbelt boundary."""

import copy
import json

from sew.cli import main

import pytest

from sew.gap.validate import validate_task
from sew.gap.workspace import capture_diff
from test_gap_catalog import TASKS, write_catalog
from test_gap_verify import correct

pytest_plugins = ("test_gap_verify",)


def task_for(job, *, control=False):
    task = copy.deepcopy(
        next(
            t
            for t in TASKS
            if t["task_type"] == "code" and t["kind"] == ("control" if control else "gap")
        )
    )
    task.update(job[4])
    task["third_party_packages"] = sorted({p["name"] for p in task["packages"]})
    return task


def reference(job):
    capture_diff(job[1], job[2], job[0] / "catalogs/gap/hidden/reference.diff")


def test_gap_triple_uses_old_naive_and_reference_three_times(job):
    correct(job)
    reference(job)
    result = validate_task(task_for(job), root=job[0], wheelhouse=job[3])
    assert result["accepted"], result["reason"]
    assert [(c["rep"], c["leg"]) for c in result["checks"]] == [
        (rep, leg) for rep in range(1, 4) for leg in ("old-visible", "naive-bump", "reference")
    ]
    assert all(
        c["result"]["escaped_defects"] == 1 for c in result["checks"] if c["leg"] == "naive-bump"
    )


@pytest.mark.parametrize("with_cache", [True, False])
def test_control_triple_requires_no_version_bump(job, tmp_path, with_cache):
    (job[1] / "app.py").write_text("def value(): return 1\n")
    (job[1] / "requirements.txt").write_text("")
    (job[2] / "app.py").write_text("def value(): return 2\n")
    (job[2] / "requirements.txt").write_text("")
    job[4]["packages"] = []
    task = task_for(job, control=True)
    task["third_party_packages"] = []
    reference(job)
    write_catalog(job[0], [task])
    argv = [
        "gap",
        "validate-task",
        task["id"],
        "--module-root",
        str(job[0]),
        "--state-root",
        str(tmp_path / "state"),
    ]
    if with_cache:
        argv.extend(["--wheelhouse", str(job[3])])
    assert main(argv) == 0
    result = json.loads((tmp_path / "state/gap/validation" / (task["id"] + ".json")).read_text())
    assert result["accepted"], result["reason"]
    assert [(c["rep"], c["leg"]) for c in result["checks"]] == [
        (rep, leg) for rep in range(1, 4) for leg in ("unmodified", "reference")
    ]


@pytest.mark.parametrize(
    "hidden",
    [
        "def test_hidden(): assert True\n",
        'import pytest\ndef test_hidden(): pytest.skip("refused")\n',
    ],
)
def test_hidden_tests_must_run_and_detect_naive_defect(job, hidden):
    correct(job)
    reference(job)
    (job[0] / "catalogs/gap/hidden/test_hidden.py").write_text(hidden)
    result = validate_task(task_for(job), root=job[0], wheelhouse=job[3])
    assert not result["accepted"]
    assert result["reason"].startswith("naive-bump, repetition 1:")


def test_packaged_task_without_wheelhouse_is_refused_before_install(job, tmp_path, monkeypatch):
    correct(job)
    reference(job)
    task = task_for(job)
    write_catalog(job[0], [task])
    monkeypatch.setattr(
        "sew.gap.verify._execute",
        lambda *a, **kw: pytest.fail("execution before cache verification"),
    )
    assert (
        main(
            [
                "gap",
                "validate-task",
                task["id"],
                "--module-root",
                str(job[0]),
                "--state-root",
                str(tmp_path / "state"),
            ]
        )
        == 1
    )
    result = json.loads((tmp_path / "state/gap/validation" / (task["id"] + ".json")).read_text())
    assert not result["accepted"]
    assert "GAP packages require a verified wheelhouse cache" in result["reason"]
    assert len(result["checks"]) == 1
    assert result["checks"][0]["result"]["errors"][0]["stage"] == "setup"
