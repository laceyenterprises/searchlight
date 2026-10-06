"""Offline supply checks; Seatbelt validity remains in test_gap_code_corpus.

No fixture code executes here. These checks cannot certify author validity or
model admission. SEW_GAP_CODE_WHEELHOUSE supplies the public author cache.
"""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import runpy
import zipfile
from email.parser import Parser

from packaging.requirements import Requirement
from packaging.markers import default_environment
from packaging.specifiers import SpecifierSet
import pytest

from sew.gap.catalog import CODE_FAMILIES, load_gap_tasks
from sew.gap.mine import mine

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "catalogs/gap"
SOURCES = BASE / "sources/code-fresh"
TASKS = [t for t in load_gap_tasks(ROOT).values() if t["id"].startswith("gap-fresh-")]
GAPS = [t for t in TASKS if t["kind"] == "gap"]


def test_fresh_cohort_counts_and_boundary():
    assert len(GAPS) >= 16
    assert sum(t["kind"] == "control" for t in TASKS) >= 4
    counts = Counter(t["family"] for t in GAPS)
    assert set(counts) == CODE_FAMILIES
    assert all(counts[f] >= 2 for f in CODE_FAMILIES)
    assert all(t["cutoff_after"] == "2026-07-01" for t in GAPS)
    assert all(t["provenance"]["event_date"] > "2026-07-01" for t in GAPS)
    # Refresh is additive: all original code identifiers remain.
    original = {
        p.name
        for p in (BASE / "fixtures").iterdir()
        if p.name.startswith(("gap-code-", "gap-control-"))
    }
    assert len(original) == 20
    assert original <= load_gap_tasks(ROOT).keys()


def test_fresh_miner_receipt_replays_and_oracles_are_verbatim(monkeypatch):
    sources = json.loads((SOURCES / "registry-and-notes.json").read_text())
    receipt = json.loads((SOURCES / "mined.json").read_text())
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))

    def read(url):
        value = sources.get(url)
        return json.dumps(value) if isinstance(value, dict) else value

    replay = mine(
        [c["package"] for c in receipt["candidates"]],
        since=receipt["since"],
        read=read,
        retrieved_at=receipt["retrieved_at"],
    )
    assert replay["candidates"] == receipt["candidates"]
    bodies = [v.get("body", "") if isinstance(v, dict) else v for v in sources.values() if v]
    for task in GAPS:
        changed = [p for p in task["packages"] if p["role"] in {"old", "new"}]
        candidate = next(c for c in receipt["candidates"] if c["packages"] == changed)
        assert task["provenance"]["event_date"] == candidate["provenance"]["event_date"]
        assert set(candidate["provenance"]["source_urls"]) <= set(task["provenance"]["source_urls"])
        assert task["oracle"]["source_url"] in task["provenance"]["source_urls"]
        assert any(task["oracle"]["excerpt"] in body for body in bodies)


@pytest.mark.parametrize("task", GAPS, ids=lambda t: t["id"])
def test_fresh_wheel_hashes_and_dependency_closure(task, monkeypatch):
    configured = os.environ.get("SEW_GAP_CODE_WHEELHOUSE")
    assert configured, "Set SEW_GAP_CODE_WHEELHOUSE to the prepared public cache"
    cache = Path(configured)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    prepare = runpy.run_path(str(ROOT / "bin/prepare-gap-code-wheelhouse"))["prepare"]
    prepare(task["packages"], cache)
    metadata = {}
    from urllib.parse import urlsplit

    for pin in task["packages"]:
        path = cache / Path(urlsplit(pin["url"]).path).name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pin["sha256"]
        with zipfile.ZipFile(path) as wheel:
            names = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
            assert len(names) == 1
            message = Parser().parsestr(wheel.read(names[0]).decode())
        assert message["Version"] == pin["version"]
        metadata[(pin["name"], pin["role"])] = message
    for role in ("old", "new"):
        pins = {p["name"]: p for p in task["packages"] if p["role"] in {role, "dependency"}}
        for python in ("3.11", "3.12", "3.14"):
            environment = {
                **default_environment(),
                "python_version": python,
                "python_full_version": python + ".0",
                "extra": "",
            }
            for name, pin in pins.items():
                message = metadata[(name, pin["role"])]
                assert python + ".0" in SpecifierSet(message.get("Requires-Python", ""))
                for raw in message.get_all("Requires-Dist", []):
                    requirement = Requirement(raw)
                    if requirement.marker and not requirement.marker.evaluate(environment):
                        continue
                    dependency = requirement.name.lower().replace("_", "-")
                    assert dependency in pins, (task["id"], role, python, raw)
                    assert pins[dependency]["version"] in requirement.specifier, raw
