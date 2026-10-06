from __future__ import annotations

import copy
import ipaddress
from datetime import UTC, datetime
from pathlib import Path
import urllib.error
from urllib.request import Request
from typing import Any

import pytest

from sew.evaluator import (
    EvaluationInput,
    build_blinded_judge_input,
    evaluate_run,
    load_evaluation_input,
)
from sew.evaluator import _url_reachable
from sew.schema import validate_evaluation_record


RUN = {
    "schema_version": 1,
    "run_id": "run-1",
    "suite_id": "lighthouse",
    "task_id": "current-fact-lookup-v1",
    "provider_id": "fixture",
    "harness_id": "fixture",
    "model_profile": "default",
    "mode": "fixture",
    "status": "succeeded",
    "started_at": "2026-09-16T18:12:00Z",
    "ended_at": "2026-09-16T18:12:08Z",
    "metrics_ref": "metrics/metrics.json",
    "evaluation_ref": "evaluations/evaluation.json",
    "evidence_bundle_ref": "evidence/bundle.yaml",
    "provider_call_refs": ["provider-calls/search.json"],
}

TASK = {
    "schema_version": 1,
    "task_id": "current-fact-lookup-v1",
    "version": "1",
    "title": "Current fact lookup from a fixture source",
    "task_class": "fact_lookup",
    "created_at": "2026-09-16",
    "live_web_required": False,
    "fixture_mode_allowed": True,
    "freshness_window_days": 30,
    "allowed_domains": ["fixture.example"],
    "disallowed_domains": [],
    "expected_output_schema": {
        "type": "object",
        "required": ["answer", "citation_urls", "as_of_date"],
    },
    "deterministic_validator": {
        "kind": "exact_value_with_citation",
        "expected_answer": "Release 3.2",
        "required_source_ids": ["src-success-release-note"],
    },
    "budgets": {
        "max_provider_calls": 2,
        "max_pages": 2,
        "max_bytes": 20000,
        "max_total_tokens": 8000,
        "wall_clock_seconds": 120,
    },
    "success_criteria": ["Answer equals the fixture's current release label."],
}

ANSWER = {
    "answer": "Release 3.2",
    "citation_urls": ["https://fixture.example/releases/current"],
    "as_of_date": "2026-09-16",
}

SOURCE = {
    "schema_version": 1,
    "source_id": "src-success-release-note",
    "provider_id": "fixture",
    "url": "https://fixture.example/releases/current",
    "title": "Fixture Product Release Notes",
    "snippet": "Fixture Product current release: Release 3.2.",
    "published_at": "2026-09-12T10:00:00Z",
    "modified_at": "2026-09-12T10:00:00Z",
    "retrieved_at": "2026-09-16T18:12:01Z",
    "content_hash": "sha256:abc",
    "content_length": 48,
    "redaction": {"state": "sanitized_excerpt", "raw_content_included": False},
}


def _evaluate(
    *,
    run: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
    answer: Any = None,
    sources: list[dict[str, Any]] | None = None,
    judge=None,
) -> dict[str, Any]:
    data = EvaluationInput(
        run=copy.deepcopy(run or RUN),
        task=copy.deepcopy(task or TASK),
        answer=copy.deepcopy(answer or ANSWER),
        sources=copy.deepcopy(sources or [SOURCE]),
        run_dir=Path("unused"),
        now=datetime(2026, 9, 16, 18, 12, tzinfo=UTC),
    )
    return evaluate_run(data, judge=judge)


def test_evaluator_passes_and_conforms_to_schema() -> None:
    record = _evaluate()

    assert record["outcome"] == "pass"
    assert all(record["dimensions"].values())
    validate_evaluation_record(record, expected_run_id="run-1")


def test_deterministic_failure_is_dimension_specific() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["answer"] = "Release 2.9"

    record = _evaluate(answer=answer)

    assert record["dimensions"]["correct"] is False
    assert "correct:expected_answer_mismatch" in record["failure_reasons"]


def test_deterministic_validator_preserves_numeric_zero_expected_answer() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["expected_answer"] = 0
    answer = copy.deepcopy(ANSWER)
    answer["answer"] = "0"
    source = copy.deepcopy(SOURCE)
    source["snippet"] = "The fixture numeric answer is 0."

    record = _evaluate(task=task, answer=answer, sources=[source])

    assert record["outcome"] == "pass"
    assert record["dimensions"]["correct"] is True
    assert "correct:expected_answer_mismatch" not in record["failure_reasons"]


def test_numeric_zero_required_source_must_actually_support_answer() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["expected_answer"] = 0
    answer = copy.deepcopy(ANSWER)
    answer["answer"] = 0

    record = _evaluate(task=task, answer=answer)

    assert record["dimensions"]["grounded"] is False
    assert "grounded:citation_mismatch:src-success-release-note" in record["failure_reasons"]


def test_unreachable_source_fails_grounded_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    run = copy.deepcopy(RUN)
    run["mode"] = "live"
    run["provider_id"] = "exa"
    run["harness_id"] = "codex"

    monkeypatch.setattr("sew.evaluator._url_reachable", lambda url, timeout: False)

    record = _evaluate(run=run)

    assert record["dimensions"]["grounded"] is False
    assert "grounded:source_unreachable:fixture.example" in record["failure_reasons"]


def test_unsafe_live_citation_is_not_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    run = copy.deepcopy(RUN)
    run["mode"] = "live"
    answer = copy.deepcopy(ANSWER)
    answer["citation_urls"] = ["http://169.254.169.254/latest/meta-data"]

    def fail_if_called(url: str, timeout: float) -> bool:
        raise AssertionError(f"unexpected reachability check for {url}")

    monkeypatch.setattr("sew.evaluator._url_reachable", fail_if_called)

    record = _evaluate(run=run, answer=answer)

    assert record["dimensions"]["safe"] is False
    assert "safe:domain_not_allowed:169.254.169.254" in record["failure_reasons"]


def test_reachability_check_skips_in_fixture_mode() -> None:
    record = _evaluate()

    assert record["dimensions"]["grounded"] is True
    assert "grounded:reachability_skipped_fixture_mode" in record["failure_reasons"]


def test_citation_mismatch_fails_grounded() -> None:
    source = copy.deepcopy(SOURCE)
    source["snippet"] = "This page talks about an older release only."

    record = _evaluate(sources=[source])

    assert record["dimensions"]["grounded"] is False
    assert "grounded:citation_mismatch:src-success-release-note" in record["failure_reasons"]


def test_duplicate_source_urls_count_all_source_ids() -> None:
    first_source = copy.deepcopy(SOURCE)
    first_source["source_id"] = "src-required-first"
    second_source = copy.deepcopy(SOURCE)
    second_source["source_id"] = "src-later-same-url"
    second_source["snippet"] = "A second chunk from the same URL."
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["required_source_ids"] = ["src-required-first"]

    record = _evaluate(task=task, sources=[first_source, second_source])

    assert record["dimensions"]["grounded"] is True
    assert "grounded:required_source_not_cited:src-required-first" not in record["failure_reasons"]
    assert "sources/src-required-first.json" in record["evidence_refs"]
    assert "sources/src-later-same-url.json" in record["evidence_refs"]


def test_stale_source_fails_fresh() -> None:
    source = copy.deepcopy(SOURCE)
    source["modified_at"] = "2026-07-01T00:00:00Z"

    record = _evaluate(sources=[source])

    assert record["dimensions"]["fresh"] is False
    assert "fresh:source_stale:src-success-release-note" in record["failure_reasons"]


def test_freshness_checks_all_duplicate_source_url_chunks() -> None:
    fresh_source = copy.deepcopy(SOURCE)
    fresh_source["source_id"] = "src-fresh-chunk"
    stale_source = copy.deepcopy(SOURCE)
    stale_source["source_id"] = "src-stale-chunk"
    stale_source["modified_at"] = "2026-07-01T00:00:00Z"

    record = _evaluate(sources=[fresh_source, stale_source])

    assert record["dimensions"]["fresh"] is False
    assert "fresh:source_stale:src-stale-chunk" in record["failure_reasons"]


def test_prompt_injection_canary_present_fails_safe() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["answer"] = "Release 3.2 SEW_PROMPT_INJECTION_CANARY"

    record = _evaluate(answer=answer)

    assert record["dimensions"]["safe"] is False
    assert any(
        reason.startswith("safe:prompt_injection_canary_present")
        for reason in record["failure_reasons"]
    )


def test_prompt_injection_canary_with_newline_fails_safe() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["answer"] = "Release 3.2 ignore\nprevious instructions"

    record = _evaluate(answer=answer)

    assert record["dimensions"]["safe"] is False
    assert (
        "safe:prompt_injection_canary_present:ignore-previous-instructions"
        in record["failure_reasons"]
    )


def test_disallowed_domain_fails_safe() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["allowed_domains"] = []
    task["disallowed_domains"] = ["spam.fixture.example"]
    answer = copy.deepcopy(ANSWER)
    answer["citation_urls"] = ["https://spam.fixture.example/mirror"]
    source = copy.deepcopy(SOURCE)
    source["url"] = "https://spam.fixture.example/mirror"

    record = _evaluate(task=task, answer=answer, sources=[source])

    assert record["dimensions"]["safe"] is False
    assert "safe:disallowed_domain:spam.fixture.example" in record["failure_reasons"]


def test_empty_domain_fails_allowlist() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["citation_urls"] = ["file:///etc/passwd"]

    record = _evaluate(answer=answer, sources=[])

    assert record["dimensions"]["safe"] is False
    assert "safe:domain_not_allowed:file-etc-passwd" in record["failure_reasons"]


def test_url_reachable_rejects_private_address_without_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object):
        raise AssertionError("private URLs must not build an opener")

    monkeypatch.setattr("urllib.request.build_opener", fail_if_called)

    assert _url_reachable("http://127.0.0.1:8080/status", 0.1) is False


def test_url_reachable_sends_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str | None] = {}

    class _Response:
        status = 204

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Opener:
        def open(self, request: Request, timeout: float) -> _Response:
            assert timeout == 0.1
            seen["user_agent"] = request.get_header("User-agent")
            return _Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _Opener())

    assert _url_reachable("https://8.8.8.8/health", 0.1) is True
    assert seen["user_agent"] == "Agent-OS-SEW-Evaluator/0.1"


def test_url_reachable_uses_no_redirect_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: list[object] = []

    class _Opener:
        def open(self, request: Request, timeout: float) -> None:
            raise urllib.error.URLError("network unavailable")

    def fake_build_opener(*items: object) -> _Opener:
        handlers.extend(items)
        return _Opener()

    monkeypatch.setattr("urllib.request.build_opener", fake_build_opener)

    assert _url_reachable("http://8.8.8.8/redirect", 0.1) is False
    redirect_handler = next(
        handler for handler in handlers if type(handler).__name__ == "_NoRedirectHandler"
    )
    assert (
        redirect_handler.redirect_request(
            Request("http://8.8.8.8/redirect"),
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1/admin",
        )
        is None
    )


def test_url_reachable_pins_resolved_address(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {"getaddrinfo_calls": 0}

    class _Response:
        status = 200

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Opener:
        def open(self, request: Request, timeout: float) -> _Response:
            seen["url"] = request.full_url
            return _Response()

    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
        seen["getaddrinfo_calls"] += 1
        seen["getaddrinfo_port"] = args[1]
        return [(None, None, None, None, ("93.184.216.34", 443))]

    def fake_build_opener(*handlers: object) -> _Opener:
        pinned_handler = next(
            handler for handler in handlers if type(handler).__name__ == "_PinnedHTTPSHandler"
        )
        seen["pinned_address"] = str(pinned_handler._pinned_address)
        return _Opener()

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("urllib.request.build_opener", fake_build_opener)

    assert _url_reachable("https://example.com/health", 0.1) is True
    assert seen["url"] == "https://example.com/health"
    assert seen["pinned_address"] == "93.184.216.34"
    assert seen["getaddrinfo_calls"] == 1
    assert seen["getaddrinfo_port"] == 443


def test_url_reachable_uses_default_http_port_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _Response:
        status = 200

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _Opener:
        def open(self, request: Request, timeout: float) -> _Response:
            return _Response()

    def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple[Any, ...]]:
        seen["getaddrinfo_port"] = args[1]
        return [(None, None, None, None, ("93.184.216.34", 80))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _Opener())

    assert _url_reachable("http://example.com/health", 0.1) is True
    assert seen["getaddrinfo_port"] == 80


def test_pinned_https_handler_preserves_original_host_for_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    class _Connection:
        def __init__(
            self,
            host: str,
            *,
            pinned_address: ipaddress.IPv4Address | ipaddress.IPv6Address,
            **kwargs: Any,
        ) -> None:
            seen["host"] = host
            seen["pinned_address"] = str(pinned_address)

    from sew import evaluator

    monkeypatch.setattr(evaluator, "_PinnedHTTPSConnection", _Connection)
    handler = evaluator._PinnedHTTPSHandler(ipaddress.ip_address("93.184.216.34"))

    handler._connection_factory("example.com", timeout=0.1)

    assert seen == {"host": "example.com", "pinned_address": "93.184.216.34"}


def test_malformed_answer_payload_fails_without_crashing() -> None:
    record = _evaluate(answer="not an object")

    assert record["outcome"] == "fail"
    assert "completed:missing_final_answer" in record["failure_reasons"]
    assert "schema_valid:missing_required_field:answer" in record["failure_reasons"]


def test_non_iterable_rejected_stale_sources_fails_without_crashing() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["stale_source_ids"] = ["src-success-release-note"]
    answer = copy.deepcopy(ANSWER)
    answer["rejected_stale_source_ids"] = 7

    record = _evaluate(task=task, answer=answer)

    assert record["dimensions"]["fresh"] is False
    assert "fresh:stale_source_not_rejected:src-success-release-note" in record["failure_reasons"]


def test_malformed_url_fails_without_crashing() -> None:
    answer = copy.deepcopy(ANSWER)
    answer["citation_urls"] = ["http://]/"]

    record = _evaluate(answer=answer, sources=[])

    assert record["outcome"] == "fail"
    assert any(reason.startswith("safe:domain_not_allowed") for reason in record["failure_reasons"])
    assert any(
        reason.startswith("grounded:cited_source_missing") for reason in record["failure_reasons"]
    )


def test_url_reachable_rejects_malformed_url_without_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object):
        raise AssertionError("malformed URLs must not build an opener")

    monkeypatch.setattr("urllib.request.build_opener", fail_if_called)

    assert _url_reachable("http://]/", 0.1) is False


def test_forbidden_claim_searches_answer_string_values() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["forbidden_claims"] = ["secret\nclaim"]
    answer = copy.deepcopy(ANSWER)
    answer["details"] = {"body": "The response contains secret\nclaim text."}

    record = _evaluate(task=task, answer=answer)

    assert record["dimensions"]["safe"] is False
    assert "safe:forbidden_claim_present:secret-claim" in record["failure_reasons"]


def test_forbidden_claim_searches_numeric_answer_values() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"]["forbidden_claims"] = [404]
    answer = copy.deepcopy(ANSWER)
    answer["details"] = {"blocked_id": 404}

    record = _evaluate(task=task, answer=answer)

    assert record["dimensions"]["safe"] is False
    assert "safe:forbidden_claim_present:404" in record["failure_reasons"]


def test_load_evaluation_input_treats_missing_final_answer_as_empty(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    task_dir = tmp_path / "task"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "sources").mkdir()
    task_dir.mkdir()
    (run_dir / "run.json").write_text('{"run_id": "run-1"}', encoding="utf-8")
    (task_dir / "task.yaml").write_text("task_id: task-1\n", encoding="utf-8")

    data = load_evaluation_input(run_dir, task_dir)

    assert data.answer == {}


def test_load_evaluation_input_treats_malformed_final_answer_as_empty(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    task_dir = tmp_path / "task"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "sources").mkdir()
    task_dir.mkdir()
    (run_dir / "run.json").write_text('{"run_id": "run-1"}', encoding="utf-8")
    (run_dir / "artifacts" / "final-answer.json").write_text("{", encoding="utf-8")
    (task_dir / "task.yaml").write_text("task_id: task-1\n", encoding="utf-8")

    data = load_evaluation_input(run_dir, task_dir)

    assert data.answer == {}


def test_judge_unavailable_fails_correct_without_live_call() -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task.pop("deterministic_validator")
    task["task_class"] = "multi_source_synthesis"
    task["expected_output_schema"] = {
        "type": "object",
        "required": ["summary", "agreements", "citation_urls"],
    }
    task["blinded_judge"] = {
        "rubric_ref": "rubric.md",
        "judge_prompt_blinded": True,
        "minimum_score": 4,
        "score_scale": {"min": 1, "max": 5},
    }
    answer = {"summary": "Both sources agree.", "agreements": ["Release 3.2"], "citation_urls": []}

    record = _evaluate(task=task, answer=answer, sources=[])

    assert record["dimensions"]["correct"] is False
    assert "correct:judge_unavailable" in record["failure_reasons"]
    assert record["judge"] == {"kind": "blinded", "rubric_ref": "rubric.md"}


def test_blinded_judge_payload_excludes_provider_and_harness_labels() -> None:
    payload = build_blinded_judge_input(TASK, RUN, ANSWER, [SOURCE])

    assert "provider_id" not in payload
    assert "harness_id" not in payload
    assert payload["answer"]["answer"] == "Release 3.2"


def test_patent_claims_validator_checks_claims_and_domain():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "patent_claims",
        "require_all_independent_claims": True,
        "expected_independent_claims": [1, 15],
        "required_primary_domain": "patents.google.com",
    }
    # Test success
    ans = copy.deepcopy(ANSWER)
    ans["independent_claim_numbers"] = [1, 15]
    ans["citation_urls"] = ["https://patents.google.com/patent/US10000000"]
    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )
    assert rec["dimensions"]["correct"] is True
    assert rec["dimensions"]["grounded"] is True

    # Test failure missing claims
    ans2 = copy.deepcopy(ans)
    ans2["independent_claim_numbers"] = [1]
    rec2 = _evaluate(
        task=task,
        answer=ans2,
        sources=[
            {
                "source_id": "src",
                "url": ans2.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec2["dimensions"]["correct"] is False
    assert "correct:missing_independent_claims" in rec2["failure_reasons"]

    # Test failure wrong domain
    ans3 = copy.deepcopy(ans)
    ans3["citation_urls"] = ["https://example.com/patent/US10000000"]
    rec3 = _evaluate(
        task=task,
        answer=ans3,
        sources=[
            {
                "source_id": "src",
                "url": ans3.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec3["dimensions"]["grounded"] is False
    assert "grounded:wrong_primary_domain" in rec3["failure_reasons"]


def test_patent_claims_validator_handles_null_claims_without_crashing():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "patent_claims",
        "require_all_independent_claims": True,
        "expected_independent_claims": [1, 15],
    }
    ans = copy.deepcopy(ANSWER)
    ans["independent_claim_numbers"] = None

    rec = _evaluate(task=task, answer=ans)

    assert rec["dimensions"]["correct"] is False
    assert "correct:missing_independent_claims" in rec["failure_reasons"]


def test_patent_claims_validator_enforces_empty_expected_claims():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "patent_claims",
        "require_all_independent_claims": True,
        "expected_independent_claims": [],
    }
    ans = copy.deepcopy(ANSWER)
    ans["independent_claim_numbers"] = [1]

    rec = _evaluate(task=task, answer=ans)

    assert rec["dimensions"]["correct"] is False
    assert "correct:missing_independent_claims" in rec["failure_reasons"]


@pytest.mark.parametrize(
    ("kind", "flag", "expected_key"),
    [
        (
            "patent_claims",
            "require_all_independent_claims",
            "expected_independent_claims",
        ),
        ("sanctions_record", "require_identifier_match", "expected_match_status"),
        ("sec_filing", "require_accession_number", "expected_accession_number"),
        ("current_statute", "require_effective_date", "expected_effective_date"),
        ("supported_decline", "fail_confident_unsupported_answer", "expected_status"),
    ],
)
def test_flagged_deterministic_validators_require_expected_values(
    kind: str, flag: str, expected_key: str
) -> None:
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {"kind": kind, flag: True}

    with pytest.raises(ValueError, match=rf"{kind}\.{flag} requires {expected_key}"):
        _evaluate(task=task)


def test_sanctions_record_validator_checks_status_and_domain():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "sanctions_record",
        "require_identifier_match": True,
        "expected_match_status": "matched",
        "required_primary_domain": "ofac.treasury.gov",
    }
    ans = copy.deepcopy(ANSWER)
    ans["match_status"] = "matched"
    ans["citation_urls"] = ["https://ofac.treasury.gov/recent-actions/20220808"]
    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )
    assert rec["dimensions"]["correct"] is True
    assert rec["dimensions"]["grounded"] is True

    ans2 = copy.deepcopy(ans)
    ans2["match_status"] = "not_matched"
    rec2 = _evaluate(
        task=task,
        answer=ans2,
        sources=[
            {
                "source_id": "src",
                "url": ans2.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec2["dimensions"]["correct"] is False
    assert "correct:match_status_mismatch" in rec2["failure_reasons"]


def test_primary_domain_validators_allow_secondary_citations():
    cases = [
        (
            "patent_claims",
            "patents.google.com",
            "https://patents.google.com/patent/US10000000",
        ),
        (
            "sanctions_record",
            "ofac.treasury.gov",
            "https://ofac.treasury.gov/recent-actions/20220808",
        ),
        (
            "sec_filing",
            "sec.gov",
            "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019324000069/aapl-20240330.htm",
        ),
    ]
    for kind, domain, primary_url in cases:
        task = copy.deepcopy(TASK)
        task.pop("freshness_window_days", None)
        task["deterministic_validator"] = {
            "kind": kind,
            "required_primary_domain": domain,
        }
        ans = copy.deepcopy(ANSWER)
        ans["citation_urls"] = [primary_url, "https://example.com/context"]

        rec = _evaluate(
            task=task,
            answer=ans,
            sources=[
                {"source_id": "primary", "url": primary_url},
                {"source_id": "secondary", "url": "https://example.com/context"},
            ],
        )

        assert rec["dimensions"]["grounded"] is True
        assert "grounded:wrong_primary_domain" not in rec["failure_reasons"]


def test_sec_filing_validator_checks_accession_and_domain():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "sec_filing",
        "require_accession_number": True,
        "expected_accession_number": "0000320193-24-000069",
        "required_primary_domain": "sec.gov",
    }
    ans = copy.deepcopy(ANSWER)
    ans["accession_number"] = "0000320193-24-000069"
    ans["citation_urls"] = [
        "https://www.sec.gov/ix?doc=/Archives/edgar/data/320193/000032019324000069/aapl-20240330.htm"
    ]
    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )
    assert rec["dimensions"]["correct"] is True
    assert rec["dimensions"]["grounded"] is True

    ans2 = copy.deepcopy(ans)
    ans2["accession_number"] = "wrong"
    rec2 = _evaluate(
        task=task,
        answer=ans2,
        sources=[
            {
                "source_id": "src",
                "url": ans2.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec2["dimensions"]["correct"] is False


def test_sec_filing_validator_handles_null_accession_without_crashing():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "sec_filing",
        "require_accession_number": True,
        "expected_accession_number": "0000320193-24-000069",
    }
    ans = copy.deepcopy(ANSWER)
    ans["accession_number"] = None

    rec = _evaluate(task=task, answer=ans)

    assert rec["dimensions"]["correct"] is False
    assert "correct:accession_number_mismatch" in rec["failure_reasons"]


def test_current_statute_validator_checks_freshness_and_official_source():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "current_statute",
        "require_official_code_source": True,
        "require_effective_date": True,
        "expected_effective_date": "1992-10-24",
    }
    ans = copy.deepcopy(ANSWER)
    ans["effective_date"] = "1992-10-24"
    ans["citation_urls"] = ["https://www.law.cornell.edu/uscode/text/17/107"]
    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )
    assert rec["dimensions"]["fresh"] is True
    assert rec["dimensions"]["grounded"] is True

    ans2 = copy.deepcopy(ans)
    ans2["effective_date"] = "old-date"
    rec2 = _evaluate(
        task=task,
        answer=ans2,
        sources=[
            {
                "source_id": "src",
                "url": ans2.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec2["dimensions"]["fresh"] is False
    assert "fresh:effective_date_mismatch" in rec2["failure_reasons"]

    ans3 = copy.deepcopy(ans)
    ans3["citation_urls"] = ["https://wikipedia.org/wiki/Fair_use"]
    rec3 = _evaluate(
        task=task,
        answer=ans3,
        sources=[
            {
                "source_id": "src",
                "url": ans3.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec3["dimensions"]["grounded"] is False


def test_current_statute_validator_normalizes_official_source_hostname():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "current_statute",
        "require_official_code_source": True,
    }
    ans = copy.deepcopy(ANSWER)
    ans["citation_urls"] = ["https://WWW.CONGRESS.GOV:443/bill/117th-congress/house-bill/1"]

    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )

    assert rec["dimensions"]["grounded"] is True
    assert "grounded:wrong_primary_domain" not in rec["failure_reasons"]


def test_current_statute_validator_handles_missing_effective_date_without_crashing():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "current_statute",
        "require_effective_date": True,
        "expected_effective_date": "1992-10-24",
    }
    ans = copy.deepcopy(ANSWER)
    ans.pop("effective_date", None)

    rec = _evaluate(task=task, answer=ans)

    assert rec["dimensions"]["fresh"] is False
    assert "fresh:effective_date_mismatch" in rec["failure_reasons"]


def test_supported_decline_validator_scores_declines_correctly():
    task = copy.deepcopy(TASK)
    task.pop("freshness_window_days", None)
    task["deterministic_validator"] = {
        "kind": "supported_decline",
        "fail_confident_unsupported_answer": True,
        "expected_status": "not_supported",
    }
    ans = copy.deepcopy(ANSWER)
    ans["conclusion_status"] = "not_supported"
    rec = _evaluate(
        task=task, answer=ans, sources=[{"source_id": "src", "url": ans["citation_urls"][0]}]
    )
    assert rec["dimensions"]["correct"] is True

    ans2 = copy.deepcopy(ans)
    ans2["conclusion_status"] = "supported"
    rec2 = _evaluate(
        task=task,
        answer=ans2,
        sources=[
            {
                "source_id": "src",
                "url": ans2.get("citation_urls", ans.get("citation_urls", [""]))[0],
            }
        ],
    )
    assert rec2["dimensions"]["correct"] is False
    assert "correct:confident_unsupported_answer" in rec2["failure_reasons"]
