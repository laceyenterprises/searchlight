"""SMV source adapters and synthetic evaluation proof for the registered owner.

Agent OS supplies the shared contracts, renderer and bounded process runner.
No agent harness or provider is started by this module.
"""

import ast
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OWNER = "tools/searchlight"
ENUMS = {
    "evaluation-run": "RUN_STATUSES",
    "provider-call": "PROVIDER_CALL_STATUSES",
    "evaluation": "EVALUATION_OUTCOMES",
}
# Independent code seams: real writer names and control-flow anchors.
WRITERS = {
    "harness.py": ["run_fixture_harness", "classify_fixture_outcome"],
    "runner.py": [
        "SuiteRunner._write_state",
        "SuiteRunner._index_entry",
        "_complete_live_bundle",
        "normalize_runner_status",
    ],
    "evaluator.py": ["evaluate_run"],
    "gap/runner.py": ["_run_locked"],
}
ANCHORS = {
    "harness.py": [
        "'status': terminal.status",
        "return TerminalStatus('timeout', TERMINAL_TIMEOUT)",
    ],
    "runner.py": ["atomic_write_json(path, state)", "'status': status"],
    "evaluator.py": ["'outcome': 'pass' if all(builder.dimensions.values()) else 'fail'"],
    "gap/runner.py": [
        "record['status'] = status",
        "status = 'contaminated'",
        "status = 'budget_exhausted'",
    ],
}


def inventory(overrides=None):
    """All status enums, guards and writer expressions, with exact source selectors."""
    found = {}
    for path in sorted((ROOT / "lib/python/sew").rglob("*.py")):
        if path.name == "smv.py":
            continue
        relative = str(path.relative_to(ROOT))
        tree = ast.parse((overrides or {}).get(relative, path.read_text()))
        rows = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Compare, ast.Return)):
                text = ast.unparse(node)
                if re.search(
                    r"\b(?:status|state|phase|outcome|stopped_reason)\b|[A-Z_]+(?:STATUSES|STATES|OUTCOMES)",
                    text,
                ):
                    rows.append(text)
        if rows:
            found[relative] = sorted(rows)
    return found


def actual(overrides=None):
    from cwp_dispatch.smv.source_contract import python_function

    def source(file):
        relative = "lib/python/sew/" + file
        return (overrides or {}).get(relative, (ROOT / relative).read_text())

    for file, selectors in WRITERS.items():
        body = "\n".join(python_function(source(file), s) for s in selectors)
        assert all(anchor in body for anchor in ANCHORS[file]), file + " writer drift"
    tree = ast.parse(source("schema.py"))
    symbols = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", "") == "frozenset":
                symbols[node.targets[0].id] = set(ast.literal_eval(node.value.args[0]))
    result = {}
    for name, enum in ENUMS.items():
        names = symbols[enum]
        result[name] = (
            names,
            {(name + ":" + s, "[*]", s) for s in names}
            | {(name + ":end:" + s, s, "[*]") for s in names},
        )
    names, run_edges = result["evaluation-run"]
    run_edges.update(("reclassify-contaminated:" + s, s, "contaminated") for s in names)
    run_edges.update(
        ("reclassify-provider:" + s, s, "provider_unavailable")
        for s in ("succeeded", "failed", "timeout", "harness_boot_failed")
    )
    run_edges.update(
        {
            ("normalize-unsupported", "unsupported", "not_applicable"),
            ("reclassify-budget", "succeeded", "budget_exhausted"),
        }
    )
    result["suite-progress"] = (
        {"pending", "recorded", "deferred"},
        {
            ("suite-create", "[*]", "pending"),
            ("suite-record", "pending", "recorded"),
            ("suite-next", "recorded", "pending"),
            ("suite-defer", "recorded", "deferred"),
            ("suite-resume", "deferred", "pending"),
            ("suite-end", "recorded", "[*]"),
        },
    )
    return result


def verify(overrides=None):
    from cwp_dispatch.smv.contracts import states
    from cwp_dispatch.smv.render import START, END, diagrams

    doc = states(
        json.loads(
            (overrides or {}).get("state-machines.json", (ROOT / "state-machines.json").read_text())
        )
    )
    declared = {
        o["id"].removeprefix("searchlight."): (
            {s["name"] for s in o["states"]},
            {(e["id"], e["from"], e["to"]) for e in o["transitions"]},
        )
        for o in doc["objects"]
    }
    assert declared == actual(overrides), "state/edge drift"
    assert doc["sourceInventory"] == inventory(overrides), "status writer inventory drift"
    for obj in doc["objects"]:
        for state in obj["states"]:
            if not state["terminal"]:
                assert state["maxDurationSeconds"] > 0 and state["overdueRecipient"]
    readme = (ROOT / "README.md").read_text()
    assert START + readme.split(START)[1].split(END)[0] + END == diagrams(doc)


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
    from cwp_dispatch.smv.contracts import acceptance
    from cwp_dispatch.smv.smoke import sandbox_env

    doc = json.loads((ROOT / "post-merge-verify.json").read_text())
    mode, commit = os.environ.get("SMV_MODE", "sandbox"), os.environ.get("SMV_COMMIT", "unknown")
    begin, rows, error = time.monotonic(), [], None
    previous = dict(os.environ)
    try:
        if mode != "sandbox":
            raise RuntimeError(
                "host-unavailable: SRE inspects authoritative recent evaluation evidence; no paid run"
            )
        with tempfile.TemporaryDirectory(prefix="smv-searchlight-") as directory:
            scratch = Path(directory)
            # This command is launched by Agent OS from its registered root.
            env = sandbox_env(Path.cwd(), scratch, commit)
            env["SEW_MODE"] = "standalone"
            env["SEW_STATE_ROOT"] = str(scratch / "state")
            os.environ.clear()
            os.environ.update(env)

            def deny(*args, **kwargs):
                raise AssertionError("external network prohibited")

            with (
                patch.object(socket.socket, "connect", deny),
                patch("urllib.request.urlopen", deny),
            ):
                for row in exercise(scratch):
                    rows.append(row)
        assert not scratch.exists(), "scratch teardown incomplete"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)[:800]
    finally:
        os.environ.clear()
        os.environ.update(previous)
    observed = {id: (evidence, duration) for id, evidence, duration in rows}
    steps = []
    for spec in doc["steps"]:
        evidence, duration = observed.get(spec["id"], ({"error": error or "missing evidence"}, 0))
        verdict = "pass" if spec["id"] in observed else "unavailable" if mode == "host" else "fail"
        steps.append(
            dict(
                **spec,
                verdict=verdict,
                observed=evidence,
                expected=spec["acceptance"],
                evidence=evidence,
                durationSeconds=duration,
                exclusions=doc["modes"][mode]["exclusions"],
            )
        )
    if error and steps and all(row["verdict"] == "pass" for row in steps):
        steps[-1].update(verdict="fail", evidence={"cleanupError": error}, observed=error)
    for row in steps:
        print(
            f"{row['verdict'].upper()} searchlight/{row['id']} promise={row['promise']} edges={','.join(row['edges'])}"
        )
    report = dict(
        module="searchlight",
        mode=mode,
        commit=commit,
        verdict="fail" if error else "pass",
        durationSeconds=time.monotonic() - begin,
        steps=steps,
    )
    code = 1 if error else 0
    try:
        acceptance(doc, report, code, mode=mode, commit=commit)
    except ValueError as exc:
        report.update(verdict="fail", contractError=str(exc))
        code = 1
    print(json.dumps(report, sort_keys=True))
    return code


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
