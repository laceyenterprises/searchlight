"""Offline tests for the L2 agent lane scorer.

The scoring rules here decide the headline claim of the whole lane ("delegating
cuts tokens and raises accuracy"), so the cases that could manufacture that
conclusion are pinned explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from sew.agent_lane import (
    AGENT_ARMS,
    CONTROL_ARM,
    TIERS,
    ArmResult,
    aggregate,
    run_exa_agent,
    run_firecrawl_agent,
    run_parallel_task,
    run_control_diy,
    score_answer,
)


@dataclass(frozen=True)
class StubProviderResult:
    status: str
    sources: tuple[dict[str, Any], ...] = ()
    error_class: str | None = None
    provider_cost: Mapping[str, Any] | None = None


class SequencedProvider:
    provider_id = "parallel-web"

    def __init__(self, results: list[StubProviderResult]) -> None:
        self._results = results
        self.calls = 0

    def call(self, request) -> StubProviderResult:
        self.calls += 1
        return self._results.pop(0)


TASK = {
    "id": "t1",
    "task_class": "company_research",
    "output_schema": {"type": "object", "required": ["founders", "founded_year"]},
    "fields": [
        {"name": "founders", "patterns": [r"Will(iam)?\s+Bryk"]},
        {"name": "founded_year", "patterns": [r"\b2021\b"]},
        {"name": "hq", "patterns": [r"San\s+Francisco"], "optional": True},
    ],
}


def test_structured_answer_scores_extracted_and_available() -> None:
    out = score_answer(TASK, {"founders": ["William Bryk"], "founded_year": "2021"})
    assert out["fields_correct"] == 2
    assert out["fields_scored"] == 2
    assert out["fields_available"] == 2
    assert out["schema_valid"] is True
    assert out["field_detail"]["founders"]["state"] == "correct"


def test_unstructured_control_text_clears_available_but_not_extracted() -> None:
    # The DIY control returns raw search text with no named fields. Scoring it
    # on extraction would report a structural 0% and manufacture the lane's
    # headline, so availability must be credited separately.
    corpus = "Exa was founded in 2021 by William Bryk and Jeffrey Wang."
    out = score_answer(TASK, corpus)
    assert out["fields_available"] == 2
    assert out["fields_correct"] == 0
    assert out["field_detail"]["founders"]["state"] == "available_unextracted"
    assert out["field_detail"]["founders"]["available"] is True
    assert out["schema_valid"] is False


def test_control_diy_retries_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SequencedProvider(
        [
            StubProviderResult("rate_limited", error_class="http_429"),
            StubProviderResult(
                "ok",
                sources=(
                    {
                        "title": "A",
                        "snippet": "answer",
                        "content_length": 12,
                    },
                ),
                provider_cost={"total": 0.01},
            ),
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr("sew.agent_lane.make_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr("sew.agent_lane.time.sleep", sleeps.append)

    result = run_control_diy({"prompt": "q"}, {}, 30.0)

    assert result["status"] == "ok"
    assert result["context_chars"] == 12
    assert provider.calls == 2
    assert sleeps == [8.0]


def test_control_diy_reports_rate_limited_after_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SequencedProvider(
        [StubProviderResult("rate_limited", error_class="http_429") for _ in range(5)]
    )
    monkeypatch.setattr("sew.agent_lane.make_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr("sew.agent_lane.time.sleep", lambda seconds: None)

    result = run_control_diy({"prompt": "q"}, {}, 30.0)

    assert result == {"status": "rate_limited", "error_class": "http_429"}
    assert provider.calls == 5


@pytest.mark.parametrize(
    "runner,cfg",
    [
        (run_exa_agent, {"effort": "low"}),
        (run_parallel_task, {"processor": "core"}),
        (run_firecrawl_agent, {"max_credits": 30}),
    ],
)
def test_agent_polling_stops_on_terminal_http_error(
    monkeypatch: pytest.MonkeyPatch,
    runner,
    cfg: dict[str, Any],
) -> None:
    polls = 0

    def fake_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, str]:
        nonlocal polls
        polls += 1
        return 401, "unauthorized"

    monkeypatch.setattr("sew.agent_lane._credential", lambda requested: f"key-{requested}")
    monkeypatch.setattr(
        "sew.agent_lane._post",
        lambda *args, **kwargs: (200, {"id": "run-1", "run_id": "run-1"}),
    )
    monkeypatch.setattr("sew.agent_lane._get", fake_get)
    monkeypatch.setattr("sew.agent_lane.time.sleep", lambda seconds: None)

    result = runner({"prompt": "q", "output_schema": {"type": "object"}}, cfg, 30.0)

    assert result["status"] == "failed"
    assert result["error_class"] == "poll_401"
    assert result["detail"] == "unauthorized"
    assert polls == 1


def test_parallel_result_fetch_retries_transient_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = [
        (200, {"status": "completed"}),
        ("ERR", "TimeoutError: timed out"),
        (200, {"output": {"content": '{"founders": ["William Bryk"]}', "basis": ["u"]}}),
    ]

    def fake_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int | str, Any]:
        return calls.pop(0)

    monkeypatch.setattr("sew.agent_lane._credential", lambda requested: f"key-{requested}")
    monkeypatch.setattr(
        "sew.agent_lane._post",
        lambda *args, **kwargs: (200, {"run_id": "run-1"}),
    )
    monkeypatch.setattr("sew.agent_lane._get", fake_get)
    monkeypatch.setattr("sew.agent_lane.time.sleep", lambda seconds: None)

    result = run_parallel_task(
        {"prompt": "q", "output_schema": {"type": "object"}},
        {"processor": "core"},
        30.0,
    )

    assert result["status"] == "ok"
    assert result["answer"] == {"founders": ["William Bryk"]}
    assert result["citation_count"] == 1
    assert calls == []


def test_text_without_the_answer_scores_neither() -> None:
    out = score_answer(TASK, "Some unrelated page about gardening.")
    assert out["fields_available"] == 0
    assert out["fields_correct"] == 0
    assert out["field_detail"]["founded_year"]["state"] == "missing"


def test_wrong_value_is_distinct_from_missing_field() -> None:
    out = score_answer(TASK, {"founders": ["Someone Else"], "founded_year": "1999"})
    assert out["field_detail"]["founders"]["state"] == "wrong"
    assert out["field_detail"]["founded_year"]["state"] == "wrong"
    assert out["fields_correct"] == 0
    # Both required keys are present, so the answer is schema-complete but wrong.
    assert out["schema_valid"] is True


def test_absent_optional_field_is_not_penalised() -> None:
    out = score_answer(TASK, {"founders": ["William Bryk"], "founded_year": "2021"})
    assert out["field_detail"]["hq"]["state"] == "absent_optional"
    assert out["field_detail"]["hq"]["correct"] is None
    assert out["fields_scored"] == 2  # hq excluded entirely


def test_renamed_field_counts_as_available_but_not_extracted() -> None:
    # An arm that buries the answer under a different key DID find it, but it
    # did not honour the requested output schema -- and a caller keying off
    # `founders` gets nothing. Availability and extraction must diverge here,
    # otherwise schema compliance stops being measured at all.
    out = score_answer(TASK, {"result": {"people": ["William Bryk"]}, "founded_year": "2021"})
    assert out["field_detail"]["founders"]["state"] == "available_unextracted"
    assert out["field_detail"]["founders"]["available"] is True
    assert out["fields_available"] == 2
    assert out["fields_correct"] == 1


def _cell(arm: str, correct: bool, tokens: int, task_id: str = "t1") -> ArmResult:
    detail = {
        "founders": {
            "state": "correct" if correct else "wrong",
            "correct": correct,
            "available": correct,
        },
    }
    return ArmResult(
        task_id=task_id,
        task_class="company_research",
        arm=arm,
        tier="low",
        repetition=1,
        status="ok",
        latency_ms=1000.0,
        fields_scored=1,
        fields_correct=int(correct),
        fields_available=int(correct),
        field_detail=detail,
        schema_valid=True,
        answer_tokens_est=tokens,
    )


def test_token_reduction_is_reported_against_the_control_not_in_isolation() -> None:
    catalog = {"catalog_id": "c", "tasks": [TASK]}
    cells = [
        _cell(CONTROL_ARM, True, 2000),
        _cell("exa-agent", True, 100),
        _cell("parallel-task", True, 20),
    ]
    report = aggregate(cells, catalog)
    assert report["by_arm"]["exa-agent"]["control_tokens_per_vendor_token"] == 20.0
    assert report["by_arm"]["parallel-task"]["control_tokens_per_vendor_token"] == 100.0
    # The control itself is the baseline and must not claim a reduction.
    assert "control_tokens_per_vendor_token" not in report["by_arm"][CONTROL_ARM]


def test_field_all_vendors_miss_is_flagged_suspect_not_booked_as_failures() -> None:
    catalog = {"catalog_id": "c", "tasks": [TASK]}
    cells = [_cell(arm, False, 100) for arm in AGENT_ARMS]
    report = aggregate(cells, catalog)
    # Two or more vendor arms disagreeing with the declared truth is far likelier
    # to be a bad pattern than a simultaneous multi-vendor failure.
    assert report["suspect_ground_truth"] == ["t1.founders"]
    for arm in AGENT_ARMS:
        # Excluded from the denominator, not counted as a miss.
        assert report["by_arm"][arm]["fields_scored"] == 0


def test_single_vendor_miss_is_a_real_failure_not_suspect() -> None:
    catalog = {"catalog_id": "c", "tasks": [TASK]}
    cells = [_cell("exa-agent", False, 100), _cell("parallel-task", True, 20)]
    report = aggregate(cells, catalog)
    assert report["suspect_ground_truth"] == []
    assert report["by_arm"]["exa-agent"]["field_accuracy"] == 0.0
    assert report["by_arm"]["parallel-task"]["field_accuracy"] == 1.0


def test_repeated_single_vendor_misses_do_not_create_provider_agreement() -> None:
    catalog = {"catalog_id": "c", "tasks": [TASK]}
    first = _cell("exa-agent", False, 100)
    second = _cell("exa-agent", False, 100)
    second.repetition = 2
    omitted = _cell("parallel-task", False, 20)
    omitted.field_detail = {}
    omitted.fields_scored = 0
    omitted.fields_correct = 0
    omitted.fields_available = 0

    report = aggregate([first, second, omitted], catalog)

    assert report["suspect_ground_truth"] == []
    assert report["by_arm"]["exa-agent"]["fields_scored"] == 2
    assert report["by_arm"]["exa-agent"]["field_accuracy"] == 0.0


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_tiers_are_matched_on_price_not_on_vendor_tier_name(tier: str) -> None:
    cfg = TIERS[tier]
    exa = cfg["exa-agent"]["list_price_usd"]
    par = cfg["parallel-task"]["list_price_usd"]
    # Exa `low`/`medium` and Parallel `core`/`pro` are exact price peers.
    # Comparing vendor DEFAULT tiers would repeat the L1 mistake of measuring
    # configuration rather than capability.
    assert exa == par, f"{tier}: exa ${exa} != parallel ${par}"
    assert cfg["firecrawl-agent"]["list_price_usd"] == pytest.approx(exa, rel=0.25)


def test_wrong_named_field_is_not_credited_by_a_correct_value_elsewhere() -> None:
    # Adversarial-review finding (PR #6901): scoring used `in_field or in_whole`,
    # so a WRONG value in the requested field still scored as extracted-correct
    # whenever the right value appeared anywhere else in the payload. That turns
    # a schema violation into a win and inflates the lane's headline metric.
    # The pre-existing wrong-value test missed it by putting the right answer
    # nowhere in the payload.
    out = score_answer(
        TASK,
        {
            "founders": ["Wrong Person"],
            "notes": "William Bryk founded it",
            "founded_year": "2021",
        },
    )
    assert out["field_detail"]["founders"]["state"] == "wrong"
    assert out["field_detail"]["founders"]["correct"] is False
    # The value WAS in the payload, so availability still records it...
    assert out["field_detail"]["founders"]["available"] is True
    # ...but extraction credits only the correctly-named field.
    assert out["fields_correct"] == 1  # founded_year only
    assert out["fields_available"] == 2


def test_correct_named_field_still_scores_as_extracted() -> None:
    out = score_answer(TASK, {"founders": ["William Bryk"], "founded_year": "2021"})
    assert out["field_detail"]["founders"]["state"] == "correct"
    assert out["fields_correct"] == 2
