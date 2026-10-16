"""Normal-suite source drift and offline promise evidence, without a host SDK."""

import json
import socket
import subprocess

import pytest

from sew import smv


def test_exact_code_states_edges_and_sibling_writer_inventory():
    smv.verify()


def test_deliberate_definition_and_valid_code_mutations_fail():
    assert smv.mutation_proof() == 8


@pytest.mark.parametrize("bound", [None, 0, -1, float("inf"), float("nan"), True])
def test_every_nonterminal_has_a_finite_positive_bound(bound):
    doc = json.loads((smv.ROOT / "state-machines.json").read_text())
    doc["objects"][0]["states"][0]["maxDurationSeconds"] = bound
    with pytest.raises(AssertionError):
        smv.validate(doc)


@pytest.mark.parametrize(
    "step", ["evaluation.bundle", "runner.resume", "runner.deferral", "gap.grading"]
)
def test_real_entrypoints_with_external_boundaries_synthetic(tmp_path, monkeypatch, step):
    def refuse(*args, **kwargs):
        pytest.fail("offline smoke attempted a network or process boundary")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    evidence = smv.exercise(step, tmp_path)
    assert evidence


@pytest.mark.parametrize("mode", ["host", "sandbox"])
def test_unavailable_required_evidence_returns_nonzero(mode, monkeypatch, capsys):
    monkeypatch.setenv("SMV_MODE", mode)
    monkeypatch.delenv("SMV_SCRATCH", raising=False)
    assert smv.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "fail"
    assert len(report["steps"]) == 5
    assert all(row["verdict"] == "unavailable" for row in report["steps"])


@pytest.mark.parametrize("result", ["missing", "failure"])
def test_missing_or_failed_step_never_passes(tmp_path, monkeypatch, capsys, result):
    declaration = json.loads((smv.ROOT / "post-merge-verify.json").read_text())
    # main's precondition tests use an empty environment, never ambient tokens.
    monkeypatch.setattr(
        smv.os,
        "environ",
        {"SMV_MODE": "sandbox", "SMV_SCRATCH": str(tmp_path), "SMV_COMMIT": "synthetic"},
    )
    (tmp_path / ".smv-owned.json").write_text(
        json.dumps(dict(uid=smv.os.geteuid(), commit="synthetic"))
    )

    def exercise(*args):
        if result == "failure":
            raise RuntimeError("synthetic boundary unavailable")
        return None

    monkeypatch.setattr(smv, "exercise", exercise)
    assert smv.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert [row["id"] for row in report["steps"]] == [step["id"] for step in declaration["steps"]]
    assert all(row["verdict"] == "fail" for row in report["steps"])


def test_diagram_and_declaration_edges_have_exact_sources():
    declaration = json.loads((smv.ROOT / "post-merge-verify.json").read_text())
    assert declaration["totalBudgetSeconds"] <= 180 and declaration["onFailure"] == "page"
    assert declaration["deployMode"] == "sandbox"
    assert sum(s["budgetSeconds"] for s in declaration["steps"]) < 180
    smv.verify()
