"""Code seeds require real, offline Seatbelt validation, separately by kind.

Prepare SEW_GAP_CODE_WHEELHOUSE before running this module. A missing cache or
unavailable containment is a failure, never fabricated acceptance or a skip.
"""

import ast
import copy
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess

import pytest

from sew.gap.catalog import CODE_FAMILIES, load_gap_tasks
from sew.gap.mine import mine
from sew.gap.validate import validate_task
from sew.gap.verify import verify

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "catalogs/gap"
TASKS = [t for t in load_gap_tasks(ROOT).values() if t["task_type"] == "code"]
GAPS = [t for t in TASKS if t["kind"] == "gap"]
CONTROLS = [t for t in TASKS if t["kind"] == "control"]


def test_code_family_and_control_counts():
    assert len(GAPS) >= 16
    assert len(CONTROLS) >= 4
    counts = Counter(t["family"] for t in GAPS)
    assert set(counts) == CODE_FAMILIES
    assert all(counts[f] >= 2 for f in CODE_FAMILIES)
    assert all(t["packages"] == [] for t in CONTROLS)


def test_mined_candidates_replay_without_network(monkeypatch):
    sources = json.loads((BASE / "sources/code/registry-and-notes.json").read_text())
    receipt = json.loads((BASE / "sources/code/mined.json").read_text())
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))

    def read(url):
        value = sources.get(url)
        return json.dumps(value) if isinstance(value, dict) else value

    mined = mine(
        sorted({c["package"] for c in receipt["candidates"]}),
        since=receipt["since"],
        read=read,
        retrieved_at=receipt["retrieved_at"],
    )
    actual = {(c["package"], c["new_version"]): c for c in mined["candidates"]}
    for candidate in receipt["candidates"]:
        replay = actual[(candidate["package"], candidate["new_version"])]
        assert replay == candidate
    # This receipt owns the original cohort; refresh cohorts carry independent
    # receipts and boundaries (test_gap_fresh_code_corpus.py).
    for task in (t for t in GAPS if t["cutoff_after"] == receipt["since"]):
        changed = [p for p in task["packages"] if p["role"] in {"old", "new"}]
        candidate = next(c for c in receipt["candidates"] if c["packages"] == changed)
        assert task["cutoff_after"] == "2026-01-01"
        assert task["provenance"] == candidate["provenance"]
        # Oracle is a contiguous task-specific slice of the primary miner input.
        source = task["oracle"]["source_url"]
        bodies = [v.get("body", "") if isinstance(v, dict) else v for v in sources.values() if v]
        assert any(task["oracle"]["excerpt"] in b for b in bodies)
        assert source in task["provenance"]["source_urls"]


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_fixture_and_verifier_assets_are_disjoint_and_parse(task):
    fixture, hidden = (BASE / task[k] for k in ("fixture", "hidden"))
    assert (hidden / "reference.diff").read_text().strip()
    assert (hidden / "test_hidden.py").is_file()
    assert (hidden / "test_smoke.py").is_file()
    patch = hidden / "reference.diff"
    changed = [line[6:] for line in patch.read_text().splitlines() if line.startswith("--- a/")]
    assert set(changed) <= {"app.py", "requirements.txt"}
    applied = subprocess.run(
        ["git", "apply", "--check", str(patch.resolve())],
        cwd=fixture,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr
    assert not any(
        p.name in {"reference.diff", "test_hidden.py", "test_smoke.py", "advisory.json"}
        for p in fixture.rglob("*")
    )
    for path in [*fixture.rglob("*.py"), *hidden.rglob("*.py")]:
        ast.parse(path.read_text(), filename=str(path))
    for pin in task["packages"]:
        assert urlsplit_host(pin["url"]) == "files.pythonhosted.org"
        assert pin["url"].endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl"))


def urlsplit_host(url):
    from urllib.parse import urlsplit

    return urlsplit(url).hostname


@pytest.fixture
def offline_wheelhouse(monkeypatch):
    path = os.environ.get("SEW_GAP_CODE_WHEELHOUSE")
    assert path, "Set SEW_GAP_CODE_WHEELHOUSE to the prepared public wheel cache"
    cache = Path(path)
    assert cache.is_dir(), "prepared wheelhouse is missing"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    prepare = runpy.run_path(str(ROOT / "bin/prepare-gap-code-wheelhouse"))["prepare"]
    prepare([p for t in GAPS for p in t["packages"]], cache)
    return cache


@pytest.mark.parametrize("task", GAPS, ids=lambda t: t["id"])
def test_every_gap_ordinary_grading_offline(task, offline_wheelhouse, tmp_path):
    empty = tmp_path / "empty.diff"
    empty.write_text("")
    # Exercise grading's requirements selection without author-only install_roles.
    unchanged = verify(task, empty, root=ROOT, wheelhouse=offline_wheelhouse)
    assert not unchanged["errors"], unchanged["errors"]
    assert unchanged["visible_regressions"] == 0
    assert unchanged["outcome"] == "fail", task["id"]
    assert unchanged["escaped_defects"] >= 1
    assert unchanged["reruns"]
    assert not unchanged["flakes"]

    reference = BASE / task["hidden"] / "reference.diff"
    repaired = verify(task, reference, root=ROOT, wheelhouse=offline_wheelhouse)
    assert not repaired["errors"], repaired["errors"]
    assert repaired["outcome"] == "pass", repaired
    assert repaired["escaped_defects"] == repaired["visible_regressions"] == 0


@pytest.mark.parametrize(
    "task", [t for t in GAPS if t["family"] == "vulnerable-dependency"], ids=lambda t: t["id"]
)
def test_security_code_only_repair_fails_ordinary_grading(task, offline_wheelhouse, tmp_path):
    reference = BASE / task["hidden"] / "reference.diff"
    patch = tmp_path / "code-only.diff"
    patch.write_text(reference.read_text().split("--- a/requirements.txt")[0])
    result = verify(task, patch, root=ROOT, wheelhouse=offline_wheelhouse)
    assert not result["errors"], result["errors"]
    assert result["visible_regressions"] == 0
    assert result["outcome"] == "fail", result
    failures = [
        t["id"] for record in result["hidden"] for t in record["tests"] if t["status"] == "failure"
    ]
    assert len(failures) == 1
    assert failures[0].endswith("::test_delivered_security_pin")
    assert result["reruns"]
    assert not result["flakes"]


@pytest.mark.parametrize("task", GAPS, ids=lambda t: t["id"])
def test_every_gap_validity_triple_offline(task, offline_wheelhouse):
    record = validate_task(task, root=ROOT, wheelhouse=offline_wheelhouse)
    assert record["accepted"], record["reason"]
    assert [(c["rep"], c["leg"]) for c in record["checks"]] == [
        (rep, leg) for rep in range(1, 4) for leg in ("old-visible", "naive-bump", "reference")
    ]
    for check in record["checks"]:
        result = check["result"]
        assert result["visible_regressions"] == 0
        if check["leg"] == "naive-bump":
            assert result["escaped_defects"] >= 1
            assert result["reruns"]
        elif check["leg"] == "reference":
            assert result["outcome"] == "pass"
            assert result["escaped_defects"] == 0


@pytest.mark.parametrize("task", CONTROLS, ids=lambda t: t["id"])
def test_every_no_change_control_offline(task, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    record = validate_task(task, root=ROOT)
    assert record["accepted"], record["reason"]
    assert [(c["rep"], c["leg"]) for c in record["checks"]] == [
        (rep, leg) for rep in range(1, 4) for leg in ("unmodified", "reference")
    ]
    for check in record["checks"]:
        assert check["result"]["visible_regressions"] == 0
        if check["leg"] == "unmodified":
            assert check["result"]["escaped_defects"] >= 1
        else:
            assert check["result"]["outcome"] == "pass"


def test_cache_preparation_is_offline_and_hash_checked(tmp_path):
    tmp_path = tmp_path / "cache"
    prepare = runpy.run_path(str(ROOT / "bin/prepare-gap-code-wheelhouse"))["prepare"]
    data = b"recorded wheel bytes"
    pin = {
        "url": "https://files.pythonhosted.org/mini-1-py3-none-any.whl",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    with pytest.raises(ValueError, match="missing cached wheel"):
        prepare([pin], tmp_path)
    with pytest.raises(ValueError, match="hash mismatch"):
        prepare([pin], tmp_path, download=True, fetch=lambda url: b"corrupt")
    assert not list(tmp_path.iterdir())
    assert prepare([pin, pin], tmp_path, download=True, fetch=lambda url: data) == 1
    assert prepare([pin], tmp_path, fetch=lambda url: pytest.fail("offline")) == 1
    with pytest.raises(ValueError, match="conflicting pin"):
        prepare([pin, {**pin, "sha256": "0" * 64}], tmp_path)


def test_cache_symlinks_are_refused(tmp_path):
    prepare = runpy.run_path(str(ROOT / "bin/prepare-gap-code-wheelhouse"))["prepare"]
    data = b"public wheel bytes"
    pin = {
        "url": "https://files.pythonhosted.org/mini-1-py3-none-any.whl",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    cache = tmp_path / "cache"
    cache.mkdir()
    source = tmp_path / "source"
    source.write_bytes(data)
    (cache / "mini-1-py3-none-any.whl").symlink_to(source)
    with pytest.raises(ValueError, match="symlink refused"):
        prepare([pin], cache)


@pytest.mark.parametrize(
    "task", [t for t in TASKS if t["task_type"] == "code"], ids=lambda t: t["id"]
)
def test_cell_wheelhouse_roles(task, tmp_path):
    from types import SimpleNamespace
    from sew.gap.workspace import prepare_workspace
    from urllib.parse import unquote, urlsplit
    import hashlib

    task = copy.deepcopy(task)
    cache = tmp_path / "verifier-cache"
    cache.mkdir()
    names = {}
    for pin in task["packages"]:
        name = Path(unquote(urlsplit(pin["url"]).path)).name
        content = name.encode()
        (cache / name).write_bytes(content)
        pin["sha256"] = hashlib.sha256(content).hexdigest()
        names[name] = pin["role"]
    config = SimpleNamespace(wheelhouse=cache)
    cell = tmp_path / "cell"
    cell.mkdir()
    prepare_workspace(config, cell, ROOT, task)
    assert {p.name for p in (cell / "wheelhouse").iterdir()} == {
        name for name, role in names.items() if role in {"old", "dependency"}
    }
    verifier = tmp_path / "verifier"
    verifier.mkdir()
    prepare_workspace(config, verifier, ROOT, task, wheel_roles=("old", "new", "dependency"))
    assert {p.name for p in (verifier / "wheelhouse").iterdir()} == set(names)


def test_new_stripe_install_fails_in_cell(offline_wheelhouse, tmp_path):
    from types import SimpleNamespace
    from sew.gap.workspace import prepare_workspace
    import subprocess
    import sys

    task = next(t for t in GAPS if t["id"] == "gap-code-transfer-refund")
    _, _, offline = prepare_workspace(
        SimpleNamespace(wheelhouse=offline_wheelhouse), tmp_path, ROOT, task
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            offline["PIP_FIND_LINKS"],
            "--no-deps",
            "--target",
            str(tmp_path / "deps16"),
            "stripe==16.0.0",
        ],
        env={**os.environ, **offline},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "No matching distribution found for stripe==16.0.0" in result.stderr
    assert not (tmp_path / "deps16" / "stripe").exists()
