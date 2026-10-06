from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from conftest import MODULE_ROOT

import sew.report as report_module
from sew.report import ReportError, build_report, generate_report, render_markdown_report


@pytest.fixture(autouse=True)
def _relax_min_comparable_repetitions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most cases here build single-repetition fixtures, so keep them comparable.

    Scoped per test rather than mutated at import time so the override cannot
    leak into other test modules that import ``sew.report``.
    """
    monkeypatch.setattr(report_module, "MIN_COMPARABLE_REPETITIONS", 1)


def test_load_index_resolves_attempt_paths_against_moved_run_root(tmp_path: Path) -> None:
    run_root = tmp_path / "unpacked" / "runs" / "fixture"
    run_root.mkdir(parents=True)
    absolute = tmp_path / "already-absolute"
    index = [
        {
            "run_dir": "bundles/final",
            "attempt_run_dirs": ["bundles/first", str(absolute)],
        }
    ]
    (run_root / "run-index.json").write_text(json.dumps(index), encoding="utf-8")

    loaded = report_module._load_index(run_root, {})

    assert loaded[0]["run_dir"] == str(run_root / "bundles/final")
    assert loaded[0]["attempt_run_dirs"] == [str(run_root / "bundles/first"), str(absolute)]
    assert index[0]["attempt_run_dirs"] == ["bundles/first", str(absolute)]


def test_report_native_baseline_delta_and_evidence_links(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "native-fail",
        provider_id="native",
        harness_id="codex",
        outcome="fail",
        status="failed",
        token_source="measured",
        total_tokens=100,
    )
    _bundle(
        run_root,
        "exa-pass",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    _state(
        run_root,
        [
            _entry(run_root, "native-fail", "native", "codex", "failed"),
            _entry(run_root, "exa-pass", "exa", "codex", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    comparison = report["native_baseline_comparison"][0]

    assert comparison["provider_id"] == "exa"
    assert comparison["baseline_provider_id"] == "native"
    assert comparison["success_delta_vs_baseline"] == 1.0
    assert any(link.endswith("evidence/bundle.yaml") for link in comparison["evidence_links"])
    assert all(not link.startswith(str(run_root)) for link in comparison["evidence_links"])
    assert "success_ci_95" in report["executive_by_task_class"][0]


def test_report_shows_intervals_for_token_and_comparison_rates(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    for run_id, outcome, token_source, total_tokens in (
        ("exa-pass", "pass", "unknown", None),
        ("exa-fail", "fail", "estimated", 80),
    ):
        _bundle(
            run_root,
            run_id,
            provider_id="exa",
            harness_id="codex",
            outcome=outcome,
            status="succeeded" if outcome == "pass" else "failed",
            token_source=token_source,
            total_tokens=total_tokens,
        )
    _state(
        run_root,
        [
            _entry(run_root, "exa-pass", "exa", "codex", "succeeded"),
            _entry(run_root, "exa-fail", "exa", "codex", "failed"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    executive = report["executive_by_task_class"][0]
    tokens = executive["tokens"]
    expected_ci = {"low": pytest.approx(0.0945, abs=1e-4), "high": pytest.approx(0.9055, abs=1e-4)}
    assert tokens["unknown_rate"] == 0.5
    assert tokens["unknown_rate_ci_95"] == expected_ci
    assert tokens["estimated_rate_ci_95"] == expected_ci
    assert report["native_baseline_comparison"][0]["success_ci_95"] == expected_ci

    markdown = render_markdown_report(report)
    assert "unknown_token_ci_95" in markdown
    assert "success_ci_95" in markdown
    assert "9.5-90.5%" in markdown


def test_report_task_class_aggregate_omits_mixed_cell_identity(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "native-pass",
        provider_id="native",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=100,
    )
    _bundle(
        run_root,
        "exa-pass",
        provider_id="exa",
        harness_id="pi",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    _state(
        run_root,
        [
            _entry(run_root, "native-pass", "native", "codex", "succeeded"),
            _entry(run_root, "exa-pass", "exa", "pi", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    executive = report["executive_by_task_class"][0]
    comparison = report["pi_oss_comparison"][0]

    assert executive["task_class"] == "fact_lookup"
    assert executive["n"] == 2
    assert "provider_id" not in executive
    assert "harness_id" not in executive
    assert "model_profile" not in executive
    assert comparison["provider_id"] == "exa"
    assert comparison["model_profile"] == "default"


def test_report_raw_run_index_matches_missing_run_id_rows(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    entry = _entry(run_root, "", "exa", "codex", "succeeded")
    del entry["run_id"]
    _state(run_root, [entry])

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    raw = report["raw_run_index"][0]

    assert raw["run_id"] is None
    assert raw["task_class"] == "fact_lookup"
    assert raw["successful"] is True
    assert any(link.endswith("evidence/bundle.yaml") for link in raw["evidence_links"])


def test_report_marks_unsupported_under_sampled_and_telemetry_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_module, "MIN_COMPARABLE_REPETITIONS", 3)
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "exa-pass",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    broken = run_root / "bundles" / "broken"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "broken",
                "suite_id": "lighthouse",
                "task_id": "current-fact-lookup-v1",
                "provider_id": "firecrawl",
                "harness_id": "codex",
                "model_profile": "default",
                "mode": "fixture",
                "status": "succeeded",
                "started_at": "2026-09-16T18:00:00Z",
                "ended_at": "2026-09-16T18:00:01Z",
                "metrics_ref": "metrics/missing.json",
                "evaluation_ref": "evaluations/missing.json",
                "evidence_bundle_ref": "evidence/missing.yaml",
                "provider_call_refs": [],
            }
        ),
        encoding="utf-8",
    )
    _state(
        run_root,
        [
            _entry(run_root, "exa-pass", "exa", "codex", "succeeded"),
            _entry(
                run_root,
                "unsupported",
                "firecrawl",
                "pi",
                "not_applicable",
                run_dir=None,
                reason="tool_calling_unsupported",
            ),
            _entry(run_root, "broken", "firecrawl", "codex", "succeeded", run_dir=broken),
        ],
        remaining=4,
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    warnings = {(warning["kind"], warning["message"]) for warning in report["warnings"]}

    assert any(
        kind == "non_comparable" and "tool_calling_unsupported" in msg for kind, msg in warnings
    )
    assert any(kind == "under_sampled" and "n=1" in msg for kind, msg in warnings)
    assert any(kind == "telemetry_incomplete" and "broken" in msg for kind, msg in warnings)
    assert any(kind == "under_sampled" and "remaining cells" in msg for kind, msg in warnings)


def test_report_keeps_estimated_and_unknown_tokens_out_of_precise_cost(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "pi-estimated",
        provider_id="exa",
        harness_id="pi",
        model_profile="oss-small",
        outcome="pass",
        status="succeeded",
        token_source="estimated",
        total_tokens=1720,
    )
    _bundle(
        run_root,
        "pi-unknown",
        provider_id="parallel-web",
        harness_id="pi",
        model_profile="oss-small",
        outcome="pass",
        status="succeeded",
        token_source="unknown",
        total_tokens=None,
    )
    _state(
        run_root,
        [
            _entry(run_root, "pi-estimated", "exa", "pi", "succeeded", model_profile="oss-small"),
            _entry(
                run_root,
                "pi-unknown",
                "parallel-web",
                "pi",
                "succeeded",
                model_profile="oss-small",
            ),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    pi_rows = {row["provider_id"]: row for row in report["pi_oss_comparison"]}
    executive = report["executive_by_task_class"][0]
    markdown = render_markdown_report(report)

    assert pi_rows["exa"]["token_source_counts"]["estimated"] == 1
    assert pi_rows["exa"]["cost_precision"] == "not_precise"
    assert pi_rows["parallel-web"]["token_source_counts"]["unknown"] == 1
    assert pi_rows["parallel-web"]["cost_precision"] == "not_precise"
    assert executive["tokens"]["unknown_rate"] == 0.5
    assert "not precise cost" in markdown


def test_report_cli_writes_json_and_markdown_artifacts(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "exa-pass",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    _state(run_root, [_entry(run_root, "exa-pass", "exa", "codex", "succeeded")])

    artifacts = generate_report(
        run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z"
    )

    assert artifacts.json_path.name == "report.json"
    assert artifacts.markdown_path.name == "report.md"
    assert (
        json.loads(artifacts.json_path.read_text(encoding="utf-8"))["manifest_id"] == "lighthouse@1"
    )
    assert "# SEW Report" in artifacts.markdown_path.read_text(encoding="utf-8")


def test_report_rewraps_malformed_top_level_json_as_report_error(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    (run_root / "runner-state.json").write_text("{", encoding="utf-8")

    with pytest.raises(ReportError, match="invalid runner state JSON"):
        build_report(run_root, module_base=MODULE_ROOT)

    run_root = _run_root(tmp_path / "index")
    _state(run_root, [])
    (run_root / "run-index.json").write_text("{", encoding="utf-8")

    with pytest.raises(ReportError, match="invalid run index JSON"):
        build_report(run_root, module_base=MODULE_ROOT)


def test_report_isolates_corrupt_telemetry_records_to_warnings(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "bad-run-record",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    (run_root / "bundles" / "bad-run-record" / "run.json").write_text("null", encoding="utf-8")
    _bundle(
        run_root,
        "bad-evaluation-record",
        provider_id="parallel-web",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=70,
    )
    _write_json(
        run_root / "bundles" / "bad-evaluation-record" / "evaluations" / "evaluation.json",
        {"outcome": "pass", "failure_reasons": None},
    )
    _state(
        run_root,
        [
            _entry(run_root, "bad-run-record", "exa", "codex", "succeeded"),
            _entry(run_root, "bad-evaluation-record", "parallel-web", "codex", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    warnings = {(warning["kind"], warning["message"]) for warning in report["warnings"]}

    assert len(report["raw_run_index"]) == 2
    assert any(kind == "telemetry_incomplete" and "bad-run-record" in msg for kind, msg in warnings)
    assert all("None" not in row["category"] for row in report["failure_taxonomy"])


def test_report_loads_evaluation_and_evidence_when_metrics_is_corrupt(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "bad-metrics-record",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    (run_root / "bundles" / "bad-metrics-record" / "metrics" / "metrics.json").write_text(
        "{", encoding="utf-8"
    )
    _state(run_root, [_entry(run_root, "bad-metrics-record", "exa", "codex", "succeeded")])

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    raw = report["raw_run_index"][0]
    warnings = {(warning["kind"], warning["message"]) for warning in report["warnings"]}

    assert raw["successful"] is True
    assert any(link.endswith("evidence/bundle.yaml") for link in raw["evidence_links"])
    assert any(
        kind == "telemetry_incomplete" and "metrics.json" in message for kind, message in warnings
    )


def test_report_best_external_baseline_preserves_zero_latency(tmp_path: Path) -> None:
    run_root = _run_root(tmp_path)
    for run_id, provider_id, latency_ms in (
        ("exa-zero", "exa", 0),
        ("firecrawl-slower", "firecrawl", 100),
        ("parallel-target", "parallel-web", 50),
    ):
        _bundle(
            run_root,
            run_id,
            provider_id=provider_id,
            harness_id="codex",
            outcome="pass",
            status="succeeded",
            token_source="measured",
            total_tokens=80,
            latency_ms=latency_ms,
        )
    _state(
        run_root,
        [
            _entry(run_root, "exa-zero", "exa", "codex", "succeeded"),
            _entry(run_root, "firecrawl-slower", "firecrawl", "codex", "succeeded"),
            _entry(run_root, "parallel-target", "parallel-web", "codex", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    comparisons = {row["provider_id"]: row for row in report["external_provider_comparison"]}

    assert report["executive_by_task_class"][0]["latency_ms"]["p50"] == 50
    assert comparisons["parallel-web"]["baseline_provider_id"] == "exa"


def test_report_best_external_baseline_uses_deterministic_provider_tie_break(
    tmp_path: Path,
) -> None:
    run_root = _run_root(tmp_path)
    for run_id, provider_id in (
        ("target", "parallel-web"),
        ("peer-b", "firecrawl"),
        ("peer-a", "exa"),
    ):
        _bundle(
            run_root,
            run_id,
            provider_id=provider_id,
            harness_id="codex",
            outcome="pass",
            status="succeeded",
            token_source="measured",
            total_tokens=80,
            latency_ms=100,
        )
    _state(
        run_root,
        [
            _entry(run_root, "target", "parallel-web", "codex", "succeeded"),
            _entry(run_root, "peer-b", "firecrawl", "codex", "succeeded"),
            _entry(run_root, "peer-a", "exa", "codex", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    comparisons = {row["provider_id"]: row for row in report["external_provider_comparison"]}

    assert comparisons["parallel-web"]["baseline_provider_id"] == "firecrawl"


def test_report_bad_task_manifest_defaults_to_unknown_when_json_decode_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "exa-pass",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=80,
    )
    _state(run_root, [_entry(run_root, "exa-pass", "exa", "codex", "succeeded")])

    def _raise_json_decode_error(path: Path) -> dict[str, object]:
        raise json.JSONDecodeError("bad manifest", "{", 0)

    monkeypatch.setattr(report_module, "load_task_manifest", _raise_json_decode_error)

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")

    assert report["raw_run_index"][0]["task_class"] == "unknown"


def test_report_numeric_formatters_do_not_render_booleans_as_numbers() -> None:
    assert report_module._pct(True) == "-"
    assert report_module._signed_pct(False) == "-"
    assert report_module._num(True) == "-"
    assert report_module._signed_num(False) == "-"


def _run_root(tmp_path: Path) -> Path:
    run_root = tmp_path / ".sew" / "runs" / "fixture-report"
    (run_root / "bundles").mkdir(parents=True)
    return run_root


def _state(run_root: Path, index: list[dict[str, object]], *, remaining: int = 0) -> None:
    state = {
        "schema_version": 1,
        "suite_id": "lighthouse",
        "suite_version": "1",
        "seed": 20260916,
        "order": [entry["cell_key"] for entry in index],
        "index": index,
        "summary": {
            "completed_cells": len(index),
            "remaining_cells": remaining,
            "status_counts": {},
        },
    }
    (run_root / "runner-state.json").write_text(json.dumps(state), encoding="utf-8")
    (run_root / "run-index.json").write_text(json.dumps(index), encoding="utf-8")


def _entry(
    run_root: Path,
    run_id: str,
    provider_id: str,
    harness_id: str,
    status: str,
    *,
    model_profile: str = "default",
    run_dir: Path | None | object = ...,
    reason: str | None = None,
) -> dict[str, object]:
    resolved_run_dir = run_root / "bundles" / run_id if run_dir is ... else run_dir
    entry: dict[str, object] = {
        "cell_key": f"current-fact-lookup-v1|{provider_id}|{harness_id}|{model_profile}|1",
        "run_id": run_id,
        "task_id": "current-fact-lookup-v1",
        "provider_id": provider_id,
        "harness_id": harness_id,
        "model_profile": model_profile,
        "repetition": 1,
        "status": status,
        "terminal": True,
        "attempts": 1,
    }
    if resolved_run_dir is not None:
        entry["run_dir"] = str(resolved_run_dir)
    if reason:
        entry["not_applicable_reason"] = reason
    return entry


def _bundle(
    run_root: Path,
    run_id: str,
    *,
    provider_id: str,
    harness_id: str,
    outcome: str,
    status: str,
    token_source: str,
    total_tokens: int | None,
    model_profile: str = "default",
    latency_ms: int = 10000,
) -> None:
    run_dir = run_root / "bundles" / run_id
    for rel in ("metrics", "evaluations", "evidence", "sources", "provider-calls"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    _write_json(
        run_dir / "run.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "suite_id": "lighthouse",
            "task_id": "current-fact-lookup-v1",
            "provider_id": provider_id,
            "harness_id": harness_id,
            "model_profile": model_profile,
            "mode": "fixture",
            "status": status,
            "started_at": "2026-09-16T18:00:00Z",
            "ended_at": "2026-09-16T18:00:10Z",
            "metrics_ref": "metrics/metrics.json",
            "evaluation_ref": "evaluations/evaluation.json",
            "evidence_bundle_ref": "evidence/bundle.yaml",
            "provider_call_refs": ["provider-calls/search.json"],
        },
    )
    _write_json(
        run_dir / "metrics" / "metrics.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "latency_ms": {
                "end_to_end": latency_ms,
                "total_wall": latency_ms,
                "provider": 1000,
                "harness": max(latency_ms - 1000, 0),
                "evaluator": 500,
            },
            "token_usage": {
                "accounting_source": token_source,
                "source_kind": token_source,
                "input": total_tokens - 10 if total_tokens is not None else None,
                "cached_input": 0 if total_tokens is not None else None,
                "output": 10 if total_tokens is not None else None,
                "reasoning": 0 if total_tokens is not None else None,
                "total_billable": total_tokens,
            },
            "provider_calls": {"total": 1, "ok": 1},
            "provider_result_chars": 120,
            "source_counts": {"normalized": 1, "cited": 1},
            "failure_categories": [] if outcome == "pass" else ["wrong_answer"],
            "cost": {"currency": "USD", "amount": None, "source": token_source},
        },
    )
    _write_json(
        run_dir / "evaluations" / "evaluation.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": "current-fact-lookup-v1",
            "outcome": outcome,
            "dimensions": {
                "completed": status == "succeeded",
                "correct": outcome == "pass",
                "grounded": outcome == "pass",
                "fresh": outcome == "pass",
                "schema_valid": True,
                "safe": True,
            },
            "failure_reasons": [] if outcome == "pass" else ["wrong answer"],
            "judge": {"kind": "fixture"},
            "evidence_refs": ["sources/source.json"],
        },
    )
    _write_json(
        run_dir / "sources" / "source.json",
        {
            "schema_version": 1,
            "source_id": "source",
            "provider_id": provider_id,
            "url": "https://fixture.example/source",
        },
    )
    _write_json(
        run_dir / "provider-calls" / "search.json",
        {"schema_version": 1, "call_id": "search", "run_id": run_id, "provider_id": provider_id},
    )
    (run_dir / "evidence" / "bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "bundle_id": f"{run_id}-bundle",
                "run_id": run_id,
                "redaction": {
                    "raw_transcripts_included": False,
                    "credentials_included": False,
                    "cookies_included": False,
                    "unrestricted_page_archives_included": False,
                },
                "artifacts": [],
                "records": {
                    "run": "run.json",
                    "metrics": "metrics/metrics.json",
                    "evaluation": "evaluations/evaluation.json",
                    "provider_calls": ["provider-calls/search.json"],
                    "normalized_sources": ["sources/source.json"],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, data: dict[str, object | None]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_wsb08_under_sampled_cell_excluded_from_headline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_module, "MIN_COMPARABLE_REPETITIONS", 3)
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "exa-under-sampled",
        provider_id="exa",
        harness_id="codex",
        outcome="pass",
        status="succeeded",
        token_source="measured",
        total_tokens=100,
    )
    _state(
        run_root,
        [
            _entry(run_root, "exa-under-sampled", "exa", "codex", "succeeded"),
        ],
    )

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-17T00:00:00Z")
    assert len(report["executive_by_task_class"]) == 0
    assert len(report["external_provider_comparison"]) == 0
    assert len(report["raw_run_index"]) == 1
    assert report["raw_run_index"][0]["run_id"] == "exa-under-sampled"


def test_an_under_sampled_baseline_is_flagged_not_reported_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thin baseline and an absent one are different failures.

    Filtering under-sampled cells before the baseline lookup made a native
    baseline with too few repetitions vanish, so a well-sampled subject read
    `missing_baseline` -- a dispatch failure -- when the truth was a
    repetition shortfall in the baseline.
    """
    monkeypatch.setattr(report_module, "MIN_COMPARABLE_REPETITIONS", 2)
    run_root = _run_root(tmp_path)
    _bundle(
        run_root,
        "native-1",
        provider_id="native",
        harness_id="codex",
        outcome="fail",
        status="failed",
        token_source="measured",
        total_tokens=100,
    )
    entries = [_entry(run_root, "native-1", "native", "codex", "failed")]
    for rep in (1, 2):
        run_id = f"exa-{rep}"
        _bundle(
            run_root,
            run_id,
            provider_id="exa",
            harness_id="codex",
            outcome="pass",
            status="succeeded",
            token_source="measured",
            total_tokens=80,
        )
        entry = _entry(run_root, run_id, "exa", "codex", "succeeded")
        entry["cell_key"] = str(entry["cell_key"])[:-1] + str(rep)
        entry["repetition"] = rep
        entries.append(entry)
    _state(run_root, entries)

    report = build_report(run_root, module_base=MODULE_ROOT, generated_at="2026-09-25T00:00:00Z")

    (row,) = report["native_baseline_comparison"]
    assert row["provider_id"] == "exa"
    assert row["baseline_provider_id"] == "native"
    assert "baseline_under_sampled" in row["warnings"]
    assert not any("missing_baseline" in w for w in row["warnings"])
