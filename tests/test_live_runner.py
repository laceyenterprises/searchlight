"""WSB-07 live cell executor, per-cell budgets, and resume.

Every test is hermetic: a fake harness speaks each CLI's real stream format
(shapes from claude 2.1.282 and codex-cli 0.157.0) and picks its behaviour
from a ``FAKE_MODE:<mode>`` marker in the task prompt, so one suite can mix
cells that answer, overspend, or hang. Each spawn appends a line to a log, so
a test can prove which cells really ran and how often.
"""

from __future__ import annotations

import _thread
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import LIB_PYTHON, MODULE_ROOT
from test_runner import _read_index, _tiny_module

from sew import live_harness
from sew.harness import HarnessRunConfig
from sew.live_harness import LIVE_ENV
from sew.runner import (
    CellExecution,
    LiveCellExecutor,
    MatrixCell,
    OperatorBudgets,
    RunnerError,
    SuiteRunner,
    load_provider_exposures,
)
from sew.schema import SchemaError, validate_fixture_run
from sew.task_resolution import resolve_task


def test_production_cell_writes_source_with_fake_harness(
    tmp_path: Path, fake_harness: Path
) -> None:
    cell = MatrixCell(
        suite_id="wsb-trial",
        suite_version="1",
        task_id="list-build-python313-pep594-removals",
        provider_id="native",
        harness_id="claude-code",
        model_profile="default",
        repetition=1,
        run_id="production-fake",
        required_operation="search",
        applicable=True,
    )
    result = _executor(MODULE_ROOT, fake_harness, tmp_path).execute(
        cell, tmp_path / "bundles", mode="live"
    )
    assert result.status == "succeeded"
    validate_fixture_run(result.run_dir)
    run = json.loads((result.run_dir / "run.json").read_text())
    assert run["task_source"] == "production"


def test_direct_production_run_applies_catalog_budgets(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct run_live_harness call (default task_source) still gets catalog budgets."""

    seen: list[HarnessRunConfig] = []
    real = live_harness.resolve_live_limits

    def spy(config: HarnessRunConfig) -> Any:
        seen.append(config)
        return real(config)

    monkeypatch.setattr(live_harness, "resolve_live_limits", spy)
    task_id = "list-build-python313-pep594-removals"
    live_harness.run_live_harness(
        HarnessRunConfig(
            harness_id="claude-code",
            provider_id="native",
            task_id=task_id,
            mode="live",
            native_search_available=True,
            env={"FAKE_SPAWN_LOG": str(tmp_path / "spawns.jsonl")},
        ),
        tmp_path / "bundles",
        environ=_environ(fake_harness, tmp_path),
    )
    assert [config.task_source for config in seen] == ["production"]
    limits = real(seen[0])
    budgets = resolve_task(task_id).budgets
    assert limits.max_total_tokens == budgets["max_total_tokens"]
    assert limits.timeout_seconds <= budgets["wall_clock_seconds"]


def test_direct_production_prompt_override_is_refused_before_spawn(
    tmp_path: Path, fake_harness: Path
) -> None:
    with pytest.raises(SchemaError, match="cannot override its catalog prompt"):
        live_harness.run_live_harness(
            HarnessRunConfig(
                harness_id="claude-code",
                provider_id="native",
                task_id="list-build-python313-pep594-removals",
                mode="live",
                prompt_text="An operator supplied a different task.",
                env={"FAKE_SPAWN_LOG": str(tmp_path / "spawns.jsonl")},
            ),
            tmp_path / "bundles",
            environ=_environ(fake_harness, tmp_path),
        )
    assert not (tmp_path / "spawns.jsonl").exists()


def test_ad_hoc_prompt_override_still_runs_as_legacy(tmp_path: Path, fake_harness: Path) -> None:
    result = live_harness.run_live_harness(
        HarnessRunConfig(
            harness_id="claude-code",
            provider_id="native",
            task_id="smoke",
            mode="live",
            harness_auth="account",
            prompt_text="FAKE_MODE:success Reply with a short answer.",
            native_search_available=True,
            env={"FAKE_SPAWN_LOG": str(tmp_path / "spawns.jsonl")},
        ),
        tmp_path / "bundles",
        environ=_environ(fake_harness, tmp_path),
    )
    assert result.status == "succeeded"
    run = json.loads((result.bundle_dir / "run.json").read_text())
    assert run["task_source"] == "legacy"


def test_legacy_prompt_override_does_not_hide_a_malformed_manifest(
    tmp_path: Path, fake_harness: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sew.task_resolution as task_resolution

    def malformed_manifest(_task_dir: Path) -> None:
        raise SchemaError("malformed legacy task manifest")

    monkeypatch.setattr(task_resolution, "load_task_manifest", malformed_manifest)
    with pytest.raises(SchemaError, match="malformed legacy task manifest"):
        live_harness.run_live_harness(
            HarnessRunConfig(
                harness_id="claude-code",
                provider_id="native",
                task_id="current-fact-lookup-v1",
                mode="live",
                prompt_text="Operator supplied prompt.",
                env={"FAKE_SPAWN_LOG": str(tmp_path / "spawns.jsonl")},
            ),
            tmp_path / "bundles",
            environ=_environ(fake_harness, tmp_path),
        )
    assert not (tmp_path / "spawns.jsonl").exists()


FAKE_HARNESS = r"""
import json, os, re, sys, time

prompt = sys.stdin.read()
harness = "codex" if "exec" in sys.argv[1:] else "claude-code"
marker = re.search(r"FAKE_MODE:(\w+)", prompt)
mode = marker.group(1) if marker else "success"
hang_marker = os.environ.get("FAKE_HANG_MARKER")
if mode == "hang_once" and hang_marker and os.path.exists(hang_marker):
    mode = "success"
with open(os.environ["FAKE_SPAWN_LOG"], "a") as handle:
    handle.write(json.dumps({"harness": harness, "mode": mode,
                             "task": prompt.splitlines()[0]}) + "\n")

def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

def ready():
    if harness == "claude-code":
        emit({"type": "system", "subtype": "init", "tools": ["WebSearch"], "model": "fake"})
    else:
        emit({"type": "thread.started", "thread_id": "t-1"})

def answer(stream_usage=None, final_usage=None, payload=None):
    text = json.dumps(payload or {"answer": "Release 3.2",
                       "citation_urls": ["https://fixture.example/releases/current"]})
    if harness == "claude-code":
        stream_usage = stream_usage or {"input_tokens": 12, "output_tokens": 40}
        final_usage = final_usage or stream_usage
        emit({"type": "assistant", "message": {"id": "msg_final", "role": "assistant",
              "content": [{"type": "text", "text": text}], "usage": stream_usage}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": text, "usage": final_usage})
    else:
        emit({"type": "item.completed", "item": {"id": "item_answer",
              "type": "agent_message", "text": text}})
        emit({"type": "turn.completed",
              "usage": final_usage or {"input_tokens": 1000, "output_tokens": 50}})

def search(index):
    if harness == "claude-code":
        emit({"type": "assistant", "message": {"id": f"msg_s{index}", "role": "assistant",
              "content": [{"type": "tool_use", "id": f"toolu_{index}", "name": "WebSearch",
                           "input": {"query": "release"}}],
              "usage": {"input_tokens": 10, "output_tokens": 5}}})
    else:
        # codex reports one call twice: started, then completed, same item id.
        for kind in ("item.started", "item.completed"):
            emit({"type": kind, "item": {"id": f"ws_{index}", "type": "web_search",
                  "query": "release"}})

ready()
if mode == "success":
    answer()
elif mode == "token_flood":
    for index in range(200):
        if harness == "claude-code":
            emit({"type": "assistant", "message": {"id": f"msg_{index}", "content": [],
                  "usage": {"input_tokens": 5000, "output_tokens": 100}}})
        else:
            emit({"type": "turn.completed", "usage": {"input_tokens": 5000, "output_tokens": 100}})
        time.sleep(0.01)
elif mode == "overspend_final":
    # Streamed snapshots stay small; only the closing usage shows the spend.
    answer(stream_usage={"input_tokens": 10, "output_tokens": 1},
           final_usage={"input_tokens": 9000, "output_tokens": 500})
elif mode == "search_flood":
    for index in range(5):
        search(index)
        time.sleep(0.01)
    answer()
elif mode == "two_searches":
    search(0)
    search(1)
    answer()
elif mode == "two_searches_then_hang":
    search(0)
    search(1)
    with open(hang_marker, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(600)
elif mode == "secret_answer":
    answer(payload={"answer": "Authorization : Basic abc123",
                    "citation_urls": ["https://fixture.example/releases/current"]})
elif mode == "hang_once":
    with open(hang_marker, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(600)
elif mode == "rate_limited_once":
    # The account 429 of 2026-09-29; the next spawn of this cell answers.
    if not os.path.exists(hang_marker):
        with open(hang_marker, "w") as handle:
            handle.write("limited")
        sys.stderr.write("API Error: Request rejected (429) · This request would exceed "
                         "your account's rate limit. Please try again later.\n")
        sys.exit(1)
    answer()
elif mode == "rate_limited":
    # The account 429 of 2026-09-29, on every spawn until the test clears the wall.
    if not os.path.exists(hang_marker + ".cleared"):
        sys.stderr.write("API Error: Request rejected (429) · This request would exceed "
                         "your account's rate limit. Please try again later.\n")
        sys.exit(1)
    answer()
sys.exit(0)
"""

BUDGETS = OperatorBudgets(
    max_provider_calls=100,
    max_provider_result_chars=1_000_000,
    max_total_tokens=1_000_000,
    max_wall_clock_seconds=600,
)


@pytest.fixture()
def fake_harness(tmp_path: Path) -> Path:
    path = tmp_path / "fake-harness"
    path.write_text(f"#!{sys.executable}\n{FAKE_HARNESS}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _environ(fake: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        # A scratch HOME: a codex provider arm copies login state from it.
        "HOME": str(home),
        LIVE_ENV: "1",
        "SEW_CLAUDE_CODE_BIN": str(fake),
        "SEW_CODEX_BIN": str(fake),
        **extra,
    }


def _executor(module_base: Path, fake: Path, tmp_path: Path, **kwargs: Any) -> LiveCellExecutor:
    return LiveCellExecutor(
        module_base=module_base,
        environ=_environ(fake, tmp_path),
        harness_auth="account",
        env={
            "FAKE_SPAWN_LOG": str(tmp_path / "spawns.jsonl"),
            "FAKE_HANG_MARKER": str(tmp_path / "hang.pid"),
        },
        **kwargs,
    )


def _module(tmp_path: Path, modes: dict[str, str] | None = None, **kwargs: Any) -> Path:
    """A tiny suite whose task prompts carry each task's fake-harness mode."""

    modes = modes or {}
    kwargs.setdefault("harnesses", {"claude-code": ["default"]})
    base = _tiny_module(
        tmp_path, tasks=kwargs.pop("tasks", None) or sorted(modes) or None, **kwargs
    )
    for task_dir in (base / "tasks").iterdir():
        mode = modes.get(task_dir.name, "success")
        (task_dir / "prompt.md").write_text(
            f"{task_dir.name}\nFAKE_MODE:{mode}\nAnswer from the evidence.\n",
            encoding="utf-8",
        )
    return base


def _spawns(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "spawns.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _read(run_dir: str | Path, rel: str) -> Any:
    return json.loads((Path(run_dir) / rel).read_text(encoding="utf-8"))


def _by_task(index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {entry["task_id"]: entry for entry in index}


def test_live_run_of_two_cells_completes_and_writes_evidence_bundles(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, harnesses={"claude-code": ["default"], "codex": ["default"]})
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="live-two", operator_budgets=BUDGETS)
    index = _read_index(Path(summary["run_root"]))

    assert summary["status_counts"] == {"succeeded": 2}
    assert summary["duplicate_run_ids"] == []
    assert sorted(spawn["harness"] for spawn in _spawns(tmp_path)) == [
        "claude-code",
        "codex",
    ]
    for entry in index:
        run_dir = Path(entry["run_dir"])
        assert validate_fixture_run(run_dir).run_id == entry["run_id"] == run_dir.name
        run = _read(run_dir, "run.json")
        assert (run["mode"], run["harness_id"], run["status"]) == (
            "live",
            entry["harness_id"],
            "succeeded",
        )
        assert (run_dir / "evidence" / "bundle.yaml").is_file()
        assert _read(run_dir, "metrics/metrics.json")["token_usage"]["accounting_source"] == (
            "measured"
        )
        # The cell ran under its task manifest's budgets, not the driver defaults.
        limits = _read(run_dir, "artifacts/spawn-metadata.json")["limits"]
        assert limits == {
            "timeout_seconds": 120.0,
            "boot_timeout_seconds": 120.0,
            "max_total_tokens": 8000,
            "max_provider_calls": 2,
        }
    state = json.loads((Path(summary["run_root"]) / "runner-state.json").read_text())
    assert state["mode"] == "live"


def test_default_runner_live_mode_no_longer_raises_but_is_operator_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_base = _module(tmp_path)
    monkeypatch.delenv(LIVE_ENV, raising=False)
    runner = SuiteRunner(module_base=module_base, state_root=tmp_path / "state")

    with pytest.raises(RunnerError, match=f"operator-gated; set {LIVE_ENV}=1"):
        runner.run("tiny", mode="live", run_id="gated", operator_budgets=BUDGETS)

    assert isinstance(runner.live_executor, LiveCellExecutor)
    assert not (tmp_path / "state" / ".sew" / "runs" / "gated").exists(), (
        "a refused live run must not create run state"
    )


def test_preflight_refuses_unrunnable_arms_before_any_spawn(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, providers=["native", "exa"])
    executor = LiveCellExecutor(
        module_base=module_base,
        environ={
            **_environ(fake_harness, tmp_path),
            "SEW_CLAUDE_CODE_BIN": "/no/such/claude",
        },
    )
    runner = SuiteRunner(
        module_base=module_base, state_root=tmp_path / "state", live_executor=executor
    )

    with pytest.raises(RunnerError) as refused:
        runner.run("tiny", mode="live", run_id="preflight", operator_budgets=BUDGETS)

    message = str(refused.value)
    assert "claude-code binary '/no/such/claude' is not executable" in message
    assert "provider arms without an MCP server config: exa" in message
    assert _spawns(tmp_path) == []


def test_preflight_refuses_a_provider_config_the_arm_contract_rejects(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, providers=["exa"], harnesses={"codex": ["default"]})
    config = tmp_path / "mcp.yaml"
    config.write_text("exa:\n  command: npx\n  cwd: /tmp\n", encoding="utf-8")
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(
            module_base,
            fake_harness,
            tmp_path,
            provider_exposures=load_provider_exposures(config, {}),
        ),
    )

    with pytest.raises(RunnerError, match="arm codex\\+exa: unsupported MCP server config keys"):
        runner.run("tiny", mode="live", run_id="bad-arm", operator_budgets=BUDGETS)

    assert _spawns(tmp_path) == []


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_cell_exceeding_its_token_budget_terminates_as_budget_exhausted(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    module_base = _module(
        tmp_path,
        {"task-a-v1": "token_flood", "task-b-v1": "success"},
        harnesses={harness_id: ["default"]},
        # Declared retries prove budget exhaustion is terminal, not retried.
        suite_retries={"max_attempts": 2},
    )
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="tokens", operator_budgets=BUDGETS)
    index = _by_task(_read_index(Path(summary["run_root"])))

    flooded = index["task-a-v1"]
    assert (flooded["status"], flooded["failure_category"]) == (
        "budget_exhausted",
        "token_budget_exceeded",
    )
    assert flooded["attempts"] == 1
    process = _read(flooded["run_dir"], "artifacts/spawn-metadata.json")["process"]
    assert process["budget_killed"] is True
    assert process["running_tokens"] > 8000
    assert _read(flooded["run_dir"], "run.json")["status"] == "budget_exhausted"
    # One cell running out of its own budget is not a run stop.
    assert index["task-b-v1"]["status"] == "succeeded"
    assert summary["stopped_reason"] is None


def test_answer_bought_over_budget_is_budget_exhausted_not_succeeded(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, {"task-a-v1": "overspend_final"})
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="overspend", operator_budgets=BUDGETS)
    entry = _read_index(Path(summary["run_root"]))[0]

    assert (entry["status"], entry["failure_category"]) == (
        "budget_exhausted",
        "token_budget_exceeded",
    )
    process = _read(entry["run_dir"], "artifacts/spawn-metadata.json")["process"]
    assert process["budget_killed"] is False, "the streamed snapshots stayed under budget"
    assert _read(entry["run_dir"], "metrics/metrics.json")["token_usage"]["input"] == 9000


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_cell_exceeding_its_provider_call_budget_terminates_as_budget_exhausted(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    module_base = _module(
        tmp_path,
        {"task-a-v1": "search_flood", "task-b-v1": "two_searches"},
        harnesses={harness_id: ["default"]},
    )
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="calls", operator_budgets=BUDGETS)
    index = _by_task(_read_index(Path(summary["run_root"])))

    flooded = index["task-a-v1"]
    assert (flooded["status"], flooded["failure_category"]) == (
        "budget_exhausted",
        "provider_call_budget_exceeded",
    )
    process = _read(flooded["run_dir"], "artifacts/spawn-metadata.json")["process"]
    assert process["provider_budget_killed"] is True
    assert process["provider_calls"] == 3, "the third search is the one over a budget of two"
    # Exactly at budget is fine, and codex's started+completed pair is one call.
    at_budget = index["task-b-v1"]
    assert at_budget["status"] == "succeeded"
    assert (
        _read(at_budget["run_dir"], "artifacts/spawn-metadata.json")["process"]["provider_calls"]
        == 2
    )


def test_a_zero_provider_call_budget_is_spent_by_the_first_search(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(
        tmp_path, {"task-a-v1": "search_flood"}, task_budgets={"max_provider_calls": 0}
    )
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="zero-calls", operator_budgets=BUDGETS)
    entry = _read_index(Path(summary["run_root"]))[0]

    assert (entry["status"], entry["failure_category"]) == (
        "budget_exhausted",
        "provider_call_budget_exceeded",
    )
    assert (
        _read(entry["run_dir"], "artifacts/spawn-metadata.json")["process"]["provider_calls"] == 1
    )


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_max_provider_calls_must_be_a_non_negative_integer(value: object) -> None:
    from sew.harness import HarnessRunConfig
    from sew.schema import SchemaError

    with pytest.raises(SchemaError, match="max_provider_calls must be a non-negative integer"):
        HarnessRunConfig(
            harness_id="codex",
            provider_id="native",
            task_id="current-fact-lookup-v1",
            max_provider_calls=value,  # type: ignore[arg-type]
        )


def test_a_budget_killed_cell_still_charges_the_suite_token_budget(
    tmp_path: Path, fake_harness: Path
) -> None:
    # A cell killed mid-stream never receives the closing usage row, so its
    # measured usage is unknown. The suite budget must charge what the driver
    # metered, or the most expensive cells would be the free ones.
    module_base = _module(
        tmp_path,
        {"task-a-v1": "token_flood", "task-b-v1": "success"},
        suite_budgets={"max_total_tokens": 9000},
    )
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path),
    )

    summary = runner.run("tiny", mode="live", run_id="suite-tokens", operator_budgets=BUDGETS)
    index = _read_index(Path(summary["run_root"]))

    assert [entry["task_id"] for entry in index] == ["task-a-v1"]
    assert _read(index[0]["run_dir"], "metrics/metrics.json")["token_usage"]["input"] is None
    assert summary["stopped_reason"] == "token_budget_exhausted"
    assert len(_spawns(tmp_path)) == 1


def test_interrupt_and_resume_produce_no_duplicate_run_ids(
    tmp_path: Path, fake_harness: Path
) -> None:
    # A wall clock no loaded host reaches first: the hung cell must end by
    # the interrupt, never by timing out before the watcher sees it hang.
    module_base = _module(
        tmp_path,
        {"task-a-v1": "success", "task-b-v1": "hang_once", "task-c-v1": "success"},
        task_budgets={"wall_clock_seconds": 900},
    )
    state_root = tmp_path / "state"
    hang_marker = tmp_path / "hang.pid"

    def _interrupt_once_hung() -> None:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if hang_marker.exists() and hang_marker.read_text(encoding="utf-8"):
                _thread.interrupt_main()
                return
            time.sleep(0.05)

    order = [
        cell["cell_key"]
        for cell in SuiteRunner(module_base=module_base, state_root=state_root).dry_run(
            "tiny", mode="live"
        )["cells"]
    ]
    hung_key = next(key for key in order if key.startswith("task-b-v1|"))
    watcher = threading.Thread(target=_interrupt_once_hung, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        SuiteRunner(
            module_base=module_base,
            state_root=state_root,
            live_executor=_executor(module_base, fake_harness, tmp_path),
        ).run("tiny", mode="live", run_id="interrupt", operator_budgets=BUDGETS)
    watcher.join(timeout=5)

    run_root = state_root / ".sew" / "runs" / "interrupt"
    interrupted = _read_index(run_root)
    # Ctrl-C lands mid-cell: that cell is not an outcome, and its child is gone.
    assert [entry["cell_key"] for entry in interrupted] == order[: order.index(hung_key)]
    _assert_dead(int(hang_marker.read_text(encoding="utf-8")))

    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="interrupt", operator_budgets=BUDGETS)
    index = _read_index(run_root)

    assert resumed["remaining_cells"] == 0
    assert resumed["duplicate_run_ids"] == []
    assert [entry["cell_key"] for entry in index] == order
    assert len({entry["run_id"] for entry in index}) == len(index) == 3
    assert len({entry["run_dir"] for entry in index}) == 3
    assert {entry["status"] for entry in index} == {"succeeded"}
    # Completed cells ran once; only the interrupted cell ran again, under a
    # fresh id beside the partial directory it left behind (never deleted).
    spawns = [spawn["task"] for spawn in _spawns(tmp_path)]
    assert sorted(spawns) == ["task-a-v1", "task-b-v1", "task-b-v1", "task-c-v1"]
    rerun = _by_task(index)["task-b-v1"]
    assert rerun["run_id"].endswith("-r2")
    partial = Path(rerun["run_dir"]).with_name(rerun["run_id"].removesuffix("-r2"))
    assert partial.is_dir() and not (partial / "run.json").exists()


def test_interrupted_live_cell_spend_is_charged_on_resume(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, {"task-a-v1": "two_searches_then_hang"})
    state_root = tmp_path / "state"
    hang_marker = tmp_path / "hang.pid"
    tight_budgets = OperatorBudgets(
        max_provider_calls=2,
        max_provider_result_chars=1_000_000,
        max_total_tokens=1_000_000,
        max_wall_clock_seconds=600,
    )

    def _interrupt_once_metered() -> None:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if hang_marker.exists() and hang_marker.read_text(encoding="utf-8"):
                _thread.interrupt_main()
                return
            time.sleep(0.05)

    watcher = threading.Thread(target=_interrupt_once_metered, daemon=True)
    watcher.start()
    with pytest.raises(KeyboardInterrupt):
        SuiteRunner(
            module_base=module_base,
            state_root=state_root,
            live_executor=_executor(module_base, fake_harness, tmp_path),
        ).run("tiny", mode="live", run_id="partial-spend", operator_budgets=tight_budgets)
    watcher.join(timeout=5)

    run_root = state_root / ".sew" / "runs" / "partial-spend"
    partials = list((run_root / "bundles").iterdir())
    assert len(partials) == 1
    provisional = _read(partials[0], "artifacts/provisional-meters.json")
    assert provisional["process"]["provider_calls"] == 2
    _assert_dead(int(hang_marker.read_text(encoding="utf-8")))

    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="partial-spend", operator_budgets=tight_budgets)

    assert resumed["completed_cells"] == 0
    assert resumed["stopped_reason"] == "provider_call_budget_exhausted"
    assert [spawn["task"] for spawn in _spawns(tmp_path)] == ["task-a-v1"]


class _CrashAfterBundle:
    """Crashes after a cell's bundle is written but before it is indexed."""

    def __init__(self, inner: LiveCellExecutor, crash_on: str) -> None:
        self.inner = inner
        self.crash_on = crash_on

    def preflight(self, cells: list[MatrixCell], *, mode: str) -> None:
        self.inner.preflight(cells, mode=mode)

    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        execution = self.inner.execute(cell, output_root, mode=mode)
        if cell.task_id == self.crash_on:
            raise RuntimeError("simulated crash between bundle write and index persist")
        return execution


def test_resume_adopts_a_completed_bundle_the_crash_left_unindexed(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, {"task-a-v1": "success", "task-b-v1": "success"})
    state_root = tmp_path / "state"
    order = [
        cell["task_id"]
        for cell in SuiteRunner(module_base=module_base, state_root=state_root).dry_run(
            "tiny", mode="live"
        )["cells"]
    ]
    crashing = _CrashAfterBundle(_executor(module_base, fake_harness, tmp_path), order[1])

    with pytest.raises(RuntimeError, match="simulated crash"):
        SuiteRunner(module_base=module_base, state_root=state_root, live_executor=crashing).run(
            "tiny", mode="live", run_id="adopt", operator_budgets=BUDGETS
        )
    assert len(_read_index(state_root / ".sew" / "runs" / "adopt")) == 1

    summary = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="adopt", operator_budgets=BUDGETS)
    index = _read_index(Path(summary["run_root"]))

    assert [spawn["task"] for spawn in _spawns(tmp_path)] == order, "no cell ran twice"
    assert summary["duplicate_run_ids"] == []
    adopted = index[1]
    assert adopted["task_id"] == order[1]
    assert Path(adopted["run_dir"]).name == adopted["run_id"]
    assert not adopted["run_id"].endswith("-r2")
    assert not list(Path(summary["output_root"]).glob("*-r2"))


def test_resume_reruns_a_provider_unavailable_bundle_instead_of_adopting_it(
    tmp_path: Path, fake_harness: Path
) -> None:
    """A 429 cell carries no answer; after the runner unindexes it, resume must re-run it."""

    module_base = _module(tmp_path, {"task-a-v1": "rate_limited_once"})
    state_root = tmp_path / "state"
    first = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="rerun-429", operator_budgets=BUDGETS)
    run_root = Path(first["run_root"])
    index = _read_index(run_root)
    assert [entry["status"] for entry in index] == ["provider_unavailable"]

    # Unindex it the way a streak stop does, then resume.
    state = json.loads((run_root / "runner-state.json").read_text(encoding="utf-8"))
    state["index"] = []
    (run_root / "runner-state.json").write_text(json.dumps(state), encoding="utf-8")
    (run_root / "run-index.json").write_text("[]", encoding="utf-8")

    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="rerun-429", operator_budgets=BUDGETS)
    index = _read_index(Path(resumed["run_root"]))

    assert [entry["status"] for entry in index] == ["succeeded"]
    assert index[0]["run_id"].endswith("-r2"), "re-ran under the next free id, not adopted"
    assert len(_spawns(tmp_path)) == 2
    # No streak recorded the walled bundle, yet it stays on the cell's entry.
    assert "unavailable_deferrals" not in index[0]
    assert [Path(path).name for path in index[0]["attempt_run_dirs"]] == [
        index[0]["run_id"].removesuffix("-r2"),
        index[0]["run_id"],
    ]


def test_live_provider_unavailable_streak_stops_and_resume_reruns_the_cells(
    tmp_path: Path, fake_harness: Path
) -> None:
    """The 2026-09-29 wall end to end: three 429 cells stop the suite; resume re-runs them."""

    tasks = ["task-a-v1", "task-b-v1", "task-c-v1"]
    module_base = _module(tmp_path, dict.fromkeys(tasks, "rate_limited"))
    state_root = tmp_path / "state"
    first = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="live-streak", operator_budgets=BUDGETS)

    assert first["stopped_reason"] == "provider_unavailable"
    assert _read_index(Path(first["run_root"])) == []
    assert len(_spawns(tmp_path)) == 3
    output_root = Path(first["output_root"])
    walled_bundles = sorted(path.name for path in output_root.iterdir())
    assert len(walled_bundles) == 3

    (tmp_path / "hang.pid.cleared").write_text("", encoding="utf-8")
    resumed = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="live-streak", operator_budgets=BUDGETS)
    index = _read_index(Path(resumed["run_root"]))

    assert resumed["stopped_reason"] is None
    assert [entry["status"] for entry in index] == ["succeeded"] * 3
    assert len(_spawns(tmp_path)) == 6
    for entry in index:
        assert entry["run_id"].endswith("-r2"), "re-ran under the next free id, not adopted"
        assert entry["unavailable_deferrals"] == 1
        # The walled bundle stays on the entry as evidence and spend.
        assert [Path(path).name for path in entry["attempt_run_dirs"]] == [
            entry["run_id"].removesuffix("-r2"),
            entry["run_id"],
        ]
    assert sorted(Path(entry["attempt_run_dirs"][0]).name for entry in index) == walled_bundles
    state = json.loads((Path(resumed["run_root"]) / "runner-state.json").read_text("utf-8"))
    assert state["deferred_unavailable"] == {}


def test_resume_rejects_secret_contaminated_completed_bundle(
    tmp_path: Path, fake_harness: Path
) -> None:
    module_base = _module(tmp_path, {"task-a-v1": "secret_answer"})
    state_root = tmp_path / "state"

    with pytest.raises(SchemaError, match="secret-like material"):
        SuiteRunner(
            module_base=module_base,
            state_root=state_root,
            live_executor=_executor(module_base, fake_harness, tmp_path),
        ).run("tiny", mode="live", run_id="secret-adopt", operator_budgets=BUDGETS)

    run_root = state_root / ".sew" / "runs" / "secret-adopt"
    assert _read_index(run_root) == []
    (module_base / "tasks" / "task-a-v1" / "prompt.md").write_text(
        "task-a-v1\nFAKE_MODE:success\nAnswer from the evidence.\n",
        encoding="utf-8",
    )

    summary = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", run_id="secret-adopt", operator_budgets=BUDGETS)
    index = _read_index(Path(summary["run_root"]))

    assert [spawn["mode"] for spawn in _spawns(tmp_path)] == [
        "secret_answer",
        "success",
    ]
    assert index[0]["status"] == "succeeded"
    assert index[0]["run_id"].endswith("-r2")


def test_seeded_order_is_stable_across_resumes(tmp_path: Path, fake_harness: Path) -> None:
    modes = {"task-a-v1": "success", "task-b-v1": "success", "task-c-v1": "success"}
    harnesses = {"claude-code": ["default"], "codex": ["default"]}
    module_base = _module(tmp_path / "suite", modes, harnesses=harnesses)

    def _run(state: str, **kwargs: Any) -> dict[str, Any]:
        return SuiteRunner(
            module_base=module_base,
            state_root=tmp_path / state,
            live_executor=_executor(module_base, fake_harness, tmp_path / state),
        ).run(
            "tiny",
            mode="live",
            run_id="seeded",
            seed=11,
            operator_budgets=BUDGETS,
            **kwargs,
        )

    (tmp_path / "whole").mkdir()
    (tmp_path / "stepped").mkdir()
    whole = _read_index(Path(_run("whole")["run_root"]))
    steps = []
    while True:
        summary = _run("stepped", max_cells=2)
        steps.append(summary["stopped_reason"])
        if summary["remaining_cells"] == 0:
            break
    stepped = _read_index(Path(summary["run_root"]))
    planned = [
        cell["cell_key"]
        for cell in SuiteRunner(module_base=module_base, state_root=tmp_path / "dry").dry_run(
            "tiny", seed=11, mode="live"
        )["cells"]
    ]

    assert steps == ["interrupted", "interrupted", None]
    assert [e["cell_key"] for e in stepped] == [e["cell_key"] for e in whole] == planned
    assert [e["run_id"] for e in stepped] == [e["run_id"] for e in whole]
    spawned = [(s["harness"], s["task"]) for s in _spawns(tmp_path / "stepped")]
    assert spawned == [(s["harness"], s["task"]) for s in _spawns(tmp_path / "whole")]
    with pytest.raises(RunnerError, match="suite order changed"):
        SuiteRunner(
            module_base=module_base,
            state_root=tmp_path / "stepped",
            live_executor=_executor(module_base, fake_harness, tmp_path / "stepped"),
        ).run("tiny", mode="live", run_id="seeded", seed=12, operator_budgets=BUDGETS)


def test_a_fixture_run_cannot_be_resumed_in_live_mode(tmp_path: Path, fake_harness: Path) -> None:
    # Fixture cells read their prompt from the real catalog, so use a real task.
    module_base = _module(tmp_path, harnesses={"claude-code": ["default"], "codex": ["default"]})
    state_root = tmp_path / "state"
    SuiteRunner(module_base=module_base, state_root=state_root).run(
        "tiny", run_id="mixed", max_cells=1
    )

    with pytest.raises(RunnerError, match="started in fixture mode, not live"):
        SuiteRunner(
            module_base=module_base,
            state_root=state_root,
            live_executor=_executor(module_base, fake_harness, tmp_path),
        ).run("tiny", mode="live", run_id="mixed", operator_budgets=BUDGETS)
    assert _spawns(tmp_path) == []

    # Without an explicit id, live and fixture runs of one suite never collide.
    live = SuiteRunner(
        module_base=module_base,
        state_root=state_root,
        live_executor=_executor(module_base, fake_harness, tmp_path),
    ).run("tiny", mode="live", operator_budgets=BUDGETS)
    fixture = SuiteRunner(module_base=module_base, state_root=state_root).run("tiny")
    assert live["suite_run_id"].startswith("tiny-live-")
    assert live["suite_run_id"] != fixture["suite_run_id"]


def test_pi_live_requires_catalog_model_before_spawn(tmp_path: Path, fake_harness: Path) -> None:
    module_base = _module(tmp_path, providers=["exa"], harnesses={"pi": ["oss-small"]})
    config = tmp_path / "mcp.yaml"
    config.write_text("exa:\n  command: /usr/bin/true\n")
    executor = _executor(module_base, fake_harness, tmp_path,
                         provider_exposures=load_provider_exposures(config, {}))
    executor._environ["SEW_OSS_ENABLED"] = "1"
    runner = SuiteRunner(module_base=module_base, state_root=tmp_path / "state",
                         live_executor=executor)
    with pytest.raises(RunnerError, match="only model_profile"):
        runner.run("tiny", mode="live", run_id="pi", operator_budgets=BUDGETS)
    assert not _spawns(tmp_path)


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_provider_arm_cell_runs_with_its_configured_mcp_server(
    tmp_path: Path, fake_harness: Path, harness_id: str
) -> None:
    module_base = _module(tmp_path, providers=["exa"], harnesses={harness_id: ["default"]})
    config = tmp_path / "mcp.yaml"
    config.write_text(
        "exa:\n  command: /usr/bin/true\n  env: {EXA_API_KEY: '${SEW_TEST_EXA_KEY}'}\n",
        encoding="utf-8",
    )
    exposures = load_provider_exposures(config, {"SEW_TEST_EXA_KEY": "exa-secret-value-123"})
    runner = SuiteRunner(
        module_base=module_base,
        state_root=tmp_path / "state",
        live_executor=_executor(module_base, fake_harness, tmp_path, provider_exposures=exposures),
    )

    summary = runner.run("tiny", mode="live", run_id="exa", operator_budgets=BUDGETS)
    entry = _read_index(Path(summary["run_root"]))[0]

    # This fake harness never initializes its configured /usr/bin/true server.
    assert entry["status"] == "provider_unavailable"
    assert entry["failure_category"] == "provider_tools_unavailable"
    contract = _read(entry["run_dir"], "artifacts/spawn-metadata.json")["arm_contract"]
    assert contract == {
        "kind": "provider",
        "provider_id": "exa",
        "allowed_mcp_servers": ["exa"],
        "native_search": False,
    }
    bundle_text = "\n".join(
        path.read_text(encoding="utf-8") for path in Path(entry["run_dir"]).rglob("*.*")
    )
    assert "exa-secret-value-123" not in bundle_text


def test_provider_mcp_config_resolves_env_references_and_fails_closed(
    tmp_path: Path,
) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"firecrawl": {"url": "https://mcp.example/${SEW_FC_KEY}/sse"}}),
        encoding="utf-8",
    )

    exposure = load_provider_exposures(config, {"SEW_FC_KEY": "fc-key"})["firecrawl"]
    assert exposure.mcp_server_name == "firecrawl"
    assert exposure.mcp_server_config == {"url": "https://mcp.example/fc-key/sse"}

    with pytest.raises(RunnerError, match=r"references \$\{SEW_FC_KEY\}, which is unset"):
        load_provider_exposures(config, {})
    config.write_text(json.dumps({"google": {"command": "x"}}), encoding="utf-8")
    with pytest.raises(RunnerError, match="unknown provider arm 'google'"):
        load_provider_exposures(config, {})


def test_cli_live_run_is_refused_without_the_operator_gate(tmp_path: Path) -> None:
    env = {
        "SEW_OSS_ENABLED": "1",
        "PYTHONPATH": str(LIB_PYTHON),
        "PATH": os.environ["PATH"],
        "HOME": str(Path.home()),
        "SEW_STATE_ROOT": str(tmp_path),
    }
    argv = [
        sys.executable,
        "-m",
        "sew.cli",
        "run",
        "--mode",
        "live",
        "--state-root",
        str(tmp_path),
    ]
    budgets = [
        "--max-provider-calls",
        "10",
        "--max-provider-result-chars",
        "1000",
        "--max-total-tokens",
        "1000",
        "--max-wall-clock-seconds",
        "60",
    ]

    refused = subprocess.run(
        argv + budgets, cwd=MODULE_ROOT, env=env, text=True, capture_output=True
    )

    assert refused.returncode == 2
    assert f"set {LIVE_ENV}=1" in refused.stderr
    assert not (tmp_path / ".sew").exists()


def _assert_dead(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail(f"harness pid {pid} outlived its interrupted cell")
