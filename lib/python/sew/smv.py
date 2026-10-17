"""Supplemental offline proofs for the canonical Searchlight SMV runner.

The registered runner in scripts/smoke.py owns isolation, deadlines and receipts.
"""

import contextlib
import io
import json
from pathlib import Path
import runpy
import shutil
import time

ROOT = Path(__file__).resolve().parents[3]


def exercise(scratch):
    from sew.cli import main
    from sew.evaluator import evaluate_run, load_evaluation_input
    from sew.harness import HarnessRunConfig, run_harness
    from sew.runner import SuiteRunner
    from sew.schema import validate_fixture_run

    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):
        assert main(["run-fixture-harnesses", "--output-root", str(scratch / "bundles")]) == 0
    bundles = sorted((scratch / "bundles").iterdir())
    assert len(bundles) == 4
    assert all(validate_fixture_run(b).status == "succeeded" for b in bundles)
    yield "run", {"bundles": 4, "statuses": ["succeeded"]}, time.monotonic() - started
    started = time.monotonic()
    success = scratch / "accepted-source-fixture"
    shutil.copytree(ROOT / "fixtures/runs/success", success)
    data = load_evaluation_input(success, ROOT / "tasks/current-fact-lookup-v1")
    record = evaluate_run(data)
    assert record["outcome"] == "pass", record
    failed = run_harness(
        HarnessRunConfig("codex", "native", "current-fact-lookup-v1", fixture_outcome="timeout"),
        scratch / "negative",
    )
    assert failed.status == "timeout"
    negative = evaluate_run(
        load_evaluation_input(failed.bundle_dir, ROOT / "tasks/current-fact-lookup-v1")
    )
    assert negative["outcome"] == "fail"
    yield (
        "evaluate",
        {"success": record["outcome"], "timeout": negative["outcome"]},
        time.monotonic() - started,
    )
    started = time.monotonic()
    runner = SuiteRunner(state_root=scratch / "state")
    first = runner.run(max_cells=1, run_id="smv", resume=False)
    resumed = runner.run(max_cells=1, run_id="smv", resume=True)
    index = json.loads((Path(resumed["run_root"]) / "run-index.json").read_text())
    assert len(index) == 2 and len({x["cell_key"] for x in index}) == 2, index
    yield (
        "resume",
        {
            "first": first["completed_cells"],
            "resumed": resumed["completed_cells"],
            "uniqueCells": 2,
        },
        time.monotonic() - started,
    )


def main():
    """Keep the original entry point on the root-anchored canonical runner."""
    return runpy.run_path(str(ROOT / "scripts/smoke.py"))["main"]()


def architecture_index(doc):
    text = "# Architecture state index\n\nGenerated with the Agent OS shared renderer from `state-machines.json`.\n\n| Object | Owner | States | Edges | Missing enforcement states |\n|---|---|---:|---:|---:|\n"
    for obj in doc["objects"]:
        text += f"| `{obj['id']}` | [tools/searchlight](../../README.md) | {len(obj['states'])} | {len(obj['transitions'])} | {sum(s.get('enforcement') == 'missing' for s in obj['states'])} |\n"
    return text


def render():
    from cwp_dispatch.smv.render import START, END, diagrams

    doc = json.loads((ROOT / "state-machines.json").read_text())
    readme = ROOT / "README.md"
    before, tail = readme.read_text().split(START, 1)
    _, after = tail.split(END, 1)
    readme.write_text(before + diagrams(doc) + after)
    (ROOT / "docs/architecture/STATE-MACHINES.md").write_text(architecture_index(doc))
