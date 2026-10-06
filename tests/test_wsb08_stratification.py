from pathlib import Path

import pytest

import sew.runner as runner_module
from sew.runner import SuiteRunner


def test_stratification_balanced_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = SuiteRunner(state_root=tmp_path, module_base=tmp_path)
    suite = {
        "suite_id": "test",
        "version": 1,
        "repetitions": 1,
        "randomization_seed": 42,
        "providers": ["p1"],
        "harnesses": {"h1": {"model_profiles": ["m1"]}},
        "tasks": ["t1", "t2", "t3", "t4", "t5", "t6"],
    }
    tasks = {
        "t1": {"task_class": "c1", "provider_operation": "search"},
        "t2": {"task_class": "c1", "provider_operation": "search"},
        "t3": {"task_class": "c1", "provider_operation": "search"},
        "t4": {"task_class": "c2", "provider_operation": "search"},
        "t5": {"task_class": "c2", "provider_operation": "search"},
        "t6": {"task_class": "c3", "provider_operation": "search"},
    }

    class DummyDriver:
        @classmethod
        def from_config(cls, _path: Path) -> "DummyDriver":
            return cls()

    monkeypatch.setattr(runner_module, "cell_applicability", lambda *_args: (True, None))
    monkeypatch.setattr(runner_module, "PiHarnessDriver", DummyDriver)

    cells = runner.expand_matrix(suite, tasks, mode="fixture")

    classes = [tasks[cell.task_id]["task_class"] for cell in cells]
    assert classes[:3] == ["c1", "c2", "c3"]
    assert classes[:6].count("c1") == 3
    assert classes[:6].count("c2") == 2
    assert classes[:6].count("c3") == 1
