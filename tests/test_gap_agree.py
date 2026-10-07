import copy
import json

import pytest

import sew.gap.agree as agree_module
from sew.gap.agree import add_agreement
from sew.gap.brief_grade import grade_brief
from sew.judge import Judge

TASK = {"id": "gap-decision", "family": "decision-brief"}
RUBRIC = {
    "acceptable_recommendations": ["new"],
    "key_facts": [
        {
            "text": "New plan is cheaper",
            "weight": 1,
            "source_snapshot": {
                "url": "https://primary.org/prices",
                "text": "New costs 7",
                "retrieved_at": "2026-09-30",
            },
        }
    ],
}
ANSWER = {
    "brief": "New plan is cheaper",
    "recommendation": "new",
    "claims": [{"text": "New costs 7", "citation_urls": ["https://primary.org/prices"]}],
}


def scorer(judge_id, overrides=None, calls=None):
    def judge(payload):
        if calls is not None:
            calls.append(judge_id)
        return {
            "dimensions": {
                key: {"score": (overrides or {}).get(key, 1), "reason": "fixture"}
                for key in payload["rubric"]["dimensions"]
            }
        }

    return Judge(judge_id, judge)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def run_root(tmp_path, monkeypatch):
    monkeypatch.setattr(agree_module, "load_gap_tasks", lambda root: {TASK["id"]: TASK})
    monkeypatch.setattr(agree_module, "load_brief_rubric", lambda task, base: copy.deepcopy(RUBRIC))
    monkeypatch.setattr(agree_module, "_identity", lambda run_dir, run: None)
    primary = grade_brief(
        TASK,
        RUBRIC,
        ANSWER,
        judges=[scorer("claude-code")],
        capture_source=lambda url: "New costs 7. Old costs 10.",
    )
    entries = {}
    for rep, status in ((1, "succeeded"), (2, "succeeded"), (3, "budget_exhausted")):
        run_dir = tmp_path / "bundles" / f"cell-{rep}"
        for name in ("gap-outcome.json", "calibration-outcome.json"):
            _write(run_dir / "evaluations" / name, primary)
        _write(run_dir / "artifacts/final-answer.json", ANSWER)
        _write(run_dir / "run.json", {"run_id": f"cell-{rep}"})
        entries[f"{TASK['id']}|brave|{rep}"] = {"status": status, "run_dir": str(run_dir)}
    code_dir = tmp_path / "bundles/code"
    _write(code_dir / "evaluations/gap-outcome.json", {"outcome": "pass"})
    entries["gap-code|brave|1"] = {"status": "succeeded", "run_dir": str(code_dir)}
    _write(tmp_path / "runner-state.json", {"entries": entries})
    return tmp_path


def outcome(root, rep, name="gap-outcome.json"):
    return json.loads((root / f"bundles/cell-{rep}/evaluations/{name}").read_text())


def test_measures_each_primary_only_cell_once_and_keeps_both_records_equal(run_root):
    calls = []
    result = add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", {"fact_0": 0}, calls))
    assert result == {
        "measured": 2,
        "already_measured": 0,
        "not_primary_only": 0,
        "judge_failed": 0,
        "failures": [],
    }
    assert calls == ["codex", "codex"]
    for rep in (1, 2):
        record = outcome(run_root, rep)
        assert record == outcome(run_root, rep, "calibration-outcome.json")
        assert record["passed"] is True and record["agreement"]["verdict_disputed"] is True
    assert outcome(run_root, 3)["agreement"]["status"] == "not_measured"
    again = add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls))
    assert (again["measured"], again["already_measured"]) == (0, 2)
    assert len(calls) == 2


def test_failed_judge_is_counted_and_retried_on_the_next_pass(run_root):
    def down(payload):
        raise TimeoutError()

    before = outcome(run_root, 1)
    result = add_agreement(run_root, catalog_root=run_root, judge=Judge("codex", down), limit=1)
    assert (result["measured"], result["judge_failed"]) == (0, 1)
    assert result["failures"][0]["cell"] == "gap-decision|brave|1"
    assert outcome(run_root, 1) == before
    retry = add_agreement(run_root, catalog_root=run_root, judge=scorer("codex"))
    assert retry["measured"] == 2


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_interrupted_companion_write_is_repaired_without_rejudging(run_root, monkeypatch, failure):
    calls = []
    before = outcome(run_root, 1)
    write = agree_module.atomic_write_json

    def interrupted_write(path, value):
        if path.name == "calibration-outcome.json":
            raise failure("interrupted companion write")
        write(path, value)

    monkeypatch.setattr(agree_module, "atomic_write_json", interrupted_write)
    with pytest.raises(failure):
        add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls))
    measured = outcome(run_root, 1)
    assert measured["agreement"]["status"] == "measured"
    assert outcome(run_root, 1, "calibration-outcome.json") == before
    assert calls == ["codex"]

    writes = []

    def recording_write(path, value):
        writes.append(path)
        write(path, value)

    monkeypatch.setattr(agree_module, "atomic_write_json", recording_write)
    # Repair does not consume judge quota, even when no new calls are allowed.
    retry = add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls), limit=0)
    assert (retry["measured"], retry["already_measured"]) == (0, 1)
    assert outcome(run_root, 1) == outcome(run_root, 1, "calibration-outcome.json") == measured
    assert writes == [run_root / "bundles/cell-1/evaluations/calibration-outcome.json"]
    assert calls == ["codex"]

    writes.clear()
    add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls), limit=0)
    assert writes == []
    assert calls == ["codex"]


def test_measured_cell_restores_missing_companion_without_rejudging(run_root):
    calls = []
    add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls), limit=1)
    measured = outcome(run_root, 1)
    companion = run_root / "bundles/cell-1/evaluations/calibration-outcome.json"
    companion.unlink()

    retry = add_agreement(run_root, catalog_root=run_root, judge=scorer("codex", calls=calls), limit=0)
    assert (retry["measured"], retry["already_measured"]) == (0, 1)
    assert outcome(run_root, 1, "calibration-outcome.json") == measured
    assert calls == ["codex"]
