"""Markdown and JSON report generation for completed SEW suite runs."""

from __future__ import annotations

import json
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .catalog import module_root
from .retrieval_lane import wilson as retrieval_wilson
from .schema import SchemaError, load_document, load_record, load_task_manifest

MIN_COMPARABLE_REPETITIONS = 3


def under_sampled(n: int) -> bool:
    """Apply WSB-08's shared minimum-n publication gate."""
    return n < MIN_COMPARABLE_REPETITIONS


class ReportError(RuntimeError):
    """Raised when a SEW report cannot be generated."""


@dataclass(frozen=True)
class ReportArtifacts:
    json_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class RunRow:
    run_id: str
    run_dir: Path | None
    task_id: str
    task_class: str
    provider_id: str
    harness_id: str
    model_profile: str
    repetition: int | None
    status: str
    mode: str | None
    evaluation_outcome: str | None
    successful: bool
    latency_ms: Mapping[str, Any]
    token_usage: Mapping[str, Any]
    provider_result_chars: int | None
    failure_categories: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    evidence_links: tuple[str, ...]
    telemetry_warnings: tuple[str, ...]


def generate_report(
    run_root: Path,
    *,
    module_base: Path | None = None,
    generated_at: str | None = None,
    output_dir: Path | None = None,
) -> ReportArtifacts:
    """Generate report artifacts for a suite run root.

    The run root is the directory containing ``runner-state.json`` and
    ``run-index.json``. Report files are written below ``reports/`` by default.
    """

    destination = output_dir or run_root / "reports"
    base = module_base or module_root()
    report = build_report(
        run_root,
        module_base=base,
        generated_at=generated_at,
        output_dir=destination,
    )
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "report.json"
    markdown_path = destination / "report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return ReportArtifacts(json_path=json_path, markdown_path=markdown_path)


def build_report(
    run_root: Path,
    *,
    module_base: Path | None = None,
    generated_at: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    base = module_base or module_root()
    report_dir = output_dir or run_root / "reports"
    state = _load_state(run_root)
    index = _load_index(run_root, state)
    task_classes = _load_task_classes(base, state, index)
    rows = _load_rows(index, task_classes, link_base=report_dir)
    groups = _aggregate_groups(rows)
    valid_cells = [
        c for c in groups["cell"].values() if "under_sampled" not in c.get("warnings", [])
    ]
    report = {
        "schema_version": 1,
        "manifest_id": f"{state.get('suite_id')}@{state.get('suite_version')}",
        "suite_id": state.get("suite_id"),
        "suite_version": state.get("suite_version"),
        "adapter_versions": _adapter_versions(rows),
        "repetition_count": _repetition_count(state, index),
        "seed": state.get("seed"),
        "run_ids": sorted(row.run_id for row in rows if row.run_id),
        "suite_run_root": str(run_root),
        "generated_at": generated_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "summary": state.get("summary", {}),
        "executive_by_task_class": [
            _cell_to_json(cell)
            for cell in _sorted_cells(groups["task_class"].values(), ("task_class",))
        ],
        # Baselines are looked up among ALL cells: filtering first would make an
        # under-sampled baseline vanish, so its subjects read `missing_baseline`
        # instead of `baseline_under_sampled` -- a dispatch failure and a
        # repetition shortfall reported as the same thing. Under-sampled
        # SUBJECTS are skipped inside the comparison functions.
        "native_baseline_comparison": _native_baseline(groups["cell"].values()),
        "external_provider_comparison": _external_provider_comparison(groups["cell"].values()),
        "pi_oss_comparison": _pi_comparison(valid_cells),
        "failure_taxonomy": _failure_taxonomy(rows),
        "warnings": _warnings(rows, groups["cell"].values(), index, state),
        "raw_run_index": _raw_run_index(rows, index),
    }
    return report


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"# SEW Report: {report.get('manifest_id')}",
        "",
        f"- Generated: {report.get('generated_at')}",
        f"- Seed: {report.get('seed')}",
        f"- Repetitions: {report.get('repetition_count')}",
        f"- Run ids: {', '.join(report.get('run_ids', [])) or 'none'}",
        "",
        "## Executive Table By Task Class",
        _table(
            [
                "task_class",
                "n",
                "success_rate",
                "success_ci_95",
                "success_variance",
                "latency_p50_ms",
                "latency_p90_ms",
                "latency_p95_ms",
                "token_p50",
                "unknown_token_rate",
                "unknown_token_ci_95",
                "warnings",
                "evidence",
            ],
            [
                [
                    cell.get("task_class"),
                    cell.get("n"),
                    _pct(cell.get("success_rate")),
                    _ci(cell.get("success_ci_95")),
                    f"{cell.get('success_rate_variance'):.4f}"
                    if cell.get("success_rate_variance") is not None
                    else "-",
                    _num(cell.get("latency_ms", {}).get("p50")),
                    _num(cell.get("latency_ms", {}).get("p90")),
                    _num(cell.get("latency_ms", {}).get("p95")),
                    _num(cell.get("tokens", {}).get("total_billable", {}).get("p50")),
                    _pct(cell.get("tokens", {}).get("unknown_rate")),
                    _ci(cell.get("tokens", {}).get("unknown_rate_ci_95")),
                    ", ".join(cell.get("warnings", [])) or "-",
                    _links(cell.get("evidence_links", [])),
                ]
                for cell in report.get("executive_by_task_class", [])
            ],
        ),
        "",
        "## Native Baseline Comparison",
        _comparison_table(report.get("native_baseline_comparison", []), include_baseline=True),
        "",
        "## External Provider Comparison",
        _comparison_table(report.get("external_provider_comparison", []), include_baseline=True),
        "",
        "## Pi OSS Comparison",
        _table(
            [
                "provider",
                "model_profile",
                "n",
                "success_rate",
                "success_ci_95",
                "measured_tokens",
                "estimated_tokens",
                "unknown_tokens",
                "cost_precision",
                "warnings",
                "evidence",
            ],
            [
                [
                    row.get("provider_id"),
                    row.get("model_profile"),
                    row.get("n"),
                    _pct(row.get("success_rate")),
                    _ci(row.get("success_ci_95")),
                    row.get("token_source_counts", {}).get("measured", 0),
                    row.get("token_source_counts", {}).get("estimated", 0),
                    row.get("token_source_counts", {}).get("unknown", 0),
                    row.get("cost_precision"),
                    ", ".join(row.get("warnings", [])) or "-",
                    _links(row.get("evidence_links", [])),
                ]
                for row in report.get("pi_oss_comparison", [])
            ],
        ),
        "",
        "## Failure Taxonomy",
        _table(
            ["category", "count", "run_ids"],
            [
                [row.get("category"), row.get("count"), ", ".join(row.get("run_ids", []))]
                for row in report.get("failure_taxonomy", [])
            ],
        ),
        "",
        "## Warnings",
    ]
    warnings = report.get("warnings", [])
    lines.extend(
        [f"- {warning.get('kind')}: {warning.get('message')}" for warning in warnings] or ["- none"]
    )
    lines.extend(
        [
            "",
            "## Raw Run Index",
            _table(
                [
                    "run_id",
                    "task_id",
                    "provider",
                    "harness",
                    "model_profile",
                    "status",
                    "success",
                    "evidence",
                ],
                [
                    [
                        row.get("run_id"),
                        row.get("task_id"),
                        row.get("provider_id"),
                        row.get("harness_id"),
                        row.get("model_profile"),
                        row.get("status"),
                        row.get("successful"),
                        _links(row.get("evidence_links", [])),
                    ]
                    for row in report.get("raw_run_index", [])
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _load_state(run_root: Path) -> dict[str, Any]:
    path = run_root / "runner-state.json"
    if not path.is_file():
        raise ReportError(f"missing runner state: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid runner state JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError("runner-state.json must contain an object")
    return data


def _load_index(run_root: Path, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = run_root / "run-index.json"
    try:
        raw = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else state.get("index", [])
        )
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid run index JSON: {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ReportError("run index must be an array")
    entries = [dict(entry) for entry in raw if isinstance(entry, dict)]
    for entry in entries:
        # A WSB-10 reproducibility bundle records run dirs relative to its run
        # root, so the bundle re-derives its report wherever it is unpacked.
        run_dir = entry.get("run_dir")
        if isinstance(run_dir, str) and run_dir and not Path(run_dir).is_absolute():
            entry["run_dir"] = str(run_root / run_dir)
        attempts = entry.get("attempt_run_dirs")
        if isinstance(attempts, list):
            entry["attempt_run_dirs"] = [
                str(run_root / attempt)
                if isinstance(attempt, str) and attempt and not Path(attempt).is_absolute()
                else attempt
                for attempt in attempts
            ]
    return entries


def _load_task_classes(
    module_base: Path, state: Mapping[str, Any], index: Iterable[Mapping[str, Any]]
) -> dict[str, str]:
    task_ids = {
        str(entry["task_id"])
        for entry in index
        if isinstance(entry.get("task_id"), str) and entry.get("task_id")
    }
    task_classes: dict[str, str] = {}
    for task_id in task_ids:
        try:
            task_classes[task_id] = load_task_manifest(module_base / "tasks" / task_id)[
                "task_class"
            ]
        except (OSError, SchemaError, json.JSONDecodeError):
            task_classes[task_id] = "unknown"
    return task_classes


def _load_rows(
    index: list[dict[str, Any]], task_classes: Mapping[str, str], *, link_base: Path
) -> list[RunRow]:
    rows: list[RunRow] = []
    for entry in index:
        run_dir = Path(entry["run_dir"]) if isinstance(entry.get("run_dir"), str) else None
        task_id = str(entry.get("task_id") or "")
        run = metrics = evaluation = evidence = None
        evidence_links: list[str] = []
        telemetry_warnings: list[str] = []
        failure_reasons: list[str] = []
        if run_dir is not None:
            run = _load_run_record(run_dir, telemetry_warnings)
            if run is not None:
                metrics = _load_referenced_record(run_dir, run, "metrics_ref", telemetry_warnings)
                evaluation = _load_referenced_record(
                    run_dir, run, "evaluation_ref", telemetry_warnings
                )
                evidence = _load_referenced_record(
                    run_dir, run, "evidence_bundle_ref", telemetry_warnings
                )
            if evidence is not None and evaluation is not None:
                try:
                    evidence_links = _evidence_links(run_dir, evidence, evaluation, link_base)
                except (TypeError, AttributeError) as exc:
                    telemetry_warnings.append(f"telemetry_incomplete:{exc}")
        status = str((run or entry).get("status") or entry.get("status") or "unknown")
        provider_id = str((run or entry).get("provider_id") or entry.get("provider_id") or "")
        harness_id = str((run or entry).get("harness_id") or entry.get("harness_id") or "")
        model_profile = str((run or entry).get("model_profile") or entry.get("model_profile") or "")
        token_usage = _mapping_or_empty((metrics or {}).get("token_usage"))
        latency = _mapping_or_empty((metrics or {}).get("latency_ms"))
        failure_categories = [
            str(item) for item in _list_or_empty((metrics or {}).get("failure_categories"))
        ]
        if (run or {}).get("failure_category"):
            failure_categories.append(str(run["failure_category"]))
        if entry.get("failure_category"):
            failure_categories.append(str(entry["failure_category"]))
        if evaluation:
            failure_reasons = [
                str(item) for item in _list_or_empty(evaluation.get("failure_reasons"))
            ]
        warnings = list(telemetry_warnings)
        expects_bundle = run_dir is not None or status not in {"not_applicable", "unsupported"}
        if expects_bundle and not metrics:
            warnings.append("telemetry_incomplete:metrics")
        if expects_bundle and not evaluation:
            warnings.append("telemetry_incomplete:evaluation")
        if not evidence_links and run_dir is not None:
            warnings.append("telemetry_incomplete:evidence_links")
        accounting = str(token_usage.get("accounting_source") or "unknown")
        if accounting == "unknown":
            warnings.append("unknown_tokens")
        elif accounting == "estimated":
            warnings.append("estimated_tokens_not_precise_cost")
        rows.append(
            RunRow(
                run_id=str((run or entry).get("run_id") or entry.get("run_id") or ""),
                run_dir=run_dir,
                task_id=task_id,
                task_class=task_classes.get(task_id, "unknown"),
                provider_id=provider_id,
                harness_id=harness_id,
                model_profile=model_profile,
                repetition=(
                    entry.get("repetition") if isinstance(entry.get("repetition"), int) else None
                ),
                status=status,
                mode=(run or {}).get("mode"),
                evaluation_outcome=(evaluation or {}).get("outcome"),
                successful=status == "succeeded" and (evaluation or {}).get("outcome") == "pass",
                latency_ms=latency,
                token_usage=token_usage,
                provider_result_chars=_int_or_none((metrics or {}).get("provider_result_chars")),
                failure_categories=tuple(sorted(set(failure_categories))),
                failure_reasons=tuple(failure_reasons),
                evidence_links=tuple(evidence_links),
                telemetry_warnings=tuple(sorted(set(warnings))),
            )
        )
    return rows


def _load_run_record(run_dir: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        return _mapping_record(load_document(run_dir / "run.json"), "run.json")
    except (
        OSError,
        TypeError,
        AttributeError,
        SchemaError,
        json.JSONDecodeError,
    ) as exc:
        warnings.append(f"telemetry_incomplete:{exc}")
        return None


def _load_referenced_record(
    run_dir: Path, run: Mapping[str, Any], ref_key: str, warnings: list[str]
) -> dict[str, Any] | None:
    try:
        ref = run[ref_key]
        return _mapping_record(load_record(run_dir, ref), str(ref))
    except (
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        SchemaError,
        json.JSONDecodeError,
    ) as exc:
        warnings.append(f"telemetry_incomplete:{exc}")
        return None


def _aggregate_groups(rows: Iterable[RunRow]) -> dict[str, dict[tuple[Any, ...], dict[str, Any]]]:
    grouped: dict[str, dict[tuple[Any, ...], list[RunRow]]] = {
        "cell": defaultdict(list),
    }
    for row in rows:
        grouped["cell"][
            (row.task_class, row.provider_id, row.harness_id, row.model_profile)
        ].append(row)

    cells = {
        key: _aggregate_cell(items, include_cell_identity=True)
        for key, items in grouped["cell"].items()
    }

    task_class_valid_rows: dict[str, list[RunRow]] = defaultdict(list)
    task_class_cell_success_rates: dict[str, list[float]] = defaultdict(list)
    for key, cell in cells.items():
        if "under_sampled" not in cell["warnings"]:
            task_class, provider_id, harness_id, model_profile = key
            task_class_valid_rows[task_class].extend(grouped["cell"][key])
            if cell["success_rate"] is not None:
                task_class_cell_success_rates[task_class].append(cell["success_rate"])

    task_class_aggregates = {}
    for task_class, valid_rows in task_class_valid_rows.items():
        agg = _aggregate_cell(valid_rows, include_cell_identity=False)
        rates = task_class_cell_success_rates[task_class]

        agg["success_rate_variance"] = statistics.variance(rates) if len(rates) > 1 else None
        task_class_aggregates[(task_class,)] = agg

    return {
        "task_class": task_class_aggregates,
        "cell": cells,
    }


def _aggregate_cell(rows: list[RunRow], *, include_cell_identity: bool = True) -> dict[str, Any]:
    first = rows[0]
    successes = sum(1 for row in rows if row.successful)
    n = len(rows)
    token_sources = Counter(
        str(row.token_usage.get("accounting_source") or "unknown") for row in rows
    )
    warnings = sorted(set(warning for row in rows for warning in row.telemetry_warnings))
    if under_sampled(n):
        warnings.append("under_sampled")
    if any(row.status in {"unsupported", "not_applicable"} for row in rows):
        warnings.append("non_comparable")
    if any(warning.startswith("telemetry_incomplete") for warning in warnings):
        warnings.append("telemetry_incomplete")
    total_tokens = [_total_tokens(row.token_usage) for row in rows]
    measured_tokens = [
        _total_tokens(row.token_usage)
        for row in rows
        if row.token_usage.get("accounting_source") == "measured"
    ]
    estimated_tokens = [
        _total_tokens(row.token_usage)
        for row in rows
        if row.token_usage.get("accounting_source") == "estimated"
    ]
    cell = {
        "task_class": first.task_class,
        "n": n,
        "successes": successes,
        "success_rate": successes / n if n else None,
        "success_ci_95": _wilson_ci(successes, n),
        "latency_ms": {
            "p50": _percentile(_latency_values(rows), 50),
            "p90": _percentile(_latency_values(rows), 90),
            "p95": _percentile(_latency_values(rows), 95),
        },
        "tokens": {
            "total_billable": {
                "p50": _percentile(_values(total_tokens), 50),
                "p90": _percentile(_values(total_tokens), 90),
                "p95": _percentile(_values(total_tokens), 95),
            },
            "measured_total_billable": {
                "p50": _percentile(_values(measured_tokens), 50),
                "p90": _percentile(_values(measured_tokens), 90),
                "p95": _percentile(_values(measured_tokens), 95),
            },
            "estimated_total_billable": {
                "p50": _percentile(_values(estimated_tokens), 50),
                "p90": _percentile(_values(estimated_tokens), 90),
                "p95": _percentile(_values(estimated_tokens), 95),
            },
            "unknown_rate": token_sources.get("unknown", 0) / n if n else None,
            "unknown_rate_ci_95": _wilson_ci(token_sources.get("unknown", 0), n),
            "estimated_rate": token_sources.get("estimated", 0) / n if n else None,
            "estimated_rate_ci_95": _wilson_ci(token_sources.get("estimated", 0), n),
            "source_counts": dict(token_sources),
        },
        "failure_categories": dict(Counter(cat for row in rows for cat in row.failure_categories)),
        "warnings": sorted(set(warnings)),
        "evidence_links": sorted(set(link for row in rows for link in row.evidence_links)),
        "run_ids": sorted(row.run_id for row in rows),
    }
    if include_cell_identity:
        cell.update(
            {
                "provider_id": first.provider_id,
                "harness_id": first.harness_id,
                "model_profile": first.model_profile,
            }
        )
    return cell


def _cell_to_json(cell: Mapping[str, Any]) -> dict[str, Any]:
    return dict(cell)


def _under_sampled(cell: Mapping[str, Any]) -> bool:
    return "under_sampled" in cell.get("warnings", [])


def _native_baseline(cells: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cells = list(cells)
    by_key = {
        (cell["task_class"], cell["harness_id"], cell["model_profile"], cell["provider_id"]): cell
        for cell in cells
    }
    rows = []
    for cell in cells:
        if cell["provider_id"] == "native" or _under_sampled(cell):
            continue
        baseline = by_key.get(
            (cell["task_class"], cell["harness_id"], cell["model_profile"], "native")
        )
        rows.append(_comparison_row(cell, baseline, "native"))
    return _sort_comparison(rows)


def _external_provider_comparison(cells: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cells = list(cells)
    rows = []
    for cell in cells:
        if cell["provider_id"] == "native" or _under_sampled(cell):
            continue
        baseline = _best_external_baseline(cell, cells)
        rows.append(_comparison_row(cell, baseline, "best_external_peer"))
    return _sort_comparison(rows)


def _pi_comparison(cells: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for cell in cells:
        if cell["harness_id"] != "pi":
            continue
        source_counts = cell.get("tokens", {}).get("source_counts", {})
        warnings = list(cell.get("warnings", []))
        if source_counts.get("estimated", 0):
            warnings.append("estimated tokens separated from measured tokens; not precise cost")
        if source_counts.get("unknown", 0):
            warnings.append("unknown token data excluded from cost precision")
        rows.append(
            {
                "task_class": cell["task_class"],
                "provider_id": cell["provider_id"],
                "model_profile": cell["model_profile"],
                "n": cell["n"],
                "success_rate": cell["success_rate"],
                "success_ci_95": cell["success_ci_95"],
                "token_source_counts": source_counts,
                "cost_precision": (
                    "precise" if source_counts.get("measured", 0) == cell["n"] else "not_precise"
                ),
                "warnings": sorted(set(warnings)),
                "evidence_links": cell.get("evidence_links", []),
            }
        )
    return _sort_comparison(rows)


def _comparison_row(
    cell: Mapping[str, Any], baseline: Mapping[str, Any] | None, baseline_kind: str
) -> dict[str, Any]:
    row = {
        "task_class": cell["task_class"],
        "provider_id": cell["provider_id"],
        "harness_id": cell["harness_id"],
        "model_profile": cell["model_profile"],
        "n": cell["n"],
        "success_rate": cell["success_rate"],
        "success_ci_95": cell["success_ci_95"],
        "baseline_kind": baseline_kind,
        "baseline_provider_id": baseline.get("provider_id") if baseline else None,
        "success_delta_vs_baseline": None,
        "latency_p50_delta_ms_vs_baseline": None,
        "warnings": list(cell.get("warnings", [])),
        "evidence_links": cell.get("evidence_links", []),
    }
    if baseline is None:
        row["warnings"].append("non_comparable:missing_baseline")
        return row
    row["success_delta_vs_baseline"] = _delta(
        cell.get("success_rate"), baseline.get("success_rate")
    )
    row["latency_p50_delta_ms_vs_baseline"] = _delta(
        cell.get("latency_ms", {}).get("p50"), baseline.get("latency_ms", {}).get("p50")
    )
    row["evidence_links"] = sorted(
        set(row["evidence_links"]) | set(baseline.get("evidence_links", []))
    )
    if "under_sampled" in baseline.get("warnings", []):
        row["warnings"].append("baseline_under_sampled")
    return row


def _best_external_baseline(
    target: Mapping[str, Any], cells: Iterable[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    peers = [
        cell
        for cell in cells
        if cell["provider_id"] != "native"
        and cell["provider_id"] != target["provider_id"]
        and cell["task_class"] == target["task_class"]
        and cell["harness_id"] == target["harness_id"]
        and cell["model_profile"] == target["model_profile"]
    ]
    if not peers:
        return None
    return max(
        peers,
        key=lambda cell: (
            cell.get("success_rate") or 0,
            -_latency_sort_value(cell.get("latency_ms", {}).get("p50")),
            str(cell.get("provider_id", "")),
        ),
    )


def _failure_taxonomy(rows: Iterable[RunRow]) -> list[dict[str, Any]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        categories = set(row.failure_categories)
        categories.update(f"reason:{reason}" for reason in row.failure_reasons)
        if not row.successful and not categories:
            categories.add(row.status)
        for category in categories:
            buckets[category].append(row.run_id)
    return [
        {"category": category, "count": len(run_ids), "run_ids": sorted(run_ids)}
        for category, run_ids in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def _warnings(
    rows: Iterable[RunRow],
    cells: Iterable[Mapping[str, Any]],
    index: Iterable[Mapping[str, Any]],
    state: Mapping[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for entry in index:
        if entry.get("status") in {"not_applicable", "unsupported"} or entry.get(
            "not_applicable_reason"
        ):
            warnings.append(
                {
                    "kind": "non_comparable",
                    "message": (
                        f"{entry.get('cell_key')} is {entry.get('status')} "
                        f"({entry.get('not_applicable_reason') or entry.get('failure_category') or 'no reason'})"
                    ),
                }
            )
    for cell in cells:
        if "under_sampled" in cell.get("warnings", []):
            warnings.append(
                {
                    "kind": "under_sampled",
                    "message": (
                        f"{cell['task_class']} / {cell['provider_id']} / {cell['harness_id']} / "
                        f"{cell['model_profile']} has n={cell['n']} (< {MIN_COMPARABLE_REPETITIONS})"
                    ),
                }
            )
    for row in rows:
        for warning in row.telemetry_warnings:
            if warning.startswith("telemetry_incomplete"):
                warnings.append(
                    {
                        "kind": "telemetry_incomplete",
                        "message": f"{row.run_id}: {warning}",
                    }
                )
    summary = state.get("summary", {})
    if isinstance(summary, dict) and summary.get("remaining_cells"):
        warnings.append(
            {
                "kind": "under_sampled",
                "message": f"suite stopped with {summary.get('remaining_cells')} remaining cells",
            }
        )
    return _unique_warning_dicts(warnings)


def _raw_run_index(
    rows: Iterable[RunRow], index: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_run_id = {row.run_id: row for row in rows}
    raw = []
    for entry in index:
        row = by_run_id.get(str(entry.get("run_id") or ""))
        raw.append(
            {
                "run_id": entry.get("run_id"),
                "cell_key": entry.get("cell_key"),
                "task_id": entry.get("task_id"),
                "task_class": row.task_class if row else None,
                "provider_id": entry.get("provider_id"),
                "harness_id": entry.get("harness_id"),
                "model_profile": entry.get("model_profile"),
                "repetition": entry.get("repetition"),
                "status": entry.get("status"),
                "successful": row.successful if row else False,
                "run_dir": entry.get("run_dir"),
                "attempts": entry.get("attempts"),
                "failure_category": entry.get("failure_category"),
                "not_applicable_reason": entry.get("not_applicable_reason"),
                "evidence_links": list(row.evidence_links) if row else [],
            }
        )
    return raw


def _adapter_versions(rows: Iterable[RunRow]) -> dict[str, Any]:
    providers = set()
    harnesses = set()
    model_profiles = set()
    for row in rows:
        providers.add(row.provider_id)
        harnesses.add(row.harness_id)
        model_profiles.add(f"{row.harness_id}:{row.model_profile}")
    return {
        "provider_adapters": {
            provider: "sew-provider-adapter-v1" for provider in sorted(providers)
        },
        "harness_adapters": {harness: "sew-harness-adapter-v1" for harness in sorted(harnesses)},
        "model_profiles": sorted(model_profiles),
    }


def _repetition_count(state: Mapping[str, Any], index: Iterable[Mapping[str, Any]]) -> int | None:
    repetitions = [
        entry.get("repetition") for entry in index if isinstance(entry.get("repetition"), int)
    ]
    if repetitions:
        return max(repetitions)
    order = state.get("order")
    if isinstance(order, list):
        parsed = [_parse_repetition(str(item)) for item in order]
        values = [value for value in parsed if value is not None]
        return max(values) if values else None
    return None


def _parse_repetition(cell_key: str) -> int | None:
    try:
        return int(cell_key.rsplit("|", 1)[1])
    except (IndexError, ValueError):
        return None


def _evidence_links(
    run_dir: Path,
    evidence: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    link_base: Path,
) -> list[str]:
    paths = {run_dir / "run.json", run_dir / "evidence" / "bundle.yaml"}
    records = evidence.get("records") if isinstance(evidence.get("records"), dict) else {}
    for value in records.values():
        if isinstance(value, str):
            paths.add(run_dir / value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    paths.add(run_dir / item)
    for ref in evaluation.get("evidence_refs", []):
        if isinstance(ref, str):
            paths.add(run_dir / ref)
    return sorted(_portable_link(path, link_base) for path in paths)


def _portable_link(path: Path, link_base: Path) -> str:
    return os.path.relpath(path, start=link_base)


def _mapping_record(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must contain an object, got {type(value).__name__}")
    return value


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _total_tokens(usage: Mapping[str, Any]) -> int | None:
    total = _int_or_none(usage.get("total_billable"))
    if total is not None:
        return total
    fields = [usage.get(key) for key in ("input", "cached_input", "output", "reasoning")]
    ints = [value for value in fields if isinstance(value, int) and not isinstance(value, bool)]
    return sum(ints) if ints and len(ints) == len(fields) else None


def _values(values: Iterable[Any]) -> list[float]:
    result = []
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result.append(float(value))
    return result


def _latency_values(rows: Iterable[RunRow]) -> list[float]:
    return _values(_first_present(row.latency_ms, "total_wall", "end_to_end") for row in rows)


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _latency_sort_value(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return float(10**12)


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percentile / 100) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _wilson_ci(successes: int, n: int) -> dict[str, float | None]:
    if n == 0:
        return {"low": None, "high": None}
    low, high = retrieval_wilson(successes, n)
    return {"low": low, "high": high}


def _delta(value: Any, baseline: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not isinstance(baseline, (int, float)) or isinstance(baseline, bool):
        return None
    return float(value) - float(baseline)


def _sorted_cells(
    cells: Iterable[Mapping[str, Any]], keys: tuple[str, ...]
) -> list[Mapping[str, Any]]:
    return sorted(cells, key=lambda cell: tuple(str(cell.get(key, "")) for key in keys))


def _sort_comparison(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in sorted(
            rows,
            key=lambda row: (
                str(row.get("task_class", "")),
                str(row.get("harness_id", "")),
                str(row.get("provider_id", "")),
                str(row.get("model_profile", "")),
            ),
        )
    ]


def _unique_warning_dicts(warnings: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    seen = set()
    result = []
    for warning in warnings:
        key = (warning.get("kind"), warning.get("message"))
        if key in seen:
            continue
        seen.add(key)
        result.append({"kind": str(warning.get("kind")), "message": str(warning.get("message"))})
    return result


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    clean_rows = [[_md_cell(value) for value in row] for row in rows]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in clean_rows)
    return "\n".join(lines)


def _comparison_table(rows: list[Mapping[str, Any]], *, include_baseline: bool) -> str:
    headers = [
        "task_class",
        "provider",
        "harness",
        "model_profile",
        "n",
        "success_rate",
        "success_ci_95",
        "baseline",
        "success_delta",
        "latency_p50_delta_ms",
        "warnings",
        "evidence",
    ]
    return _table(
        headers,
        [
            [
                row.get("task_class"),
                row.get("provider_id"),
                row.get("harness_id"),
                row.get("model_profile"),
                row.get("n"),
                _pct(row.get("success_rate")),
                _ci(row.get("success_ci_95")),
                row.get("baseline_provider_id") if include_baseline else "-",
                _signed_pct(row.get("success_delta_vs_baseline")),
                _signed_num(row.get("latency_p50_delta_ms_vs_baseline")),
                ", ".join(row.get("warnings", [])) or "-",
                _links(row.get("evidence_links", [])),
            ]
            for row in rows
        ],
    )


def _pct(value: Any) -> str:
    return "-" if not _is_number(value) else f"{value * 100:.1f}%"


def _signed_pct(value: Any) -> str:
    return "-" if not _is_number(value) else f"{value * 100:+.1f}pp"


def _num(value: Any) -> str:
    return "-" if not _is_number(value) else f"{value:.0f}"


def _signed_num(value: Any) -> str:
    return "-" if not _is_number(value) else f"{value:+.0f}"


def _ci(value: Any) -> str:
    if not isinstance(value, dict):
        return "-"
    low = value.get("low")
    high = value.get("high")
    if not _is_number(low) or not _is_number(high):
        return "-"
    return f"{low * 100:.1f}-{high * 100:.1f}%"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _links(values: Iterable[Any]) -> str:
    links = [str(value) for value in values]
    if not links:
        return "-"
    return "<br>".join(f"[{Path(link).name}]({link})" for link in links[:8])


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "-")
    return text.replace("|", "\\|").replace("\n", "<br>")
