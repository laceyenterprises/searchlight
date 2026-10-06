from __future__ import annotations

import copy
from pathlib import Path

import pytest

from sew.schema import (
    SchemaError,
    validate_artifact_path,
    validate_evidence_bundle,
    validate_evaluation_record,
    validate_harness_record,
    validate_metrics_record,
    validate_normalized_source,
    validate_provider_call,
    validate_run_record,
    validate_task_manifest,
)
from sew.state import default_state_root

VALID_RUN = {
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

VALID_SOURCE = {
    "schema_version": 1,
    "source_id": "source-1",
    "provider_id": "fixture",
    "url": "https://fixture.example/source",
    "title": "Fixture Source",
    "snippet": "A bounded sanitized excerpt.",
    "retrieved_at": "2026-09-16T18:12:01Z",
    "content_hash": "sha256:abc",
    "content_length": 28,
    "redaction": {"state": "sanitized_excerpt", "raw_content_included": False},
}

VALID_TASK = {
    "schema_version": 1,
    "task_id": "current-fact-lookup-v1",
    "version": "2026-09-16",
    "title": "Current Fact Lookup",
    "task_class": "fact_lookup",
    "created_at": "2026-09-16T18:00:00Z",
    "live_web_required": False,
    "fixture_mode_allowed": True,
    "allowed_domains": [],
    "disallowed_domains": [],
    "expected_output_schema": {"type": "object", "required": ["answer"]},
    "deterministic_validator": {"kind": "exact_value", "expected_answer": "Agent OS"},
    "budgets": {
        "max_provider_calls": 1,
        "max_pages": 1,
        "max_bytes": 1000,
        "max_total_tokens": 1000,
        "wall_clock_seconds": 30,
    },
    "success_criteria": ["Returns the expected answer."],
}

VALID_PROVIDER_CALL = {
    "schema_version": 1,
    "call_id": "call-1",
    "run_id": "run-1",
    "provider_id": "fixture",
    "operation": "search",
    "status": "ok",
    "started_at": "2026-09-16T18:12:01Z",
    "ended_at": "2026-09-16T18:12:02Z",
    "request": {},
    "response": {},
    "normalized_source_refs": ["sources/source-1.json"],
    "retry_count": 0,
}

VALID_BUNDLE = {
    "schema_version": 1,
    "bundle_id": "bundle-1",
    "run_id": "run-1",
    "redaction": {
        "raw_transcripts_included": False,
        "credentials_included": False,
        "cookies_included": False,
        "unrestricted_page_archives_included": False,
    },
    "artifacts": [
        {
            "path": "artifacts/final-answer.json",
            "media_type": "application/json",
            "size_bytes": 100,
        }
    ],
    "records": {"run": "run.json"},
}

VALID_METRICS = {
    "schema_version": 1,
    "run_id": "run-1",
    "latency_ms": {
        "end_to_end": 8120,
        "provider": 240,
        "harness": 7420,
        "evaluator": 460,
    },
    "token_usage": {
        "accounting_source": "unknown",
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
    },
    "provider_calls": {"total": 1, "ok": 1, "failed": 0},
    "provider_result_chars": 418,
    "source_counts": {"normalized": 1, "cited": 1},
    "cost": {"currency": "USD", "amount": None, "source": "unknown"},
}

VALID_EVALUATION = {
    "schema_version": 1,
    "run_id": "run-1",
    "task_id": "current-fact-lookup-v1",
    "outcome": "pass",
    "dimensions": {
        "completed": True,
        "correct": True,
        "grounded": True,
        "fresh": True,
        "schema_valid": True,
        "safe": True,
    },
    "failure_reasons": [],
    "judge": {"kind": "deterministic", "validator": "exact_value_with_citation"},
    "evidence_refs": ["sources/src-success-release-note.json"],
}

VALID_HARNESS = {
    "schema_version": 1,
    "run_id": "run-1",
    "harness_id": "pi",
    "profile_id": "oss-small",
    "resolved_model": "local/qwen2.5-coder-7b-instruct",
    "tool_calling_mode": "prompt_wrapped",
    "context_window_tokens": 32768,
    "provider_result_compression": {
        "mode": "truncate",
        "max_chars": 24000,
        "preserves_source_refs": True,
    },
    "token_accounting_source": "estimated",
    "provider_adapter_exposure": {
        "exposed": True,
        "provider_ids": ["exa", "parallel-web", "firecrawl"],
        "fixture_replay": True,
        "native_search_available": False,
    },
    "fixture_mode": True,
}


def test_missing_run_id_rejected() -> None:
    data = copy.deepcopy(VALID_RUN)
    del data["run_id"]

    with pytest.raises(SchemaError, match="run_id"):
        validate_run_record(data)


def test_missing_metrics_ref_rejected() -> None:
    data = copy.deepcopy(VALID_RUN)
    del data["metrics_ref"]

    with pytest.raises(SchemaError, match="metrics_ref"):
        validate_run_record(data)


def test_missing_evaluation_ref_rejected() -> None:
    data = copy.deepcopy(VALID_RUN)
    del data["evaluation_ref"]

    with pytest.raises(SchemaError, match="evaluation_ref"):
        validate_run_record(data)


def test_missing_run_suite_task_and_start_rejected() -> None:
    for key in ("suite_id", "task_id", "started_at"):
        data = copy.deepcopy(VALID_RUN)
        del data[key]

        with pytest.raises(SchemaError, match=key):
            validate_run_record(data)


def test_task_manifest_requires_title_and_created_at_strings() -> None:
    for key in ("title", "created_at"):
        data = copy.deepcopy(VALID_TASK)
        data[key] = {"not": "a string"}

        with pytest.raises(SchemaError, match=key):
            validate_task_manifest(data)


def test_unsafe_artifact_path_rejected() -> None:
    data = copy.deepcopy(VALID_BUNDLE)
    data["artifacts"][0]["path"] = "../raw/transcript.json"

    with pytest.raises(SchemaError, match="unsafe"):
        validate_evidence_bundle(data, expected_run_id="run-1")


def test_artifact_path_returns_normalized_posix_path() -> None:
    assert validate_artifact_path("artifacts/./final-answer.json", "artifact path") == (
        "artifacts/final-answer.json"
    )


def test_unknown_provider_rejected() -> None:
    data = copy.deepcopy(VALID_RUN)
    data["provider_id"] = "mystery-search"

    with pytest.raises(SchemaError, match="provider_id"):
        validate_run_record(data)


def test_unknown_harness_rejected() -> None:
    data = copy.deepcopy(VALID_RUN)
    data["harness_id"] = "mystery-harness"

    with pytest.raises(SchemaError, match="harness_id"):
        validate_run_record(data)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html;base64,PHN2Zz4=",
        "ftp://example.com/x",
        "//example.com/x",
    ],
)
def test_malformed_normalized_source_rejected(url: str) -> None:
    data = copy.deepcopy(VALID_SOURCE)
    data["url"] = url

    with pytest.raises(SchemaError, match="url"):
        validate_normalized_source(data)


def test_http_source_url_is_recorded_not_rejected() -> None:
    # Live providers return http:// results. A normalized source is a record of
    # what came back, so refusing it would make the benchmark unable to describe
    # reality and would penalize whichever provider returned it. Fetch-time
    # scheme safety is the evaluator reachability checker's job, not this one's.
    data = copy.deepcopy(VALID_SOURCE)
    data["url"] = "http://fixture.example/source"

    assert validate_normalized_source(data)["url"] == "http://fixture.example/source"


def test_normalized_source_optional_metadata_must_be_strings() -> None:
    for key in ("published_at", "modified_at", "retrieved_at", "content_hash"):
        data = copy.deepcopy(VALID_SOURCE)
        data[key] = 123

        with pytest.raises(SchemaError, match=key):
            validate_normalized_source(data)


def test_metrics_run_id_must_match() -> None:
    metrics = copy.deepcopy(VALID_METRICS)
    metrics["run_id"] = "other-run"

    with pytest.raises(SchemaError, match="does not match"):
        validate_metrics_record(metrics, expected_run_id="run-1")


def test_metrics_nested_types_are_strict() -> None:
    metrics = copy.deepcopy(VALID_METRICS)
    metrics["latency_ms"]["end_to_end"] = "fast"

    with pytest.raises(SchemaError, match="latency_ms.end_to_end"):
        validate_metrics_record(metrics, expected_run_id="run-1")


def test_metrics_compression_ratio_allows_growth() -> None:
    metrics = copy.deepcopy(VALID_METRICS)
    metrics["provider_result_context"] = {
        "result_chars": 150,
        "injected_chars": 150,
        "compression_ratio": 1.5,
        "compression_mode": "passthrough",
    }

    validate_metrics_record(metrics, expected_run_id="run-1")


def test_metrics_nested_unknown_keys_are_rejected() -> None:
    metrics = copy.deepcopy(VALID_METRICS)
    metrics["cost"]["surprise_fee"] = 1

    with pytest.raises(SchemaError, match="metrics record.cost"):
        validate_metrics_record(metrics, expected_run_id="run-1")


def test_evaluation_dimensions_reject_unknown_and_require_fresh() -> None:
    evaluation = copy.deepcopy(VALID_EVALUATION)
    evaluation["dimensions"]["freshh"] = True

    with pytest.raises(SchemaError, match="evaluation record.dimensions"):
        validate_evaluation_record(evaluation, expected_run_id="run-1")

    evaluation = copy.deepcopy(VALID_EVALUATION)
    del evaluation["dimensions"]["fresh"]

    with pytest.raises(SchemaError, match="dimensions.fresh"):
        validate_evaluation_record(evaluation, expected_run_id="run-1")


def test_evaluation_judge_shape_is_strict() -> None:
    evaluation = copy.deepcopy(VALID_EVALUATION)
    evaluation["judge"]["extra"] = "surprise"

    with pytest.raises(SchemaError, match="evaluation record.judge"):
        validate_evaluation_record(evaluation, expected_run_id="run-1")

    evaluation = copy.deepcopy(VALID_EVALUATION)
    evaluation["judge"]["kind"] = {"not": "a string"}

    with pytest.raises(SchemaError, match="judge.kind"):
        validate_evaluation_record(evaluation, expected_run_id="run-1")


def test_run_failure_category_must_be_string_when_present() -> None:
    data = copy.deepcopy(VALID_RUN)
    data["failure_category"] = {"not": "a string"}

    with pytest.raises(SchemaError, match="failure_category"):
        validate_run_record(data)


def test_run_accepts_optional_harness_ref() -> None:
    data = copy.deepcopy(VALID_RUN)
    data["harness_id"] = "pi"
    data["harness_ref"] = "harness/pi.json"

    assert validate_run_record(data)["harness_ref"] == "harness/pi.json"


def test_provider_call_error_class_must_be_string_when_present() -> None:
    data = copy.deepcopy(VALID_PROVIDER_CALL)
    data["error_class"] = {"not": "a string"}

    with pytest.raises(SchemaError, match="error_class"):
        validate_provider_call(data, expected_run_id="run-1")


def test_harness_record_classifies_pi_profile_telemetry() -> None:
    assert validate_harness_record(VALID_HARNESS, expected_run_id="run-1")["profile_id"] == (
        "oss-small"
    )


def test_harness_record_rejects_ambiguous_tool_or_token_modes() -> None:
    data = copy.deepcopy(VALID_HARNESS)
    data["tool_calling_mode"] = "maybe"

    with pytest.raises(SchemaError, match="tool_calling_mode"):
        validate_harness_record(data, expected_run_id="run-1")

    data = copy.deepcopy(VALID_HARNESS)
    data["token_accounting_source"] = "free-ish"

    with pytest.raises(SchemaError, match="token_accounting_source"):
        validate_harness_record(data, expected_run_id="run-1")


def test_evidence_redaction_requires_no_cookies_or_page_archives() -> None:
    bundle = copy.deepcopy(VALID_BUNDLE)
    bundle["redaction"]["cookies_included"] = True

    with pytest.raises(SchemaError, match="cookies_included"):
        validate_evidence_bundle(bundle, expected_run_id="run-1")

    bundle = copy.deepcopy(VALID_BUNDLE)
    del bundle["redaction"]["unrestricted_page_archives_included"]

    with pytest.raises(SchemaError, match="unrestricted_page_archives_included"):
        validate_evidence_bundle(bundle, expected_run_id="run-1")


def test_evidence_records_reject_unknown_keys() -> None:
    bundle = copy.deepcopy(VALID_BUNDLE)
    bundle["records"]["raw_cookie_jar"] = "artifacts/cookies.json"

    with pytest.raises(SchemaError, match="evidence bundle.records"):
        validate_evidence_bundle(bundle, expected_run_id="run-1")


def test_evidence_records_accept_optional_harness_record() -> None:
    bundle = copy.deepcopy(VALID_BUNDLE)
    bundle["records"]["metrics"] = "metrics/metrics.json"
    bundle["records"]["evaluation"] = "evaluations/evaluation.json"
    bundle["records"]["provider_calls"] = []
    bundle["records"]["normalized_sources"] = []
    bundle["records"]["harness"] = "harness/pi.json"

    assert validate_evidence_bundle(bundle, expected_run_id="run-1")["records"]["harness"] == (
        "harness/pi.json"
    )


def test_standalone_state_root_defaults_under_xdg_outside_worktree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEW_STATE_ROOT", raising=False)
    monkeypatch.setenv("SEW_MODE", "standalone")
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/example-state")

    assert default_state_root() == Path("/tmp/example-state/sew")


def test_state_root_explicit_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEW_STATE_ROOT", str(tmp_path / "sew-state"))

    assert default_state_root() == tmp_path / "sew-state"


@pytest.mark.parametrize("value", ["available", "unavailable", "unknown"])
def test_run_provider_availability_accepts_saved_verdict(value):
    data = {**VALID_RUN, "provider_availability": value}
    assert validate_run_record(data)["provider_availability"] == value


@pytest.mark.parametrize("value", [None, False, "pending", {"available": True}])
def test_run_provider_availability_rejects_invalid_verdict(value):
    with pytest.raises(SchemaError, match="provider_availability"):
        validate_run_record({**VALID_RUN, "provider_availability": value})
