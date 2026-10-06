"""Offline author validity evidence, not a substitute for GAP-13 calibration.

Fixture judges recognize reviewed paraphrases of source passages. They inspect
content only, never task/arm IDs or a canned reference/stale verdict. Mutations
prove missing facts, wrong recommendations and changed sources fail the check.
"""

import copy
import hashlib
import json
import shutil
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from sew.gap.brief_grade import load_brief_rubric
from sew.gap.brief_validate import validate_brief_task
from sew.gap.catalog import BRIEF_FAMILIES, load_gap_tasks
from sew.judge import Judge

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "catalogs/gap"
TASKS = [t for t in load_gap_tasks(ROOT).values() if t["family"] in BRIEF_FAMILIES]


def evidence_judges(rubric):
    def judge(payload):
        evidence = payload["deliverable"]
        # Use the blinded passage bytes, preserving citation normalization.
        entailments = {f["text"]: f["source_snapshot"]["text"] for f in evidence["facts"]}
        sources = {source["id"]: source["text"] for source in evidence["sources"]}
        labels = {}
        for i, fact in enumerate(evidence["facts"]):
            labels[f"fact_{i}"] = int(fact["text"] in evidence["brief"])
        for i, claim in enumerate(evidence["claims"]):
            expected = entailments.get(claim["text"])
            cited = [sources[source_id] for source_id in claim["source_ids"]]
            labels[f"claim_{i}"] = int(expected is not None and expected in cited)
        return {
            "dimensions": {
                key: {"score": value, "reason": "reviewed passage/paraphrase content check"}
                for key, value in labels.items()
            }
        }

    return [Judge("claude-code", judge), Judge("codex", judge)]


def validate(task, root=ROOT, *, capture=None, judges=None):
    base = root / "catalogs/gap"
    rubric = load_brief_rubric(task, base)
    snapshots = {
        f["source_snapshot"]["url"]: f["source_snapshot"]["text"] for f in rubric["key_facts"]
    }
    return validate_brief_task(
        task,
        root=root,
        judges=judges or evidence_judges(rubric),
        capture_source=capture or snapshots.__getitem__,
    )


def test_seed_family_counts():
    counts = Counter(t["family"] for t in TASKS)
    assert counts["decision-brief"] >= 6
    assert counts["research-brief"] >= 4
    assert len({t["oracle"]["source_url"] for t in TASKS}) == len(TASKS)


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_every_brief_validity_pair(task):
    record = validate(task)
    assert record["accepted"], record["reason"]
    reference, stale = (record["checks"][leg] for leg in ("reference", "stale"))
    assert reference["outcome"] == "pass"
    assert reference["key_fact_recall"] == 1
    assert reference["unsupported_claim_rate"] == 0
    assert stale["outcome"] == "fail"
    assert stale["key_fact_recall"] < 0.7
    if task["family"] == "decision-brief":
        assert reference["decision_correct"]
        assert not stale["decision_correct"]


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_fresh_primary_evidence_and_oracle_set(task):
    assert task["cutoff_after"] == "2026-01-01"
    assert task["provenance"]["event_date"] > task["cutoff_after"]
    excerpts = json.loads((BASE / task["hidden"] / "oracle-excerpts.json").read_text())
    assert excerpts
    assert sum(len(e["text"].split()) for e in excerpts) <= 400
    assert "\n\n".join(e["text"] for e in excerpts) == task["oracle"]["excerpt"]
    rubric = load_brief_rubric(task, BASE)
    for fact in rubric["key_facts"]:
        snapshot = fact["source_snapshot"]
        assert snapshot in excerpts
        assert snapshot["url"] in task["provenance"]["source_urls"]
        assert snapshot["url"].startswith(
            (
                "https://github.blog/changelog/2026-",
                "https://blog.python.org/2026/",
                "https://www.python.org/downloads/release/",
            )
        )
        assert date.fromisoformat(snapshot["published_at"]) > date(2026, 1, 1)
        assert snapshot["published_at"] <= snapshot["retrieved_at"]
        assert hashlib.sha256(snapshot["text"].encode()).hexdigest() == snapshot["sha256"]


@pytest.fixture
def mutable_catalog(tmp_path):
    shutil.copytree(BASE, tmp_path / "catalogs/gap")
    return tmp_path, copy.deepcopy(TASKS[0])


@pytest.mark.parametrize(
    "mutation",
    [
        "reference-fails",
        "stale-passes",
        "invalid-stale",
        "missing-reference",
        "wrong-decision",
        "missing-facts",
        "unsupported-reference",
    ],
)
def test_invalid_pair_is_rejected(mutable_catalog, mutation):
    root, task = mutable_catalog
    hidden = root / "catalogs/gap" / task["hidden"]
    reference = json.loads((hidden / "reference.json").read_text())
    if mutation == "reference-fails":
        (hidden / "reference.json").write_text((hidden / "stale.json").read_text())
    elif mutation == "stale-passes":
        (hidden / "stale.json").write_text(json.dumps(reference))
    elif mutation == "invalid-stale":
        (hidden / "stale.json").write_text("{}")
    elif mutation == "missing-reference":
        (hidden / "reference.json").unlink()
    else:
        if mutation == "wrong-decision":
            reference["recommendation"] = "convert-account"
        if mutation == "missing-facts":
            reference["brief"] = "No findings available."
        if mutation == "unsupported-reference":
            reference["claims"][0]["text"] = "The service costs nothing forever."
        (hidden / "reference.json").write_text(json.dumps(reference))
    assert not validate(task, root)["accepted"]


def test_changed_capture_cannot_pass_reference():
    assert not validate(TASKS[0], capture=lambda url: "Unrelated primary content")["accepted"]


def test_unavailable_judge_is_not_a_stale_failure():
    def unavailable(payload):
        raise OSError("offline transport")

    record = validate(
        TASKS[0], judges=[Judge("claude-code", unavailable), Judge("codex", unavailable)]
    )
    assert not record["accepted"]
    assert record["reason"] == "reference ungraded: judge_unavailable"


def test_disputed_validity_is_rejected():
    rubric = load_brief_rubric(TASKS[0], BASE)
    pair = evidence_judges(rubric)

    def reject(payload):
        return {
            "dimensions": {
                key: {"score": 0, "reason": "fixture disagreement"}
                for key in payload["rubric"]["dimensions"]
            }
        }

    pair[1] = Judge("codex", reject)
    record = validate(TASKS[0], judges=pair)
    assert not record["accepted"]
    assert "disagree" in record["reason"]
