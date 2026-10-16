"""Bounded synthetic Searchlight entry points; no live runtime/credential fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))


def exercise(step, scratch):
    from sew.harness import run_acceptance_fixture_matrix
    from sew.runner import SuiteRunner
    from sew.schema import validate_fixture_run

    sys.path.insert(0, str(ROOT / "tests"))
    from test_runner import _tiny_module, RecordingExecutor

    if step == "fixture.matrix":
        results = run_acceptance_fixture_matrix(scratch / "bundles")
        assert len(results) == 4
        for result in results:
            assert result.status == "succeeded"
            validated = validate_fixture_run(result.bundle_dir)
            assert validated.run_id
        return {
            "bundles": len(results),
            "source": "existing synthetic acceptance matrix",
            "validated": True,
        }
    if step == "index.resume":
        module = _tiny_module(scratch, providers=["native"], harnesses={"codex": ["default"]})
        runner = SuiteRunner(module_base=module, state_root=scratch / "state")
        first = runner.run("tiny", run_id="synthetic", resume=False)
        path = Path(first["run_root"]) / "runner-state.json"
        before = json.loads(path.read_text())["index"]
        assert len(before) == 1 and before[0]["status"] == "succeeded"
        validate_fixture_run(Path(before[0]["run_dir"]))
        second = runner.run("tiny", run_id="synthetic", resume=True)
        after = json.loads(path.read_text())["index"]
        assert before == after and second["remaining_cells"] == 0
        return {"indexEntries": len(after), "resumeUnchanged": True, "synthetic": True}
    if step == "index.defer":
        module = _tiny_module(
            scratch / "wall",
            providers=["native", "no-search"],
            harnesses={"codex": ["default"], "claude-code": ["default"]},
        )
        state = scratch / "wall-state"
        walled = RecordingExecutor(["provider_unavailable"] * 4)
        first = SuiteRunner(module_base=module, state_root=state, executor=walled).run(
            "tiny", run_id="synthetic-wall", resume=False
        )
        assert first["stopped_reason"] == "provider_unavailable" and len(walled.calls) == 3
        assert first["deferred_unavailable_cells"] == 3 and first["remaining_cells"] == 4
        healthy = RecordingExecutor()
        resumed = SuiteRunner(module_base=module, state_root=state, executor=healthy).run(
            "tiny", run_id="synthetic-wall", resume=True
        )
        index = json.loads((Path(resumed["run_root"]) / "runner-state.json").read_text())["index"]
        assert resumed["remaining_cells"] == 0 and resumed["deferred_unavailable_cells"] == 0
        assert len(healthy.calls) == 4 and len({e["cell_key"] for e in index}) == 4
        assert {e["status"] for e in index} == {"succeeded"}
        return {
            "wallVerdict": "provider_unavailable",
            "deferred": 3,
            "resumed": len(index),
            "executor": "synthetic",
        }
    raise ValueError("missing step: " + step)


def run(mode="sandbox", commit="synthetic", fail_step=None):
    doc = json.loads((ROOT / "post-merge-verify.json").read_text())
    started = time.monotonic()
    rows = []

    def deny(*args, **kwargs):
        raise RuntimeError("sandbox attempted network")

    with (
        tempfile.TemporaryDirectory(prefix="searchlight-smv-") as tmp,
        patch.object(socket.socket, "connect", deny),
    ):
        for step in doc["steps"]:
            tick = time.monotonic()
            verdict = "pass" if mode == "sandbox" else "unavailable"
            try:
                if mode != "sandbox":
                    evidence = {"reason": doc["modes"]["host"]["exclusions"][0]}
                elif fail_step == step["id"]:
                    raise RuntimeError("synthetic required-step failure")
                else:
                    evidence = exercise(step["id"], Path(tmp))
            except Exception as exc:
                verdict, evidence = "fail", {"error": f"{type(exc).__name__}: {exc}"}
            rows.append(
                {
                    **{k: step[k] for k in ("id", "promise", "edges")},
                    "verdict": verdict,
                    "observed": evidence,
                    "expected": "README entry-point assertions",
                    "evidence": evidence,
                    "durationSeconds": time.monotonic() - tick,
                    "exclusions": doc["modes"].get(mode, doc["modes"]["host"])["exclusions"],
                }
            )
    return {
        "module": doc["module"],
        "mode": mode,
        "commit": commit,
        "verdict": "pass" if all(r["verdict"] == "pass" for r in rows) else "fail",
        "durationSeconds": time.monotonic() - started,
        "steps": rows,
    }


def main():
    mode = os.environ.get("SMV_MODE", "sandbox")
    commit = (
        os.environ.get("SMV_COMMIT")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
    )
    # Allowlist before importing owner code. The harness itself never inherits
    # DSNs, provider/model keys, notification URLs or an operator HOME.
    clean = {
        k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "PYTHONUSERBASE") if k in os.environ
    }

    def overdue(*_):
        raise TimeoutError("180-second invocation budget exceeded")

    previous = signal.signal(signal.SIGALRM, overdue)
    signal.setitimer(signal.ITIMER_REAL, 175)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="searchlight-home-") as home,
            patch.dict(
                os.environ,
                {**clean, "HOME": home, "AGENT_OS_SESSION_LEDGER_VDB": "false"},
                clear=True,
            ),
        ):
            report = run(mode=mode, commit=commit)
        for row in report["steps"]:
            print(
                f"{row['verdict'].upper()} {report['module']}/{row['id']} promise={row['promise']} edges={','.join(row['edges'])}"
            )
        print(json.dumps(report))
        return 0 if report["verdict"] == "pass" else 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == "__main__":
    sys.stdin.close()
    raise SystemExit(main())
