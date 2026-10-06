from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE_ROOT, LIB_PYTHON, MODULE_ROOT

import sew.schema
from sew.schema import FixtureValidation, validate_fixture_tree


def test_fixture_tree_validates_recorded_success_and_failure() -> None:
    results = validate_fixture_tree(FIXTURE_ROOT)

    assert [(result.run_id, result.status) for result in results] == [
        ("fixture-failure-20260916T181400Z", "failed"),
        ("fixture-success-20260916T181200Z", "succeeded"),
    ]


def test_fixture_tree_ignores_auxiliary_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "valid-run").mkdir()
    (tmp_path / "valid-run" / "run.json").write_text("{}", encoding="utf-8")
    seen = []

    def fake_validate_fixture_run(run_dir: Path) -> FixtureValidation:
        seen.append(run_dir.name)
        return FixtureValidation(
            run_id="valid-run",
            status="succeeded",
            metrics_ref="metrics/metrics.json",
            evaluation_ref="evaluations/evaluation.json",
        )

    monkeypatch.setattr(sew.schema, "validate_fixture_run", fake_validate_fixture_run)

    results = validate_fixture_tree(tmp_path)

    assert seen == ["valid-run"]
    assert [result.run_id for result in results] == ["valid-run"]


def test_fixture_tree_rejects_file_root(tmp_path: Path) -> None:
    fixture_root = tmp_path / "not-a-directory"
    fixture_root.write_text("{}", encoding="utf-8")

    with pytest.raises(sew.schema.SchemaError, match="fixture root must be a directory"):
        validate_fixture_tree(fixture_root)


def test_cli_validates_fixtures_without_network() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sew.cli",
            "validate-fixtures",
            "--fixture-root",
            str(FIXTURE_ROOT),
        ],
        cwd=MODULE_ROOT,
        env={"PYTHONPATH": str(LIB_PYTHON)},
        check=True,
        text=True,
        capture_output=True,
    )

    assert "catalog=lighthouse tasks=6" in completed.stdout
    assert "fixture=fixture-success-20260916T181200Z status=succeeded" in completed.stdout
    assert "fixture=fixture-failure-20260916T181400Z status=failed" in completed.stdout


def test_cli_validates_domain_catalog_without_network() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sew.cli",
            "validate-domain-catalog",
        ],
        cwd=MODULE_ROOT,
        env={"PYTHONPATH": str(LIB_PYTHON)},
        check=True,
        text=True,
        capture_output=True,
    )

    assert "catalog=domains tasks=24" in completed.stdout


def test_hq_sew_wrapper_runs_without_ambient_pythonpath(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    (tmp_path / "json.py").write_text("raise RuntimeError('cwd import hijack')\n", encoding="utf-8")

    completed = subprocess.run(
        [
            str(MODULE_ROOT / "bin" / "hq-sew"),
            "validate-fixtures",
            "--fixture-root",
            str(FIXTURE_ROOT),
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "catalog=lighthouse tasks=6" in completed.stdout
