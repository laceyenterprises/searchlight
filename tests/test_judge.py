"""Offline tests for the WSB-02 blinded judge harness.

No test calls a model. Judges are stub transports, and two of them are
deliberately adversarial: one awards points to any provider it can recognise,
one reads only the blinded payload the way a real judge would. The first is
what makes the identical-score test mean something -- a judge that ignores the
payload would pass it trivially.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import pytest

from sew.judge import (
    REDACTED,
    ArmIdentity,
    BlindingError,
    Judge,
    blind_for_judge,
    build_judge_payload,
    find_identity_leaks,
    judge_deliverable,
    payload_digest,
    quadratic_weighted_kappa,
    resolve_rubric,
    summarize_agreement,
)
from sew.production_catalog import load_production_catalog
from sew.schema import SchemaError

# The WSB arm set (SPEC §10 naming), each with the identifiers a real cell of
# that arm would carry: label, harness, provider, model and exposed tools.
ARMS = (
    ArmIdentity(
        "claude-code+exa", "claude-code", "exa", "claude-opus-5-5", ("mcp__exa__web_search_exa",)
    ),
    ArmIdentity(
        "claude-code+parallel",
        "claude-code",
        "parallel-web",
        "claude-sonnet-5",
        ("mcp__parallel__web_search",),
    ),
    ArmIdentity(
        "claude-code+firecrawl",
        "claude-code",
        "firecrawl",
        "claude-opus-5-5",
        ("mcp__firecrawl__firecrawl_scrape",),
    ),
    ArmIdentity("claude-code+native", "claude-code", None, "claude-opus-5-5", ("WebSearch",)),
    ArmIdentity("claude-code+no-search", "claude-code", None, "claude-opus-5-5"),
    ArmIdentity("codex+exa", "codex", "exa", "gpt-5-codex", ("exa.web_search",)),
    ArmIdentity("codex+native", "codex", None, "gpt-5-codex", ("web_search",)),
    ArmIdentity("exa-agent", None, "exa", None, ("exa-research",)),
)
PROVIDER_HOSTS = {
    "exa": "exa.ai",
    "parallel-web": "parallel.ai",
    "firecrawl": "firecrawl.dev",
    None: "platform.openai.com",
}


@pytest.fixture(scope="module")
def catalog() -> dict[str, Any]:
    return load_production_catalog()


@pytest.fixture(scope="module")
def list_task(catalog: dict[str, Any]) -> dict[str, Any]:
    return next(
        t for t in catalog["catalog"]["tasks"] if t["id"] == "list-build-sca-tool-shortlist"
    )


@pytest.fixture(scope="module")
def list_rubric(catalog: dict[str, Any], list_task: dict[str, Any]) -> dict[str, Any]:
    return resolve_rubric(list_task, catalog["rubrics"])


def good_shortlist() -> dict[str, Any]:
    names = ("osv-scanner", "grype", "trivy", "dependency-check", "syft", "retire")
    return {
        "candidates": [
            {
                "project": name,
                "license": "Apache-2.0",
                "offline_capable": "yes, with a mirrored advisory database",
                "latest_release": "v2.1.0",
                "latest_release_date": "2026-08-01",
                "source_url": f"https://github.com/example/{name}/releases/tag/v2.1.0",
            }
            for name in names
        ],
        "exclusions": [{"project": "a hosted scanner", "reason": "hosted-only service"}],
        "evidence_urls": [
            "https://github.com/example/osv-scanner/blob/0123456789abcdef/README.md",
            "https://github.com/example/grype",
        ],
    }


def as_arm_output(deliverable: dict[str, Any], arm: ArmIdentity) -> dict[str, Any]:
    """Wrap a deliverable the way a leaky arm would emit it."""

    host = PROVIDER_HOSTS[arm.provider_id]
    tool = arm.tool_names[0] if arm.tool_names else "no-search"
    wrapped = json.loads(json.dumps(deliverable))
    wrapped["exclusions"].append(
        {
            "project": "a second hosted scanner",
            "reason": (
                f"Found by {arm.arm_id} ({arm.harness_id or arm.provider_id} on "
                f"{arm.model_id or 'gpt-5'}) using {tool}; see https://{host}/search?q=sca "
                f"and {host}. Checked via {arm.provider_id or arm.harness_id}."
            ),
        }
    )
    wrapped["evidence_urls"].append(f"https://docs.{host}/results/42")
    wrapped["arm"] = arm.arm_id
    wrapped["provider_id"] = arm.provider_id
    wrapped["tool_calls"] = [{"name": name, "args": {"q": "sca"}} for name in arm.tool_names]
    return wrapped


def fixed_judge(scores: Mapping[str, int], judge_id: str = "judge-a") -> Judge:
    def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "model_profile": judge_id,
            "dimensions": {
                name: {"score": scores[name], "reason": f"{name} scored {scores[name]}"}
                for name in payload["rubric"]["dimensions"]
            },
        }

    return Judge(judge_id, transport)


def provider_biased_judge() -> Judge:
    """Scores a deliverable higher the more provider names it can still see."""

    def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        text = json.dumps(payload).casefold()
        bias = sum(text.count(word) for word in ("exa", "parallel", "firecrawl", "claude", "codex"))
        digest = int(payload_digest(payload)[:8], 16)
        return {
            "dimensions": {
                name: {
                    "score": 1 + (digest + index + bias) % 5,
                    "reason": f"bias={bias}",
                }
                for index, name in enumerate(payload["rubric"]["dimensions"])
            }
        }

    return Judge("biased", transport)


def reading_judge() -> Judge:
    """A rule-based judge that reads only the blinded payload, as a model would."""

    def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        deliverable = payload["deliverable"] if isinstance(payload["deliverable"], dict) else {}
        candidates = deliverable.get("candidates") or []
        fields = ("project", "license", "offline_capable", "latest_release", "source_url")
        complete = bool(candidates) and all(all(c.get(f) for f in fields) for c in candidates)
        cited = bool(candidates) and all(
            str(c.get("source_url", "")).startswith("[source-") for c in candidates
        )
        scores = {
            "criteria_conformance": (5 if len(candidates) >= 6 else 2, "count vs the floor of six"),
            "enrichment_completeness": (5 if complete else 1, "every column populated"),
            "primary_source_grounding": (5 if cited else 1, "each row carries a citation"),
            "exclusion_discipline": (5 if deliverable.get("exclusions") else 1, "exclusions"),
            "honest_uncertainty": (4, "no overclaiming observed"),
        }
        return {
            "dimensions": {
                name: {"score": score, "reason": reason} for name, (score, reason) in scores.items()
            }
        }

    return Judge("reader", transport)


# --------------------------------------------------------------------------
# Blinding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", ARMS, ids=lambda arm: arm.arm_id)
def test_blinding_strips_every_arm_identifying_token(list_task, list_rubric, arm) -> None:
    payload, report = blind_for_judge(
        list_task, list_rubric, as_arm_output(good_shortlist(), arm), arm
    )

    assert find_identity_leaks(payload, arm) == []
    text = json.dumps(payload).casefold()
    for identifier in arm.identifiers():
        assert identifier.casefold() not in text, identifier
    for host in PROVIDER_HOSTS.values():
        assert host not in text
    assert "://" not in text
    # Trace fields never reach the judge, and the report says they were cut.
    assert {"arm", "provider_id", "tool_calls"} <= set(report["dropped_fields"])
    assert report["redacted_spans"] > 0


def test_every_rubric_task_in_the_catalog_blinds_cleanly(catalog) -> None:
    # Guards the catalog side: a prompt or rubric that names a provider would
    # fail every arm's leak check, and it should fail here first.
    rubric_tasks = [t for t in catalog["catalog"]["tasks"] if "judge_rubric" in t]
    assert rubric_tasks
    for task in rubric_tasks:
        payload, _ = build_judge_payload(task, resolve_rubric(task, catalog["rubrics"]), {})
        assert find_identity_leaks(payload) == [], task["id"]
        assert REDACTED not in json.dumps([payload["task"], payload["rubric"]]), task["id"]


def test_citations_become_opaque_refs_that_keep_their_url_shape(list_task, list_rubric) -> None:
    payload, report = build_judge_payload(list_task, list_rubric, good_shortlist())

    assert payload["deliverable"]["evidence_urls"] == ["[source-7]", "[source-8]"]
    shapes = {c["ref"]: c["shape"] for c in payload["citations"]}
    assert shapes["source-1"] == "release_or_tag"
    assert shapes["source-7"] == "commit_pinned"
    assert shapes["source-8"] == "document"
    assert report["citations_normalized"] == 8


def test_blinding_projects_the_deliverable_onto_its_declared_schema(list_task, list_rubric) -> None:
    deliverable = good_shortlist()
    deliverable["candidates"][0]["search_highlights"] = "provider-shaped extra"
    deliverable["notes"] = "unrequested field"

    payload, report = build_judge_payload(list_task, list_rubric, deliverable)

    assert "search_highlights" not in payload["deliverable"]["candidates"][0]
    assert "notes" not in payload["deliverable"]
    assert report["dropped_fields"] == ["candidates[0].search_highlights", "notes"]


def test_unknown_model_name_fails_closed_before_any_judge_runs(list_task, list_rubric) -> None:
    arm = ArmIdentity("claude-code+exa", "claude-code", "exa", "mistral-large-2")
    deliverable = good_shortlist()
    deliverable["exclusions"][0]["reason"] = "per mistral-large-2, hosted-only"
    calls: list[Any] = []

    def transport(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append(payload)
        return {}

    with pytest.raises(BlindingError, match="mistral-large-2"):
        blind_for_judge(list_task, list_rubric, deliverable, arm)
    record = judge_deliverable(
        list_task, list_rubric, deliverable, judges=[Judge("j", transport)], arm=arm
    )

    assert calls == []
    assert record["status"] == "blinding_failed"
    assert record["passed"] is False
    assert record["blinding"]["leaks"] == ["arm_identifier:mistral-large-2"]


# --------------------------------------------------------------------------
# Identical deliverables, different arm labels
# --------------------------------------------------------------------------


def test_identical_deliverables_under_different_arm_labels_score_identically(
    list_task, list_rubric
) -> None:
    base = good_shortlist()
    records = [
        judge_deliverable(
            list_task,
            list_rubric,
            as_arm_output(base, arm),
            judges=[provider_biased_judge(), reading_judge()],
            arm=arm,
        )
        for arm in ARMS
    ]

    assert {record["status"] for record in records} == {"scored"}
    assert len({record["payload_sha256"] for record in records}) == 1
    comparable = [{k: v for k, v in record.items() if k != "arm_id"} for record in records]
    assert all(record == comparable[0] for record in comparable)
    # The biased judge saw no provider name to reward.
    assert all(
        entry["reason"] == "bias=0" for entry in records[0]["judges"][0]["dimensions"].values()
    )


def test_payload_is_independent_of_key_order(list_task, list_rubric) -> None:
    deliverable = good_shortlist()
    reordered = {key: deliverable[key] for key in reversed(list(deliverable))}

    first, _ = build_judge_payload(list_task, list_rubric, deliverable)
    second, _ = build_judge_payload(list_task, list_rubric, reordered)

    assert payload_digest(first) == payload_digest(second)


# --------------------------------------------------------------------------
# Per-dimension scoring
# --------------------------------------------------------------------------


def test_a_deliberately_bad_deliverable_scores_below_threshold(list_task, list_rubric) -> None:
    good = judge_deliverable(list_task, list_rubric, good_shortlist(), judges=[reading_judge()])
    bad_deliverable = {
        "candidates": [{"project": "one tool", "license": "", "source_url": "trust me"}],
        "exclusions": [],
        "evidence_urls": [],
    }
    bad = judge_deliverable(list_task, list_rubric, bad_deliverable, judges=[reading_judge()])

    assert good["passed"] is True
    assert bad["passed"] is False
    assert bad["status"] == "scored"
    assert bad["failed_dimensions"] == [
        "criteria_conformance",
        "enrichment_completeness",
        "primary_source_grounding",
        "exclusion_discipline",
    ]
    for name in bad["failed_dimensions"]:
        assert bad["dimensions"][name]["score"] < bad["minimum_score"]
        assert bad["dimensions"][name]["reason"]


def test_one_failed_dimension_is_not_averaged_away(list_task, list_rubric) -> None:
    scores = dict.fromkeys(list_rubric["dimensions"], 5)
    scores["exclusion_discipline"] = 2  # mean 4.4 clears a minimum of 4
    record = judge_deliverable(
        list_task, list_rubric, good_shortlist(), judges=[fixed_judge(scores)]
    )

    assert record["passed"] is False
    assert record["failed_dimensions"] == ["exclusion_discipline"]
    assert "score" not in record  # there is no holistic number to quote


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"score": 5, "reason": "great"}, "dimensions_missing"),
        ({"dimensions": {}}, "dimension_missing:criteria_conformance"),
        (
            {"dimensions": {"criteria_conformance": {"score": 5, "reason": " "}}},
            "reason_missing:criteria_conformance",
        ),
        (
            {"dimensions": {"criteria_conformance": {"score": 9, "reason": "x"}}},
            "score_out_of_scale:criteria_conformance",
        ),
        (
            {"dimensions": {"criteria_conformance": {"score": True, "reason": "x"}}},
            "score_not_integer:criteria_conformance",
        ),
        ({"dimensions": {"vibes": {"score": 5, "reason": "x"}}}, "unknown_dimension:vibes"),
        ("not a mapping", "dimensions_missing"),
    ],
)
def test_malformed_judge_results_are_invalid_not_scored(
    list_task, list_rubric, response, error
) -> None:
    record = judge_deliverable(
        list_task, list_rubric, good_shortlist(), judges=[Judge("j", lambda _p: response)]
    )

    assert record["status"] == "judge_invalid_result"
    assert record["passed"] is False
    assert error in record["judges"][0]["errors"]


def test_a_transport_failure_is_judge_unavailable(list_task, list_rubric) -> None:
    def transport(_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise TimeoutError("judge did not answer")

    record = judge_deliverable(
        list_task, list_rubric, good_shortlist(), judges=[Judge("j", transport)]
    )

    assert record["status"] == "judge_unavailable"
    assert record["passed"] is False
    assert record["judges"][0]["errors"] == ["transport:TimeoutError"]


def test_each_judge_gets_its_own_copy_of_the_payload(list_task, list_rubric) -> None:
    seen: list[str] = []

    def vandal(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        payload["deliverable"]["candidates"].clear()  # type: ignore[index]
        return reading_judge().transport(payload)

    def witness(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        seen.append(payload_digest(payload))
        return reading_judge().transport(payload)

    record = judge_deliverable(
        list_task,
        list_rubric,
        good_shortlist(),
        judges=[Judge("vandal", vandal), Judge("witness", witness)],
    )

    assert seen == [record["payload_sha256"]]


def test_duplicate_or_missing_judges_are_refused(list_task, list_rubric) -> None:
    with pytest.raises(ValueError, match="at least one judge"):
        judge_deliverable(list_task, list_rubric, good_shortlist(), judges=[])
    with pytest.raises(ValueError, match="distinct"):
        judge_deliverable(
            list_task, list_rubric, good_shortlist(), judges=[reading_judge(), reading_judge()]
        )


def test_resolve_rubric_applies_the_task_minimum(catalog, list_task) -> None:
    task = {**list_task, "judge_rubric": {"rubric_id": "wsb01-list-build-v1", "minimum_score": 3}}
    assert resolve_rubric(task, catalog["rubrics"])["minimum_score"] == 3
    with pytest.raises(SchemaError, match="unknown rubric"):
        resolve_rubric({**task, "judge_rubric": {"rubric_id": "nope"}}, catalog["rubrics"])


# --------------------------------------------------------------------------
# Inter-judge agreement
# --------------------------------------------------------------------------


def test_inter_judge_agreement_is_computed_and_reported(list_task, list_rubric) -> None:
    dimensions = list_rubric["dimensions"]
    primary = dict.fromkeys(dimensions, 5)
    secondary = {**primary, "honest_uncertainty": 4, "exclusion_discipline": 2}

    record = judge_deliverable(
        list_task,
        list_rubric,
        good_shortlist(),
        judges=[fixed_judge(primary, "judge-a"), fixed_judge(secondary, "judge-b")],
    )
    agreement = record["agreement"]

    # The first judge decides; the second is a measurement, not a vote.
    assert record["passed"] is True
    assert agreement["status"] == "measured"
    assert agreement["judges"] == 2
    assert agreement["dimensions"]["exclusion_discipline"] == {
        "scores": [5, 2],
        "spread": 3,
        "verdict_agrees": False,
    }
    assert agreement["exact_agreement"] == pytest.approx(3 / 5)
    assert agreement["within_one_agreement"] == pytest.approx(4 / 5)
    assert agreement["dimension_verdict_agreement"] == pytest.approx(4 / 5)
    assert agreement["max_spread"] == 3
    assert agreement["verdict_disputed"] is True

    summary = summarize_agreement([record, record])
    assert summary["status"] == "measured"
    assert summary["records_measured"] == 2
    assert summary["verdicts_disputed"] == 2
    assert summary["dimension_pairs"] == 10
    assert summary["exact_agreement"] == pytest.approx(3 / 5)
    assert summary["verdict_agreement"] == pytest.approx(4 / 5)
    assert summary["by_dimension"]["wsb01-list-build-v1/exclusion_discipline"]["n"] == 2
    assert summary["quadratic_weighted_kappa"] is not None


def test_agreement_is_never_implied_when_it_was_not_measured(list_task, list_rubric) -> None:
    single = judge_deliverable(list_task, list_rubric, good_shortlist(), judges=[reading_judge()])

    def broken(_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("down")

    half = judge_deliverable(
        list_task,
        list_rubric,
        good_shortlist(),
        judges=[reading_judge(), Judge("second", broken)],
    )

    assert single["agreement"] == {"status": "not_measured", "reason": "single_judge", "judges": 1}
    assert half["passed"] is True
    assert half["agreement"]["reason"] == "judge_unscored"
    assert half["agreement"]["unscored_judges"] == ["second"]
    summary = summarize_agreement([single, half])
    assert summary["status"] == "not_measured"
    assert summary["records_unmeasured"] == 2


@pytest.mark.parametrize(
    ("first", "second", "low", "high", "expected"),
    [
        ([1, 2, 3], [1, 2, 3], 1, 3, 1.0),
        ([1, 1, 2, 2], [1, 2, 1, 2], 1, 2, 0.0),
        ([1, 2], [2, 1], 1, 2, -1.0),
        # Hand-derived: weighted observed 1/16, weighted expected 17/16.
        ([1, 2, 3, 4, 5], [1, 2, 3, 4, 4], 1, 5, 16 / 17),
        ([3, 3, 3], [3, 3, 3], 1, 5, None),
    ],
)
def test_quadratic_weighted_kappa_matches_reference_values(
    first, second, low, high, expected
) -> None:
    kappa = quadratic_weighted_kappa(first, second, minimum=low, maximum=high)
    if expected is None:
        assert kappa is None
    else:
        assert kappa == pytest.approx(expected)


def test_leak_scan_reads_decoded_text_not_json_escapes():
    # A changelog line "from http:// to https://" followed by a newline was
    # flagged as the URL "https://\n#947" because the scan read the JSON form.
    payload = {"deliverable": {"sources": [{"text": "links from http:// to https://\n#947 #958"}]}}
    assert find_identity_leaks(payload) == []
    leaky = {"deliverable": {"brief": "see https://example.org/x\nnext"}}
    assert "url:https://example.org/x" in find_identity_leaks(leaky)
    keyed = {"deliverable": {"https://example.org/key": 1}}
    assert "url:https://example.org/key" in find_identity_leaks(keyed)


@pytest.mark.parametrize("arm", ["native", "floor", "ceiling", "no-search"])
def test_generic_arm_labels_are_not_identity_leaks(arm):
    # A native-arm brief citing a docs page that says "native" was refused.
    payload = {
        "deliverable": {"sources": [{"text": "a native integration; a price floor and ceiling"}]}
    }
    identity = ArmIdentity(f"claude-code+{arm}", "claude-code", arm, "claude-opus-5-5")
    assert find_identity_leaks(payload, identity) == []
    leaky = {"deliverable": {"brief": "written with claude-code"}}
    assert "arm_identifier:claude-code" in find_identity_leaks(leaky, identity)


def test_vendor_provider_names_still_leak():
    identity = ArmIdentity("codex+brave", "codex", "brave", "gpt-6.1-sol")
    payload = {"deliverable": {"brief": "according to Brave results"}}
    assert "arm_identifier:brave" in find_identity_leaks(payload, identity)


def test_url_scan_skips_text_without_scheme_separator(monkeypatch):
    import sew.judge as module

    class UnexpectedScan:
        def sub(self, *args):
            raise AssertionError('URL regex scanned text without a URL separator')

        def finditer(self, *args):
            raise AssertionError('URL regex scanned text without a URL separator')

    monkeypatch.setattr(module, '_URL_PATTERN', UnexpectedScan())
    assert module.find_identity_leaks({'evidence': 'x' * 100_000}) == []
    assert module._Blinder().text('x' * 100_000) == 'x' * 100_000
