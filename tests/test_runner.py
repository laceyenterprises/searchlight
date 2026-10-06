from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import MODULE_ROOT

from sew.runner import (
    BudgetTracker,
    CellExecution,
    MatrixCell,
    PROVIDER_UNAVAILABLE_MAX_DEFERRALS,
    PROVIDER_UNAVAILABLE_STOP_STREAK,
    OperatorBudgets,
    RunnerError,
    SuiteRunner,
    trailing_unavailable_streak,
)
from sew.schema import validate_fixture_run


class RecordingExecutor:
    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = statuses or []
        self.calls: list[str] = []
        self.run_ids: list[str] = []

    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        self.calls.append(cell.key)
        self.run_ids.append(cell.run_id)
        status = self.statuses.pop(0) if self.statuses else "succeeded"
        if status == "raise":
            raise RuntimeError("simulated executor crash")
        return CellExecution(status=status, run_id=cell.run_id, run_dir=None)


class BundleDirExecutor(RecordingExecutor):
    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        self.calls.append(cell.key)
        self.run_ids.append(cell.run_id)
        status = self.statuses.pop(0) if self.statuses else "succeeded"
        run_dir = output_root / cell.run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        return CellExecution(status=status, run_id=cell.run_id, run_dir=run_dir)


class MetricsBundleExecutor(RecordingExecutor):
    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        self.calls.append(cell.key)
        self.run_ids.append(cell.run_id)
        status = self.statuses.pop(0) if self.statuses else "succeeded"
        run_dir = output_root / cell.run_id
        (run_dir / "metrics").mkdir(parents=True, exist_ok=False)
        (run_dir / "run.json").write_text(
            json.dumps({"run_id": cell.run_id, "metrics_ref": "metrics/metrics.json"}),
            encoding="utf-8",
        )
        (run_dir / "metrics" / "metrics.json").write_text(
            json.dumps(
                {
                    "provider_calls": {"total": 1},
                    "provider_result_chars": 10,
                    "token_usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
                }
            ),
            encoding="utf-8",
        )
        return CellExecution(status=status, run_id=cell.run_id, run_dir=run_dir)


def test_dry_run_matrix_expansion_is_seed_stable(tmp_path: Path) -> None:
    runner = SuiteRunner(state_root=tmp_path)

    first = runner.dry_run("lighthouse", repetitions=1, seed=123)
    second = runner.dry_run("lighthouse", repetitions=1, seed=123)
    third = runner.dry_run("lighthouse", repetitions=1, seed=456)

    assert [cell["cell_key"] for cell in first["cells"]] == [
        cell["cell_key"] for cell in second["cells"]
    ]
    assert [cell["cell_key"] for cell in first["cells"]] != [
        cell["cell_key"] for cell in third["cells"]
    ]
    # 6 tasks x 7 providers (native, exa, parallel-web, firecrawl, brave,
    # tavily, perplexity) x 4 harness profiles x 1 repetition.
    assert first["total_cells"] == 6 * 7 * 4
    assert first["scheduled_cells"] + first["not_applicable_cells"] == 6 * 7 * 4


def test_unsupported_capability_cells_are_not_applicable(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        providers=["firecrawl", "exa", "native"],
        task_class="deep_crawl",
        harnesses={"codex": ["default"], "pi": ["oss-small"]},
    )
    runner = SuiteRunner(module_base=module_base, state_root=tmp_path / "state")

    dry = runner.dry_run("tiny")
    reasons = {
        cell["cell_key"]: cell["not_applicable_reason"]
        for cell in dry["cells"]
        if not cell["applicable"]
    }

    assert any(reason == "provider_lacks_crawl" for reason in reasons.values())
    assert any(reason == "native_search_unavailable" for reason in reasons.values())
    assert dry["not_applicable_cells"] == 3


def test_live_mode_allows_live_only_tasks(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, fixture_mode_allowed=False)
    executor = RecordingExecutor()
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run(
        "tiny",
        mode="live",
        run_id="live-only",
        resume=False,
        operator_budgets=OperatorBudgets(
            max_provider_calls=10,
            max_provider_result_chars=60000,
            max_total_tokens=120000,
            max_wall_clock_seconds=10,
        ),
    )

    assert executor.calls
    assert summary["status_counts"] == {"succeeded": 1}


def test_budget_exhaustion_stops_before_scheduling_over_budget_cell(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, suite_budgets={"max_provider_calls": 1})
    executor = RecordingExecutor()
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="budget", resume=False)

    assert executor.calls == []
    assert summary["completed_cells"] == 0
    assert summary["stopped_reason"] == "provider_call_budget_exhausted"


def test_zero_wall_clock_budget_records_timeout_without_execution(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, task_budgets={"wall_clock_seconds": 0})
    executor = RecordingExecutor()
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="timeout", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert executor.calls == []
    assert index[0]["status"] == "timeout"
    assert index[0]["failure_category"] == "task_timeout_before_start"


def test_zero_suite_wall_clock_budget_aborts_without_cell_churn(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, suite_timeouts={"run_seconds": 0})
    executor = RecordingExecutor()
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="suite-timeout", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert executor.calls == []
    assert index == []
    assert summary["completed_cells"] == 0
    assert summary["stopped_reason"] == "wall_clock_budget_exhausted"


def test_resume_restores_run_wall_clock_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = {"value": 1000.0}
    monkeypatch.setattr("sew.runner.time.monotonic", lambda: now["value"])

    class AdvancingExecutor(RecordingExecutor):
        def __init__(self, advance_seconds: float) -> None:
            super().__init__()
            self.advance_seconds = advance_seconds

        def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
            execution = super().execute(cell, output_root, mode=mode)
            now["value"] += self.advance_seconds
            return execution

    module_base = _tiny_module(
        tmp_path,
        tasks=["task-a-v1", "task-b-v1"],
        suite_timeouts={"run_seconds": 60},
    )
    state_root = tmp_path / "state"
    first_executor = AdvancingExecutor(60.5)

    first = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=first_executor,
    ).run("tiny", run_id="wall", resume=False, max_cells=1)
    state = json.loads((Path(first["run_root"]) / "runner-state.json").read_text(encoding="utf-8"))

    assert first["completed_cells"] == 1
    assert first["stopped_reason"] == "wall_clock_budget_exhausted"
    assert state["budget_elapsed_seconds"] >= 60

    second_executor = AdvancingExecutor(1)
    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=second_executor,
    ).run("tiny", run_id="wall")

    assert second_executor.calls == []
    assert resumed["completed_cells"] == 1
    assert resumed["remaining_cells"] == 1
    assert resumed["stopped_reason"] == "wall_clock_budget_exhausted"


def _operator(wall_clock_seconds: int, **spend: int) -> OperatorBudgets:
    return OperatorBudgets(
        max_provider_calls=spend.get("max_provider_calls", 10000),
        max_provider_result_chars=spend.get("max_provider_result_chars", 50_000_000),
        max_total_tokens=spend.get("max_total_tokens", 50_000_000),
        max_wall_clock_seconds=wall_clock_seconds,
    )


def _suite(run_seconds: int) -> dict[str, Any]:
    return {
        "timeouts": {"run_seconds": run_seconds, "provider_call_seconds": 60},
        "budgets": {
            "max_provider_calls": 400,
            "max_provider_result_chars": 1_000_000,
            "max_total_tokens": 3_000_000,
        },
    }


def test_operator_wall_clock_is_the_run_wall_clock_not_the_suite_timeout() -> None:
    # The published WSB reproduction suite carries run_seconds 1200, and the
    # reproduction passes --max-wall-clock-seconds 172800. The run used to stop
    # for good at ~1260 s elapsed (min of the two, persisted across resumes).
    tracker = BudgetTracker.from_suite(_suite(1200), _operator(172800))
    tracker.restore_elapsed_seconds(1260)

    assert tracker.limits.max_wall_clock_seconds == 172800
    assert tracker.exhausted_reason() is None

    tracker.restore_elapsed_seconds(172800)
    assert tracker.exhausted_reason() == "wall_clock_budget_exhausted"


def test_suite_spend_budgets_stay_ceilings_under_larger_operator_caps() -> None:
    tracker = BudgetTracker.from_suite(_suite(1200), _operator(172800))

    assert tracker.limits.max_provider_calls == 400
    assert tracker.limits.max_provider_result_chars == 1_000_000
    assert tracker.limits.max_total_tokens == 3_000_000

    lower = BudgetTracker.from_suite(_suite(1200), _operator(60, max_total_tokens=5000))
    assert lower.limits.max_total_tokens == 5000
    assert lower.limits.max_wall_clock_seconds == 60


def test_suite_timeout_is_the_wall_clock_only_without_operator_caps() -> None:
    assert BudgetTracker.from_suite(_suite(1200), None).limits.max_wall_clock_seconds == 1200


def test_resume_after_a_wall_clock_stop_continues_under_a_larger_operator_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = {"value": 1000.0}
    monkeypatch.setattr("sew.runner.time.monotonic", lambda: now["value"])

    class AdvancingExecutor(RecordingExecutor):
        def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
            execution = super().execute(cell, output_root, mode=mode)
            now["value"] += 1260
            return execution

    module_base = _tiny_module(
        tmp_path,
        tasks=["task-a-v1", "task-b-v1"],
        suite_timeouts={"run_seconds": 1200},
    )
    state_root = tmp_path / "state"

    first = SuiteRunner(
        module_base=module_base, state_root=state_root, executor=AdvancingExecutor()
    ).run("tiny", run_id="wall", resume=False, operator_budgets=_operator(1200))
    assert first["completed_cells"] == 1
    assert first["stopped_reason"] == "wall_clock_budget_exhausted"
    assert first["budget_limits"]["max_wall_clock_seconds"] == 1200

    same_cap = RecordingExecutor()
    held = SuiteRunner(
        module_base=module_base, state_root=state_root, executor=same_cap
    ).run("tiny", run_id="wall", operator_budgets=_operator(1200))
    assert same_cap.calls == []
    assert held["stopped_reason"] == "wall_clock_budget_exhausted"

    larger = AdvancingExecutor()
    resumed = SuiteRunner(
        module_base=module_base, state_root=state_root, executor=larger
    ).run("tiny", run_id="wall", operator_budgets=_operator(172800))
    assert len(larger.calls) == 1
    assert resumed["completed_cells"] == 2
    assert resumed["remaining_cells"] == 0
    assert resumed["stopped_reason"] is None
    assert resumed["budget_limits"] == {
        "max_provider_calls": 12,
        "max_provider_result_chars": 60000,
        "max_total_tokens": 120000,
        "max_wall_clock_seconds": 172800,
    }
    assert resumed["budget_elapsed_seconds"] >= 2520


def test_retries_apply_only_when_manifest_declares_them(tmp_path: Path) -> None:
    no_retry_base = _tiny_module(tmp_path / "no-retry")
    retry_base = _tiny_module(tmp_path / "with-retry", suite_retries={"max_attempts": 1})

    no_retry_executor = RecordingExecutor(["failed", "succeeded"])
    retry_executor = RecordingExecutor(["failed", "succeeded"])

    SuiteRunner(
        module_base=no_retry_base,
        state_root=tmp_path / "state1",
        executor=no_retry_executor,
    ).run("tiny", run_id="no-retry", resume=False)
    retry_summary = SuiteRunner(
        module_base=retry_base,
        state_root=tmp_path / "state2",
        executor=retry_executor,
    ).run("tiny", run_id="retry", resume=False)

    assert len(no_retry_executor.calls) == 1
    assert len(retry_executor.calls) == 2
    assert _read_index(Path(retry_summary["run_root"]))[0]["attempts"] == 2


def test_retry_attempts_use_distinct_bundle_run_ids(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, suite_retries={"max_attempts": 1})
    executor = BundleDirExecutor(["failed", "succeeded"])
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="retry-bundles", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert len(executor.run_ids) == 2
    assert executor.run_ids[1] == f"{executor.run_ids[0]}-att2"
    assert index[0]["run_id"] == executor.run_ids[1]
    assert Path(index[0]["run_dir"]).name == executor.run_ids[1]
    assert [Path(path).name for path in index[0]["attempt_run_dirs"]] == executor.run_ids


def test_retry_loop_stops_before_over_budget_attempt(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        suite_budgets={"max_provider_calls": 1},
        task_budgets={"max_provider_calls": 1},
        suite_retries={"max_attempts": 3},
    )
    executor = MetricsBundleExecutor(["failed", "succeeded"])
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="retry-budget", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert len(executor.calls) == 1
    assert index[0]["attempts"] == 1
    assert summary["stopped_reason"] == "provider_call_budget_exhausted"


def test_failed_retry_attempt_metrics_count_toward_budget(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        suite_budgets={"max_provider_calls": 2},
        task_budgets={"max_provider_calls": 1},
        suite_retries={"max_attempts": 1},
    )
    executor = MetricsBundleExecutor(["failed", "succeeded"])
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    summary = runner.run("tiny", run_id="retry-metrics", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert len(executor.calls) == 2
    assert index[0]["attempts"] == 2
    assert summary["stopped_reason"] == "provider_call_budget_exhausted"


def test_resumed_retry_attempt_metrics_count_toward_budget(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        tasks=["task-a-v1", "task-b-v1"],
        suite_budgets={"max_provider_calls": 3},
        task_budgets={"max_provider_calls": 2},
        suite_retries={"max_attempts": 1},
    )
    state_root = tmp_path / "state"
    first_executor = MetricsBundleExecutor(["failed", "succeeded"])
    first_runner = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=first_executor,
    )

    first = first_runner.run("tiny", run_id="retry-resume-budget", resume=False, max_cells=1)
    second_executor = MetricsBundleExecutor()
    second = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=second_executor,
    ).run("tiny", run_id="retry-resume-budget", resume=True)
    index = _read_index(Path(second["run_root"]))

    assert first["stopped_reason"] == "interrupted"
    assert len(first_executor.calls) == 2
    assert second_executor.calls == []
    assert len(index) == 1
    assert index[0]["attempts"] == 2
    assert len(index[0]["attempt_run_dirs"]) == 2
    assert second["stopped_reason"] == "provider_call_budget_exhausted"


def test_provider_unavailable_streak_stops_resumably(tmp_path: Path) -> None:
    """2026-09-29: an account 429 failed 145 cells in five minutes. Stop after a streak."""

    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
    )
    state_root = tmp_path / "state"
    walled = RecordingExecutor(["provider_unavailable"] * 4)
    first = SuiteRunner(module_base=module_base, state_root=state_root, executor=walled).run(
        "tiny", run_id="unavailable-streak", resume=False
    )

    assert first["stopped_reason"] == "provider_unavailable"
    assert len(walled.calls) == 3
    assert _read_index(Path(first["run_root"])) == []
    # All four cells remain; three of them wait on the wall, one never started.
    assert first["remaining_cells"] == 4
    assert first["deferred_unavailable_cells"] == 3

    healthy = RecordingExecutor()
    resumed = SuiteRunner(module_base=module_base, state_root=state_root, executor=healthy).run(
        "tiny", run_id="unavailable-streak", resume=True
    )
    assert resumed["stopped_reason"] is None
    assert resumed["deferred_unavailable_cells"] == 0
    assert len(healthy.calls) == 4
    assert [entry["status"] for entry in _read_index(Path(resumed["run_root"]))] == [
        "succeeded"
    ] * 4


def test_isolated_provider_unavailable_does_not_stop_the_suite(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
    )
    executor = RecordingExecutor(["provider_unavailable", "succeeded", "provider_unavailable"])
    summary = SuiteRunner(
        module_base=module_base, state_root=tmp_path / "state", executor=executor
    ).run("tiny", run_id="isolated-unavailable", resume=False)

    assert summary["stopped_reason"] is None
    assert len(executor.calls) == 4
    statuses = [entry["status"] for entry in _read_index(Path(summary["run_root"]))]
    assert statuses.count("provider_unavailable") == 2


class WalledArmExecutor(RecordingExecutor):
    """One harness account is walled (a weekly usage limit); every other arm answers."""

    def __init__(self, walled_harness: str) -> None:
        super().__init__()
        self.walled_harness = walled_harness

    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        self.calls.append(cell.key)
        walled = cell.harness_id == self.walled_harness
        return CellExecution(
            status="provider_unavailable" if walled else "succeeded",
            run_id=cell.run_id,
            run_dir=None,
        )


def test_one_walled_arm_cannot_livelock_resumes(tmp_path: Path) -> None:
    """Deferred cells line up at the head of every resume; bounded deferrals let the suite finish."""

    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
        tasks=["task-a-v1", "task-b-v1", "task-c-v1"],
    )
    state_root = tmp_path / "state"
    stops = []
    for _ in range(20):
        executor = WalledArmExecutor("codex")
        summary = SuiteRunner(
            module_base=module_base, state_root=state_root, executor=executor
        ).run("tiny", run_id="walled-arm", resume=True)
        stops.append(summary["stopped_reason"])
        if summary["stopped_reason"] is None:
            break

    # However the matrix order groups the six walled cells, each streak stops
    # the suite at most MAX_DEFERRALS times before its cells are recorded.
    walled = 6
    streaks = -(-walled // PROVIDER_UNAVAILABLE_STOP_STREAK)
    assert stops[-1] is None
    assert set(stops[:-1]) <= {"provider_unavailable"}
    assert len(stops) <= streaks * PROVIDER_UNAVAILABLE_MAX_DEFERRALS + 1
    index = _read_index(Path(summary["run_root"]))
    assert len(index) == 12
    for entry in index:
        expected = "provider_unavailable" if entry["harness_id"] == "codex" else "succeeded"
        assert entry["status"] == expected
    codex_keys = sorted(entry["cell_key"] for entry in index if entry["harness_id"] == "codex")
    assert len(codex_keys) == walled
    # Every walled cell ran out of deferrals, and the summary names each one.
    assert summary["unavailable_exhausted_cells"] == codex_keys


def test_streak_split_by_a_crash_still_defers_on_resume(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
    )
    state_root = tmp_path / "state"
    # Two streak cells are indexed when the call ends (a crash or an interrupt).
    first = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=RecordingExecutor(["provider_unavailable"] * 2),
    ).run("tiny", run_id="split-streak", resume=False, max_cells=2)
    assert first["stopped_reason"] == "interrupted"
    assert len(_read_index(Path(first["run_root"]))) == 2

    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=RecordingExecutor(["provider_unavailable"]),
    ).run("tiny", run_id="split-streak", resume=True)

    # The third unavailable cell completes the streak, and all three are deferred.
    assert resumed["stopped_reason"] == "provider_unavailable"
    assert _read_index(Path(resumed["run_root"])) == []
    assert resumed["deferred_unavailable_cells"] == 3
    assert resumed["unavailable_exhausted_cells"] == []


def test_trailing_unavailable_streak_mirrors_the_run_loop() -> None:
    def entry(status: str, *, executed: bool = True, deferrals: int = 0) -> dict[str, Any]:
        record: dict[str, Any] = {"status": status}
        if executed:
            record["elapsed_seconds"] = 1.0
        if deferrals:
            record["unavailable_deferrals"] = deferrals
        return record

    unavailable = entry("provider_unavailable")
    after_na = entry("provider_unavailable", deferrals=1)
    index = [
        entry("provider_unavailable"),
        entry("succeeded"),
        unavailable,
        entry("provider_unavailable", deferrals=PROVIDER_UNAVAILABLE_MAX_DEFERRALS),
        after_na,
        entry("not_applicable", executed=False),
    ]

    streak = trailing_unavailable_streak(index)

    # Exhausted and never-executed cells neither extend nor break the streak.
    assert streak == [unavailable, after_na]
    assert streak[0] is unavailable
    assert trailing_unavailable_streak([*index, entry("succeeded")]) == []


def test_streak_stop_keeps_budget_reason_and_deferred_evidence(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
        suite_budgets={"max_provider_calls": 3},
        task_budgets={"max_provider_calls": 1},
    )
    state_root = tmp_path / "state"
    walled = MetricsBundleExecutor(["provider_unavailable"] * 3)
    first = SuiteRunner(module_base=module_base, state_root=state_root, executor=walled).run(
        "tiny", run_id="streak-budget", resume=False
    )

    # The third unavailable cell also spent the last provider call: budget binds.
    assert first["stopped_reason"] == "provider_call_budget_exhausted"
    run_root = Path(first["run_root"])
    assert _read_index(run_root) == []
    state = json.loads((run_root / "runner-state.json").read_text(encoding="utf-8"))
    deferred = state["deferred_unavailable"]
    assert sorted(deferred) == sorted(walled.calls)
    for cell_key, record in deferred.items():
        assert record["deferrals"] == 1
        assert [Path(path).name for path in record["attempt_run_dirs"]] == [
            walled.run_ids[walled.calls.index(cell_key)]
        ]


def test_resumed_deferred_cell_keeps_its_unavailable_bundle(tmp_path: Path) -> None:
    module_base = _tiny_module(
        tmp_path,
        providers=["native", "no-search"],
        harnesses={"codex": ["default"], "claude-code": ["default"]},
    )
    state_root = tmp_path / "state"
    SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=MetricsBundleExecutor(["provider_unavailable"] * 3),
    ).run("tiny", run_id="deferred-evidence", resume=False)

    class NextIdExecutor(RecordingExecutor):
        def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
            self.calls.append(cell.key)
            run_dir = output_root / f"{cell.run_id}-r2"
            run_dir.mkdir(parents=True, exist_ok=True)
            return CellExecution(status="succeeded", run_id=run_dir.name, run_dir=run_dir)

    resumed = SuiteRunner(
        module_base=module_base, state_root=state_root, executor=NextIdExecutor()
    ).run("tiny", run_id="deferred-evidence", resume=True)

    index = _read_index(Path(resumed["run_root"]))
    assert resumed["stopped_reason"] is None
    deferred_entries = [entry for entry in index if "unavailable_deferrals" in entry]
    assert len(deferred_entries) == 3
    for entry in deferred_entries:
        assert entry["unavailable_deferrals"] == 1
        assert [Path(path).name for path in entry["attempt_run_dirs"]] == [
            entry["run_id"].removesuffix("-r2"),
            entry["run_id"],
        ]
    # Indexed cells leave the deferral map, which lists only cells still out.
    state = json.loads((Path(resumed["run_root"]) / "runner-state.json").read_text("utf-8"))
    assert state["deferred_unavailable"] == {}
    assert resumed["deferred_unavailable_cells"] == 0


def test_no_resume_clears_existing_run_root_before_reuse(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path)
    state_root = tmp_path / "state"
    first_executor = BundleDirExecutor()
    SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=first_executor,
    ).run("tiny", run_id="fresh", resume=False)
    stale_marker = state_root / ".sew" / "runs" / "fresh" / "stale.txt"
    stale_marker.write_text("old run artifact\n", encoding="utf-8")

    second_executor = BundleDirExecutor()
    summary = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        executor=second_executor,
    ).run("tiny", run_id="fresh", resume=False)

    assert second_executor.run_ids == first_executor.run_ids
    assert not stale_marker.exists()
    assert _read_index(Path(summary["run_root"]))[0]["run_id"] == second_executor.run_ids[0]


def test_run_id_must_be_single_path_segment_before_no_resume_delete(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("do not delete\n", encoding="utf-8")

    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=BundleDirExecutor(),
    )

    with pytest.raises(RunnerError, match="single path segment"):
        runner.run("tiny", run_id=str(outside), resume=False)

    assert marker.exists()


def test_interrupt_and_resume_do_not_duplicate_or_reorder_completed_cells(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, tasks=["task-a-v1", "task-b-v1", "task-c-v1"])
    executor = RecordingExecutor()
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    first = runner.run("tiny", run_id="resume", resume=True, max_cells=2)
    second = runner.run("tiny", run_id="resume", resume=True)
    index = _read_index(Path(second["run_root"]))

    assert first["stopped_reason"] == "interrupted"
    assert second["remaining_cells"] == 0
    assert len(index) == 3
    assert len({entry["run_id"] for entry in index}) == 3
    assert [entry["cell_key"] for entry in index[:2]] == executor.calls[:2]
    assert executor.calls == [entry["cell_key"] for entry in index]


def test_runner_persists_completed_cell_before_later_executor_crash(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, tasks=["task-a-v1", "task-b-v1"])
    executor = RecordingExecutor(["succeeded", "raise"])
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=executor,
    )

    with pytest.raises(RuntimeError, match="simulated executor crash"):
        runner.run("tiny", run_id="crash", resume=False)

    index = _read_index(tmp_path / "state" / ".sew" / "runs" / "crash")

    assert len(index) == 1
    assert index[0]["cell_key"] == executor.calls[0]


def test_fixture_mode_executes_small_lighthouse_matrix_without_network(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, providers=["native"], harnesses={"codex": ["default"]})
    runner = SuiteRunner(module_base=module_base, state_root=tmp_path / "state")

    summary = runner.run("tiny", run_id="fixture", resume=False)
    index = _read_index(Path(summary["run_root"]))

    assert summary["status_counts"] == {"succeeded": 1}
    run_dir = Path(index[0]["run_dir"])
    validation = validate_fixture_run(run_dir)
    assert validation.run_id == index[0]["run_id"]


def test_live_mode_refuses_without_explicit_operator_budgets(tmp_path: Path) -> None:
    runner = SuiteRunner(state_root=tmp_path)

    with pytest.raises(RunnerError, match="explicit operator budgets"):
        runner.run("lighthouse", mode="live", run_id="live")


def test_live_mode_cli_accepts_only_complete_operator_budgets(tmp_path: Path) -> None:
    cmd = [
        str(MODULE_ROOT / "bin" / "hq-sew"),
        "run",
        "--suite",
        "lighthouse",
        "--mode",
        "live",
        "--state-root",
        str(tmp_path),
    ]

    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    assert result.returncode == 2
    assert "explicit operator budgets" in result.stderr


def test_operator_budgets_can_be_constructed_for_live_budget_gate() -> None:
    budgets = OperatorBudgets(
        max_provider_calls=1,
        max_provider_result_chars=1,
        max_total_tokens=1,
        max_wall_clock_seconds=1,
    )

    assert budgets.max_provider_calls == 1


def test_wall_clock_budget_stops_new_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0, 11.0])
    monkeypatch.setattr("sew.runner.time.monotonic", lambda: next(ticks))
    budgets = BudgetTracker(
        OperatorBudgets(
            max_provider_calls=10,
            max_provider_result_chars=1000,
            max_total_tokens=1000,
            max_wall_clock_seconds=10,
        )
    )

    assert (
        budgets.can_start(
            {
                "budgets": {
                    "max_provider_calls": 1,
                    "max_bytes": 1,
                    "max_total_tokens": 1,
                }
            }
        )
        == "wall_clock_budget_exhausted"
    )


def test_incomplete_bundle_absorbs_as_zero_budget(tmp_path: Path) -> None:
    budgets = BudgetTracker(
        OperatorBudgets(
            max_provider_calls=10,
            max_provider_result_chars=1000,
            max_total_tokens=1000,
            max_wall_clock_seconds=10,
        )
    )
    missing_run = tmp_path / "missing-run"
    missing_run.mkdir()
    missing_metrics = tmp_path / "missing-metrics"
    missing_metrics.mkdir()
    (missing_metrics / "run.json").write_text(
        json.dumps({"metrics_ref": "metrics/metrics.json"}),
        encoding="utf-8",
    )
    partial_metrics = tmp_path / "partial-metrics"
    (partial_metrics / "metrics").mkdir(parents=True)
    (partial_metrics / "run.json").write_text(
        json.dumps({"metrics_ref": "metrics/metrics.json"}),
        encoding="utf-8",
    )
    (partial_metrics / "metrics" / "metrics.json").write_text("{}", encoding="utf-8")

    budgets.absorb_run_dir(missing_run)
    budgets.absorb_run_dir(missing_metrics)
    budgets.absorb_run_dir(partial_metrics)

    assert budgets.provider_calls == 0
    assert budgets.provider_result_chars == 0
    assert budgets.total_tokens == 0


def test_null_metrics_sections_absorb_as_zero_budget(tmp_path: Path) -> None:
    budgets = BudgetTracker(
        OperatorBudgets(
            max_provider_calls=10,
            max_provider_result_chars=1000,
            max_total_tokens=1000,
            max_wall_clock_seconds=10,
        )
    )
    run_dir = tmp_path / "null-metrics"
    (run_dir / "metrics").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"metrics_ref": "metrics/metrics.json"}),
        encoding="utf-8",
    )
    (run_dir / "metrics" / "metrics.json").write_text(
        json.dumps(
            {
                "provider_calls": None,
                "provider_result_chars": None,
                "token_usage": None,
            }
        ),
        encoding="utf-8",
    )

    budgets.absorb_run_dir(run_dir)

    assert budgets.provider_calls == 0
    assert budgets.provider_result_chars == 0
    assert budgets.total_tokens == 0


def test_metered_run_dir_is_charged_once_across_resume_scans(tmp_path: Path) -> None:
    budgets = BudgetTracker(
        OperatorBudgets(
            max_provider_calls=10,
            max_provider_result_chars=1000,
            max_total_tokens=1000,
            max_wall_clock_seconds=10,
        )
    )
    run_dir = tmp_path / "metered"
    (run_dir / "metrics").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"metrics_ref": "metrics/metrics.json"}), encoding="utf-8"
    )
    (run_dir / "metrics" / "metrics.json").write_text(
        json.dumps(
            {
                "provider_calls": {"total": 1},
                "provider_result_chars": 12,
                "token_usage": {"input": 2, "cached_input": 3, "output": 5, "reasoning": 7},
            }
        ),
        encoding="utf-8",
    )

    budgets.absorb_index([{"attempt_run_dirs": [str(run_dir), str(run_dir)]}])
    budgets.absorb_run_dir(run_dir)

    assert budgets.provider_calls == 1
    assert budgets.provider_result_chars == 12
    assert budgets.total_tokens == 17


def test_runner_state_records_resolved_seed_override(tmp_path: Path) -> None:
    module_base = _tiny_module(tmp_path, tasks=["task-a-v1", "task-b-v1"])
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        executor=RecordingExecutor(),
    )

    summary = runner.run("tiny", run_id="seeded", resume=False, seed=123)
    state = json.loads(
        (Path(summary["run_root"]) / "runner-state.json").read_text(encoding="utf-8")
    )

    assert state["seed"] == 123


def _tiny_module(
    root: Path,
    *,
    providers: list[str] | None = None,
    harnesses: dict[str, list[str]] | None = None,
    task_class: str = "fact_lookup",
    tasks: list[str] | None = None,
    suite_budgets: dict[str, int] | None = None,
    task_budgets: dict[str, int] | None = None,
    suite_retries: dict[str, int] | None = None,
    suite_timeouts: dict[str, int] | None = None,
    fixture_mode_allowed: bool = True,
) -> Path:
    base = root / "module"
    (base / "catalogs" / "tiny").mkdir(parents=True)
    (base / "config").mkdir(parents=True)
    (base / "config" / "pi-model-profiles.yaml").write_text(
        (MODULE_ROOT / "config" / "pi-model-profiles.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    task_ids = tasks or ["current-fact-lookup-v1"]
    budgets = {
        "max_provider_calls": 12,
        "max_provider_result_chars": 60000,
        "max_total_tokens": 120000,
    }
    budgets.update(suite_budgets or {})
    suite: dict[str, Any] = {
        "schema_version": 1,
        "suite_id": "tiny",
        "version": "1",
        "description": "tiny runner test suite",
        "randomization_seed": 7,
        "fixture_mode": True,
        "repetitions": 1,
        "timeouts": {"run_seconds": 600, "provider_call_seconds": 60} | (suite_timeouts or {}),
        "budgets": budgets,
        "providers": providers or ["native"],
        "harnesses": {
            key: {"model_profiles": value}
            for key, value in (harnesses or {"codex": ["default"]}).items()
        },
        "tasks": task_ids,
    }
    if suite_retries is not None:
        suite["retries"] = suite_retries
    (base / "catalogs" / "tiny" / "suite.yaml").write_text(
        yaml.safe_dump(suite, sort_keys=False),
        encoding="utf-8",
    )
    for task_id in task_ids:
        task_dir = base / "tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "prompt.md").write_text("Answer from fixture evidence.\n", encoding="utf-8")
        task_budget = {
            "max_provider_calls": 2,
            "max_pages": 2,
            "max_bytes": 20000,
            "max_total_tokens": 8000,
            "wall_clock_seconds": 120,
        }
        task_budget.update(task_budgets or {})
        task = {
            "schema_version": 1,
            "task_id": task_id,
            "version": "1",
            "title": task_id,
            "task_class": task_class,
            "created_at": "2026-09-16",
            "live_web_required": False,
            "fixture_mode_allowed": fixture_mode_allowed,
            "allowed_domains": ["fixture.example"],
            "disallowed_domains": [],
            "expected_output_schema": {"type": "object", "required": ["answer"]},
            "deterministic_validator": {"kind": "fixture", "expected_answer": "Release 3.2"},
            "budgets": task_budget,
            "success_criteria": ["fixture"],
        }
        (task_dir / "task.yaml").write_text(
            yaml.safe_dump(task, sort_keys=False),
            encoding="utf-8",
        )
    return base


def _read_index(run_root: Path) -> list[dict[str, Any]]:
    return json.loads((run_root / "run-index.json").read_text(encoding="utf-8"))
