from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import LIB_PYTHON, MODULE_ROOT

from sew.harness import (
    TERMINAL_CANCELLED,
    TERMINAL_HARNESS_BOOT_FAILURE,
    TERMINAL_TIMEOUT,
    TERMINAL_TOKEN_USAGE_UNKNOWN,
    TERMINAL_UNSUPPORTED_NATIVE_SEARCH,
    HarnessRunConfig,
    ProviderExposure,
    classify_fixture_outcome,
    fixture_provider_exposure,
    parse_citations,
    run_acceptance_fixture_matrix,
    run_fixture_harness,
)
from sew.schema import SchemaError, validate_fixture_run


def test_fixture_matrix_writes_distinct_codex_and_claude_bundles(tmp_path: Path) -> None:
    results = run_acceptance_fixture_matrix(tmp_path)

    assert [result.run_id for result in results] == [
        "fixture-codex-native-current-fact-lookup-v1-20260916T182000Z",
        "fixture-codex-exa-current-fact-lookup-v1-20260916T182000Z",
        "fixture-claude-code-native-current-fact-lookup-v1-20260916T182000Z",
        "fixture-claude-code-exa-current-fact-lookup-v1-20260916T182000Z",
    ]
    assert len({result.bundle_dir for result in results}) == 4

    for result in results:
        validation = validate_fixture_run(result.bundle_dir)
        assert validation.run_id == result.run_id
        bundle_path = result.bundle_dir / result.evidence_bundle_ref
        bundle = yaml.safe_load(bundle_path.read_text(encoding="utf-8"))
        artifact_paths = {artifact["path"] for artifact in bundle["artifacts"]}
        assert {
            "artifacts/final-answer.json",
            "artifacts/prompt.md",
            "artifacts/transcript.json",
            "artifacts/spawn-metadata.json",
        } <= artifact_paths


def test_external_provider_mode_exposes_exactly_one_adapter(tmp_path: Path) -> None:
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="parallel-web",
            task_id="current-fact-lookup-v1",
            external_provider=fixture_provider_exposure("parallel-web"),
        ),
        tmp_path,
    )

    spawn = json.loads(
        (result.bundle_dir / "artifacts" / "spawn-metadata.json").read_text(encoding="utf-8")
    )
    assert spawn["provider_exposure"] == {
        "mode": "external",
        "provider_id": "parallel-web",
        "tool_name": "sew_parallel_web_search",
    }
    transcript = json.loads(
        (result.bundle_dir / "artifacts" / "transcript.json").read_text(encoding="utf-8")
    )
    assert all("role" in event for event in transcript)
    assert transcript[0]["role"] == "system"
    assert transcript[1]["role"] == "system"


def test_native_mode_exposes_no_external_provider(tmp_path: Path) -> None:
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="claude-code",
            provider_id="native",
            task_id="current-fact-lookup-v1",
        ),
        tmp_path,
    )

    run = json.loads((result.bundle_dir / "run.json").read_text(encoding="utf-8"))
    spawn = json.loads(
        (result.bundle_dir / "artifacts" / "spawn-metadata.json").read_text(encoding="utf-8")
    )
    assert run["provider_call_refs"] == []
    assert spawn["provider_exposure"]["mode"] == "native"
    assert "tool_name" not in spawn["provider_exposure"]


@pytest.mark.parametrize(
    ("kwargs", "expected_status", "expected_category"),
    [
        (
            {
                "provider_id": "native",
                "native_search_available": False,
                "outcome": "success",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "unsupported",
            TERMINAL_UNSUPPORTED_NATIVE_SEARCH,
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "success",
                "usage": None,
            },
            "succeeded",
            TERMINAL_TOKEN_USAGE_UNKNOWN,
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "boot_failure",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "harness_boot_failed",
            TERMINAL_HARNESS_BOOT_FAILURE,
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "provider_unavailable",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "provider_unavailable",
            "provider_unavailable",
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "budget_exhausted",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "budget_exhausted",
            "budget_exhausted",
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "timeout",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "timeout",
            TERMINAL_TIMEOUT,
        ),
        (
            {
                "provider_id": "exa",
                "native_search_available": True,
                "outcome": "cancelled",
                "usage": {"input": 1, "cached_input": 0, "output": 1, "reasoning": 0},
            },
            "cancelled",
            TERMINAL_CANCELLED,
        ),
    ],
)
def test_terminal_status_classification(
    kwargs: dict[str, object], expected_status: str, expected_category: str
) -> None:
    status = classify_fixture_outcome(**kwargs)

    assert status.status == expected_status
    assert status.category == expected_category


def test_missing_usage_writes_unknown_markers(tmp_path: Path) -> None:
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            external_provider=fixture_provider_exposure("exa"),
            usage=None,
        ),
        tmp_path,
    )

    run = json.loads((result.bundle_dir / "run.json").read_text(encoding="utf-8"))
    metrics = json.loads((result.bundle_dir / "metrics" / "metrics.json").read_text("utf-8"))
    assert run["status"] == "succeeded"
    assert run["failure_category"] == TERMINAL_TOKEN_USAGE_UNKNOWN
    assert metrics["token_usage"] == {
        "accounting_source": "unknown",
        "source_kind": "unknown",
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
        "total_billable": None,
    }


def test_secret_material_is_redacted_from_evidence(tmp_path: Path) -> None:
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            external_provider=fixture_provider_exposure("exa"),
            env={
                "EXA_API_KEY": "sk-live-secret",
                "AUTHORIZATION": "Bearer very-secret-token",
                "COOKIE": "sessionid=secret",
                "SAFE_VALUE": "visible",
            },
        ),
        tmp_path,
    )

    evidence_text = "\n".join(
        path.read_text(encoding="utf-8") for path in result.bundle_dir.rglob("*") if path.is_file()
    )
    assert "sk-live-secret" not in evidence_text
    assert "very-secret-token" not in evidence_text
    assert "sessionid=secret" not in evidence_text
    assert '"SAFE_VALUE": "visible"' in evidence_text


def test_bearer_token_in_string_redacts_without_failing_validation(tmp_path: Path) -> None:
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            external_provider=fixture_provider_exposure("exa"),
            env={"SAFE_VALUE": "prefix Bearer very-secret-token suffix"},
        ),
        tmp_path,
    )

    evidence_text = "\n".join(
        path.read_text(encoding="utf-8") for path in result.bundle_dir.rglob("*") if path.is_file()
    )
    assert "very-secret-token" not in evidence_text
    assert "Bearer <redacted>" not in evidence_text
    assert "prefix <redacted> suffix" in evidence_text


def test_provider_call_records_all_retrieved_sources(tmp_path: Path) -> None:
    exposure = ProviderExposure(
        provider_id="exa",
        tool_name="sew_exa_search",
        fixture_sources=(
            {
                "source_id": "src-fixture-release-note",
                "url": "https://fixture.example/releases/current",
                "title": "Fixture Product Release Notes",
                "snippet": "Fixture Product current release: Release 3.2.",
                "published_at": "2026-09-12T10:00:00Z",
                "modified_at": "2026-09-12T10:00:00Z",
                "content_length": 45,
            },
            {
                "source_id": "src-fixture-uncited",
                "url": "https://fixture.example/releases/older",
                "title": "Fixture Product Older Release",
                "snippet": "Fixture Product older release: Release 3.1.",
                "published_at": "2026-08-12T10:00:00Z",
                "modified_at": "2026-08-12T10:00:00Z",
                "content_length": 43,
            },
        ),
        fixture_provider_calls=(
            {
                "operation": "search",
                "request": {"query": "fixture product release"},
                "response": {"result_count": 2},
            },
        ),
    )
    result = run_fixture_harness(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            external_provider=exposure,
        ),
        tmp_path,
    )

    call = json.loads(
        (
            result.bundle_dir
            / "provider-calls"
            / "fixture-codex-exa-current-fact-lookup-v1-20260916T182000Z-provider-1.json"
        ).read_text(encoding="utf-8")
    )
    assert call["normalized_source_refs"] == [
        "sources/src-fixture-release-note-codex.json",
        "sources/src-fixture-uncited-codex.json",
    ]
    assert (result.bundle_dir / "sources" / "src-fixture-uncited-codex.json").exists()


def test_parse_citations_stops_before_escaped_json_newline() -> None:
    assert parse_citations("See https://fixture.example/releases/current\\nNext") == [
        "https://fixture.example/releases/current"
    ]


@pytest.mark.parametrize("provider_id", ["no-search", "floor", "ceiling"])
@pytest.mark.parametrize("search_surface", ["native", "external"])
def test_search_disabled_arms_report_the_shared_constraint(provider_id, search_surface) -> None:
    with pytest.raises(
        SchemaError,
        match="no-search baseline and GAP reference arms must disable native search "
        "and expose no external provider",
    ):
        HarnessRunConfig(
            harness_id="codex",
            provider_id=provider_id,
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=search_surface == "native",
            external_provider=(
                fixture_provider_exposure() if search_surface == "external" else None
            ),
        )


def test_invalid_external_provider_shape_is_rejected() -> None:
    with pytest.raises(SchemaError, match="requires exactly one provider"):
        HarnessRunConfig(
            harness_id="codex",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
        )


def test_cli_runs_acceptance_fixture_matrix(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sew.cli",
            "run-fixture-harnesses",
            "--output-root",
            str(tmp_path),
        ],
        cwd=MODULE_ROOT,
        env={"PYTHONPATH": str(LIB_PYTHON)},
        check=True,
        text=True,
        capture_output=True,
    )

    assert completed.stdout.count("bundle=") == 4
    assert "fixture-codex-native-current-fact-lookup-v1" in completed.stdout
    assert "fixture-claude-code-exa-current-fact-lookup-v1" in completed.stdout
