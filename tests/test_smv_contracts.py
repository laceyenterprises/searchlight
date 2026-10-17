"""Canonical contract drift and cwd-independent standalone smoke regressions."""

import copy
import json
import os
import runpy
import site
from pathlib import Path
import subprocess
import sys

import pytest

from sew import smv
from smv_sources import sources, verify

ROOT = Path(__file__).resolve().parents[1]
DOC = json.loads((ROOT / "state-machines.json").read_text())
DECL = json.loads((ROOT / "post-merge-verify.json").read_text())


def test_actual_states_edges_writers_and_index():
    verify(DOC)
    edges = {e["id"] for o in DOC["objects"] for e in o["transitions"]}
    for step in DECL["steps"]:
        assert set(step["edges"]) <= edges
        assert "`" + step["promise"] + "`" in (ROOT / "README.md").read_text()
    assert (ROOT / "docs/architecture/STATE-MACHINES.md").read_text() == smv.architecture_index(DOC)


def test_contamination_reclassification_only_changes_states():
    obj = next(o for o in DOC["objects"] if o["id"] == "searchlight.run")
    edges = [e for e in obj["transitions"] if ".contaminated." in e["id"]]
    assert {e["from"] for e in edges} == {s["name"] for s in obj["states"]} - {"contaminated"}
    assert all(e["to"] == "contaminated" for e in edges)


@pytest.mark.parametrize("kind", ["remove-state", "rename-state", "edge", "bound"])
def test_definition_mutation_rejected(kind):
    doc = copy.deepcopy(DOC)
    obj = doc["objects"][0]
    if kind == "remove-state":
        obj["states"].pop()
    elif kind == "rename-state":
        obj["states"][0]["name"] = "deliberately-renamed"
    elif kind == "bound":
        state = next(s for o in doc["objects"] for s in o["states"] if not s["terminal"])
        del state["maxDurationSeconds"]
    else:
        obj["transitions"][0]["to"] = next(
            s["name"] for s in obj["states"] if s["name"] != obj["transitions"][0]["to"]
        )
    with pytest.raises((AssertionError, KeyError)):
        verify(doc)


@pytest.mark.parametrize("script", ["smoke.py", "smv-smoke.py"])
@pytest.mark.parametrize("directory", ["root", "scripts", "unrelated"])
def test_standalone_smoke_from_any_directory(script, directory, tmp_path):
    cwd = {"root": ROOT, "scripts": ROOT / "scripts", "unrelated": tmp_path}[directory]
    # No Agent OS helpers, credentials or user configuration are needed.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    env.update(HOME=str(tmp_path), SMV_COMMIT="smv-test", SMV_MODE="sandbox",
               PYTHONUSERBASE=site.getuserbase())
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["verdict"] == "pass" and report["commit"] == "smv-test"
    assert {s["id"] for s in report["steps"]} == {s["id"] for s in DECL["steps"]}
    proofs = report["steps"][0]["evidence"]["proofs"]
    assert proofs["run"]["bundles"] == 4
    assert proofs["evaluate"] == {"success": "pass", "timeout": "fail"}
    assert proofs["resume"]["uniqueCells"] == 2
    assert not list(tmp_path.iterdir()), "standalone smoke left persistent state outside its scratch root"


def test_actual_writer_mutation_rejected():
    src = sources()
    src["runner.py"] = src["runner.py"].replace("index.remove(failed)", "index.append(failed)")
    with pytest.raises(AssertionError):
        verify(DOC, src)


@pytest.mark.parametrize("kind", ["missing", "failed"])
def test_supplemental_proofs_are_required(kind, tmp_path, monkeypatch):
    helper = runpy.run_path(str(ROOT / "scripts/smoke.py"))

    def broken_proofs(scratch):
        if kind == "failed":
            raise RuntimeError("synthetic supplemental proof failure")
        yield "run", {"bundles": 4}, 0

    monkeypatch.setattr(smv, "exercise", broken_proofs)
    report = helper["run"]()
    assert report["verdict"] == "fail"
    assert report["steps"][0]["verdict"] == "fail"
    assert "error" in report["steps"][0]["evidence"]
