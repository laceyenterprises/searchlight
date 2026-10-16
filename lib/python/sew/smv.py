"""SMV-18 source audit and offline acceptance, independent of host SDK imports."""

import ast
import copy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[3]
START = "<!-- smv:state-machines:start -->"
END = "<!-- smv:state-machines:end -->"
OWNER = "tools/searchlight"
# Desired lifecycle projections, derived observations explicitly named below.
# Every selector is checked against actual code; sibling guards/writers are
# separately audited. Neither projection nor inventory reads the definition.
MODELS = [
    (
        "suite",
        "runner.py",
        "queued running paused !completed",
        [
            ("[*]", "queued", '"index": []'),
            ("queued", "running", "for cell in cells:"),
            ("running", "paused", 'stopped_reason = "interrupted"'),
            ("running", "paused", "stopped_reason = budget_reason"),
            ("running", "paused", 'stopped_reason = post_reason or "provider_unavailable"'),
            ("paused", "running", "if resume and state_path.exists():"),
            ("running", "completed", 'state["summary"] = summarize_index('),
        ],
    ),
    (
        "retry",
        "runner.py",
        "attempt retrying !returned",
        [
            ("[*]", "attempt", "attempts = 1"),
            (
                "attempt",
                "retrying",
                "while last.status in RETRYABLE_STATUSES and attempts < max_attempts:",
            ),
            ("retrying", "retrying", "attempts += 1"),
            ("retrying", "returned", "stopped_reason = budgets.can_start(task)"),
            ("attempt", "returned", "return CellExecution("),
            ("retrying", "returned", "return CellExecution("),
        ],
    ),
    (
        "deferral",
        "runner.py",
        "eligible deferred !exhausted",
        [
            ("[*]", "eligible", "deferral = deferred.pop(cell.key, None)"),
            ("eligible", "deferred", "index.remove(failed)"),
            ("deferred", "eligible", "deferral = deferred.pop(cell.key, None)"),
            (
                "eligible",
                "exhausted",
                'elif entry.get("unavailable_deferrals", 0) < PROVIDER_UNAVAILABLE_MAX_DEFERRALS:',
            ),
            ("deferred", "deferred", "deferred[cell.key] = deferral"),
        ],
    ),
    (
        "availability",
        "mcp_meter.py",
        "unknown available unavailable",
        [
            ("[*]", "unknown", 'availability="unknown"'),
            *[
                (a, b, 'snapshot["availability"] = (')
                for a in ("unknown", "available", "unavailable")
                for b in ("unknown", "available", "unavailable")
            ],
        ],
    ),
    (
        "calibration",
        "gap/calibrate.py",
        "measuring admitted !rejected !retired",
        [
            ("[*]", "measuring", "def calibrate("),
            (
                "measuring",
                "admitted",
                '"verdict": "retired" if retired else "admitted" if admitted else "rejected"',
            ),
            (
                "measuring",
                "rejected",
                '"verdict": "retired" if retired else "admitted" if admitted else "rejected"',
            ),
            ("admitted", "measuring", "def recalibrate("),
            ("measuring", "retired", "retired = floor_rate > max_floor"),
        ],
    ),
    (
        "gap-run",
        "gap/runner.py",
        "running paused !completed",
        [
            ("[*]", "running", "stopped_reason=None"),
            ("running", "paused", 'state["stopped_reason"] = "provider_unavailable_streak"'),
            ("running", "paused", 'state["stopped_reason"] = "cancelled"'),
            ("paused", "running", 'state["stopped_reason"] = None'),
            ("running", "completed", "return state, destination"),
        ],
    ),
]
VOCABS = [
    ("cell", "RUN_STATUSES"),
    ("provider-call", "PROVIDER_CALL_STATUSES"),
    ("evaluation", "EVALUATION_OUTCOMES"),
]


def read(file, overrides=None):
    key = "lib/python/sew/" + file
    return (overrides or {}).get(key, (ROOT / key).read_text())


def vocabulary(symbol, overrides=None):
    tree = ast.parse(read("schema.py", overrides))
    assignment = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == symbol for t in n.targets)
    )
    return sorted(ast.literal_eval(assignment.value.args[0]))


def projection(overrides=None):
    models = list(MODELS)
    for name, symbol in VOCABS:
        values = vocabulary(symbol, overrides)
        models.append(
            (
                name,
                "schema.py",
                " ".join("!" + v for v in values),
                [("[*]", v, symbol) for v in values],
            )
        )
    values = vocabulary("RUN_STATUSES", overrides)
    # A bundle's status can be refined before its immutable index observation.
    # Keep these mutations separate from the schema's per-record vocabulary.
    models.append(
        (
            "run-classification",
            "live_harness.py",
            " ".join(values),
            [
                *[("[*]", v, '"status": terminal.status') for v in values],
                *[
                    (
                        v,
                        "provider_unavailable",
                        'run["status"] = "provider_unavailable"',
                        "runner.py",
                    )
                    for v in ("succeeded", "failed", "timeout", "harness_boot_failed")
                ],
                *[
                    (v, "provider_unavailable", 'status = "provider_unavailable"', "gap/runner.py")
                    for v in ("succeeded", "failed", "timeout", "harness_boot_failed")
                ],
                *[
                    (
                        v,
                        "provider_unavailable",
                        'terminal = TerminalStatus(STATUS_PROVIDER_UNAVAILABLE, "provider_tools_unavailable")',
                        "live_harness.py",
                    )
                    for v in ("succeeded", "failed", "timeout", "harness_boot_failed")
                ],
                *[
                    (
                        v,
                        "contaminated",
                        'terminal = TerminalStatus("contaminated", CATEGORY_CONTAMINATED)',
                        "live_harness.py",
                    )
                    for v in values
                ],
                *[(v, "contaminated", 'status = "contaminated"', "gap/runner.py") for v in values],
                ("succeeded", "budget_exhausted", 'status = "budget_exhausted"', "gap/runner.py"),
                (
                    "succeeded",
                    "failed",
                    "terminal = TerminalStatus(STATUS_FAILED, CATEGORY_ANSWER_OVERFLOW)",
                    "live_harness.py",
                ),
                (
                    "succeeded",
                    "failed",
                    "terminal = TerminalStatus(STATUS_FAILED, CATEGORY_TRANSCRIPT_OVERFLOW)",
                    "live_harness.py",
                ),
                ("unsupported", "not_applicable", 'if status == "unsupported":', "runner.py"),
                *[(v, "[*]", "return HarnessRunResult(") for v in values],
            ],
        )
    )
    result = []
    for name, file, names, writers in models:
        identity = "searchlight." + name
        edges = []
        for i, writer in enumerate(writers):
            before, after, selector = writer[:3]
            edge_file = writer[3] if len(writer) == 4 else file
            assert selector in read(edge_file, overrides), f"missing writer {edge_file}#{selector}"
            edges.append(
                dict(
                    id=f"{identity}.{i}",
                    **{"from": before, "to": after},
                    trigger=selector,
                    codeSource=f"{OWNER}/lib/python/sew/{edge_file}#{selector}",
                )
            )
        states = []
        for value in names.split():
            terminal = value.startswith("!")
            value = value.lstrip("!")
            states.append(
                dict(
                    name=value,
                    terminal=terminal,
                    **(
                        {}
                        if terminal
                        else dict(
                            exitOwner="Searchlight maintainer",
                            maxDurationSeconds=3600,
                            overdueRecipient="SRE",
                            enforcement="missing",
                            deadEndEvidence=f"{file}: no universal overdue sweep after crash or operator pause; proposed bound",
                        )
                    ),
                )
            )
            if terminal:
                edges.append(
                    dict(
                        id=f"{identity}.end-{value}",
                        **{"from": value, "to": "[*]"},
                        trigger="return immutable record",
                        codeSource=f"{OWNER}/lib/python/sew/{file}",
                    )
                )
        result.append(
            dict(
                id=identity,
                owner=OWNER,
                codeSources=[
                    dict(file=f"{OWNER}/lib/python/sew/{f}", selector=selector)
                    for f, selector in sorted(
                        {(w[3] if len(w) == 4 else file, w[2]) for w in writers}
                    )
                ],
                states=states,
                transitions=edges,
            )
        )
    return result


def inventory(overrides=None):
    """All owned status constants, writes and predicates, including nested guards.

    Simple AST statements retain guards and enclosing function names, avoiding
    snapshots of entire unrelated functions. There are no owned SQL schemas.
    Reads/reporting adapters remain visible for ownership dispositions.
    """
    result = {}
    pattern = re.compile(
        r"status|outcome|verdict|availability|stopped_reason|defer|retry|phase|state", re.I
    )
    for path in sorted((ROOT / "lib/python/sew").rglob("*.py")):
        if path.name == "smv.py":
            continue
        key = str(path.relative_to(ROOT))
        source = (overrides or {}).get(key, path.read_text())
        rows = []

        def walk(node, scope="module", guards=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope += "." + node.name
            if isinstance(node, (ast.If, ast.While)):
                condition = ast.unparse(node.test)
                if pattern.search(condition):
                    rows.append(f"{scope}:guard:{condition}")
                for child in node.body:
                    walk(child, scope, (*guards, condition))
                for child in node.orelse:
                    walk(child, scope, (*guards, "not (" + condition + ")"))
                return
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return, ast.Expr)):
                # Only value statements; never embed an entire nested body.
                text = ast.unparse(node)
                if pattern.search(text):
                    rows.append(f"{scope}:{' && '.join(guards)}:{text}")
            for child in ast.iter_child_nodes(node):
                walk(child, scope, guards)

        walk(ast.parse(source))
        if rows:
            result[key] = rows
    return result


def validate(doc):
    assert type(doc["version"]) is int and doc["version"] == 1
    assert len({o["id"] for o in doc["objects"]}) == len(doc["objects"])
    for obj in doc["objects"]:
        names = {s["name"] for s in obj["states"]}
        assert len(names) == len(obj["states"]) and obj["owner"] and obj["codeSources"]
        assert len({e["id"] for e in obj["transitions"]}) == len(obj["transitions"])
        for state in obj["states"]:
            assert type(state["terminal"]) is bool
            if not state["terminal"]:
                bound = state.get("maxDurationSeconds")
                assert type(bound) in (float, int) and math.isfinite(bound) and bound > 0
                assert state.get("exitOwner") and state.get("overdueRecipient")
                assert state.get("enforcement") in {"missing", "present"}
                assert state["enforcement"] != "missing" or state.get("deadEndEvidence")
        for edge in obj["transitions"]:
            assert edge["from"] in names | {"[*]"} and edge["to"] in names | {"[*]"}
            assert edge["trigger"] and edge["codeSource"]
    return doc


def verify(doc=None, overrides=None):
    doc = validate(doc or json.loads((ROOT / "state-machines.json").read_text()))
    assert doc["objects"] == projection(overrides), "exact state/edge drift"
    assert doc["sourceInventory"] == inventory(overrides), "sibling writer/guard drift"
    decl = json.loads((ROOT / "post-merge-verify.json").read_text())
    edges = {e["id"] for o in doc["objects"] for e in o["transitions"]}
    readme = (ROOT / "README.md").read_text()
    for step in decl["steps"]:
        assert set(step["edges"]) <= edges
        assert f"`{step['promise']}`" in readme
    block = readme.split(START, 1)[1].split(END, 1)[0]
    for obj in doc["objects"]:
        diagram = (
            block.split("#### " + obj["id"] + "\n", 1)[1]
            .split("```mermaid", 1)[1]
            .split("```", 1)[0]
        )
        actual = set(
            re.findall(r"(\[\*\]|s_[0-9a-f]+) --> (\[\*\]|s_[0-9a-f]+): ([^\n]+)", diagram)
        )

        def sid(name):
            return "[*]" if name == "[*]" else "s_" + name.encode().hex()

        assert actual == {(sid(e["from"]), sid(e["to"]), e["id"]) for e in obj["transitions"]}, (
            "diagram edge drift"
        )
        assert set(re.findall(r'    state "([^"\n]+)" as ', diagram)) == {
            s["name"] for s in obj["states"]
        }, "diagram state drift"
    index = (ROOT / "docs/architecture/STATE-MACHINES.md").read_text()
    for obj in doc["objects"]:
        assert f"| `{obj['id']}` | {len(obj['states'])} | {len(obj['transitions'])} |" in index


def mutation_proof():
    original = json.loads((ROOT / "state-machines.json").read_text())
    fixtures = []
    for kind in ("remove", "rename", "edge", "bound"):
        doc = copy.deepcopy(original)
        obj = doc["objects"][0]
        if kind == "remove":
            obj["states"].pop()
        elif kind == "rename":
            obj["states"][0]["name"] = "changed"
        elif kind == "edge":
            obj["transitions"][0]["to"] = "[*]"
        else:
            obj["states"][0].pop("maxDurationSeconds")
        fixtures.append((doc, None))
    for file, before, after in (
        ("schema.py", '"succeeded",', ""),
        ("schema.py", '"succeeded",', '"changed-state",'),
        (
            "runner.py",
            'if entry.get("status") != "provider_unavailable":',
            'if entry.get("status") == "provider_unavailable":',
        ),
        ("runner.py", "deferred[cell.key] = deferral", "deferred[cell.key] = {}"),
    ):
        source = read(file)
        assert before in source
        source = source.replace(before, after, 1)
        ast.parse(source)
        fixtures.append((original, {"lib/python/sew/" + file: source}))
    for doc, overrides in fixtures:
        try:
            verify(doc, overrides)
        except (AssertionError, KeyError, ValueError):
            continue
        raise AssertionError("accepted deliberately changed fixture")
    return len(fixtures)


def exercise(step, scratch):
    """Real entry points with recorded transport/model data, never live mode."""
    from .harness import run_acceptance_fixture_matrix
    from .runner import CellExecution, FixtureCellExecutor, SuiteRunner
    from .schema import validate_fixture_run

    if step == "contract.drift":
        verify()
        return dict(exactStatesAndEdges=True, mutationsRejected=mutation_proof())
    if step == "evaluation.bundle":
        from .evaluator import EvaluationInput, evaluate_run, load_evaluation_input

        results = run_acceptance_fixture_matrix(scratch / "bundles")
        outcomes = []
        for result in results:
            validate_fixture_run(result.bundle_dir)
            data = load_evaluation_input(result.bundle_dir, ROOT / "tasks/current-fact-lookup-v1")
            # The acceptance matrix gives each harness its own source identity.
            # Bind the synthetic rubric to those recorded sources; preserve all
            # evaluator validation and the fixture-mode network prohibition.
            task = copy.deepcopy(data.task)
            task["deterministic_validator"]["required_source_ids"] = [
                source["source_id"] for source in data.sources
            ]
            data = replace(data, task=task)
            good = evaluate_run(data)
            bad = evaluate_run(
                EvaluationInput(run=data.run, task=data.task, answer={}, sources=data.sources)
            )
            assert good["outcome"] == "pass" and bad["outcome"] == "fail"
            outcomes.append(good["outcome"])
        assert len(outcomes) == 4
        return dict(validatedBundles=4, goodOutcomes=outcomes, corruptedAnswer="fail")
    if step == "runner.resume":
        runner = SuiteRunner(state_root=scratch / "runner")
        first = runner.run("lighthouse", run_id="smv", max_cells=1)
        assert first["stopped_reason"] == "interrupted"
        second = runner.run("lighthouse", run_id="smv", max_cells=1)
        index = json.loads((Path(second["run_root"]) / "run-index.json").read_text())
        assert second["completed_cells"] > first["completed_cells"]
        assert len({e["cell_key"] for e in index}) == len(index)
        assert not second["duplicate_run_ids"]
        for entry in index:
            if entry.get("run_dir"):
                validate_fixture_run(Path(entry["run_dir"]))
        from .runner import BudgetTracker

        class RetryBoundary:
            calls = 0

            def execute(self, cell, output_root, *, mode):
                self.calls += 1
                return CellExecution(
                    status="harness_boot_failed" if self.calls == 1 else "succeeded",
                    run_id=cell.run_id,
                    run_dir=None,
                )

        boundary = RetryBoundary()
        suite, tasks = runner._load_suite("lighthouse")
        suite["retries"] = dict(max_attempts=2)
        cell = next(c for c in runner.expand_matrix(suite, tasks) if c.applicable)
        result = runner._execute_with_retries(
            boundary,
            cell,
            scratch / "retry",
            "fixture",
            suite,
            tasks[cell.task_id],
            BudgetTracker.from_suite(suite, None),
        )
        assert result.attempts == 2 and result.status == "succeeded"
        return dict(
            before=first["completed_cells"],
            after=second["completed_cells"],
            duplicates=0,
            retryAttempts=2,
        )
    if step == "gap.grading":
        import shutil
        from types import SimpleNamespace
        import yaml
        from .cost_model import parse_price_table
        from .gap.calibrate import calibrate
        from .gap.runner import run

        root = scratch / "gap-catalog"
        golden = ROOT / "tests/fixtures/gap"
        shutil.copytree(golden, root / "catalogs/gap")
        tasks = json.loads((golden / "tasks.json").read_text())[:1]
        (root / "catalogs/gap/tasks.yaml").write_text(
            yaml.safe_dump(
                dict(
                    schema_version=1,
                    catalog_id="smv-fixture",
                    authoring_policy="synthetic",
                    tasks=tasks,
                )
            )
        )
        calls, over_budget = [], False

        def write(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))

        def execute(config, output):
            calls.append(config.run_id_override)
            directory = output / config.run_id_override
            write(
                directory / "run.json", dict(status="succeeded", metrics_ref="metrics/metrics.json")
            )
            write(
                directory / "metrics/metrics.json",
                dict(
                    token_usage=dict(
                        input=20000 if over_budget else 10,
                        output=1,
                        total_billable=20001 if over_budget else 11,
                        cached_input=0,
                        reasoning=0,
                        accounting_source="measured",
                    )
                ),
            )
            write(
                directory / "artifacts/spawn-metadata.json",
                dict(
                    model_id=config.model_id,
                    arm_audit=dict(contaminated=False),
                    process=dict(provider_calls=0),
                ),
            )
            return SimpleNamespace(bundle_dir=directory)

        def grader(task, directory, *args):
            assert not over_budget, "over-budget cell was graded"
            return dict(outcome="pass" if "-ceiling-" in directory.name else "fail")

        kwargs = dict(
            harness="codex",
            model="synthetic-model",
            root=root,
            state_root=scratch / "gap-state",
            execute=execute,
            grader=grader,
        )
        calibration, _ = calibrate(**kwargs)
        assert calibration["tasks"][0]["verdict"] == "admitted"
        prices = parse_price_table(dict(version=1, vendors={}, models={}))
        kwargs.update(
            arms=["floor", "ceiling"], reps=1, run_root=scratch / "gap-run", price_table=prices
        )
        state, destination = run(**kwargs)
        assert len(state["entries"]) == 2
        assert all(
            (Path(e["run_dir"]) / "evaluations/gap-outcome.json").is_file()
            for e in state["entries"].values()
        )
        before = len(calls)
        run(**kwargs, resume=True)
        assert len(calls) == before
        over_budget = True
        state, _ = run(**(kwargs | dict(run_root=scratch / "gap-budget")))
        assert all(e["status"] == "budget_exhausted" for e in state["entries"].values())
        return dict(admitted=True, gradedCells=2, duplicateCalls=0, budgetExhausted=2)
    if step == "runner.deferral":

        class Boundary(FixtureCellExecutor):
            calls = 0

            def execute(self, cell, output_root, *, mode):
                self.calls += 1
                if self.calls <= 3:
                    return CellExecution(
                        status="provider_unavailable", run_id=cell.run_id, run_dir=None
                    )
                return super().execute(cell, output_root, mode=mode)

        boundary = Boundary()
        runner = SuiteRunner(state_root=scratch / "deferral", executor=boundary)
        paused = runner.run("lighthouse", run_id="smv-wall")
        assert (
            paused["stopped_reason"] == "provider_unavailable"
            and paused["deferred_unavailable_cells"] == 3
        )
        resumed = runner.run("lighthouse", run_id="smv-wall", max_cells=3)
        assert resumed["deferred_unavailable_cells"] == 0 and resumed["completed_cells"] >= 3
        return dict(deferred=3, recovered=3, duplicateRunIds=resumed["duplicate_run_ids"])
    raise ValueError("unknown required step " + step)


def main():
    declaration = json.loads((ROOT / "post-merge-verify.json").read_text())
    mode = os.environ.get("SMV_MODE", "sandbox")
    commit = os.environ.get("SMV_COMMIT", "unknown")
    rows, started, error = [], time.monotonic(), None
    try:
        if mode != "sandbox":
            raise RuntimeError(
                "host-unavailable: SRE read-only bundle review, no approved paid canary"
            )
        scratch = Path(os.environ["SMV_SCRATCH"]).resolve()
        assert json.loads((scratch / ".smv-owned.json").read_text()) == dict(
            uid=os.geteuid(), commit=commit
        )
        assert not any(k.endswith(("TOKEN", "API_KEY", "BEARER", "DSN")) for k in os.environ)
        for step in declaration["steps"]:
            begin = time.monotonic()
            try:
                evidence = exercise(step["id"], scratch)
                assert evidence, "missing required evidence"
                verdict = "pass"
            except Exception as exc:
                verdict, evidence = "fail", dict(error=f"{type(exc).__name__}: {exc}")
            rows.append(
                dict(
                    **{k: step[k] for k in ("id", "promise", "edges")},
                    verdict=verdict,
                    observed=evidence,
                    expected=step["acceptance"],
                    evidence=evidence,
                    durationSeconds=time.monotonic() - begin,
                    exclusions=declaration["modes"][mode]["exclusions"],
                )
            )
    except Exception as exc:
        error = str(exc)
    for step in declaration["steps"]:
        if not any(row["id"] == step["id"] for row in rows):
            rows.append(
                dict(
                    **{k: step[k] for k in ("id", "promise", "edges")},
                    verdict="unavailable",
                    observed=error,
                    expected=step["acceptance"],
                    durationSeconds=0,
                    evidence=dict(error=error),
                    exclusions=declaration["modes"].get(mode, {}).get("exclusions", []),
                )
            )
    passed = bool(rows) and not error and all(row["verdict"] == "pass" for row in rows)
    print(
        json.dumps(
            dict(
                module=declaration["module"],
                mode=mode,
                commit=commit,
                verdict="pass" if passed else "fail",
                durationSeconds=time.monotonic() - started,
                steps=rows,
            )
        )
    )
    return 0 if passed else 1
