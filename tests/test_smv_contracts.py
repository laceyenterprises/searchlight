"""Normal-suite exact source drift and required-evidence regressions."""

import ast
import copy
import json
from pathlib import Path
import sys

import pytest

from sew import smv
from cwp_dispatch.smv.contracts import acceptance, declaration
import cwp_dispatch
from cwp_dispatch.process_group_runner import run_process_group
from cwp_dispatch.smv.smoke import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
DOC = json.loads((ROOT / "state-machines.json").read_text())
DECL = declaration(json.loads((ROOT / "post-merge-verify.json").read_text()))


def test_actual_states_edges_writers_and_diagrams():
    smv.verify()
    edges = {e["id"] for o in DOC["objects"] for e in o["transitions"]}
    for s in DECL["steps"]:
        assert set(s["edges"]) <= edges
        assert "`" + s["promise"] + "`" in (ROOT / "README.md").read_text()
    assert (ROOT / "docs/architecture/STATE-MACHINES.md").read_text() == smv.architecture_index(DOC)


@pytest.mark.parametrize("kind", ["remove-state", "rename-state", "edge", "bound"])
def test_definition_mutation_rejected(kind):
    doc = copy.deepcopy(DOC)
    obj = doc["objects"][-1] if kind == "bound" else doc["objects"][0]
    if kind == "remove-state":
        obj["states"].pop()
    elif kind == "rename-state":
        obj["states"][0]["name"] = "deliberately-renamed"
    elif kind == "bound":
        del obj["states"][0]["maxDurationSeconds"]
    else:
        obj["transitions"][0]["to"] = next(
            s["name"] for s in obj["states"] if s["name"] != obj["transitions"][0]["to"]
        )
    with pytest.raises((AssertionError, ValueError)):
        smv.verify({"state-machines.json": json.dumps(doc)})


def test_actual_enum_and_writer_mutations_rejected():
    file = "lib/python/sew/schema.py"
    with pytest.raises(AssertionError):
        smv.verify({file: (ROOT / file).read_text().replace('"succeeded"', '"renamed"', 1)})
    for file, anchors in smv.ANCHORS.items():
        file = "lib/python/sew/" + file
        source = ast.unparse(ast.parse((ROOT / file).read_text()))
        with pytest.raises((AssertionError, ValueError, SyntaxError)):
            smv.verify(
                {
                    file: source.replace(
                        anchors[0],
                        anchors[0]
                        .replace("terminal.status", "'changed-status'")
                        .replace("(path, state)", "(path, {})")
                        .replace("all(builder.dimensions.values())", "False")
                        .replace("= status", "= 'changed-status'"),
                        1,
                    )
                }
            )


@pytest.mark.parametrize("mode", ["sandbox", "host"])
def test_canonical_harness_required_evidence(mode, tmp_path):
    command = [sys.executable, str(ROOT / "scripts/smv-smoke.py")]
    env = sandbox_env(ROOT, tmp_path, "smv-test")
    env["PYTHONPATH"] += ":" + str(Path(cwp_dispatch.__file__).parent.parent)
    env["SMV_MODE"] = mode
    result = run_process_group(
        [sys.executable, "-m", "cwp_dispatch.smv.capture", *command], cwd=ROOT, env=env, timeout=168
    )
    code, report = result.returncode, json.loads(result.stdout.splitlines()[-1])
    if mode == "host":
        assert code != 0
        assert all(s["verdict"] == "unavailable" for s in report["steps"])
        return
    assert code == 0, report
    acceptance(DECL, report, code, mode=mode, commit="smv-test")
    for kind in ("missing", "failed", "unavailable"):
        changed = copy.deepcopy(report)
        if kind == "missing":
            changed["steps"].pop()
        else:
            changed["steps"][0]["verdict"] = "fail" if kind == "failed" else "unavailable"
        with pytest.raises(ValueError):
            acceptance(DECL, changed, 0, mode=mode, commit="smv-test")
