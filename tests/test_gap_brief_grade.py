import copy
import json

import pytest

import sew.gap.brief_grade as brief_grade_module

from sew.gap.brief_grade import brief_schema, grade_brief, load_brief_rubric
from sew.judge import ArmIdentity, Judge, find_identity_leaks
from sew.schema import SchemaError


@pytest.fixture
def rubric():
    return {
        "acceptable_recommendations": ["new", "alternate"],
        "key_facts": [
            {
                "text": "New plan is cheaper",
                "weight": 7,
                "source_snapshot": {
                    "url": "https://primary.org/prices",
                    "text": "New costs 7",
                    "retrieved_at": "2026-09-30",
                },
            },
            {
                "text": "Old costs ten",
                "weight": 3,
                "source_snapshot": {
                    "url": "https://primary.org/prices",
                    "text": "Old costs 10",
                    "retrieved_at": "2026-09-30",
                },
            },
        ],
    }


@pytest.fixture
def task():
    return {"id": "gap-decision", "family": "decision-brief"}


@pytest.fixture
def answer():
    return {
        "brief": "New plan is cheaper",
        "recommendation": "new",
        "claims": [
            {"text": f"fact {i}", "citation_urls": ["https://primary.org/prices"]}
            for i in range(10)
        ],
    }


def judges(first=None, second=None, seen=None, *, reverse_secondary=False):
    def transport(overrides, reverse=False):
        def judge(payload):
            if seen is not None:
                seen.append(copy.deepcopy(payload))
            assert not find_identity_leaks(payload)
            response = {
                "dimensions": {
                    key: {"score": (overrides or {}).get(key, 1), "reason": "fixture evidence"}
                    for key in (
                        reversed(payload["rubric"]["dimensions"])
                        if reverse
                        else payload["rubric"]["dimensions"]
                    )
                }
            }
            if reverse:
                assert list(response["dimensions"]) == list(
                    reversed(payload["rubric"]["dimensions"])
                )
            return response

        return judge

    return [
        Judge("claude-code", transport(first)),
        Judge("codex", transport(second, reverse_secondary)),
    ]


def grade(task, rubric, answer, **kwargs):
    return grade_brief(
        task,
        rubric,
        answer,
        judges=kwargs.pop("judges", judges()),
        capture_source=kwargs.pop("capture_source", lambda url: "New costs 7. Old costs 10."),
        **kwargs,
    )


def test_weighted_recall_and_exact_default_edges(task, rubric, answer):
    result = grade(task, rubric, answer, judges=judges({"fact_1": 0, "claim_0": 0}))
    assert result["key_fact_recall"] == 0.7
    assert result["unsupported_claim_rate"] == 0.1
    assert result["passed"]
    assert result["agreement"]["verdict_disputed"] is False


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"fact_0": 0}, False),
        # The default allows at most a quarter of claims unsupported (operator,
        # 2026-10-03): two of ten passes, three of ten fails.
        ({"claim_0": 0, "claim_1": 0}, True),
        ({"claim_0": 0, "claim_1": 0, "claim_2": 0}, False),
        ({}, True),
    ],
)
def test_default_threshold_failures(task, rubric, answer, changes, expected):
    assert grade(task, rubric, answer, judges=judges(changes))["passed"] is expected


@pytest.mark.parametrize(
    "recommendation,expected", [("new", True), ("alternate", True), ("NEW", False), ("old", False)]
)
def test_deterministic_acceptable_set(task, rubric, answer, recommendation, expected):
    answer["recommendation"] = recommendation
    assert grade(task, rubric, answer)["passed"] is expected


def test_custom_rule_and_research(task, rubric, answer):
    answer["recommendation"] = "old"
    rubric["pass_rule"] = {
        "recommendation_correct": False,
        "min_recall": 0.3,
        "max_unsupported_claim_rate": 0.2,
    }
    result = grade(task, rubric, answer, judges=judges({"fact_0": 0, "claim_0": 0, "claim_1": 0}))
    assert result["passed"]
    task["family"] = "research-brief"
    del answer["recommendation"]
    assert grade(task, rubric, answer)["decision_correct"] is None
    assert grade(task, rubric, answer)["passed"]
    assert "recommendation" in brief_schema(decision=True)["required"]
    assert "recommendation" not in brief_schema()["required"]


def test_cohens_kappa_disagreement(task, rubric, answer):
    answer["claims"] = []
    result = grade(task, rubric, answer, judges=judges({"fact_1": 0}, {"fact_0": 0}))
    assert result["agreement"]["cohens_kappa"] == -1
    assert result["agreement"]["disagreements"] == ["fact_0", "fact_1"]
    answer["claims"] = [{"text": "true", "citation_urls": ["https://primary.org/prices"]}]
    result = grade(task, rubric, answer, judges=judges({}, {"fact_0": 0}))
    assert result["agreement"]["verdict_disputed"]
    assert result["passed"]  # primary is authoritative
    assert result["agreement"]["cohens_kappa"] == 0
    assert grade(task, rubric, answer)["agreement"]["cohens_kappa"] is None


@pytest.mark.parametrize(
    "secondary,expected", [({"fact_1": 0, "claim_0": 0}, 1), ({"claim_0": 0}, 0.4)]
)
def test_cohens_kappa_ignores_judge_key_order(task, rubric, answer, secondary, expected):
    answer["claims"] = answer["claims"][:1]
    primary = {"fact_1": 0, "claim_0": 0}
    ordered = grade(task, rubric, answer, judges=judges(primary, secondary))
    reordered = grade(
        task, rubric, answer, judges=judges(primary, secondary, reverse_secondary=True)
    )
    assert list(reordered["judge_scores"][0]["labels"]) == ["fact_0", "fact_1", "claim_0"]
    assert list(reordered["judge_scores"][1]["labels"]) == ["fact_0", "fact_1", "claim_0"]
    assert reordered["agreement"] == ordered["agreement"]
    assert reordered["agreement"]["cohens_kappa"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "secondary,expected", [({"fact_1": 0, "claim_0": 0}, 1), ({"claim_0": 0}, 0.4)]
)
def test_cohens_kappa_aligns_framework_labels(
    task, rubric, answer, monkeypatch, secondary, expected
):
    answer["claims"] = answer["claims"][:1]
    primary = {"fact_1": 0, "claim_0": 0}
    original = brief_grade_module.judge_deliverable

    def reversed_labels(*args, **kwargs):
        result = original(*args, **kwargs)
        entry = result["judges"][1]
        entry["dimensions"] = dict(reversed(list(entry["dimensions"].items())))
        return result

    monkeypatch.setattr(brief_grade_module, "judge_deliverable", reversed_labels)
    result = grade(task, rubric, answer, judges=judges(primary, secondary))
    assert list(result["judge_scores"][1]["labels"]) == ["claim_0", "fact_1", "fact_0"]
    assert result["agreement"]["cohens_kappa"] == pytest.approx(expected)


def test_citation_schema_requires_nonblank_strings():
    for decision in (False, True):
        items = brief_schema(decision=decision)["properties"]["claims"]["items"]["properties"][
            "citation_urls"
        ]["items"]
        assert items == {"type": "string", "minLength": 1, "pattern": r"\S"}


def test_capture_cache_failure_and_no_citation(task, rubric, answer):
    calls = []

    def capture(url):
        calls.append(url)
        raise OSError("cannot capture")

    result = grade(task, rubric, answer, capture_source=capture)
    assert calls == ["https://primary.org/prices"]
    assert result["unsupported_claim_rate"] == 1
    assert len(result["forced_unsupported_claims"]) == 10
    assert result["source_snapshots"][0]["status"] == "uncapturable"
    assert not result["passed"]
    answer["claims"][0]["citation_urls"] = []
    result = grade(task, rubric, answer)
    assert result["forced_unsupported_claims"] == [0]
    assert result["unsupported_claim_rate"] == 0.1


@pytest.mark.parametrize("capture", [lambda url: "", lambda url: None])
def test_empty_capture_unsupported(task, rubric, answer, capture):
    assert grade(task, rubric, answer, capture_source=capture)["unsupported_claim_rate"] == 1


@pytest.mark.parametrize("suffix", ["truncated", "member", "garbage"])
def test_compressed_capture_failure_records_reason_outside_judge_evidence(
    task, rubric, answer, suffix
):
    import gzip

    from sew.gap.calibrate import _decode_content

    raw = gzip.compress(b"New costs 7.")
    if suffix == "truncated":
        raw = raw[:-1]
        reason = "incomplete gzip source"
    else:
        raw += gzip.compress(b"Only with annual billing.") if suffix == "member" else b"garbage"
        reason = "unexpected trailing data in gzip source"
    seen = []
    result = grade(
        task,
        rubric,
        answer,
        capture_source=lambda url: _decode_content(raw, "gzip"),
        judges=judges(seen=seen),
    )
    snapshot = result["source_snapshots"][0]
    assert snapshot["status"] == "uncapturable"
    assert snapshot["text"] is None
    assert snapshot["error"] == "ValueError"
    assert snapshot["error_detail"] == reason
    assert result["unsupported_claim_rate"] == 1
    assert not result["passed"]
    assert all(payload["deliverable"]["sources"] == [] for payload in seen)
    assert reason not in json.dumps(seen)


def test_capture_failure_detail_is_bounded_and_retains_final_reason(task, rubric, answer):
    reason = "unexpected trailing data in gzip source"

    def capture(url):
        raise ValueError("context\n" * 500 + reason)

    snapshot = grade(task, rubric, answer, capture_source=capture)["source_snapshots"][0]
    assert len(snapshot["error_detail"]) == 2000
    assert snapshot["error_detail"].endswith(reason)


def test_payload_contains_evidence_and_blinds_all_content(task, rubric, answer):
    seen = []
    answer["brief"] += " codex+perplexity https://perplexity.ai/search"
    rubric["key_facts"][0]["source_snapshot"]["text"] += " claude-code"
    result = grade(
        task, rubric, answer, judges=judges(seen=seen), arm=ArmIdentity("codex+perplexity")
    )
    assert seen[0] == seen[1]
    assert "New costs 7" in seen[0]["deliverable"]["facts"][0]["source_snapshot"]["text"]
    deliverable = seen[0]["deliverable"]
    assert deliverable["claims"][0]["source_ids"] == ["S1"]
    assert "Old costs 10" in deliverable["sources"][0]["text"]
    assert "perplexity" not in json.dumps(seen[0])
    assert result["source_snapshots"][0]["url"] == "https://primary.org/prices"


def test_source_content_changes_support_without_url_scoring(task, rubric, answer):
    def check(payload):
        deliverable = payload["deliverable"]
        texts = {source["id"]: source["text"] for source in deliverable["sources"]}
        supported = all("New costs 7" in texts[i] for i in deliverable["claims"][0]["source_ids"])
        return {
            "dimensions": {
                k: {
                    "score": int(supported) if k.startswith("claim_") else 1,
                    "reason": "content check",
                }
                for k in payload["rubric"]["dimensions"]
            }
        }

    pair = [Judge("claude-code", check), Judge("codex", check)]
    assert grade(task, rubric, answer, judges=pair)["passed"]
    assert not grade(task, rubric, answer, judges=pair, capture_source=lambda url: "Old costs 2")[
        "passed"
    ]


def test_each_cited_source_reaches_the_judges_once(task, rubric, answer):
    # Ten claims cite one page: the page is sent once and every claim names it.
    seen = []
    answer["claims"].append(
        {"text": "fact 10", "citation_urls": ["https://primary.org/prices", "https://other.org/a"]}
    )
    result = grade(task, rubric, answer, judges=judges(seen=seen))
    deliverable = seen[0]["deliverable"]
    assert [source["id"] for source in deliverable["sources"]] == ["S1", "S2"]
    assert [claim["source_ids"] for claim in deliverable["claims"]] == [["S1"]] * 10 + [
        ["S1", "S2"]
    ]
    assert "sources" not in deliverable["claims"][0]
    assert result["status"] == "scored"


def test_uncaptured_sources_are_not_sent_and_still_force_unsupported(task, rubric, answer):
    seen = []
    answer["claims"][0]["citation_urls"].append("https://gone.org/404")

    def capture(url):
        if "gone" in url:
            raise ValueError("404")
        return "New costs 7. Old costs 10."

    result = grade(task, rubric, answer, judges=judges(seen=seen), capture_source=capture)
    deliverable = seen[0]["deliverable"]
    assert [source["id"] for source in deliverable["sources"]] == ["S1"]
    assert deliverable["claims"][0]["source_ids"] == ["S1"]
    assert result["forced_unsupported_claims"] == [0]
    assert result["judge_scores"][0]["labels"]["claim_0"] == 0


@pytest.mark.parametrize(
    "page",
    [
        "Unsupported configurations\nPython 3.10.21 fixes the tarfile filter\n" + "detail\n" * 30,
        "Python 3.10.21 fixes the tarfile filter " + "detail " * 30 + "except on Windows.",
    ],
    ids=["governing-heading", "qualification-beyond-cap"],
)
def test_oversized_evidence_never_becomes_an_apparent_assertion(
    task, rubric, answer, monkeypatch, page
):
    # A source over the cap is withheld whole, never sliced, and the claim citing
    # it is forced unsupported; the rest of the cell is still graded.
    monkeypatch.setattr(brief_grade_module, "SOURCE_CHAR_CAP", 80)
    answer["claims"] = [
        {
            "text": "Python 3.10.21 fixes the tarfile filter",
            "citation_urls": ["https://python.org/changelog"],
        }
    ]
    seen = []
    result = grade(task, rubric, answer, judges=judges(seen=seen), capture_source=lambda url: page)
    assert result["status"] == "scored"
    deliverable = seen[0]["deliverable"]
    assert deliverable["sources"] == []
    assert deliverable["claims"][0]["source_ids"] == []
    assert result["forced_unsupported_claims"] == [0]
    assert result["judge_scores"][0]["labels"]["claim_0"] == 0
    assert not result["passed"]
    snapshot = result["source_snapshots"][0]
    assert snapshot["text"] == page
    assert snapshot["judged_chars"] == 0 and snapshot["excerpted"] is False
    assert snapshot["withheld"] == "source_char_cap_exceeded"


def test_complete_source_retains_governing_heading_and_qualification(task, rubric, answer):
    page = "Unsupported configurations\nNew costs 7.\nThis pricing does not apply to Windows."
    seen = []
    result = grade(task, rubric, answer, judges=judges(seen=seen), capture_source=lambda url: page)
    assert result["status"] == "scored"
    assert seen[0]["deliverable"]["sources"][0]["text"] == page
    snapshot = result["source_snapshots"][0]
    assert snapshot["judged_chars"] == len(page) and snapshot["excerpted"] is False


@pytest.mark.parametrize("count,length,judged", [(16, 20_000, 15), (160, 8_000, 37)])
def test_sources_past_the_aggregate_budget_are_withheld_in_citation_order(
    task, rubric, answer, count, length, judged
):
    answer["claims"] = [
        {"text": f"A cited fact {i}", "citation_urls": [f"https://example.org/{i}"]}
        for i in range(count)
    ]
    seen = []
    result = grade(
        task, rubric, answer, judges=judges(seen=seen), capture_source=lambda url: "a" * length
    )
    assert result["status"] == "scored"
    sources = seen[0]["deliverable"]["sources"]
    assert [source["id"] for source in sources] == [f"S{i + 1}" for i in range(judged)]
    assert sum(len(source["text"]) for source in sources) <= brief_grade_module.SOURCES_CHAR_BUDGET
    assert result["forced_unsupported_claims"] == list(range(judged, count))
    snapshots = result["source_snapshots"]
    assert all(s["judged_chars"] == length and "withheld" not in s for s in snapshots[:judged])
    assert all(
        s["judged_chars"] == 0 and s["withheld"] == "sources_char_budget_exceeded"
        for s in snapshots[judged:]
    )


@pytest.mark.parametrize("count,length", [(3, 100_000), (30, 10_000), (160, 1_875)])
def test_complete_sources_at_both_budget_boundaries_are_graded(task, rubric, answer, count, length):
    answer["claims"] = [
        {
            "text": "A cited fact",
            "citation_urls": [f"https://example.org/{i}" for i in range(count)],
        }
    ]
    seen = []
    result = grade(
        task, rubric, answer, judges=judges(seen=seen), capture_source=lambda url: "a" * length
    )
    assert result["status"] == "scored"
    assert result["forced_unsupported_claims"] == []
    sources = seen[0]["deliverable"]["sources"]
    assert len(sources) == count
    assert sum(len(source["text"]) for source in sources) == brief_grade_module.SOURCES_CHAR_BUDGET
    assert all(source["text"] == "a" * length for source in sources)
    assert all(
        s["judged_chars"] == length and not s["excerpted"] and "withheld" not in s
        for s in result["source_snapshots"]
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda a: a.pop("brief"),
        lambda a: a.pop("recommendation"),
        lambda a: a.update(arm="native"),
        lambda a: a["claims"][0].update(text=5),
        lambda a: a["claims"][0].update(citation_urls="url"),
        lambda a: a["claims"][0].update(citation_urls=[""]),
        lambda a: a["claims"][0].update(citation_urls=[" \t\n"]),
    ],
)
def test_schema_invalid_never_calls_judge(task, rubric, answer, change):
    change(answer)
    seen = []
    assert grade(task, rubric, answer, judges=judges(seen=seen))["status"] == "schema_invalid"
    assert seen == []


@pytest.mark.parametrize("failed_index", [0, 1], ids=["primary", "secondary"])
@pytest.mark.parametrize("failure", ["error", "invalid"])
def test_judge_error_is_ungraded(task, rubric, answer, failed_index, failure):
    def fail(payload):
        if failure == "error":
            raise OSError("judge transport unavailable")
        return {}

    pair = judges()
    pair[failed_index] = Judge(pair[failed_index].judge_id, fail)
    result = grade(task, rubric, answer, judges=pair)
    assert result["status"] == "judge_unavailable"
    assert result["outcome"] == "not_applicable"
    assert not result["passed"]
    assert result["judge_record"]["judges"][failed_index]["status"] == failure
    assert result["judge_record"]["judges"][1 - failed_index]["status"] == "scored"
    assert result["agreement"] == {"status": "not_measured", "reason": "judge_unscored"}
    assert "judge_scores" not in result
    with pytest.raises(ValueError):
        grade(task, rubric, answer, judges=pair[1:])  # codex can never grade alone


@pytest.mark.parametrize(
    "field,value", [("weight", 0), ("weight", float("nan")), ("source_snapshot", {}), ("text", "")]
)
def test_bad_fact_refused(task, rubric, answer, field, value):
    rubric["key_facts"][0][field] = value
    with pytest.raises(SchemaError):
        grade(task, rubric, answer)


def test_rubric_loader_and_rules(tmp_path, task, rubric, answer):
    task["rubric"] = "rubric.json"
    (tmp_path / "rubric.json").write_text(json.dumps(rubric))
    assert load_brief_rubric(task, tmp_path) == rubric
    task["rubric"] = "../rubric.json"
    with pytest.raises(SchemaError):
        load_brief_rubric(task, tmp_path)
    for rule in (
        {"min_recall": 1.1},
        {"max_unsupported_claim_rate": -1},
        {"recommendation_correct": 1},
        {"unknown": 1},
    ):
        rubric["pass_rule"] = rule
        with pytest.raises(SchemaError):
            grade(task, rubric, answer)


def test_mixed_citations_fail_closed_and_cache_success(task, rubric, answer):
    calls = []

    def capture(url):
        calls.append(url)
        if url.endswith("missing"):
            raise OSError()
        return "exact source text"

    answer["claims"][0]["citation_urls"].append("https://primary.org/missing")
    result = grade(task, rubric, answer, capture_source=capture)
    assert calls == ["https://primary.org/prices", "https://primary.org/missing"]
    assert result["unsupported_claim_rate"] == 0.1
    assert result["source_snapshots"][0]["text"] == "exact source text"
    assert result["source_snapshots"][0]["captured_at"]
    assert result["judge_scores"][0]["labels"]["claim_0"] == 0
    assert result["judge_scores"][1]["labels"]["claim_0"] == 0


def test_empty_claims_do_not_pass_faithfulness(task, rubric, answer):
    answer["claims"] = []
    result = grade(task, rubric, answer)
    assert result["unsupported_claim_rate"] == 1
    assert not result["passed"]


def test_judge_identity_model_and_usage_retained(task, rubric, answer):
    pair = judges()
    pair[0].transport.model_id = "fixture-model"
    pair[0].transport.usage = {"input": 100, "output": 10}
    result = grade(task, rubric, answer, judges=pair)
    assert result["judge_scores"][0]["judge_id"] == "claude-code"
    assert result["judge_scores"][1]["judge_id"] == "codex"
    assert result["judge_record"]["judges"][0]["token_usage"] == {"input": 100, "output": 10}
    assert result["judge_record"]["judges"][0]["model_id"] == "fixture-model"


def test_single_primary_judge_decides_and_leaves_agreement_unmeasured(task, rubric, answer):
    two = grade(task, rubric, answer, judges=judges({"fact_1": 0}, {"fact_0": 0}))
    one = grade(task, rubric, answer, judges=judges({"fact_1": 0})[:1])
    assert one["status"] == "scored" and one["passed"] == two["passed"]
    assert one["key_fact_recall"] == two["key_fact_recall"]
    assert one["agreement"] == {"status": "not_measured", "reason": "single_judge"}
    assert [s["judge_id"] for s in one["judge_scores"]] == [two["judge_scores"][0]["judge_id"]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ("claude-code", "codex")), ("", ("claude-code", "codex")), ("claude-code", ("claude-code",)),
     (" claude-code , codex ", ("claude-code", "codex"))],
)
def test_gap_judge_selection(value, expected):
    from sew.gap.judges import brief_judge_harnesses

    assert brief_judge_harnesses({} if value is None else {"SEW_GAP_JUDGES": value}) == expected


@pytest.mark.parametrize("value", ["gemini", "codex", "codex,claude-code", "claude-code,claude-code", ","])
def test_gap_judge_selection_rejects_unknown_or_repeated(value):
    from sew.gap.judges import brief_judge_harnesses

    with pytest.raises(ValueError):
        brief_judge_harnesses({"SEW_GAP_JUDGES": value})


def test_codex_cannot_grade_alone_or_first(task, rubric, answer):
    pair = judges()
    for wrong in ([pair[1]], [pair[1], pair[0]]):
        with pytest.raises(ValueError):
            grade(task, rubric, answer, judges=wrong)
